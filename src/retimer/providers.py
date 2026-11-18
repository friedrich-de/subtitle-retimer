"""The hosted AI services, behind one small client.

Speech-to-text, embeddings and the reading of pictures all go through
OpenRouter, so one key covers them. Nothing here knows about subtitles: the
functions take bytes and text and return what the provider said, plus what it
cost.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1"
OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"

DEFAULT_SPEECH_MODEL = "openai/whisper-large-v3-turbo"
# Of the upstreams that serve the speech model, this one was the fastest and
# found the most dialogue on the seed films. One of the others returned loops of
# invented text, so the upstream is always pinned.
DEFAULT_SPEECH_PROVIDER = "groq"
DEFAULT_EMBEDDING_MODEL = "baai/bge-m3"

_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 524, 529}
_ERROR_CHARS = 300


class ProviderError(RuntimeError):
    """A provider call failed for good. The message never contains the key."""


class ModelUnavailable(ProviderError):
    """The model that settles a job could not be asked: no key, no connection, or an error answer.

    A model is part of every deployment, so a job is not delivered without
    what the model was to settle. It fails and can be sent again. `cost_usd`
    is what the calls before the failure cost.
    """

    def __init__(self, message: str, cost_usd: float = 0.0) -> None:
        super().__init__(message)
        self.cost_usd = cost_usd


@dataclass
class Transcription:
    """The provider's verbose transcription response, and its price."""

    body: dict
    cost_usd: float


@dataclass
class Embeddings:
    vectors: list[list[float]]
    cost_usd: float


@dataclass
class Reading:
    """What a model that can see wrote about the pictures it was shown."""

    text: str
    cost_usd: float
    input_tokens: int = 0
    output_tokens: int = 0


class OpenRouter:
    """A thin client for the two OpenRouter endpoints the service uses."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = OPENROUTER_URL,
        timeout_s: float = 120.0,
        retries: int = 3,
        client: httpx.Client | None = None,
    ) -> None:
        self._key = api_key or os.environ.get(OPENROUTER_KEY_ENV)
        self._base_url = base_url.rstrip("/")
        self._retries = retries
        self._client = client or httpx.Client(timeout=timeout_s)

    def _post(self, path: str, **kwargs) -> dict:
        if not self._key:
            raise ProviderError(f"{OPENROUTER_KEY_ENV} is not set")
        headers = {"Authorization": f"Bearer {self._key}"}
        problem = "no attempt was made"
        for attempt in range(self._retries + 1):
            if attempt:
                time.sleep(min(2.0**attempt, 20.0))
            try:
                response = self._client.post(f"{self._base_url}{path}", headers=headers, **kwargs)
            except httpx.HTTPError as error:
                problem = f"{type(error).__name__}"
                continue
            if response.status_code == 200:
                try:
                    body = response.json()
                except json.JSONDecodeError:
                    problem = "the response was not JSON"
                    continue
                # OpenRouter reports some upstream failures inside a 200 response.
                if isinstance(body, dict) and "error" in body and len(body) == 1:
                    problem = f"provider error: {str(body['error'])[:_ERROR_CHARS]}"
                    continue
                return body
            problem = f"HTTP {response.status_code}: {response.text[:_ERROR_CHARS]}"
            if response.status_code not in _RETRY_STATUS:
                break
        raise ProviderError(f"{path} failed: {problem}")

    def transcribe(
        self,
        audio: bytes,
        *,
        filename: str = "audio.ogg",
        content_type: str = "audio/ogg",
        model: str = DEFAULT_SPEECH_MODEL,
        language: str | None = None,
        provider: str | None = None,
    ) -> Transcription:
        """Transcribe audio with word and segment timestamps.

        `provider` pins the upstream that serves the model, by its OpenRouter
        slug. The same model gives very different transcripts at different
        upstreams, so a pinned provider is part of what was benchmarked.
        """
        data: dict[str, str | list[str]] = {
            "model": model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": ["word", "segment"],
            "temperature": "0",
        }
        if language:
            data["language"] = language
        if provider:
            data["provider"] = json.dumps({"only": [provider], "allow_fallbacks": False})
        body = self._post(
            "/audio/transcriptions",
            data=data,
            files={"file": (filename, audio, content_type)},
        )
        if "words" not in body:
            raise ProviderError("the transcription came back without word timestamps")
        return Transcription(body=body, cost_usd=_cost(body))

    def embed(self, texts: list[str], *, model: str = DEFAULT_EMBEDDING_MODEL) -> Embeddings:
        """Embed texts. The result has one vector per text, in the same order."""
        if not texts:
            return Embeddings(vectors=[], cost_usd=0.0)
        body = self._post("/embeddings", json={"model": model, "input": texts})
        rows = sorted(body.get("data", []), key=lambda row: row.get("index", 0))
        if len(rows) != len(texts):
            raise ProviderError(f"asked for {len(texts)} embeddings and got {len(rows)}")
        return Embeddings(vectors=[row["embedding"] for row in rows], cost_usd=_cost(body))

    def read_pictures(
        self,
        instructions: str,
        pictures: list[tuple[str, bytes]],
        *,
        model: str,
        max_tokens: int = 4000,
        provider: dict | None = None,
        extra: dict | None = None,
    ) -> Reading:
        """Show PNG pictures to a model that can see, each after its label, and return its answer.

        `provider` is OpenRouter's routing object, to pin the upstream that
        serves the model. `extra` is merged into the request, for what only
        some models take.
        """
        content: list[dict] = []
        for label, png in pictures:
            content.append({"type": "text", "text": label})
            data = base64.b64encode(png).decode("ascii")
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}
            )
        request: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": content},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
            "usage": {"include": True},
        }
        if provider:
            request["provider"] = provider
        if extra:
            request.update(extra)
        body = self._post("/chat/completions", json=request)
        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderError("the model's answer came back without text") from error
        if isinstance(text, list):
            # Some models answer in parts.
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
        usage = body.get("usage") or {}
        return Reading(
            text=text or "",
            cost_usd=_cost(body),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )


def _cost(body: dict) -> float:
    """What a request cost, from what OpenRouter reports about it.

    Where the account holds the upstream's own key for a provider, OpenRouter
    reports only its own fee as `cost`, often nothing, and what the upstream
    charged on that key apart, as `cost_details.upstream_inference_cost`. Both
    are spent.
    """
    usage = body.get("usage") or {}
    try:
        cost = float(usage.get("cost") or 0.0)
        details = usage.get("cost_details")
        upstream = details.get("upstream_inference_cost") if isinstance(details, dict) else None
        if upstream and (usage.get("is_byok") or cost == 0.0):
            cost += float(upstream)
        return cost
    except (TypeError, ValueError):
        return 0.0
