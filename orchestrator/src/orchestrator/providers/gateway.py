"""Route A: a seat on an Anthropic-compatible gateway (docs/per-agent-models.md, "Setting up a gateway").

Unlike the HTTP providers, a gateway seat still runs `claude -p` in the sandbox, so it keeps tool use and file access. Its
`claude` process is pointed at the gateway with ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN. This module only builds that
environment; it never reads or sets anything for any other seat, and the values are never logged or audited.

Settings (orchestrator environment): BAYTO_GATEWAY_URL (required), BAYTO_GATEWAY_TOKEN (optional), and
BAYTO_ALLOW_GATEWAY_BUILD=1 to let a Build seat use a gateway model (off by default; see `gateway_build_allowed`).
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from .base import ProviderError

GATEWAY = "gateway"
URL_VAR = "BAYTO_GATEWAY_URL"
TOKEN_VAR = "BAYTO_GATEWAY_TOKEN"
# A gateway that needs no key still needs *some* token value: without one `claude` would send the user's own Claude login
# to the gateway. This placeholder is not a secret.
BUILD_FLAG = "BAYTO_ALLOW_GATEWAY_BUILD"
PLACEHOLDER_TOKEN = "bayto-gateway-no-token"
# The only variables a per-seat override may carry into the sandbox.
ALLOWED_OVERRIDE = re.compile(r"^ANTHROPIC_[A-Z_]+$")


def gateway_build_allowed() -> bool:
    """Whether a Build seat may run on a gateway model. An explicit opt-in, off by default: tool use through a gateway
    varies by model, and the seat's permissions are still enforced by `claude` itself (the same --allowedTools /
    dontAsk flags as any Build seat), not by the gateway or the model."""
    return os.environ.get(BUILD_FLAG, "").strip() == "1"


BUILD_FALLBACK_VAR = "BAYTO_BUILD_FALLBACK_MODEL"


def build_fallback_model() -> str | None:
    """The bare gateway model name a Build seat switches to when Claude is out of usage, or None when that is off.

    On only when ALL of these hold: BAYTO_BUILD_FALLBACK_MODEL names a model, the gateway is set up (BAYTO_GATEWAY_URL)
    and Build seats may use a gateway (BAYTO_ALLOW_GATEWAY_BUILD=1). The seat keeps its tools and its permission flags:
    only the model behind its `claude -p` changes, for that turn."""
    name = (os.environ.get(BUILD_FALLBACK_VAR) or "").strip()
    if not name or any(c in name for c in "\r\n\0 ") or gateway_url() is None or not gateway_build_allowed():
        return None
    return name


def gateway_url() -> str | None:
    raw = os.environ.get(URL_VAR, "").strip().rstrip("/")
    parsed = urlparse(raw)
    return raw if parsed.scheme in ("http", "https") and parsed.hostname else None


def gateway_host() -> str | None:
    url = gateway_url()
    return urlparse(url).hostname if url else None


def gateway_model_name(model_id: str) -> str:
    """'qwen3' for 'gateway/qwen3': the name the gateway knows the model by."""
    head, _, tail = model_id.partition("/")
    return tail if head == GATEWAY and tail else model_id


def gateway_env(model_id: str) -> dict[str, str]:
    """The environment for the `claude` process of ONE seat whose model is `gateway/<name>`.

    Raises ProviderError (message safe to show) when the gateway is not set up. Never call it for another seat."""
    if not model_id.startswith(f"{GATEWAY}/") or len(model_id) == len(GATEWAY) + 1:
        raise ProviderError(f"{model_id} is not a gateway model")
    url = gateway_url()
    if url is None:
        raise ProviderError(f"the gateway is not set up: set {URL_VAR} to its http(s) address and restart Bayto")
    token = os.environ.get(TOKEN_VAR, "").strip() or PLACEHOLDER_TOKEN
    return {"ANTHROPIC_BASE_URL": url, "ANTHROPIC_AUTH_TOKEN": token}


def check_override(env: dict[str, str]) -> None:
    """Refuse an override that could smuggle other variables or break the line-based hand-over to the sandbox."""
    for key, value in env.items():
        if not ALLOWED_OVERRIDE.match(key):
            raise ValueError(f"{key!r} is not an allowed per-seat variable")
        if any(c in value for c in "\r\n\0"):
            raise ValueError(f"{key} holds a line break or NUL")


def redact(text: str, env: dict[str, str] | None) -> str:
    """`text` with every override value (the token above all) replaced, for an error that may echo the child's output."""
    for value in (env or {}).values():
        if value and len(value) >= 4:
            text = text.replace(value, "[redacted]")
    return text
