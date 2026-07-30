"""Repository for Open-Meteo weather data (no API key required).

Fix (2026-07): Open-Meteo has two separate endpoints:

  /forecast  — works for dates roughly within the last 2-3 days + future
  /archive   — works for historical data (anything older than ~5 days)

The original code always called /forecast regardless of the date range.
When get_timeseries() requested 12 months of history, /forecast returned
HTTP 400 because the start_date was too far in the past.

The fix: split requests — dates older than ARCHIVE_THRESHOLD_DAYS use
/archive, recent + future dates use /forecast. When a range spans both,
we make two requests and merge the results.
"""

from __future__ import annotations

from datetime import date, timedelta
from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.core.exceptions import WeatherDataError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Open-Meteo /forecast supports at most this many days into the past.
# Beyond this, we must use /archive instead.
_FORECAST_LOOKBACK_DAYS = 5

# Daily variables available on both /forecast and /archive
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

# Hourly variables for humidity and pressure
# Note: /archive uses relative_humidity_2m (underscore), not relativehumidity_2m
_HOURLY_VARS_FORECAST = "relativehumidity_2m,surface_pressure"
_HOURLY_VARS_ARCHIVE = "relative_humidity_2m,surface_pressure"


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

        Automatically routes to /archive or /forecast (or both) depending
        on whether the requested dates are historical or recent/future.

        Args:
            start: First date of the range (inclusive).
            end:   Last date of the range (inclusive).

        Returns:
            Dict mapping each date in the range to a DailyWeather record.

        Raises:
            WeatherDataError: On HTTP error or network failure.
        """
        logger.info("weather_fetch_start", start=str(start), end=str(end))

        today = date.today()
        # Boundary: dates up to and including this go to /archive
        archive_cutoff = today - timedelta(days=_FORECAST_LOOKBACK_DAYS)

        result: dict[date, DailyWeather] = {}

        # ── Historical range → /archive ───────────────────────────────────
        if start <= archive_cutoff:
            archive_end = min(end, archive_cutoff)
            data = await self._fetch(
                endpoint="archive",
                start=start,
                end=archive_end,
                hourly_vars=_HOURLY_VARS_ARCHIVE,
            )
            result.update(self._parse(data))

        # ── Recent / forecast range → /forecast ───────────────────────────
        if end > archive_cutoff:
            forecast_start = max(start, archive_cutoff + timedelta(days=1))
            data = await self._fetch(
                endpoint="forecast",
                start=forecast_start,
                end=end,
                hourly_vars=_HOURLY_VARS_FORECAST,
            )
            result.update(self._parse(data))

        logger.info("weather_fetch_done", days=len(result), start=str(start), end=str(end))
        return result

    # ── Private ───────────────────────────────────────────────────────────────

    async def _fetch(
        self,
        endpoint: str,
        start: date,
        end: date,
        hourly_vars: str,
    ) -> dict:
        """Make a single request to Open-Meteo and return the raw JSON dict."""
        base = self._settings.open_meteo_base_url
        url = f"{base}/{endpoint}"

        params = {
            "latitude": self.ROME_LAT,
            "longitude": self.ROME_LON,
            "daily": _DAILY_VARS,
            "hourly": hourly_vars,
            "timezone": self.TIMEZONE,
            "start_date": str(start),
            "end_date": str(end),
        }

        logger.debug("open_meteo_request", endpoint=endpoint,
                     start=str(start), end=str(end))

        async with httpx.AsyncClient(timeout=20) as client:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                body = exc.response.text[:300]
                raise WeatherDataError(
                    f"Open-Meteo /{endpoint} returned {exc.response.status_code} "
                    f"for {start}→{end}: {body}"
                ) from exc
            except httpx.RequestError as exc:
                raise WeatherDataError(
                    f"Cannot reach Open-Meteo /{endpoint}: {exc}"
                ) from exc

        return response.json()

    def _parse(self, data: dict) -> dict[date, DailyWeather]:
        """Parse Open-Meteo JSON response into DailyWeather records."""
        daily = data.get("daily", {})
        hourly = data.get("hourly", {})
        today = date.today()
        result: dict[date, DailyWeather] = {}

        # Build noon-hour lookup for humidity and pressure
        # Works for both relativehumidity_2m (/forecast) and
        # relative_humidity_2m (/archive) — we check both keys
        hourly_times = hourly.get("time", [])
        hum_values = (
            hourly.get("relativehumidity_2m")
            or hourly.get("relative_humidity_2m")
            or []
        )
        pres_values = hourly.get("surface_pressure", [])

        noon_lookup: dict[str, tuple[float, float]] = {}
        for i, t in enumerate(hourly_times):
            if "T12:00" in t:
                hum = float(hum_values[i]) if i < len(hum_values) else 55.0
                pres = float(pres_values[i]) if i < len(pres_values) else 1013.0
                noon_lookup[t[:10]] = (hum, pres)

        for i, d_str in enumerate(daily.get("time", [])):
            d = date.fromisoformat(d_str)
            hum, pres = noon_lookup.get(d_str, (55.0, 1013.0))

            sr = self._safe_str(daily.get("sunrise", []), i)
            ss = self._safe_str(daily.get("sunset", []), i)

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

    @staticmethod
    def _safe_str(lst: list, idx: int) -> str:
        return lst[idx] if idx < len(lst) else ""
