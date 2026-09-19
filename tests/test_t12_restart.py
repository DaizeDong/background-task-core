"""Actual producer calls after process loss, using freshly loaded fake adapters."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from test_registration_transaction import Crash, api
from t12_restart_support import initialize, reopen


@pytest.mark.parametrize("boundary", ["journal_created", "staged", "intent", "published", "committed", "cleanup"])
def test_process_death_then_fresh_runtime_recovers(tmp_path, boundary):
    runtime, ids = initialize(tmp_path, enable=True)
    before = {key: runtime.files.read(key)["value"] for key in runtime.load()["paths"].values()}
    child = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("t12_restart_support.py")),
                            str(tmp_path), boundary, "apply"],
                           capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    assert child.returncode == 73, child.stderr
    fresh = reopen(tmp_path)
    assert fresh is not runtime and fresh.journal is not runtime.journal
    tx = next(iter(fresh.journal.items))
    calls = []
    with pytest.raises(api().Conflict, match="recovery_required"):
        api().dispatch("AcmeSync", "enable", legacy=lambda: calls.append("enable"), runtime=fresh)
    assert calls == []
    result = api().recover(tx, runtime=fresh)
    terminal = "committed" if boundary in ("committed", "cleanup") else "rolled_back"
    assert result["status"] == terminal and result["cleaned"]
    again = reopen(tmp_path)
    assert api().recover(tx, runtime=again)["status"] == terminal
    assert again.journal.pending(["authority"]) == []
    assert not again.files.stages and not again.scheduler.stages
    if terminal == "rolled_back":
        assert {key: again.files.read(key)["value"] for key in before} == before
        assert again.scheduler.read("AcmeSync")["value"]["enabled"] is False
    else:
        assert again.scheduler.read("AcmeSync")["value"]["enabled"] is True
        assert json.loads(again.files.read("machine.json")["value"])["migrated_tasks"] == ids


@pytest.mark.parametrize("disabled_while_running", [False, True])
def test_running_after_crash_defers_files_until_idle_on_fresh_recovery(tmp_path, disabled_while_running):
    runtime, ids = initialize(tmp_path, enable=True)
    plan = api().build_plan(runtime, ids, migrate=True, approve_enable=True)
    before = {key: runtime.files.read(key)["value"] for key in runtime.load()["paths"].values()}

    def crash(point):
        if point == "published" and runtime.scheduler.read("AcmeSync")["value"]["enabled"]:
            raise Crash()

    runtime.checkpoint = crash
    with pytest.raises(Crash):
        api().apply(plan, plan["input_revision"], runtime=runtime)
    fresh = reopen(tmp_path)
    tx = next(iter(fresh.journal.items))
    support = {key: fresh.files.read(key) for key in before}
    fresh.scheduler.items["AcmeSync"]["value"]["running"] = True
    if disabled_while_running:
        fresh.scheduler.items["AcmeSync"]["value"]["enabled"] = False
    fresh.scheduler.flush()
    blocked = api().recover(tx, runtime=reopen(tmp_path))
    assert blocked["status"] == "conflict" and not blocked["cleaned"]
    assert {key: reopen(tmp_path).files.read(key) for key in before} == support
    # Simulate the external execution ending. Restore only its volatile state;
    # the optional external config edit must still produce a conflict.
    idle = reopen(tmp_path)
    idle.scheduler.items["AcmeSync"]["value"]["running"] = False
    idle.scheduler.flush()
    result = api().recover(tx, runtime=reopen(tmp_path))
    if disabled_while_running:
        assert result["status"] == "conflict"
        assert any(c["code"] == "concurrent_edit" for c in result["conflicts"])
    else:
        assert result["status"] == "rolled_back"
        assert {key: reopen(tmp_path).files.read(key)["value"] for key in before} == before


def test_absent_retirement_process_death_does_not_create_task(tmp_path):
    runtime, ids = initialize(tmp_path, absent_retire=True)
    child = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("t12_restart_support.py")),
                            str(tmp_path), "published", "retire"],
                           capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    assert child.returncode == 73, child.stderr
    fresh = reopen(tmp_path)
    assert fresh.scheduler.read("AcmeSync") == {"state": "absent"}
    assert fresh.scheduler.calls == []
    tx = next(iter(fresh.journal.items))
    assert api().recover(tx, runtime=fresh)["status"] == "rolled_back"
    assert reopen(tmp_path).scheduler.read("AcmeSync") == {"state": "absent"}


@pytest.mark.parametrize("boundary", ["staged", "intent", "published"])
def test_death_at_activation_recovers_with_fresh_adapters(tmp_path, boundary):
    runtime, ids = initialize(tmp_path, enable=True)
    plan = api().build_plan(runtime, ids, migrate=True, approve_enable=True)
    # One disabled Scheduler image, all changed files, then one enable image.
    position = len(plan["changes"]["files"]) + 2
    child = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("t12_restart_support.py")),
                            str(tmp_path), boundary, "apply", str(position)],
                           capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    assert child.returncode == 73, child.stderr
    fresh = reopen(tmp_path)
    tx = next(iter(fresh.journal.items))
    assert api().recover(tx, runtime=fresh)["status"] == "rolled_back"
    assert reopen(tmp_path).scheduler.read("AcmeSync")["value"]["enabled"] is False
    assert json.loads(reopen(tmp_path).files.read("machine.json")["value"])["migrated_tasks"] == []


@pytest.mark.parametrize("boundary", ["undo_intent", "undone"])
def test_recovery_itself_crashes_then_reopens_after_enable(tmp_path, boundary):
    runtime, ids = initialize(tmp_path, enable=True)
    plan = api().build_plan(runtime, ids, migrate=True, approve_enable=True)

    def crash_enable(point):
        if point == "published" and runtime.scheduler.read("AcmeSync")["value"]["enabled"]:
            raise Crash()

    runtime.checkpoint = crash_enable
    with pytest.raises(Crash):
        api().apply(plan, plan["input_revision"], runtime=runtime)
    fresh = reopen(tmp_path)
    tx = next(iter(fresh.journal.items))

    def crash_undo(point):
        if point == boundary:
            raise Crash()

    fresh.checkpoint = crash_undo
    with pytest.raises(Crash):
        api().recover(tx, runtime=fresh)
    assert api().recover(tx, runtime=reopen(tmp_path))["status"] == "rolled_back"
    final = reopen(tmp_path)
    assert final.scheduler.read("AcmeSync")["value"]["enabled"] is False
    assert not final.files.stages and not final.scheduler.stages
