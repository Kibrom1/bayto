"""Embedding client for the opt-in semantic stage of attachment retrieval (docs/per-agent-context-rag.md, P4).

Two back ends, both over plain HTTP like http_providers.py, both configured from the environment only:

- OpenAI (or anything that speaks `/v1/embeddings`): OPENAI_API_KEY, OPENAI_BASE_URL (optional), and the model in
  BAYTO_EMBEDDING_MODEL (default text-embedding-3-small).
- Ollama: OLLAMA_HOST, `/api/embed`, and the model in BAYTO_OLLAMA_EMBEDDING_MODEL (default nomic-embed-text).

OpenAI is used when its key is set, otherwise Ollama when its host is set; BAYTO_EMBEDDING_PROVIDER=openai|ollama
forces one. Keys are read on every call, never stored or logged, and an error carries the provider's own message,
never the request. Whatever is embedded leaves this machine for the provider (an Ollama host on this machine
excepted): that is the attachments' chunks and the retrieval query, nothing else (see semantic.py).
"""
from __future__ import annotations

import os

import httpx

from .base import ProviderError
from .http_providers import _json
from .http_retry import post_json

DEFAULT_OPENAI_MODEL = "text-embedding-3-small"
DEFAULT_OLLAMA_MODEL = "nomic-embed-text"
BATCH = 64   # texts per request
NEEDS = "OPENAI_API_KEY or OLLAMA_HOST"


def embedding_provider() -> str | None:
    """'openai', 'ollama', or None when neither is set up."""
    forced = os.environ.get("BAYTO_EMBEDDING_PROVIDER", "").strip().lower()
    if forced in ("openai", "ollama"):
        var = "OPENAI_API_KEY" if forced == "openai" else "OLLAMA_HOST"
        return forced if os.environ.get(var) else None
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("OLLAMA_HOST"):
        return "ollama"
    return None


def embedding_model(provider: str) -> str:
    if provider == "ollama":
        return os.environ.get("BAYTO_OLLAMA_EMBEDDING_MODEL") or DEFAULT_OLLAMA_MODEL
    return os.environ.get("BAYTO_EMBEDDING_MODEL") or DEFAULT_OPENAI_MODEL


def embeddings_ready() -> bool:
    return embedding_provider() is not None


def embedding_tag() -> str | None:
    """'<provider>:<model>' as stored beside cached vectors, so a change of either invalidates them."""
    provider = embedding_provider()
    return f"{provider}:{embedding_model(provider)}" if provider else None


class EmbeddingClient:
    def __init__(self, provider: str, *, base_url: str, api_key: str | None, model: str,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.provider = provider
        self.model = model
        self.tag = f"{provider}:{model}"
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._transport = transport

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per text, in order. Raises ProviderError on any failure."""
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            out.extend(await self._request(texts[i:i + BATCH]))
        return out

    async def _request(self, batch: list[str]) -> list[list[float]]:
        if self.provider == "ollama":
            url, headers, body = f"{self._base}/api/embed", {}, {"model": self.model, "input": batch}
        else:
            url, body = f"{self._base}/embeddings", {"model": self.model, "input": batch}
            headers = {"Authorization": f"Bearer {self._key}"}
        resp = await post_json(url, provider=self.provider, body=body, headers=headers, transport=self._transport,
                               secrets=(self._key,), what="for embeddings")
        data = _json(self.provider, resp)
        try:
            if self.provider == "ollama":
                vecs = data["embeddings"]
            else:
                vecs = [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]
            vecs = [[float(x) for x in v] for v in vecs]
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderError(f"{self.provider} sent embeddings in an unexpected shape") from None
        if len(vecs) != len(batch) or not all(vecs) or len({len(v) for v in vecs}) != 1:
            raise ProviderError(f"{self.provider} sent the wrong number of embeddings")
        return vecs


def embedding_client(transport: httpx.AsyncBaseTransport | None = None) -> EmbeddingClient:
    provider = embedding_provider()
    if provider is None:
        raise ProviderError(f"embeddings are not set up: set {NEEDS} and restart Bayto")
    if provider == "ollama":
        host = os.environ["OLLAMA_HOST"].rstrip("/")
        host = host[:-3] if host.endswith("/v1") else host   # /api/embed lives beside /v1, not under it
        return EmbeddingClient("ollama", base_url=host, api_key=None, model=embedding_model("ollama"), transport=transport)
    return EmbeddingClient("openai", base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                           api_key=os.environ["OPENAI_API_KEY"], model=embedding_model("openai"), transport=transport)
