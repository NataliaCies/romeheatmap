"""GET /api/v1/climate/hourly — hourly air temperature for one day."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models.hourly import HourlyResponse
from app.services.hourly_service import HourlyService

router = APIRouter(prefix="/climate", tags=["climate"])

# Keep requests small: last 7 days and next 7 days only.
MAX_DAYS_BACK = 7
MAX_DAYS_AHEAD = 7


def get_hourly_service() -> HourlyService:
    return HourlyService()


@router.get("/hourly", response_model=HourlyResponse)
async def get_hourly(
    target_date: date = Query(default=None, description="YYYY-MM-DD, within ±7 days of today"),
    service: HourlyService = Depends(get_hourly_service),
) -> HourlyResponse:
    today = date.today()
    target_date = target_date or today
    if not (today - timedelta(days=MAX_DAYS_BACK) <= target_date <= today + timedelta(days=MAX_DAYS_AHEAD)):
        raise HTTPException(
            status_code=422,
            detail=f"target_date must be within {MAX_DAYS_BACK} days back and {MAX_DAYS_AHEAD} days ahead of today",
        )
    return await service.get_day(target_date)
