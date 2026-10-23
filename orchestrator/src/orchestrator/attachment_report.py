"""What a seat was actually shown of the attached material, for the room and the audit trail.

`attachment_report` builds the same prompt text as `attachment_prompt` and also says, per attachment, whether it went
in whole, as numbered passages, as its start only, or not at all. The report is read back from the text that was
built rather than recomputed, so it cannot disagree with what the seat read. It holds names and passage numbers only,
never attachment text (it is stored on the seat's message and written to the audit trail).
"""
from __future__ import annotations

import re
from typing import Iterable

from .attachment_prompt import PROMPT_BUDGET_CHARS, _Attachment, _label, attachment_prompt, visible_to
from .retrieval import split_chunks

# One fenced block as `attachment_prompt._block` writes it. Attachment text has its own `<attachment` escaped, so a
# match is always a block we wrote; a name never contains a double quote or angle bracket (`_label`).
_BLOCK = re.compile(r'<attachment kind="[^"]*" name="([^"]*)"[^>]*?(?: excerpt="parts ([0-9, ]+) of ([0-9]+)")?>\n(.*?)\n</attachment>',
                    re.DOTALL)


def attachment_report(attachments: Iterable[_Attachment], budget: int = PROMPT_BUDGET_CHARS,
                      query: str | None = None, seat: str | None = None) -> tuple[str, list[dict]]:
    """(prompt text, report). Report entries, in attachment order:
    {"name", "mode": "whole" | "excerpt" | "start" | "not_shown", "parts": [passage numbers, 1-based], "total": passages}.
    "start" is the old head cut (no passage numbers); "whole" lists no parts."""
    items = visible_to(attachments, seat)  # what this seat may not read is not in the prompt, the report or the audit
    text = attachment_prompt(items, budget=budget, query=query, seat=seat)
    blocks = list(_BLOCK.finditer(text))
    report, at = [], 0
    for a in items:
        total = len(split_chunks(a.content))
        m = blocks[at] if at < len(blocks) and blocks[at].group(1) == _label(a.name) else None
        if m is None:
            report.append({"name": a.name, "mode": "not_shown", "parts": [], "total": total})
            continue
        at += 1
        if m.group(2):
            parts = [int(n) for n in m.group(2).split(",")]
            report.append({"name": a.name, "mode": "excerpt", "parts": parts, "total": int(m.group(3))})
        elif m.group(4).endswith("[…cut to fit]"):
            report.append({"name": a.name, "mode": "start", "parts": [], "total": total})
        else:
            report.append({"name": a.name, "mode": "whole", "parts": [], "total": total})
    return text, report
