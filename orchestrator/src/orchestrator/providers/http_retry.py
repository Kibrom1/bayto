"""One place for how the direct HTTP adapters call a provider: explicit timeouts, bounded retry, safe failure text.

Everything is tunable from the environment and read on each call:

- BAYTO_PROVIDER_CONNECT_TIMEOUT  seconds to open a connection (default 10)
- BAYTO_PROVIDER_TIMEOUT          seconds to wait for the answer to be read (default 120; also the write/pool limit)
- BAYTO_PROVIDER_MAX_RETRIES      extra attempts after the first (default 2; 0 turns retry off)
- BAYTO_PROVIDER_BACKOFF          first backoff in seconds, doubled each retry, with jitter (default 1.0)
- BAYTO_PROVIDER_MAX_WAIT         most seconds spent sleeping between attempts in total (default 30)

Retried: 429, 5xx, and failures to connect (refused, DNS, connect timeout, dropped before a reply). Never retried:
any other 4xx (a bad key or request will not get better), and a read timeout (the provider may still be working on
it, and a second try would double the bill). These calls are not streamed, so there is never partial output to
repeat. Retry-After (seconds or an HTTP date) is honoured; when it asks for more than the wait budget left, the call
fails at once instead of sleeping a shortened time. A failure reason never holds the request, so never the key.
"""
from __future__ import annotations

import asyncio
import email.utils
import logging
import os
import random
import re
import time

import httpx

from .base import ProviderError

log = logging.getLogger(__name__)

_RETRY_STATUS = frozenset({429, 500, 502, 503, 504, 529})
_KEYLIKE = re.compile(r"(sk-[A-Za-z0-9_\-*.]{6,}|AIza[A-Za-z0-9_\-]{10,}|Bearer\s+\S+)")


def _env_float(name: str, default: float, low: float = 0.0) -> float:
    try:
        return max(low, float(os.environ.get(name, default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


def read_timeout() -> float:
    return _env_float("BAYTO_PROVIDER_TIMEOUT", 120.0, 1.0)


def timeouts() -> httpx.Timeout:
    read = read_timeout()
    return httpx.Timeout(read, connect=_env_float("BAYTO_PROVIDER_CONNECT_TIMEOUT", 10.0, 0.5))


def redact(text: str, secrets: tuple[str | None, ...] = ()) -> str:
    """Remove the key (and anything shaped like one) from text that came from, or about, a provider."""
    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, "[redacted]")
    return _KEYLIKE.sub("[redacted]", text)


def error_detail(resp: httpx.Response, secrets: tuple[str | None, ...] = ()) -> str:
    try:
        body = resp.json()
        err = body.get("error", {}) if isinstance(body, dict) else {}
        detail = err.get("message") if isinstance(err, dict) else str(err)
    except Exception:  # noqa: BLE001
        detail = None
    return redact((detail or resp.text or "").strip()[:300], secrets)


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
        return max(0.0, when.timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def _describe(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.ConnectTimeout):
        return "timed out connecting"
    if isinstance(exc, httpx.TimeoutException):
        return f"timed out after {read_timeout():g}s waiting for an answer"
    if isinstance(exc, httpx.ConnectError):
        return "connection refused or host unreachable"
    return type(exc).__name__


async def post_json(url: str, *, provider: str, body: dict, headers: dict[str, str],
                    transport: httpx.AsyncBaseTransport | None = None,
                    secrets: tuple[str | None, ...] = (), what: str = "") -> httpx.Response:
    """POST and return the 200 response, or raise ProviderError with a safe reason. `what` names the call
    ("for embeddings") in messages."""
    retries = _env_int("BAYTO_PROVIDER_MAX_RETRIES", 2)
    backoff = _env_float("BAYTO_PROVIDER_BACKOFF", 1.0)
    budget = _env_float("BAYTO_PROVIDER_MAX_WAIT", 30.0)
    label = f"{provider}{' ' + what if what else ''}"
    attempt = 0
    while True:
        attempt += 1
        resp: httpx.Response | None = None
        problem = ""
        retryable = False
        try:
            async with httpx.AsyncClient(timeout=timeouts(), transport=transport) as client:
                resp = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            retryable = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError))
            problem = f"could not reach {label}: {_describe(exc)}"
        else:
            if resp.status_code == 200:
                return resp
            retryable = resp.status_code in _RETRY_STATUS
            problem = f"{provider} answered {resp.status_code}: {error_detail(resp, secrets)}"
        if not retryable:
            raise ProviderError(problem) from None
        if attempt > retries:
            raise ProviderError(f"{problem} (gave up after {attempt} attempts)") from None
        hinted = _retry_after(resp) if resp is not None else None
        delay = hinted if hinted is not None else backoff * (2 ** (attempt - 1)) * random.uniform(0.75, 1.25)
        if delay > budget:
            raise ProviderError(f"{problem} (gave up after {attempt} attempt{'s' if attempt > 1 else ''}; "
                                f"asked to wait {delay:.0f}s, more than the {budget:.0f}s allowed)") from None
        budget -= delay
        log.warning("%s: attempt %d failed (%s); retrying in %.1fs", provider, attempt,
                    resp.status_code if resp is not None else "no reply", delay)
        await asyncio.sleep(delay)
