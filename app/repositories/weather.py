"""Repository for Open-Meteo weather data.

Simplified: only last 7 days + 7 days ahead via /forecast.
No /archive calls — avoids 404 errors and reduces API usage.
Timeseries endpoint also uses only last 7 days.
"""
from __future__ import annotations
from datetime import date, timedelta
from dataclasses import dataclass
import httpx
from app.core.config import get_settings
from app.core.exceptions import WeatherDataError
from app.core.logging import get_logger

logger = get_logger(__name__)

_DAILY_VARS = ",".join([
    "temperature_2m_max", "temperature_2m_min", "cloud_cover_mean",
    "precipitation_sum", "sunrise", "sunset", "uv_index_max", "wind_speed_10m_max",
])
_HOURLY_VARS = "relativehumidity_2m,surface_pressure"


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
        """Fetch weather for a date range using /forecast only.

        /forecast covers last 92 days + 16 days ahead — enough for all use cases.
        No /archive calls to avoid 404 errors.
        """
        # Clamp start to at most 7 days ago to keep requests small
        today = date.today()
        earliest = today - timedelta(days=7)
        if start < earliest:
            start = earliest

        logger.info("weather_fetch_start", start=str(start), end=str(end))
        data = await self._fetch(start, end)
        result = self._parse(data)
        logger.info("weather_fetch_done", days=len(result))
        return result

    async def _fetch(self, start: date, end: date) -> dict:
        url = f"{self._settings.open_meteo_base_url}/forecast"
        params = {
            "latitude": self.ROME_LAT,
            "longitude": self.ROME_LON,
            "daily": _DAILY_VARS,
            "hourly": _HOURLY_VARS,
            "timezone": self.TIMEZONE,
            "start_date": str(start),
            "end_date": str(end),
        }
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise WeatherDataError(
                    f"Open-Meteo /forecast returned {exc.response.status_code} "
                    f"for {start}->{end}: {exc.response.text[:200]}"
                ) from exc
            except httpx.RequestError as exc:
                raise WeatherDataError(
                    f"Cannot reach Open-Meteo: {exc}"
                ) from exc
        return response.json()

    def _parse(self, data: dict) -> dict[date, DailyWeather]:
        daily = data.get("daily", {})
        hourly = data.get("hourly", {})
        today = date.today()

        hum_values = hourly.get("relativehumidity_2m", [])
        pres_values = hourly.get("surface_pressure", [])
        hourly_times = hourly.get("time", [])

        noon_lookup: dict[str, tuple[float, float]] = {}
        for i, t in enumerate(hourly_times):
            if "T12:00" in t:
                hum = float(hum_values[i]) if i < len(hum_values) else 55.0
                pres = float(pres_values[i]) if i < len(pres_values) else 1013.0
                noon_lookup[t[:10]] = (hum, pres)

        sr_list = daily.get("sunrise", [])
        ss_list = daily.get("sunset", [])
        result: dict[date, DailyWeather] = {}

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
