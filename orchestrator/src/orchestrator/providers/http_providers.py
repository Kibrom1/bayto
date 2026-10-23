"""OpenAI-style and Gemini adapters over plain HTTP (httpx). Keys come from the environment only."""
from __future__ import annotations

import os

import httpx

from .base import Completion, ProviderError

TIMEOUT = float(os.environ.get("BAYTO_PROVIDER_TIMEOUT", "120"))


def _cost(tokens_in: int, tokens_out: int, price_in: float | None, price_out: float | None) -> float | None:
    """Prices are USD per million tokens, from the model's catalogue entry."""
    if price_in is None or price_out is None:
        return None
    return round(tokens_in * price_in / 1e6 + tokens_out * price_out / 1e6, 6)


def _fail(provider: str, resp: httpx.Response) -> ProviderError:
    # Show the provider's own error message, never the request (which holds the key).
    try:
        detail = resp.json().get("error", {})
        detail = detail.get("message") if isinstance(detail, dict) else str(detail)
    except Exception:  # noqa: BLE001
        detail = None
    return ProviderError(f"{provider} answered {resp.status_code}: {(detail or resp.text)[:300]}")


class OpenAICompatProvider:
    """OpenAI, Ollama (`/v1`) and any gateway that speaks the chat-completions API."""

    def __init__(self, provider_id: str, *, base_url: str, api_key: str | None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.id = provider_id
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._transport = transport

    async def complete(self, *, model, system, prompt, max_tokens=1500, price_in=None, price_out=None) -> Completion:
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        body = {"model": model, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as client:
                resp = await client.post(f"{self._base}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"could not reach {self.id}: {type(exc).__name__}") from None
        if resp.status_code != 200:
            raise _fail(self.id, resp)
        data = resp.json()
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise ProviderError(f"{self.id} sent an answer in an unexpected shape") from None
        usage = data.get("usage") or {}
        tin, tout = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        return Completion(text=text.strip(), tokens_in=tin, tokens_out=tout, cost=_cost(tin, tout, price_in, price_out))


class GeminiProvider:
    id = "gemini"

    def __init__(self, *, api_key: str, base_url: str = "https://generativelanguage.googleapis.com/v1beta",
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._transport = transport

    async def complete(self, *, model, system, prompt, max_tokens=1500, price_in=None, price_out=None) -> Completion:
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens}}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as client:
                resp = await client.post(f"{self._base}/models/{model}:generateContent", json=body,
                                         headers={"x-goog-api-key": self._key})  # a header, so it never lands in a URL log
        except httpx.HTTPError as exc:
            raise ProviderError(f"could not reach gemini: {type(exc).__name__}") from None
        if resp.status_code != 200:
            raise _fail("gemini", resp)
        data = resp.json()
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError):
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            raise ProviderError(f"gemini returned no answer{f' (blocked: {reason})' if reason else ''}") from None
        usage = data.get("usageMetadata") or {}
        tin, tout = int(usage.get("promptTokenCount") or 0), int(usage.get("candidatesTokenCount") or 0)
        return Completion(text=text.strip(), tokens_in=tin, tokens_out=tout, cost=_cost(tin, tout, price_in, price_out))
