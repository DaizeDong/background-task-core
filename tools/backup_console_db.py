"""Refresh or verify one consistent, compressed console database recovery snapshot."""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile

from storage_retention import REPO, ordinary_path, private_data_root, relative_path


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def metadata(connection):
    checks = [row[0] for row in connection.execute("PRAGMA quick_check")]
    if checks != ["ok"]:
        raise ValueError("Database integrity check failed")
    schema = list(connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"))
    counts = {}
    for kind, name, _, _ in schema:
        if kind == "table":
            quoted = '"' + name.replace('"', '""') + '"'
            counts[name] = connection.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0]
    return {"quick_check": "ok", "tables": counts,
            "schema_sha256": hashlib.sha256(json.dumps(schema).encode()).hexdigest(),
            "user_version": connection.execute("PRAGMA user_version").fetchone()[0]}


@contextmanager
def snapshot_lock(directory):
    """Use an OS lock; the retained lock file is not an ownership indicator."""
    lock = ordinary_path(directory / "snapshot.lock")
    with lock.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def verify_archive(archive, staging):
    """Validate the embedded receipt, bytes, schema and every table count."""
    database = staging / "verified.sqlite3"
    with zipfile.ZipFile(archive) as bundle:
        if sorted(bundle.namelist()) != ["console.sqlite3", "receipt.json"]:
            raise ValueError("Unexpected recovery archive contents")
        receipt = json.loads(bundle.read("receipt.json"))
        if receipt.get("schema_version") != 1:
            raise ValueError("Unsupported recovery receipt")
        with bundle.open("console.sqlite3") as source, database.open("xb") as target:
            shutil.copyfileobj(source, target)
    if database.stat().st_size != receipt["database_bytes"] or digest(database) != receipt["database_sha256"]:
        raise ValueError("Recovery database digest mismatch")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        if metadata(connection) != receipt["database"]:
            raise ValueError("Recovery schema or table counts differ")
    return receipt


def backup(data_root, *, verify_only=False, timeout=120):
    """Caller supplies a proven private root, or a synthetic test directory."""
    root = ordinary_path(data_root)
    source = relative_path(root, "task-console/console.sqlite3")
    recovery = relative_path(root, "task-console/recovery")
    if not verify_only and not source.is_file():
        raise FileNotFoundError("Current database is missing; refusing to create an empty backup")
    recovery.mkdir(parents=True, exist_ok=True)
    with snapshot_lock(recovery):
        return _backup_locked(root, verify_only=verify_only, timeout=timeout)


def _backup_locked(root, *, verify_only=False, timeout=120):
    source = relative_path(root, "task-console/console.sqlite3")
    recovery = relative_path(root, "task-console/recovery")
    archive = relative_path(root, "task-console/recovery/current.zip")
    receipt_path = relative_path(root, "task-console/recovery/current.json")
    with tempfile.TemporaryDirectory(prefix="snapshot-", dir=recovery) as temporary:
        stage = Path(temporary)
        if verify_only:
            receipt = verify_archive(archive, stage)
            expected = {**receipt, "archive_bytes": archive.stat().st_size, "archive_sha256": digest(archive)}
            if json.loads(receipt_path.read_text(encoding="utf-8")) != expected:
                raise ValueError("External receipt differs from the verified archive; refresh the snapshot")
            return expected
        database = stage / "console.sqlite3"
        deadline = time.monotonic() + timeout

        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise TimeoutError("Database backup exceeded its time budget")

        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)) as live:
            # This read transaction binds source metadata and backup to one SQLite snapshot.
            live.execute("BEGIN")
            original = metadata(live)
            with closing(sqlite3.connect(database)) as saved:
                live.backup(saved, pages=256, progress=progress, sleep=0.1)
                if metadata(saved) != original:
                    raise ValueError("Online backup differs from its source snapshot")
            live.rollback()
        database_sha256 = digest(database)
        if archive.is_file() and receipt_path.is_file():
            previous = json.loads(receipt_path.read_text(encoding="utf-8"))
            if previous.get("database_sha256") == database_sha256 and previous.get("database") == original:
                with tempfile.TemporaryDirectory(prefix="verify-", dir=stage) as verification:
                    embedded = verify_archive(archive, Path(verification))
                checked = {**embedded, "archive_bytes": archive.stat().st_size, "archive_sha256": digest(archive)}
                if checked == previous:
                    return previous
        receipt = {"schema_version": 1, "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                   "database_bytes": database.stat().st_size, "database_sha256": database_sha256,
                   "database": original}
        pending_archive = stage / "current.zip"
        with zipfile.ZipFile(pending_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            bundle.write(database, "console.sqlite3")
            bundle.writestr("receipt.json", json.dumps(receipt, sort_keys=True))
        verify_archive(pending_archive, stage)
        result = {**receipt, "archive_bytes": pending_archive.stat().st_size,
                  "archive_sha256": digest(pending_archive)}
        pending_receipt = stage / "current.json"
        pending_receipt.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        # The atomic archive contains its own receipt; interruption cannot split that pair.
        # A stale external mirror is detected by --verify and repaired by a fresh capture.
        for pending in (pending_archive, pending_receipt):
            with pending.open("r+b") as stream:
                os.fsync(stream.fileno())
        ordinary_path(archive)
        ordinary_path(receipt_path)
        os.replace(pending_archive, archive)
        os.replace(pending_receipt, receipt_path)
        return result


def console_store():
    spec = importlib.util.spec_from_file_location("backup_console_store", REPO / "scripts/task_console/console_store.py")
    store = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(store)
    return store


def resolve_scheduled_source(source_db):
    """Resolve the launcher-selected DB through the console's existing domain resolver."""
    store = console_store()
    if source_db is None:
        path, state = store.resolve_db(create_parent=False)
        configured = any(key.startswith("TASK_CONSOLE_") and value for key, value in os.environ.items())
        if path is None and state and state.code == "NO_COMPANION" and not configured:
            return None
        raise ValueError("Initialized console requires the source DB from Read-TaskConsoleLauncherBinding")
    source = Path(source_db).expanduser()
    if not source.is_absolute():
        raise ValueError("Scheduled source DB must be absolute")
    source = ordinary_path(source)
    previous = os.environ.get("TASK_CONSOLE_DB")
    try:
        # JSON launcher bindings override ambient UI settings. Resolve using that exact binding.
        os.environ["TASK_CONSOLE_DB"] = str(source)
        resolved, state = store.resolve_db(create_parent=False)
    finally:
        if previous is None:
            os.environ.pop("TASK_CONSOLE_DB", None)
        else:
            os.environ["TASK_CONSOLE_DB"] = previous
    if state or resolved is None or ordinary_path(resolved) != source:
        raise ValueError("Console database resolver refused the launcher-selected source")
    if source.name != "console.sqlite3" or source.parent.name != "task-console":
        raise ValueError("Scheduled backup requires the reviewed task-console/console.sqlite3 layout")
    if not source.is_file():
        raise FileNotFoundError("Launcher-selected database is missing")
    return private_data_root(source.parent.parent)


def git(repo, *arguments, mutation=False):
    """Keep normal hooks and their complete output; stdout remains one JSON receipt."""
    result = subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=300)
    if mutation and result.stdout:
        sys.stderr.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"Git {arguments[0]} failed with status {result.returncode}")
    return result.stdout.strip() if "-z" not in arguments else result.stdout


def assert_snapshot_index(repo, pair):
    staged = set(filter(None, git(repo, "diff", "--cached", "--name-only", "-z").split("\0")))
    if staged - set(pair):
        raise ValueError("Other staged changes exist; refusing to include or disturb them")


def origin_target(repo):
    fetch = git(repo, "remote", "get-url", "--all", "origin").splitlines()
    push = git(repo, "remote", "get-url", "--push", "--all", "origin").splitlines()
    if len(fetch) != 1 or fetch != push:
        raise ValueError("Origin requires one identical effective fetch and push destination")
    return fetch[0]


def remote_main(repo, target):
    rows = git(repo, "ls-remote", "--exit-code", target, "refs/heads/main").splitlines()
    if len(rows) != 1 or rows[0].split()[1:] != ["refs/heads/main"]:
        raise ValueError("Origin must have exactly one main branch")
    return rows[0].split()[0]


def assert_pending_scope(repo, remote_head, local_head, pair):
    if remote_head == local_head:
        return []
    git(repo, "merge-base", "--is-ancestor", remote_head, local_head)
    pending = git(repo, "rev-list", f"{remote_head}..{local_head}").splitlines()
    for commit in pending:
        parents = git(repo, "rev-list", "--parents", "-n", "1", commit).split()[1:]
        changed = set(filter(None, git(repo, "diff-tree", "--root", "--no-commit-id", "--name-only",
                                      "-r", "-z", commit).split("\0")))
        if len(parents) != 1 or not changed or changed - set(pair):
            raise ValueError("Unpublished commits include changes outside the recovery pair")
    return pending


def committed_pair(repo, commit, pair):
    contents = []
    for path in pair:
        result = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", f"{commit}:{path}"],
                                capture_output=True, timeout=300)
        if result.returncode != 0:
            raise ValueError("Committed recovery pair is incomplete")
        contents.append(result.stdout)
    archive, raw_receipt = contents
    receipt = json.loads(raw_receipt)
    if len(archive) != receipt["archive_bytes"] or hashlib.sha256(archive).hexdigest() != receipt["archive_sha256"]:
        raise ValueError("Committed recovery archive differs from its receipt")
    return archive, receipt


def verify_pending_snapshot(root, repo, commit, pair):
    archive, receipt = committed_pair(repo, commit, pair)
    with tempfile.TemporaryDirectory(prefix="commit-check-", dir=root / "task-console/recovery") as temporary:
        stage = Path(temporary)
        saved = stage / "current.zip"
        saved.write_bytes(archive)
        embedded = verify_archive(saved, stage)
    expected = {**embedded, "archive_bytes": len(archive), "archive_sha256": hashlib.sha256(archive).hexdigest()}
    if expected != receipt:
        raise ValueError("Unpublished recovery commit has an inconsistent receipt; reconcile it explicitly")


def assert_main_head(repo, expected=None):
    if git(repo, "symbolic-ref", "--quiet", "--short", "HEAD") != "main":
        raise ValueError("Scheduled recovery publication requires main")
    head = git(repo, "rev-parse", "HEAD")
    if expected is not None and head != expected:
        raise ValueError("Companion HEAD changed during recovery capture")
    return head


def scheduled_backup(source_db=None):
    """Refresh and publish only the recovery pair from a reviewed launcher binding."""
    root = resolve_scheduled_source(source_db)
    if root is None:
        return {"schema_version": 1, "status": "skipped", "reason": "uninitialized"}
    routing = {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
               "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG_COUNT",
               "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"}
    if any(os.environ.get(name) for name in routing):
        raise ValueError("Scheduled backup refuses Git repository, index or configuration overrides")
    repo = ordinary_path(git(root, "rev-parse", "--show-toplevel"))
    if repo != root and repo not in root.parents:
        raise ValueError("Recovery DATA must be inside its proven private repository")
    pair = tuple((root / "task-console/recovery" / name).relative_to(repo).as_posix()
                 for name in ("current.zip", "current.json"))
    recovery = relative_path(root, "task-console/recovery")
    recovery.mkdir(parents=True, exist_ok=True)
    with snapshot_lock(recovery):
        before = assert_main_head(repo)
        assert_snapshot_index(repo, pair)
        target = origin_target(repo)
        published = remote_main(repo, target)
        pending = assert_pending_scope(repo, published, before, pair)
        for commit in pending:
            verify_pending_snapshot(root, repo, commit, pair)
        private_data_root(root)
        receipt = _backup_locked(root)
        assert_main_head(repo, before)
        assert_snapshot_index(repo, pair)
        private_data_root(root)
        if origin_target(repo) != target:
            raise ValueError("Origin changed during recovery capture")
        git(repo, "add", "--", *pair, mutation=True)
        assert_snapshot_index(repo, pair)
        changed = bool(git(repo, "diff", "--cached", "--name-only", "--", *pair))
        assert_main_head(repo, before)
        if changed:
            # --only prevents a concurrent actor's newly staged files entering this commit.
            git(repo, "commit", "--only", "-m", "Refresh verified console database recovery", "--", *pair,
                mutation=True)
        head = assert_main_head(repo)
        assert_snapshot_index(repo, pair)
        assert_pending_scope(repo, published, head, pair)
        _, committed = committed_pair(repo, head, pair)
        if committed != receipt:
            raise ValueError("Committed recovery pair differs from the verified capture; refusing publication")
        private_data_root(root)
        if origin_target(repo) != target:
            raise ValueError("Origin changed before recovery publication")
        if head != published:
            # Pin the reviewed commit even if another actor advances local HEAD after this check.
            git(repo, "push", target, f"{head}:refs/heads/main", mutation=True)
        if remote_main(repo, target) != head:
            raise ValueError("Remote main does not match the published recovery commit")
        return {"schema_version": 1, "status": "published" if changed or before != published else "unchanged",
                "commit": head, "archive_bytes": receipt["archive_bytes"],
                "archive_sha256": receipt["archive_sha256"], "captured_at_utc": receipt["captured_at_utc"],
                "database": {"quick_check": receipt["database"]["quick_check"]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--data-root")
    mode.add_argument("--scheduled", action="store_true")
    parser.add_argument("--source-db", help="Exact database selected by Read-TaskConsoleLauncherBinding")
    parser.add_argument("--verify", action="store_true", help="Validate the saved snapshot without refreshing it")
    args = parser.parse_args()
    if args.scheduled and args.verify or args.source_db and not args.scheduled:
        parser.error("--source-db requires --scheduled; --verify is a manual saved-snapshot check")
    result = scheduled_backup(args.source_db) if args.scheduled else backup(
        private_data_root(args.data_root), verify_only=args.verify)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
