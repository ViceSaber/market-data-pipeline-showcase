"""Global rate limiter for Polymarket Gamma API.

All jobs share a single RateLimiter instance to prevent
combined request bursts from exceeding API limits.

Conservative limit: 200 req / 10s (leaves headroom for Cloudflare throttle).
"""

import time
import threading
from dataclasses import dataclass, field

from config.settings import RATE_LIMIT_MAX_REQUESTS, RATE_LIMIT_WINDOW_SECONDS


@dataclass
class RateLimiter:
    max_per_window: int = RATE_LIMIT_MAX_REQUESTS
    window_seconds: float = RATE_LIMIT_WINDOW_SECONDS
    _timestamps: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def acquire(self):
        """Block until a request slot is available."""
        while True:
            with self._lock:
                now = time.monotonic()
                self._timestamps = [
                    t for t in self._timestamps
                    if now - t < self.window_seconds
                ]
                if len(self._timestamps) < self.max_per_window:
                    self._timestamps.append(now)
                    return
            time.sleep(0.1)


# Global singleton
_limiter = RateLimiter()


def get_limiter() -> RateLimiter:
    return _limiter
