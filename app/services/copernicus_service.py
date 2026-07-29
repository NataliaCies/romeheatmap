"""CopernicusService — auth verification and scene discovery."""
from __future__ import annotations
from datetime import date, timedelta
from dataclasses import dataclass
from app.core.cache import cache_get, cache_set
from app.core.config import get_settings
from app.core.exceptions import CopernicusAuthError, SatelliteDataError
from app.core.logging import get_logger
from app.repositories.copernicus_auth import CopernicusTokenRepository
from app.repositories.satellite_search import SatelliteScene, SatelliteSearchRepository
from app.utils.timing import log_execution_time

logger = get_logger(__name__)
_STATUS_CACHE_TTL = 300
_SCENES_CACHE_TTL = 3_600


@dataclass
class CopernicusStatus:
    configured: bool; authenticated: bool; catalogue_reachable: bool
    latest_scene_date: date | None; latest_scene_cloud_pct: float | None
    scenes_last_30_days: int; error_message: str | None


@dataclass
class SceneSummary:
    scene_id: str; product_name: str; sensing_date: str
    cloud_cover_pct: float; orbit_direction: str; online: bool
    size_mb: float; quicklook_url: str; days_ago: int


class CopernicusService:
    def __init__(self, auth=None, search=None):
        self._auth = auth or CopernicusTokenRepository()
        self._search = search or SatelliteSearchRepository(self._auth)
        self._settings = get_settings()

    @log_execution_time
    async def get_status(self) -> CopernicusStatus:
        cache_key = "copernicus:status"
        cached = await cache_get(cache_key)
        if cached: return CopernicusStatus(**cached)
        status = await self._run_status_checks()
        await cache_set(cache_key, status.__dict__, _STATUS_CACHE_TTL)
        return status

    @log_execution_time
    async def list_scenes(self, days: int = 30, max_cloud_pct: float | None = None) -> list[SceneSummary]:
        cache_key = f"copernicus:scene_list:{days}"
        cached = await cache_get(cache_key)
        if cached:
            summaries = [SceneSummary(**s) for s in cached]
            if max_cloud_pct is not None:
                summaries = [s for s in summaries if s.cloud_cover_pct <= max_cloud_pct]
            return summaries
        scenes = await self._search.list_recent_scenes(bbox=self._settings.rome_bbox, days=days)
        today = date.today()
        summaries = [SceneSummary(
            scene_id=s.scene_id, product_name=s.product_name, sensing_date=str(s.sensing_date),
            cloud_cover_pct=s.cloud_cover_pct, orbit_direction=s.orbit_direction,
            online=s.online, size_mb=s.size_mb, quicklook_url=s.quicklook_url,
            days_ago=(today - s.sensing_date).days) for s in scenes]
        await cache_set(cache_key, [s.__dict__ for s in summaries], _SCENES_CACHE_TTL)
        if max_cloud_pct is not None:
            summaries = [s for s in summaries if s.cloud_cover_pct <= max_cloud_pct]
        return summaries

    async def find_best_scene_for_date(self, target_date: date, max_cloud_pct=None) -> SatelliteScene:
        return await self._search.find_best_scene(
            target_date=target_date, bbox=self._settings.rome_bbox, max_cloud_pct=max_cloud_pct)

    async def _run_status_checks(self) -> CopernicusStatus:
        if not self._auth.is_configured():
            return CopernicusStatus(configured=False, authenticated=False, catalogue_reachable=False,
                latest_scene_date=None, latest_scene_cloud_pct=None, scenes_last_30_days=0,
                error_message="Copernicus credentials not configured. Register free at https://dataspace.copernicus.eu")
        try:
            await self._auth.get_token()
        except CopernicusAuthError as exc:
            return CopernicusStatus(configured=True, authenticated=False, catalogue_reachable=False,
                latest_scene_date=None, latest_scene_cloud_pct=None, scenes_last_30_days=0, error_message=str(exc))
        if not await self._search.check_connectivity():
            return CopernicusStatus(configured=True, authenticated=True, catalogue_reachable=False,
                latest_scene_date=None, latest_scene_cloud_pct=None, scenes_last_30_days=0,
                error_message="Copernicus catalogue not reachable")
        try:
            scenes = await self._search.list_recent_scenes(bbox=self._settings.rome_bbox, days=30)
            latest = scenes[0] if scenes else None
        except SatelliteDataError:
            scenes = []; latest = None
        return CopernicusStatus(configured=True, authenticated=True, catalogue_reachable=True,
            latest_scene_date=latest.sensing_date if latest else None,
            latest_scene_cloud_pct=latest.cloud_cover_pct if latest else None,
            scenes_last_30_days=len(scenes), error_message=None)
