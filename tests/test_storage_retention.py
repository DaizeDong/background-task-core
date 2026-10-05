"""Retirement cannot discard core state, recovery inputs, or unfinished work."""
from pathlib import Path
import sys

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import make_fixtures
import storage_retention as retention


def test_completed_work_is_admitted_without_touching_state_or_recovery(tmp_path):
    record = make_fixtures.storage_retirement_case(tmp_path)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = retention.plan(tmp_path, record)
    assert [e["relative_path"] for e in result["entries"]] == ["work/completed"]
    assert result["files"] == 1
    assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("target", ["launcher-binding.json", "task-console", "task-console/console.sqlite3",
    "task-console/console.sqlite3-wal", "task-console/run-events.jsonl",
    "task-console/registration/current.json", "declarations-example", "storage", "maintenance", "profile-rollback"])
def test_core_state_and_restore_inputs_cannot_be_retired(tmp_path, target):
    record = make_fixtures.storage_retirement_case(tmp_path)
    record["retirements"][0]["path"] = target
    with pytest.raises(retention.RetentionError, match="Core"):
        retention.plan(tmp_path, record)


@pytest.mark.parametrize("field,value", [("completed", False), ("source_reconciled", False), ("active_writer", True)])
def test_unfinished_source_or_active_writer_refuses(tmp_path, field, value):
    record = make_fixtures.storage_retirement_case(tmp_path)
    record["retirements"][0][field] = value
    with pytest.raises(retention.RetentionError):
        retention.plan(tmp_path, record)


def test_modified_result_refuses_and_preserved_other_input_blocks_parent(tmp_path):
    record = make_fixtures.storage_retirement_case(tmp_path)
    record["protected_paths"] = ["work/completed/build-output.txt"]
    with pytest.raises(retention.RetentionError, match="protected"):
        retention.plan(tmp_path, record)
    record["protected_paths"] = []
    (tmp_path / "storage/final-results.md").write_text("changed", encoding="utf-8")
    with pytest.raises(retention.RetentionError, match="changed"):
        retention.plan(tmp_path, record)


@pytest.mark.parametrize("target", ["../other", "/outside", "C:/outside", "work/../task-console"])
def test_paths_cannot_escape(tmp_path, target):
    record = make_fixtures.storage_retirement_case(tmp_path)
    record["retirements"][0]["path"] = target
    with pytest.raises(retention.RetentionError):
        retention.plan(tmp_path, record)


def test_reparse_metadata_refuses_before_enumeration(tmp_path, monkeypatch):
    record = make_fixtures.storage_retirement_case(tmp_path)
    original = Path.lstat
    target = tmp_path / "work/completed"

    def metadata(path):
        info = original(path)
        if path == target:
            from types import SimpleNamespace
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, "lstat", metadata)
    with pytest.raises(retention.RetentionError, match="Reparse"):
        retention.plan(tmp_path, record)


def test_changed_artifacts_change_plan_and_unknown_work_is_preserved(tmp_path):
    record = make_fixtures.storage_retirement_case(tmp_path)
    first = retention.plan(tmp_path, record)
    (tmp_path / "work/unreviewed").mkdir()
    (tmp_path / "work/unreviewed/source.py").write_text("unfinished = True\n")
    assert retention.plan(tmp_path, record) == first
    (tmp_path / "work/completed/build-output.txt").write_text("new output")
    assert retention.plan(tmp_path, record) != first


def test_retirement_is_idempotent_and_record_count_is_bounded(tmp_path):
    record = make_fixtures.storage_retirement_case(tmp_path)
    (tmp_path / "work/completed/build-output.txt").unlink()
    (tmp_path / "work/completed").rmdir()
    assert retention.plan(tmp_path, record)["entries"] == []
    record["retirements"] *= retention.MAX_RETIREMENTS + 1
    with pytest.raises(retention.RetentionError, match="bounded"):
        retention.plan(tmp_path, record)


def test_cross_referenced_final_results_cannot_delete_each_other(tmp_path):
    import hashlib

    record = make_fixtures.storage_retirement_case(tmp_path)
    other = tmp_path / "work/other"
    other.mkdir()
    result = other / "final.md"
    result.write_text("synthetic final result", encoding="utf-8")
    row = record["retirements"][0]
    second = dict(row, path="work/other")
    row.update(final_deliverable="work/other/final.md",
               final_sha256=hashlib.sha256(result.read_bytes()).hexdigest())
    record["retirements"].append(second)
    with pytest.raises(retention.RetentionError, match="another retirement"):
        retention.plan(tmp_path, record)
