#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "beautifulsoup4>=4.13.4",
#   "html2text>=2025.4.15",
#   "requests>=2.32.4",
# ]
# ///
"""Resolve, validate, download, and mux a Reddit-hosted video."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote
from xml.etree import ElementTree

import requests

from extract_reddit import (
    USER_AGENT,
    ExtractionError,
    reddit_json_url,
    request_json,
    resolve_reddit_url,
)

HTTP_OK = 200


@dataclass(frozen=True)
class MediaSource:
    """Canonical Reddit source and its resolved DASH endpoint."""

    canonical_url: str
    media_id: str
    manifest_url: str
    resolver: str


@dataclass(frozen=True)
class ManifestInfo:
    """Validated properties read from a DASH manifest."""

    duration_seconds: float | None
    has_audio: bool
    has_video: bool
    stream_names: tuple[str, ...]


def media_id_from_url(url: str) -> str | None:
    match = re.search(r"https?://v\.redd\.it/([a-z0-9]+)", url)
    return match.group(1) if match else None


def reddit_video_urls(value: object) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        reddit_video = value.get("reddit_video")
        if isinstance(reddit_video, dict):
            for key in ("dash_url", "fallback_url", "hls_url"):
                candidate = reddit_video.get(key)
                if isinstance(candidate, str):
                    found.append(candidate.replace("&amp;", "&"))
        for child in value.values():
            found.extend(reddit_video_urls(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(reddit_video_urls(child))
    return found


def resolve_with_rapidsave(session: Any, canonical_url: str, timeout: float) -> str:
    url = f"https://rapidsave.com/info?url={quote(canonical_url, safe='')}"
    response = session.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        allow_redirects=True,
    )
    if response.status_code != HTTP_OK:
        raise ExtractionError(f"Rapidsave returned HTTP {response.status_code}")
    media_ids = sorted(set(re.findall(r"v\.redd\.it/([a-z0-9]+)", response.text)))
    if len(media_ids) != 1:
        raise ExtractionError(f"Rapidsave returned {len(media_ids)} unique v.redd.it media IDs")
    return media_ids[0]


def resolve_media(url: str, timeout: float = 20.0) -> MediaSource:
    session = requests.Session()
    canonical = resolve_reddit_url(session, url, timeout)
    try:
        payload = request_json(session, reddit_json_url(canonical), timeout)
        urls = reddit_video_urls(payload)
        media_ids = {item for candidate in urls if (item := media_id_from_url(candidate))}
        if len(media_ids) == 1:
            media_id = media_ids.pop()
            manifest = next(
                (candidate for candidate in urls if "DASHPlaylist.mpd" in candidate and media_id in candidate),
                f"https://v.redd.it/{media_id}/DASHPlaylist.mpd",
            )
            return MediaSource(canonical, media_id, manifest, "old Reddit JSON")
    except ExtractionError as error:
        print(f"old Reddit JSON unavailable: {error}", file=sys.stderr)

    media_id = resolve_with_rapidsave(session, canonical, timeout)
    return MediaSource(
        canonical,
        media_id,
        f"https://v.redd.it/{media_id}/DASHPlaylist.mpd",
        "Rapidsave",
    )


def parse_iso_duration(value: str | None) -> float | None:
    if not value:
        return None
    match = re.fullmatch(
        r"P(?:(?P<days>\d+(?:\.\d+)?)D)?T"
        r"(?:(?P<hours>\d+(?:\.\d+)?)H)?"
        r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
        r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?",
        value,
    )
    if not match:
        return None
    values = {key: float(number or 0) for key, number in match.groupdict().items()}
    return values["days"] * 86400 + values["hours"] * 3600 + values["minutes"] * 60 + values["seconds"]


def parse_manifest(xml: bytes) -> ManifestInfo:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as error:
        raise ExtractionError(f"invalid DASH XML: {error}") from error
    if not root.tag.endswith("MPD"):
        raise ExtractionError("DASH response root is not MPD")

    adaptations = [item for item in root.iter() if item.tag.endswith("AdaptationSet")]
    has_audio = any(
        item.get("contentType") == "audio" or (item.get("mimeType") or "").startswith("audio/") for item in adaptations
    )
    has_video = any(
        item.get("contentType") == "video" or (item.get("mimeType") or "").startswith("video/") for item in adaptations
    )
    if not has_video:
        raise ExtractionError("DASH manifest has no video adaptation set")
    names = tuple(
        sorted(
            {
                (element.text or "").strip()
                for element in root.iter()
                if element.tag.endswith("BaseURL") and (element.text or "").strip()
            }
        )
    )
    return ManifestInfo(
        parse_iso_duration(root.get("mediaPresentationDuration")),
        has_audio,
        has_video,
        names,
    )


def fetch_manifest(source: MediaSource, timeout: float) -> tuple[bytes, ManifestInfo]:
    response = requests.get(
        source.manifest_url,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        allow_redirects=True,
    )
    if response.status_code != HTTP_OK:
        raise ExtractionError(f"DASH manifest returned HTTP {response.status_code}")
    return response.content, parse_manifest(response.content)


def validate_download(path: Path, expect_audio: bool) -> dict[str, object]:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise ExtractionError("ffprobe is required to validate downloaded media")
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            os.fspath(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise ExtractionError(f"ffprobe rejected download: {completed.stderr.strip()}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ExtractionError("ffprobe returned invalid JSON") from error
    streams = result.get("streams", [])
    kinds = {stream.get("codec_type") for stream in streams if isinstance(stream, dict)}
    if "video" not in kinds:
        raise ExtractionError("download has no video stream")
    if expect_audio and "audio" not in kinds:
        raise ExtractionError("download has no audio stream advertised by the manifest")
    return result


def download_video(source: MediaSource, destination: Path, expect_audio: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=f".{destination.name}.") as temporary:
        template = Path(temporary) / "download.%(ext)s"
        command = [
            "uvx",
            "yt-dlp",
            "--no-playlist",
            "--merge-output-format",
            "mp4",
            "--output",
            os.fspath(template),
            source.manifest_url,
        ]
        completed = subprocess.run(command, check=False)
        if completed.returncode:
            raise ExtractionError(f"yt-dlp exited with status {completed.returncode}")
        candidates = [item for item in Path(temporary).glob("download.*") if item.is_file()]
        if len(candidates) != 1:
            raise ExtractionError(f"yt-dlp produced {len(candidates)} output files")
        validate_download(candidates[0], expect_audio)
        os.replace(candidates[0], destination)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--resolve-only", action="store_true")
    parser.add_argument("--timeout", type=float, default=20.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        source = resolve_media(args.url, timeout=args.timeout)
        _, manifest = fetch_manifest(source, timeout=args.timeout)
        details = {"media": asdict(source), "manifest": asdict(manifest)}
        if args.resolve_only:
            print(json.dumps(details, indent=2))
            return 0
        destination = (args.output or Path(f"reddit-{source.media_id}.mp4")).resolve()
        download_video(source, destination, manifest.has_audio)
        print(json.dumps({**details, "published": os.fspath(destination)}, indent=2))
    except (OSError, ExtractionError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
