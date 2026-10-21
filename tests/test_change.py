import unittest

import numpy as np

from tests import paths  # noqa: F401
from detector.change import (
    BUILT_OR_BARE, NO_CHANGE, NO_DATA, VEG_GAIN, VEG_LOSS,
    Thresholds, classify, normalized_difference, remove_small_clusters, summarize, valid_mask,
)
from detector.grid import build_grid, lonlat_to_merc, merc_to_lonlat
from detector.render import change_overlay, to_png, true_color


def scene(shape, red, green, blue, nir, swir16):
    return {k: np.full(shape, v, dtype=np.float32) for k, v in
            dict(red=red, green=green, blue=blue, nir=nir, swir16=swir16).items()}


# Typical surface reflectances.
VEGETATION = dict(red=0.04, green=0.08, blue=0.03, nir=0.40, swir16=0.20)
BARE_SOIL = dict(red=0.20, green=0.17, blue=0.13, nir=0.25, swir16=0.32)


class ChangeDetectionTest(unittest.TestCase):
    def setUp(self):
        self.shape = (40, 40)
        self.valid = np.ones(self.shape, dtype=bool)

    def test_normalized_difference_handles_zero_denominator(self):
        out = normalized_difference(np.zeros((2, 2)), np.zeros((2, 2)))
        self.assertTrue(np.all(out == 0))

    def test_identical_scenes_show_no_change(self):
        before = scene(self.shape, **VEGETATION)
        classes, _ = classify(before, before, self.valid)
        self.assertTrue(np.all(classes == NO_CHANGE))

    def test_cleared_block_is_built_or_bare(self):
        before = scene(self.shape, **VEGETATION)
        after = scene(self.shape, **VEGETATION)
        for k, v in BARE_SOIL.items():
            after[k][10:20, 10:20] = v
        classes, _ = classify(before, after, self.valid)
        self.assertTrue(np.all(classes[10:20, 10:20] == BUILT_OR_BARE))
        self.assertEqual(int((classes != NO_CHANGE).sum()), 100)

    def test_regrowth_is_vegetation_gain(self):
        before = scene(self.shape, **BARE_SOIL)
        after = scene(self.shape, **BARE_SOIL)
        for k, v in VEGETATION.items():
            after[k][0:10, 0:10] = v
        classes, _ = classify(before, after, self.valid)
        self.assertTrue(np.all(classes[0:10, 0:10] == VEG_GAIN))

    def test_vegetation_loss_without_ndbi_rise(self):
        before = scene(self.shape, **VEGETATION)
        after = scene(self.shape, **VEGETATION)
        # Thinner canopy: NDVI drops a lot, SWIR barely moves so NDBI stays flat-ish.
        after["nir"][5:15, 5:15] = 0.18
        after["swir16"][5:15, 5:15] = 0.09
        classes, _ = classify(before, after, self.valid)
        self.assertTrue(np.all(classes[5:15, 5:15] == VEG_LOSS))

    def test_small_speckle_is_removed(self):
        before = scene(self.shape, **VEGETATION)
        after = scene(self.shape, **VEGETATION)
        for k, v in BARE_SOIL.items():
            after[k][1:3, 1:3] = v  # 4 px, below the 8 px minimum
        classes, _ = classify(before, after, self.valid, Thresholds(min_cluster_px=8))
        self.assertTrue(np.all(classes == NO_CHANGE))

    def test_clouds_become_no_data(self):
        scl_b = np.full(self.shape, 4, dtype=np.uint8)
        scl_a = scl_b.copy()
        scl_a[:, :10] = 9  # high-probability cloud
        valid = valid_mask(scl_b, scl_a)
        before = scene(self.shape, **VEGETATION)
        classes, _ = classify(before, before, valid)
        self.assertTrue(np.all(classes[:, :10] == NO_DATA))
        self.assertTrue(np.all(classes[:, 10:] == NO_CHANGE))

    def test_remove_small_clusters_keeps_large(self):
        mask = np.zeros((10, 10), dtype=bool)
        mask[0:3, 0:3] = True   # 9 px
        mask[8, 8] = True       # 1 px
        cleaned = remove_small_clusters(mask, 5)
        self.assertEqual(int(cleaned.sum()), 9)

    def test_summary_reports_area_and_cluster_location(self):
        grid = build_grid([-97.64, 30.20, -97.60, 30.24])
        shape = (grid.height, grid.width)
        before = scene(shape, **VEGETATION)
        after = scene(shape, **VEGETATION)
        for k, v in BARE_SOIL.items():
            after[k][100:150, 100:150] = v
        classes, deltas = classify(before, after, np.ones(shape, dtype=bool))
        stats = summarize(classes, deltas, grid.pixel_to_lonlat, grid.pixel_area_m2)

        self.assertAlmostEqual(stats["changed_area_km2"], 0.25, places=3)  # 2500 px * 100 m2
        self.assertEqual(len(stats["clusters"]), 1)
        cluster = stats["clusters"][0]
        self.assertEqual(cluster["dominant_change"], "built_or_bare_ground_gain")
        lon, lat = cluster["centroid"]["lon"], cluster["centroid"]["lat"]
        self.assertTrue(-97.64 < lon < -97.60 and 30.20 < lat < 30.24)
        self.assertLess(cluster["mean_d_ndvi"], 0)
        self.assertGreater(cluster["mean_d_ndbi"], 0)


class GridTest(unittest.TestCase):
    def test_mercator_round_trip(self):
        lon, lat = merc_to_lonlat(*lonlat_to_merc(-97.62, 30.22))
        self.assertAlmostEqual(lon, -97.62, places=9)
        self.assertAlmostEqual(lat, 30.22, places=9)

    def test_grid_is_about_ten_metres_on_the_ground(self):
        # ~3.85 km wide, ~4.42 km tall at 30 deg N.
        grid = build_grid([-97.64, 30.20, -97.60, 30.24])
        self.assertTrue(370 <= grid.width <= 400, grid.width)
        self.assertTrue(430 <= grid.height <= 460, grid.height)

    def test_overlay_bounds_cover_bbox(self):
        bbox = [-97.64, 30.20, -97.60, 30.24]
        (south, west), (north, east) = build_grid(bbox).bounds_lonlat()
        self.assertAlmostEqual(west, bbox[0], places=6)
        self.assertAlmostEqual(north, bbox[3], places=6)
        self.assertLessEqual(south, bbox[1] + 1e-6)
        self.assertGreaterEqual(east, bbox[2] - 1e-6)


class RenderTest(unittest.TestCase):
    def test_pngs_render(self):
        bands = scene((20, 30), **VEGETATION)
        rgb_png = to_png(true_color(bands))
        classes = np.zeros((20, 30), dtype=np.uint8)
        classes[0:5, 0:5] = BUILT_OR_BARE
        overlay = change_overlay(classes)
        self.assertEqual(overlay.shape, (20, 30, 4))
        self.assertEqual(overlay[0, 0, 3], 210)
        self.assertEqual(overlay[10, 10, 3], 0)
        for png in (rgb_png, to_png(overlay)):
            self.assertTrue(png.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
