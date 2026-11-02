"""Hourly timeline service: cache + response assembly."""
from __future__ import annotations

from datetime import date

from app.core.cache import cache_get, cache_set
from app.core.config import get_settings
from app.models.hourly import HourlyResponse
from app.repositories.hourly_weather import HourlyWeatherRepository

SOURCE = "Open-Meteo forecast (temperature_2m)"
SCOPE = "Rome city centre point, not district-specific"


class HourlyService:
    def __init__(self, repo: HourlyWeatherRepository | None = None) -> None:
        self._repo = repo or HourlyWeatherRepository()
        self._settings = get_settings()

    async def get_day(self, target_date: date) -> HourlyResponse:
        key = f"hourly:{target_date}"
        cached = await cache_get(key)
        if cached:
            return HourlyResponse(**cached)

        points = await self._repo.get_day(target_date)
        response = HourlyResponse(
            date=target_date,
            timezone=HourlyWeatherRepository.TIMEZONE,
            source=SOURCE,
            scope=SCOPE,
            points=points,
        )
        if points:  # never cache an empty answer
            await cache_set(key, response.model_dump(mode="json"), self._settings.cache_ttl_weather)
        return response
