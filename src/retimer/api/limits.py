"""Rate limits, kept in memory.

The service is one process, so a count in memory is the whole truth. A restart
empties the counts, which nobody outside can cause.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from collections import deque

# Beyond this many names a limiter forgets the quietest ones, so that a flood
# of made-up addresses cannot fill the memory.
MAX_TRACKED = 10_000


class SlidingWindow:
    """At most `limit` hits per name within `window_s`. Safe to use from several threads."""

    def __init__(self, limit: int, window_s: float, clock=time.monotonic) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, name: str, now: float) -> deque[float]:
        hits = self._hits.get(name)
        if hits is None:
            return deque()
        while hits and hits[0] <= now - self.window_s:
            hits.popleft()
        if not hits:
            del self._hits[name]
        return hits

    def wait_s(self, name: str) -> float:
        """How long `name` has to wait before its next hit counts. Zero when it need not."""
        with self._lock:
            now = self._clock()
            hits = self._recent(name, now)
            if len(hits) < self.limit:
                return 0.0
            return max(0.0, hits[0] + self.window_s - now)

    def hit(self, name: str) -> float:
        """Count one hit. Returns zero when it was within the limit, else the wait in seconds.

        A hit over the limit is not counted, so waiting is always enough.
        """
        with self._lock:
            now = self._clock()
            hits = self._recent(name, now)
            if len(hits) >= self.limit:
                return max(0.0, hits[0] + self.window_s - now)
            if not hits:
                if len(self._hits) >= MAX_TRACKED:
                    self._forget(now)
                self._hits[name] = hits
            hits.append(now)
            return 0.0

    def _forget(self, now: float) -> None:
        for name in list(self._hits):
            self._recent(name, now)
        if len(self._hits) >= MAX_TRACKED:
            self._hits.clear()


class Slots:
    """Counts what each name has under way right now, such as uploads in flight."""

    def __init__(self) -> None:
        self._held: dict[str, int] = {}
        self._lock = threading.Lock()

    def held(self, name: str) -> int:
        with self._lock:
            return self._held.get(name, 0)

    def take(self, name: str, limit: int, already: int = 0) -> bool:
        """Take a slot unless `name` holds `limit` together with what it has `already`."""
        with self._lock:
            held = self._held.get(name, 0)
            if held + already >= limit:
                return False
            self._held[name] = held + 1
            return True

    def release(self, name: str) -> None:
        with self._lock:
            held = self._held.get(name, 0) - 1
            if held > 0:
                self._held[name] = held
            else:
                self._held.pop(name, None)


def address_name(host: str | None) -> str:
    """The name an address is counted under. An IPv6 address counts as its /64 network.

    One customer line holds a whole /64, so counting single addresses there
    would count nothing.
    """
    if not host:
        return "unknown"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host[:64]
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network((address, 64), strict=False).network_address) + "/64"
    return str(address)
