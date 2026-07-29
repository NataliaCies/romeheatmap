"""Cache manager — key helpers, PipelineResultCache, warming, invalidation."""
from __future__ import annotations
import asyncio, io, time
from dataclasses import dataclass, field
from datetime import date, timedelta
import numpy as np
from app.core.cache import cache_get, cache_set, cache_delete_pattern
from app.core.logging import get_logger

logger = get_logger(__name__)


def overview_key(d: date) -> str: return f"climate:overview:{d}"
def all_districts_key(d: date) -> str: return f"climate:all_districts:{d}"
def district_key(district_id: str, d: date) -> str: return f"climate:district:{district_id}:{d}"
def weather_key(d: date) -> str: return f"climate:weather:{d}"
def timeseries_key(district_id: str, months: int) -> str: return f"climate:timeseries:{district_id}:{months}"


@dataclass
class CacheStats:
    hits: int = 0; misses: int = 0; sets: int = 0; errors: int = 0
    last_warm: str | None = None; warmed_dates: list[str] = field(default_factory=list)

    @property
    def hit_rate_pct(self) -> float:
        total = self.hits + self.misses
        return round(self.hits / total * 100, 1) if total > 0 else 0.0

    def to_dict(self) -> dict:
        return {"hits": self.hits, "misses": self.misses, "sets": self.sets,
                "errors": self.errors, "hit_rate_pct": self.hit_rate_pct,
                "last_warm": self.last_warm, "warmed_dates": self.warmed_dates}


_stats = CacheStats()
def get_cache_stats() -> CacheStats: return _stats


class PipelineResultCache:
    _KEY_PREFIX = "climate:pipeline:"; _TTL = 43_200

    @staticmethod
    def _array_to_hex(arr: np.ndarray) -> str:
        buf = io.BytesIO(); np.save(buf, arr); return buf.getvalue().hex()

    @staticmethod
    def _hex_to_array(h: str) -> np.ndarray:
        return np.load(io.BytesIO(bytes.fromhex(h)), allow_pickle=False)

    async def save(self, scene_id: str, result) -> bool:
        key = f"{self._KEY_PREFIX}{scene_id}"
        try:
            payload = {
                "lst_celsius": self._array_to_hex(result.lst_celsius),
                "ndvi": self._array_to_hex(result.ndvi),
                "intensity": self._array_to_hex(result.intensity),
                "lats": self._array_to_hex(result.lats),
                "lons": self._array_to_hex(result.lons),
                "cloud_mask": self._array_to_hex(result.cloud_mask.astype(np.uint8)),
                "scene_id": result.scene_id, "sensor": result.sensor,
                "processing_time_s": result.processing_time_s,
                "cloud_pct": result.cloud_pct, "mean_lst": result.mean_lst, "mean_ndvi": result.mean_ndvi,
            }
            await cache_set(key, payload, self._TTL)
            return True
        except Exception as exc:
            logger.warning("pipeline_result_cache_error", scene_id=scene_id, error=str(exc)); return False

    async def load(self, scene_id: str):
        from app.services.satellite.lst_pipeline import PipelineResult
        key = f"{self._KEY_PREFIX}{scene_id}"
        try:
            data = await cache_get(key)
            if data is None: return None
            return PipelineResult(
                lst_celsius=self._hex_to_array(data["lst_celsius"]),
                ndvi=self._hex_to_array(data["ndvi"]),
                intensity=self._hex_to_array(data["intensity"]),
                lats=self._hex_to_array(data["lats"]), lons=self._hex_to_array(data["lons"]),
                cloud_mask=self._hex_to_array(data["cloud_mask"]).astype(bool),
                scene_id=data["scene_id"], sensor=data["sensor"],
                processing_time_s=data["processing_time_s"],
                cloud_pct=data["cloud_pct"], mean_lst=data["mean_lst"], mean_ndvi=data["mean_ndvi"],
            )
        except Exception as exc:
            logger.warning("pipeline_result_load_error", scene_id=scene_id, error=str(exc)); return None


async def invalidate_date(target_date: date) -> int:
    patterns = [f"climate:overview:{target_date}", f"climate:all_districts:{target_date}",
                f"climate:weather:{target_date}", f"climate:district:*:{target_date}"]
    total = 0
    for p in patterns: total += await cache_delete_pattern(p)
    logger.info("cache_invalidated", date=str(target_date), keys_deleted=total)
    return total


async def warm_cache_for_date(target_date: date) -> bool:
    from app.services.climate_service import ClimateService
    logger.info("cache_warm_start", date=str(target_date))
    t0 = time.perf_counter()
    try:
        svc = ClimateService()
        await svc.get_overview(target_date)
        _stats.last_warm = f"{date.today().isoformat()} ({time.perf_counter()-t0:.1f}s)"
        if str(target_date) not in _stats.warmed_dates:
            _stats.warmed_dates.append(str(target_date))
        logger.info("cache_warm_done", date=str(target_date))
        return True
    except Exception as exc:
        logger.error("cache_warm_failed", date=str(target_date), error=str(exc)); return False


async def warm_upcoming_days(days_ahead: int = 2) -> None:
    today = date.today()
    for i in range(days_ahead + 1):
        await warm_cache_for_date(today + timedelta(days=i))
        await asyncio.sleep(1)
