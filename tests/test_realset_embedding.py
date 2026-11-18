import numpy as np
import pytest

from retimer.embed import Embedder
from retimer.io import save
from retimer.providers import ProviderError
from retimer.realset.embedding import (
    CachedOnly,
    CompactCache,
    fill_cache,
    reference_texts,
    target_texts,
)

from .sync_helpers import SENTENCES, FakeClient, make_subtitle


def test_the_compact_cache_keeps_embeddings_at_half_precision(tmp_path):
    cache = CompactCache(tmp_path)
    vector = np.linspace(-1, 1, 1024, dtype=np.float32)
    cache.put_many("embedding-v1", {"a": vector.tobytes()})
    cache.put("transcript-v1", "b", b"left as it is")
    back = np.frombuffer(cache.get("embedding-v1", "a"), dtype=np.float32)
    assert back.shape == vector.shape
    assert np.allclose(back, vector, atol=1e-3)
    # What is read is the same every time, which is what a rerun needs.
    assert cache.get("embedding-v1", "a") == cache.get_many("embedding-v1", ["a"])["a"]
    assert cache.get("transcript-v1", "b") == b"left as it is"


def test_the_compact_cache_packs_vectors_side_by_side(tmp_path):
    cache = CompactCache(tmp_path)
    vectors = np.random.default_rng(0).normal(size=(600, 1024)).astype(np.float32)
    cache.put_many(
        "embedding-v1", {f"{index:064d}": row.tobytes() for index, row in enumerate(vectors)}
    )
    # 600 vectors of 2 KB each. With the usual small pages the file would be twice that.
    assert cache.path.stat().st_size < 1.25 * 600 * 2048
    # An existing cache is opened as it is.
    again = CompactCache(tmp_path)
    assert len(again.get_many("embedding-v1", [f"{index:064d}" for index in range(600)])) == 600


def test_cached_only_refuses_to_embed():
    with pytest.raises(ProviderError, match="realset embed"):
        CachedOnly().embed(["a text"])


def test_a_filled_cache_serves_the_pipeline_without_a_provider(tmp_path):
    starts = [index * 4_000 for index in range(12)]
    target = make_subtitle([start + 1_500 for start in starts], SENTENCES[:12])
    reference = make_subtitle(starts, SENTENCES[:12])
    save(target, tmp_path / "target.srt")
    save(reference, tmp_path / "reference.srt")
    cache = CompactCache(tmp_path / "cache")
    client = FakeClient()
    wanted = set(target_texts(target)) | set(reference_texts(reference))
    new = fill_cache(
        [tmp_path / "target.srt", tmp_path / "missing.srt"],
        [tmp_path / "reference.srt"],
        cache,
        client=client,
    )
    assert new == len(wanted) == len(set(client.embedded))
    # A second pass finds everything there.
    assert fill_cache([tmp_path / "target.srt"], [tmp_path / "reference.srt"], cache, client) == 0

    embedder = Embedder(client=CachedOnly(), cache=cache)
    vectors = embedder.embed(sorted(wanted))
    assert vectors.shape[0] == len(wanted)
    assert embedder.texts_embedded == 0
