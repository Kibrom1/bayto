"""GET /models (step 1 of docs/per-agent-models.md): the catalogue a session-setup picker
reads once. No DB access -- the catalogue is process config, not stored data."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..models_catalog import catalog, default_model_id
from ..providers import provider_needs
from ..providers.registry import LABELS

router = APIRouter()


class ModelOut(BaseModel):
    id: str
    label: str
    tier: str
    usd_per_turn: float
    default: bool
    provider: str = "anthropic"
    provider_label: str = "Anthropic"
    ready: bool = True          # false: the provider's key or address is not set, so the model cannot be chosen yet
    needs: str | None = None    # the environment variable that would make it ready


class ModelsOut(BaseModel):
    models: list[ModelOut]
    default: str


@router.get("/models", response_model=ModelsOut)
async def list_models() -> ModelsOut:
    return ModelsOut(
        models=[ModelOut(id=m.id, label=m.label, tier=m.tier, usd_per_turn=m.usd_per_turn, default=m.default,
                         provider=m.provider, provider_label=LABELS.get(m.provider, m.provider),
                         ready=provider_needs(m.provider) is None, needs=provider_needs(m.provider))
                for m in catalog()],
        default=default_model_id(),
    )
