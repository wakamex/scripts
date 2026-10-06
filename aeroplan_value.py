#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "fast-flights>=3.0",
#   "typing_extensions",
# ]
# ///
"""Price a flight in Aeroplan points and in cash, and report what each point is worth.

The points side runs Aeroplan's award search in a signed-in Chrome profile on the
Windows PC, through the Aeroplan bridge extension and its native host
(aeroplan_bridge/README.md), reached over an SSH tunnel to the `windows` host. The
cash side comes from Google Flights. Each itinerary that appears in both is valued as

    cents per point = (cash fare - award taxes and fees) / points * 100

and every check is appended to a CSV log. Each run also keeps, in its own folder under
~/.local/share/aeroplan_value/runs/, the query and tool commit (meta.json), Air Canada's raw award
responses (award.json), Google's raw results data (cash_raw.json), the parsed cash fares (cash.json)
and the computed results (results.json).

Sign the bridge in when its session expires with --sign-in: it asks Aeroplan to email the one-time
code and reads it from Gmail with the app password git send-email uses. --code enters a code by hand.

Examples:
  aeroplan_value.py YOW YVR 2026-11-18
  aeroplan_value.py YOW YVR 2026-11-18 --adults 2 --target 1.6
  aeroplan_value.py --status
  aeroplan_value.py --history
"""

from __future__ import annotations

import argparse
import os
import re
import csv
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

BRIDGE_HOST = "windows"  # ssh alias of the PC running the bridge
BRIDGE_PORT = 47820
DATA = Path.home() / ".local/share/aeroplan_value"
LOG = DATA / "checks.csv"
RUNS = DATA / "runs"
LOG_FIELDS = ["checked", "origin", "destination", "date", "adults", "flights", "fare", "cabin",
              "points", "taxes", "cash", "cents_per_point", "run"]
# Google Flights' cheapest fare once Basic economy is excluded is Air Canada's Standard fare, so only
# Standard award prices are compared with it. Flex, Latitude, Premium Economy and Business award
# prices are listed with --all but get no cash comparison.
COMPARABLE_FARE = "STANDARD"
# For business, Google's cheapest business fare is compared with Air Canada's Business Standard award
# (EXECSTAND); itineraries that mix business and economy segments are not compared.
COMPARABLE_FARES = {"economy": COMPARABLE_FARE, "business": "EXECSTAND"}
CABINS = {"eco": "economy", "ecoPremium": "premium", "premium": "premium", "business": "business", "first": "first"}


@dataclass
class Segment:
    origin: str
    destination: str
    departs: str  # local "YYYY-MM-DDTHH:MM"
    flight: str = ""  # "AC123" when known


@dataclass
class Itinerary:
    segments: list[Segment]
    cabin: str = ""
    fare: str = ""  # Air Canada fare family, e.g. STANDARD, FLEX, LATITUDE, EXECSTAND
    mixed_cabin: bool = False
    points: int | None = None
    taxes: float | None = None
    cash: float | None = None
    extra: dict = field(default_factory=dict)

    def key(self):
        return tuple((s.origin, s.departs) for s in self.segments)

    def label(self):
        parts = [s.flight or f"{s.origin}-{s.destination}" for s in self.segments]
        return f"{' + '.join(parts)} dep {self.segments[0].departs[11:]}"


def cents_per_point(cash, taxes, points):
    """Value of one point in cents: the cash the points replace, per point."""
    if not points or cash is None or taxes is None:
        return None
    return (cash - taxes) / points * 100


# ---------------------------------------------------------------- award side

def parse_flight_id(flight_id):
    """Parse "SEG-AC3-YVRNRT-2026-10-19-1235", Air Canada's segment id, when the dictionary lacks it."""
    m = re.match(r"^SEG-([A-Z0-9]{2})(\d+[A-Z]?)-([A-Z]{3})([A-Z]{3})-(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})$", flight_id or "")
    if not m:
        return None
    airline, number, origin, destination, day, hh, mm = m.groups()
    return Segment(origin, destination, f"{day}T{hh}:{mm}", f"{airline}{number}")


def parse_award_response(body):
    """Turn one Air Canada air-bounds response into itineraries with points and taxes."""
    dictionaries = body.get("dictionaries") or {}
    flights = dictionaries.get("flight") or {}
    currencies = dictionaries.get("currency") or {}
    itineraries = []
    for group in (body.get("data") or {}).get("airBoundGroups") or []:
        segments = []
        for seg in (group.get("boundDetails") or {}).get("segments") or []:
            info = flights.get(seg.get("flightId"))
            if info:
                dep, arr = info.get("departure") or {}, info.get("arrival") or {}
                segments.append(Segment(dep.get("locationCode", ""), arr.get("locationCode", ""),
                                        (dep.get("dateTime") or "")[:16],
                                        f"{info.get('marketingAirlineCode', '')}{info.get('marketingFlightNumber', '')}"))
            elif parsed := parse_flight_id(seg.get("flightId")):
                segments.append(parsed)
        for bound in group.get("airBounds") or []:
            prices = bound.get("prices") or {}
            miles = (prices.get("convertedMiles")
                     or ((prices.get("unitPrices") or [{}])[0].get("milesConversion") or {}).get("convertedMiles")
                     or (prices.get("milesConversion") or {}).get("convertedMiles") or {})
            points = miles.get("base")
            if points is None:
                continue
            totals = (prices.get("totalPrices") or [{}])[0]
            taxes = miles.get("totalTaxes", totals.get("totalTaxes"))
            # Amounts are integers in the currency's minor unit.
            decimals = (currencies.get(totals.get("currencyCode")) or {}).get("decimalPlaces", 2)
            cabin = ((bound.get("availabilityDetails") or [{}])[0]).get("cabin", "")
            itineraries.append(Itinerary(
                segments=segments, cabin=CABINS.get(cabin, cabin), fare=bound.get("fareFamilyCode", ""),
                mixed_cabin=bool(bound.get("isMixedCabin")),
                points=int(points), taxes=None if taxes is None else taxes / 10 ** decimals))
    return itineraries


class BridgeError(Exception):
    pass


def bridge_request(path, payload=None, timeout=180):
    """Call the Windows bridge host through a short-lived SSH tunnel."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        local_port = probe.getsockname()[1]
    tunnel = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-N",
         "-L", f"127.0.0.1:{local_port}:127.0.0.1:{BRIDGE_PORT}", BRIDGE_HOST],
        stdin=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        url = f"http://127.0.0.1:{local_port}{path}"
        data = None if payload is None else json.dumps(payload).encode()
        deadline = time.time() + 15
        while True:
            try:
                request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as e:
                raise BridgeError(json.loads(e.read() or b"{}").get("error", f"HTTP {e.code}")) from None
            except (ConnectionError, urllib.error.URLError) as e:
                if tunnel.poll() is not None:
                    raise BridgeError(f"ssh tunnel to {BRIDGE_HOST} failed: {tunnel.stderr.read().decode().strip()}") from None
                if time.time() > deadline:
                    raise BridgeError(f"bridge host not reachable on {BRIDGE_HOST}:{BRIDGE_PORT} ({e}); "
                                      "is the Aeroplan Chrome profile open?") from None
                time.sleep(0.5)
    finally:
        tunnel.terminate()


def fetch_award(origin, destination, day, adults, run_dir):
    """Run the award search in the bridge's signed-in browser and return the raw responses."""
    log("asking the Aeroplan bridge for award prices")
    try:
        result = bridge_request("/search", {"origin": origin, "destination": destination, "date": day, "adults": adults})
    except BridgeError as e:
        sys.exit(f"error: {e}")
    if result.get("error") == "signed_out":
        log("bridge session expired; signing in again")
        if sign_in() != 0:
            sys.exit("error: automatic sign-in failed; see the bridge host log")
        try:
            result = bridge_request("/search", {"origin": origin, "destination": destination, "date": day, "adults": adults})
        except BridgeError as e:
            sys.exit(f"error: {e}")
    (run_dir / "award.json").write_text(json.dumps(result, indent=1))
    if not result.get("ok"):
        hint = {"signed_out": "automatic sign-in did not hold; try --sign-in",
                "timeout": "the award page did not return results in time"}.get(result.get("error"), "")
        sys.exit(f"error: bridge search failed: {result.get('error')}" + (f"; {hint}" if hint else ""))
    return result.get("responses") or []


GMAIL_USER = "mcosma@gmail.com"


def gmail_password():
    """The Gmail app password, from GMAIL_APP_PASSWORD or the one git send-email uses."""
    if os.environ.get("GMAIL_APP_PASSWORD"):
        return os.environ["GMAIL_APP_PASSWORD"]
    out = subprocess.run(["git", "credential", "fill"], capture_output=True, text=True,
                         input=f"protocol=smtp\nhost=smtp.gmail.com:587\nusername={GMAIL_USER}\n\n",
                         env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    sys.exit("error: no Gmail app password (set GMAIL_APP_PASSWORD or store it for git send-email)")


def gmail_code(since, timeout=240):
    """Wait for an Aeroplan code email received after `since` (epoch seconds) and return its 6-digit code."""
    import email
    import imaplib
    from email.utils import parsedate_to_datetime

    password = gmail_password()
    deadline = time.time() + timeout
    while time.time() < deadline:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        try:
            mail.login(GMAIL_USER, password)
            mail.select("INBOX", readonly=True)
            # The codes come from info@communications.aeroplan.com ("Verification code to access your account").
            _, data = mail.search(None, f'(FROM "aeroplan.com" SINCE {time.strftime("%d-%b-%Y", time.gmtime(since - 86400))})')
            for msg_id in reversed(data[0].split()[-10:]):
                _, parts = mail.fetch(msg_id, "(BODY.PEEK[])")
                message = email.message_from_bytes(parts[0][1])
                if parsedate_to_datetime(message["Date"]).timestamp() < since - 60:
                    continue
                body = " ".join(p.get_payload(decode=True).decode(errors="ignore") for p in message.walk()
                                if p.get_content_type() in ("text/plain", "text/html"))
                if m := re.search(r"(?<![\d#])(\d{6})(?!\d)", re.sub(r"<[^>]+>", " ", body)):
                    return m.group(1)
        finally:
            mail.logout()
        time.sleep(10)
    sys.exit("error: no Aeroplan code email arrived")


def sign_in():
    """Sign the bridge browser in, asking for the one-time code by email and reading it from Gmail."""
    # The bridge browser fills in its saved login; AEROPLAN_USER/AEROPLAN_PASSWORD override it.
    payload = {"user": os.environ.get("AEROPLAN_USER", ""), "password": os.environ.get("AEROPLAN_PASSWORD", ""),
               "emailCode": True}
    started = time.time()
    try:
        result = bridge_request("/signin", payload, timeout=90)
        if result.get("state") == "email_code_sent":
            log("code requested by email; waiting for it in Gmail")
            code = gmail_code(started)
            log("entering the code")
            result = bridge_request("/signin", {"code": code}, timeout=90)
    except BridgeError as e:
        sys.exit(f"error: {e}")
    log("signed in" if result.get("ok") else f"sign-in did not finish: {json.dumps(result)}")
    return 0 if result.get("ok") else 1


def log(message):
    print(f"[{datetime.now():%H:%M:%S}] {message}", file=sys.stderr, flush=True)


# ----------------------------------------------------------------- cash side

def google_time(value):
    """Google omits zero components: [8] is 08:00 and [None, 31] is 00:31."""
    hour, minute = [*(value or []), None, None][:2]
    return hour or 0, minute or 0


def parse_google_flights(payload):
    """Itineraries with a total cash price from Google Flights' embedded results data.

    payload[2] holds the "best" itineraries and payload[3] the rest; each segment carries the
    marketing flight number in field 22. Entries without a price are skipped.
    """
    itineraries = []
    for block in (payload[2], payload[3]):
        for entry in (block or [None])[0] or []:
            price = (entry[1] or [[None, None]])[0]
            if not price or len(price) < 2 or price[1] is None:
                continue
            segments = []
            for seg in entry[0][2]:
                hour, minute = google_time(seg[8])
                year, month, day = seg[20]
                number = seg[22] or []
                segments.append(Segment(seg[3], seg[6], f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}",
                                        f"{number[0]}{number[1]}" if len(number) > 1 else ""))
            itineraries.append(Itinerary(segments=segments, cash=float(price[1])))
    return itineraries


def fetch_cash(origin, destination, day, adults, include_basic=False, run_dir=None, seat="economy", raw_name="cash_raw.json"):
    """Google Flights fares for the same day, as itineraries with a total cash price in CAD."""
    import fast_flights as ff

    query = ff.create_query(
        flights=[ff.FlightQuery(date=day, from_airport=origin, to_airport=destination)],
        trip="one-way", seat=seat, passengers=ff.Passengers(adults=adults),
        currency="CAD", language="en-US", exclude_basic_economy=not include_basic)
    html = ff.fetch_flights_html(query)
    script = re.search(r'<script class="ds:1"[^>]*>(.*?)</script>', html, re.S)
    if not script:
        sys.exit("error: Google Flights returned no results data")
    payload = json.loads(script.group(1).split("data:", 1)[1].rsplit(",", 1)[0])
    if run_dir:
        # Google's full results data, so later parser changes can re-read this run.
        (run_dir / raw_name).write_text(json.dumps({"query_url": query.url(), "payload": payload}))
    return parse_google_flights(payload)


def match(awards, cash, fare=COMPARABLE_FARE):
    """Attach the cheapest cash fare to Standard award prices for the same flights (same airports and departures)."""
    cheapest = {}
    for c in cash:
        if c.key() not in cheapest or c.cash < cheapest[c.key()]:
            cheapest[c.key()] = c.cash
    for a in awards:
        a.cash = cheapest.get(a.key()) if a.fare == fare and not a.mixed_cabin else None
    return awards


# -------------------------------------------------------------------- output

def as_dict(it):
    return {**it.__dict__, "segments": [s.__dict__ for s in it.segments],
            "cents_per_point": cents_per_point(it.cash, it.taxes, it.points)}


def log_checks(rows, args, run_dir):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    new = not LOG.exists()
    with LOG.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            writer.writeheader()
        for it in rows:
            cpp = cents_per_point(it.cash, it.taxes, it.points)
            writer.writerow({
                "checked": datetime.now().isoformat(timespec="seconds"), "origin": args.origin,
                "destination": args.destination, "date": args.date, "adults": args.adults,
                "flights": it.label(), "fare": it.fare, "cabin": it.cabin, "points": it.points, "taxes": it.taxes,
                "cash": it.cash, "cents_per_point": None if cpp is None else round(cpp, 2), "run": run_dir.name,
            })


def print_table(rows, target, fare=COMPARABLE_FARE):
    print(f"{'flights':38s} {'fare':9s} {'points':>8s} {'taxes':>8s} {'cash':>8s} {'c/pt':>6s}  verdict")
    for it in rows:
        cpp = cents_per_point(it.cash, it.taxes, it.points)
        cash = "-" if it.cash is None else f"{it.cash:.0f}"
        value = "-" if cpp is None else f"{cpp:.2f}"
        verdict = ("no cash match" if it.fare == fare and not it.mixed_cabin else "not compared") if cpp is None else ("use points" if cpp >= target else "pay cash")
        print(f"{it.label()[:38]:38s} {it.fare[:9]:9s} {it.points:>8,} {it.taxes or 0:>8.2f} {cash:>8s} {value:>6s}  {verdict}")


def show_history():
    if not LOG.exists():
        sys.exit("no checks logged yet")
    print(LOG.read_text(), end="")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("origin", nargs="?", help="IATA code, e.g. YOW")
    parser.add_argument("destination", nargs="?", help="IATA code, e.g. YVR")
    parser.add_argument("date", nargs="?", help="departure date, YYYY-MM-DD")
    parser.add_argument("--adults", type=int, default=1)
    parser.add_argument("--target", type=float, default=2.0, help="cents per point worth using points for (default 2.0)")
    parser.add_argument("--include-basic", action="store_true", help="compare against Basic economy fares too")
    parser.add_argument("--cabin", choices=sorted(COMPARABLE_FARES), default="economy",
                        help="cabin to compare: economy (Standard award vs non-Basic fare) or business "
                             "(Business Standard award vs cheapest business fare; first-class fares are saved too)")
    parser.add_argument("--all", action="store_true", help="also list award itineraries with no matching cash fare")
    parser.add_argument("--json", action="store_true", help="print results as JSON")
    parser.add_argument("--history", action="store_true", help=f"print the log of past checks ({LOG})")
    parser.add_argument("--status", action="store_true", help="check that the Windows bridge and its browser tab are up")
    parser.add_argument("--sign-in", action="store_true",
                        help="sign the bridge browser in with its saved login, reading the emailed code from Gmail")
    parser.add_argument("--code", help="enter a one-time code from Aeroplan by hand")
    args = parser.parse_args(argv)

    if args.history:
        return show_history()
    if args.code:
        try:
            print(json.dumps(bridge_request("/signin", {"code": args.code}, timeout=90), indent=1))
        except BridgeError as e:
            sys.exit(f"error: {e}")
        return 0
    if args.sign_in:
        return sign_in()
    if args.status:
        try:
            print(json.dumps(bridge_request("/status", timeout=30), indent=1))
        except BridgeError as e:
            sys.exit(f"error: {e}")
        return 0
    if not (args.origin and args.destination and args.date):
        parser.error("origin, destination and date are required")
    args.origin, args.destination = args.origin.upper(), args.destination.upper()
    date.fromisoformat(args.date)

    cabin_tag = "" if args.cabin == "economy" else f"_{args.cabin}"
    run_dir = RUNS / f"{datetime.now():%Y%m%dT%H%M%S}_{args.origin}-{args.destination}_{args.date}_{args.adults}ad{cabin_tag}"
    run_dir.mkdir(parents=True, mode=0o700)
    commit = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "status", "--porcelain", "--", Path(__file__).name],
                           capture_output=True, text=True).stdout.strip()
    (run_dir / "meta.json").write_text(json.dumps({
        "started": datetime.now().isoformat(timespec="seconds"), "origin": args.origin, "destination": args.destination,
        "date": args.date, "adults": args.adults, "include_basic": args.include_basic, "target": args.target,
        "cabin": args.cabin, "comparable_fare": COMPARABLE_FARES[args.cabin], "aeroplan_value_commit": commit + ("+dirty" if dirty else "")}, indent=1))
    raw = fetch_award(args.origin, args.destination, args.date, args.adults, run_dir)
    awards = [it for body in raw for it in parse_award_response(body)]
    log(f"parsed {len(awards)} award options; fetching cash fares")
    cash = fetch_cash(args.origin, args.destination, args.date, args.adults, args.include_basic, run_dir, seat=args.cabin)
    if args.cabin == "business":
        # Saved for reference: on routes where an airline sells a separate first cabin, its fares show here.
        first = fetch_cash(args.origin, args.destination, args.date, args.adults, True, run_dir, seat="first",
                           raw_name="cash_first_raw.json")
        (run_dir / "cash_first.json").write_text(json.dumps([as_dict(it) for it in first], indent=1))
    (run_dir / "cash.json").write_text(json.dumps([as_dict(it) for it in cash], indent=1))
    rows = match(awards, cash, COMPARABLE_FARES[args.cabin])
    rows.sort(key=lambda it: -(cents_per_point(it.cash, it.taxes, it.points) or -1))
    shown = rows if args.all else [it for it in rows if it.cash is not None]
    log_checks(shown, args, run_dir)
    (run_dir / "results.json").write_text(json.dumps([as_dict(it) for it in rows], indent=1))
    if args.json:
        print(json.dumps([as_dict(it) for it in shown], indent=1))
    else:
        print(f"{args.origin}-{args.destination} {args.date}, {args.adults} adult(s); "
              f"{len(awards)} award options, {len(cash)} cash fares, {len(shown)} shown")
        print_table(shown, args.target, COMPARABLE_FARES[args.cabin])
    return 0


if __name__ == "__main__":
    sys.exit(main())
