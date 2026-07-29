"""Health and readiness probe endpoints."""
from datetime import datetime, timezone
from fastapi import APIRouter, Response
from app.models.schemas import HealthResponse
from app.core.cache import get_redis

router = APIRouter(tags=["health"])
_app_ready = False

def mark_ready() -> None:
    global _app_ready; _app_ready = True

@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Liveness probe — returns 200 if the app process is alive."""
    services: dict[str, str] = {}
    try:
        redis = await get_redis(); await redis.ping(); services["redis"] = "ok"
    except Exception:
        services["redis"] = "error"
    status = "ok" if all(v == "ok" for v in services.values()) else "degraded"
    return HealthResponse(status=status, version="1.0.0",
        timestamp=datetime.now(timezone.utc), services=services)

@router.get("/ready")
async def readiness_probe(response: Response) -> dict:
    """Readiness probe — 503 during startup warming, 200 when ready."""
    try:
        redis = await get_redis(); await redis.ping(); redis_ok = True
    except Exception:
        redis_ok = False
    ready = _app_ready and redis_ok
    if not ready:
        response.status_code = 503
        return {"ready": False, "redis": redis_ok, "startup_complete": _app_ready}
    return {"ready": True, "redis": True, "startup_complete": True}
