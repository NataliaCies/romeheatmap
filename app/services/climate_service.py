"""ClimateService — satellite pipeline with automatic weather fallback."""
from __future__ import annotations
import asyncio
from datetime import date, timedelta
from enum import Enum
from app.core.cache import cache_get, cache_set
from app.core.config import get_settings
from app.core.exceptions import (CopernicusAuthError, DistrictNotFoundError,
    GISProcessingError, NoSatelliteSceneError, SatelliteDataError)
from app.core.logging import get_logger
from app.models.schemas import (CompareResponse, DistrictCompareItem, DistrictDetail,
    DistrictSummary, DistrictTimeseries, RomeOverview)
from app.repositories.copernicus_auth import CopernicusTokenRepository
from app.repositories.satellite_download import get_satellite_repository
from app.repositories.satellite_search import SatelliteSearchRepository
from app.repositories.weather import WeatherRepository, DailyWeather
from app.services.satellite.district_stats import DistrictStatsService
from app.services.satellite.lst_pipeline import LSTCalibrator, LSTProcessingPipeline, UHIModifier
from app.services.weather_service import WeatherService
from app.utils.districts import DISTRICT_REGISTRY
from app.utils.timing import log_execution_time

logger = get_logger(__name__)
_FALLBACK_EXCEPTIONS = (NoSatelliteSceneError, SatelliteDataError, CopernicusAuthError, GISProcessingError)


class DataSource(str, Enum):
    SENTINEL2 = "sentinel2+open-meteo"
    WEATHER_FALLBACK = "open-meteo+uhi-model"


class ClimateService:
    def __init__(self, weather_service=None, weather_repo=None):
        self._settings = get_settings()
        self._auth = CopernicusTokenRepository()
        self._search = SatelliteSearchRepository(self._auth)
        self._stats_svc = DistrictStatsService()
        self._weather_svc = weather_service or WeatherService(weather_repo=weather_repo or WeatherRepository())

    @log_execution_time
    async def get_overview(self, target_date: date) -> RomeOverview:
        cache_key = f"climate:overview:{target_date}"
        cached = await cache_get(cache_key)
        if cached: return RomeOverview(**cached)
        overview, source = await self._get_overview_with_source(target_date)
        ttl = self._settings.cache_ttl_satellite if source == DataSource.SENTINEL2 else self._settings.cache_ttl_weather
        await cache_set(cache_key, overview.model_dump(), ttl)
        logger.info("overview_served", date=str(target_date), source=source)
        return overview

    @log_execution_time
    async def get_district(self, district_id: str, target_date: date) -> DistrictDetail:
        if district_id not in DISTRICT_REGISTRY:
            raise DistrictNotFoundError(f"District '{district_id}' not found")
        cache_key = f"climate:district:{district_id}:{target_date}"
        cached = await cache_get(cache_key)
        if cached: return DistrictDetail(**cached)
        all_details, source = await self._get_all_districts_with_source(target_date)
        detail = next((d for d in all_details if d.id == district_id), None)
        if detail is None: raise DistrictNotFoundError(f"No data for '{district_id}'")
        ttl = self._settings.cache_ttl_satellite if source == DataSource.SENTINEL2 else self._settings.cache_ttl_weather
        await cache_set(cache_key, detail.model_dump(), ttl)
        return detail

    @log_execution_time
    async def compare(self, district_a: str, district_b: str, target_date: date) -> CompareResponse:
        for did in (district_a, district_b):
            if did not in DISTRICT_REGISTRY: raise DistrictNotFoundError(f"District '{did}' not found")
        da, db = await asyncio.gather(self.get_district(district_a, target_date),
                                      self.get_district(district_b, target_date))
        return CompareResponse(date=target_date,
            district_a=self._to_compare_item(da), district_b=self._to_compare_item(db),
            delta_lst_celsius=round(da.mean_lst_celsius-db.mean_lst_celsius,1),
            delta_ndvi=round(da.mean_ndvi-db.mean_ndvi,3),
            delta_livability=round(da.livability_score-db.livability_score,1),
            delta_humidity_pct=round(da.humidity_pct-db.humidity_pct,1))

    @log_execution_time
    async def get_timeseries(self, district_id: str, months: int = 12):
        if district_id not in DISTRICT_REGISTRY:
            raise DistrictNotFoundError(f"District '{district_id}' not found")
        cache_key = f"climate:timeseries:{district_id}:{months}"
        cached = await cache_get(cache_key)
        if cached: return DistrictTimeseries(**cached)
        result = await self._weather_svc.get_timeseries(district_id, months)
        await cache_set(cache_key, result.model_dump(), self._settings.cache_ttl_satellite)
        return result

    async def _get_overview_with_source(self, target_date):
        try:
            details, source = await self._run_satellite_pipeline(target_date)
            weather = await self._fetch_weather(target_date)
            return self._build_overview(details, weather, target_date, source), source
        except _FALLBACK_EXCEPTIONS as exc:
            logger.warning("satellite_fallback_triggered", reason=type(exc).__name__, detail=str(exc)[:120])
            overview = await self._weather_svc.get_overview(target_date)
            return overview, DataSource.WEATHER_FALLBACK

    async def _get_all_districts_with_source(self, target_date):
        cache_key = f"climate:all_districts:{target_date}"
        cached = await cache_get(cache_key)
        if cached:
            return [DistrictDetail(**d) for d in cached["districts"]], DataSource(cached["source"])
        try:
            details, source = await self._run_satellite_pipeline(target_date)
        except _FALLBACK_EXCEPTIONS as exc:
            logger.warning("satellite_fallback_triggered", reason=type(exc).__name__, detail=str(exc)[:120])
            weather = await self._fetch_weather(target_date)
            details = self._weather_svc._compute_all_districts(weather, target_date)
            source = DataSource.WEATHER_FALLBACK
        payload = {"districts": [d.model_dump() for d in details], "source": source.value}
        ttl = self._settings.cache_ttl_satellite if source == DataSource.SENTINEL2 else self._settings.cache_ttl_weather
        await cache_set(cache_key, payload, ttl)
        return details, source

    async def _run_satellite_pipeline(self, target_date):
        weather, scene = await asyncio.gather(
            self._fetch_weather(target_date),
            self._search.find_best_scene(target_date=target_date, bbox=self._settings.rome_bbox))
        logger.info("satellite_pipeline_starting", scene_id=scene.scene_id)
        satellite = get_satellite_repository("sentinel2", self._auth)
        pipeline = LSTProcessingPipeline(satellite=satellite, calibrator=LSTCalibrator(), uhi=UHIModifier())
        result = await pipeline.run(scene=scene, bbox=self._settings.rome_bbox, weather=weather)
        details = self._stats_svc.compute_from_pipeline(result=result, weather=weather, data_source="sentinel2")
        return details, DataSource.SENTINEL2

    async def _fetch_weather(self, target_date):
        cache_key = f"climate:weather:{target_date}"
        cached = await cache_get(cache_key)
        if cached: return DailyWeather(**cached)
        today = date.today()
        start = min(target_date, today) - timedelta(days=5)
        end = max(target_date, today) + timedelta(days=7)
        weather_map = await WeatherRepository().get_range(start, end)
        wx = weather_map.get(target_date) or min(weather_map.values(), key=lambda w: abs((w.date-target_date).days))
        await cache_set(cache_key, wx.__dict__, self._settings.cache_ttl_weather)
        return wx

    def _build_overview(self, details, weather, target_date, source):
        return RomeOverview(date=target_date, tmax_celsius=round(weather.tmax_celsius,1),
            tmin_celsius=round(weather.tmin_celsius,1), humidity_pct=round(weather.humidity_pct,1),
            pressure_hpa=round(weather.pressure_hpa,1), uv_index=round(weather.uv_index,1),
            cloud_pct=round(weather.cloud_pct,1), wind_kmh=round(weather.wind_kmh,1),
            sunrise=weather.sunrise, sunset=weather.sunset,
            districts=[self._to_summary(d) for d in details],
            heat_points=[p for d in details for p in d.heat_points],
            green_points=[p for d in details for p in d.green_points],
            data_source=source.value)

    @staticmethod
    def _to_summary(d): return DistrictSummary(id=d.id, label=d.label, lat=d.lat, lon=d.lon,
        mean_lst_celsius=d.mean_lst_celsius, mean_ndvi=d.mean_ndvi, humidity_pct=d.humidity_pct,
        livability_score=d.livability_score, cloud_masked_pct=d.cloud_masked_pct,
        data_source=d.data_source, scene_date=d.scene_date)

    @staticmethod
    def _to_compare_item(d): return DistrictCompareItem(id=d.id, label=d.label,
        mean_lst_celsius=d.mean_lst_celsius, mean_ndvi=d.mean_ndvi,
        humidity_pct=d.humidity_pct, livability_score=d.livability_score)
