import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import rename_code_dir as rename

# unittest names already describe the behavior under test.
# ruff: noqa: D101, D102, PLR0915


class RenameCodeDirTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.code = self.root / "code"
        self.home = self.root / "home"
        self.code.mkdir()
        self.home.mkdir()
        self.old = self.code / "old"
        self.old.mkdir()
        (self.old / "file.txt").write_text("hello\n")
        self.layout = rename.Layout(self.code, self.home, self.root / "backups")
        self.renamer = rename.Renamer(self.layout)

    def tearDown(self):
        self.temporary.cleanup()

    def make_database(self, path, statements):
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        for statement in statements:
            connection.execute(statement)
        connection.commit()
        connection.close()

    def test_names_must_be_direct_children(self):
        self.assertEqual(rename.parse_name(str(self.old), self.code), "old")
        for value in ("../old", "/tmp/old", str(self.old / "nested"), "has space"):
            with self.subTest(value=value), self.assertRaises(rename.RenameError):
                rename.parse_name(value, self.code)

    def test_preview_does_not_change_anything(self):
        plan = self.renamer.plan("old", "new")

        self.assertEqual(plan.old_path, self.old)
        self.assertTrue(self.old.exists())
        self.assertFalse((self.code / "new").exists())

    def test_default_backup_root_is_temporary(self):
        layout = rename.Layout(code_root=self.code, home=self.home)

        self.assertEqual(layout.backups, Path(tempfile.gettempdir()))

    def test_apply_renames_directory_and_all_harness_history(self):
        codex = self.home / ".codex"
        codex.mkdir()
        rollout = codex / "sessions/2026/01/01/rollout-test.jsonl"
        rollout.parent.mkdir(parents=True)
        rollout.write_text(
            json.dumps({"type": "session_meta", "payload": {"id": "parent", "cwd": str(self.old / "fork")}})
            + "\n"
            + json.dumps({"type": "session_meta", "payload": {"id": "test", "cwd": str(self.old)}})
            + "\n"
        )
        codex_db = codex / "state_5.sqlite"
        self.make_database(
            codex_db,
            [
                "CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT NOT NULL, rollout_path TEXT NOT NULL)",
                f"INSERT INTO threads VALUES ('one', '{self.old}', '{rollout}')",
            ],
        )
        codex_config = codex / "config.toml"
        codex_config.write_text(f'[projects."{self.old}"]\ntrust_level = "trusted"\n')

        claude = self.home / ".claude"
        claude_project = claude / "projects" / str(self.old).replace("/", "-")
        claude_project.mkdir(parents=True)
        claude_session = claude_project / "session.jsonl"
        claude_session.write_text(json.dumps({"type": "user", "cwd": str(self.old)}) + "\n")
        (claude / "history.jsonl").write_text(json.dumps({"project": str(self.old), "display": "hi"}) + "\n")
        (self.home / ".claude.json").write_text(json.dumps({"projects": {str(self.old): {}}}))

        gemini = self.home / ".gemini"
        marker = gemini / "tmp/old/.project_root"
        marker.parent.mkdir(parents=True)
        marker.write_text(str(self.old))
        (gemini / "projects.json").write_text(json.dumps({"projects": {str(self.old): "old"}}))

        agy = gemini / "antigravity-cli"
        metadata = agy / "cache/conversation_metadata.json"
        metadata.parent.mkdir(parents=True)
        metadata.write_text(json.dumps({"conversations": [{"summary": {"WorkspaceURIs": [f"file://{self.old}"]}}]}))
        transcript = agy / "brain/session/.system_generated/logs/transcript_full.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(json.dumps({"tool_calls": [{"args": {"Cwd": str(self.old)}}]}) + "\n")

        opencode_db = self.home / ".local/share/opencode/opencode.db"
        self.make_database(
            opencode_db,
            [
                "CREATE TABLE project (id TEXT PRIMARY KEY, worktree TEXT)",
                "CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT)",
                "CREATE TABLE project_directory (project_id TEXT, directory TEXT, PRIMARY KEY(project_id, directory))",
                "CREATE TABLE workspace (id TEXT PRIMARY KEY, directory TEXT)",
                f"INSERT INTO project VALUES ('p', '{self.old}')",
                f"INSERT INTO session VALUES ('s', '{self.old}')",
            ],
        )

        plan = self.renamer.plan("old", "new")
        backup = self.renamer.apply(plan)
        new = self.code / "new"

        self.assertFalse(self.old.exists())
        self.assertEqual((new / "file.txt").read_text(), "hello\n")
        self.assertTrue(backup.is_dir())
        connection = sqlite3.connect(codex_db)
        self.assertEqual(connection.execute("SELECT cwd FROM threads").fetchone()[0], str(new))
        connection.close()
        self.assertIn(str(new), codex_config.read_text())
        rollout_records = [json.loads(line) for line in rollout.read_text().splitlines()]
        self.assertEqual(rollout_records[0]["payload"]["cwd"], str(new / "fork"))
        self.assertEqual(rollout_records[1]["payload"]["cwd"], str(new))

        moved_claude = claude / "projects" / str(new).replace("/", "-") / "session.jsonl"
        self.assertEqual(json.loads(moved_claude.read_text())["cwd"], str(new))
        self.assertEqual(json.loads((claude / "history.jsonl").read_text())["project"], str(new))
        self.assertIn(str(new), json.loads((self.home / ".claude.json").read_text())["projects"])
        moved_marker = gemini / "tmp/new/.project_root"
        self.assertEqual(moved_marker.read_text(), str(new))
        self.assertEqual(json.loads((gemini / "projects.json").read_text())["projects"][str(new)], "new")
        self.assertEqual(
            json.loads(metadata.read_text())["conversations"][0]["summary"]["WorkspaceURIs"],
            [f"file://{new}"],
        )
        self.assertEqual(json.loads(transcript.read_text())["tool_calls"][0]["args"]["Cwd"], str(new))
        connection = sqlite3.connect(opencode_db)
        self.assertEqual(connection.execute("SELECT worktree FROM project").fetchone()[0], str(new))
        self.assertEqual(connection.execute("SELECT directory FROM session").fetchone()[0], str(new))
        connection.close()

    def test_existing_destination_is_refused(self):
        (self.code / "new").mkdir()
        with self.assertRaisesRegex(rename.RenameError, "destination already exists"):
            self.renamer.plan("old", "new")

    def test_deep_scan_finds_unindexed_codex_rollout(self):
        codex = self.home / ".codex"
        sessions = codex / "sessions"
        sessions.mkdir(parents=True)
        indexed = sessions / "rollout-indexed.jsonl"
        orphan = sessions / "rollout-orphan.jsonl"
        record = json.dumps({"type": "session_meta", "payload": {"cwd": str(self.old)}}) + "\n"
        indexed.write_text(record)
        orphan.write_text(record)
        self.make_database(
            codex / "state_5.sqlite",
            [
                "CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT NOT NULL, rollout_path TEXT NOT NULL)",
                f"INSERT INTO threads VALUES ('one', '{self.old}', '{indexed}')",
            ],
        )

        normal_paths = {edit.path for edit in self.renamer.plan("old", "new").files}
        deep_paths = {edit.path for edit in self.renamer.plan("old", "new", deep_scan=True).files}

        self.assertIn(indexed, normal_paths)
        self.assertNotIn(orphan, normal_paths)
        self.assertIn(orphan, deep_paths)


if __name__ == "__main__":
    unittest.main()
