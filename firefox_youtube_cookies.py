#!/usr/bin/env python3
"""Export active Google and YouTube Firefox cookies in Netscape format."""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path


def export_cookies(profile: Path) -> int:
    database = profile / "cookies.sqlite"
    if not database.is_file():
        raise SystemExit(f"Firefox cookie database not found: {database}")

    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            """
            SELECT host, path, isSecure, expiry, name, value, isHttpOnly
            FROM moz_cookies
            WHERE (
                host = 'youtube.com'
                OR host LIKE '%.youtube.com'
                OR host = 'google.com'
                OR host LIKE '%.google.com'
            )
              AND (expiry = 0 OR expiry > ?)
            ORDER BY host, path, name
            """,
            (int(time.time()),),
        ).fetchall()
    finally:
        connection.close()

    if not rows:
        raise SystemExit("No active Google or YouTube cookies found")

    print("# Netscape HTTP Cookie File")
    for host, path, secure, expiry, name, value, http_only in rows:
        domain = f"#HttpOnly_{host}" if http_only else host
        include_subdomains = "TRUE" if host.startswith(".") else "FALSE"
        fields = (
            domain,
            include_subdomains,
            path,
            "TRUE" if secure else "FALSE",
            str(expiry),
            name,
            value,
        )
        if any("\t" in field or "\n" in field or "\r" in field for field in fields):
            raise SystemExit("Cookie contains an unsupported control character")
        print("\t".join(fields))
    return len(rows)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: firefox_youtube_cookies.py FIREFOX_PROFILE")
    export_cookies(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
