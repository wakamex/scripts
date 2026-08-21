#!/usr/bin/env python3
"""Rename a direct child of /code and carry local agent session history with it."""

# This is an internal command module. Its public methods map directly to CLI phases.
# ruff: noqa: D101, D102, D107, PLR0912, PLR0915

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterable

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
CODEX_PROJECT_RE = re.compile(r'^(\[projects\.)("(?:[^"\\]|\\.)*")(\]\s*)$', re.MULTILINE)


class RenameError(RuntimeError):
    """A preflight check or rename operation failed."""


@dataclass(frozen=True)
class Layout:
    code_root: Path = Path("/code")
    home: Path = field(default_factory=Path.home)
    backup_root: Path | None = None

    @property
    def backups(self) -> Path:
        return self.backup_root or Path(tempfile.gettempdir())


@dataclass
class FileEdit:
    path: Path
    description: str
    content: bytes | None = None
    writer: Callable[[BinaryIO], None] | None = None


@dataclass
class DatabaseEdit:
    path: Path
    changes: list[tuple[str, str, int]]
    apply: Callable[[sqlite3.Connection], None]
    description: str


@dataclass
class DirectoryMove:
    source: Path
    target: Path
    description: str


@dataclass
class Plan:
    old_path: Path
    new_path: Path
    files: list[FileEdit] = field(default_factory=list)
    databases: list[DatabaseEdit] = field(default_factory=list)
    directories: list[DirectoryMove] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def change_count(self) -> int:
        return len(self.files) + len(self.databases) + len(self.directories) + 1


def parse_name(value: str, code_root: Path) -> str:
    candidate = Path(value)
    if candidate.is_absolute():
        try:
            relative = candidate.relative_to(code_root)
        except ValueError as error:
            raise RenameError(f"path must be a direct child of {code_root}: {value}") from error
        if len(relative.parts) != 1:
            raise RenameError(f"path must be a direct child of {code_root}: {value}")
        value = relative.name
    if not NAME_RE.fullmatch(value) or value in {".", ".."}:
        raise RenameError("names may contain only letters, numbers, dot, underscore, and hyphen")
    return value


def path_matches(value: str, old: str) -> bool:
    return value == old or value.startswith(old + os.sep)


def replace_path(value: str, old: str, new: str) -> str:
    return new + value[len(old) :] if path_matches(value, old) else value


def replace_path_reference(value: str, old: str, new: str) -> str:
    if value.startswith("file://"):
        return "file://" + replace_path(value.removeprefix("file://"), old, new)
    return replace_path(value, old, new)


def atomic_write(edit: FileEdit) -> None:
    path = edit.path
    stat = path.stat()
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            if edit.writer is not None:
                edit.writer(output)
            elif edit.content is not None:
                output.write(edit.content)
            else:
                raise AssertionError(f"file edit has no content writer: {path}")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, stat.st_mode)
        os.chown(temporary, stat.st_uid, stat.st_gid)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def count_jsonl_changes(path: Path, transform: Callable[[dict[str, Any]], bool], *, first_only: bool = False) -> int:
    changed = 0
    with path.open("rb") as source:
        for raw_line in source:
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if transform(record):
                changed += 1
            if first_only:
                break
    return changed


def jsonl_writer(
    path: Path, transform: Callable[[dict[str, Any]], bool], *, first_only: bool = False
) -> Callable[[BinaryIO], None]:
    def write(output: BinaryIO) -> None:
        with path.open("rb") as source:
            for line_number, raw_line in enumerate(source, 1):
                if first_only and line_number > 1:
                    output.write(raw_line)
                    shutil.copyfileobj(source, output)
                    break
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError:
                    output.write(raw_line)
                    continue
                if transform(record):
                    encoded_line = (json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
                else:
                    encoded_line = raw_line
                output.write(encoded_line)

    return write


def file_contains(path: Path, needle: bytes) -> bool:
    with path.open("rb") as source:
        return any(needle in line for line in source)


def deep_codex_rollout_paths(root: Path, old: str) -> set[Path]:
    session_roots = [path for path in (root / "sessions", root / "archived_sessions") if path.is_dir()]
    if not session_roots:
        return set()
    ripgrep = shutil.which("rg")
    if ripgrep:
        pattern = rf'"cwd"\s*:\s*"{re.escape(old)}(?:/|")'
        completed = subprocess.run(
            [
                ripgrep,
                "-l",
                "-0",
                "--no-messages",
                "--glob",
                "rollout-*.jsonl",
                "-e",
                pattern,
                *(str(path) for path in session_roots),
            ],
            check=False,
            capture_output=True,
        )
        if completed.returncode in {0, 1}:
            return {Path(value.decode()) for value in completed.stdout.split(b"\0") if value}

    return {
        path
        for sessions_root in session_roots
        for path in sessions_root.rglob("rollout-*.jsonl")
        if file_contains(path, old.encode())
    }


def replace_json_paths(value: Any, old: str, new: str) -> tuple[Any, int]:
    if isinstance(value, str):
        replacement = replace_path_reference(value, old, new)
        return replacement, int(replacement != value)
    if isinstance(value, list):
        changed = 0
        result = []
        for item in value:
            replacement, count = replace_json_paths(item, old, new)
            result.append(replacement)
            changed += count
        return result, changed
    if isinstance(value, dict):
        changed = 0
        result = {}
        for key, item in value.items():
            replacement_key = replace_path_reference(key, old, new)
            if replacement_key in result:
                raise RenameError(f"JSON key collision while changing {key} to {replacement_key}")
            replacement, count = replace_json_paths(item, old, new)
            result[replacement_key] = replacement
            changed += count + int(replacement_key != key)
        return result, changed
    return value, 0


def sqlite_has_table(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


class Renamer:
    def __init__(self, layout: Layout = Layout()):
        self.layout = layout

    def plan(self, old_name: str, new_name: str, *, deep_scan: bool = False) -> Plan:
        old_name = parse_name(old_name, self.layout.code_root)
        new_name = parse_name(new_name, self.layout.code_root)
        if old_name == new_name:
            raise RenameError("old and new names are identical")
        old = self.layout.code_root / old_name
        new = self.layout.code_root / new_name
        if not old.is_dir() or old.is_symlink():
            raise RenameError(f"source is not a directory: {old}")
        if new.exists() or new.is_symlink():
            raise RenameError(f"destination already exists: {new}")

        plan = Plan(old, new)
        self._plan_codex(plan, deep_scan=deep_scan)
        self._plan_claude(plan)
        self._plan_gemini(plan)
        self._plan_antigravity(plan)
        self._plan_opencode(plan)
        return plan

    def _add_json_edit(self, plan: Plan, path: Path, description: str) -> None:
        if not path.is_file():
            return
        value = json.loads(path.read_text())
        replacement, count = replace_json_paths(value, str(plan.old_path), str(plan.new_path))
        if count:
            content = (json.dumps(replacement, indent=2, ensure_ascii=False) + "\n").encode()
            plan.files.append(FileEdit(path, f"{description}: {count} path reference(s)", content=content))

    def _plan_codex(self, plan: Plan, *, deep_scan: bool) -> None:
        root = self.layout.home / ".codex"
        old = str(plan.old_path)
        new = str(plan.new_path)
        indexed_rollouts: set[Path] = set()
        index_available = False

        for database in sorted(root.glob("state_*.sqlite")):
            try:
                connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
                if not sqlite_has_table(connection, "threads"):
                    connection.close()
                    continue
                columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)").fetchall()}
                if "cwd" not in columns:
                    connection.close()
                    continue
                index_available = index_available or "rollout_path" in columns
                selected = "cwd, rollout_path" if "rollout_path" in columns else "cwd, NULL"
                rows = connection.execute(f"SELECT {selected} FROM threads").fetchall()
                connection.close()
            except sqlite3.Error as error:
                raise RenameError(f"cannot inspect Codex database {database}: {error}") from error
            counts: dict[str, int] = {}
            for cwd, rollout_path in rows:
                if not path_matches(cwd, old):
                    continue
                counts[cwd] = counts.get(cwd, 0) + 1
                if rollout_path:
                    path = Path(rollout_path)
                    if path.is_file():
                        indexed_rollouts.add(path)
            matches = [(cwd, replace_path(cwd, old, new), count) for cwd, count in counts.items()]
            if matches:

                def apply_codex(connection: sqlite3.Connection, *, old=old, new=new) -> None:
                    connection.execute(
                        "UPDATE threads SET cwd = ? || substr(cwd, ?) WHERE cwd = ? OR cwd LIKE ? ESCAPE '\\'",
                        (new, len(old) + 1, old, old.replace("%", "\\%").replace("_", "\\_") + "/%"),
                    )

                plan.databases.append(DatabaseEdit(database, matches, apply_codex, "Codex thread index"))

        config = root / "config.toml"
        if config.is_file():
            original = config.read_text()
            changes = 0

            def rewrite_header(match: re.Match[str]) -> str:
                nonlocal changes
                path = json.loads(match.group(2))
                replacement = replace_path(path, old, new)
                if replacement != path:
                    changes += 1
                return match.group(1) + json.dumps(replacement) + match.group(3)

            replacement = CODEX_PROJECT_RE.sub(rewrite_header, original)
            if changes:
                headers = [json.loads(item[1]) for item in CODEX_PROJECT_RE.findall(replacement)]
                if len(headers) != len(set(headers)):
                    raise RenameError("Codex config would contain duplicate project sections")
                plan.files.append(
                    FileEdit(
                        config,
                        f"Codex config: {changes} project section(s)",
                        content=replacement.encode(),
                    )
                )

        rollout_paths = set(indexed_rollouts)
        if deep_scan or not index_available:
            if not index_available:
                plan.notes.append("Codex rollout_path index unavailable; using a full session scan")
            rollout_paths.update(deep_codex_rollout_paths(root, old))

        for path in sorted(rollout_paths):

            def transform(record: dict[str, Any], *, old=old, new=new) -> bool:
                if record.get("type") != "session_meta" or not isinstance(record.get("payload"), dict):
                    return False
                cwd = record["payload"].get("cwd")
                if not isinstance(cwd, str) or not path_matches(cwd, old):
                    return False
                record["payload"]["cwd"] = replace_path(cwd, old, new)
                return True

            count = count_jsonl_changes(path, transform)
            if count:
                plan.files.append(FileEdit(path, "Codex rollout metadata", writer=jsonl_writer(path, transform)))

    def _plan_claude(self, plan: Plan) -> None:
        root = self.layout.home / ".claude"
        old = str(plan.old_path)
        new = str(plan.new_path)
        source = root / "projects" / old.replace("/", "-")
        target = root / "projects" / new.replace("/", "-")
        if source.exists():
            if target.exists():
                raise RenameError(f"Claude project history destination already exists: {target}")
            plan.directories.append(DirectoryMove(source, target, "Claude project history"))
            for path in source.rglob("*.jsonl"):

                def transform(record: dict[str, Any], *, old=old, new=new) -> bool:
                    cwd = record.get("cwd")
                    if not isinstance(cwd, str) or not path_matches(cwd, old):
                        return False
                    record["cwd"] = replace_path(cwd, old, new)
                    return True

                count = count_jsonl_changes(path, transform)
                if count:
                    plan.files.append(
                        FileEdit(
                            path,
                            f"Claude session records: {count} cwd value(s)",
                            writer=jsonl_writer(path, transform),
                        )
                    )

        history = root / "history.jsonl"
        if history.is_file() and file_contains(history, old.encode()):

            def transform_history(record: dict[str, Any]) -> bool:
                project = record.get("project")
                if not isinstance(project, str) or not path_matches(project, old):
                    return False
                record["project"] = replace_path(project, old, new)
                return True

            count = count_jsonl_changes(history, transform_history)
            if count:
                plan.files.append(
                    FileEdit(
                        history,
                        f"Claude prompt history: {count} record(s)",
                        writer=jsonl_writer(history, transform_history),
                    )
                )

        self._add_json_edit(plan, self.layout.home / ".claude.json", "Claude config")

    def _plan_gemini(self, plan: Plan) -> None:
        root = self.layout.home / ".gemini"
        old = str(plan.old_path)
        new = str(plan.new_path)
        tmp = root / "tmp"
        registry = root / "projects.json"
        if registry.is_file():
            data = json.loads(registry.read_text())
            projects = data.get("projects")
            if not isinstance(projects, dict):
                raise RenameError(f"Gemini project registry has no projects object: {registry}")
            claimed = {slug for path, slug in projects.items() if not path_matches(path, old)}
            rewritten: dict[str, Any] = {}
            changed = 0
            for path, slug in projects.items():
                replacement_path = replace_path(path, old, new)
                replacement_slug = slug
                if path == old:
                    base = re.sub(r"[^a-z0-9]+", "-", plan.new_path.name.lower()).strip("-") or "project"
                    replacement_slug = base
                    suffix = 0
                    while replacement_slug in claimed or (
                        (tmp / replacement_slug).exists() and replacement_slug != slug
                    ):
                        suffix += 1
                        replacement_slug = f"{base}-{suffix}"
                    source = tmp / slug
                    target = tmp / replacement_slug
                    if source.is_dir() and source != target:
                        plan.directories.append(DirectoryMove(source, target, "Gemini project history"))
                if replacement_path in rewritten:
                    raise RenameError(f"Gemini registry path collision: {replacement_path}")
                rewritten[replacement_path] = replacement_slug
                changed += int(path != replacement_path) + int(slug != replacement_slug)
            if changed:
                data["projects"] = rewritten
                plan.files.append(
                    FileEdit(
                        registry,
                        f"Gemini project registry: {changed} value(s)",
                        content=(json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode(),
                    )
                )

        if tmp.is_dir():
            for marker in tmp.glob("*/.project_root"):
                value = marker.read_text().strip()
                if path_matches(value, old):
                    replacement = replace_path(value, old, new)
                    suffix = "\n" if marker.read_text().endswith("\n") else ""
                    plan.files.append(
                        FileEdit(
                            marker,
                            "Gemini project ownership marker",
                            content=(replacement + suffix).encode(),
                        )
                    )

    def _plan_antigravity(self, plan: Plan) -> None:
        root = self.layout.home / ".gemini/antigravity-cli"
        self._add_json_edit(
            plan,
            root / "cache/conversation_metadata.json",
            "Antigravity conversation metadata",
        )
        old = str(plan.old_path)
        new = str(plan.new_path)

        def transform(record: dict[str, Any]) -> bool:
            changed = False

            def visit(value: Any) -> None:
                nonlocal changed
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key in {"Cwd", "cwd", "WorkspaceRoot"} and isinstance(item, str):
                            replacement = replace_path_reference(item, old, new)
                            if replacement != item:
                                value[key] = replacement
                                changed = True
                        else:
                            visit(item)
                elif isinstance(value, list):
                    for item in value:
                        visit(item)

            visit(record)
            return changed

        brain = root / "brain"
        if brain.is_dir():
            for path in brain.glob("*/.system_generated/logs/transcript_full.jsonl"):
                if not file_contains(path, old.encode()):
                    continue
                count = count_jsonl_changes(path, transform)
                if count:
                    plan.files.append(
                        FileEdit(
                            path,
                            f"Antigravity transcript: {count} path-bearing event(s)",
                            writer=jsonl_writer(path, transform),
                        )
                    )

    def _plan_opencode(self, plan: Plan) -> None:
        database = self.layout.home / ".local/share/opencode/opencode.db"
        if not database.is_file():
            return
        old = str(plan.old_path)
        new = str(plan.new_path)
        columns = [
            ("project", "worktree"),
            ("session", "directory"),
            ("project_directory", "directory"),
            ("workspace", "directory"),
        ]
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        matches: list[tuple[str, str, int]] = []
        present: list[tuple[str, str]] = []
        try:
            for table, column in columns:
                if not sqlite_has_table(connection, table):
                    continue
                rows = connection.execute(
                    f'SELECT "{column}", count(*) FROM "{table}" WHERE "{column}" IS NOT NULL GROUP BY "{column}"'
                ).fetchall()
                table_matches = [
                    (value, replace_path(value, old, new), count) for value, count in rows if path_matches(value, old)
                ]
                if table_matches:
                    present.append((table, column))
                    matches.extend(
                        (f"{table}.{column}: {before}", after, count) for before, after, count in table_matches
                    )

            if sqlite_has_table(connection, "project_directory"):
                collision = connection.execute(
                    "SELECT 1 FROM project_directory old JOIN project_directory new "
                    "ON old.project_id=new.project_id AND new.directory=? "
                    "WHERE old.directory=? LIMIT 1",
                    (new, old),
                ).fetchone()
                if collision:
                    raise RenameError("OpenCode project directory migration would collide with an existing row")
        finally:
            connection.close()

        if matches:

            def apply_opencode(connection: sqlite3.Connection, *, present=present, old=old, new=new) -> None:
                pattern = old.replace("%", "\\%").replace("_", "\\_") + "/%"
                for table, column in present:
                    connection.execute(
                        f'UPDATE "{table}" SET "{column}" = ? || substr("{column}", ?) '
                        f'WHERE "{column}" = ? OR "{column}" LIKE ? ESCAPE \'\\\'',
                        (new, len(old) + 1, old, pattern),
                    )

            plan.databases.append(DatabaseEdit(database, matches, apply_opencode, "OpenCode session index"))

    def active_processes(self, old_path: Path) -> list[tuple[int, str]]:
        matches = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                cwd = os.readlink(entry / "cwd")
            except OSError:
                continue
            if path_matches(cwd.removesuffix(" (deleted)"), str(old_path)):
                try:
                    command = (entry / "comm").read_text().strip()
                except OSError:
                    command = "unknown"
                matches.append((int(entry.name), command))
        return sorted(matches)

    def apply(self, plan: Plan, *, allow_active: bool = False) -> Path:
        active = self.active_processes(plan.old_path)
        if active and not allow_active:
            preview = ", ".join(f"{pid}:{command}" for pid, command in active[:8])
            raise RenameError(
                f"{len(active)} process(es) still have a cwd under {plan.old_path}: {preview}. "
                "Stop them or pass --allow-active."
            )

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_name = f"rename-code-dir-{plan.old_path.name}-to-{plan.new_path.name}-{stamp}"
        if self.layout.backup_root is None:
            backup = Path(tempfile.mkdtemp(prefix=backup_name + "-", dir=self.layout.backups))
        else:
            backup = self.layout.backups / backup_name
            backup.mkdir(parents=True)
        backed_up: dict[Path, Path] = {}
        database_backups: dict[Path, Path] = {}

        def backup_path(path: Path) -> Path:
            relative = Path(str(path).lstrip("/"))
            target = backup / "files" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(path, target)
            except OSError:
                shutil.copy2(path, target)
            backed_up[path] = target
            return target

        try:
            for edit in plan.files:
                backup_path(edit.path)
            for edit in plan.databases:
                target = backup / "databases" / Path(str(edit.path).lstrip("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                source_connection = sqlite3.connect(edit.path)
                target_connection = sqlite3.connect(target)
                try:
                    source_connection.backup(target_connection)
                finally:
                    target_connection.close()
                    source_connection.close()
                database_backups[edit.path] = target

            manifest = {
                "old_path": str(plan.old_path),
                "new_path": str(plan.new_path),
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "files": [str(path) for path in backed_up],
                "databases": [str(path) for path in database_backups],
                "directory_moves": [[str(move.source), str(move.target)] for move in plan.directories],
            }
            (backup / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

            for edit in plan.files:
                atomic_write(edit)
            for edit in plan.databases:
                connection = sqlite3.connect(edit.path, timeout=10)
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    edit.apply(connection)
                    result = connection.execute("PRAGMA integrity_check").fetchone()
                    if result != ("ok",):
                        raise RenameError(f"SQLite integrity check failed for {edit.path}: {result}")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                finally:
                    connection.close()
            for move in plan.directories:
                move.source.rename(move.target)
            plan.old_path.rename(plan.new_path)
        except Exception:
            if plan.new_path.exists() and not plan.old_path.exists():
                plan.new_path.rename(plan.old_path)
            for move in reversed(plan.directories):
                if move.target.exists() and not move.source.exists():
                    move.target.rename(move.source)
            for path, saved in backed_up.items():
                shutil.copy2(saved, path)
            for path, saved in database_backups.items():
                source_connection = sqlite3.connect(saved)
                target_connection = sqlite3.connect(path)
                try:
                    source_connection.backup(target_connection)
                finally:
                    target_connection.close()
                    source_connection.close()
            raise
        return backup


def print_plan(plan: Plan, active: Iterable[tuple[int, str]]) -> None:
    print(f"directory: {plan.old_path} -> {plan.new_path}")
    for move in plan.directories:
        print(f"history directory: {move.source} -> {move.target} ({move.description})")
    for edit in plan.files:
        print(f"file: {edit.path} ({edit.description})")
    for edit in plan.databases:
        count = sum(item[2] for item in edit.changes)
        print(f"database: {edit.path} ({edit.description}: {count} row(s))")
    for note in plan.notes:
        print(f"note: {note}")
    active = list(active)
    if active:
        print(f"active cwd warning: {len(active)} process(es)")
        for pid, command in active[:8]:
            print(f"  {pid} {command}")
    if plan.change_count == 1:
        print("session history: no matching local records found")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Rename /code/OLD to /code/NEW and migrate Codex, Claude, Gemini, "
            "Antigravity, and OpenCode session metadata."
        )
    )
    result.add_argument("old", help="existing direct child name or /code path")
    result.add_argument("new", help="new direct child name or /code path")
    result.add_argument("--apply", action="store_true", help="perform the rename; otherwise only preview it")
    result.add_argument(
        "--allow-active",
        action="store_true",
        help="allow processes whose working directory is inside the source tree",
    )
    result.add_argument(
        "--deep-scan",
        action="store_true",
        help="scan every Codex rollout for orphaned records missing from the SQLite index",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        renamer = Renamer()
        plan = renamer.plan(args.old, args.new, deep_scan=args.deep_scan)
        active = renamer.active_processes(plan.old_path)
        print_plan(plan, active)
        if not args.apply:
            print("preview only; rerun with --apply to make these changes")
            return 0
        backup = renamer.apply(plan, allow_active=args.allow_active)
        print(f"renamed successfully; backup: {backup}")
        return 0
    except (OSError, sqlite3.Error, RenameError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
