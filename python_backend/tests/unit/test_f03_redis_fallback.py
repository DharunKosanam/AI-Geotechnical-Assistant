"""Audit F-03 (2026-08-26): Redis must not be a hard dependency.

  * cache_service: is_connected is only True after a real PING; an
    unreachable Redis costs ONE fast probe, then every cache call returns
    immediately (no network) until the back-off expires; a failure mid-flight
    marks it unreachable again; the caller always proceeds uncached.
  * rate_limit: the slowapi limiter has the in-memory fallback enabled so a
    Redis outage cannot 500 login/chat/upload (behaviour with Redis UP is
    unchanged: the Redis-backed limiter is used while _storage_dead is False).
"""
import time

import pytest

from app.core import rate_limit
from app.services import cache_service

pytestmark = pytest.mark.unit


class _FakeRedis:
    """Stands in for redis.asyncio.Redis. ``fail`` simulates unreachable."""

    def __init__(self, *a, fail=False, **k):
        self.fail = fail
        self.kwargs = k
        self.pings = 0
        self.ops = 0
        self.store = {}

    async def ping(self):
        self.pings += 1
        if self.fail:
            raise ConnectionError("Error 111 connecting to redis:6379. Connection refused.")
        return True

    async def get(self, key):
        self.ops += 1
        if self.fail:
            raise ConnectionError("refused")
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.ops += 1
        if self.fail:
            raise ConnectionError("refused")
        self.store[key] = value

    async def close(self):
        pass


@pytest.fixture()
def fresh(monkeypatch):
    """A new RedisClient whose redis.Redis is the fake; ``fail`` set per test."""
    made = {}

    def factory(fail):
        def _redis(*a, **k):
            made["client"] = _FakeRedis(*a, fail=fail, **k)
            return made["client"]
        monkeypatch.setattr(cache_service.redis, "Redis", _redis)
        monkeypatch.setattr(cache_service.RedisClient, "_instance", None)
        monkeypatch.setattr(cache_service, "_redis_client", None)
        rc = cache_service.get_redis_client()
        return rc, made["client"]
    return factory


async def test_not_connected_until_ping_succeeds(fresh):
    rc, fake = fresh(fail=False)
    assert rc.is_connected is False          # constructor no longer claims success
    assert fake.pings == 0
    assert await rc.ensure_connected() is True
    assert rc.is_connected is True and fake.pings == 1


async def test_client_is_built_without_retries_and_with_short_timeouts(fresh):
    rc, fake = fresh(fail=False)
    assert fake.kwargs["retry"]._retries == 0
    assert fake.kwargs["socket_connect_timeout"] == cache_service.REDIS_OP_TIMEOUT_S
    assert fake.kwargs["socket_timeout"] == cache_service.REDIS_OP_TIMEOUT_S


async def test_unreachable_costs_one_probe_then_fails_fast(fresh):
    rc, fake = fresh(fail=True)
    t = time.monotonic()
    assert await rc.get_cached_answer("uA:q") is None
    await rc.set_cached_answer("uA:q", "answer")
    assert await rc.get_cached_answer("uA:q") is None
    elapsed = time.monotonic() - t
    assert fake.pings == 1                   # ONE probe for the whole burst
    assert fake.ops == 0                     # no get/set ever hit the network
    assert rc.is_connected is False
    assert elapsed < 0.5


async def test_probe_is_retried_after_backoff(fresh):
    rc, fake = fresh(fail=True)
    assert await rc.get_cached_answer("q") is None
    assert fake.pings == 1
    rc._last_failure -= cache_service.REDIS_RETRY_AFTER_S + 1   # back-off expired
    fake.fail = False                                            # Redis came back
    assert await rc.get_cached_answer("q") is None               # miss, but via Redis
    assert fake.pings == 2 and fake.ops == 1 and rc.is_connected is True


async def test_reachable_roundtrip_and_midflight_failure(fresh):
    rc, fake = fresh(fail=False)
    await rc.set_cached_answer("uA:q", "the answer", sources=[{"title": "t"}])
    assert await rc.get_cached_answer("uA:q") == {"answer": "the answer",
                                                 "sources": [{"title": "t"}]}
    assert fake.pings == 1                   # ping once, not per op
    fake.fail = True                         # Redis dies mid-flight
    assert await rc.get_cached_answer("uA:q") is None
    assert rc.is_connected is False and rc._last_failure > 0
    ops_before = fake.ops
    assert await rc.get_cached_answer("uA:q") is None
    assert fake.ops == ops_before            # back-off: no network call


def test_limiter_has_in_memory_fallback_and_redis_path_is_default():
    lim = rate_limit.limiter
    assert lim._in_memory_fallback_enabled is True
    assert lim._fallback_limiter is not None
    # Redis UP: the Redis-backed limiter is the one in use (unchanged path).
    assert lim._storage_dead is False
    assert lim.limiter is lim._limiter
