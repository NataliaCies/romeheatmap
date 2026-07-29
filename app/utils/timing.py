"""Timing decorator for async functions."""
import time, functools
from app.core.logging import get_logger

logger = get_logger(__name__)


def log_execution_time(func):
    """Decorator that logs execution time of async functions."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            result = await func(*args, **kwargs)
            logger.info("function_executed", function=func.__qualname__,
                        elapsed_ms=round((time.perf_counter() - start) * 1000, 2))
            return result
        except Exception as exc:
            logger.error("function_failed", function=func.__qualname__, error=str(exc)); raise
    return wrapper
