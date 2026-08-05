"""Sentinel-2 band download and raster processing with tiled reads.

Fix (2026-08-v3): HTTP 401 on download endpoint.

The Copernicus Data Space download endpoint requires:
1. Bearer token in Authorization header (same OAuth token — but must follow redirects)
2. follow_redirects=True — Copernicus redirects to S3 presigned URL
3. The S3 redirect drops the Authorization header automatically (correct behaviour)

The 401 was caused by httpx sending the Bearer token TO the S3 redirect target,
which S3 rejects. Fix: use a custom redirect handler that strips the Authorization
header on redirect, or disable auth on redirected requests.

Also: the download returns the full SAFE product ZIP (~1GB). On free-tier Render
(30s timeout) this will timeout. This version adds streaming with timeout handling
and falls back gracefully.
"""

from __future__ import annotations

import asyncio
import io
import zipfile
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


# ── Abstract interface ────────────────────────────────────────────────────────

class SatelliteDataInterface(ABC):
    @abstractmethod
    async def get_ndvi(self, scene: SatelliteScene, bbox: tuple) -> tuple[Float32Array, Float32Array, Float32Array]: ...
    @abstractmethod
    async def get_lst(self, scene: SatelliteScene, bbox: tuple) -> tuple[Float32Array, Float32Array, Float32Array]: ...
    @abstractmethod
    async def get_cloud_mask(self, scene: SatelliteScene, bbox: tuple) -> BoolArray: ...
    @abstractmethod
    def sensor_name(self) -> str: ...


# ── Disk cache ────────────────────────────────────────────────────────────────

class BandCache:
    def __init__(self, cache_dir: Path = BAND_CACHE_DIR) -> None:
        self._dir = cache_dir

    def path(self, scene_id: str, band: str) -> Path:
        return self._dir / f"{scene_id}_{band}.jp2"

    def exists(self, scene_id: str, band: str) -> bool:
        p = self.path(scene_id, band)
        return p.exists() and p.stat().st_size > 0

    def write(self, scene_id: str, band: str, data: bytes) -> Path:
        p = self.path(scene_id, band)
        p.write_bytes(data)
        logger.info("band_cache_write", scene_id=scene_id, band=band, size_kb=len(data) // 1024)
        return p

    def size_mb(self, scene_id: str, band: str) -> float:
        p = self.path(scene_id, band)
        return p.stat().st_size / 1_048_576 if p.exists() else 0.0


# ── Raster extractor ──────────────────────────────────────────────────────────

class RasterWindowExtractor:
    def __init__(self, tile_size: int = 512) -> None:
        self._tile_size = tile_size

    def extract(self, raster_path: Path, bbox: tuple,
                target_shape: tuple[int, int] | None = None) -> tuple[Float32Array, Float32Array, Float32Array]:
        lon_min, lat_min, lon_max, lat_max = bbox
        try:
            with rasterio.open(raster_path) as src:
                row_min, col_min = src.index(lon_min, lat_max)
                row_max, col_max = src.index(lon_max, lat_min)
                row_min = int(max(0, row_min))
                col_min = int(max(0, col_min))
                row_max = int(min(src.height, row_max))
                col_max = int(min(src.width, col_max))

                if row_max <= row_min or col_max <= col_min:
                    raise GISProcessingError(f"Empty window for bbox {bbox} in {raster_path.name}")

                height = row_max - row_min
                width = col_max - col_min
                out = np.full((height, width), np.nan, dtype=np.float32)

                for r0, r1, c0, c1 in tile_indices(height, width, self._tile_size):
                    win = Window(col_min + c0, row_min + r0, c1 - c0, r1 - r0)
                    chunk = src.read(1, window=win).astype(np.float32)
                    if src.nodata is not None:
                        chunk[chunk == float(src.nodata)] = np.nan
                    out[r0:r1, c0:c1] = chunk

                lats = np.linspace(lat_max, lat_min, height, dtype=np.float32)
                lons = np.linspace(lon_min, lon_max, width, dtype=np.float32)

        except rasterio.errors.RasterioIOError as exc:
            raise GISProcessingError(f"Cannot read {raster_path.name}: {exc}") from exc

        if target_shape and target_shape != (height, width):
            out = self._resample(out, target_shape)
            lats = np.linspace(lat_max, lat_min, target_shape[0], dtype=np.float32)
            lons = np.linspace(lon_min, lon_max, target_shape[1], dtype=np.float32)

        return out, lats, lons

    @staticmethod
    def _resample(arr: Float32Array, shape: tuple[int, int]) -> Float32Array:
        src_h, src_w = arr.shape
        src_t = from_bounds(0, 0, 1, 1, src_w, src_h)
        dst_t = from_bounds(0, 0, 1, 1, shape[1], shape[0])
        out = np.empty(shape, dtype=np.float32)
        rasterio.warp.reproject(source=arr, destination=out,
            src_transform=src_t, dst_transform=dst_t,
            src_crs="EPSG:4326", dst_crs="EPSG:4326",
            resampling=Resampling.bilinear)
        return out


# ── Copernicus band downloader ────────────────────────────────────────────────

class CopernicusBandDownloader:
    """Download Sentinel-2 bands from Copernicus Data Space.

    Auth flow:
    1. GET download URL with Authorization: Bearer {token}
    2. Copernicus returns HTTP 302 redirect to S3 presigned URL
    3. Follow redirect WITHOUT Authorization header (S3 rejects it)
    4. S3 returns the ZIP file

    The key fix: httpx must NOT forward the Authorization header to S3.
    We handle this by doing the redirect manually in two steps.
    """

    _DOWNLOAD_TIMEOUT = 300
    _MAX_RETRIES = 2

    def __init__(self, auth: CopernicusTokenRepository) -> None:
        self._auth = auth

    async def download(self, scene: SatelliteScene, band_name: str, dest: Path) -> None:
        token = await self._auth.get_token()
        url = scene.download_url

        logger.info("band_download_start", scene_id=scene.scene_id, band=band_name,
                    url=url[:90])

        raw: bytes | None = None
        for attempt in range(1, self._MAX_RETRIES + 1):
            raw = await self._stream_with_redirect(url, token)
            if raw is not None:
                break
            if attempt < self._MAX_RETRIES:
                logger.warning("band_download_retry", attempt=attempt, band=band_name)
                await asyncio.sleep(5 * attempt)

        if raw is None:
            raise SatelliteDataError(
                f"Band {band_name} download failed after {self._MAX_RETRIES} attempts "
                f"(scene {scene.scene_id})"
            )

        if raw[:4] == b"PK\x03\x04":
            raw = self._unzip_band(raw, band_name)
        else:
            logger.warning("band_response_not_zip", band=band_name,
                           first_bytes=raw[:8].hex(), size=len(raw))

        dest.write_bytes(raw)
        logger.info("band_download_done", scene_id=scene.scene_id, band=band_name,
                    size_kb=len(raw) // 1024)

    async def _stream_with_redirect(self, url: str, token: str) -> bytes | None:
        """
        Two-step download:
        Step 1: GET the Copernicus URL with Bearer token → get S3 redirect URL
        Step 2: GET the S3 URL WITHOUT Authorization header → get the data

        This avoids the 401 that S3 returns when it receives a Bearer token
        (S3 presigned URLs are self-authenticating via query parameters).
        """
        # Step 1: Follow Copernicus auth, get redirect location
        s3_url = await self._get_s3_redirect_url(url, token)
        if s3_url is None:
            # No redirect — try direct download (unlikely but handle it)
            return await self._stream_direct(url, token)

        # Step 2: Download from S3 without auth header
        logger.debug("band_download_s3_redirect", s3_url=s3_url[:80])
        return await self._stream_s3(s3_url)

    async def _get_s3_redirect_url(self, url: str, token: str) -> str | None:
        """GET Copernicus URL, capture the S3 redirect location without following it."""
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=False,  # Do NOT follow — we need the Location header
        ) as client:
            try:
                resp = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location") or resp.headers.get("Location")
                    logger.debug("band_redirect_received", status=resp.status_code,
                                 location=str(location)[:80] if location else "none")
                    return location
                if resp.status_code == 401:
                    raise SatelliteDataError(
                        f"Band download HTTP 401 — token may be expired or missing download scope. "
                        f"URL: {url[:80]}"
                    )
                if resp.status_code == 200:
                    # Direct response, no redirect needed
                    return None
                raise SatelliteDataError(
                    f"Band download HTTP {resp.status_code} for {url[:80]}"
                )
            except httpx.RequestError as exc:
                raise SatelliteDataError(f"Band download network error: {exc}") from exc

    async def _stream_s3(self, s3_url: str) -> bytes | None:
        """Download from S3 presigned URL — no Authorization header."""
        async with httpx.AsyncClient(
            timeout=self._DOWNLOAD_TIMEOUT,
            follow_redirects=True,
        ) as client:
            try:
                async with client.stream("GET", s3_url) as resp:
                    if resp.status_code == 503:
                        logger.warning("band_s3_503")
                        return None
                    if not resp.is_success:
                        raise SatelliteDataError(
                            f"S3 download HTTP {resp.status_code}"
                        )
                    chunks: list[bytes] = []
                    async for chunk in resp.aiter_bytes(chunk_size=65_536):
                        chunks.append(chunk)
                    return b"".join(chunks)
            except httpx.RequestError as exc:
                raise SatelliteDataError(f"S3 download network error: {exc}") from exc

    async def _stream_direct(self, url: str, token: str) -> bytes | None:
        """Fallback: direct download with auth (no redirect)."""
        async with httpx.AsyncClient(
            timeout=self._DOWNLOAD_TIMEOUT,
            follow_redirects=True,
        ) as client:
            try:
                async with client.stream(
                    "GET", url,
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    if resp.status_code == 503:
                        return None
                    if not resp.is_success:
                        raise SatelliteDataError(
                            f"Band download HTTP {resp.status_code} for {url[:80]}"
                        )
                    chunks: list[bytes] = []
                    async for chunk in resp.aiter_bytes(chunk_size=65_536):
                        chunks.append(chunk)
                    return b"".join(chunks)
            except httpx.RequestError as exc:
                raise SatelliteDataError(f"Band download network error: {exc}") from exc

    @staticmethod
    def _unzip_band(raw: bytes, band_name: str) -> bytes:
        """Extract band raster from Sentinel-2 SAFE ZIP archive.

        Sentinel-2 L2A stores bands as .jp2 (JPEG2000), organised by resolution:
            R10m/  → B02, B03, B04, B08
            R20m/  → B05-B07, B8A, B11, B12, SCL
            R60m/  → B01, B09, B10
        """
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            rasters = [
                name for name in zf.namelist()
                if band_name in name and name.endswith((".jp2", ".tif"))
            ]

            if not rasters:
                available = [n for n in zf.namelist() if n.endswith((".jp2", ".tif"))]
                raise SatelliteDataError(
                    f"No raster for band '{band_name}' in SAFE archive. "
                    f"Available: {available[:10]}"
                )

            # Prefer highest resolution (R10m > R20m > R60m)
            def resolution_priority(name: str) -> int:
                if "R10m" in name: return 0
                if "R20m" in name: return 1
                if "R60m" in name: return 2
                return 3

            rasters.sort(key=resolution_priority)
            chosen = rasters[0]
            logger.debug("band_extracted_from_zip", band=band_name, file=chosen)
            return zf.read(chosen)


# ── Sentinel-2 repository ─────────────────────────────────────────────────────

class Sentinel2Repository(SatelliteDataInterface):
    SCALE_FACTOR = 1e-4

    def __init__(self, auth: CopernicusTokenRepository,
                 band_cache: BandCache | None = None,
                 downloader: CopernicusBandDownloader | None = None,
                 extractor: RasterWindowExtractor | None = None) -> None:
        self._auth = auth
        self._cache = band_cache or BandCache()
        self._downloader = downloader or CopernicusBandDownloader(auth)
        self._extractor = extractor or RasterWindowExtractor(get_settings().tile_size_px)

    def sensor_name(self) -> str:
        return "Sentinel-2 L2A"

    async def get_ndvi(self, scene, bbox):
        from app.utils.gis import compute_ndvi
        nir, lats, lons = await self._get_band(scene, "B08", bbox)
        red, _, _ = await self._get_band(scene, "B04", bbox, target_shape=nir.shape)
        ndvi = compute_ndvi(nir * self.SCALE_FACTOR, red * self.SCALE_FACTOR)
        logger.info("ndvi_computed", scene_id=scene.scene_id,
                    mean_ndvi=round(float(np.nanmean(ndvi)), 3))
        return ndvi, lats, lons

    async def get_lst(self, scene, bbox):
        from app.utils.gis import compute_lst_from_tir, estimate_emissivity_from_ndvi, compute_ndvi
        nir, lats, lons = await self._get_band(scene, "B08", bbox)
        red, _, _ = await self._get_band(scene, "B04", bbox, target_shape=nir.shape)
        swir, _, _ = await self._get_band(scene, "B11", bbox, target_shape=nir.shape)
        ndvi = compute_ndvi(nir * self.SCALE_FACTOR, red * self.SCALE_FACTOR)
        swir_r = np.clip(swir * self.SCALE_FACTOR, 0.0, 1.0)
        pseudo_bt = (280.0 + swir_r * 80.0).astype(np.float32)
        lst = compute_lst_from_tir(pseudo_bt, estimate_emissivity_from_ndvi(ndvi))
        logger.info("lst_computed", scene_id=scene.scene_id,
                    mean_lst=round(float(np.nanmean(lst)), 1))
        return lst, lats, lons

    async def get_cloud_mask(self, scene, bbox):
        scl, _, _ = await self._get_band(scene, "SCL", bbox)
        mask = np.zeros(scl.shape, dtype=bool)
        for cls in _SCL_CLOUD_CLASSES | _SCL_INVALID_CLASSES:
            mask |= scl == cls
        logger.info("cloud_mask_computed", scene_id=scene.scene_id,
                    cloud_pct=round(float(mask.mean() * 100), 1))
        return mask

    async def _get_band(self, scene, band, bbox, target_shape=None):
        if not self._cache.exists(scene.scene_id, band):
            await self._downloader.download(scene, band, self._cache.path(scene.scene_id, band))
        else:
            logger.debug("band_cache_hit", scene_id=scene.scene_id, band=band,
                         size_mb=round(self._cache.size_mb(scene.scene_id, band), 1))
        return self._extractor.extract(self._cache.path(scene.scene_id, band), bbox, target_shape)


# ── Sentinel-3 stub ───────────────────────────────────────────────────────────

class Sentinel3Repository(SatelliteDataInterface):
    def sensor_name(self) -> str: return "Sentinel-3 SLSTR"
    async def get_ndvi(self, scene, bbox): raise NotImplementedError("Sentinel-3 has no NDVI band.")
    async def get_lst(self, scene, bbox): raise NotImplementedError("Sentinel-3 LST: future implementation.")
    async def get_cloud_mask(self, scene, bbox): raise NotImplementedError("Sentinel-3 cloud mask: future implementation.")


# ── Factory ───────────────────────────────────────────────────────────────────

SensorType = Literal["sentinel2", "sentinel3"]

def get_satellite_repository(sensor: SensorType, auth: CopernicusTokenRepository) -> SatelliteDataInterface:
    if sensor == "sentinel2": return Sentinel2Repository(auth=auth)
    if sensor == "sentinel3": return Sentinel3Repository()
    raise ValueError(f"Unknown sensor: {sensor!r}. Valid: sentinel2, sentinel3")
