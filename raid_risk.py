#!/usr/bin/env python3
"""Estimate the risk of losing a Linux md RAID 5 array, from live drive data.

Reads the array's member disks from /proc/mdstat and each disk's age and bad-sector count from
UDisks (no root needed). Combines three risks:

- a drive failing within the next year, from an age-based annual failure rate;
- an unreadable sector during the rebuild that follows, from the drives' rated error rate,
  the same formula as https://magj.github.io/raid-failure/;
- a second drive failing during that rebuild.

Usage: raid_risk.py [md127]
"""

import math
import re
import subprocess
import sys
from pathlib import Path

# Rated unrecoverable read errors: one per this many bits read. Consumer and NAS drives are
# usually rated 1e14, enterprise drives 1e15.
URE_BITS = {"WD30EFRX": 1e14, "ST4000DM000": 1e14}
DEFAULT_URE_BITS = 1e14

# Approximate annual failure rate by age, after Backblaze's drive statistics: failures are
# lowest in years 1 to 4 and climb after about 5 years.
AFR_BY_AGE = [(1, 0.015), (4, 0.01), (5, 0.02), (6, 0.04), (8, 0.06), (99, 0.08)]

REBUILD_MB_PER_S = 100


def members(md: str) -> list[str]:
    for line in Path("/proc/mdstat").read_text().splitlines():
        if line.startswith(md + " "):
            if "raid5" not in line:
                sys.exit(f"{md} is not RAID 5")
            return sorted(re.findall(r"(sd[a-z]+)\d*\[", line))
    sys.exit(f"{md} not found in /proc/mdstat")


def drive_info(disk: str) -> dict:
    block = subprocess.run(["udisksctl", "info", "-b", f"/dev/{disk}"], capture_output=True, text=True, check=True).stdout
    drive = re.search(r"Drive:\s+'(/org/freedesktop/UDisks2/drives/[^']+)'", block).group(1)
    info = subprocess.run(["udisksctl", "info", "-p", drive.split("/org/freedesktop/UDisks2/")[1]],
                          capture_output=True, text=True, check=True).stdout
    field = lambda name: re.search(rf"^\s*{name}:\s+(.+)$", info, re.M).group(1).strip()
    size = int(Path(f"/sys/block/{disk}/size").read_text()) * 512
    return {
        "disk": disk,
        "model": field("Model"),
        "years": int(field("SmartPowerOnSeconds")) / 3600 / 24 / 365.25,
        "bad_sectors": int(field("SmartNumBadSectors")),
        "failing": field("SmartFailing") == "true",
        "bytes": size,
    }


def afr(years: float) -> float:
    return next(rate for limit, rate in AFR_BY_AGE if years < limit)


def ure_bits(model: str) -> float:
    return next((bits for key, bits in URE_BITS.items() if key in model.replace(" ", "")), DEFAULT_URE_BITS)


def main() -> None:
    md = sys.argv[1] if len(sys.argv) > 1 else "md127"
    drives = [drive_info(d) for d in members(md)]
    n = len(drives)
    print(f"{md}: RAID 5, {n} drives\n")
    print(f"{'disk':5} {'model':24} {'age (y)':>8} {'bad sectors':>12} {'AFR':>6}")
    for d in drives:
        flag = "  SMART FAILING" if d["failing"] else ""
        print(f"{d['disk']:5} {d['model']:24} {d['years']:8.1f} {d['bad_sectors']:12d} {afr(d['years']):6.1%}{flag}")

    p_year = 1 - math.prod(1 - afr(d["years"]) for d in drives)
    size = max(d["bytes"] for d in drives)
    rebuild_hours = size / (REBUILD_MB_PER_S * 1e6) / 3600
    bits_read = (n - 1) * size * 8
    rate = min(ure_bits(d["model"]) for d in drives)
    p_ure = 1 - math.exp(-bits_read / rate)
    mean_afr = sum(afr(d["years"]) for d in drives) / n
    p_second = 1 - (1 - mean_afr * rebuild_hours / 8766) ** (n - 1)

    print(f"\nChance a drive fails in the next year:           {p_year:6.1%}")
    print(f"Rebuild reads {bits_read / 8e12:.1f} TB, about {rebuild_hours:.0f} h at {REBUILD_MB_PER_S} MB/s")
    print(f"Chance of an unreadable sector during rebuild:   {p_ure:6.1%}  (rated 1 per {rate:.0e} bits; real drives usually do better)")
    print(f"Chance a second drive dies during rebuild:       {p_second:6.3%}")
    print(f"Chance in the next year of a failure plus a rebuild error: {p_year * p_ure:6.1%}")
    bad = [d["disk"] for d in drives if d["bad_sectors"] or d["failing"]]
    if bad:
        print(f"\nDrives with bad sectors or failing SMART: {', '.join(bad)}. Bad sectors make these drives far more likely to fail; replace them first.")


if __name__ == "__main__":
    main()
