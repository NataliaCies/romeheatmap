"""Sentinel-2 band download and raster processing with tiled reads.

Fix (2026-07): Two bugs corrected based on Copernicus Data Space API behaviour:

1. URL fix — the old code appended ?band=B08 to the download URL:
       url = f"{scene.download_url}?band={band_name}"   # ← 404 always
   Copernicus does not accept a band parameter. The $value endpoint returns
   the entire SAFE product as a ZIP. We now download the full product and
   extract the individual band from the archive.

2. JP2 format — Sentinel-2 L2A products store bands as .jp2 (JPEG2000),
   not .tif. The old unzip filter only looked for .tif files so it always
   raised "No .tif for band … in ZIP". The filter now accepts both
   .jp2 and .tif so it works with real Copernicus downloads.

Everything else (BandCache, RasterWindowExtractor, Sentinel2Repository,
Sentinel3Repository, factory) is unchanged.
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

# SCL scene-classification classes treated as cloud / invalid
_SCL_CLOUD_CLASSES = {3, 8, 9, 10}   # shadow, med-cloud, hi-cloud, cirrus
_SCL_INVALID_CLASSES = {0, 1}        # no-data, saturated

BAND_CACHE_DIR = Path("/tmp/rome_satellite_cache")
BAND_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ── Abstract interface ────────────────────────────────────────────────────────

class SatelliteDataInterface(ABC):
    @abstractmethod
    async def get_ndvi(
        self, scene: SatelliteScene, bbox: tuple
    ) -> tuple[Float32Array, Float32Array, Float32Array]: ...

    @abstractmethod
    async def get_lst(
        self, scene: SatelliteScene, bbox: tuple
    ) -> tuple[Float32Array, Float32Array, Float32Array]: ...

    @abstractmethod
    async def get_cloud_mask(
        self, scene: SatelliteScene, bbox: tuple
    ) -> BoolArray: ...

    @abstractmethod
    def sensor_name(self) -> str: ...


# ── Disk cache for downloaded bands ──────────────────────────────────────────

class BandCache:
    """Cache individual band files on disk to avoid re-downloading."""

    def __init__(self, cache_dir: Path = BAND_CACHE_DIR) -> None:
        self._dir = cache_dir

    def path(self, scene_id: str, band: str) -> Path:
        # Extension is .jp2 to reflect the real Copernicus format,
        # but rasterio opens both .jp2 and .tif transparently.
        return self._dir / f"{scene_id}_{band}.jp2"

    def exists(self, scene_id: str, band: str) -> bool:
        p = self.path(scene_id, band)
        return p.exists() and p.stat().st_size > 0

    def write(self, scene_id: str, band: str, data: bytes) -> Path:
        p = self.path(scene_id, band)
        p.write_bytes(data)
        logger.info("band_cache_write", scene_id=scene_id, band=band,
                    size_kb=len(data) // 1024)
        return p

    def size_mb(self, scene_id: str, band: str) -> float:
        p = self.path(scene_id, band)
        return p.stat().st_size / 1_048_576 if p.exists() else 0.0


# ── Tiled raster reader ───────────────────────────────────────────────────────

class RasterWindowExtractor:
    """Extract a geographic bbox window from a raster file using tiled reads.

    Works with both GeoTIFF (.tif) and JPEG2000 (.jp2) — rasterio handles
    both formats transparently via GDAL drivers.
    """

    def __init__(self, tile_size: int = 512) -> None:
        self._tile_size = tile_size

    def extract(
        self,
        raster_path: Path,
        bbox: tuple,
        target_shape: tuple[int, int] | None = None,
    ) -> tuple[Float32Array, Float32Array, Float32Array]:
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
                    raise GISProcessingError(
                        f"Empty window for bbox {bbox} in {raster_path.name}"
                    )

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
            raise GISProcessingError(
                f"Cannot read {raster_path.name}: {exc}"
            ) from exc

        if target_shape and target_shape != (height, width):
            out = self._resample(out, target_shape)
            lats = np.linspace(lat_max, lat_min, target_shape[0], dtype=np.float32)
            lons = np.linspace(lon_min, lon_max, target_shape[1], dtype=np.float32)

        return out, lats, lons

    @staticmethod
    def _resample(arr: Float32Array, shape: tuple[int, int]) -> Float32Array:
        src_h, src_w = arr.shape
        dst_h, dst_w = shape
        src_t = from_bounds(0, 0, 1, 1, src_w, src_h)
        dst_t = from_bounds(0, 0, 1, 1, dst_w, dst_h)
        out = np.empty(shape, dtype=np.float32)
        rasterio.warp.reproject(
            source=arr, destination=out,
            src_transform=src_t, dst_transform=dst_t,
            src_crs="EPSG:4326", dst_crs="EPSG:4326",
            resampling=Resampling.bilinear,
        )
        return out


# ── Copernicus band downloader ────────────────────────────────────────────────

class CopernicusBandDownloader:
    """Download individual Sentinel-2 bands from a Copernicus SAFE product.

    How Copernicus Data Space works
    --------------------------------
    The download URL (scene.download_url) points to the full SAFE product
    archive via the $value endpoint.  There is no ?band= query parameter —
    that caused HTTP 404 in the original code.

    Correct flow:
        1. Download the full SAFE ZIP from scene.download_url  (no ?band=)
        2. Locate the band file inside the ZIP  (e.g. *B08*.jp2)
        3. Extract and write it to the band cache directory

    Sentinel-2 L2A band files are stored as JPEG2000 (.jp2), not GeoTIFF.
    rasterio opens .jp2 natively via the GDAL JP2OpenJPEG driver, which is
    included in the standard rasterio binary wheels.
    """

    _DOWNLOAD_TIMEOUT = 300   # seconds — full SAFE products are large (~600 MB)
    _MAX_RETRIES = 2

    def __init__(self, auth: CopernicusTokenRepository) -> None:
        self._auth = auth

    async def download(
        self, scene: SatelliteScene, band_name: str, dest: Path
    ) -> None:
        """Download the full SAFE product and extract the requested band.

        Args:
            scene:     Scene metadata (download_url = full product $value URL).
            band_name: Sentinel-2 band identifier, e.g. "B08", "SCL".
            dest:      Destination path for the extracted band file.

        Raises:
            SatelliteDataError: On download failure or missing band in archive.
        """
        token = await self._auth.get_token()

        # FIX: Use scene.download_url directly — do NOT append ?band=...
        # The old code did: url = f"{scene.download_url}?band={band_name}"
        # which always returned HTTP 404 from Copernicus.
        url = scene.download_url

        logger.info(
            "band_download_start",
            scene_id=scene.scene_id,
            band=band_name,
            url=url[:80],
        )

        # Retry loop — handles transient 503 (product being restored from archive)
        raw: bytes | None = None
        for attempt in range(1, self._MAX_RETRIES + 1):
            raw = await self._stream(url, token)
            if raw is not None:
                break
            if attempt < self._MAX_RETRIES:
                wait = 5 * attempt
                logger.warning(
                    "band_download_retry",
                    attempt=attempt,
                    wait_seconds=wait,
                    band=band_name,
                )
                await asyncio.sleep(wait)

        if raw is None:
            raise SatelliteDataError(
                f"Band {band_name} download failed after {self._MAX_RETRIES} attempts "
                f"(scene {scene.scene_id})"
            )

        # Extract the specific band from the SAFE ZIP archive
        if raw[:4] == b"PK\x03\x04":
            raw = self._unzip_band(raw, band_name)
        else:
            # If Copernicus ever returns a raw JP2 directly (unlikely for $value)
            logger.debug("band_response_not_zip", band=band_name,
                         first_bytes=raw[:4].hex())

        dest.write_bytes(raw)
        logger.info(
            "band_download_done",
            scene_id=scene.scene_id,
            band=band_name,
            size_kb=len(raw) // 1024,
            dest=str(dest),
        )

    async def _stream(self, url: str, token: str) -> bytes | None:
        """Stream-download url and return raw bytes, or None on 503."""
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
                        logger.warning("band_download_503", url=url[:80])
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
                raise SatelliteDataError(
                    f"Band download network error: {exc}"
                ) from exc

    @staticmethod
    def _unzip_band(raw: bytes, band_name: str) -> bytes:
        """Extract the band raster from a Sentinel-2 SAFE ZIP archive.

        Sentinel-2 L2A structure (example):
            S2A_MSIL2A_.../
              GRANULE/
                L2A_T33TTG_.../
                  IMG_DATA/
                    R10m/  ← B02, B03, B04, B08  (10 m)
                    R20m/  ← B05-B07, B8A, B11, B12, SCL  (20 m)
                    R60m/  ← B01, B09

        Band files are JPEG2000 (.jp2).  We also accept .tif as a fallback
        in case a processing provider converts them.

        Args:
            raw:       Raw bytes of the ZIP archive.
            band_name: Band identifier, e.g. "B08", "B11", "SCL".

        Returns:
            Raw bytes of the extracted JP2 (or TIF) file.

        Raises:
            SatelliteDataError: If no matching raster is found in the archive.
        """
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            # FIX: Accept .jp2 (real Sentinel-2 format) in addition to .tif.
            # Old code: n.endswith(".tif")  → always failed with real data.
            rasters = [
                name for name in zf.namelist()
                if band_name in name and name.endswith((".jp2", ".tif"))
            ]

            if not rasters:
                available = [n for n in zf.namelist() if n.endswith((".jp2", ".tif"))]
                raise SatelliteDataError(
                    f"No raster found for band '{band_name}' in SAFE archive. "
                    f"Available rasters: {available[:10]}"
                )

            # Prefer the highest resolution when multiple resolutions exist
            # (e.g. B08 exists at R10m and sometimes R20m)
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
    """Download and process Sentinel-2 L2A bands.

    Bands used:
        B04  Red   10 m  — NDVI denominator
        B08  NIR   10 m  — NDVI numerator
        B11  SWIR  20 m  — LST proxy (no thermal band on S2)
        SCL       20 m  — Scene Classification Layer (cloud mask)
    """

    SCALE_FACTOR = 1e-4   # Sentinel-2 DN → surface reflectance

    def __init__(
        self,
        auth: CopernicusTokenRepository,
        band_cache: BandCache | None = None,
        downloader: CopernicusBandDownloader | None = None,
        extractor: RasterWindowExtractor | None = None,
    ) -> None:
        self._auth = auth
        self._cache = band_cache or BandCache()
        self._downloader = downloader or CopernicusBandDownloader(auth)
        self._extractor = extractor or RasterWindowExtractor(
            get_settings().tile_size_px
        )

    def sensor_name(self) -> str:
        return "Sentinel-2 L2A"

    async def get_ndvi(self, scene: SatelliteScene, bbox: tuple):
        from app.utils.gis import compute_ndvi
        nir, lats, lons = await self._get_band(scene, "B08", bbox)
        red, _, _ = await self._get_band(scene, "B04", bbox, target_shape=nir.shape)
        ndvi = compute_ndvi(nir * self.SCALE_FACTOR, red * self.SCALE_FACTOR)
        logger.info("ndvi_computed", scene_id=scene.scene_id,
                    mean_ndvi=round(float(np.nanmean(ndvi)), 3))
        return ndvi, lats, lons

    async def get_lst(self, scene: SatelliteScene, bbox: tuple):
        from app.utils.gis import (compute_lst_from_tir,
                                   estimate_emissivity_from_ndvi, compute_ndvi)
        nir, lats, lons = await self._get_band(scene, "B08", bbox)
        red, _, _ = await self._get_band(scene, "B04", bbox, target_shape=nir.shape)
        swir, _, _ = await self._get_band(scene, "B11", bbox, target_shape=nir.shape)

        ndvi = compute_ndvi(nir * self.SCALE_FACTOR, red * self.SCALE_FACTOR)
        swir_r = np.clip(swir * self.SCALE_FACTOR, 0.0, 1.0)

        # SWIR → pseudo brightness temperature (Kelvin)
        # Empirical calibration for Mediterranean summer range (280–360 K)
        pseudo_bt = (280.0 + swir_r * 80.0).astype(np.float32)
        lst = compute_lst_from_tir(pseudo_bt, estimate_emissivity_from_ndvi(ndvi))

        logger.info("lst_computed", scene_id=scene.scene_id,
                    mean_lst=round(float(np.nanmean(lst)), 1))
        return lst, lats, lons

    async def get_cloud_mask(self, scene: SatelliteScene, bbox: tuple) -> BoolArray:
        scl, _, _ = await self._get_band(scene, "SCL", bbox)
        mask = np.zeros(scl.shape, dtype=bool)
        for cls in _SCL_CLOUD_CLASSES | _SCL_INVALID_CLASSES:
            mask |= scl == cls
        logger.info("cloud_mask_computed", scene_id=scene.scene_id,
                    cloud_pct=round(float(mask.mean() * 100), 1))
        return mask

    async def _get_band(
        self,
        scene: SatelliteScene,
        band: str,
        bbox: tuple,
        target_shape: tuple[int, int] | None = None,
    ) -> tuple[Float32Array, Float32Array, Float32Array]:
        if not self._cache.exists(scene.scene_id, band):
            await self._downloader.download(
                scene, band, self._cache.path(scene.scene_id, band)
            )
        else:
            logger.debug("band_cache_hit", scene_id=scene.scene_id, band=band,
                         size_mb=round(self._cache.size_mb(scene.scene_id, band), 1))

        return self._extractor.extract(
            self._cache.path(scene.scene_id, band), bbox, target_shape
        )


# ── Sentinel-3 stub ───────────────────────────────────────────────────────────

class Sentinel3Repository(SatelliteDataInterface):
    """Stub for Sentinel-3 SLSTR true thermal LST — future implementation."""

    def sensor_name(self) -> str:
        return "Sentinel-3 SLSTR"

    async def get_ndvi(self, scene, bbox):
        raise NotImplementedError(
            "Sentinel-3 SLSTR has no NDVI band. Use Sentinel-2."
        )

    async def get_lst(self, scene, bbox):
        raise NotImplementedError(
            "Sentinel-3 LST implementation pending (future stage)."
        )

    async def get_cloud_mask(self, scene, bbox):
        raise NotImplementedError(
            "Sentinel-3 cloud mask implementation pending (future stage)."
        )


# ── Factory ───────────────────────────────────────────────────────────────────

SensorType = Literal["sentinel2", "sentinel3"]


def get_satellite_repository(
    sensor: SensorType,
    auth: CopernicusTokenRepository,
) -> SatelliteDataInterface:
    """Return the correct repository for a given sensor type."""
    if sensor == "sentinel2":
        return Sentinel2Repository(auth=auth)
    if sensor == "sentinel3":
        return Sentinel3Repository()
    raise ValueError(
        f"Unknown sensor: {sensor!r}. Valid values: sentinel2, sentinel3"
    )
