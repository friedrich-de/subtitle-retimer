"""API keys: how they are made, kept and checked.

A key is 256 random bits. The server keeps only its SHA-256 hash, so neither
its configuration nor its logs can give a key away. A fast hash is enough here,
because there is nothing to guess: a slow one protects passwords people choose.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from pydantic import BaseModel

KEY_PREFIX = "rt_"
# Longer than any key this module makes. A longer one is not even hashed.
MAX_KEY_CHARS = 128
_LABEL = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


class ApiKey(BaseModel):
    """One key the server accepts: a name for the logs, and the hash of the key."""

    label: str
    sha256: str


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_key(label: str) -> tuple[str, ApiKey]:
    """A fresh key for the client, and the entry for the server's configuration."""
    if not _LABEL.match(label):
        raise ValueError(
            "a label is 1 to 32 characters: lower-case letters, digits, hyphen, underscore"
        )
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    return key, ApiKey(label=label, sha256=hash_key(key))


def format_entry(entry: ApiKey) -> str:
    return f"{entry.label}:{entry.sha256}"


def parse_keys(text: str) -> list[ApiKey]:
    """Read `label:hash` entries, separated by commas or whitespace.

    Anything else is refused, with no part of it in the message: a key pasted
    here by mistake must not end up in a log.
    """
    keys: list[ApiKey] = []
    for position, entry in enumerate(re.split(r"[,\s]+", text.strip()), start=1):
        if not entry:
            continue
        label, _, digest = entry.partition(":")
        if not _LABEL.match(label) or not _HASH.match(digest):
            raise ValueError(
                f"entry {position} is not of the form label:sha256-hash. "
                "The server keeps hashes, never the keys themselves. Make an entry with: "
                "retimer api-key"
            )
        if any(known.label == label for known in keys):
            raise ValueError(f"entry {position} repeats the label of an earlier one")
        keys.append(ApiKey(label=label, sha256=digest))
    return keys


def bearer_key(headers: dict[str, str]) -> str | None:
    """The key a request presents, from `Authorization: Bearer` or `X-API-Key`.

    A key in the address of a request is not accepted: addresses end up in logs.
    """
    authorization = headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return headers.get("x-api-key") or None


def verify(presented: str | None, keys: list[ApiKey]) -> ApiKey | None:
    """The entry the presented key belongs to, or None.

    Every entry is compared, and each comparison takes the same time whatever
    the outcome, so the time a refusal takes says nothing about the keys.
    """
    if not presented or len(presented) > MAX_KEY_CHARS:
        return None
    digest = hash_key(presented)
    found: ApiKey | None = None
    for entry in keys:
        if hmac.compare_digest(digest, entry.sha256):
            found = entry
    return found
