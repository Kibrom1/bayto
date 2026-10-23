"""Provider adapters (OpenAI-style, Gemini), the registry, and the catalogue rules for non-Claude models."""
import json

import httpx
import pytest

from orchestrator import models_catalog
from orchestrator.providers import ProviderError, provider_for, provider_id_of, provider_ready, split_model_id
from orchestrator.providers.http_providers import GeminiProvider, OpenAICompatProvider


def _transport(handler):
    return httpx.MockTransport(handler)


def test_model_ids_name_their_provider():
    assert split_model_id("openai/gpt-4o-mini") == ("openai", "gpt-4o-mini")
    assert split_model_id("ollama/llama3:8b") == ("ollama", "llama3:8b")
    assert split_model_id("claude-opus-5-5") == ("anthropic", "claude-opus-5-5")
    assert split_model_id("acme/model") == ("anthropic", "acme/model")  # not a known provider prefix
    assert provider_id_of(None) == "anthropic"


def test_readiness_comes_from_the_environment(monkeypatch):
    for var in ("OPENAI_API_KEY", "GEMINI_API_KEY", "OLLAMA_HOST"):
        monkeypatch.delenv(var, raising=False)
    assert provider_ready("anthropic") and not provider_ready("openai") and not provider_ready("ollama")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    assert provider_ready("openai") and provider_ready("ollama")
    with pytest.raises(ProviderError, match="GEMINI_API_KEY"):
        provider_for("gemini")


async def test_openai_style_request_and_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers.get("authorization"), json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": " Hello there. "}}],
                                         "usage": {"prompt_tokens": 1000, "completion_tokens": 200}})

    p = OpenAICompatProvider("openai", base_url="https://api.example/v1", api_key="sk-secret", transport=_transport(handler))
    out = await p.complete(model="gpt-x", system="be brief", prompt="hi", price_in=0.5, price_out=2.0)
    assert out.text == "Hello there." and (out.tokens_in, out.tokens_out) == (1000, 200)
    assert out.cost == pytest.approx(0.0009)  # 1000*0.5/1e6 + 200*2/1e6
    assert seen["url"] == "https://api.example/v1/chat/completions" and seen["auth"] == "Bearer sk-secret"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "be brief"}


async def test_cost_is_unknown_without_prices():
    t = _transport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "x"}}], "usage": {}}))
    out = await OpenAICompatProvider("ollama", base_url="http://h/v1", api_key=None, transport=t).complete(model="m", system="s", prompt="p")
    assert out.cost is None


async def test_a_rejected_request_fails_loudly_without_leaking_the_key():
    t = _transport(lambda r: httpx.Response(401, json={"error": {"message": "Incorrect API key provided"}}))
    p = OpenAICompatProvider("openai", base_url="https://api.example/v1", api_key="sk-secret", transport=t)
    with pytest.raises(ProviderError) as err:
        await p.complete(model="m", system="s", prompt="p")
    assert "401" in str(err.value) and "Incorrect API key" in str(err.value) and "sk-secret" not in str(err.value)


async def test_an_unreachable_provider_is_a_provider_error():
    def boom(request):
        raise httpx.ConnectError("refused", request=request)
    with pytest.raises(ProviderError, match="could not reach openai"):
        await OpenAICompatProvider("openai", base_url="http://x/v1", api_key="k", transport=_transport(boom)).complete(
            model="m", system="s", prompt="p")


async def test_gemini_request_and_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["key"], seen["body"] = str(request.url), request.headers.get("x-goog-api-key"), json.loads(request.content)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Hi "}, {"text": "there"}]}}],
                                         "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3}})

    out = await GeminiProvider(api_key="g-secret", transport=_transport(handler)).complete(model="gem-1", system="s", prompt="p")
    assert out.text == "Hi there" and (out.tokens_in, out.tokens_out) == (10, 3)
    assert seen["url"].endswith("/models/gem-1:generateContent") and "g-secret" not in seen["url"] and seen["key"] == "g-secret"
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "s"


async def test_gemini_blocked_answer_is_explained():
    t = _transport(lambda r: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
    with pytest.raises(ProviderError, match="blocked: SAFETY"):
        await GeminiProvider(api_key="k", transport=t).complete(model="m", system="s", prompt="p")


def test_catalogue_entries_can_carry_a_provider_and_prices(monkeypatch):
    monkeypatch.setenv("BAYTO_EXTRA_MODELS", "openai/gpt-x:GPT X:small:0.05:0.15:0.6,ollama/llama3:Llama 3:local:0")
    gpt, llama = models_catalog.get_model("openai/gpt-x"), models_catalog.get_model("ollama/llama3")
    assert (gpt.provider, gpt.price_in, gpt.price_out) == ("openai", 0.15, 0.6)
    assert llama.provider == "ollama" and llama.price_in is None
    monkeypatch.setenv("BAYTO_EXTRA_MODELS", "openai/gpt-x:GPT X:small")
    with pytest.raises(ValueError, match="expected id:label"):
        models_catalog.catalog()


def test_seat_model_rules(monkeypatch):
    monkeypatch.setenv("BAYTO_EXTRA_MODELS", "openai/gpt-x:GPT X:small:0.05")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    check = models_catalog.seat_model_problem
    assert check("claude-opus-5-5") is None and check(None) is None
    assert check("nope")[0] == "invalid_model"
    assert check("openai/gpt-x")[0] == "provider_not_configured"
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    assert check("openai/gpt-x") is None
    assert check("openai/gpt-x", build=True)[0] == "provider_not_supported"
    assert check("openai/gpt-x", synthesis=True)[0] == "provider_not_supported"
