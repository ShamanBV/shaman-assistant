"""
Cache Service
=============
Simple in-memory cache with TTL support.
Can be swapped for Redis later if needed.
"""
import hashlib
import time
from typing import Optional, Any
import config


class Cache:
    """
    Simple in-memory cache with TTL.
    
    For production, swap this for Redis:
        pip install redis
        self.client = redis.Redis(...)
    """
    
    def __init__(self, ttl: int = None):
        self._cache: dict[str, dict] = {}
        self.ttl = ttl or config.CACHE_TTL_SECONDS
        self._hits = 0
        self._misses = 0
    
    def _normalize_key(self, question: str) -> str:
        """
        Normalize question to cache key.
        Makes cache somewhat fuzzy (case-insensitive, trimmed).
        """
        normalized = question.lower().strip()
        # Remove common filler words for better cache hits
        for word in ["please", "can you", "could you", "help me", "i need to"]:
            normalized = normalized.replace(word, "")
        normalized = " ".join(normalized.split())  # Normalize whitespace
        return hashlib.md5(normalized.encode()).hexdigest()
    
    def get(self, question: str) -> Optional[Any]:
        """
        Get cached value for question.
        
        Args:
            question: User question
            
        Returns:
            Cached value or None if not found/expired
        """
        key = self._normalize_key(question)
        
        if key in self._cache:
            entry = self._cache[key]
            if time.time() - entry["timestamp"] < self.ttl:
                self._hits += 1
                return entry["value"]
            else:
                # Expired, clean up
                del self._cache[key]
        
        self._misses += 1
        return None
    
    def set(self, question: str, value: Any) -> None:
        """
        Cache a value for a question.
        
        Args:
            question: User question (key)
            value: Value to cache
        """
        key = self._normalize_key(question)
        self._cache[key] = {
            "value": value,
            "timestamp": time.time(),
            "question": question  # Store original for debugging
        }
    
    def invalidate(self, question: str) -> bool:
        """
        Remove a specific question from cache.
        
        Returns:
            True if key was found and removed
        """
        key = self._normalize_key(question)
        if key in self._cache:
            del self._cache[key]
            return True
        return False
    
    def clear(self) -> int:
        """
        Clear all cached entries.
        
        Returns:
            Number of entries cleared
        """
        count = len(self._cache)
        self._cache = {}
        self._hits = 0
        self._misses = 0
        return count
    
    def cleanup_expired(self) -> int:
        """
        Remove expired entries.
        
        Returns:
            Number of entries removed
        """
        now = time.time()
        expired_keys = [
            key for key, entry in self._cache.items()
            if now - entry["timestamp"] >= self.ttl
        ]
        for key in expired_keys:
            del self._cache[key]
        return len(expired_keys)
    
    def stats(self) -> dict:
        """Get cache statistics."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        
        return {
            "entries": len(self._cache),
            "ttl_seconds": self.ttl,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{hit_rate:.1f}%"
        }
