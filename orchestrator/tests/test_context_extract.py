"""context_extract.py: what may be attached, and what is refused. No real network: links use an injected
transport and resolver; the address checks use literal IPs."""
import os
from pathlib import Path

import httpx
import pytest

from orchestrator import context_extract as cx
from orchestrator.context_extract import ContextError


def pdf_bytes(text: str) -> bytes:
    """A minimal one-page PDF whose page text is `text` (hand-built so the test needs no PDF writer)."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        None,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET".encode()
    objs[3] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


# ---------------------------------------------------------------- files

def test_text_and_markdown_files_are_read():
    name, text, truncated = cx.extract_file("notes.md", b"# Title\n\nSome notes.")
    assert (name, truncated) == ("notes.md", False) and "Some notes." in text


def test_a_path_in_the_file_name_is_reduced_to_the_file_name():
    assert cx.extract_file("../../etc/passwd.txt", b"x")[0] == "passwd.txt"
    assert cx.extract_file("C:\\Users\\me\\plan.txt", b"x")[0] == "plan.txt"


def test_a_pdf_is_read():
    _name, text, _ = cx.extract_file("brief.pdf", pdf_bytes("Hello vendor terms"))
    assert "Hello vendor terms" in text


@pytest.mark.parametrize("name,data,why", [
    ("a.exe", b"MZ\x00\x00", "not supported"),
    ("a.txt", b"abc\x00def", "binary"),
    ("a.txt", b"", "empty"),
    ("a.pdf", b"%PDF-1.4 garbage", "could not read"),
    ("a.png", b"\x89PNG", "not supported"),
])
def test_files_that_cannot_be_read_are_refused_with_a_reason(name, data, why):
    with pytest.raises(ContextError, match=why):
        cx.extract_file(name, data)


def test_oversized_files_are_refused_and_long_text_is_truncated():
    with pytest.raises(ContextError, match="larger than"):
        cx.extract_file("big.txt", b"a" * (cx.MAX_FILE_BYTES + 1))
    _n, text, truncated = cx.extract_file("long.txt", b"a" * (cx.MAX_STORED_CHARS + 50))
    assert truncated and len(text) == cx.MAX_STORED_CHARS


# ---------------------------------------------------------------- folders

def make_tree(root: Path):
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "guide.md").write_text("# Guide\nUse the thing.")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('hi')\n")
    (root / "README.md").write_text("Readme body")
    (root / ".env").write_text("API_KEY=sk-live-123")
    (root / "config.env").write_text("X=1")
    (root / "secrets.txt").write_text("hunter2")
    (root / "id_rsa.txt").write_text("-----BEGIN")
    (root / "server.pem").write_text("-----BEGIN CERT")
    (root / ".git").mkdir()
    (root / ".git" / "config.txt").write_text("[remote]")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "lib.js").write_text("module.exports = 1")
    (root / "logo.png").write_bytes(b"\x89PNG\x00")


def test_a_folder_gives_a_tree_and_its_text_files_but_never_secrets_or_dependencies(tmp_path):
    make_tree(tmp_path / "proj")
    resolved, text, _ = cx.extract_folder(str(tmp_path / "proj"))
    assert resolved == str((tmp_path / "proj").resolve())
    for wanted in ("docs/guide.md", "src/app.py", "README.md", "Use the thing.", "print('hi')"):
        assert wanted in text
    for forbidden in ("sk-live-123", "hunter2", "-----BEGIN", "[remote]", "module.exports", "X=1", "logo.png", ".env"):
        assert forbidden not in text


def test_a_symlink_that_leaves_the_folder_is_not_followed(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("TOP SECRET OUTSIDE")
    root = tmp_path / "proj"
    root.mkdir()
    (root / "ok.md").write_text("inside")
    os.symlink(outside, root / "link.md")
    os.symlink(tmp_path, root / "dirlink")
    _r, text, _ = cx.extract_folder(str(root))
    assert "inside" in text and "TOP SECRET OUTSIDE" not in text


@pytest.mark.parametrize("raw,why", [
    ("relative/path", "full path"),
    ("/definitely/not/here", "does not exist"),
    ("/", "specific project folder"),
])
def test_bad_folder_paths_are_refused(raw, why):
    with pytest.raises(ContextError, match=why):
        cx.extract_folder(raw)


def test_the_home_folder_itself_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ContextError, match="specific project folder"):
        cx.extract_folder(str(tmp_path))


def test_a_file_path_and_a_folder_with_nothing_readable_are_refused(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    with pytest.raises(ContextError, match="is a file"):
        cx.extract_folder(str(f))
    empty = tmp_path / "empty"
    (empty / "node_modules").mkdir(parents=True)
    with pytest.raises(ContextError, match="no text"):
        cx.extract_folder(str(empty))


def test_large_folders_are_cut_to_the_limits(tmp_path):
    root = tmp_path / "big"
    root.mkdir()
    for i in range(cx.FOLDER_MAX_FILES + 20):
        (root / f"f{i:03}.txt").write_text("x" * 5000)
    _r, text, truncated = cx.extract_folder(str(root))
    assert truncated and len(text) <= cx.MAX_STORED_CHARS + 200 and "more file(s) not included" in text


# ---------------------------------------------------------------- links

@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.1", "169.254.169.254",
                                "0.0.0.0", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "100.64.0.1", "224.0.0.1"])
def test_non_public_addresses_are_blocked(ip):
    assert cx._blocked_address(ip)


def test_public_addresses_are_allowed():
    assert not cx._blocked_address("93.184.216.34") and not cx._blocked_address("2606:4700:4700::1111")


def test_localhost_and_literal_private_hosts_do_not_resolve_as_public():
    for host in ("localhost", "127.0.0.1", "169.254.169.254", "[::1]".strip("[]")):
        with pytest.raises(ContextError, match="not on the public internet"):
            cx.resolve_public(host, 80)


PUBLIC = lambda host, port: "93.184.216.34"  # noqa: E731


def page(html: str, ctype="text/html; charset=utf-8", status=200):
    return httpx.MockTransport(lambda req: httpx.Response(status, headers={"content-type": ctype}, content=html.encode()))


async def test_a_page_is_fetched_as_text_with_its_title():
    html = "<html><head><title>Vendor terms</title><style>p{}</style></head><body><nav>menu</nav><p>Data stays in the EU.</p><script>evil()</script></body></html>"
    title, text, truncated = await cx.fetch_url("https://example.com/terms", transport=page(html), resolver=PUBLIC)
    assert title == "Vendor terms" and "Data stays in the EU." in text
    assert "evil" not in text and "menu" not in text and not truncated


async def test_the_request_is_pinned_to_the_checked_address_with_the_original_host():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["host"] = str(req.url), req.headers["host"]
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"ok")
    await cx.fetch_url("https://example.com:8443/a?b=1", transport=httpx.MockTransport(handler), resolver=PUBLIC)
    assert seen["url"].startswith("https://93.184.216.34:8443/a?b=1") and seen["host"] == "example.com:8443"


@pytest.mark.parametrize("url,why", [
    ("file:///etc/passwd", "http"),
    ("ftp://example.com/x", "http"),
    ("javascript:alert(1)", "http"),
    ("https://user:pw@example.com/", "username"),
    ("https://", "http"),
])
async def test_only_plain_web_addresses_are_accepted(url, why):
    with pytest.raises(ContextError, match=why):
        await cx.fetch_url(url, transport=page("x"), resolver=PUBLIC)


async def test_a_private_target_is_refused_before_any_request_is_made():
    def boom(req):
        raise AssertionError("no request should be made")
    for url in ("http://127.0.0.1:8000/tasks", "http://169.254.169.254/latest/meta-data/", "http://localhost/", "http://[::1]/"):
        with pytest.raises(ContextError, match="not on the public internet"):
            await cx.fetch_url(url, transport=httpx.MockTransport(boom))


async def test_a_redirect_to_a_private_address_is_refused():
    def resolver(host, port):  # the real resolver's check, with a fixed name -> address table
        ip = "10.0.0.5" if host == "internal.example" else "93.184.216.34"
        if cx._blocked_address(ip):
            raise ContextError("that address is not on the public internet, so it can't be attached")
        return ip

    def handler(req):
        return httpx.Response(302, headers={"location": "http://internal.example/admin"})
    with pytest.raises(ContextError, match="not on the public internet"):
        await cx.fetch_url("https://example.com/", transport=httpx.MockTransport(handler), resolver=resolver)


async def test_redirect_loops_and_chains_are_cut_off():
    handler = lambda req: httpx.Response(302, headers={"location": "https://example.com/again"})  # noqa: E731
    with pytest.raises(ContextError, match="too many times"):
        await cx.fetch_url("https://example.com/", transport=httpx.MockTransport(handler), resolver=PUBLIC)


async def test_a_redirect_to_a_public_page_is_followed():
    def handler(req):
        if req.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"moved here")
    _t, text, _ = await cx.fetch_url("https://example.com/old", transport=httpx.MockTransport(handler), resolver=PUBLIC)
    assert text == "moved here"


async def test_errors_oversize_and_unreadable_types_are_refused():
    with pytest.raises(ContextError, match="error \\(404\\)"):
        await cx.fetch_url("https://example.com/", transport=page("nope", status=404), resolver=PUBLIC)
    with pytest.raises(ContextError, match="larger than 2 MB"):
        await cx.fetch_url("https://example.com/", transport=page("a" * (cx.MAX_URL_BYTES + 1), "text/plain"), resolver=PUBLIC)
    with pytest.raises(ContextError, match="can't be read as text"):
        await cx.fetch_url("https://example.com/", transport=page("MZ", "application/octet-stream"), resolver=PUBLIC)
    with pytest.raises(ContextError, match="no readable text"):
        await cx.fetch_url("https://example.com/", transport=page("<html><body><script>x()</script></body></html>"), resolver=PUBLIC)


async def test_a_pdf_link_is_read():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, headers={"content-type": "application/pdf"}, content=pdf_bytes("Linked report")))
    _t, text, _ = await cx.fetch_url("https://example.com/r.pdf", transport=transport, resolver=PUBLIC)
    assert "Linked report" in text
