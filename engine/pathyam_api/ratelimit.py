"""Per-IP rate limiting.

WHAT THIS IS NOT
----------------
It is not a substitute for limiting at the edge, and should not be treated as the
place this problem is solved. Three reasons, all structural:

* State is per process. Two uvicorn workers give an attacker two buckets, and a
  horizontally scaled deployment gives them one per instance. A shared store
  (Redis) or the load balancer is where a real limit lives.
* It cannot shed load. Every limited request has still been accepted, parsed and
  routed before it is refused, so this does nothing for volumetric abuse.
* It sees the client only as well as the deployment lets it -- see below.

What it does do is make online password guessing expensive from a single source,
which the per-account lockout alone does not: an attacker spraying one password
across many accounts never trips a per-account counter.

CLIENT IDENTIFICATION, AND WHY X-FORWARDED-FOR IS OFF BY DEFAULT
-----------------------------------------------------------------
Trusting ``X-Forwarded-For`` blindly does not weaken this limiter, it removes it: a
client sets the header to a fresh random value per request and every request looks
like a new IP. That is worse than no limiter, because the dashboard says you have
one.

So the header is ignored unless ``PATHYAM_TRUSTED_PROXY_HOPS`` says how many proxies
sit in front of the app. With N hops, the client address is the Nth entry from the
RIGHT of the chain -- the leftmost entries are attacker-supplied and the rightmost N
were appended by infrastructure you control. Getting this backwards (taking the
first entry) is the common form of the bug.

With no proxy configured, the socket peer address is used, which cannot be forged
over a completed TCP handshake.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass

__all__ = ["RateLimiter", "RateLimitExceeded", "client_address", "AUTH_LIMIT"]

_TRUSTED_HOPS_ENV = "PATHYAM_TRUSTED_PROXY_HOPS"

# Entries idle for longer than this are dropped. Without eviction the limiter is
# itself a memory-exhaustion vector: one request per forged address grows the table
# forever.
_EVICT_AFTER_SECONDS = 3600
_EVICT_EVERY_SECONDS = 300


class RateLimitExceeded(Exception):
    """Raised when a caller is over its limit. Carries seconds until retry."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__(f"rate limit exceeded; retry in {retry_after}s")


@dataclass(frozen=True)
class Limit:
    requests: int
    per_seconds: int
    name: str


# Login and registration. Deliberately tight: a human signing in does so once, and
# 10 attempts a minute from one address is already generous for anything legitimate.
AUTH_LIMIT = Limit(requests=10, per_seconds=60, name="auth")


def client_address(
    *,
    peer: str | None,
    forwarded_for: str | None = None,
    trusted_hops: int | None = None,
) -> str:
    """Best available client address.

    ``forwarded_for`` is consulted only when ``trusted_hops`` is positive. See the
    module docstring: honouring it unconditionally lets a caller forge a new
    identity per request and defeats the limiter entirely.
    """
    if trusted_hops is None:
        try:
            trusted_hops = int(os.environ.get(_TRUSTED_HOPS_ENV, "0"))
        except ValueError:
            trusted_hops = 0

    if trusted_hops > 0 and forwarded_for:
        chain = [part.strip() for part in forwarded_for.split(",") if part.strip()]
        # Count from the RIGHT: the rightmost `trusted_hops` entries were appended by
        # infrastructure we control, so the client is the one just left of them.
        index = len(chain) - trusted_hops
        if 0 <= index < len(chain):
            return chain[index]
        if chain:
            # Chain shorter than expected — the request did not traverse the proxies
            # we think it did. Fall back to the leftmost, which is at worst
            # attacker-controlled, rather than silently indexing off the end.
            return chain[0]

    return peer or "unknown"


class RateLimiter:
    """Sliding-window counter, keyed by client address.

    A sliding window rather than a fixed one: a fixed window lets a caller send a
    full allowance at the end of one window and again at the start of the next,
    which is twice the intended rate at the boundary.
    """

    def __init__(self, limit: Limit) -> None:
        self._limit = limit
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_evicted = time.monotonic()

    def check(self, key: str) -> None:
        """Record a request. Raises :class:`RateLimitExceeded` if over the limit."""
        now = time.monotonic()
        window_start = now - self._limit.per_seconds

        with self._lock:
            self._maybe_evict(now)

            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] < window_start:
                hits.popleft()

            if len(hits) >= self._limit.requests:
                retry_after = max(1, int(hits[0] + self._limit.per_seconds - now) + 1)
                # The rejected attempt is NOT recorded. Otherwise a caller hammering
                # the endpoint keeps pushing its own window forward and can never
                # recover, which turns a rate limit into a permanent ban.
                raise RateLimitExceeded(retry_after)

            hits.append(now)

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)

    def _maybe_evict(self, now: float) -> None:
        if now - self._last_evicted < _EVICT_EVERY_SECONDS:
            return
        cutoff = now - _EVICT_AFTER_SECONDS
        self._hits = {
            key: hits for key, hits in self._hits.items()
            if hits and hits[-1] >= cutoff
        }
        self._last_evicted = now
