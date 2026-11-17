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


def test_pictures_are_shown_to_the_model_each_after_its_label():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.read())
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"1": "a", "2": "b"}'}}],
                "usage": {"cost": 0.0004, "prompt_tokens": 300, "completion_tokens": 12},
            },
        )

    reading = client_for(handler).read_pictures(
        "Read them.",
        [("[1]", b"\x89PNG one"), ("[2]", b"\x89PNG two")],
        model="anthropic/claude-haiku-5.5",
        provider={"only": ["anthropic"], "allow_fallbacks": False},
        extra={"reasoning": {"effort": "low"}},
    )

    assert seen["path"].endswith("/chat/completions")
    body = seen["body"]
    assert body["model"] == "anthropic/claude-haiku-5.5"
    assert body["temperature"] == 0
    assert body["provider"] == {"only": ["anthropic"], "allow_fallbacks": False}
    assert body["reasoning"] == {"effort": "low"}
    assert body["messages"][0] == {"role": "system", "content": "Read them."}
    content = body["messages"][1]["content"]
    assert [part["type"] for part in content] == ["text", "image_url", "text", "image_url"]
    assert content[0]["text"] == "[1]"
    assert content[1]["image_url"]["url"] == "data:image/png;base64,iVBORyBvbmU="
    assert reading.text == '{"1": "a", "2": "b"}'
    assert reading.cost_usd == pytest.approx(0.0004)
    assert (reading.input_tokens, reading.output_tokens) == (300, 12)


def test_an_answer_in_parts_is_joined_and_one_without_text_is_an_error():
    def in_parts(request: httpx.Request) -> httpx.Response:
        content = [{"type": "text", "text": '{"1": '}, {"type": "text", "text": '"a"}'}]
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    assert client_for(in_parts).read_pictures("x", [("[1]", b"p")], model="m").text == '{"1": "a"}'

    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    with pytest.raises(ProviderError, match="without text"):
        client_for(empty, retries=0).read_pictures("x", [("[1]", b"p")], model="m")


@pytest.mark.parametrize(
    ("usage", "cost"),
    [
        ({"cost": 0.0004}, 0.0004),
        # The upstream's own key: OpenRouter's fee, and what the upstream charged.
        ({"cost": 0.0, "is_byok": True, "cost_details": {"upstream_inference_cost": 0.02}}, 0.02),
        (
            {"cost": 0.001, "is_byok": True, "cost_details": {"upstream_inference_cost": 0.02}},
            0.021,
        ),
        ({"cost": 0.0004, "cost_details": {"upstream_inference_cost": None}}, 0.0004),
        ({}, 0.0),
    ],
)
def test_the_cost_of_a_request_includes_what_the_upstream_charged(usage, cost):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "{}"}}], "usage": usage}
        )

    reading = client_for(handler).read_pictures("Read.", [("[1]", b"png")], model="m")

    assert reading.cost_usd == pytest.approx(cost)
