"""Model providers other than Claude, for discussion seats (docs/per-agent-models.md, "Route B").

A seat whose model id is `<provider>/<model>` (for example `openai/gpt-4o-mini`) is run by the orchestrator itself with
`Provider.complete` instead of by `claude -p` in the sandbox. These seats get text only: no file or shell tools.
"""
from .base import Completion, Provider, ProviderError
from .registry import provider_for, provider_id_of, provider_needs, provider_ready, split_model_id

__all__ = ["Completion", "Provider", "ProviderError", "provider_for", "provider_id_of", "provider_needs", "provider_ready", "split_model_id"]
