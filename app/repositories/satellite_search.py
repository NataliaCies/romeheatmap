"""Sentinel-2 scene search via Copernicus OData API."""
from __future__ import annotations
from datetime import date, timedelta
from dataclasses import dataclass, field
import httpx
from app.core.cache import cache_get, cache_set
from app.core.config import get_settings
from app.core.exceptions import NoSatelliteSceneError, SatelliteDataError
from app.core.logging import get_logger
from app.repositories.copernicus_auth import CopernicusTokenRepository

logger = get_logger(__name__)
_SCENE_CACHE_TTL = 43_200


@dataclass
class SatelliteScene:
    scene_id: str; product_name: str; sensing_date: date
    cloud_cover_pct: float; download_url: str
    bbox: tuple[float, float, float, float]
    orbit_number: int = 0; orbit_direction: str = "DESCENDING"
    processing_baseline: str = ""; size_mb: float = 0.0
    quicklook_url: str = ""; online: bool = True

    def to_dict(self) -> dict:
        return {"scene_id": self.scene_id, "product_name": self.product_name,
                "sensing_date": str(self.sensing_date), "cloud_cover_pct": self.cloud_cover_pct,
                "download_url": self.download_url, "bbox": list(self.bbox),
                "orbit_number": self.orbit_number, "orbit_direction": self.orbit_direction,
                "processing_baseline": self.processing_baseline, "size_mb": self.size_mb,
                "quicklook_url": self.quicklook_url, "online": self.online}

    @classmethod
    def from_dict(cls, d: dict) -> "SatelliteScene":
        return cls(scene_id=d["scene_id"], product_name=d["product_name"],
                   sensing_date=date.fromisoformat(d["sensing_date"]),
                   cloud_cover_pct=d["cloud_cover_pct"], download_url=d["download_url"],
                   bbox=tuple(d["bbox"]), orbit_number=d.get("orbit_number", 0),
                   orbit_direction=d.get("orbit_direction", "DESCENDING"),
                   processing_baseline=d.get("processing_baseline", ""),
                   size_mb=d.get("size_mb", 0.0), quicklook_url=d.get("quicklook_url", ""),
                   online=d.get("online", True))


class SatelliteSearchRepository:
    COLLECTION = "SENTINEL-2"; PRODUCT_TYPE = "S2MSI2A"
    MAX_RESULTS_PER_PAGE = 20; MAX_PAGES = 3

    def __init__(self, auth: CopernicusTokenRepository) -> None:
        self._auth = auth; self._settings = get_settings()

    async def find_best_scene(self, target_date: date,
                               bbox: tuple[float, float, float, float],
                               max_cloud_pct: int | None = None,
                               search_window_days: int = 10,
                               require_online: bool = True) -> SatelliteScene:
        if max_cloud_pct is None:
            max_cloud_pct = self._settings.max_cloud_cover_pct
        start = target_date - timedelta(days=search_window_days)
        end = target_date + timedelta(days=1)
        logger.info("satellite_search_start", target_date=str(target_date), max_cloud_pct=max_cloud_pct)
        scenes = await self._search_with_cache(bbox, start, end)
        candidates = [s for s in scenes
                      if s.cloud_cover_pct <= max_cloud_pct
                      and s.orbit_direction == "DESCENDING"
                      and (not require_online or s.online)]
        if not candidates:
            raise NoSatelliteSceneError(
                f"No Sentinel-2 scene with ≤{max_cloud_pct}% cloud cover "
                f"found between {start} and {end} over Rome. "
                f"({len(scenes)} total scenes, all filtered out)")
        best = min(candidates, key=lambda s: abs((s.sensing_date - target_date).days))
        logger.info("satellite_scene_selected", scene_id=best.scene_id,
                    sensing_date=str(best.sensing_date), cloud_pct=best.cloud_cover_pct)
        return best

    async def list_recent_scenes(self, bbox: tuple, days: int = 30) -> list[SatelliteScene]:
        end = date.today(); start = end - timedelta(days=days)
        scenes = await self._search_with_cache(bbox, start, end)
        return sorted(scenes, key=lambda s: s.sensing_date, reverse=True)

    async def check_connectivity(self) -> bool:
        try:
            await self._auth.get_token()
            bbox = self._settings.rome_bbox
            end = date.today(); start = end - timedelta(days=3)
            await self._query_catalogue(bbox, start, end, top=1)
            return True
        except Exception as exc:
            logger.warning("copernicus_connectivity_failed", error=str(exc)); return False

    async def _search_with_cache(self, bbox, start, end) -> list[SatelliteScene]:
        cache_key = f"satellite:scenes:{start}:{end}:{hash(bbox)}"
        cached = await cache_get(cache_key)
        if cached:
            return [SatelliteScene.from_dict(s) for s in cached]
        scenes = await self._query_all_pages(bbox, start, end)
        await cache_set(cache_key, [s.to_dict() for s in scenes], _SCENE_CACHE_TTL)
        return scenes

    async def _query_all_pages(self, bbox, start, end) -> list[SatelliteScene]:
        all_scenes = []
        for page in range(self.MAX_PAGES):
            page_scenes = await self._query_catalogue(bbox, start, end, skip=page * self.MAX_RESULTS_PER_PAGE)
            all_scenes.extend(page_scenes)
            if len(page_scenes) < self.MAX_RESULTS_PER_PAGE:
                break
        logger.info("satellite_search_complete", total=len(all_scenes), start=str(start), end=str(end))
        return all_scenes

    async def _query_catalogue(self, bbox, start, end, top=None, skip=0) -> list[SatelliteScene]:
        token = await self._auth.get_token()
        lon_min, lat_min, lon_max, lat_max = bbox
        page_size = top or self.MAX_RESULTS_PER_PAGE
        footprint = (f"POLYGON(({lon_min} {lat_min},{lon_max} {lat_min},"
                     f"{lon_max} {lat_max},{lon_min} {lat_max},{lon_min} {lat_min}))")
        odata_filter = (
            f"Collection/Name eq '{self.COLLECTION}' and "
            f"Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' and "
            f"att/OData.CSC.StringAttribute/Value eq '{self.PRODUCT_TYPE}') and "
            f"OData.CSC.Intersects(area=geography'SRID=4326;{footprint}') and "
            f"ContentDate/Start gt {start.isoformat()}T00:00:00.000Z and "
            f"ContentDate/Start lt {end.isoformat()}T23:59:59.000Z")
        params = {"$filter": odata_filter, "$orderby": "ContentDate/Start desc",
                  "$top": str(page_size), "$skip": str(skip), "$expand": "Attributes"}
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                response = await client.get(f"{self._settings.copernicus_search_url}/Products",
                    params=params, headers={"Authorization": f"Bearer {token}"})
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise SatelliteDataError(f"Catalogue query failed ({exc.response.status_code})") from exc
        return self._parse_results(response.json())

    def _parse_results(self, data: dict) -> list[SatelliteScene]:
        scenes = []
        for item in data.get("value", []):
            try:
                attrs = {a["Name"]: a.get("Value") for a in item.get("Attributes", [])}
                scenes.append(SatelliteScene(
                    scene_id=item["Id"], product_name=item["Name"],
                    sensing_date=date.fromisoformat(item["ContentDate"]["Start"][:10]),
                    cloud_cover_pct=float(attrs.get("cloudCover") or 100.0),
                    download_url=f"{self._settings.copernicus_download_url}/{item['Id']}/$value",
                    bbox=self._settings.rome_bbox,
                    orbit_number=int(attrs.get("relativeOrbitNumber") or 0),
                    orbit_direction=str(attrs.get("orbitDirection") or "DESCENDING"),
                    processing_baseline=str(attrs.get("processingBaseline") or ""),
                    size_mb=round((item.get("ContentLength") or 0) / 1_048_576, 1),
                    online=bool(item.get("Online", True))))
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("scene_parse_error", item_id=item.get("Id","?"), error=str(exc))
        return scenes
