"""Spectral-index change detection. Pure numpy/scipy so it can be unit tested
without GDAL or network access.

Inputs are surface-reflectance arrays (0..1) on a shared grid for two dates.
Output is a per-pixel class map plus summary statistics for the report.
"""
from dataclasses import asdict, dataclass

import numpy as np
from scipy import ndimage

# Scene Classification Layer values treated as clear ground.
# 4 vegetation, 5 not-vegetated, 6 water, 7 unclassified.
VALID_SCL = (4, 5, 6, 7)

NO_CHANGE = 0
BUILT_OR_BARE = 1   # new construction, cleared or graded ground, new paving
VEG_LOSS = 2        # vegetation removed (often the first sign of construction)
VEG_GAIN = 3        # regrowth, new landscaping, seasonal greening
SURFACE_CHANGE = 4  # other notable brightness change (roofing, parking, water level)
NO_DATA = 255

CLASS_NAMES = {
    BUILT_OR_BARE: "built_or_bare_ground_gain",
    VEG_LOSS: "vegetation_loss",
    VEG_GAIN: "vegetation_gain",
    SURFACE_CHANGE: "other_surface_change",
}


@dataclass(frozen=True)
class Thresholds:
    ndbi_gain: float = 0.10
    ndvi_loss: float = 0.15
    ndvi_gain: float = 0.20
    brightness: float = 0.08
    min_cluster_px: int = 8  # 8 px at 10 m = 800 m^2


def normalized_difference(a, b):
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    denom = a + b
    out = np.zeros_like(a)
    np.divide(a - b, denom, out=out, where=np.abs(denom) > 1e-6)
    return np.clip(out, -1, 1)


def indices(bands):
    return {
        "ndvi": normalized_difference(bands["nir"], bands["red"]),
        "ndbi": normalized_difference(bands["swir16"], bands["nir"]),
        "brightness": (bands["red"] + bands["green"] + bands["blue"]) / 3.0,
    }


def valid_mask(scl_before, scl_after, nodata=None):
    valid = np.isin(scl_before, VALID_SCL) & np.isin(scl_after, VALID_SCL)
    if nodata is not None:
        valid &= ~nodata
    return valid


def remove_small_clusters(mask, min_px):
    if min_px <= 1 or not mask.any():
        return mask
    labels, count = ndimage.label(mask)
    sizes = ndimage.sum(mask, labels, index=np.arange(1, count + 1))
    keep = np.zeros(count + 1, dtype=bool)
    keep[1:] = sizes >= min_px
    return keep[labels]


def classify(before, after, valid, t=Thresholds()):
    """Return (class_map uint8, deltas dict)."""
    ib, ia = indices(before), indices(after)
    d_ndvi = ia["ndvi"] - ib["ndvi"]
    d_ndbi = ia["ndbi"] - ib["ndbi"]
    d_bright = ia["brightness"] - ib["brightness"]

    built = (d_ndbi > t.ndbi_gain) & (d_ndvi < 0)
    veg_loss = (d_ndvi < -t.ndvi_loss) & ~built
    veg_gain = d_ndvi > t.ndvi_gain
    surface = (np.abs(d_bright) > t.brightness) & ~(built | veg_loss | veg_gain)

    classes = np.full(d_ndvi.shape, NO_CHANGE, dtype=np.uint8)
    # Later assignments win; order from least to most specific.
    for code, mask in ((SURFACE_CHANGE, surface), (VEG_GAIN, veg_gain),
                       (VEG_LOSS, veg_loss), (BUILT_OR_BARE, built)):
        cleaned = remove_small_clusters(mask & valid, t.min_cluster_px)
        classes[cleaned] = code
    classes[~valid] = NO_DATA
    return classes, {"d_ndvi": d_ndvi, "d_ndbi": d_ndbi, "d_brightness": d_bright}


def summarize(classes, deltas, pixel_to_lonlat, pixel_area_m2=100.0, top_n=10, t=Thresholds()):
    """Area per class and the largest change clusters with their locations."""
    total = classes.size
    valid = classes != NO_DATA
    valid_px = int(valid.sum())
    changed = valid & (classes != NO_CHANGE)

    def km2(px):
        return round(px * pixel_area_m2 / 1e6, 4)

    per_class = {}
    for code, name in CLASS_NAMES.items():
        px = int((classes == code).sum())
        per_class[name] = {
            "area_km2": km2(px),
            "pct_of_valid": round(100 * px / valid_px, 2) if valid_px else 0.0,
        }

    clusters = []
    labels, count = ndimage.label(changed)
    if count:
        sizes = ndimage.sum(changed, labels, index=np.arange(1, count + 1))
        centroids = ndimage.center_of_mass(changed, labels, index=np.arange(1, count + 1))
        for idx in np.argsort(sizes)[::-1][:top_n]:
            label = idx + 1
            member = labels == label
            codes, counts = np.unique(classes[member], return_counts=True)
            dominant = int(codes[np.argmax(counts)])
            row, col = centroids[idx]
            lon, lat = pixel_to_lonlat(row, col)
            clusters.append({
                "rank": len(clusters) + 1,
                "dominant_change": CLASS_NAMES.get(dominant, "unknown"),
                "area_km2": km2(int(sizes[idx])),
                "centroid": {"lon": round(lon, 6), "lat": round(lat, 6)},
                "mean_d_ndvi": round(float(deltas["d_ndvi"][member].mean()), 3),
                "mean_d_ndbi": round(float(deltas["d_ndbi"][member].mean()), 3),
            })

    return {
        "valid_fraction": round(valid_px / total, 3) if total else 0.0,
        "valid_area_km2": km2(valid_px),
        "changed_area_km2": km2(int(changed.sum())),
        "changed_pct_of_valid": round(100 * changed.sum() / valid_px, 2) if valid_px else 0.0,
        "classes": per_class,
        "clusters": clusters,
        "thresholds": asdict(t),
        "pixel_size_m": round(pixel_area_m2 ** 0.5, 2),
    }
