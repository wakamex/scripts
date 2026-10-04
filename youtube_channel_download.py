#!/usr/bin/env python3
"""Download every public video from a YouTube channel with bounded concurrency."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

YT_DLP_VERSION = "2026.8.19"
DEFAULT_WORKERS = 3
SECONDS_PER_MINUTE = 60


@dataclass(frozen=True)
class Video:
    """One public channel upload returned by YouTube."""

    video_id: str
    title: str
    url: str
    upload_date: str
    duration: float | None
    availability: str


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of downloading one video."""

    video_id: str
    status: str
    elapsed_seconds: float
    error: str = ""


@dataclass(frozen=True)
class ManifestContext:
    """Stable metadata shared by every manifest refresh."""

    output_dir: Path
    source_url: str
    channel_url: str
    audio_only: bool
    videos: list[Video]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str, max_length: int = 100) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return (slug or "untitled")[:max_length].rstrip("-")


def is_video_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    return host == "youtu.be" or parsed.path.startswith(("/watch", "/shorts/", "/live/"))


def videos_url(channel_url: str) -> str:
    return channel_url.rstrip("/") if channel_url.rstrip("/").endswith("/videos") else f"{channel_url.rstrip('/')}/videos"


def yt_dlp_command() -> list[str]:
    return [
        "uvx",
        "--no-config",
        "--from",
        f"yt-dlp=={YT_DLP_VERSION}",
        "yt-dlp",
        "--ignore-config",
    ]


def cookie_arguments(cookie_file: Path | None) -> list[str]:
    return ["--cookies", str(cookie_file)] if cookie_file else []


def run_json_command(command: list[str]) -> str:
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"command exited {result.returncode}"
        raise RuntimeError(detail)
    return result.stdout


def resolve_channel_url(source_url: str, cookie_file: Path | None) -> str:
    if not is_video_url(source_url):
        return videos_url(source_url)

    command = [
        *yt_dlp_command(),
        *cookie_arguments(cookie_file),
        "--skip-download",
        "--no-playlist",
        "--dump-single-json",
        source_url,
    ]
    payload = json.loads(run_json_command(command))
    channel_url = payload.get("channel_url") or payload.get("uploader_url")
    if not isinstance(channel_url, str) or not channel_url.startswith("http"):
        raise RuntimeError(f"YouTube metadata did not identify a channel for {source_url}")
    return videos_url(channel_url)


def parse_entries(output: str) -> list[Video]:
    videos: list[Video] = []
    seen: set[str] = set()
    for line in output.splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        video_id = entry.get("id")
        if not isinstance(video_id, str) or not video_id or video_id in seen:
            continue
        seen.add(video_id)
        title = entry.get("title") if isinstance(entry.get("title"), str) else video_id
        url = entry.get("url")
        if not isinstance(url, str) or not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={video_id}"
        duration = entry.get("duration")
        videos.append(
            Video(
                video_id=video_id,
                title=title,
                url=url,
                upload_date=str(entry.get("upload_date") or ""),
                duration=float(duration) if isinstance(duration, (int, float)) else None,
                availability=str(entry.get("availability") or ""),
            )
        )
    return videos


def enumerate_channel(channel_url: str, cookie_file: Path | None) -> list[Video]:
    command = [
        *yt_dlp_command(),
        *cookie_arguments(cookie_file),
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
        channel_url,
    ]
    videos = parse_entries(run_json_command(command))
    if not videos:
        raise RuntimeError(f"YouTube returned no public videos for {channel_url}")
    return videos


def output_filename(video: Video, audio_only: bool) -> str:
    suffix = "mp3" if audio_only else "mp4"
    return f"{video.video_id}__{slugify(video.title)}.{suffix}"


def valid_media(path: Path, audio_only: bool) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    selector = "a:0" if audio_only else "v:0"
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        selector,
        "-show_entries",
        "stream=codec_type",
        "-of",
        "default=nk=1:nw=1",
        str(path),
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    expected = "audio" if audio_only else "video"
    return result.returncode == 0 and expected in result.stdout.splitlines()


def build_download_command(
    video: Video,
    destination: Path,
    audio_only: bool,
    cookie_file: Path | None,
) -> list[str]:
    command = [str(Path(__file__).with_name("yt.sh"))]
    if audio_only:
        command.append("--audio-only")
    if cookie_file:
        command.extend(["--cookies", str(cookie_file)])
    command.extend(["--output", str(destination), video.url])
    return command


def download_video(
    video: Video,
    destination: Path,
    log_path: Path,
    audio_only: bool,
    cookie_file: Path | None,
) -> DownloadResult:
    started = time.monotonic()
    result = subprocess.run(
        build_download_command(video, destination, audio_only, cookie_file),
        check=False,
        capture_output=True,
        text=True,
    )
    log_path.write_text(result.stdout + result.stderr, encoding="utf-8")
    elapsed = time.monotonic() - started
    if result.returncode == 0 and valid_media(destination, audio_only):
        return DownloadResult(video.video_id, "completed", elapsed)
    detail = result.stderr.strip().splitlines()
    error = detail[-1] if detail else f"download exited {result.returncode}"
    return DownloadResult(video.video_id, "failed", elapsed, error)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_manifests(context: ManifestContext, states: dict[str, dict[str, object]]) -> None:
    generated_at = utc_now()
    payload = {
        "schema_version": 1,
        "source_url": context.source_url,
        "channel_url": context.channel_url,
        "generated_at": generated_at,
        "media_type": "audio" if context.audio_only else "video",
        "videos": [{**asdict(video), **states[video.video_id]} for video in context.videos],
    }
    atomic_write_text(
        context.output_dir / "manifest.json",
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )

    fieldnames = [
        "status",
        "video_id",
        "upload_date",
        "duration",
        "title",
        "url",
        "file",
        "elapsed_seconds",
        "error",
    ]
    rows = []
    for video in context.videos:
        state = states[video.video_id]
        rows.append(
            {
                "status": state["status"],
                "video_id": video.video_id,
                "upload_date": video.upload_date,
                "duration": "" if video.duration is None else video.duration,
                "title": video.title,
                "url": video.url,
                "file": state["file"],
                "elapsed_seconds": state["elapsed_seconds"],
                "error": state["error"],
            }
        )
    temporary = context.output_dir / ".manifest.tsv.render"
    with temporary.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    atomic_write_text(context.output_dir / "manifest.tsv", temporary.read_text(encoding="utf-8"))
    temporary.unlink()


def export_firefox_cookies(host: str, profile: str, destination: Path) -> None:
    exporter = Path(__file__).with_name("firefox_youtube_cookies.py")
    remote_command = f"py -3 - {shlex.quote(profile)}"
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with exporter.open("rb") as source, os.fdopen(descriptor, "wb") as output:
            result = subprocess.run(
                ["ssh", host, remote_command],
                stdin=source,
                stdout=output,
                stderr=subprocess.PIPE,
                check=False,
            )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Firefox cookie export failed: {detail or result.returncode}")
        first_line = destination.read_text(encoding="utf-8").splitlines()[0]
        if first_line != "# Netscape HTTP Cookie File":
            raise RuntimeError("Firefox cookie export did not produce a Netscape cookie file")
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def secure_unlink(path: Path) -> None:
    if not path.exists():
        return
    if shutil.which("shred"):
        subprocess.run(["shred", "--remove", str(path)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        path.unlink(missing_ok=True)


@contextmanager
def cookie_context(args: argparse.Namespace) -> Iterator[Path | None]:
    if args.cookies:
        cookie_file = Path(args.cookies).expanduser().resolve()
        if not cookie_file.is_file():
            raise RuntimeError(f"Cookie file is not a regular file: {cookie_file}")
        yield cookie_file
        return

    if not args.firefox_ssh:
        yield None
        return

    runtime_parent = Path(os.environ.get("XDG_RUNTIME_DIR", "/dev/shm"))
    if not runtime_parent.is_dir():
        raise RuntimeError(f"Runtime directory does not exist: {runtime_parent}")
    temporary_directory = Path(tempfile.mkdtemp(prefix="youtube-channel-cookies-", dir=runtime_parent))
    temporary_directory.chmod(0o700)
    cookie_file = temporary_directory / "cookies.txt"
    try:
        export_firefox_cookies(args.firefox_ssh, args.firefox_profile, cookie_file)
        yield cookie_file
    finally:
        secure_unlink(cookie_file)
        temporary_directory.rmdir()


def initial_states(output_dir: Path, videos: list[Video], audio_only: bool, force: bool) -> dict[str, dict[str, object]]:
    states: dict[str, dict[str, object]] = {}
    for video in videos:
        filename = output_filename(video, audio_only)
        destination = output_dir / filename
        existing = not force and valid_media(destination, audio_only)
        states[video.video_id] = {
            "status": "existing" if existing else "pending",
            "file": filename,
            "elapsed_seconds": 0.0,
            "error": "",
            "completed_at": utc_now() if existing else "",
        }
    return states


def render_duration(seconds: float) -> str:
    if seconds < SECONDS_PER_MINUTE:
        return f"{seconds:.1f}s"
    minutes, remainder = divmod(round(seconds), SECONDS_PER_MINUTE)
    return f"{minutes}m {remainder:02d}s"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download every public upload from a YouTube channel with a resumable manifest."
    )
    parser.add_argument("source_url", help="YouTube channel, account, videos-tab, or video URL")
    parser.add_argument("-o", "--output-dir", required=True, help="Destination directory")
    parser.add_argument("-a", "--audio-only", action="store_true", help="Download MP3 audio instead of MP4 video")
    parser.add_argument("-j", "--workers", type=int, default=DEFAULT_WORKERS, help=f"Concurrent downloads (default: {DEFAULT_WORKERS})")
    parser.add_argument("--force", action="store_true", help="Replace valid existing media")
    parser.add_argument("--list-only", action="store_true", help="Refresh the manifest without downloading media")
    parser.add_argument("--cookies", help="Existing Netscape-format cookie file")
    parser.add_argument("--firefox-ssh", default=os.environ.get("YT_FIREFOX_SSH_HOST"), help="SSH host with an authenticated Firefox profile")
    parser.add_argument("--firefox-profile", default=os.environ.get("YT_FIREFOX_PROFILE"), help="Firefox profile path on the SSH host")
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if bool(args.firefox_ssh) != bool(args.firefox_profile):
        parser.error("--firefox-ssh and --firefox-profile must be supplied together")
    if args.cookies and args.firefox_ssh:
        parser.error("--cookies cannot be combined with the Firefox SSH options")


def run(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(exist_ok=True)

    with cookie_context(args) as cookie_file:
        channel_url = resolve_channel_url(args.source_url, cookie_file)
        videos = enumerate_channel(channel_url, cookie_file)
        states = initial_states(output_dir, videos, args.audio_only, args.force)
        manifest_context = ManifestContext(output_dir, args.source_url, channel_url, args.audio_only, videos)
        write_manifests(manifest_context, states)

        existing = sum(state["status"] == "existing" for state in states.values())
        pending = [video for video in videos if states[video.video_id]["status"] == "pending"]
        print(f"Found {len(videos)} public videos: {existing} existing, {len(pending)} pending", flush=True)
        print(f"Manifest: {output_dir / 'manifest.tsv'}", flush=True)
        if args.list_only or not pending:
            return 0

        started = time.monotonic()
        durations: list[float] = []
        completed = 0
        failed = 0
        future_to_video: dict[Future[DownloadResult], Video] = {}
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            for video in pending:
                state = states[video.video_id]
                destination = output_dir / str(state["file"])
                future = executor.submit(
                    download_video,
                    video,
                    destination,
                    logs_dir / f"{video.video_id}.log",
                    args.audio_only,
                    cookie_file,
                )
                future_to_video[future] = video

            total = len(pending)
            for progress, future in enumerate(as_completed(future_to_video), start=1):
                video = future_to_video[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = DownloadResult(video.video_id, "failed", 0.0, str(exc))
                state = states[video.video_id]
                state["status"] = result.status
                state["elapsed_seconds"] = round(result.elapsed_seconds, 3)
                state["error"] = result.error
                state["completed_at"] = utc_now() if result.status == "completed" else ""
                durations.append(result.elapsed_seconds)
                if result.status == "completed":
                    completed += 1
                else:
                    failed += 1
                write_manifests(manifest_context, states)
                print(
                    f"[{progress}/{total}] {video.video_id} {result.status} {render_duration(result.elapsed_seconds)}",
                    flush=True,
                )

        elapsed = time.monotonic() - started
        timing = ""
        if durations:
            timing = f"; median {render_duration(statistics.median(durations))}, max {render_duration(max(durations))}"
        print(
            f"Finished {completed}/{len(pending)} downloaded, {failed} failed in {render_duration(elapsed)}{timing}",
            flush=True,
        )
        return 1 if failed else 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    validate_args(parser, args)
    try:
        return run(args)
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
