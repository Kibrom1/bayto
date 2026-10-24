"""OpenAI-style and Gemini adapters over plain HTTP (httpx). Keys come from the environment only."""
from __future__ import annotations

import httpx

from .base import Completion, ProviderError

from .http_retry import error_detail, post_json, read_timeout

TIMEOUT = read_timeout()  # kept for importers; the adapters read the live value through http_retry.timeouts()


def _cost(tokens_in: int, tokens_out: int, price_in: float | None, price_out: float | None) -> float | None:
    """Prices are USD per million tokens, from the model's catalogue entry."""
    if price_in is None or price_out is None:
        return None
    return round(tokens_in * price_in / 1e6 + tokens_out * price_out / 1e6, 6)


def _fail(provider: str, resp: httpx.Response, secrets: tuple[str | None, ...] = ()) -> ProviderError:
    # Show the provider's own error message, never the request (which holds the key).
    return ProviderError(f"{provider} answered {resp.status_code}: {error_detail(resp, secrets)}")


def _json(provider: str, resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        raise ProviderError(f"{provider} sent an answer that is not JSON") from None
    if not isinstance(data, dict):
        raise ProviderError(f"{provider} sent an answer in an unexpected shape")
    return data


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
        resp = await post_json(f"{self._base}/chat/completions", provider=self.id, body=body, headers=headers,
                               transport=self._transport, secrets=(self._key,))
        data = _json(self.id, resp)
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
        resp = await post_json(f"{self._base}/models/{model}:generateContent", provider="gemini", body=body,
                               headers={"x-goog-api-key": self._key},  # a header, so it never lands in a URL log
                               transport=self._transport, secrets=(self._key,))
        data = _json("gemini", resp)
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError):
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            raise ProviderError(f"gemini returned no answer{f' (blocked: {reason})' if reason else ''}") from None
        usage = data.get("usageMetadata") or {}
        tin, tout = int(usage.get("promptTokenCount") or 0), int(usage.get("candidatesTokenCount") or 0)
        return Completion(text=text.strip(), tokens_in=tin, tokens_out=tout, cost=_cost(tin, tout, price_in, price_out))
