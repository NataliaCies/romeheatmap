"""ClimateService — satellite pipeline with background download and weather fallback.

Stage 5 (revised): Background satellite download pattern.

Problem: Render free tier has 30s HTTP timeout. Sentinel-2 SAFE product
is ~1GB and takes 3-5 minutes to download. This causes every request to
timeout before satellite data is ready.

Solution: Fire-and-forget background download pattern:
  1. Request arrives → check Redis cache
  2. Cache miss → return Open-Meteo fallback IMMEDIATELY (fast response)
  3. In background → download satellite data (no HTTP timeout constraint)
  4. Background task finishes → stores result in Redis with 12h TTL
  5. Next request → cache hit → returns real satellite data

This means:
  - First request always returns "estimated" (Open-Meteo)
  - Subsequent requests return "sentinel2" once background download completes
  - Background download runs once per scene (cached 12h)
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from enum import Enum

from app.core.cache import cache_get, cache_set
from app.core.config import get_settings
from app.core.exceptions import (
    CopernicusAuthError,
    DistrictNotFoundError,
    GISProcessingError,
    NoSatelliteSceneError,
    SatelliteDataError,
)
from app.core.logging import get_logger
from app.models.schemas import (
    CompareResponse,
    DistrictCompareItem,
    DistrictDetail,
    DistrictSummary,
    DistrictTimeseries,
    RomeOverview,
)
from app.repositories.copernicus_auth import CopernicusTokenRepository
from app.repositories.satellite_download import get_satellite_repository
from app.repositories.satellite_search import SatelliteSearchRepository
from app.repositories.weather import WeatherRepository, DailyWeather
from app.services.satellite.district_stats import DistrictStatsService
from app.services.satellite.lst_pipeline import (
    LSTCalibrator,
    LSTProcessingPipeline,
    UHIModifier,
)
from app.services.weather_service import WeatherService
from app.utils.districts import DISTRICT_REGISTRY
from app.utils.timing import log_execution_time

logger = get_logger(__name__)

_FALLBACK_EXCEPTIONS = (
    NoSatelliteSceneError,
    SatelliteDataError,
    CopernicusAuthError,
    GISProcessingError,
)

# Key prefix to track which scenes are currently downloading
_DOWNLOADING_KEY_PREFIX = "satellite:downloading:"


class DataSource(str, Enum):
    SENTINEL2 = "sentinel2+open-meteo"
    WEATHER_FALLBACK = "open-meteo+uhi-model"


class ClimateService:
    def __init__(self, weather_service=None, weather_repo=None):
        self._settings = get_settings()
        self._auth = CopernicusTokenRepository()
        self._search = SatelliteSearchRepository(self._auth)
        self._stats_svc = DistrictStatsService()
        self._weather_svc = weather_service or WeatherService(
            weather_repo=weather_repo or WeatherRepository()
        )

    @log_execution_time
    async def get_overview(self, target_date: date) -> RomeOverview:
        cache_key = f"climate:overview:{target_date}"
        cached = await cache_get(cache_key)
        if cached:
            return RomeOverview(**cached)

        overview, source = await self._get_overview_with_source(target_date)
        ttl = (self._settings.cache_ttl_satellite
               if source == DataSource.SENTINEL2
               else self._settings.cache_ttl_weather)
        await cache_set(cache_key, overview.model_dump(), ttl)
        logger.info("overview_served", date=str(target_date), source=source)
        return overview

    @log_execution_time
    async def get_district(self, district_id: str, target_date: date) -> DistrictDetail:
        if district_id not in DISTRICT_REGISTRY:
            raise DistrictNotFoundError(f"District '{district_id}' not found")

        cache_key = f"climate:district:{district_id}:{target_date}"
        cached = await cache_get(cache_key)
        if cached:
            return DistrictDetail(**cached)

        all_details, source = await self._get_all_districts_with_source(target_date)
        detail = next((d for d in all_details if d.id == district_id), None)
        if detail is None:
            raise DistrictNotFoundError(f"No data for '{district_id}'")

        ttl = (self._settings.cache_ttl_satellite
               if source == DataSource.SENTINEL2
               else self._settings.cache_ttl_weather)
        await cache_set(cache_key, detail.model_dump(), ttl)
        return detail

    @log_execution_time
    async def compare(self, district_a: str, district_b: str,
                      target_date: date) -> CompareResponse:
        for did in (district_a, district_b):
            if did not in DISTRICT_REGISTRY:
                raise DistrictNotFoundError(f"District '{did}' not found")
        da, db = await asyncio.gather(
            self.get_district(district_a, target_date),
            self.get_district(district_b, target_date),
        )
        return CompareResponse(
            date=target_date,
            district_a=self._to_compare_item(da),
            district_b=self._to_compare_item(db),
            delta_lst_celsius=round(da.mean_lst_celsius - db.mean_lst_celsius, 1),
            delta_ndvi=round(da.mean_ndvi - db.mean_ndvi, 3),
            delta_livability=round(da.livability_score - db.livability_score, 1),
            delta_humidity_pct=round(da.humidity_pct - db.humidity_pct, 1),
        )

    @log_execution_time
    async def get_timeseries(self, district_id: str, months: int = 12):
        if district_id not in DISTRICT_REGISTRY:
            raise DistrictNotFoundError(f"District '{district_id}' not found")
        cache_key = f"climate:timeseries:{district_id}:{months}"
        cached = await cache_get(cache_key)
        if cached:
            return DistrictTimeseries(**cached)
        result = await self._weather_svc.get_timeseries(district_id, months)
        await cache_set(cache_key, result.model_dump(), self._settings.cache_ttl_satellite)
        return result

    # ── Core routing ──────────────────────────────────────────────────────────

    async def _get_overview_with_source(self, target_date: date):
        # Check if we have satellite data cached from a background download
        sat_cache_key = f"climate:satellite_districts:{target_date}"
        sat_cached = await cache_get(sat_cache_key)

        if sat_cached:
            logger.info("satellite_cache_hit", date=str(target_date))
            details = [DistrictDetail(**d) for d in sat_cached]
            weather = await self._fetch_weather(target_date)
            overview = self._build_overview(details, weather, target_date, DataSource.SENTINEL2)
            return overview, DataSource.SENTINEL2

        # No satellite cache — return weather fallback immediately
        # and trigger background download
        weather = await self._fetch_weather(target_date)
        overview = await self._weather_svc.get_overview(target_date)

        # Fire background satellite download (non-blocking)
        asyncio.create_task(
            self._background_satellite_download(target_date, weather)
        )

        return overview, DataSource.WEATHER_FALLBACK

    async def _get_all_districts_with_source(self, target_date: date):
        sat_cache_key = f"climate:satellite_districts:{target_date}"
        sat_cached = await cache_get(sat_cache_key)

        if sat_cached:
            return [DistrictDetail(**d) for d in sat_cached], DataSource.SENTINEL2

        # Return weather fallback immediately
        weather = await self._fetch_weather(target_date)
        details = self._weather_svc._compute_all_districts(weather, target_date)

        # Fire background satellite download (non-blocking)
        asyncio.create_task(
            self._background_satellite_download(target_date, weather)
        )

        return details, DataSource.WEATHER_FALLBACK

    # ── Background satellite download ─────────────────────────────────────────

    async def _background_satellite_download(
        self, target_date: date, weather: DailyWeather
    ) -> None:
        """Run satellite pipeline in background — no HTTP timeout constraint.

        Stores result in Redis when done. Next request will hit cache.
        Uses a lock key to prevent parallel downloads of the same scene.
        """
        lock_key = f"{_DOWNLOADING_KEY_PREFIX}{target_date}"

        # Check if already downloading
        already = await cache_get(lock_key)
        if already:
            logger.info("satellite_download_already_running", date=str(target_date))
            return

        # Set lock (expires in 30 minutes)
        await cache_set(lock_key, "1", 1800)

        logger.info("satellite_background_download_start", date=str(target_date))

        try:
            scene = await self._search.find_best_scene(
                target_date=target_date,
                bbox=self._settings.rome_bbox,
            )

            satellite = get_satellite_repository("sentinel2", self._auth)
            pipeline = LSTProcessingPipeline(
                satellite=satellite,
                calibrator=LSTCalibrator(),
                uhi=UHIModifier(),
            )

            result = await pipeline.run(
                scene=scene,
                bbox=self._settings.rome_bbox,
                weather=weather,
            )

            details = self._stats_svc.compute_from_pipeline(
                result=result,
                weather=weather,
                data_source="sentinel2",
            )

            # Cache satellite results for 12 hours
            sat_cache_key = f"climate:satellite_districts:{target_date}"
            await cache_set(
                sat_cache_key,
                [d.model_dump() for d in details],
                self._settings.cache_ttl_satellite,
            )

            # Also cache per-district
            for d in details:
                await cache_set(
                    f"climate:district:{d.id}:{target_date}",
                    d.model_dump(),
                    self._settings.cache_ttl_satellite,
                )

            logger.info(
                "satellite_background_download_done",
                date=str(target_date),
                scene_id=scene.scene_id,
                districts=len(details),
            )

        except _FALLBACK_EXCEPTIONS as exc:
            logger.warning(
                "satellite_background_download_failed",
                date=str(target_date),
                reason=type(exc).__name__,
                detail=str(exc)[:120],
            )
        except Exception as exc:
            logger.error(
                "satellite_background_download_error",
                date=str(target_date),
                error=str(exc),
            )
        finally:
            # Remove lock so it can retry later
            await cache_set(lock_key, "", 1)

    # ── Weather helpers ───────────────────────────────────────────────────────

    async def _fetch_weather(self, target_date: date) -> DailyWeather:
        cache_key = f"climate:weather:{target_date}"
        cached = await cache_get(cache_key)
        if cached:
            return DailyWeather(**cached)

        today = date.today()
        start = today - timedelta(days=7)
        end = today + timedelta(days=7)
        weather_map = await WeatherRepository().get_range(start, end)
        wx = weather_map.get(target_date) or min(
            weather_map.values(),
            key=lambda w: abs((w.date - target_date).days),
        )
        await cache_set(cache_key, wx.__dict__, self._settings.cache_ttl_weather)
        return wx

    # ── Build helpers ─────────────────────────────────────────────────────────

    def _build_overview(self, details, weather, target_date, source):
        return RomeOverview(
            date=target_date,
            tmax_celsius=round(weather.tmax_celsius, 1),
            tmin_celsius=round(weather.tmin_celsius, 1),
            humidity_pct=round(weather.humidity_pct, 1),
            pressure_hpa=round(weather.pressure_hpa, 1),
            uv_index=round(weather.uv_index, 1),
            cloud_pct=round(weather.cloud_pct, 1),
            wind_kmh=round(weather.wind_kmh, 1),
            sunrise=weather.sunrise,
            sunset=weather.sunset,
            districts=[self._to_summary(d) for d in details],
            heat_points=[p for d in details for p in d.heat_points],
            green_points=[p for d in details for p in d.green_points],
            data_source=source.value,
        )

    @staticmethod
    def _to_summary(d: DistrictDetail) -> DistrictSummary:
        return DistrictSummary(
            id=d.id, label=d.label, lat=d.lat, lon=d.lon,
            mean_lst_celsius=d.mean_lst_celsius, mean_ndvi=d.mean_ndvi,
            humidity_pct=d.humidity_pct, livability_score=d.livability_score,
            cloud_masked_pct=d.cloud_masked_pct, data_source=d.data_source,
            scene_date=d.scene_date,
        )

    @staticmethod
    def _to_compare_item(d: DistrictDetail) -> DistrictCompareItem:
        return DistrictCompareItem(
            id=d.id, label=d.label,
            mean_lst_celsius=d.mean_lst_celsius, mean_ndvi=d.mean_ndvi,
            humidity_pct=d.humidity_pct, livability_score=d.livability_score,
        )
