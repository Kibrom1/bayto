"""Opt-in failover of one discussion seat from Claude to a local model when Claude is out of usage or credit.

A task carries `local_fallback = {"enabled": bool, "model": "ollama/<name>" | None}`; None model means the environment
default `BAYTO_LOCAL_FALLBACK_MODEL`. Off unless enabled. The runner (moderator/runner.py) does the switching; this module
is the configuration and the validation shared with the API.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

ENV_MODEL = "BAYTO_LOCAL_FALLBACK_MODEL"
PROVIDER = "ollama"


def env_default_model() -> str | None:
    return (os.environ.get(ENV_MODEL) or "").strip() or None


@dataclass(frozen=True)
class FallbackConfig:
    enabled: bool
    model: str | None  # resolved: the task's own model, else the environment default

    @property
    def usable(self) -> bool:
        return self.enabled and bool(self.model)


def resolve(raw: dict | None) -> FallbackConfig:
    """The effective setting for a task's stored `local_fallback` value."""
    raw = raw or {}
    return FallbackConfig(enabled=bool(raw.get("enabled")), model=(raw.get("model") or None) or env_default_model())


def model_problem(model: str | None) -> tuple[str, str] | None:
    """(error code, message) when this cannot be the local fallback model, else None. It must be a catalogue model that
    runs on the direct Ollama adapter, with OLLAMA_HOST set: the same check a discussion seat on that model gets."""
    from .models_catalog import seat_model_problem
    from .providers import provider_id_of
    if not model:
        return "no_model", f"choose a local model, or set {ENV_MODEL} in the environment Bayto runs in"
    if provider_id_of(model) != PROVIDER:
        return "invalid_model", f"{model} is not a local Ollama model (use one of the ollama/... models)"
    return seat_model_problem(model)
