"""PNG rendering: true-colour chips for each date and an RGBA change overlay."""
import io

import numpy as np
from PIL import Image

from detector.change import BUILT_OR_BARE, NO_DATA, SURFACE_CHANGE, VEG_GAIN, VEG_LOSS

OVERLAY_COLORS = {
    BUILT_OR_BARE: (255, 92, 56, 210),   # orange-red
    VEG_LOSS: (255, 196, 0, 200),        # amber
    VEG_GAIN: (64, 220, 120, 190),       # green
    SURFACE_CHANGE: (90, 170, 255, 190), # blue
    NO_DATA: (40, 40, 40, 110),          # grey haze over clouds / gaps
}


def true_color(bands, low=2, high=98, gamma=0.9):
    rgb = np.dstack([bands["red"], bands["green"], bands["blue"]])
    valid = rgb.sum(axis=2) > 0
    if valid.any():
        lo, hi = np.percentile(rgb[valid], [low, high])
    else:
        lo, hi = 0.0, 0.3
    stretched = np.clip((rgb - lo) / max(hi - lo, 1e-6), 0, 1) ** gamma
    return (stretched * 255).astype(np.uint8)


def change_overlay(classes):
    rgba = np.zeros(classes.shape + (4,), dtype=np.uint8)
    for code, color in OVERLAY_COLORS.items():
        rgba[classes == code] = color
    return rgba


def to_png(array):
    buf = io.BytesIO()
    # uint8 HxWx3 / HxWx4 arrays map to RGB / RGBA automatically.
    Image.fromarray(np.ascontiguousarray(array, dtype=np.uint8)).save(buf, format="PNG", optimize=True)
    return buf.getvalue()
