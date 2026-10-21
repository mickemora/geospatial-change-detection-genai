"""Request validation for new analyses. Pure Python so it is easy to unit test."""
import math
from datetime import date, timedelta

MIN_SIDE_KM = 0.5
MAX_SIDE_KM = 15.0
MIN_GAP_DAYS = 14
EARLIEST_DATE = date(2017, 4, 1)  # Sentinel-2 L2A archive on Earth Search starts around here


class ValidationError(ValueError):
    pass


def aoi_size_km(bbox):
    """Approximate width/height in km of a [min_lon, min_lat, max_lon, max_lat] box."""
    min_lon, min_lat, max_lon, max_lat = bbox
    mid_lat = math.radians((min_lat + max_lat) / 2)
    width = (max_lon - min_lon) * 111.32 * math.cos(mid_lat)
    height = (max_lat - min_lat) * 110.57
    return width, height


def parse_bbox(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValidationError("bbox must be [min_lon, min_lat, max_lon, max_lat]")
    try:
        bbox = [float(v) for v in value]
    except (TypeError, ValueError):
        raise ValidationError("bbox values must be numbers")
    min_lon, min_lat, max_lon, max_lat = bbox
    if not (-180 <= min_lon < max_lon <= 180 and -85 <= min_lat < max_lat <= 85):
        raise ValidationError("bbox is out of range or min/max are swapped")
    width, height = aoi_size_km(bbox)
    if min(width, height) < MIN_SIDE_KM:
        raise ValidationError(f"area of interest must be at least {MIN_SIDE_KM} km on each side")
    if max(width, height) > MAX_SIDE_KM:
        raise ValidationError(f"area of interest must be at most {MAX_SIDE_KM} km on each side")
    return bbox


def parse_date(value, field):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError(f"{field} must be an ISO date (YYYY-MM-DD)")


def validate_request(body, today=None):
    """Return a normalized analysis request or raise ValidationError."""
    if not isinstance(body, dict):
        raise ValidationError("request body must be a JSON object")
    today = today or date.today()

    bbox = parse_bbox(body.get("bbox"))
    before = parse_date(body.get("before_date"), "before_date")
    after = parse_date(body.get("after_date"), "after_date")

    if before < EARLIEST_DATE:
        raise ValidationError(f"before_date must be on or after {EARLIEST_DATE.isoformat()}")
    if after > today:
        raise ValidationError("after_date cannot be in the future")
    if after - before < timedelta(days=MIN_GAP_DAYS):
        raise ValidationError(f"after_date must be at least {MIN_GAP_DAYS} days after before_date")

    label = str(body.get("label") or "").strip()[:80]
    return {
        "bbox": bbox,
        "before_date": before.isoformat(),
        "after_date": after.isoformat(),
        "label": label,
    }
