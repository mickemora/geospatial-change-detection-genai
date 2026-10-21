"""Windowed reads of Sentinel-2 COGs, resampled onto the analysis grid.

Only the pixels inside the AOI are fetched (HTTP range requests), so a
10 km x 10 km analysis reads a few MB instead of whole 100 km tiles.
"""
import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "VSI_CACHE": "TRUE",
}

SPECTRAL = ("red", "green", "blue", "nir", "swir16")


def read_to_grid(href, grid, resampling):
    with rasterio.open(href) as src, WarpedVRT(
        src,
        crs=grid.crs,
        transform=Affine(*grid.affine_params()),
        width=grid.width,
        height=grid.height,
        resampling=resampling,
        src_nodata=0,
        nodata=0,
    ) as vrt:
        return vrt.read(1)


def read_scene(scene, grid):
    """Return (reflectance bands dict, scl array, nodata mask)."""
    bands = {}
    nodata = np.zeros((grid.height, grid.width), dtype=bool)
    with rasterio.Env(**GDAL_ENV):
        for name in SPECTRAL:
            info = scene["assets"][name]
            raw = read_to_grid(info["href"], grid, Resampling.bilinear)
            nodata |= raw == 0
            refl = raw.astype(np.float32) * float(info["scale"]) + float(info["offset"])
            bands[name] = np.clip(refl, 0, 1)
        scl = read_to_grid(scene["assets"]["scl"]["href"], grid, Resampling.nearest)
    return bands, scl, nodata
