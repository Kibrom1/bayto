"""Has Claude run out of usage or credit? Read from what the `claude` CLI itself reports.

Only the SHAPES below are recognised. They are written from what Claude Code is known to print and are NOT
verified against a real exhausted account (see docs/per-agent-models.md, "Local fallback"). That is why the structured
test is the primary one and why a short-message phrase match remains as a backstop:

1. Structured: the CLI's final `result` JSON (or `--output-format json` reply) with `is_error: true` whose text says a
   limit was hit, or whose `api_error_status` is 429. `is_error` is the guard: a seat that merely TALKS about rate limits
   in a normal answer never has it set, so a debate about API quotas cannot trigger a switch.
2. Backstop: a short message (a platform error is a line or two) from a place with no structured result, such as
   stderr of a failed command or an answer that arrived through the message log.

A reset time is taken from the message when it has one: `...limit reached|1760000000` (epoch seconds, the older CLI form),
`resets 5pm (America/New_York)`, `resets at 3:30 PM`, a `resetsAt` epoch, or `retry-after`/"try again in N minutes".
Anything else gives None and the caller waits DEFAULT_RETRY_MINUTES.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_RETRY_MINUTES = 30
_MAX_BACKSTOP_CHARS = 400  # a platform error is a line or two; a discussion about rate limiting is longer

# Phrases the CLI uses when the account, not the request, is out of quota or credit.
_LIMIT_PHRASES = ("weekly limit", "usage limit", "rate limit", "limit reached", "hit your limit", "out of usage",
                  "5-hour limit", "session limit", "credit balance is too low", "out of credits", "quota exceeded")
# Unstructured backstop: patterns, not bare words. "We should add a rate limit" is a short answer about rate limiting and
# must not match; "usage limit reached" and "you've hit your weekly limit" are the platform speaking.
_BACKSTOP = re.compile(
    r"(?:usage|weekly|daily|5-hour|five-hour|session|rate)\s+limit\s+(?:has\s+been\s+)?(?:reached|exceeded|hit)"
    r"|limit\s+reached|hit\s+your\s+(?:\w+\s+)?limit|reached\s+your\s+(?:\w+\s+)?limit"
    r"|out\s+of\s+(?:usage|credits?)|credit\s+balance\s+is\s+too\s+low"
    r"|(?:usage|weekly|5-hour)\s+limit[^.]{0,40}\bresets?\b", re.I)


@dataclass(frozen=True)
class LimitHit:
    message: str                      # what the platform said, trimmed; never holds a key
    reset_at: datetime | None = None  # UTC, when the message gave one
    source: str = "structured"        # "structured" | "backstop"

    def retry_at(self, now: datetime | None = None) -> datetime:
        """When to try Claude again: the reset time if it is in the future, else DEFAULT_RETRY_MINUTES from now."""
        now = now or datetime.now(timezone.utc)
        if self.reset_at is not None and self.reset_at > now:
            return self.reset_at
        return now + timedelta(minutes=DEFAULT_RETRY_MINUTES)


_EPOCH_SUFFIX = re.compile(r"\|\s*(\d{9,11})\s*$")
_RESETS_AT = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?:\s*\(([A-Za-z_]+/[A-Za-z_]+(?:/[A-Za-z_]+)?|UTC)\))?", re.I)
_RETRY_MIN = re.compile(r"(?:try again|retry)\s+in\s+(\d+)\s*(minute|min|hour|hr|second|sec)", re.I)


def _from_epoch(value: Any) -> datetime | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n > 1e12:  # milliseconds
        n /= 1000
    if n < 1e9:
        return None
    return datetime.fromtimestamp(n, tz=timezone.utc)


def parse_reset(text: str, *, now: datetime | None = None) -> datetime | None:
    """A reset time found in a limit message, as UTC, or None."""
    now = now or datetime.now(timezone.utc)
    m = _EPOCH_SUFFIX.search(text.strip())
    if m:
        return _from_epoch(m.group(1))
    m = _RETRY_MIN.search(text)
    if m:
        unit = m.group(2).lower()
        secs = int(m.group(1)) * (3600 if unit.startswith(("hour", "hr")) else 1 if unit.startswith("sec") else 60)
        return now + timedelta(seconds=secs)
    m = _RESETS_AT.search(text)
    if m:
        hour, minute, ampm, zone = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower(), m.group(4)
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            return None
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(zone) if zone else now.astimezone().tzinfo  # no zone named: the zone this server runs in (a guess)
        except Exception:  # noqa: BLE001  unknown zone name
            return None
        local_now = now.astimezone(tz)
        candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local_now:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)
    return None


def _text_says_limit(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in _LIMIT_PHRASES)


def detect_limit(payload: Any, *, now: datetime | None = None) -> LimitHit | None:
    """The limit a structured CLI result reports, or None. `payload` is the parsed final `result` object (stream-json or
    json output). Needs `is_error` (or an `error` object): a normal answer is never a limit, whatever it says."""
    if not isinstance(payload, dict):
        return None
    text = str(payload.get("result") or payload.get("error") or payload.get("message") or "")
    err = payload.get("error")
    if isinstance(err, dict):
        text = str(err.get("message") or text)
        etype = str(err.get("type") or "")
    else:
        etype = ""
    is_error = bool(payload.get("is_error")) or bool(err)
    if not is_error:
        return None
    status = payload.get("api_error_status")
    info = payload.get("rate_limit_info") or payload.get("rateLimitInfo")
    rate_limited = status == 429 or etype == "rate_limit_error" or (isinstance(info, dict) and info.get("status") == "rejected")
    if not (rate_limited or _text_says_limit(text)):
        return None
    reset = None
    if isinstance(info, dict):
        reset = _from_epoch(info.get("resetsAt") or info.get("resets_at"))
    reset = reset or _from_epoch(payload.get("resets_at")) or parse_reset(text, now=now)
    return LimitHit(message=text.strip()[:300] or "Claude usage limit reached", reset_at=reset)


def detect_limit_in_text(text: str | None, *, now: datetime | None = None) -> LimitHit | None:
    """Backstop for output with no structured result (stderr, a seat's message): a SHORT message with a limit phrase.
    If the text is a JSON result it is read as one instead."""
    t = (text or "").strip()
    if not t:
        return None
    if t.startswith("{"):
        try:
            import json
            hit = detect_limit(json.loads(t), now=now)
        except ValueError:
            hit = None
        if hit:
            return hit
    if len(t) <= _MAX_BACKSTOP_CHARS and _BACKSTOP.search(t):
        return LimitHit(message=t[:300], reset_at=parse_reset(t, now=now), source="backstop")
    return None


def looks_like_platform_limit(text: str | None) -> bool:
    """Kept for callers of the old name: the short-message backstop."""
    return detect_limit_in_text(text) is not None
