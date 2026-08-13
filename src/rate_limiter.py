"""
Sliding-window rate limiter matching Sayari's documented tiers:
  - standard: 200 req / 60s   (resolution, entity, projects, project-entity, ...)
  - advanced: 15 req / 10s    (search, traversal, ubo, ownership, watchlist, shortest_path)
See: https://documentation.sayari.com/api/key-concepts/rate-limits

We throttle to a safety margin below the documented ceiling (not right up against
it) so a slightly bursty caller doesn't trip a 429 in normal operation. 429s are
still handled defensively on top of this in SayariClient, since the effective
limit for a given credential could differ from what's documented.
"""
import time
import threading
from collections import deque

TIER_LIMITS = {
    "standard": {"max_calls": 180, "window_sec": 60},   # documented: 200/60s
    "advanced": {"max_calls": 12, "window_sec": 10},    # documented: 15/10s
}


class RateLimiter:
    def __init__(self):
        self._windows = {tier: deque() for tier in TIER_LIMITS}
        self._lock = threading.Lock()

    def wait(self, tier: str) -> None:
        if tier not in TIER_LIMITS:
            raise ValueError(f"Unknown rate limit tier: {tier}")
        cfg = TIER_LIMITS[tier]
        with self._lock:
            window = self._windows[tier]
            now = time.monotonic()
            while window and now - window[0] > cfg["window_sec"]:
                window.popleft()
            if len(window) >= cfg["max_calls"]:
                sleep_for = cfg["window_sec"] - (now - window[0]) + 0.05
                if sleep_for > 0:
                    time.sleep(sleep_for)
                now = time.monotonic()
                while window and now - window[0] > cfg["window_sec"]:
                    window.popleft()
            window.append(time.monotonic())
