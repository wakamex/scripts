from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import youtube_channel_download as module


class YouTubeChannelDownloadTests(unittest.TestCase):
    """Exercise channel enumeration, command construction, and manifests."""

    def test_resolves_channel_and_video_urls(self) -> None:
        """Channel inputs gain a videos suffix while video inputs remain identifiable."""
        self.assertEqual(module.videos_url("https://www.youtube.com/@example"), "https://www.youtube.com/@example/videos")
        self.assertEqual(
            module.videos_url("https://www.youtube.com/channel/UC123/videos"),
            "https://www.youtube.com/channel/UC123/videos",
        )
        self.assertTrue(module.is_video_url("https://www.youtube.com/watch?v=abcdefghijk"))
        self.assertTrue(module.is_video_url("https://youtu.be/abcdefghijk"))
        self.assertFalse(module.is_video_url("https://www.youtube.com/@example"))

    def test_parses_flat_playlist_entries_and_deduplicates(self) -> None:
        """Flat-playlist parsing keeps one complete record per video ID."""
        output = "\n".join(
            [
                json.dumps({"id": "one", "title": "First video", "duration": 12, "upload_date": "20260101"}),
                json.dumps({"id": "one", "title": "Duplicate"}),
                json.dumps({"id": "two", "title": "Second video", "url": "https://example.test/two"}),
                json.dumps({"title": "Missing ID"}),
            ]
        )

        videos = module.parse_entries(output)

        self.assertEqual([video.video_id for video in videos], ["one", "two"])
        self.assertEqual(videos[0].url, "https://www.youtube.com/watch?v=one")
        self.assertEqual(videos[0].duration, 12.0)
        self.assertEqual(videos[1].url, "https://example.test/two")

    def test_download_command_reuses_cookie_file(self) -> None:
        """Worker commands share the one securely exported cookie file."""
        video = module.Video("abcdefghijk", "Example", "https://youtu.be/abcdefghijk", "", None, "public")
        command = module.build_download_command(
            video,
            Path("/tmp/example.mp3"),
            audio_only=True,
            cookie_file=Path("/run/user/1000/cookies.txt"),
        )

        self.assertIn("--audio-only", command)
        self.assertEqual(command[command.index("--cookies") + 1], "/run/user/1000/cookies.txt")
        self.assertEqual(command[-1], video.url)

    @patch.object(module, "valid_media", return_value=False)
    def test_manifest_preserves_every_video(self, _valid_media) -> None:
        """Both manifest formats retain every enumerated public upload."""
        videos = [
            module.Video("one", "First", "https://youtu.be/one", "20260101", 12.0, "public"),
            module.Video("two", "Second", "https://youtu.be/two", "20260102", None, "public"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            states = module.initial_states(output_dir, videos, audio_only=True, force=False)
            context = module.ManifestContext(output_dir, "source", "channel", True, videos)
            module.write_manifests(context, states)

            payload = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            rows = (output_dir / "manifest.tsv").read_text(encoding="utf-8").splitlines()

        self.assertEqual([entry["video_id"] for entry in payload["videos"]], ["one", "two"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(payload["media_type"], "audio")


if __name__ == "__main__":
    unittest.main()
