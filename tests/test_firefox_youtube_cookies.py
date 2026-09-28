from __future__ import annotations

import importlib.util
import sqlite3
import time
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "firefox_youtube_cookies.py"
SPEC = importlib.util.spec_from_file_location("firefox_youtube_cookies", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_export_filters_and_formats_cookies(tmp_path: Path, capsys) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    connection = sqlite3.connect(profile / "cookies.sqlite")
    connection.execute(
        """
        CREATE TABLE moz_cookies (
            host TEXT,
            path TEXT,
            isSecure INTEGER,
            expiry INTEGER,
            name TEXT,
            value TEXT,
            isHttpOnly INTEGER
        )
        """
    )
    future = int(time.time()) + 3600
    past = int(time.time()) - 3600
    connection.executemany(
        "INSERT INTO moz_cookies VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (".youtube.com", "/", 1, future, "SID", "youtube-value", 1),
            ("accounts.google.com", "/", 1, 0, "SSID", "google-value", 0),
            (".youtube.com", "/", 1, past, "EXPIRED", "old", 0),
            ("example.com", "/", 0, future, "OTHER", "ignored", 0),
        ],
    )
    connection.commit()
    connection.close()

    assert MODULE.export_cookies(profile) == 2
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "# Netscape HTTP Cookie File"
    assert set(lines[1:]) == {
        "accounts.google.com\tFALSE\t/\tTRUE\t0\tSSID\tgoogle-value",
        f"#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t{future}\tSID\tyoutube-value",
    }
    assert "EXPIRED" not in "\n".join(lines)
    assert "OTHER" not in "\n".join(lines)
