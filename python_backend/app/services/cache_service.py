"""
Redis caching service for storing and retrieving chat answers.

Audit F-03 (2026-08-26): ``connect()`` used to build the client and print
"[OK] Redis client initialized and connected." without ever connecting
(``redis.asyncio.Redis`` is lazy), so ``is_connected`` was always True and,
with Redis unreachable, every cache read AND write ran redis-py's default
retry policy (10 attempts, exponential jitter backoff) before failing --
measured ~12 s per op, ~25 s added to every chat turn, silently.

Now:
  * the client is built with no retries and 2 s socket timeouts;
  * connectivity is verified by an explicit PING on first use
    (``ensure_connected``) -- ``is_connected`` is only ever True after a
    successful ping;
  * a failed ping or a failed operation marks Redis unreachable and every
    cache call for the next REDIS_RETRY_AFTER_S seconds returns immediately
    without touching the network; after that ONE ping is retried;
  * the turn always proceeds -- uncached -- on any failure.
"""
import asyncio
import hashlib
import json
import time
from typing import Any, Dict, Optional

import redis.asyncio as redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from app.core.config import REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_USER

# Ceiling for one Redis operation (connect or command). The cache is
# best-effort: a slow Redis is treated as an absent one.
REDIS_OP_TIMEOUT_S = 2.0
# After a failure, how long cache calls skip Redis entirely before ONE probe
# ping is attempted again. Bounds the cost of an outage to one ping per
# interval instead of one timeout per operation.
REDIS_RETRY_AFTER_S = 30.0


class RedisClient:
    """
    Redis client for caching chat answers.
    Reduces load on vector store and LLM by caching frequently asked questions.
    """
    _instance = None

    def __new__(cls):
        """Singleton pattern to reuse Redis connection"""
        if cls._instance is None:
            cls._instance = super(RedisClient, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """Initialize Redis connection with cloud credentials"""
        self.client = None
        self.is_connected = False
        self._last_failure = 0.0  # time.monotonic() of the last failure, 0 = never
        self.connect()

    def connect(self):
        """Build the Redis client. Does NOT connect: ``redis.asyncio.Redis`` is
        lazy, so reachability is only known after ``ensure_connected`` pings.
        No retries (``Retry(NoBackoff(), 0)``): one attempt per operation, so
        an unreachable Redis costs at most one socket timeout, never the
        default ten-attempt backoff."""
        try:
            self.client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                username=REDIS_USER,
                password=REDIS_PASSWORD,
                decode_responses=True,
                socket_connect_timeout=REDIS_OP_TIMEOUT_S,
                socket_timeout=REDIS_OP_TIMEOUT_S,
                retry=Retry(NoBackoff(), 0),
            )
            self.is_connected = False
            print(
                f"[REDIS] Client configured for {REDIS_HOST}:{REDIS_PORT}; "
                f"reachability is verified by PING on first use"
            )
        except Exception as e:
            self.client = None
            self.is_connected = False
            print(f"[ERROR] Failed to build Redis client: {e}")
            print("[WARNING]  Cache will be disabled, but application will continue")

    def _mark_unreachable(self, error: Exception) -> None:
        self.is_connected = False
        self._last_failure = time.monotonic()
        print(
            f"[WARNING] Redis unreachable ({type(error).__name__}: {error}) -- "
            f"cache disabled for the next {REDIS_RETRY_AFTER_S:.0f}s; "
            f"requests proceed uncached"
        )

    async def ensure_connected(self) -> bool:
        """True when Redis answered a PING (now, or earlier and nothing has
        failed since). False -- immediately, no network -- while inside the
        REDIS_RETRY_AFTER_S back-off after a failure."""
        if self.client is None:
            return False
        if self.is_connected:
            return True
        if self._last_failure and (time.monotonic() - self._last_failure) < REDIS_RETRY_AFTER_S:
            return False
        try:
            await asyncio.wait_for(self.client.ping(), timeout=REDIS_OP_TIMEOUT_S)
        except Exception as e:
            self._mark_unreachable(e)
            return False
        self.is_connected = True
        print(f"[OK] Redis reachable (PING {REDIS_HOST}:{REDIS_PORT}) -- cache enabled")
        return True

    def _generate_cache_key(self, query: str) -> str:
        """
        Generate a consistent cache key from a query.
        Uses SHA256 hash to handle long queries and ensure consistency.

        Args:
            query: The user's question

        Returns:
            Cache key in format "chat:{hash}"
        """
        # Normalize query: lowercase and strip whitespace
        normalized_query = query.lower().strip()

        # Create hash for consistent key generation
        query_hash = hashlib.sha256(normalized_query.encode('utf-8')).hexdigest()

        return f"chat:{query_hash[:16]}"  # Use first 16 chars for brevity

    async def get_cached_answer(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a cached answer (with sources) for a query.

        Args:
            query: The user's question

        Returns:
            Dict with "answer" and "sources" if cached, None otherwise.
            Falls back gracefully for legacy plain-string entries.
        """
        if not await self.ensure_connected():
            return None

        try:
            key = self._generate_cache_key(query)
            cached_data = await asyncio.wait_for(self.client.get(key), timeout=REDIS_OP_TIMEOUT_S)

            if cached_data:
                print(f"[CACHE HIT] Cache HIT for query: '{query}'")
                try:
                    parsed = json.loads(cached_data)
                    if isinstance(parsed, dict) and "answer" in parsed:
                        return parsed
                except (json.JSONDecodeError, TypeError):
                    pass
                return {"answer": cached_data, "sources": []}
            print(f"[CACHE MISS] Cache MISS for query: '{query}'")
            return None
        except Exception as e:
            print(f"[WARNING]  Error getting from Redis cache: {e}")
            self._mark_unreachable(e)
            return None

    async def set_cached_answer(self, query: str, answer: str, sources: list = None, ttl: int = 3600):
        """
        Cache an answer and its sources for a query with TTL (Time To Live).

        Args:
            query: The user's question
            answer: The generated answer
            sources: List of source dicts ({title, url})
            ttl: Time to live in seconds (default: 3600 = 1 hour)
        """
        if not await self.ensure_connected():
            return

        try:
            key = self._generate_cache_key(query)
            payload = json.dumps({"answer": answer, "sources": sources or []})
            await asyncio.wait_for(self.client.setex(key, ttl, payload), timeout=REDIS_OP_TIMEOUT_S)
            print(f"[OK] Cached answer + {len(sources or [])} sources for query: '{query}' with TTL {ttl}s")
        except Exception as e:
            print(f"[WARNING]  Error setting to Redis cache: {e}")
            self._mark_unreachable(e)

    async def clear_cache(self):
        """Clear all cached chat answers."""
        if not await self.ensure_connected():
            return

        try:
            await self.client.flushdb()
            print("[CLEAR]  Redis cache cleared.")
        except Exception as e:
            print(f"[WARNING]  Error clearing Redis cache: {e}")
            self._mark_unreachable(e)

    async def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache stats
        """
        if not await self.ensure_connected():
            return {"status": "disconnected"}

        try:
            info = await self.client.info('memory')
            db_size = await self.client.dbsize()

            return {
                "status": "connected",
                "used_memory_human": info.get('used_memory_human'),
                "total_keys": db_size
            }
        except Exception as e:
            print(f"[WARNING]  Error getting Redis stats: {e}")
            self._mark_unreachable(e)
            return {"status": "error", "details": str(e)}

    async def close(self):
        """Close Redis connection"""
        if self.client:
            try:
                await self.client.close()
                print("[CONNECTION] Redis connection closed")
            except Exception as e:
                print(f"[WARNING]  Error closing Redis connection: {e}")


# Global Redis client instance
_redis_client = None


def get_redis_client() -> RedisClient:
    """
    Get or create the global Redis client instance.
    Singleton pattern to avoid multiple connections.

    Returns:
        RedisClient instance
    """
    global _redis_client

    if _redis_client is None:
        _redis_client = RedisClient()

    return _redis_client
