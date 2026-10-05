# ruff: noqa: D101, D102

import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "aeroplan_value.py"
SPEC = importlib.util.spec_from_file_location("aeroplan_value", SCRIPT_PATH)
av = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = av  # dataclasses resolve annotations through sys.modules
SPEC.loader.exec_module(av)


def itinerary(departs, **kw):
    return av.Itinerary(segments=[av.Segment("YOW", "YVR", departs)], **kw)


class ValueTests(unittest.TestCase):
    def test_cents_per_point_subtracts_award_taxes(self):
        self.assertAlmostEqual(av.cents_per_point(450, 80, 15000), 2.4667, places=3)

    def test_cents_per_point_needs_all_inputs(self):
        self.assertIsNone(av.cents_per_point(None, 80, 15000))
        self.assertIsNone(av.cents_per_point(450, 80, 0))


class MatchTests(unittest.TestCase):
    def test_attaches_cheapest_cash_fare_for_same_departures(self):
        awards = [itinerary("2026-11-18T07:45", fare="STANDARD", points=20000, taxes=40.0),
                  itinerary("2026-11-18T07:45", fare="LATITUDE", points=40000, taxes=40.0),
                  itinerary("2026-11-18T09:00", fare="STANDARD", points=1)]
        cash = [itinerary("2026-11-18T07:45", cash=400.0), itinerary("2026-11-18T07:45", cash=348.0)]
        matched = av.match(awards, cash)
        self.assertEqual(matched[0].cash, 348.0)
        self.assertIsNone(matched[1].cash)  # only Standard awards compare with Google's cheapest non-Basic fare
        self.assertIsNone(matched[2].cash)


class ParseTests(unittest.TestCase):
    def test_flight_id_gives_segment_when_dictionary_lacks_it(self):
        seg = av.parse_flight_id("SEG-AC3-YVRNRT-2026-10-19-1235")
        self.assertEqual((seg.flight, seg.origin, seg.destination, seg.departs),
                         ("AC3", "YVR", "NRT", "2026-10-19T12:35"))
        self.assertIsNone(av.parse_flight_id("garbage"))


class AwardResponseTests(unittest.TestCase):
    """Real Air Canada award response captured through the bridge (one itinerary, trimmed)."""

    FIXTURE = Path(__file__).resolve().parent / "fixtures" / "aeroplan_award_response.json"

    def test_parses_points_taxes_and_fare_family(self):
        rows = av.parse_award_response(json.loads(self.FIXTURE.read_text()))
        standard = next(r for r in rows if r.fare == "STANDARD")
        self.assertEqual([s.flight for s in standard.segments], ["AC353", "AC209"])
        self.assertEqual(standard.segments[0].departs, "2026-11-18T07:50")
        self.assertEqual((standard.points, standard.taxes, standard.cabin), (17528, 55.89, "economy"))


class GoogleFlightsTests(unittest.TestCase):
    """Real Google Flights results data for YOW-SFO (two best, two other and one unpriced itinerary)."""

    FIXTURE = Path(__file__).resolve().parent / "fixtures" / "google_flights_payload.json"

    def test_reads_both_lists_with_flight_numbers_and_skips_unpriced(self):
        rows = av.parse_google_flights(json.loads(self.FIXTURE.read_text()))
        self.assertEqual(len(rows), 4)
        self.assertEqual([s.flight for s in rows[0].segments], ["WS589", "DL5073", "DL469"])
        self.assertEqual((rows[0].segments[0].departs, rows[0].cash), ("2026-11-18T05:30", 391.0))


class BridgeHostTests(unittest.TestCase):
    """Drives the Windows host over Chrome's native messaging framing, playing the extension's side."""

    HOST = SCRIPT_PATH.parent / "aeroplan_bridge" / "host" / "aeroplan_bridge_host.py"

    @staticmethod
    def frame(message):
        data = json.dumps(message).encode()
        return struct.pack("<I", len(data)) + data

    @staticmethod
    def read_frame(stream):
        (length,) = struct.unpack("<I", stream.read(4))
        return json.loads(stream.read(length))

    def test_search_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = subprocess.Popen([sys.executable, "-u", str(self.HOST)], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, env={**os.environ, "LOCALAPPDATA": tmp})
            try:
                host.stdin.write(self.frame({"type": "hello", "version": "test"}))
                host.stdin.flush()
                time.sleep(0.5)
                reply = {}

                def post():
                    body = json.dumps({"origin": "YOW", "destination": "YVR", "date": "2026-11-18"}).encode()
                    request = urllib.request.Request("http://127.0.0.1:47820/search", data=body)
                    with urllib.request.urlopen(request, timeout=20) as response:
                        reply.update(json.loads(response.read()))

                client = threading.Thread(target=post)
                client.start()
                job = self.read_frame(host.stdout)
                self.assertEqual((job["type"], job["origin"], job["date"]), ("search", "YOW", "2026-11-18"))
                host.stdin.write(self.frame({"type": "result", "id": job["id"], "ok": True, "responses": [{"data": {}}]}))
                host.stdin.flush()
                client.join(20)
                self.assertEqual(reply, {"type": "result", "id": job["id"], "ok": True, "responses": [{"data": {}}]})
            finally:
                host.stdin.close()
                host.wait(10)
                host.stdout.close()


class CliTests(unittest.TestCase):
    def test_help_runs(self):
        result = subprocess.run(["python3", str(SCRIPT_PATH), "--help"], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("cents per point", result.stdout)


if __name__ == "__main__":
    unittest.main()
