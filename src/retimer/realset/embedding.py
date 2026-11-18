"""A local stand-in for the embedding provider, and the set's embedding cache.

The service asks a hosted model for its embeddings. Over thousands of real
files that is slow and it is billed, so the kit runs the same model, bge-m3,
on this machine and puts what it computes into a cache that stays with the
set. The pipeline then reads every embedding from that cache: a run asks no
provider, costs nothing, and gives the same result every time.

The local model needs `sentence-transformers`, which is not a dependency of
the service. It is only loaded when a text is not in the cache yet.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import numpy as np

from retimer.cache import Cache
from retimer.classify import classify_lines
from retimer.embed import Embedder
from retimer.io import SubtitleError, load
from retimer.match import context_texts
from retimer.model import Subtitle
from retimer.providers import DEFAULT_EMBEDDING_MODEL, Embeddings, ProviderError
from retimer.reference import timeline_from_subtitle
from retimer.views import build_match_view

# The model the provider serves as `baai/bge-m3`, by its name where models are published.
LOCAL_MODEL = "BAAI/bge-m3"
_EMBEDDING_NAMESPACE = "embedding"
_PAGE_SIZE = 65_536


class LocalEmbeddings:
    """Answers `embed` as the provider's client does, from a model on this machine."""

    def __init__(self, model_name: str = LOCAL_MODEL, batch_size: int = 128) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        try:
            import torch
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise ProviderError(
                "the local embedding model needs the sentence-transformers package"
            ) from error
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = SentenceTransformer(self.model_name, device=device)
        if device == "cuda":
            model.half()
        return model

    def embed(self, texts: list[str], *, model: str = DEFAULT_EMBEDDING_MODEL) -> Embeddings:
        if model != DEFAULT_EMBEDDING_MODEL:
            raise ProviderError(f"the local stand-in only knows {DEFAULT_EMBEDDING_MODEL}")
        if not texts:
            return Embeddings(vectors=[], cost_usd=0.0)
        # The pipeline asks from several threads. The model takes one batch at a time.
        with self._lock:
            if self._model is None:
                self._model = self._load()
            vectors = self._model.encode(
                texts, batch_size=self.batch_size, normalize_embeddings=True
            )
        return Embeddings(vectors=list(np.asarray(vectors, dtype=np.float32)), cost_usd=0.0)


class CachedOnly:
    """Stands where the provider's client stands, and refuses to be asked.

    A run over the set is meant to read every embedding from the cache. A
    text that is missing there is a reason to fill the cache first, not to
    load a model in every worker.
    """

    def embed(self, texts: list[str], *, model: str = DEFAULT_EMBEDDING_MODEL) -> Embeddings:
        raise ProviderError(
            f"{len(texts)} texts are not in the set's embedding cache: "
            "run `retimer realset embed` first"
        )


class CompactCache(Cache):
    """The set's cache. Embeddings are kept at half precision, which halves the file.

    The pipeline never sees a value that was not stored first, so what it
    reads is the same on the first run and on every later one.
    """

    def __init__(self, directory: str | Path) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "cache.sqlite3"
        if not path.exists():
            # A vector at half precision is just over half a page of the usual
            # size, so each would take a page of its own and half the file
            # would be air. With large pages they lie side by side.
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(f"PRAGMA page_size = {_PAGE_SIZE}")
                connection.execute("VACUUM")
        super().__init__(directory)

    def get_many(self, namespace: str, keys: list[str]) -> dict[str, bytes]:
        found = super().get_many(namespace, keys)
        if not namespace.startswith(_EMBEDDING_NAMESPACE):
            return found
        return {
            key: np.frombuffer(value, dtype=np.float16).astype(np.float32).tobytes()
            for key, value in found.items()
        }

    def get(self, namespace: str, key: str) -> bytes | None:
        return self.get_many(namespace, [key]).get(key)

    def put_many(self, namespace: str, values: dict[str, bytes]) -> None:
        if namespace.startswith(_EMBEDDING_NAMESPACE):
            values = {
                key: np.frombuffer(value, dtype=np.float32).astype(np.float16).tobytes()
                for key, value in values.items()
            }
        super().put_many(namespace, values)


def _with_context(items: list[tuple[int, int, str]]) -> list[str]:
    return [text for _, _, text in items] + context_texts(items)


def target_texts(subtitle: Subtitle) -> list[str]:
    """Every text the pipeline embeds of a target: its lines of dialogue, alone and in context."""
    lines = sorted(
        build_match_view(subtitle, classify_lines(subtitle)).lines,
        key=lambda line: (line.start_ms, line.id),
    )
    return _with_context([(line.start_ms, line.end_ms, line.text) for line in lines])


def reference_texts(subtitle: Subtitle) -> list[str]:
    """Every text the pipeline embeds of a reference subtitle."""
    timeline = timeline_from_subtitle(subtitle, None, classify_lines(subtitle))
    return _with_context([(event.start_ms, event.end_ms, event.text) for event in timeline.events])


def fill_cache(
    targets: list[Path],
    references: list[Path],
    cache: Cache,
    client: object | None = None,
    say: Callable[[str], None] | None = None,
    chunk: int = 20_000,
) -> int:
    """Embed every text of the given files that the cache does not hold. Returns how many."""
    say = say or (lambda message: None)
    embedder = Embedder(client=client or LocalEmbeddings(), cache=cache)
    pending: list[str] = []
    files = [(path, target_texts) for path in targets] + [
        (path, reference_texts) for path in references
    ]

    def flush() -> None:
        if pending:
            embedder.embed(list(dict.fromkeys(pending)))
            pending.clear()

    for index, (path, texts_of) in enumerate(files, start=1):
        try:
            pending.extend(texts_of(load(path)))
        except (SubtitleError, OSError) as error:
            say(f"skipped {path.name}: {error}")
        if len(pending) >= chunk:
            flush()
            say(f"embeddings: {index}/{len(files)} files, {embedder.texts_embedded} new texts")
    flush()
    say(f"embeddings: {len(files)} files, {embedder.texts_embedded} new texts")
    return embedder.texts_embedded
