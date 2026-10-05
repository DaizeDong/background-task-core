"""A saved snapshot must preserve WAL data and remain recoverable after failed refreshes."""
from contextlib import closing
import json
from pathlib import Path
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import backup_console_db as recovery
import make_fixtures


def test_online_snapshot_captures_wal_schema_and_all_tables(tmp_path):
    with closing(make_fixtures.console_backup_case(tmp_path)) as live:
        assert (tmp_path / "task-console/console.sqlite3-wal").stat().st_size > 0
        original = recovery.metadata(live)
        result = recovery.backup(tmp_path)
        assert result["database"] == original
        assert recovery.metadata(live) == original
        assert recovery.backup(tmp_path, verify_only=True) == result
        with zipfile.ZipFile(tmp_path / "task-console/recovery/current.zip") as bundle:
            assert sorted(bundle.namelist()) == ["console.sqlite3", "receipt.json"]


def test_refresh_replaces_one_snapshot_and_rejects_concurrent_capture(tmp_path):
    with closing(make_fixtures.console_backup_case(tmp_path)) as live:
        recovery.backup(tmp_path)
        directory = tmp_path / "task-console/recovery"
        old = (directory / "current.zip").read_bytes()
        with recovery.snapshot_lock(directory), pytest.raises(OSError):
            recovery.backup(tmp_path)
        assert (directory / "current.zip").read_bytes() == old
        live.execute("INSERT INTO events VALUES (2, 'AcmeReview')")
        live.commit()
        result = recovery.backup(tmp_path)
        assert result["database"]["tables"]["events"] == 2
        assert sorted(p.name for p in directory.iterdir()) == ["current.json", "current.zip", "snapshot.lock"]


def test_backup_timeout_keeps_previous_verified_snapshot(tmp_path):
    with closing(make_fixtures.console_backup_case(tmp_path)):
        before = recovery.backup(tmp_path)
        with pytest.raises(TimeoutError):
            recovery.backup(tmp_path, timeout=-1)
        assert recovery.backup(tmp_path, verify_only=True) == before


def test_missing_database_never_creates_an_empty_snapshot(tmp_path):
    with pytest.raises(FileNotFoundError, match="missing"):
        recovery.backup(tmp_path)
    assert not (tmp_path / "task-console/recovery").exists()


def test_interrupted_receipt_publication_is_detected_and_refresh_repairs(tmp_path, monkeypatch):
    with closing(make_fixtures.console_backup_case(tmp_path)) as live:
        recovery.backup(tmp_path)
        live.execute("INSERT INTO events VALUES (2, 'AcmeReview')")
        live.commit()
        replace = recovery.os.replace

        def interrupt(source, target):
            if target.name == "current.json":
                raise OSError("synthetic receipt publication failure")
            replace(source, target)

        monkeypatch.setattr(recovery.os, "replace", interrupt)
        with pytest.raises(OSError, match="synthetic"):
            recovery.backup(tmp_path)
        with pytest.raises(ValueError, match="External receipt"):
            recovery.backup(tmp_path, verify_only=True)
        monkeypatch.setattr(recovery.os, "replace", replace)
        result = recovery.backup(tmp_path)
        assert result["database"]["tables"]["events"] == 2
        assert recovery.backup(tmp_path, verify_only=True) == result


def test_corrupt_saved_database_is_rejected(tmp_path):
    with closing(make_fixtures.console_backup_case(tmp_path)):
        result = recovery.backup(tmp_path)
        archive = tmp_path / "task-console/recovery/current.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("receipt.json", json.dumps(result))
            bundle.writestr("console.sqlite3", b"synthetic corrupt database")
        with pytest.raises(ValueError, match="digest"):
            recovery.backup(tmp_path, verify_only=True)
