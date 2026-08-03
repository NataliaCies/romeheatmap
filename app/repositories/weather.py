"""Repository for Open-Meteo weather data (no API key required).

Open-Meteo has two separate endpoints:

  /forecast — covers last ~92 days + 16 days ahead (including today)
  /archive  — covers historical data, BUT with ~5-7 day delay
              (today's date is NOT yet available in /archive)

Fix (2026-07-v2): The previous fix used a 5-day cutoff which was too
aggressive — it sent dates like "today - 5 days" to /archive, but the
archive doesn't have those yet, causing HTTP 404.

Correct rule:
  - dates <= today - 7  →  /archive  (safe margin, definitely available)
  - dates >  today - 7  →  /forecast (covers last 92 days, includes today)

When a range spans both (e.g. 12-month timeseries), we make two requests
and merge results.
"""

from __future__ import annotations

from datetime import date, timedelta
from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.core.exceptions import WeatherDataError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Use /archive only for dates older than this many days.
# 7 is a safe margin — Open-Meteo archive typically lags by ~5 days,
# so 7 ensures we never request a date that isn't there yet.
_ARCHIVE_SAFE_DAYS = 7

_DAILY_VARS = ",".join([
    "temperature_2m_max",
    "temperature_2m_min",
    "cloud_cover_mean",
    "precipitation_sum",
    "sunrise",
    "sunset",
    "uv_index_max",
    "wind_speed_10m_max",
])

# /forecast uses "relativehumidity_2m" (no underscore before 2m)
# /archive uses "relative_humidity_2m" (underscore before 2m)
_HOURLY_FORECAST = "relativehumidity_2m,surface_pressure"
_HOURLY_ARCHIVE = "relative_humidity_2m,surface_pressure"


@dataclass
class DailyWeather:
    date: date
    tmax_celsius: float
    tmin_celsius: float
    cloud_pct: float
    humidity_pct: float
    pressure_hpa: float
    wind_kmh: float
    uv_index: float
    sunrise: str
    sunset: str
    is_forecast: bool


class WeatherRepository:
    ROME_LAT = 41.89
    ROME_LON = 12.48
    TIMEZONE = "Europe/Rome"

    def __init__(self) -> None:
        self._settings = get_settings()

    async def get_range(self, start: date, end: date) -> dict[date, DailyWeather]:
        """Fetch daily weather for a date range.

        Routes automatically:
          - old dates (> 7 days ago)  -> /archive
          - recent + future dates     -> /forecast
          - range spanning both       -> two requests, merged result

        Raises:
            WeatherDataError: On HTTP error or network failure.
        """
        today = date.today()
        # Dates from this day onward always go to /forecast
        forecast_boundary = today - timedelta(days=_ARCHIVE_SAFE_DAYS)

        logger.info("weather_fetch_start", start=str(start), end=str(end),
                    forecast_boundary=str(forecast_boundary))

        result: dict[date, DailyWeather] = {}

        # Old dates -> /archive
        if start < forecast_boundary:
            archive_end = min(end, forecast_boundary - timedelta(days=1))
            data = await self._fetch("archive", start, archive_end, _HOURLY_ARCHIVE)
            result.update(self._parse(data))

        # Recent / future dates -> /forecast
        if end >= forecast_boundary:
            fc_start = max(start, forecast_boundary)
            data = await self._fetch("forecast", fc_start, end, _HOURLY_FORECAST)
            result.update(self._parse(data))

        logger.info("weather_fetch_done", days=len(result))
        return result

    async def _fetch(self, endpoint: str, start: date, end: date,
                     hourly_vars: str) -> dict:
        """Single HTTP request to one Open-Meteo endpoint."""
        url = f"{self._settings.open_meteo_base_url}/{endpoint}"
        params = {
            "latitude": self.ROME_LAT, "longitude": self.ROME_LON,
            "daily": _DAILY_VARS, "hourly": hourly_vars,
            "timezone": self.TIMEZONE,
            "start_date": str(start), "end_date": str(end),
        }
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise WeatherDataError(
                    f"Open-Meteo /{endpoint} returned {exc.response.status_code} "
                    f"for {start}->{end}: {exc.response.text[:300]}"
                ) from exc
            except httpx.RequestError as exc:
                raise WeatherDataError(
                    f"Cannot reach Open-Meteo /{endpoint}: {exc}"
                ) from exc
        return response.json()

    def _parse(self, data: dict) -> dict[date, DailyWeather]:
        """Parse Open-Meteo JSON into DailyWeather records."""
        daily = data.get("daily", {})
        hourly = data.get("hourly", {})
        today = date.today()
        result: dict[date, DailyWeather] = {}

        # Humidity key name differs between /forecast and /archive
        hum_values = (
            hourly.get("relativehumidity_2m")
            or hourly.get("relative_humidity_2m")
            or []
        )
        pres_values = hourly.get("surface_pressure", [])
        hourly_times = hourly.get("time", [])

        # Noon-hour lookup for humidity and pressure
        noon_lookup: dict[str, tuple[float, float]] = {}
        for i, t in enumerate(hourly_times):
            if "T12:00" in t:
                hum = float(hum_values[i]) if i < len(hum_values) else 55.0
                pres = float(pres_values[i]) if i < len(pres_values) else 1013.0
                noon_lookup[t[:10]] = (hum, pres)

        sr_list = daily.get("sunrise", [])
        ss_list = daily.get("sunset", [])

        for i, d_str in enumerate(daily.get("time", [])):
            d = date.fromisoformat(d_str)
            hum, pres = noon_lookup.get(d_str, (55.0, 1013.0))
            sr = sr_list[i] if i < len(sr_list) else ""
            ss = ss_list[i] if i < len(ss_list) else ""

            result[d] = DailyWeather(
                date=d,
                tmax_celsius=self._safe(daily, "temperature_2m_max", i, 32.0),
                tmin_celsius=self._safe(daily, "temperature_2m_min", i, 20.0),
                cloud_pct=self._safe(daily, "cloud_cover_mean", i, 25.0),
                humidity_pct=hum,
                pressure_hpa=pres,
                wind_kmh=self._safe(daily, "wind_speed_10m_max", i, 15.0),
                uv_index=self._safe(daily, "uv_index_max", i, 5.0),
                sunrise=sr.split("T")[1][:5] if "T" in sr else "05:30",
                sunset=ss.split("T")[1][:5] if "T" in ss else "20:30",
                is_forecast=d > today,
            )

        return result

    @staticmethod
    def _safe(daily: dict, key: str, idx: int, default: float) -> float:
        vals = daily.get(key, [])
        val = vals[idx] if idx < len(vals) else None
        return float(val) if val is not None else default
