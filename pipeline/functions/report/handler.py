"""Step 3: turn the change statistics and image chips into an analyst report
with a multimodal model on Amazon Bedrock (Converse API + guardrail)."""
import json
import logging
import os
from datetime import datetime, timezone

import boto3

from prompt import SYSTEM_PROMPT, build_user_text, parse_report

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
BUCKET = os.environ["RESULTS_BUCKET"]
MODEL_ID = os.environ["MODEL_ID"]
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID")
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION")

s3 = boto3.client("s3")
bedrock = boto3.client("bedrock-runtime")


def handler(event, _context):
    analysis_id = event["analysis_id"]
    prefix = f"analyses/{analysis_id}/"
    update(analysis_id, "REPORTING")

    stats = json.loads(get(prefix + "stats.json"))
    content = [{"text": build_user_text(stats, event.get("label", ""))}]
    for name in ("before.png", "after.png", "change.png"):
        content.append({"image": {"format": "png", "source": {"bytes": get(prefix + name)}}})

    request = {
        "modelId": MODEL_ID,
        "system": [{"text": SYSTEM_PROMPT}],
        "messages": [{"role": "user", "content": content}],
        "inferenceConfig": {"maxTokens": 1500, "temperature": 0.2},
    }
    if GUARDRAIL_ID:
        request["guardrailConfig"] = {
            "guardrailIdentifier": GUARDRAIL_ID,
            "guardrailVersion": GUARDRAIL_VERSION or "DRAFT",
        }

    response = bedrock.converse(**request)
    text = "".join(
        block.get("text", "") for block in response["output"]["message"]["content"]
    )

    if response.get("stopReason") == "guardrail_intervened":
        report = {
            "headline": "Report withheld by guardrail",
            "summary": text,
            "observations": [],
            "overall_confidence": "low",
            "guardrail_intervened": True,
        }
    else:
        report = parse_report(text)

    report["model_id"] = MODEL_ID
    report["usage"] = response.get("usage")
    report["generated_at"] = datetime.now(timezone.utc).isoformat()

    s3.put_object(
        Bucket=BUCKET,
        Key=prefix + "report.json",
        Body=json.dumps(report, indent=2).encode(),
        ContentType="application/json",
    )
    update(analysis_id, "COMPLETE", headline=report.get("headline", "")[:200])
    return {"headline": report.get("headline")}


def get(key):
    return s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()


def update(analysis_id, status, headline=None):
    expr = "SET #s = :s, updated_at = :t"
    values = {":s": status, ":t": datetime.now(timezone.utc).isoformat()}
    if headline is not None:
        expr += ", headline = :h"
        values[":h"] = headline
    TABLE.update_item(
        Key={"analysis_id": analysis_id},
        UpdateExpression=expr,
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues=values,
    )
