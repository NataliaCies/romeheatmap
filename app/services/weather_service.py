"""WeatherService — district data from Open-Meteo when satellite unavailable."""
from __future__ import annotations
import asyncio
from datetime import date, timedelta
import numpy as np
from app.core.cache import cache_get, cache_set
from app.core.config import get_settings
from app.core.exceptions import DistrictNotFoundError
from app.core.logging import get_logger
from app.models.schemas import (CompareResponse, DistrictCompareItem, DistrictDetail,
    DistrictSummary, DistrictTimeseries, HeatPoint, RomeOverview, TimeseriesPoint)
from app.repositories.weather import DailyWeather, WeatherRepository
from app.utils.districts import DISTRICT_REGISTRY, District
from app.utils.gis import compute_livability_score
from app.utils.timing import log_execution_time

logger = get_logger(__name__)


class WeatherService:
    _ALPHA = 1.08; _BETA = 3.5

    def __init__(self, weather_repo: WeatherRepository | None = None) -> None:
        self._weather = weather_repo or WeatherRepository()
        self._settings = get_settings()

    @log_execution_time
    async def get_overview(self, target_date: date) -> RomeOverview:
        cache_key = f"weather:overview:{target_date}"
        cached = await cache_get(cache_key)
        if cached: return RomeOverview(**cached)
        weather = await self._fetch_weather(target_date)
        details = self._compute_all_districts(weather, target_date)
        overview = RomeOverview(
            date=target_date, tmax_celsius=round(weather.tmax_celsius,1),
            tmin_celsius=round(weather.tmin_celsius,1), humidity_pct=round(weather.humidity_pct,1),
            pressure_hpa=round(weather.pressure_hpa,1), uv_index=round(weather.uv_index,1),
            cloud_pct=round(weather.cloud_pct,1), wind_kmh=round(weather.wind_kmh,1),
            sunrise=weather.sunrise, sunset=weather.sunset,
            districts=[self._to_summary(d) for d in details],
            heat_points=[p for d in details for p in d.heat_points],
            green_points=[p for d in details for p in d.green_points],
            data_source="open-meteo+uhi-model")
        await cache_set(cache_key, overview.model_dump(), self._settings.cache_ttl_weather)
        return overview

    @log_execution_time
    async def get_district(self, district_id: str, target_date: date) -> DistrictDetail:
        if district_id not in DISTRICT_REGISTRY: raise DistrictNotFoundError(f"District '{district_id}' not found")
        cache_key = f"weather:district:{district_id}:{target_date}"
        cached = await cache_get(cache_key)
        if cached: return DistrictDetail(**cached)
        weather = await self._fetch_weather(target_date)
        detail = self._compute_district(DISTRICT_REGISTRY[district_id], weather, target_date)
        await cache_set(cache_key, detail.model_dump(), self._settings.cache_ttl_weather)
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
    async def get_timeseries(self, district_id: str, months: int = 12) -> DistrictTimeseries:
        if district_id not in DISTRICT_REGISTRY: raise DistrictNotFoundError(f"District '{district_id}' not found")
        cache_key = f"weather:timeseries:{district_id}:{months}"
        cached = await cache_get(cache_key)
        if cached: return DistrictTimeseries(**cached)
        today = date.today()
        start = today.replace(day=1) - timedelta(days=30*(months-1))
        weather_map = await self._weather.get_range(start, today)
        district = DISTRICT_REGISTRY[district_id]
        points = []
        for i in range(months):
            sample = today.replace(day=15) - timedelta(days=30*(months-1-i))
            closest = min(weather_map.keys(), key=lambda d: abs((d-sample).days), default=None)
            if closest is None: continue
            wx = weather_map[closest]
            lst = self._compute_lst(wx, district)
            ndvi = self._seasonal_ndvi(district, closest.month)
            humidity = float(np.clip(wx.humidity_pct + district.humidity_modifier, 10, 100))
            score = float(np.clip(50+ndvi*25 - np.clip((lst-20)/30,0,1)*30 + (1-wx.cloud_pct/100)*5, 0, 100))
            points.append(TimeseriesPoint(date=sample, month_label=sample.strftime("%b %Y"),
                mean_lst_celsius=round(lst,1), mean_ndvi=round(ndvi,3),
                humidity_pct=round(humidity,1), livability_score=round(score,1), cloud_masked_pct=0.0))
        result = DistrictTimeseries(district_id=district_id, district_label=district.label, points=points)
        await cache_set(cache_key, result.model_dump(), self._settings.cache_ttl_districts)
        return result

    async def _fetch_weather(self, target_date: date) -> DailyWeather:
        cache_key = f"weather:daily:{target_date}"
        cached = await cache_get(cache_key)
        if cached: return DailyWeather(**cached)
        today = date.today()
        start = min(target_date, today) - timedelta(days=5)
        end = max(target_date, today) + timedelta(days=7)
        weather_map = await self._weather.get_range(start, end)
        wx = weather_map.get(target_date) or min(weather_map.values(), key=lambda w: abs((w.date-target_date).days))
        await cache_set(cache_key, wx.__dict__, self._settings.cache_ttl_weather)
        return wx

    def _compute_all_districts(self, weather: DailyWeather, target_date: date) -> list[DistrictDetail]:
        return [self._compute_district(d, weather, target_date) for d in DISTRICT_REGISTRY.values()]

    def _compute_district(self, district: District, weather: DailyWeather, target_date: date) -> DistrictDetail:
        lst = self._compute_lst(weather, district)
        ndvi = self._seasonal_ndvi(district, target_date.month)
        humidity = float(np.clip(weather.humidity_pct + district.humidity_modifier, 10, 100))
        score = float(np.clip(50+ndvi*25 - np.clip((lst-20)/30,0,1)*30 + (1-weather.cloud_pct/100)*5, 0, 100))
        return DistrictDetail(
            id=district.id, label=district.label, lat=district.lat, lon=district.lon,
            mean_lst_celsius=round(lst,1), mean_ndvi=round(ndvi,3),
            humidity_pct=round(humidity,1), livability_score=round(score,1),
            cloud_masked_pct=0.0, data_source="estimated", scene_date=None,
            heat_points=self._heat_points(district, lst, weather),
            green_points=self._green_points(district, ndvi),
            tmax_celsius=weather.tmax_celsius, tmin_celsius=weather.tmin_celsius)

    def _compute_lst(self, weather: DailyWeather, district: District) -> float:
        air_day = weather.tmax_celsius*0.65 + weather.tmin_celsius*0.35
        return air_day * self._ALPHA + self._BETA + district.uhi_modifier

    def _seasonal_ndvi(self, district: District, month: int) -> float:
        offset = -0.15 * np.sin((month-3)*np.pi/6)
        return float(np.clip(district.ndvi_baseline + float(offset), 0.0, 1.0))

    def _heat_points(self, district, mean_lst, weather):
        rng = np.random.default_rng(seed=hash(district.id) % (2**31))
        pts = []
        for dlat in np.linspace(-0.012, 0.012, 5):
            for dlon in np.linspace(-0.014, 0.014, 5):
                lst_val = round(mean_lst + float(rng.uniform(-1.5, 1.5)), 1)
                pts.append(HeatPoint(lat=round(district.lat+float(dlat),5),
                    lon=round(district.lon+float(dlon),5), lst_celsius=lst_val, ndvi=0.0,
                    intensity=round(float(np.clip((lst_val-20)/30,0,1)),3)))
        return pts

    def _green_points(self, district, mean_ndvi):
        rng = np.random.default_rng(seed=hash(district.id+"g") % (2**31))
        pts = []
        for dlat in np.linspace(-0.012, 0.012, 5):
            for dlon in np.linspace(-0.014, 0.014, 5):
                ndvi_val = float(np.clip(mean_ndvi + float(rng.uniform(-0.05,0.05)), -1, 1))
                pts.append(HeatPoint(lat=round(district.lat+float(dlat),5),
                    lon=round(district.lon+float(dlon),5), lst_celsius=0.0,
                    ndvi=round(ndvi_val,3), intensity=round(float(np.clip((ndvi_val+1)/2,0,1)),3)))
        return pts

    @staticmethod
    def _to_summary(d: DistrictDetail) -> DistrictSummary:
        return DistrictSummary(id=d.id, label=d.label, lat=d.lat, lon=d.lon,
            mean_lst_celsius=d.mean_lst_celsius, mean_ndvi=d.mean_ndvi, humidity_pct=d.humidity_pct,
            livability_score=d.livability_score, cloud_masked_pct=d.cloud_masked_pct,
            data_source=d.data_source, scene_date=d.scene_date)

    @staticmethod
    def _to_compare_item(d: DistrictDetail) -> DistrictCompareItem:
        return DistrictCompareItem(id=d.id, label=d.label, mean_lst_celsius=d.mean_lst_celsius,
            mean_ndvi=d.mean_ndvi, humidity_pct=d.humidity_pct, livability_score=d.livability_score)
