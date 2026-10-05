"""Refresh or verify one consistent, compressed console database recovery snapshot."""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import zipfile

from storage_retention import ordinary_path, private_data_root, relative_path


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
    archive = relative_path(root, "task-console/recovery/current.zip")
    receipt_path = relative_path(root, "task-console/recovery/current.json")
    if not verify_only and not source.is_file():
        raise FileNotFoundError("Current database is missing; refusing to create an empty backup")
    recovery.mkdir(parents=True, exist_ok=True)
    with snapshot_lock(recovery), tempfile.TemporaryDirectory(prefix="snapshot-", dir=recovery) as temporary:
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
        receipt = {"schema_version": 1, "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                   "database_bytes": database.stat().st_size, "database_sha256": digest(database),
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--verify", action="store_true", help="Validate the saved snapshot without refreshing it")
    args = parser.parse_args()
    print(json.dumps(backup(private_data_root(args.data_root), verify_only=args.verify), sort_keys=True))


if __name__ == "__main__":
    main()
