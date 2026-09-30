"""Resolves a Mode DB row's name to its modes/<name>.yaml file (M2.8).

Convention established by M2.5/M2.7 but never before given a concrete lookup path:
mode YAML files live at the repo root's `modes/` directory, resolved by `Mode.name`
(`modes/<Mode.name>.yaml`). `BAYTO_MODES_DIR` overrides the default, which is computed
relative to this file's own location -- correct for the editable dev install used
everywhere in this project today (same caveat class as `acp.floor.hand_raise_json_schema`'s
schema-path assumption); revisit if a build that doesn't ship the repo layout is ever used.
"""
from __future__ import annotations

import os
from pathlib import Path

from acp.modes import ModeConfig, load_mode

DEFAULT_MODES_DIR = Path(__file__).resolve().parents[3] / "modes"


class ModeNotFoundError(FileNotFoundError):
    pass


class ModeParseError(ValueError):
    pass


def modes_dir() -> Path:
    return Path(os.environ.get("BAYTO_MODES_DIR", DEFAULT_MODES_DIR))


def resolve_mode(name: str) -> ModeConfig:
    """Called both eagerly at POST /sessions (fail the request with 422 if this raises)
    and again at /start and each runner (re)start -- a mode file can change between the
    two, and the eager check does not guarantee it hasn't (see docs/decisions.md,
    2026-09-30; byte-identical replay across a mode-file edit remains an open M2.5 gap)."""
    path = modes_dir() / f"{name}.yaml"
    if not path.exists():
        raise ModeNotFoundError(f"no mode file at {path}")
    try:
        return load_mode(path)
    except Exception as exc:
        raise ModeParseError(f"{path}: {exc}") from exc
