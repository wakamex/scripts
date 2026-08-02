import runpy
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


USAGE_BURN = runpy.run_path(Path(__file__).with_name("usage-burn"))
agy_period_hours = USAGE_BURN["agy_period_hours"]
duration_hours = USAGE_BURN["duration_hours"]
print_agy = USAGE_BURN["print_agy"]
print_claude = USAGE_BURN["print_claude"]
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


class GenericDurationTests(unittest.TestCase):
    def test_duration_hours_parses_machine_and_display_values(self):
        cases = {
            "24h": 24,
            "2d": 48,
            "weekly": 168,
            "P1DT12H": 36,
            "Five Hour Limit": 5,
            "30 minutes": 0.5,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(duration_hours(value), expected)

    def test_agy_period_prefers_explicit_seconds(self):
        bucket = {
            "window_secs": 86_400,
            "window": "weekly",
            "display_name": "Weekly Limit",
        }
        self.assertEqual(agy_period_hours(bucket), 24)

    def test_print_agy_keeps_unknown_and_new_durations(self):
        data = {
            "quota_summary": {
                "groups": [
                    {
                        "display_name": "Gemini Models",
                        "buckets": [
                            {
                                "display_name": "Daily Limit",
                                "window": "daily",
                                "remaining_pct": 75,
                            },
                            {
                                "display_name": "Flexible Limit",
                                "window": "flexible",
                                "remaining_pct": 80,
                            },
                        ],
                    }
                ]
            }
        }
        output = StringIO()
        with redirect_stdout(output):
            print_agy(data)

        rendered = output.getvalue()
        self.assertIn("Daily Limit", rendered)
        self.assertIn("Flexible Limit", rendered)
        self.assertIn("burn     -", rendered)

    def test_print_claude_keeps_semantic_session_without_guessing_period(self):
        output = StringIO()
        with redirect_stdout(output):
            print_claude({"session": {"pct": 25, "resets_at": None}})

        rendered = output.getvalue()
        self.assertIn("Session", rendered)
        self.assertIn("25.0% used", rendered)
        self.assertIn("burn     -", rendered)

    def test_print_claude_reports_unavailable_without_stale_buckets(self):
        output = StringIO()
        with redirect_stdout(output):
            print_claude({
                "status": "unavailable",
                "unavailable": {
                    "hint": "no active subscription or organization OAuth disabled",
                },
                "session": {"pct": 25, "resets_at": None},
            })

        rendered = output.getvalue()
        self.assertIn(
            "claude  unavailable: no active subscription or organization OAuth disabled",
            rendered,
        )
        self.assertNotIn("Session", rendered)
        self.assertNotIn("25.0%", rendered)


if __name__ == "__main__":
    unittest.main()
