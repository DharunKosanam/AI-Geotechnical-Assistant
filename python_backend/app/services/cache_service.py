"""
Redis caching service for storing and retrieving chat answers
"""
import redis.asyncio as redis
import hashlib
import json
from typing import Optional, Dict, Any
from app.core.config import REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_USER


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
        self.connect()
    
    def connect(self):
        """Establish Redis connection"""
        try:
            self.client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                username=REDIS_USER,
                password=REDIS_PASSWORD,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            self.is_connected = True
            print("[OK] Redis client initialized and connected.")
        except Exception as e:
            self.is_connected = False
            print(f"[ERROR] Failed to connect to Redis: {e}")
            print("[WARNING]  Cache will be disabled, but application will continue")
    
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
    
    async def get_cached_answer(self, query: str) -> Optional[str]:
        """
        Retrieve a cached answer for a query.
        
        Args:
            query: The user's question
            
        Returns:
            Cached answer if exists, None otherwise
        """
        if not self.is_connected:
            return None
            
        try:
            key = self._generate_cache_key(query)
            cached_data = await self.client.get(key)
            
            if cached_data:
                print(f"[CACHE HIT] Cache HIT for query: '{query}'")
                return cached_data
            print(f"[ERROR] Cache MISS for query: '{query}'")
            return None
        except Exception as e:
            print(f"[WARNING]  Error getting from Redis cache: {e}")
            return None
    
    async def set_cached_answer(self, query: str, answer: str, ttl: int = 3600):
        """
        Cache an answer for a query with TTL (Time To Live).
        
        Args:
            query: The user's question
            answer: The generated answer
            ttl: Time to live in seconds (default: 3600 = 1 hour)
        """
        if not self.is_connected:
            return
            
        try:
            key = self._generate_cache_key(query)
            await self.client.setex(key, ttl, answer)
            print(f"[OK] Cached answer for query: '{query}' with TTL {ttl}s")
        except Exception as e:
            print(f"[WARNING]  Error setting to Redis cache: {e}")
    
    async def clear_cache(self):
        """Clear all cached chat answers."""
        if not self.is_connected:
            return
            
        try:
            await self.client.flushdb()
            print("[CLEAR]  Redis cache cleared.")
        except Exception as e:
            print(f"[WARNING]  Error clearing Redis cache: {e}")
    
    async def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache stats
        """
        if not self.is_connected:
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
