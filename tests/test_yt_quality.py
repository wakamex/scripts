from __future__ import annotations

import pytest

import yt_quality

FACTORS = {"av1": 1.3, "vp9": 1.0, "h264": 0.75, "other": 1.0}
EXPECTED_SIZE_BITRATE = 8000


def metadata() -> dict[str, object]:
    return {
        "duration": 100,
        "format_id": "399+251",
        "requested_formats": [
            {"format_id": "399", "vcodec": "av01.0.08M.08", "acodec": "none"},
            {"format_id": "251", "vcodec": "none", "acodec": "opus"},
        ],
        "formats": [
            {"format_id": "140", "vcodec": "none", "acodec": "mp4a", "abr": 129},
            {"format_id": "251", "vcodec": "none", "acodec": "opus", "abr": 140},
            {"format_id": "137", "vcodec": "avc1.640028", "acodec": "none", "height": 1080, "fps": 30, "vbr": 6000},
            {"format_id": "399", "vcodec": "av01.0.08M.08", "acodec": "none", "height": 1080, "fps": 30, "vbr": 4800},
            {"format_id": "616", "vcodec": "vp09.00.41.08", "acodec": "none", "height": 1080, "fps": 30, "vbr": 6500},
            {"format_id": "higher", "vcodec": "av01", "acodec": "none", "height": 1440, "fps": 30, "vbr": 9000},
        ],
    }


def test_selects_codec_adjusted_score_at_requested_height() -> None:
    selection = yt_quality.select_formats(metadata(), 1080, FACTORS)

    assert selection.candidate.format_id == "616"
    assert selection.audio_id == "251"
    assert selection.format_selector == "616+251/616"


def test_av1_factor_can_outscore_vp9() -> None:
    info = metadata()
    formats = info["formats"]
    assert isinstance(formats, list)
    next(fmt for fmt in formats if fmt["format_id"] == "399")["vbr"] = 5100

    selection = yt_quality.select_formats(info, 1080, FACTORS)

    assert selection.candidate.format_id == "399"


def test_prefers_fps_before_quality_score() -> None:
    info = metadata()
    formats = info["formats"]
    assert isinstance(formats, list)
    formats.append({"format_id": "60fps", "vcodec": "vp9", "acodec": "none", "height": 1080, "fps": 60, "vbr": 1000})

    selection = yt_quality.select_formats(info, 1080, FACTORS)

    assert selection.candidate.format_id == "60fps"
    assert [candidate.format_id for candidate in selection.finalists] == ["60fps"]


def test_estimates_bitrate_from_approximate_size() -> None:
    info = {
        "duration": 100,
        "formats": [
            {
                "format_id": "size-only",
                "vcodec": "vp9",
                "acodec": "none",
                "height": 1080,
                "fps": 30,
                "filesize_approx": 100_000_000,
            }
        ],
    }

    selection = yt_quality.select_formats(info, 1080, FACTORS)

    assert selection.candidate.bitrate == EXPECTED_SIZE_BITRATE
    assert selection.candidate.bitrate_source == "filesize_approx/duration"


def test_rejects_candidates_without_quality_evidence() -> None:
    info = {
        "formats": [
            {
                "format_id": "unknown",
                "vcodec": "vp9",
                "acodec": "none",
                "height": 1080,
                "fps": 30,
            }
        ]
    }

    with pytest.raises(yt_quality.SelectionError, match="bitrate or size"):
        yt_quality.select_formats(info, 1080, FACTORS)


def test_reports_when_wrapper_overrides_yt_dlp_default() -> None:
    info = metadata()
    selection = yt_quality.select_formats(info, 1080, FACTORS)
    default = yt_quality.yt_dlp_default_selection(info)

    assert default is not None
    assert default.selector == "399+251"
    assert default.video_id == "399"
    assert default.audio_id == "251"
    assert "Built-in ordering outcome overridden: YES" in yt_quality.render_report(
        "https://example.test", info, selection, 1080
    )


def test_reports_when_wrapper_matches_yt_dlp_default() -> None:
    info = metadata()
    info["format_id"] = "616+251"
    requested = info["requested_formats"]
    assert isinstance(requested, list)
    requested[0]["format_id"] = "616"
    selection = yt_quality.select_formats(info, 1080, FACTORS)

    assert "Built-in ordering outcome overridden: NO" in yt_quality.render_report(
        "https://example.test", info, selection, 1080
    )
