"""REST API for the demo (API Gateway Lambda proxy integration).

POST /analyses        start an analysis  {bbox, before_date, after_date, label?}
GET  /analyses        list recent analyses
GET  /analyses/{id}   status, report, stats and short-lived image URLs
"""
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

from validation import ValidationError, validate_request

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
BUCKET = os.environ["RESULTS_BUCKET"]
STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]

s3 = boto3.client("s3")
sfn = boto3.client("stepfunctions")

TTL_DAYS = 30
IMAGE_KEYS = ("before.png", "after.png", "change.png")
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}


def respond(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **CORS_HEADERS},
        "body": json.dumps(body, default=str),
    }


def handler(event, _context):
    method = event.get("httpMethod")
    path = (event.get("path") or "").rstrip("/")
    params = event.get("pathParameters") or {}
    try:
        if method == "OPTIONS":
            return respond(200, {})
        if method == "POST" and path.endswith("/analyses"):
            return create_analysis(event)
        if method == "GET" and path.endswith("/analyses"):
            return list_analyses()
        if method == "GET" and "/analyses/" in path:
            analysis_id = params.get("proxy", path).rsplit("/", 1)[-1]
            return get_analysis(analysis_id)
        return respond(404, {"error": "not found"})
    except ValidationError as exc:
        return respond(400, {"error": str(exc)})
    except Exception:  # noqa: BLE001 - never leak internals to a public endpoint
        logger.exception("Unhandled error")
        return respond(500, {"error": "internal error"})


def create_analysis(event):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        raise ValidationError("request body is not valid JSON")
    request = validate_request(body)

    analysis_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    TABLE.put_item(
        Item={
            "analysis_id": analysis_id,
            "status": "QUEUED",
            "label": request["label"],
            "bbox": json.dumps(request["bbox"]),
            "before_date": request["before_date"],
            "after_date": request["after_date"],
            "created_at": now,
            "updated_at": now,
            "expires_at": int(time.time()) + TTL_DAYS * 86400,
        }
    )
    sfn.start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        name=f"analysis-{analysis_id}",
        input=json.dumps({"analysis_id": analysis_id, **request}),
    )
    return respond(202, {"analysis_id": analysis_id, "status": "QUEUED"})


def list_analyses():
    # A scan is fine at demo scale (items expire after 30 days).
    items = TABLE.scan(Limit=200).get("Items", [])
    items.sort(key=lambda i: i.get("created_at", ""), reverse=True)
    return respond(200, {"analyses": [summarize(i) for i in items[:25]]})


def get_analysis(analysis_id):
    item = TABLE.get_item(Key={"analysis_id": analysis_id}).get("Item")
    if not item:
        return respond(404, {"error": "analysis not found"})

    result = summarize(item)
    result["scenes"] = json.loads(item["scenes"]) if item.get("scenes") else None
    result["error_message"] = item.get("error_message")

    if item.get("status") == "COMPLETE":
        prefix = f"analyses/{analysis_id}/"
        result["images"] = {
            key.split(".")[0]: s3.generate_presigned_url(
                "get_object", Params={"Bucket": BUCKET, "Key": prefix + key}, ExpiresIn=3600
            )
            for key in IMAGE_KEYS
        }
        result["stats"] = read_json(prefix + "stats.json")
        result["report"] = read_json(prefix + "report.json")
    return respond(200, result)


def summarize(item):
    return {
        "analysis_id": item["analysis_id"],
        "status": item.get("status"),
        "label": item.get("label"),
        "bbox": json.loads(item["bbox"]) if item.get("bbox") else None,
        "before_date": item.get("before_date"),
        "after_date": item.get("after_date"),
        "headline": item.get("headline"),
        "created_at": item.get("created_at"),
    }


def read_json(key):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except ClientError:
        return None
