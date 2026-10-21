"""Fargate entry point: read request.json, detect change, write outputs to S3.

Outputs under s3://$RESULTS_BUCKET/analyses/$ANALYSIS_ID/:
  before.png, after.png   true-colour chips
  change.png              RGBA change overlay (same grid as the chips)
  stats.json              areas per class, top clusters, overlay bounds
"""
import json
import logging
import os
import sys
import time

import boto3

from detector.change import Thresholds, classify, summarize, valid_mask
from detector.grid import build_grid
from detector.raster_io import read_scene
from detector.render import change_overlay, to_png, true_color

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("detector")

MIN_VALID_FRACTION = 0.4


def run(bucket, analysis_id, s3=None):
    s3 = s3 or boto3.client("s3")
    prefix = f"analyses/{analysis_id}/"
    request = json.loads(s3.get_object(Bucket=bucket, Key=prefix + "request.json")["Body"].read())

    grid = build_grid(request["bbox"])
    log.info("Grid %dx%d px at %.1f m", grid.width, grid.height, grid.ground_resolution_m)

    started = time.time()
    before, scl_b, nodata_b = read_scene(request["before"], grid)
    after, scl_a, nodata_a = read_scene(request["after"], grid)
    log.info("Read both scenes in %.1fs", time.time() - started)

    thresholds = Thresholds()
    valid = valid_mask(scl_b, scl_a, nodata_b | nodata_a)
    classes, deltas = classify(before, after, valid, thresholds)
    stats = summarize(classes, deltas, grid.pixel_to_lonlat, grid.pixel_area_m2, t=thresholds)

    stats.update({
        "analysis_id": analysis_id,
        "bbox": request["bbox"],
        "overlay_bounds": grid.bounds_lonlat(),
        "grid": {"width": grid.width, "height": grid.height},
        "before_scene": {k: request["before"][k] for k in ("id", "datetime", "cloud_cover", "tile")},
        "after_scene": {k: request["after"][k] for k in ("id", "datetime", "cloud_cover", "tile")},
        "low_confidence": stats["valid_fraction"] < MIN_VALID_FRACTION,
    })

    outputs = {
        "before.png": (to_png(true_color(before)), "image/png"),
        "after.png": (to_png(true_color(after)), "image/png"),
        "change.png": (to_png(change_overlay(classes)), "image/png"),
        "stats.json": (json.dumps(stats, indent=2).encode(), "application/json"),
    }
    for name, (body, content_type) in outputs.items():
        s3.put_object(Bucket=bucket, Key=prefix + name, Body=body, ContentType=content_type)

    log.info("Changed area %.3f km2 (%.2f%% of valid)", stats["changed_area_km2"],
             stats["changed_pct_of_valid"])
    return stats


def main():
    try:
        run(os.environ["RESULTS_BUCKET"], os.environ["ANALYSIS_ID"])
    except Exception:
        log.exception("Change detection failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
