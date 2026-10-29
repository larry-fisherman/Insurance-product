"""Distributed counters when Redis is configured; bounded local counters for isolated tests."""

import threading
import time
from typing import cast

import redis

from app.config import Settings


class Limiter:
    def __init__(self, configuration: Settings) -> None:
        self.client = (
            redis.Redis.from_url(
                configuration.redis_url.get_secret_value(),
                socket_timeout=2,
                socket_connect_timeout=2,
            )
            if configuration.redis_url
            else None
        )
        self.local: dict[str, tuple[int, float]] = {}
        self.lock = threading.Lock()

    def allow(self, key: str, maximum: int, seconds: int) -> bool:
        if self.client:
            try:
                remote_count = self.client.eval(
                    "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n",
                    1,
                    "cgl:rate:" + key,
                    str(seconds),
                )
                return int(cast(int, remote_count)) <= maximum
            except redis.RedisError:
                return False  # Do not bypass limits when shared protection is unavailable.
        with self.lock:
            count, expires = self.local.get(key, (0, 0))
            if time.monotonic() >= expires:
                count, expires = 0, time.monotonic() + seconds
            self.local[key] = count + 1, expires
            if len(self.local) > 10000:
                self.local = {k: v for k, v in self.local.items() if v[1] > time.monotonic()}
            return count < maximum
