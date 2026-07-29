"""Sentinel-2 band download and raster processing with tiled reads."""
from __future__ import annotations
import io, zipfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal
import httpx
import numpy as np
import rasterio
import rasterio.warp
from numpy.typing import NDArray
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.windows import Window
from app.core.config import get_settings
from app.core.exceptions import GISProcessingError, SatelliteDataError
from app.core.logging import get_logger
from app.repositories.copernicus_auth import CopernicusTokenRepository
from app.repositories.satellite_search import SatelliteScene
from app.utils.gis import tile_indices

logger = get_logger(__name__)
Float32Array = NDArray[np.float32]
BoolArray = NDArray[np.bool_]
_SCL_CLOUD_CLASSES = {3, 8, 9, 10}
_SCL_INVALID_CLASSES = {0, 1}
BAND_CACHE_DIR = Path("/tmp/rome_satellite_cache")
BAND_CACHE_DIR.mkdir(parents=True, exist_ok=True)


class SatelliteDataInterface(ABC):
    @abstractmethod
    async def get_ndvi(self, scene: SatelliteScene, bbox: tuple) -> tuple[Float32Array, Float32Array, Float32Array]: ...
    @abstractmethod
    async def get_lst(self, scene: SatelliteScene, bbox: tuple) -> tuple[Float32Array, Float32Array, Float32Array]: ...
    @abstractmethod
    async def get_cloud_mask(self, scene: SatelliteScene, bbox: tuple) -> BoolArray: ...
    @abstractmethod
    def sensor_name(self) -> str: ...


class BandCache:
    def __init__(self, cache_dir: Path = BAND_CACHE_DIR) -> None: self._dir = cache_dir
    def path(self, scene_id: str, band: str) -> Path: return self._dir / f"{scene_id}_{band}.tif"
    def exists(self, scene_id: str, band: str) -> bool:
        p = self.path(scene_id, band); return p.exists() and p.stat().st_size > 0
    def write(self, scene_id: str, band: str, data: bytes) -> Path:
        p = self.path(scene_id, band); p.write_bytes(data)
        logger.info("band_cache_write", scene_id=scene_id, band=band, size_kb=len(data)//1024); return p
    def size_mb(self, scene_id: str, band: str) -> float:
        p = self.path(scene_id, band); return p.stat().st_size / 1_048_576 if p.exists() else 0.0


class RasterWindowExtractor:
    def __init__(self, tile_size: int = 512) -> None: self._tile_size = tile_size

    def extract(self, tif_path: Path, bbox: tuple,
                target_shape: tuple[int,int] | None = None) -> tuple[Float32Array, Float32Array, Float32Array]:
        lon_min, lat_min, lon_max, lat_max = bbox
        try:
            with rasterio.open(tif_path) as src:
                row_min, col_min = src.index(lon_min, lat_max)
                row_max, col_max = src.index(lon_max, lat_min)
                row_min, col_min = int(max(0, row_min)), int(max(0, col_min))
                row_max, col_max = int(min(src.height, row_max)), int(min(src.width, col_max))
                if row_max <= row_min or col_max <= col_min:
                    raise GISProcessingError(f"Empty window for bbox {bbox} in {tif_path.name}")
                height, width = row_max - row_min, col_max - col_min
                out = np.full((height, width), np.nan, dtype=np.float32)
                for r0, r1, c0, c1 in tile_indices(height, width, self._tile_size):
                    win = Window(col_min + c0, row_min + r0, c1 - c0, r1 - r0)
                    chunk = src.read(1, window=win).astype(np.float32)
                    if src.nodata is not None: chunk[chunk == float(src.nodata)] = np.nan
                    out[r0:r1, c0:c1] = chunk
                lats = np.linspace(lat_max, lat_min, height, dtype=np.float32)
                lons = np.linspace(lon_min, lon_max, width, dtype=np.float32)
        except rasterio.errors.RasterioIOError as exc:
            raise GISProcessingError(f"Cannot read {tif_path.name}: {exc}") from exc
        if target_shape and target_shape != (height, width):
            out = self._resample(out, target_shape)
            lats = np.linspace(lat_max, lat_min, target_shape[0], dtype=np.float32)
            lons = np.linspace(lon_min, lon_max, target_shape[1], dtype=np.float32)
        return out, lats, lons

    @staticmethod
    def _resample(arr: Float32Array, shape: tuple[int,int]) -> Float32Array:
        src_h, src_w = arr.shape; dst_h, dst_w = shape
        src_t = from_bounds(0, 0, 1, 1, src_w, src_h)
        dst_t = from_bounds(0, 0, 1, 1, dst_w, dst_h)
        out = np.empty(shape, dtype=np.float32)
        rasterio.warp.reproject(source=arr, destination=out,
            src_transform=src_t, dst_transform=dst_t,
            src_crs="EPSG:4326", dst_crs="EPSG:4326", resampling=Resampling.bilinear)
        return out


class CopernicusBandDownloader:
    _DOWNLOAD_TIMEOUT = 180; _MAX_RETRIES = 2

    def __init__(self, auth: CopernicusTokenRepository) -> None: self._auth = auth

    async def download(self, scene: SatelliteScene, band_name: str, dest: Path) -> None:
        token = await self._auth.get_token()
        url = f"{scene.download_url}?band={band_name}"
        logger.info("band_download_start", scene_id=scene.scene_id, band=band_name)
        for attempt in range(1, self._MAX_RETRIES + 1):
            raw = await self._stream(url, token)
            if raw is not None: break
            if attempt < self._MAX_RETRIES:
                import asyncio; await asyncio.sleep(5 * attempt)
        else:
            raise SatelliteDataError(f"Band {band_name} download failed after {self._MAX_RETRIES} attempts")
        if raw[:4] == b"PK\x03\x04":
            raw = self._unzip_band(raw, band_name)
        dest.write_bytes(raw)
        logger.info("band_download_done", scene_id=scene.scene_id, band=band_name, size_kb=len(raw)//1024)

    async def _stream(self, url: str, token: str) -> bytes | None:
        async with httpx.AsyncClient(timeout=self._DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            try:
                async with client.stream("GET", url, headers={"Authorization": f"Bearer {token}"}) as resp:
                    if resp.status_code == 503: return None
                    if not resp.is_success:
                        raise SatelliteDataError(f"Band download HTTP {resp.status_code}")
                    chunks = []
                    async for chunk in resp.aiter_bytes(chunk_size=65_536): chunks.append(chunk)
                    return b"".join(chunks)
            except httpx.RequestError as exc:
                raise SatelliteDataError(f"Band download network error: {exc}") from exc

    @staticmethod
    def _unzip_band(raw: bytes, band_name: str) -> bytes:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            tifs = [n for n in zf.namelist() if band_name in n and n.endswith(".tif")]
            if not tifs:
                raise SatelliteDataError(f"No .tif for band {band_name} in ZIP")
            return zf.read(tifs[0])


class Sentinel2Repository(SatelliteDataInterface):
    """Sentinel-2 L2A: NDVI (B08/B04), LST proxy (B11/SWIR), SCL cloud mask."""
    SCALE_FACTOR = 1e-4

    def __init__(self, auth: CopernicusTokenRepository,
                 band_cache: BandCache | None = None,
                 downloader: CopernicusBandDownloader | None = None,
                 extractor: RasterWindowExtractor | None = None) -> None:
        self._auth = auth
        self._cache = band_cache or BandCache()
        self._downloader = downloader or CopernicusBandDownloader(auth)
        self._extractor = extractor or RasterWindowExtractor(get_settings().tile_size_px)

    def sensor_name(self) -> str: return "Sentinel-2 L2A"

    async def get_ndvi(self, scene, bbox):
        from app.utils.gis import compute_ndvi
        nir, lats, lons = await self._get_band(scene, "B08", bbox)
        red, _, _ = await self._get_band(scene, "B04", bbox, target_shape=nir.shape)
        return compute_ndvi(nir * self.SCALE_FACTOR, red * self.SCALE_FACTOR), lats, lons

    async def get_lst(self, scene, bbox):
        from app.utils.gis import compute_lst_from_tir, estimate_emissivity_from_ndvi, compute_ndvi
        nir, lats, lons = await self._get_band(scene, "B08", bbox)
        red, _, _ = await self._get_band(scene, "B04", bbox, target_shape=nir.shape)
        swir, _, _ = await self._get_band(scene, "B11", bbox, target_shape=nir.shape)
        ndvi = compute_ndvi(nir * self.SCALE_FACTOR, red * self.SCALE_FACTOR)
        swir_r = np.clip(swir * self.SCALE_FACTOR, 0.0, 1.0)
        pseudo_bt = (280.0 + swir_r * 80.0).astype(np.float32)
        lst = compute_lst_from_tir(pseudo_bt, estimate_emissivity_from_ndvi(ndvi))
        return lst, lats, lons

    async def get_cloud_mask(self, scene, bbox):
        scl, _, _ = await self._get_band(scene, "SCL", bbox)
        mask = np.zeros(scl.shape, dtype=bool)
        for cls in _SCL_CLOUD_CLASSES | _SCL_INVALID_CLASSES: mask |= scl == cls
        logger.info("cloud_mask_computed", scene_id=scene.scene_id,
                    cloud_pct=round(float(mask.mean() * 100), 1))
        return mask

    async def _get_band(self, scene, band, bbox, target_shape=None):
        if not self._cache.exists(scene.scene_id, band):
            await self._downloader.download(scene, band, self._cache.path(scene.scene_id, band))
        return self._extractor.extract(self._cache.path(scene.scene_id, band), bbox, target_shape)


class Sentinel3Repository(SatelliteDataInterface):
    """Stub for Sentinel-3 SLSTR thermal LST — Stage 5+ implementation."""
    def sensor_name(self) -> str: return "Sentinel-3 SLSTR"
    async def get_ndvi(self, scene, bbox): raise NotImplementedError("Sentinel-3 has no NDVI. Use Sentinel-2.")
    async def get_lst(self, scene, bbox): raise NotImplementedError("Sentinel-3 LST: Stage 5+ pending.")
    async def get_cloud_mask(self, scene, bbox): raise NotImplementedError("Sentinel-3 cloud mask: Stage 5+ pending.")


SensorType = Literal["sentinel2", "sentinel3"]

def get_satellite_repository(sensor: SensorType, auth: CopernicusTokenRepository) -> SatelliteDataInterface:
    if sensor == "sentinel2": return Sentinel2Repository(auth=auth)
    if sensor == "sentinel3": return Sentinel3Repository()
    raise ValueError(f"Unknown sensor: {sensor!r}. Valid: sentinel2, sentinel3")
