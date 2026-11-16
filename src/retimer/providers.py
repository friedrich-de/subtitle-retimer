"""The hosted AI services, behind one small client.

Speech-to-text and embeddings both go through OpenRouter, so one key covers
them. Nothing here knows about subtitles: the functions take bytes and text and
return what the provider said, plus what it cost.
"""

from __future__ import annotations

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


@dataclass
class Transcription:
    """The provider's verbose transcription response, and its price."""

    body: dict
    cost_usd: float


@dataclass
class Embeddings:
    vectors: list[list[float]]
    cost_usd: float


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


def _cost(body: dict) -> float:
    usage = body.get("usage") or {}
    try:
        return float(usage.get("cost") or 0.0)
    except (TypeError, ValueError):
        return 0.0
