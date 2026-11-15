"""Per-address request limits, held in memory.

A sliding window: at most `limit` requests in any `window` seconds. Like the job
runner, this lives in one process; with several replicas it would move to Redis.
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from starlette.requests import Request

from app.core import config

_MAX_KEYS = 20_000


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: float) -> int | None:
        """Record one request. Returns None if allowed, else seconds until it would be."""
        now = self._clock()
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= _MAX_KEYS:  # bound memory under a flood of addresses
                    self.sweep(max_age=window)
                    if len(self._hits) >= _MAX_KEYS:
                        del self._hits[next(iter(self._hits))]
                hits = self._hits[key] = deque()
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(hits) >= limit:
                return max(1, math.ceil(hits[0] + window - now))
            hits.append(now)
            return None

    def sweep(self, max_age: float = 3600.0) -> None:
        """Forget addresses that have not been seen for `max_age` seconds."""
        cutoff = self._clock() - max_age
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]

    def __len__(self) -> int:
        return len(self._hits)


@dataclass(frozen=True)
class Rule:
    name: str
    limit: int
    window: float
    message: str


def rule_for(method: str, path: str) -> Rule | None:
    """Which limit applies to this request. Health checks are never limited."""
    s = config.settings
    if path in ("/healthz", "/readyz"):
        return None
    if method == "POST" and (path == "/api/upload" or path.startswith("/api/samples/")):
        return Rule(
            "upload",
            s.rate_uploads_per_hour,
            3600,
            f"You have uploaded {s.rate_uploads_per_hour} files in the last hour, "
            "which is the limit.",
        )
    if method == "POST" and path == "/api/analyse":
        return Rule(
            "analyse",
            s.rate_analyses_per_hour,
            3600,
            f"You have run {s.rate_analyses_per_hour} analyses in the last hour, "
            "which is the limit.",
        )
    if method == "GET" and path.startswith("/api/jobs/"):
        return Rule("poll", s.rate_polls_per_minute, 60, "Too many requests.")
    return Rule("request", s.rate_requests_per_minute, 60, "Too many requests.")


def client_address(request: Request) -> str:
    """The address to count against.

    X-Forwarded-For is only read when the app is told it sits behind proxies, and
    then only the entry added by the outermost trusted proxy: everything to the
    left of it was supplied by the client and cannot be trusted.
    """
    hops = config.settings.trusted_proxy_hops
    if hops > 0:
        forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",")]
        forwarded = [p for p in forwarded if p]
        if len(forwarded) >= hops:
            return forwarded[-hops]
    return request.client.host if request.client else "unknown"


def wait_phrase(seconds: int) -> str:
    if seconds < 90:
        return f"Try again in {seconds} seconds."
    return f"Try again in about {math.ceil(seconds / 60)} minutes."


limiter = RateLimiter()
