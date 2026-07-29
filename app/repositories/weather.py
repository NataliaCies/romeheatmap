"""Repository for Open-Meteo weather data (no API key required)."""
from __future__ import annotations
from datetime import date
from dataclasses import dataclass
import httpx
from app.core.config import get_settings
from app.core.exceptions import WeatherDataError
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class DailyWeather:
    date: date; tmax_celsius: float; tmin_celsius: float
    cloud_pct: float; humidity_pct: float; pressure_hpa: float
    wind_kmh: float; uv_index: float; sunrise: str; sunset: str
    is_forecast: bool


class WeatherRepository:
    ROME_LAT = 41.89; ROME_LON = 12.48; TIMEZONE = "Europe/Rome"

    def __init__(self) -> None:
        self._settings = get_settings()

    async def get_range(self, start: date, end: date) -> dict[date, DailyWeather]:
        """Fetch daily weather for a date range from Open-Meteo."""
        logger.info("weather_fetch_start", start=str(start), end=str(end))
        params = {
            "latitude": self.ROME_LAT, "longitude": self.ROME_LON,
            "daily": ",".join(["temperature_2m_max","temperature_2m_min","cloud_cover_mean",
                               "precipitation_sum","sunrise","sunset","uv_index_max","wind_speed_10m_max"]),
            "hourly": "relativehumidity_2m,surface_pressure",
            "timezone": self.TIMEZONE, "start_date": str(start), "end_date": str(end),
        }
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                response = await client.get(f"{self._settings.open_meteo_base_url}/forecast", params=params)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise WeatherDataError(f"Open-Meteo returned {exc.response.status_code}") from exc
            except httpx.RequestError as exc:
                raise WeatherDataError(f"Cannot reach Open-Meteo: {exc}") from exc
        data = response.json()
        logger.info("weather_fetch_done", days=len(data.get("daily", {}).get("time", [])))
        return self._parse(data, start)

    def _parse(self, data: dict, start: date) -> dict[date, DailyWeather]:
        daily = data.get("daily", {}); hourly = data.get("hourly", {})
        today = date.today(); result: dict[date, DailyWeather] = {}
        hourly_times = hourly.get("time", [])
        noon_lookup: dict[str, tuple[float, float]] = {}
        for i, t in enumerate(hourly_times):
            if "T12:00" in t:
                noon_lookup[t[:10]] = (
                    float(hourly.get("relativehumidity_2m", [55])[i]) if i < len(hourly.get("relativehumidity_2m",[])) else 55.0,
                    float(hourly.get("surface_pressure", [1013])[i]) if i < len(hourly.get("surface_pressure",[])) else 1013.0,
                )
        for i, d_str in enumerate(daily.get("time", [])):
            d = date.fromisoformat(d_str)
            hum, pres = noon_lookup.get(d_str, (55.0, 1013.0))
            sr = (daily.get("sunrise") or [""])[i] if i < len(daily.get("sunrise",[])) else ""
            ss = (daily.get("sunset") or [""])[i] if i < len(daily.get("sunset",[])) else ""
            result[d] = DailyWeather(
                date=d,
                tmax_celsius=self._safe(daily, "temperature_2m_max", i, 32.0),
                tmin_celsius=self._safe(daily, "temperature_2m_min", i, 20.0),
                cloud_pct=self._safe(daily, "cloud_cover_mean", i, 25.0),
                humidity_pct=hum, pressure_hpa=pres,
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
