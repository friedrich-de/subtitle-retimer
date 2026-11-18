"""Jimaku, a site where Japanese subtitle files for anime are shared.

The kit takes its targets from there: files people really sync. Only three
calls are needed: find the entry of a series, list its files, download one.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import httpx

API_URL = "https://jimaku.cc/api"
KEY_ENV = "JIMAKU_API_KEY"
# A generic name. The site asks for none in particular.
USER_AGENT = "retimer-realset/0.1"

_LONGEST_WAIT_S = 120.0


class JimakuError(RuntimeError):
    """A call to Jimaku failed for good. The message never contains the key."""


class Jimaku:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = API_URL,
        client: httpx.Client | None = None,
        retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=60.0, follow_redirects=True)
        self._retries = retries
        self._sleep = sleep

    def _request(self, url: str, *, signed: bool, **kwargs) -> httpx.Response:
        headers = {"User-Agent": USER_AGENT}
        if signed:
            headers["Authorization"] = self._key
        problem = "no attempt was made"
        for attempt in range(self._retries + 1):
            try:
                response = self._client.get(url, headers=headers, **kwargs)
            except httpx.HTTPError as error:
                problem = type(error).__name__
                self._sleep(min(2.0**attempt, 30.0))
                continue
            if response.status_code == 200:
                return response
            problem = f"HTTP {response.status_code}"
            if response.status_code == 429:
                self._sleep(_wait_s(response.headers))
            elif response.status_code >= 500:
                self._sleep(min(2.0**attempt, 30.0))
            else:
                break
        raise JimakuError(f"{url.split('?')[0]} failed: {problem}")

    def entries(self, *, anilist_id: int | None = None, query: str | None = None) -> list[dict]:
        """The entries for an AniList id, or those whose name matches `query`."""
        params: dict[str, str | int] = {"anime": "true"}
        if anilist_id is not None:
            params["anilist_id"] = anilist_id
        if query:
            params["query"] = query
        url = f"{self._base_url}/entries/search"
        return self._request(url, signed=True, params=params).json()

    def files(self, entry_id: int) -> list[dict]:
        """The files of an entry: `name`, `url`, `size` and `last_modified` each."""
        return self._request(f"{self._base_url}/entries/{entry_id}/files", signed=True).json()

    def download(self, url: str, destination: Path) -> int:
        """Fetch one file. Returns its size in bytes."""
        response = self._request(url, signed=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return len(response.content)


def _wait_s(headers: httpx.Headers) -> float:
    """How long the site asks to wait, from whichever header says so."""
    for name in ("x-ratelimit-reset-after", "retry-after"):
        try:
            return min(max(float(headers[name]), 0.5), _LONGEST_WAIT_S)
        except (KeyError, ValueError):
            continue
    try:
        return min(max(float(headers["x-ratelimit-reset"]) - time.time(), 0.5), _LONGEST_WAIT_S)
    except (KeyError, ValueError):
        return 5.0
