"""A cache by content hash for transcripts and embeddings.

A second subtitle for the same audio costs no second transcription, and
benchmark reruns stay cheap. Everything lives in one SQLite file.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import closing
from pathlib import Path

CACHE_DIR_ENV = "RETIMER_CACHE_DIR"
DEFAULT_CACHE_DIR = Path("data/cache")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value BLOB NOT NULL,
    PRIMARY KEY (namespace, key)
)
"""


def content_hash(*parts: bytes | str) -> str:
    """One hash over several parts. The parts are length-prefixed, so they cannot blur."""
    digest = hashlib.sha256()
    for part in parts:
        data = part.encode("utf-8") if isinstance(part, str) else part
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


class Cache:
    """Byte values by namespace and key. Safe to use from several threads."""

    def __init__(self, directory: str | Path | None = None) -> None:
        directory = Path(directory or os.environ.get(CACHE_DIR_ENV) or DEFAULT_CACHE_DIR)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "cache.sqlite3"
        with closing(self._connect()) as connection, connection:
            connection.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def get(self, namespace: str, key: str) -> bytes | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value FROM entries WHERE namespace = ? AND key = ?", (namespace, key)
            ).fetchone()
        return None if row is None else bytes(row[0])

    def get_many(self, namespace: str, keys: list[str]) -> dict[str, bytes]:
        found: dict[str, bytes] = {}
        with closing(self._connect()) as connection:
            for start in range(0, len(keys), 500):
                batch = keys[start : start + 500]
                marks = ",".join("?" * len(batch))
                rows = connection.execute(
                    f"SELECT key, value FROM entries WHERE namespace = ? AND key IN ({marks})",
                    (namespace, *batch),
                )
                found.update({key: bytes(value) for key, value in rows})
        return found

    def put(self, namespace: str, key: str, value: bytes) -> None:
        self.put_many(namespace, {key: value})

    def put_many(self, namespace: str, values: dict[str, bytes]) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executemany(
                "INSERT OR REPLACE INTO entries (namespace, key, value) VALUES (?, ?, ?)",
                [(namespace, key, value) for key, value in values.items()],
            )


class NoCache(Cache):
    """Remembers nothing. For tests and for runs that must hit the provider."""

    def __init__(self) -> None:
        pass

    def get(self, namespace: str, key: str) -> bytes | None:
        return None

    def get_many(self, namespace: str, keys: list[str]) -> dict[str, bytes]:
        return {}

    def put_many(self, namespace: str, values: dict[str, bytes]) -> None:
        return None
