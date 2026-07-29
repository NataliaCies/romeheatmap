"""Aggregate raster grids into per-district statistics."""
from __future__ import annotations
import numpy as np
from numpy.typing import NDArray
from app.core.logging import get_logger
from app.models.schemas import DistrictDetail, HeatPoint
from app.repositories.weather import DailyWeather
from app.utils.districts import District, DISTRICT_REGISTRY
from app.utils.gis import compute_livability_score

logger = get_logger(__name__)
Float32Array = NDArray[np.float32]
BoolArray = NDArray[np.bool_]


class DistrictStatsService:
    """Aggregate LST/NDVI rasters to per-district stats using vectorised numpy."""

    def compute_from_pipeline(self, result, weather: DailyWeather,
                               data_source: str = "sentinel2") -> list[DistrictDetail]:
        return self.compute_all_districts(
            lst=result.lst_celsius, ndvi=result.ndvi, intensity=result.intensity,
            lats=result.lats, lons=result.lons, cloud_mask=result.cloud_mask,
            weather=weather, scene_date=None, data_source=data_source)

    def compute_all_districts(self, lst, ndvi, intensity, lats, lons,
                               cloud_mask, weather, scene_date, data_source="sentinel2"):
        results = []
        for district in DISTRICT_REGISTRY.values():
            detail = self._compute_district(district, lst, ndvi, intensity,
                                            lats, lons, cloud_mask, weather, scene_date, data_source)
            logger.debug("district_stats_computed", district=district.id,
                         lst=detail.mean_lst_celsius, score=detail.livability_score)
            results.append(detail)
        return results

    def _compute_district(self, district, lst, ndvi, intensity,
                          lats, lons, cloud_mask, weather, scene_date, data_source):
        lon_min, lat_min, lon_max, lat_max = district.bbox
        row_mask = (lats >= lat_min) & (lats <= lat_max)
        col_mask = (lons >= lon_min) & (lons <= lon_max)
        spatial_mask = np.outer(row_mask, col_mask)
        valid_mask = spatial_mask & ~cloud_mask
        lst_d = np.where(valid_mask, lst, np.nan)
        ndvi_d = np.where(valid_mask, ndvi, np.nan)
        cloud_d = cloud_mask & spatial_mask
        valid_lst = lst_d[~np.isnan(lst_d)]
        valid_ndvi = ndvi_d[~np.isnan(ndvi_d)]
        total_px = int(spatial_mask.sum())
        cloud_pct = (int(cloud_d.sum()) / total_px * 100) if total_px > 0 else 0.0
        mean_lst = float(np.mean(valid_lst)) if len(valid_lst) > 0 else self._fallback_lst(district, weather)
        mean_ndvi = float(np.mean(valid_ndvi)) if len(valid_ndvi) > 0 else district.ndvi_baseline
        humidity = float(np.clip(weather.humidity_pct + district.humidity_modifier, 10, 100))
        livability = compute_livability_score(lst_d, ndvi_d, cloud_pct / 100)
        return DistrictDetail(
            id=district.id, label=district.label, lat=district.lat, lon=district.lon,
            mean_lst_celsius=round(mean_lst, 1), mean_ndvi=round(mean_ndvi, 3),
            humidity_pct=round(humidity, 1), livability_score=round(livability, 1),
            cloud_masked_pct=round(cloud_pct, 1), data_source=data_source,
            scene_date=scene_date,
            heat_points=self._heat_pts(lst, intensity, lats, lons, cloud_mask, spatial_mask),
            green_points=self._green_pts(ndvi, lats, lons, cloud_mask, spatial_mask),
            tmax_celsius=weather.tmax_celsius, tmin_celsius=weather.tmin_celsius)

    @staticmethod
    def _fallback_lst(district, weather):
        return (weather.tmax_celsius * 0.65 + weather.tmin_celsius * 0.35) + 3.5 + district.uhi_modifier

    def _heat_pts(self, lst, intensity, lats, lons, cloud_mask, spatial_mask, sub=6):
        rows, cols = np.where(spatial_mask & ~cloud_mask)
        idx = np.arange(0, len(rows), sub)
        pts = []
        for r, c in zip(rows[idx], cols[idx]):
            v = float(lst[r, c])
            if np.isnan(v): continue
            pts.append(HeatPoint(lat=round(float(lats[r]),5), lon=round(float(lons[c]),5),
                                 lst_celsius=round(v,1), ndvi=0.0, intensity=round(float(intensity[r,c]),3)))
        return pts

    def _green_pts(self, ndvi, lats, lons, cloud_mask, spatial_mask, sub=6):
        rows, cols = np.where(spatial_mask & ~cloud_mask)
        idx = np.arange(0, len(rows), sub)
        pts = []
        for r, c in zip(rows[idx], cols[idx]):
            v = float(ndvi[r, c])
            if np.isnan(v): continue
            pts.append(HeatPoint(lat=round(float(lats[r]),5), lon=round(float(lons[c]),5),
                                 lst_celsius=0.0, ndvi=round(v,3),
                                 intensity=round(float(np.clip((v+1)/2,0,1)),3)))
        return pts
