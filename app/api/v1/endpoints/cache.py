"""Cache management endpoints."""
from datetime import date
from fastapi import APIRouter, Query
from pydantic import BaseModel
from app.core.cache_manager import get_cache_stats, invalidate_date, warm_cache_for_date

router = APIRouter(prefix="/cache", tags=["cache"])

class CacheStatsResponse(BaseModel):
    hits: int; misses: int; sets: int; errors: int
    hit_rate_pct: float; last_warm: str | None; warmed_dates: list[str]

class WarmResponse(BaseModel):
    success: bool; date: str; message: str

class InvalidateResponse(BaseModel):
    date: str; keys_deleted: int

@router.get("/stats", response_model=CacheStatsResponse)
async def cache_stats():
    return CacheStatsResponse(**get_cache_stats().to_dict())

@router.post("/warm", response_model=WarmResponse)
async def warm_cache(target_date: date = Query(default=None)):
    target_date = target_date or date.today()
    success = await warm_cache_for_date(target_date)
    return WarmResponse(success=success, date=str(target_date),
        message=f"Cache warmed for {target_date}." if success else f"Cache warming failed for {target_date}.")

@router.delete("/invalidate", response_model=InvalidateResponse)
async def invalidate_cache(target_date: date = Query(default=None)):
    target_date = target_date or date.today()
    deleted = await invalidate_date(target_date)
    return InvalidateResponse(date=str(target_date), keys_deleted=deleted)
