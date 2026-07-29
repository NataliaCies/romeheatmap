"""LST processing pipeline: satellite bands → calibrated LST → UHI → heatmap."""
from __future__ import annotations
import asyncio, time
from dataclasses import dataclass
import numpy as np
from numpy.typing import NDArray
from app.core.logging import get_logger
from app.repositories.satellite_download import SatelliteDataInterface
from app.repositories.satellite_search import SatelliteScene
from app.repositories.weather import DailyWeather
from app.utils.districts import District
from app.utils.gis import apply_cloud_mask, normalise_array

logger = get_logger(__name__)
Float32Array = NDArray[np.float32]
BoolArray = NDArray[np.bool_]


class LSTCalibrator:
    """Bias-correct satellite LST against Open-Meteo air temperature."""
    ALPHA = 1.08; BETA = 3.5

    def calibrate(self, raw_lst: Float32Array, weather: DailyWeather) -> Float32Array:
        air_day = weather.tmax_celsius * 0.65 + weather.tmin_celsius * 0.35
        expected_mean = air_day * self.ALPHA + self.BETA
        valid = raw_lst[~np.isnan(raw_lst)]
        if len(valid) == 0:
            logger.warning("lst_calibration_skipped", reason="all pixels NaN"); return raw_lst
        bias = expected_mean - float(np.mean(valid))
        calibrated = np.where(np.isnan(raw_lst), np.nan, raw_lst + bias).astype(np.float32)
        logger.info("lst_calibration_applied", bias_c=round(bias, 2),
                    expected_mean_c=round(expected_mean, 2), valid_pixels=len(valid))
        return calibrated


class UHIModifier:
    """Apply per-district Urban Heat Island modifiers to the LST grid."""

    def apply(self, lst: Float32Array, district: District,
              lats: Float32Array, lons: Float32Array) -> Float32Array:
        lon_min, lat_min, lon_max, lat_max = district.bbox
        modifier = np.zeros_like(lst)
        rows = np.where((lats >= lat_min) & (lats <= lat_max))[0]
        cols = np.where((lons >= lon_min) & (lons <= lon_max))[0]
        if rows.size > 0 and cols.size > 0:
            modifier[np.ix_(rows, cols)] = district.uhi_modifier
        return np.where(np.isnan(lst), np.nan, lst + modifier).astype(np.float32)

    def apply_all_districts(self, lst: Float32Array,
                            lats: Float32Array, lons: Float32Array) -> Float32Array:
        from app.utils.districts import DISTRICT_REGISTRY
        modifier_grid = np.zeros_like(lst)
        for district in DISTRICT_REGISTRY.values():
            lon_min, lat_min, lon_max, lat_max = district.bbox
            rows = np.where((lats >= lat_min) & (lats <= lat_max))[0]
            cols = np.where((lons >= lon_min) & (lons <= lon_max))[0]
            if rows.size > 0 and cols.size > 0:
                modifier_grid[np.ix_(rows, cols)] += district.uhi_modifier
        return np.where(np.isnan(lst), np.nan, lst + modifier_grid).astype(np.float32)


@dataclass
class PipelineResult:
    lst_celsius: Float32Array; ndvi: Float32Array; intensity: Float32Array
    lats: Float32Array; lons: Float32Array; cloud_mask: BoolArray
    scene_id: str; sensor: str; processing_time_s: float
    cloud_pct: float; mean_lst: float; mean_ndvi: float


class LSTProcessingPipeline:
    """Orchestrate the full LST + NDVI processing chain: fetch → mask → calibrate → UHI → normalise."""

    def __init__(self, satellite: SatelliteDataInterface,
                 calibrator: LSTCalibrator | None = None,
                 uhi: UHIModifier | None = None) -> None:
        self._satellite = satellite
        self._calibrator = calibrator or LSTCalibrator()
        self._uhi = uhi or UHIModifier()

    async def run(self, scene: SatelliteScene, bbox: tuple,
                  weather: DailyWeather) -> PipelineResult:
        t0 = time.perf_counter()
        logger.info("pipeline_start", scene_id=scene.scene_id, sensor=self._satellite.sensor_name())

        # Parallel fetch — NDVI, LST proxy, cloud mask
        (ndvi_raw, lats, lons), (lst_raw, _, _), cloud_mask = await asyncio.gather(
            self._satellite.get_ndvi(scene, bbox),
            self._satellite.get_lst(scene, bbox),
            self._satellite.get_cloud_mask(scene, bbox),
        )

        lst_clean = apply_cloud_mask(lst_raw, cloud_mask)
        ndvi_clean = apply_cloud_mask(ndvi_raw, cloud_mask)
        lst_calibrated = self._calibrator.calibrate(lst_clean, weather)
        lst_uhi = self._uhi.apply_all_districts(lst_calibrated, lats, lons)
        intensity = normalise_array(lst_uhi)

        valid_lst = lst_uhi[~np.isnan(lst_uhi)]
        valid_ndvi = ndvi_clean[~np.isnan(ndvi_clean)]
        cloud_pct = float(cloud_mask.mean() * 100)
        elapsed = time.perf_counter() - t0

        logger.info("pipeline_done", scene_id=scene.scene_id, elapsed_s=round(elapsed, 2),
                    mean_lst=round(float(np.mean(valid_lst)), 1) if len(valid_lst) else None,
                    cloud_pct=round(cloud_pct, 1))

        return PipelineResult(
            lst_celsius=lst_uhi, ndvi=ndvi_clean, intensity=intensity,
            lats=lats, lons=lons, cloud_mask=cloud_mask,
            scene_id=scene.scene_id, sensor=self._satellite.sensor_name(),
            processing_time_s=round(elapsed, 2), cloud_pct=round(cloud_pct, 1),
            mean_lst=round(float(np.mean(valid_lst)), 1) if len(valid_lst) else 0.0,
            mean_ndvi=round(float(np.mean(valid_ndvi)), 3) if len(valid_ndvi) else 0.0,
        )
