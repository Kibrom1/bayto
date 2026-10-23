"""GET /models (step 1 of docs/per-agent-models.md): the catalogue a session-setup picker
reads once. No DB access -- the catalogue is process config, not stored data."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..models_catalog import catalog, default_model_id

router = APIRouter()


class ModelOut(BaseModel):
    id: str
    label: str
    tier: str
    usd_per_turn: float
    default: bool


class ModelsOut(BaseModel):
    models: list[ModelOut]
    default: str


@router.get("/models", response_model=ModelsOut)
async def list_models() -> ModelsOut:
    return ModelsOut(
        models=[ModelOut(id=m.id, label=m.label, tier=m.tier, usd_per_turn=m.usd_per_turn, default=m.default)
                for m in catalog()],
        default=default_model_id(),
    )
