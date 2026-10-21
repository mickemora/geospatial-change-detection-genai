"""Prompt construction and response parsing for the analyst report."""
import json
import re

SYSTEM_PROMPT = """You are a geospatial imagery analyst writing a short change report.
You are given a BEFORE and an AFTER Sentinel-2 true-colour image of the same area
(10 m pixels), a change overlay, and statistics computed by a spectral-index
change detector.

Overlay colours: orange-red = built-up or bare ground gain, amber = vegetation loss,
green = vegetation gain, blue = other surface change, grey = cloud or no data.

Rules:
- Describe only what is observable at 10 m resolution: land clearing, new
  structures or pavement, earthworks, water-level or vegetation change.
  Do not claim to see vehicles, aircraft, people or other small objects.
- Use the statistics as ground truth for areas and locations; do not invent numbers.
- Offer the most likely explanation for each change and say how confident you are.
  Seasonal vegetation and lighting differences are common false positives; flag them.
- Stay descriptive. Do not recommend targeting or any action against the site.
- Respond with JSON only, matching this schema:
{
  "headline": "one sentence, under 20 words",
  "summary": "2-4 sentences",
  "observations": [
    {"location": "where in the image or cluster rank", "change": "what changed",
     "likely_explanation": "...", "confidence": "high|medium|low"}
  ],
  "overall_confidence": "high|medium|low",
  "possible_false_positives": ["..."],
  "recommended_follow_up": ["e.g. higher-resolution imagery, another date"]
}"""


def build_user_text(stats, label=""):
    compact = {
        "area_label": label or None,
        "bbox": stats.get("bbox"),
        "before_scene": stats.get("before_scene"),
        "after_scene": stats.get("after_scene"),
        "valid_fraction": stats.get("valid_fraction"),
        "low_confidence_data": stats.get("low_confidence"),
        "changed_area_km2": stats.get("changed_area_km2"),
        "changed_pct_of_valid": stats.get("changed_pct_of_valid"),
        "classes": stats.get("classes"),
        "top_clusters": stats.get("clusters", [])[:8],
    }
    return (
        "Change detector statistics:\n"
        + json.dumps(compact, indent=2)
        + "\n\nImages follow in order: BEFORE, AFTER, CHANGE OVERLAY. Write the JSON report."
    )


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_report(text):
    """Parse the model's JSON, tolerating code fences or leading prose."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                pass
    return {
        "headline": "Report generated (unstructured)",
        "summary": text.strip()[:2000],
        "observations": [],
        "overall_confidence": "low",
    }
