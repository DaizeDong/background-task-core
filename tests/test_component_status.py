"""Versioned read adapters, including CLI versus the HTTP-facing evaluator."""
from copy import deepcopy
import io
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "task_console"))
sys.path.insert(0, str(ROOT))
from tools.make_fixtures import catalog_snapshot, health_case, legacy_health_snapshot
import component_status as C
import freshness


def test_catalog_preserves_typed_entries_and_independent_statuses():
    source = catalog_snapshot()
    result = C.catalog_view(source)
    assert result["coverage"] == source["coverage"]
    assert result["statistics"] == {"skill": 1, "plugin": 1, "mcp_binding": 1}
    assert result["records"][1]["entrypoints"][0]["kind"] == "agent_template"
    assert result["records"][1]["entrypoints"][0]["source_id"] == source["records"][1]["source_id"]
    assert result["records"][1]["status"]["discovered"] == "unknown"
    assert result["records"][1]["entrypoints"][0]["status"]["discovered"] == "yes"
    assert "authenticated" not in result["records"][1]["status"]
    assert result["records"][2]["status"]["authenticated"] == "unknown"
    assert "source_id" not in source["records"][1]["entrypoints"][0]


def test_authentication_is_connector_specific():
    snapshot = catalog_snapshot()
    connector = snapshot["records"][-1]
    connector["status"]["authenticated"] = "yes"
    unchecked = deepcopy(connector)
    unchecked["source_id"] = "synthetic:second-connector"
    unchecked["status"].pop("authenticated")
    snapshot["records"].append(unchecked)
    result = C.catalog_view(snapshot)
    assert [r["status"]["authenticated"] for r in result["records"][-2:]] == ["yes", "unknown"]


def test_missing_catalog_is_different_from_checked_empty():
    assert C.catalog_view(None)["available"] is False
    snapshot = catalog_snapshot()
    snapshot["records"] = []
    empty = C.catalog_view(snapshot)
    assert empty["available"] is True
    assert empty["records"] == []


@pytest.mark.parametrize("mutation", ["version", "records", "status"])
def test_catalog_rejects_malformed_contract(mutation):
    snapshot = catalog_snapshot()
    if mutation == "version":
        snapshot["schema_version"] = 2
    elif mutation == "records":
        snapshot["records"] = {}
    else:
        snapshot["records"][0]["status"]["enabled"] = True
    with pytest.raises(ValueError):
        C.catalog_view(snapshot)


def test_cli_and_ui_consume_identical_fixture_verdicts(monkeypatch, capsys):
    fixture = health_case()
    fixture["observations"]["checks"][0]["state"] = "unhealthy"
    snapshot = {"schemaVersion": 1, "tasks": [fixture], "catalog": catalog_snapshot()}
    expected = C.evaluate_snapshot(snapshot, fixture["now"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(snapshot)))
    assert C.main(["--now", str(fixture["now"])]) == 0
    assert json.loads(capsys.readouterr().out) == expected
    assert expected["tasks"][0]["verdict"] == "unhealthy"


def test_configured_component_entry_matches_http_without_reading_stdin(tmp_path, monkeypatch, capsys):
    fixture = health_case()
    path = tmp_path / 'status.json'
    path.write_text(json.dumps({'schemaVersion': 1, 'tasks': [fixture]}))
    monkeypatch.setenv('TASK_CONSOLE_STATUS_SNAPSHOT', str(path))
    monkeypatch.delenv('TASK_CONSOLE_CATALOG_SNAPSHOT', raising=False)
    monkeypatch.setattr(sys, 'stdin', None)
    assert C.main(['--configured', '--now', str(fixture['now'])]) == 0
    assert json.loads(capsys.readouterr().out) == C.read_configured(fixture['now'])


def test_missing_configured_reader_is_visible_without_stdin(monkeypatch, capsys):
    monkeypatch.delenv('TASK_CONSOLE_STATUS_SNAPSHOT', raising=False)
    monkeypatch.delenv('TASK_CONSOLE_CATALOG_SNAPSHOT', raising=False)
    monkeypatch.setattr(sys, 'stdin', None)
    assert C.main(['--configured']) == 2
    result = json.loads(capsys.readouterr().out)
    assert not result['available'] and result['coverage']['checked'] == 0


def test_legacy_monitor_and_freshness_use_one_evaluator(monkeypatch, capsys):
    snapshot = legacy_health_snapshot()
    now = health_case()["now"]
    expected = freshness.evaluate(snapshot["declarations"], snapshot["rows"], now,
                                   mtime_of=lambda path: (snapshot["artifact_observations"][path]["mtime"], None))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(snapshot)))
    assert C.main(["--now", str(now)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["tasks"] == expected["tasks"]
    assert result["summary"] == expected["summary"]


def test_reader_failure_is_nonzero_not_health_failure(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"schemaVersion":99}'))
    assert C.main([]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["reason_code"] == "reader_failed"


def test_configured_missing_source_remains_visible(monkeypatch):
    monkeypatch.setenv("TASK_CONSOLE_STATUS_SNAPSHOT", "synthetic-missing.json")
    monkeypatch.delenv("TASK_CONSOLE_CATALOG_SNAPSHOT", raising=False)
    monkeypatch.setattr(C, "read_snapshot", lambda _: (_ for _ in ()).throw(FileNotFoundError()))
    result = C.read_configured()
    assert not result["available"]
    assert result["reason_code"] == "query_failed"
    assert result["coverage"]["state"] == "zero"


def test_report_commands_never_become_observation_instructions(monkeypatch):
    fixture = health_case()
    fixture["spec"].update(read="synthetic-command", argv=["synthetic-executable"])
    fixture["observations"]["artifact"] = "synthetic-report-path"
    monkeypatch.setattr(C, "read_snapshot", lambda _: pytest.fail("report path was opened"))
    result = C.evaluate_snapshot({"schemaVersion": 1, "tasks": [fixture]}, fixture["now"])
    assert result["tasks"][0]["verdict"] == "healthy"
    assert result["authority"] == "observation_only"


def test_expected_tasks_cannot_shrink_with_missing_reports():
    fixture = health_case()
    result = C.evaluate_snapshot({"schemaVersion": 1, "tasks": [fixture], "expected": 3}, fixture["now"])
    assert result["coverage"]["expected"] == 3
    assert result["coverage"]["checked"] == 1
