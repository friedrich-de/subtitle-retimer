import json

import httpx
import pytest

from retimer.providers import OpenRouter, ProviderError


def client_for(handler, **kwargs) -> OpenRouter:
    transport = httpx.MockTransport(handler)
    return OpenRouter("test-key", client=httpx.Client(transport=transport), **kwargs)


def test_transcribe_asks_for_word_timestamps_from_the_pinned_provider():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read().decode("latin-1")
        return httpx.Response(
            200, json={"text": "hi", "words": [], "segments": [], "usage": {"cost": 0.002}}
        )

    result = client_for(handler).transcribe(b"OggS", provider="groq", language="en")

    assert seen["path"].endswith("/audio/transcriptions")
    assert seen["auth"] == "Bearer test-key"
    body = seen["body"]
    assert body.count('name="timestamp_granularities[]"') == 2
    assert "verbose_json" in body
    assert json.dumps({"only": ["groq"], "allow_fallbacks": False}) in body
    assert 'name="language"' in body
    assert result.cost_usd == pytest.approx(0.002)


def test_transcription_without_word_timestamps_is_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "hi"})

    with pytest.raises(ProviderError, match="word timestamps"):
        client_for(handler).transcribe(b"OggS")


def test_embed_returns_vectors_in_the_order_asked():
    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.read())["input"]
        rows = [{"index": i, "embedding": [float(len(t))]} for i, t in enumerate(texts)]
        return httpx.Response(200, json={"data": rows[::-1], "usage": {"cost": 0.0001}})

    result = client_for(handler).embed(["a", "bbb", "cc"], model="some/model")

    assert result.vectors == [[1.0], [3.0], [2.0]]
    assert client_for(handler).embed([], model="some/model").vectors == []


def test_a_busy_provider_is_tried_again(monkeypatch):
    monkeypatch.setattr("retimer.providers.time.sleep", lambda seconds: None)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    assert client_for(handler).embed(["a"], model="m").vectors == [[1.0]]
    assert len(calls) == 3


def test_a_refused_request_fails_at_once_without_showing_the_key(monkeypatch):
    monkeypatch.setattr("retimer.providers.time.sleep", lambda seconds: None)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, text="no such key")

    with pytest.raises(ProviderError) as error:
        client_for(handler).embed(["a"], model="m")
    assert len(calls) == 1
    assert "test-key" not in str(error.value)


def test_no_key_is_reported_by_the_name_of_the_variable(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="OPENROUTER_API_KEY is not set"):
        OpenRouter().embed(["a"], model="m")
