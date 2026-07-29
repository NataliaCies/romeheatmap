"""Redis-backed cache with TTL management."""
import json
from typing import Any
import redis.asyncio as aioredis
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
_redis_client = None


async def get_redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(get_settings().redis_url, encoding="utf-8", decode_responses=True)
    return _redis_client


async def cache_get(key: str) -> Any | None:
    try:
        redis = await get_redis()
        raw = await redis.get(key)
        if raw is None: logger.debug("cache_miss", key=key); return None
        logger.debug("cache_hit", key=key); return json.loads(raw)
    except Exception as exc:
        logger.warning("cache_get_error", key=key, error=str(exc)); return None


async def cache_set(key: str, value: Any, ttl: int) -> None:
    try:
        redis = await get_redis()
        await redis.setex(key, ttl, json.dumps(value, default=str))
    except Exception as exc:
        logger.warning("cache_set_error", key=key, error=str(exc))


async def cache_delete_pattern(pattern: str) -> int:
    try:
        redis = await get_redis()
        keys = await redis.keys(pattern)
        if keys: await redis.delete(*keys)
        logger.info("cache_delete_pattern", pattern=pattern, count=len(keys))
        return len(keys)
    except Exception as exc:
        logger.warning("cache_delete_pattern_error", error=str(exc)); return 0


async def close_redis() -> None:
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose(); _redis_client = None
