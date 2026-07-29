"""Background task scheduler — cache warming and midnight refresh."""
from __future__ import annotations
import asyncio
from datetime import date, datetime, timedelta
from app.core.cache_manager import invalidate_date, warm_upcoming_days
from app.core.logging import get_logger

logger = get_logger(__name__)
_tasks: list[asyncio.Task] = []


async def run_startup_warming() -> None:
    logger.info("startup_warming_begin")
    try:
        await warm_upcoming_days(days_ahead=2)
    except Exception as exc:
        logger.error("startup_warming_failed", error=str(exc))
    finally:
        from app.api.v1.endpoints.health import mark_ready
        mark_ready()
        logger.info("app_marked_ready")


async def run_midnight_refresh() -> None:
    while True:
        now = datetime.now()
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        await asyncio.sleep((tomorrow - now).total_seconds())
        logger.info("midnight_refresh_start")
        try:
            await invalidate_date(date.today() - timedelta(days=1))
            await warm_upcoming_days(days_ahead=1)
        except Exception as exc:
            logger.error("midnight_refresh_error", error=str(exc))


def start_background_tasks() -> None:
    loop = asyncio.get_event_loop()
    async def delayed_warm():
        await asyncio.sleep(5); await run_startup_warming()
    _tasks.extend([
        loop.create_task(delayed_warm(), name="startup_warming"),
        loop.create_task(run_midnight_refresh(), name="midnight_refresh"),
    ])
    logger.info("background_tasks_started", tasks=[t.get_name() for t in _tasks])


def stop_background_tasks() -> None:
    for task in _tasks:
        if not task.done(): task.cancel()
    _tasks.clear()
