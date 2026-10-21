"""Sentinel-2 L2A scene search against the Earth Search STAC API (stdlib only)."""
import json
import urllib.request
from datetime import date, datetime, timedelta

COLLECTION = "sentinel-2-l2a"
BANDS = ("red", "green", "blue", "nir", "swir16", "scl")
DEFAULT_SCALE = 0.0001

# Look mostly backward from each requested date, a little forward.
WINDOW_BEFORE_DAYS = 45
WINDOW_AFTER_DAYS = 15


class NoSceneFound(Exception):
    pass


def search(api_url, bbox, start, end, max_cloud=30, limit=100, timeout=20):
    body = {
        "collections": [COLLECTION],
        "bbox": bbox,
        "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "limit": limit,
    }
    req = urllib.request.Request(
        f"{api_url.rstrip('/')}/search",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/geo+json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read()).get("features", [])


def window(target):
    return target - timedelta(days=WINDOW_BEFORE_DAYS), target + timedelta(days=WINDOW_AFTER_DAYS)


def item_date(item):
    return datetime.fromisoformat(item["properties"]["datetime"].replace("Z", "+00:00")).date()


def contains(outer, inner):
    return (
        outer[0] <= inner[0] and outer[1] <= inner[1]
        and outer[2] >= inner[2] and outer[3] >= inner[3]
    )


def tile_of(item):
    props = item.get("properties", {})
    return props.get("grid:code") or props.get("s2:mgrs_tile")


def has_bands(item):
    return all(b in item.get("assets", {}) for b in BANDS)


def score(item, target, preferred_tile=None):
    """Lower is better: days from target plus a cloud penalty, tile mismatch penalized."""
    props = item["properties"]
    days = abs((item_date(item) - target).days)
    cloud = float(props.get("eo:cloud_cover", 100))
    nodata = float(props.get("s2:nodata_pixel_percentage", 0))
    penalty = 30 if preferred_tile and tile_of(item) != preferred_tile else 0
    return days + 1.5 * cloud + 0.5 * nodata + penalty


def pick_scene(items, target, aoi_bbox, preferred_tile=None):
    candidates = [i for i in items if has_bands(i) and contains(i.get("bbox", [0, 0, 0, 0]), aoi_bbox)]
    if not candidates:
        raise NoSceneFound(
            f"No sufficiently clear Sentinel-2 scene fully covers the area near {target.isoformat()}"
        )
    return min(candidates, key=lambda i: score(i, target, preferred_tile))


def band_info(asset):
    raster = (asset.get("raster:bands") or [{}])[0]
    return {
        "href": asset["href"],
        "scale": raster.get("scale", DEFAULT_SCALE),
        "offset": raster.get("offset", 0.0),
    }


def scene_summary(item):
    props = item["properties"]
    return {
        "id": item["id"],
        "datetime": props["datetime"],
        "cloud_cover": props.get("eo:cloud_cover"),
        "tile": tile_of(item),
        "assets": {b: band_info(item["assets"][b]) for b in BANDS},
        "thumbnail": (item["assets"].get("thumbnail") or {}).get("href"),
    }


def find_pair(api_url, bbox, before_date, after_date, searcher=search):
    before_target = date.fromisoformat(before_date)
    after_target = date.fromisoformat(after_date)

    before = pick_scene(searcher(api_url, bbox, *window(before_target)), before_target, bbox)
    # Same MGRS tile for both dates means the same sensor geometry and fewer artifacts.
    after = pick_scene(
        searcher(api_url, bbox, *window(after_target)), after_target, bbox, tile_of(before)
    )
    if before["id"] == after["id"]:
        raise NoSceneFound("Both dates resolved to the same scene; choose dates further apart")
    return scene_summary(before), scene_summary(after)
