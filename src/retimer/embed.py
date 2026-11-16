"""Text embeddings with a cache, for comparing lines by meaning across languages."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np

from retimer.cache import Cache, content_hash
from retimer.providers import DEFAULT_EMBEDDING_MODEL, Embeddings, OpenRouter

_CACHE_NAMESPACE = "embedding-v1"
_BATCH = 96
_WORKERS = 4


class Embedder:
    """Embeds texts once and remembers them. Counts what it spent."""

    def __init__(
        self,
        client: OpenRouter | None = None,
        cache: Cache | None = None,
        model: str = DEFAULT_EMBEDDING_MODEL,
    ) -> None:
        self._client = client
        self._cache = cache if cache is not None else Cache()
        self.model = model
        self.cost_usd = 0.0
        self.texts_embedded = 0
        self.texts_from_cache = 0

    def embed(self, texts: list[str]) -> np.ndarray:
        """One unit-length vector per text, as rows of a float32 matrix."""
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        keys = [content_hash(self.model, text) for text in texts]
        known = self._cache.get_many(_CACHE_NAMESPACE, list(set(keys)))
        vectors = {key: np.frombuffer(value, dtype=np.float32) for key, value in known.items()}
        self.texts_from_cache += sum(1 for key in keys if key in vectors)

        missing: dict[str, str] = {}
        for key, text in zip(keys, texts, strict=True):
            if key not in vectors:
                missing.setdefault(key, text)
        pending = list(missing.items())
        batches = [pending[start : start + _BATCH] for start in range(0, len(pending), _BATCH)]
        if batches and self._client is None:
            self._client = OpenRouter()

        def request(batch: list[tuple[str, str]]) -> Embeddings:
            return self._client.embed([text for _, text in batch], model=self.model)

        # A feature film is some thousand texts, so the batches go out side by side.
        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            results = list(pool.map(request, batches))
        for batch, result in zip(batches, results, strict=True):
            self.cost_usd += result.cost_usd
            self.texts_embedded += len(batch)
            fresh = {}
            for (key, _), vector in zip(batch, result.vectors, strict=True):
                array = np.asarray(vector, dtype=np.float32)
                norm = float(np.linalg.norm(array))
                array = array / norm if norm else array
                vectors[key] = array
                fresh[key] = array.tobytes()
            self._cache.put_many(_CACHE_NAMESPACE, fresh)
        return np.stack([vectors[key] for key in keys])
