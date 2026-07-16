import runpy
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


USAGE_BURN = runpy.run_path(Path(__file__).with_name("usage-burn"))
codex_windows = USAGE_BURN["codex_windows"]
print_codex = USAGE_BURN["print_codex"]


def render_codex(data: dict, weekly_only: bool = False) -> str:
    output = StringIO()
    with redirect_stdout(output):
        print_codex(data, weekly_only=weekly_only)
    return output.getvalue()


class CodexUsageTests(unittest.TestCase):
    def test_schema_v2_prints_primary_and_secondary_windows(self):
        output = render_codex({
            "schema_version": 2,
            "primary": {"pct": 12, "window_secs": 18000},
            "secondary": {"pct": 34, "window_secs": 604800},
        })

        self.assertIn("Session (5h)", output)
        self.assertIn("Week (7d)", output)
        self.assertEqual(output.count("codex"), 2)

    def test_schema_v2_ignores_legacy_aliases(self):
        weekly = {"pct": 3, "window_secs": 604800}
        output = render_codex({
            "schema_version": 2,
            "primary": weekly,
            "7d": weekly,
        })

        self.assertIn("Week (7d)", output)
        self.assertEqual(output.count("codex"), 1)

    def test_legacy_cache_remains_supported(self):
        output = render_codex({
            "5h": {"pct": 12},
            "7d": {"pct": 34},
        })

        self.assertIn("Session (5h)", output)
        self.assertIn("Week (7d)", output)

    def test_unknown_duration_uses_positional_label(self):
        output = render_codex({
            "schema_version": 2,
            "primary": {"pct": 12, "window_secs": 7200},
        })

        self.assertIn("Primary", output)
        self.assertNotIn("Session", output)

    def test_weekly_filter_uses_actual_duration(self):
        output = render_codex(
            {
                "schema_version": 2,
                "primary": {"pct": 12, "window_secs": 18000},
                "secondary": {"pct": 34, "window_secs": 604799},
            },
            weekly_only=True,
        )

        self.assertNotIn("Session (5h)", output)
        self.assertIn("Week (7d)", output)

    def test_additional_limits_are_not_printed(self):
        output = render_codex({
            "schema_version": 2,
            "primary": {"pct": 12, "window_secs": 18000},
            "additional": [
                {"name": "Spark", "primary": {"pct": 90, "window_secs": 18000}},
            ],
        })

        self.assertNotIn("Spark", output)
        self.assertEqual(output.count("codex"), 1)


if __name__ == "__main__":
    unittest.main()
