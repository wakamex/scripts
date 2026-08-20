"""Tests for subdir-sizes."""

import runpy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "subdir-sizes"
MODULE = runpy.run_path(SCRIPT)


class SubdirSizesTests(unittest.TestCase):
    """Test subdirectory discovery, sizing, and output formatting."""

    def test_format_size(self) -> None:
        """Sizes use readable IEC units."""
        self.assertEqual(MODULE["format_size"](0), "0 B")
        self.assertEqual(MODULE["format_size"](1024), "1.0 KiB")
        self.assertEqual(MODULE["format_size"](1024**3 + 512 * 1024**2), "1.5 GiB")

    def test_immediate_subdirectories_excludes_files_and_symlinks(self) -> None:
        """Only real immediate child directories are candidates."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            directory = root / "directory"
            directory.mkdir()
            (root / "file").touch()
            (root / "link").symlink_to(directory, target_is_directory=True)

            result = MODULE["immediate_subdirectories"](root)

            self.assertEqual(result, [directory])

    def test_disk_usage_is_sorted_ascending(self) -> None:
        """Dust results are filtered and ordered numerically, smallest first."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            small = root / "small"
            large = root / "large"
            mounted = root / "mounted"
            small.mkdir()
            large.mkdir()
            mounted.mkdir()
            dust_output = {
                "size": "3100B",
                "name": str(root),
                "children": [
                    {"size": "3000B", "name": str(large), "children": []},
                    {"size": "100B", "name": str(mounted), "children": []},
                    {"size": "1000B", "name": str(small), "children": []},
                ],
            }

            completed = mock.Mock(stdout=MODULE["json"].dumps(dust_output))
            with (
                mock.patch.object(MODULE["shutil"], "which", return_value="/usr/bin/dust"),
                mock.patch.object(MODULE["subprocess"], "run", return_value=completed) as run,
            ):
                result = MODULE["disk_usage"](root, [large, small], limit_filesystem=True)

            self.assertEqual([path for _, path in result], [small, large])
            command = run.call_args.args[0]
            self.assertIn("--limit-filesystem", command)
            self.assertNotIn("--exclude-mounts", command)


if __name__ == "__main__":
    unittest.main()
