"""Step 1: choose a clear before/after Sentinel-2 scene pair for the AOI.

Writes analyses/{id}/request.json for the Fargate change detector and records
the chosen scenes on the analysis item.
"""
import json
import logging
import os
from datetime import datetime, timezone

import boto3

from stac import find_pair

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
BUCKET = os.environ["RESULTS_BUCKET"]
STAC_API_URL = os.environ["STAC_API_URL"]
s3 = boto3.client("s3")


def handler(event, _context):
    analysis_id = event["analysis_id"]
    bbox = event["bbox"]
    set_status(analysis_id, "SEARCHING")

    before, after = find_pair(STAC_API_URL, bbox, event["before_date"], event["after_date"])
    logger.info("Selected scenes %s -> %s", before["id"], after["id"])

    request = {"analysis_id": analysis_id, "bbox": bbox, "before": before, "after": after}
    s3.put_object(
        Bucket=BUCKET,
        Key=f"analyses/{analysis_id}/request.json",
        Body=json.dumps(request).encode(),
        ContentType="application/json",
    )

    scenes = {
        k: {key: s[key] for key in ("id", "datetime", "cloud_cover", "tile", "thumbnail")}
        for k, s in (("before", before), ("after", after))
    }
    set_status(analysis_id, "DETECTING", scenes=json.dumps(scenes))
    return scenes


def set_status(analysis_id, status, **extra):
    names = {"#s": "status"}
    values = {":s": status, ":t": datetime.now(timezone.utc).isoformat()}
    expr = "SET #s = :s, updated_at = :t"
    for i, (key, value) in enumerate(extra.items()):
        names[f"#k{i}"] = key
        values[f":v{i}"] = value
        expr += f", #k{i} = :v{i}"
    TABLE.update_item(
        Key={"analysis_id": analysis_id},
        UpdateExpression=expr,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )
