import unittest
from datetime import date

from tests import paths  # noqa: F401
from prompt import build_user_text, parse_report
from validation import ValidationError, validate_request

TODAY = date(2026, 10, 20)
GOOD = {"bbox": [-97.64, 30.20, -97.60, 30.24], "before_date": "2020-06-01",
        "after_date": "2023-06-01", "label": "Example site"}


class ValidationTest(unittest.TestCase):
    def test_valid_request_is_normalized(self):
        req = validate_request(GOOD, today=TODAY)
        self.assertEqual(req["before_date"], "2020-06-01")
        self.assertEqual(req["label"], "Example site")

    def bad(self, **overrides):
        with self.assertRaises(ValidationError):
            validate_request({**GOOD, **overrides}, today=TODAY)

    def test_rejects_bad_inputs(self):
        self.bad(bbox=[1, 2, 3])
        self.bad(bbox=[-97.60, 30.20, -97.64, 30.24])      # swapped
        self.bad(bbox=[-98.5, 30.0, -97.0, 31.0])          # far too large
        self.bad(bbox=[-97.601, 30.200, -97.600, 30.201])  # ~100 m, too small
        self.bad(before_date="June 2020")
        self.bad(after_date="2027-01-01")                  # future
        self.bad(before_date="2023-05-25")                 # too close to after_date
        self.bad(before_date="2015-01-01")                 # before archive

    def test_label_is_truncated(self):
        req = validate_request({**GOOD, "label": "x" * 200}, today=TODAY)
        self.assertEqual(len(req["label"]), 80)


class PromptTest(unittest.TestCase):
    def test_parse_plain_and_fenced_json(self):
        self.assertEqual(parse_report('{"headline": "a"}')["headline"], "a")
        self.assertEqual(parse_report('```json\n{"headline": "b"}\n```')["headline"], "b")
        self.assertEqual(parse_report('Here you go: {"headline": "c"} done')["headline"], "c")

    def test_unparseable_text_is_kept(self):
        report = parse_report("not json at all")
        self.assertEqual(report["overall_confidence"], "low")
        self.assertIn("not json", report["summary"])

    def test_user_text_includes_stats(self):
        text = build_user_text({"changed_area_km2": 1.23, "clusters": []}, "Site A")
        self.assertIn("1.23", text)
        self.assertIn("Site A", text)


if __name__ == "__main__":
    unittest.main()
