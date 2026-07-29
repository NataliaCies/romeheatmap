"""GIS utility functions using numpy for raster operations."""
from __future__ import annotations
import numpy as np
from numpy.typing import NDArray

Float32Array = NDArray[np.float32]


def compute_ndvi(nir: Float32Array, red: Float32Array) -> Float32Array:
    """NDVI = (NIR - RED) / (NIR + RED). Returns NaN where both bands are zero."""
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = np.where(nir + red != 0, (nir - red) / (nir + red), np.nan)
    return ndvi.astype(np.float32)


def compute_lst_from_tir(tir_kelvin: Float32Array, emissivity: Float32Array | None = None) -> Float32Array:
    """Convert TIR brightness temperature (K) to LST in Celsius."""
    WAVELENGTH_UM = 10.8; RHO = 14388.0
    if emissivity is None:
        emissivity = np.full_like(tir_kelvin, 0.97, dtype=np.float32)
    with np.errstate(divide="ignore", invalid="ignore"):
        correction = (WAVELENGTH_UM * tir_kelvin / RHO) * np.log(emissivity)
        lst_kelvin = tir_kelvin / (1 + correction)
    return np.clip(lst_kelvin - 273.15, -10, 80).astype(np.float32)


def estimate_emissivity_from_ndvi(ndvi: Float32Array) -> Float32Array:
    """Estimate emissivity from NDVI (Valor & Caselles, 1996)."""
    pv = np.clip((ndvi - 0.2) / 0.3, 0, 1) ** 2
    return (0.970 + 0.020 * pv).astype(np.float32)


def apply_cloud_mask(array: Float32Array, cloud_mask: NDArray[np.bool_]) -> Float32Array:
    """Set cloud-covered pixels to NaN."""
    result = array.copy(); result[cloud_mask] = np.nan; return result


def compute_livability_score(lst: Float32Array, ndvi: Float32Array, cloud_fraction: float) -> float:
    """Livability Score 0-100 from LST, NDVI and cloud fraction."""
    valid_lst = lst[~np.isnan(lst)]; valid_ndvi = ndvi[~np.isnan(ndvi)]
    mean_lst = float(np.mean(valid_lst)) if len(valid_lst) > 0 else 35.0
    mean_ndvi = float(np.mean(valid_ndvi)) if len(valid_ndvi) > 0 else 0.2
    heat_norm = np.clip((mean_lst - 20.0) / 30.0, 0.0, 1.0)
    return float(np.clip(50.0 + mean_ndvi * 25.0 - float(heat_norm) * 30.0 + (1.0 - cloud_fraction) * 5.0, 0, 100))


def normalise_array(arr: Float32Array) -> Float32Array:
    """Min-max normalise to [0,1], ignoring NaN."""
    vmin, vmax = float(np.nanmin(arr)), float(np.nanmax(arr))
    if vmax == vmin: return np.zeros_like(arr)
    return ((arr - vmin) / (vmax - vmin)).astype(np.float32)


def tile_indices(height: int, width: int, tile_size: int) -> list[tuple[int, int, int, int]]:
    """Generate (r0,r1,c0,c1) tile indices for block-processing large rasters."""
    tiles = []
    for r in range(0, height, tile_size):
        for c in range(0, width, tile_size):
            tiles.append((r, min(r + tile_size, height), c, min(c + tile_size, width)))
    return tiles
