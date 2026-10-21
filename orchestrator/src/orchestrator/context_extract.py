"""Turns attachments into text the agents can read: an uploaded file, a folder on this machine, or a web page.

Everything here produces a *snapshot of text*. Agents never get a path, a mount or a network connection for
attachments: the orchestrator reads, extracts and stores the text, and `moderator/runner.py` puts a capped
amount of it in front of each seat as reference material. That is what keeps attachments read-only and keeps
agents offline.

Three things are guarded on purpose:
- Folders: only text-like files, never hidden/credential-looking files or build/dependency directories,
  never a symlink that leaves the folder, and hard limits on how much is read.
- Links: only public http(s) hosts. Every hop (redirects included) is resolved and checked, and the request
  is pinned to the checked address so a hostname cannot be re-pointed at a private one between the check and
  the request. The orchestrator can reach this machine's own services and the cloud metadata address, which is
  exactly what a link must not be able to ask it for.
- Content: stored with a size cap; reading is bounded in bytes before anything is decoded.
"""
from __future__ import annotations

import io
import ipaddress
import os
import socket
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_STORED_CHARS = 100_000          # what is kept per attachment
MAX_URL_BYTES = 2 * 1024 * 1024
MAX_PDF_PAGES = 200
MAX_REDIRECTS = 3
URL_TIMEOUT = 10.0

TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".log",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs", ".rb", ".php", ".c", ".h", ".cpp", ".hpp",
    ".cs", ".swift", ".sql", ".sh", ".html", ".htm", ".css", ".scss", ".xml", ".tf", ".gradle", ".proto",
})
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", "target",
    ".next", ".nuxt", ".cache", ".idea", ".vscode", "coverage", ".pytest_cache", ".mypy_cache", ".tox",
})
# Names that suggest credentials. Skipped even though the extension is text-like.
_SECRET_PARTS = ("secret", "credential", "password", "passwd", "token", "private", "id_rsa", "id_ed25519")
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".crt", ".cer", ".keystore", ".jks", ".kdbx", ".env")
_SECRET_NAMES = frozenset({".npmrc", ".pypirc", ".netrc", ".pgpass", ".htpasswd", ".git-credentials"})

FOLDER_MAX_FILES = 60
FOLDER_MAX_FILE_CHARS = 12_000
FOLDER_MAX_DEPTH = 6
FOLDER_MAX_SCANNED = 5_000


class ContextError(ValueError):
    """A reason an attachment was refused, written for the person who tried to add it."""


def looks_secret(name: str) -> bool:
    low = name.lower()
    return (low in _SECRET_NAMES or low.startswith(".env") or low.endswith(_SECRET_SUFFIXES)
            or any(part in low for part in _SECRET_PARTS))


def _cap(text: str) -> tuple[str, bool]:
    text = text.replace("\x00", "")
    if len(text) <= MAX_STORED_CHARS:
        return text, False
    return text[:MAX_STORED_CHARS], True


def _decode(data: bytes) -> str:
    if b"\x00" in data[:4096]:
        raise ContextError("that looks like a binary file; attach text, Markdown or a PDF")
    return data.decode("utf-8", errors="replace")


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ContextError("that PDF is password-protected")
        parts = [(page.extract_text() or "") for page in reader.pages[:MAX_PDF_PAGES]]
    except ContextError:
        raise
    except (PyPdfError, ValueError, KeyError, OSError, RecursionError) as exc:
        raise ContextError(f"could not read that PDF ({type(exc).__name__})") from exc
    text = "\n\n".join(p.strip() for p in parts if p.strip())
    if not text.strip():
        raise ContextError("that PDF has no text to read (is it a scan?)")
    return text


# ---------------------------------------------------------------- uploaded files

def extract_file(name: str, data: bytes) -> tuple[str, str, bool]:
    """(clean name, text, truncated) for an uploaded file."""
    base = os.path.basename(name.replace("\\", "/")).strip()
    if not base or base in (".", ".."):
        raise ContextError("the file needs a name")
    if len(data) == 0:
        raise ContextError("that file is empty")
    if len(data) > MAX_FILE_BYTES:
        raise ContextError(f"that file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    ext = Path(base).suffix.lower()
    if ext == ".pdf" or data[:5] == b"%PDF-":
        text = pdf_text(data)
    elif ext in TEXT_EXTENSIONS or ext == "":
        text = _decode(data)
    else:
        raise ContextError(f"{ext} files are not supported; attach text, Markdown, code or a PDF")
    if not text.strip():
        raise ContextError("that file has no text")
    text, truncated = _cap(text)
    return base[:200], text, truncated


# ---------------------------------------------------------------- folders

def extract_folder(raw_path: str) -> tuple[str, str, bool]:
    """(resolved path, text, truncated): a file tree and the contents of its text files, read-only."""
    path = Path(os.path.expanduser(raw_path.strip()))
    if not path.is_absolute():
        raise ContextError("use the full path of the folder, for example /Users/you/project/docs")
    try:
        root = path.resolve(strict=True)
    except (FileNotFoundError, RuntimeError, OSError):
        raise ContextError("that folder does not exist") from None
    if not root.is_dir():
        raise ContextError("that path is a file, not a folder; use Add file for a single file")
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise ContextError("pick a specific project folder, not the whole disk or your home folder")

    files: list[Path] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        depth = len(Path(dirpath).relative_to(root).parts)
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".") and depth < FOLDER_MAX_DEPTH)
        for fname in sorted(filenames):
            scanned += 1
            if scanned > FOLDER_MAX_SCANNED:
                break
            full = Path(dirpath, fname)
            if fname.startswith(".") or looks_secret(fname) or full.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            try:
                real = full.resolve(strict=True)
                if not real.is_relative_to(root) or not real.is_file():
                    continue  # a symlink that leaves the folder
            except (OSError, RuntimeError):
                continue
            files.append(full)
        if scanned > FOLDER_MAX_SCANNED:
            break
    if not files:
        raise ContextError("that folder has no text, Markdown or code files to read")

    tree = [f"{root.name}/"] + [f"  {f.relative_to(root)}" for f in files[:200]]
    blocks = ["Files:\n" + "\n".join(tree)]
    used = 0
    omitted = max(0, len(files) - FOLDER_MAX_FILES)
    for f in files[:FOLDER_MAX_FILES]:
        try:
            with open(f, "rb") as fh:
                data = fh.read(FOLDER_MAX_FILE_CHARS * 4)
        except OSError:
            continue
        if b"\x00" in data[:4096]:
            continue
        body = data.decode("utf-8", errors="replace")
        clipped = len(body) > FOLDER_MAX_FILE_CHARS
        body = body[:FOLDER_MAX_FILE_CHARS]
        block = f"--- {f.relative_to(root)}{' (clipped)' if clipped else ''} ---\n{body}"
        if used + len(block) > MAX_STORED_CHARS:
            omitted += 1
            continue
        blocks.append(block)
        used += len(block)
    text = "\n\n".join(blocks)
    if omitted:
        text += f"\n\n[{omitted} more file(s) not included]"
    text, truncated = _cap(text)
    return str(root), text, truncated or omitted > 0


# ---------------------------------------------------------------- links

def _blocked_address(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return not addr.is_global or addr.is_multicast


def resolve_public(host: str, port: int) -> str:
    """The first address for `host` if every address it resolves to is public, else ContextError."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ContextError("could not find that address") from None
    ips = [info[4][0] for info in infos]
    if not ips:
        raise ContextError("could not find that address")
    if any(_blocked_address(ip) for ip in ips):
        raise ContextError("that address is not on the public internet, so it can't be attached")
    return ips[0]


class _TextOnly(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "svg", "head", "nav", "footer", "form", "iframe"}
    _BREAK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "pre", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    parser = _TextOnly()
    parser.feed(html)
    parser.close()
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    text = "\n".join(line for line in lines if line)
    return " ".join(parser.title.split()), text


async def fetch_url(url: str, *, transport: httpx.AsyncBaseTransport | None = None,
                    resolver=resolve_public) -> tuple[str, str, bool]:
    """(title or url, text, truncated) for a public web page, a text file or a PDF at `url`."""
    current = url.strip()
    for _hop in range(MAX_REDIRECTS + 1):
        parts = urlsplit(current)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ContextError("use a full http:// or https:// address")
        if parts.username or parts.password:
            raise ContextError("remove the username and password from the address")
        host = parts.hostname
        port = parts.port or (443 if parts.scheme == "https" else 80)
        ip = resolver(host, port)  # tests inject a resolver and a transport
        pinned_host = f"[{ip}]" if ":" in ip else ip
        pinned = parts._replace(netloc=f"{pinned_host}:{port}").geturl()
        headers = {"Host": host if not parts.port else f"{host}:{parts.port}", "User-Agent": "Bayto/1 (attachment fetch)",
                   "Accept": "text/html,text/plain,text/markdown,application/json,application/pdf;q=0.9"}
        try:
            async with httpx.AsyncClient(transport=transport, timeout=URL_TIMEOUT, follow_redirects=False) as client:
                async with client.stream("GET", pinned, headers=headers, extensions={"sni_hostname": host}) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location")
                        if not location:
                            raise ContextError("that address redirects nowhere")
                        current = urljoin(current, location)
                        continue
                    if resp.status_code >= 400:
                        raise ContextError(f"that address answered with an error ({resp.status_code})")
                    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    body = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_URL_BYTES:
                            raise ContextError("that page is larger than 2 MB")
        except httpx.HTTPError as exc:
            raise ContextError(f"could not fetch that address ({type(exc).__name__})") from exc
        data = bytes(body)
        if ctype == "application/pdf" or data[:5] == b"%PDF-":
            text, title = pdf_text(data), ""
        elif ctype in ("text/html", "application/xhtml+xml"):
            title, text = html_to_text(data.decode("utf-8", errors="replace"))
        elif ctype.startswith("text/") or ctype in ("application/json", "application/xml", "application/yaml"):
            title, text = "", _decode(data)
        else:
            raise ContextError(f"that address returns {ctype or 'an unknown type'}, which can't be read as text")
        if not text.strip():
            raise ContextError("that page has no readable text (it may need JavaScript or a login)")
        text, truncated = _cap(text)
        return (title or current)[:200], text, truncated
    raise ContextError("that address redirects too many times")
