import numpy as np

from retimer.cache import Cache, NoCache, content_hash
from retimer.embed import Embedder

from .sync_helpers import FakeClient


def test_content_hash_keeps_parts_apart():
    assert content_hash("ab", "c") != content_hash("a", "bc")
    assert content_hash("ab", "c") == content_hash(b"ab", "c")


def test_cache_returns_what_was_put(tmp_path):
    cache = Cache(tmp_path)
    cache.put("names", "one", b"1")
    cache.put_many("names", {"two": b"2", "three": b"3"})

    assert cache.get("names", "one") == b"1"
    assert cache.get("other", "one") is None
    assert cache.get_many("names", ["one", "three", "nine"]) == {"one": b"1", "three": b"3"}
    assert Cache(tmp_path).get("names", "two") == b"2"


def test_no_cache_forgets_everything():
    cache = NoCache()
    cache.put_many("names", {"one": b"1"})
    assert cache.get("names", "one") is None
    assert cache.get_many("names", ["one"]) == {}


def test_embedder_gives_unit_vectors_and_asks_once_per_text(tmp_path):
    client = FakeClient()
    embedder = Embedder(client=client, cache=Cache(tmp_path))

    first = embedder.embed(["red door", "blue door", "red door"])

    assert first.shape[0] == 3
    assert np.allclose(np.linalg.norm(first, axis=1), 1.0, atol=1e-5)
    assert np.allclose(first[0], first[2])
    assert sorted(client.embedded) == ["blue door", "red door"]
    assert embedder.texts_embedded == 2

    again = Embedder(client=client, cache=Cache(tmp_path))
    second = again.embed(["blue door", "green door"])
    assert np.allclose(second[0], first[1])
    assert client.embedded.count("blue door") == 1
    assert again.texts_from_cache == 1
    assert again.embed([]).shape == (0, 0)


def test_embedder_sends_large_requests_in_batches(tmp_path):
    client = FakeClient()
    texts = [f"line {index}" for index in range(250)]
    vectors = Embedder(client=client, cache=Cache(tmp_path)).embed(texts)
    assert vectors.shape[0] == 250
    assert len(client.embedded) == 250
