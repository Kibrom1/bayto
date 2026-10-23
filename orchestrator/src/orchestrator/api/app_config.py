"""GET /app-config: settings the web UI needs that come from the environment. For now only the
optional feedback destination (BAYTO_FEEDBACK_URL). No DB access."""
from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlsplit

from fastapi import APIRouter
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter()

_BAD_CHARS = re.compile(r"[\s\x00-\x1f\x7f]")


def feedback_url() -> str | None:
    """BAYTO_FEEDBACK_URL if it is a plain https or mailto URL, else None (with a log line).
    Anything else (javascript:, data:, http:, relative paths) is ignored at read time."""
    raw = os.environ.get("BAYTO_FEEDBACK_URL", "").strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
    except ValueError:
        parts = None
    ok = False
    if parts is not None and not _BAD_CHARS.search(raw):
        if parts.scheme == "https":
            ok = bool(parts.hostname)
        elif parts.scheme == "mailto":
            ok = bool(parts.path)
    if not ok:
        scheme = parts.scheme if parts is not None and parts.scheme else "none"
        log.warning("BAYTO_FEEDBACK_URL ignored: it must be an https or mailto URL (got scheme %r)", scheme)
        return None
    return raw


class AppConfigOut(BaseModel):
    feedback_url: str | None = None


@router.get("/app-config", response_model=AppConfigOut)
async def app_config() -> AppConfigOut:
    return AppConfigOut(feedback_url=feedback_url())
