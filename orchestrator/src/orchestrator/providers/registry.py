"""Which provider runs a model id, and whether it is configured.

Model ids are `<provider>/<model>`; anything without a known provider prefix is a Claude model run by `claude -p`.
Keys are read from the environment on every call and never stored, logged or sent anywhere but the provider.
"""
from __future__ import annotations

import os

from .base import Provider, ProviderError
from .http_providers import GeminiProvider, OpenAICompatProvider

ANTHROPIC = "anthropic"
EXTERNAL = ("openai", "gemini", "ollama")
LABELS = {"anthropic": "Anthropic", "openai": "OpenAI", "gemini": "Google Gemini", "ollama": "Ollama (local)"}
KEY_HINT = {"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "ollama": "OLLAMA_HOST"}


def split_model_id(model_id: str) -> tuple[str, str]:
    """('openai', 'gpt-4o-mini') for 'openai/gpt-4o-mini'; ('anthropic', id) for everything else."""
    head, sep, tail = model_id.partition("/")
    if sep and head in EXTERNAL and tail:
        return head, tail
    return ANTHROPIC, model_id


def provider_id_of(model_id: str | None) -> str:
    return split_model_id(model_id)[0] if model_id else ANTHROPIC


def provider_ready(provider_id: str) -> bool:
    if provider_id == ANTHROPIC:
        return True
    if provider_id == "ollama":
        return bool(os.environ.get("OLLAMA_HOST"))  # a local server has no key; setting its address opts in
    return bool(os.environ.get(KEY_HINT.get(provider_id, "")))


def provider_for(provider_id: str) -> Provider:
    if not provider_ready(provider_id) or provider_id == ANTHROPIC:
        hint = KEY_HINT.get(provider_id)
        raise ProviderError(f"{LABELS.get(provider_id, provider_id)} is not set up: set {hint} and restart Bayto"
                            if hint else f"{provider_id} is not an external provider")
    if provider_id == "gemini":
        return GeminiProvider(api_key=os.environ["GEMINI_API_KEY"])
    if provider_id == "openai":
        return OpenAICompatProvider("openai", base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                                    api_key=os.environ["OPENAI_API_KEY"])
    host = os.environ["OLLAMA_HOST"].rstrip("/")
    return OpenAICompatProvider("ollama", base_url=host if host.endswith("/v1") else f"{host}/v1", api_key=None)
