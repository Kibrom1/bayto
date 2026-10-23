from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ProviderError(RuntimeError):
    """A provider call failed (no key, rejected, timed out). The message is safe to show: it never holds a key."""


@dataclass(frozen=True)
class Completion:
    text: str
    tokens_in: int
    tokens_out: int
    cost: float | None  # None when the model's price is not configured


class Provider(Protocol):
    id: str

    async def complete(self, *, model: str, system: str, prompt: str, max_tokens: int = 1500,
                       price_in: float | None = None, price_out: float | None = None) -> Completion: ...
