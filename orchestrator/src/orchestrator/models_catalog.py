"""The catalogue of models a person may choose for a seat (step 1 of docs/per-agent-models.md).

This module validates NEW writes only: `is_known_model()` is called from
`POST /sessions`'s roster loop, never from a read path. A seat's stored model that predates
this catalogue (e.g. `seed.py`'s legacy `AGENT_MODEL`, "claude-sonnet-5") is simply carried
forward unchanged wherever it's read (`POST /sessions/{id}/continue`, `api/runtime.py`'s
`model=sa.model or a.model`) -- that's what keeps old sessions and seeded agents loading.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    id: str            # what is passed to `claude --model`
    label: str          # "Opus 5.5"
    tier: str            # "large" | "standard" | "small"
    usd_per_turn: float  # rough planning figure for a typical discussion turn, not a real cost
    default: bool = False


_BUILTIN: tuple[ModelInfo, ...] = (
    ModelInfo(id="claude-opus-5-5", label="Opus 5.5", tier="large", usd_per_turn=0.9),
    ModelInfo(id="claude-sonnet-5-5", label="Sonnet 5.5", tier="standard", usd_per_turn=0.5, default=True),
    ModelInfo(id="claude-haiku-5-5", label="Haiku 5.5", tier="small", usd_per_turn=0.1),
)


def _parse_extra_models() -> list[ModelInfo]:
    """`BAYTO_EXTRA_MODELS`: comma-separated `id:label:tier:usd_per_turn` entries. Read fresh
    from the environment on every call (not cached at import time) so a model added mid-process
    (or by a test's monkeypatch) is picked up immediately."""
    raw = os.environ.get("BAYTO_EXTRA_MODELS", "").strip()
    if not raw:
        return []
    out = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        model_id, label, tier, usd_per_turn = entry.split(":")
        out.append(ModelInfo(id=model_id, label=label, tier=tier, usd_per_turn=float(usd_per_turn)))
    return out


def catalog() -> list[ModelInfo]:
    return [*_BUILTIN, *_parse_extra_models()]


def default_model_id() -> str:
    return next(m.id for m in _BUILTIN if m.default)


def is_known_model(model_id: str | None) -> bool:
    """`None` (the field is omitted or explicitly null, meaning "use the agent's default")
    is always valid. Never call this on a value read back from storage -- see module docstring."""
    if model_id is None:
        return True
    return model_id in {m.id for m in catalog()}
