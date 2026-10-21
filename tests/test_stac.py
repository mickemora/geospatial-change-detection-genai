import unittest
from datetime import date

from tests import paths  # noqa: F401
from stac import BANDS, NoSceneFound, find_pair, pick_scene, scene_summary

AOI = [-97.64, 30.20, -97.60, 30.24]
TILE_BBOX = [-98.0, 29.8, -96.9, 30.8]


def item(item_id, dt, cloud, tile="MGRS-14RPU", bbox=TILE_BBOX):
    assets = {
        b: {"href": f"https://example.com/{item_id}/{b}.tif",
            "raster:bands": [{"scale": 0.0001, "offset": -0.1}]}
        for b in BANDS
    }
    return {
        "id": item_id,
        "bbox": bbox,
        "properties": {"datetime": f"{dt}T17:00:00Z", "eo:cloud_cover": cloud,
                       "grid:code": tile, "s2:nodata_pixel_percentage": 0},
        "assets": assets,
    }


class PickSceneTest(unittest.TestCase):
    def test_prefers_clear_scene_over_slightly_closer_cloudy_one(self):
        items = [item("cloudy", "2021-06-01", 25), item("clear", "2021-05-27", 1)]
        self.assertEqual(pick_scene(items, date(2021, 6, 1), AOI)["id"], "clear")

    def test_rejects_scene_that_does_not_cover_aoi(self):
        partial = item("partial", "2021-06-01", 0, bbox=[-97.62, 30.0, -96.9, 30.8])
        with self.assertRaises(NoSceneFound):
            pick_scene([partial], date(2021, 6, 1), AOI)

    def test_prefers_matching_tile(self):
        items = [item("other", "2021-06-01", 2, tile="MGRS-14RNU"),
                 item("same", "2021-06-03", 2)]
        chosen = pick_scene(items, date(2021, 6, 1), AOI, preferred_tile="MGRS-14RPU")
        self.assertEqual(chosen["id"], "same")

    def test_summary_carries_scale_and_offset(self):
        summary = scene_summary(item("a", "2021-06-01", 1))
        self.assertEqual(summary["assets"]["nir"]["offset"], -0.1)
        self.assertEqual(summary["tile"], "MGRS-14RPU")


class FindPairTest(unittest.TestCase):
    def test_find_pair_uses_windows_around_each_date(self):
        calls = []

        def fake_search(_url, _bbox, start, end):
            calls.append((start, end))
            year = start.year if start.year == end.year else end.year
            return [item(f"scene-{year}", f"{year}-06-01", 3)]

        before, after = find_pair("https://stac", AOI, "2020-06-10", "2023-06-10", fake_search)
        self.assertEqual(before["id"], "scene-2020")
        self.assertEqual(after["id"], "scene-2023")
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0][0] < date(2020, 6, 10) < calls[0][1])

    def test_same_scene_for_both_dates_is_an_error(self):
        def fake_search(*_args):
            return [item("only", "2021-06-01", 3)]

        with self.assertRaises(NoSceneFound):
            find_pair("https://stac", AOI, "2021-05-20", "2021-06-10", fake_search)


if __name__ == "__main__":
    unittest.main()
