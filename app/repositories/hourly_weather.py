"""Hourly air temperature from Open-Meteo for one day.

Separate from weather.py on purpose: the daily pipeline stays untouched.
One small request per date (temperature_2m only), cached by the service.
"""
from __future__ import annotations

from datetime import date

import httpx

from app.core.config import get_settings
from app.core.exceptions import WeatherDataError
from app.core.logging import get_logger
from app.models.hourly import HourlyPoint

logger = get_logger(__name__)


def parse_hourly(data: dict, target_date: date) -> list[HourlyPoint]:
    """Turn an Open-Meteo hourly payload into HourlyPoint objects.

    Keeps only rows of target_date and skips null temperatures.
    """
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    prefix = target_date.isoformat()
    points: list[HourlyPoint] = []
    for t, temp in zip(times, temps):
        if temp is None or not str(t).startswith(prefix):
            continue
        try:
            hour = int(str(t)[11:13])
        except ValueError:
            continue
        points.append(HourlyPoint(hour=hour, time=str(t), temperature_celsius=round(float(temp), 1)))
    return points


class HourlyWeatherRepository:
    ROME_LAT = 41.89
    ROME_LON = 12.48
    TIMEZONE = "Europe/Rome"

    def __init__(self) -> None:
        self._settings = get_settings()

    async def get_day(self, target_date: date) -> list[HourlyPoint]:
        params = {
            "latitude": self.ROME_LAT,
            "longitude": self.ROME_LON,
            "hourly": "temperature_2m",
            "timezone": self.TIMEZONE,
            "start_date": target_date.isoformat(),
            "end_date": target_date.isoformat(),
        }
        url = f"{self._settings.open_meteo_base_url}/forecast"
        logger.info("hourly_fetch_start", date=str(target_date))
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise WeatherDataError(
                    f"Open-Meteo hourly returned {exc.response.status_code}: {exc.response.text[:150]}"
                ) from exc
            except httpx.RequestError as exc:
                raise WeatherDataError(f"Cannot reach Open-Meteo: {exc}") from exc
        points = parse_hourly(response.json(), target_date)
        logger.info("hourly_fetch_done", date=str(target_date), points=len(points))
        return points
