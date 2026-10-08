"""Headless turn runner events (M1.13 verdict: message-level output is not enough for the room).

A streamed turn is one `claude -p --output-format stream-json --verbose --include-partial-messages`
process run through `sbx exec` in the team sandbox. This module turns its stdout lines into a small set
of events the moderator can publish to the room as they arrive. Grounded in the real stream captured by
`spikes/m1-checks/m1-13-streaming.sh` (event types seen: system/init, stream_event with
content_block_delta/text_delta and thinking_delta, assistant, rate_limit_event, result/success).
Tool-activity events, long turns and several seats streaming at once were NOT covered by that capture.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ToolUse:
    """A tool call started (name only; arguments are not streamed to the room)."""
    name: str


@dataclass(frozen=True, slots=True)
class TurnResult:
    text: str
    tokens_in: int  # input + cache creation + cache read: everything the model was fed
    tokens_out: int
    cost: float | None
    session_id: str | None
    is_error: bool = False


TurnEvent = TextDelta | ToolUse | TurnResult


def parse_stream_line(line: str) -> TurnEvent | None:
    """One stdout line -> event, or None for anything the room does not show (init, thinking, rate
    limit, malformed or non-JSON lines such as shell noise)."""
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    kind = obj.get("type")
    if kind == "stream_event":
        ev = obj.get("event") or {}
        if ev.get("type") == "content_block_delta":
            delta = ev.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                return TextDelta(text=delta["text"])
        elif ev.get("type") == "content_block_start":
            block = ev.get("content_block") or {}
            if block.get("type") == "tool_use":
                return ToolUse(name=block.get("name", "?"))
        return None
    if kind == "result":
        usage = obj.get("usage") or {}
        tokens_in = sum(int(usage.get(k) or 0) for k in
                        ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        cost = obj.get("total_cost_usd")
        return TurnResult(
            text=obj.get("result") or "",
            tokens_in=tokens_in,
            tokens_out=int(usage.get("output_tokens") or 0),
            cost=float(cost) if cost is not None else None,
            session_id=obj.get("session_id"),
            is_error=bool(obj.get("is_error")) or obj.get("subtype") not in (None, "success"),
        )
    return None
