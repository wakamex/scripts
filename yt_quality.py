#!/usr/bin/env python3
"""Choose a YouTube video format using codec-adjusted bitrate."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

YT_DLP_VERSION = "2026.8.19"
BYTES_PER_UNIT = 1024


class SelectionError(RuntimeError):
    """Raised when metadata has no usable formats."""


@dataclass(frozen=True)
class Candidate:
    """A scored video-only format."""

    format_id: str
    codec: str
    height: int
    fps: float
    bitrate: float
    bitrate_source: str
    factor: float
    score: float
    filesize: int | None
    protocol: str
    note: str


@dataclass(frozen=True)
class Selection:
    """The chosen video format, audio format, and comparable candidates."""

    candidate: Candidate
    audio_id: str | None
    finalists: tuple[Candidate, ...]

    @property
    def format_selector(self) -> str:
        """Return a selector suitable for yt-dlp's --format option."""
        if self.audio_id:
            return f"{self.candidate.format_id}+{self.audio_id}/{self.candidate.format_id}"
        return self.candidate.format_id


@dataclass(frozen=True)
class DefaultSelection:
    """The formats selected by yt-dlp before the wrapper intervenes."""

    selector: str
    video_id: str | None
    audio_id: str | None


def number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def codec_family(codec: str) -> str:
    normalized = codec.lower()
    if normalized.startswith(("av01", "av1")):
        return "av1"
    if normalized.startswith(("vp09", "vp9")):
        return "vp9"
    if normalized.startswith(("avc1", "avc3", "h264")):
        return "h264"
    return "other"


def effective_bitrate(fmt: dict[str, Any], duration: float | None) -> tuple[float, str]:
    for field in ("vbr", "tbr"):
        bitrate = number(fmt.get(field))
        if bitrate and bitrate > 0:
            return bitrate, field

    for field in ("filesize", "filesize_approx"):
        size = number(fmt.get(field))
        if size and size > 0 and duration and duration > 0:
            return size * 8 / duration / 1000, f"{field}/duration"

    return 0.0, "unknown"


def make_candidate(fmt: dict[str, Any], duration: float | None, factors: dict[str, float]) -> Candidate | None:
    codec = str(fmt.get("vcodec") or "none")
    height = number(fmt.get("height"))
    format_id = str(fmt.get("format_id") or "")
    if not format_id or codec == "none" or fmt.get("acodec") not in {None, "none"} or not height or fmt.get("has_drm"):
        return None

    bitrate, source = effective_bitrate(fmt, duration)
    factor = factors[codec_family(codec)]
    filesize = number(fmt.get("filesize")) or number(fmt.get("filesize_approx"))
    return Candidate(
        format_id=format_id,
        codec=codec,
        height=int(height),
        fps=number(fmt.get("fps")) or 0.0,
        bitrate=bitrate,
        bitrate_source=source,
        factor=factor,
        score=bitrate * factor,
        filesize=int(filesize) if filesize else None,
        protocol=str(fmt.get("protocol") or "unknown"),
        note=str(fmt.get("format_note") or ""),
    )


def select_formats(info: dict[str, Any], max_height: int, factors: dict[str, float]) -> Selection:
    formats = info.get("formats")
    if not isinstance(formats, list):
        raise SelectionError("yt-dlp metadata contains no format list")

    duration = number(info.get("duration"))
    videos = [candidate for fmt in formats if (candidate := make_candidate(fmt, duration, factors))]
    videos = [candidate for candidate in videos if candidate.height <= max_height]
    if not videos:
        raise SelectionError(f"no video format is available at or below {max_height}p")

    selected_height = max(candidate.height for candidate in videos)
    videos = [candidate for candidate in videos if candidate.height == selected_height]
    selected_fps = max(candidate.fps for candidate in videos)
    finalists = tuple(candidate for candidate in videos if candidate.fps == selected_fps)
    if not any(candidate.bitrate > 0 for candidate in finalists):
        raise SelectionError("no candidate at the selected resolution and FPS has bitrate or size data")
    selected = max(finalists, key=lambda candidate: (candidate.score, candidate.bitrate, candidate.format_id))

    # yt-dlp emits formats in its configured worst-to-best order. The last
    # audio-only entry therefore matches its normal bestaudio choice.
    audio_ids = [
        str(fmt.get("format_id"))
        for fmt in formats
        if fmt.get("vcodec") == "none"
        and fmt.get("acodec") not in {None, "none"}
        and fmt.get("format_id")
        and not fmt.get("has_drm")
    ]
    return Selection(selected, audio_ids[-1] if audio_ids else None, finalists)


def yt_dlp_default_selection(info: dict[str, Any]) -> DefaultSelection | None:
    requested = info.get("requested_formats")
    if not isinstance(requested, list):
        downloads = info.get("requested_downloads")
        if isinstance(downloads, list) and downloads and isinstance(downloads[0], dict):
            requested = downloads[0].get("requested_formats")

    video_id = None
    audio_id = None
    if isinstance(requested, list):
        for fmt in requested:
            if not isinstance(fmt, dict) or not fmt.get("format_id"):
                continue
            format_id = str(fmt["format_id"])
            if video_id is None and fmt.get("vcodec") not in {None, "none"}:
                video_id = format_id
            if audio_id is None and fmt.get("acodec") not in {None, "none"}:
                audio_id = format_id

    selector = str(info.get("format_id") or "")
    if not selector and requested:
        selector = "+".join(
            str(fmt["format_id"]) for fmt in requested if isinstance(fmt, dict) and fmt.get("format_id")
        )
    if not selector:
        return None
    return DefaultSelection(selector, video_id, audio_id)


def yt_dlp_command() -> list[str]:
    return ["uvx", "--no-config", "--from", f"yt-dlp=={YT_DLP_VERSION}", "yt-dlp"]


def fetch_metadata(url: str) -> dict[str, Any]:
    command = [*yt_dlp_command(), "--no-playlist", "--skip-download", "--dump-single-json", url]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise SelectionError("uvx is required") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip().splitlines()
        message = detail[-1] if detail else f"yt-dlp exited with status {exc.returncode}"
        raise SelectionError(message) from exc

    try:
        metadata = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SelectionError("yt-dlp returned invalid JSON metadata") from exc
    if not isinstance(metadata, dict):
        raise SelectionError("yt-dlp returned unexpected JSON metadata")
    return metadata


def human_size(size: int | None) -> str:
    if size is None:
        return "?"
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < BYTES_PER_UNIT or unit == "GiB":
            return f"{value:.1f} {unit}"
        value /= BYTES_PER_UNIT
    raise AssertionError("unreachable")


def render_report(url: str, info: dict[str, Any], selection: Selection, max_height: int) -> str:
    selected = selection.candidate
    default = yt_dlp_default_selection(info)
    lines = [
        f"URL: {url}",
        f"Title: {info.get('title') or '(unknown)'}",
        f"Target: highest resolution at or below {max_height}p, then highest FPS",
        "",
        "ID\tcodec\tresolution\tbitrate\tsource\tfactor\tscore\tsize\tprotocol\tnote",
    ]
    for candidate in sorted(selection.finalists, key=lambda item: item.score, reverse=True):
        marker = " <- selected" if candidate == selected else ""
        resolution = f"{candidate.height}p{candidate.fps:g}"
        lines.append(
            f"{candidate.format_id}\t{candidate.codec}\t{resolution}\t{candidate.bitrate:.1f} kbps\t"
            f"{candidate.bitrate_source}\t{candidate.factor:g}\t{candidate.score:.1f}\t"
            f"{human_size(candidate.filesize)}\t{candidate.protocol}\t{candidate.note}{marker}"
        )

    audio = selection.audio_id or "none (selected video must contain audio)"
    download = [
        *yt_dlp_command(),
        "--no-playlist",
        "--format",
        selection.format_selector,
        "--merge-output-format",
        "mp4",
        url,
    ]
    lines.extend(["", f"Selected video: {selected.format_id}", f"Selected audio: {audio}"])
    if default:
        default_parts = [f"video {default.video_id or '?'}", f"audio {default.audio_id or '?'}"]
        lines.append(f"yt-dlp built-in default: {default.selector} ({', '.join(default_parts)})")
        if default.video_id == selected.format_id and default.audio_id == selection.audio_id:
            lines.append("Built-in ordering outcome overridden: NO - wrapper selects the same video and audio")
        else:
            lines.append("Built-in ordering outcome overridden: YES - wrapper selects different video or audio")
    else:
        lines.append("Built-in ordering outcome overridden: UNKNOWN - yt-dlp did not report its selected formats")
    lines.extend([f"Format selector: {selection.format_selector}", f"Download command: {shlex.join(download)}"])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run codec-adjusted YouTube quality selection using yt-dlp 2026.08.19."
    )
    parser.add_argument("urls", nargs="+", metavar="URL", help="one or more YouTube URLs")
    parser.add_argument(
        "--height", type=int, default=1080, help="maximum height; use the highest available height at or below it"
    )
    parser.add_argument("--av1-factor", type=float, default=1.3, help="AV1 bitrate multiplier (default: 1.3)")
    parser.add_argument("--vp9-factor", type=float, default=1.0, help="VP9 bitrate multiplier (default: 1.0)")
    parser.add_argument("--h264-factor", type=float, default=0.75, help="H.264 bitrate multiplier (default: 0.75)")
    parser.add_argument(
        "--format-only", action="store_true", help="print only the yt-dlp format selector; requires exactly one URL"
    )
    parser.add_argument("--json", action="store_true", help="print one JSON selection object per URL")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.height <= 0:
        parser.error("--height must be positive")
    if args.format_only and len(args.urls) != 1:
        parser.error("--format-only requires exactly one URL")
    if args.format_only and args.json:
        parser.error("--format-only and --json cannot be combined")

    factors = {"av1": args.av1_factor, "vp9": args.vp9_factor, "h264": args.h264_factor, "other": 1.0}
    if any(factor <= 0 for factor in factors.values()):
        parser.error("codec factors must be positive")

    had_error = False
    for index, url in enumerate(args.urls):
        try:
            info = fetch_metadata(url)
            selection = select_formats(info, args.height, factors)
        except SelectionError as exc:
            print(f"{url}: {exc}", file=sys.stderr)
            had_error = True
            continue

        if args.format_only:
            print(selection.format_selector)
        elif args.json:
            selected = selection.candidate
            default = yt_dlp_default_selection(info)
            print(
                json.dumps(
                    {
                        "url": url,
                        "title": info.get("title"),
                        "video_format_id": selected.format_id,
                        "audio_format_id": selection.audio_id,
                        "format_selector": selection.format_selector,
                        "height": selected.height,
                        "fps": selected.fps,
                        "codec": selected.codec,
                        "bitrate_kbps": selected.bitrate,
                        "bitrate_source": selected.bitrate_source,
                        "codec_factor": selected.factor,
                        "score": selected.score,
                        "yt_dlp_default_format_selector": default.selector if default else None,
                        "yt_dlp_default_video_format_id": default.video_id if default else None,
                        "yt_dlp_default_audio_format_id": default.audio_id if default else None,
                        "overrides_yt_dlp_default": (
                            default is not None
                            and (default.video_id != selected.format_id or default.audio_id != selection.audio_id)
                        ),
                    },
                    sort_keys=True,
                )
            )
        else:
            if index:
                print()
            print(render_report(url, info, selection, args.height))

    return int(had_error)


if __name__ == "__main__":
    raise SystemExit(main())
