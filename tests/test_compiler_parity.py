"""Read-only projection, field parity, multiple checks and offline JSON CLI."""
from copy import deepcopy
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))
from make_fixtures import example_request, generate


def plan(request):
    assert importlib.util.find_spec("task_console.compiler"), "read-only compiler is required"
    return importlib.import_module("task_console.compiler").plan(request)


def test_legacy_parity_retains_each_check_and_has_no_side_effects(tmp_path, monkeypatch):
    request = example_request()
    before = deepcopy(request)
    monkeypatch.chdir(tmp_path)
    result = plan(request)
    assert result["mode"] == "read-only" and result["applicable"] is False
    assert result["authority"] == {"mode": "legacy", "epoch": 0, "migrated_tasks": []}
    assert all(p["status"] == "equal" for p in result["parity"].values())
    assert result["changes"] == []
    assert result["check_coverage"]["declared"] == 2
    assert result["check_coverage"]["matched"] == 2
    rows = json.loads(result["generated_files"]["task-health.json"]["content"])["tasks"]
    assert [r["check_id"] for r in rows] == ["output", "dependencies"]
    assert [r["max_age_hours"] for r in rows] == [26, 48]
    assert {r["name"] for r in rows} == {"AcmeSync"}
    assert request == before and list(tmp_path.iterdir()) == []
    assert plan(request) == result


@pytest.mark.parametrize("field, value", [
    ("enabled", True), ("trigger", {"type": "logon"}),
    ("argv", ["C:/Acme/runtime/other.exe"]), ("cwd", "C:/Acme/other"),
    ("timezone", "Etc/GMT+1"), ("principal", {"user_id": "AcmeOther"}),
    ("power", {"wake_to_run": True}), ("timeout_seconds", 600),
    ("concurrency", {"policy": "Parallel"}),
])
def test_scheduler_fields_are_compared_individually(field, value):
    request = example_request()
    request["bindings"]["tasks"]["acme-maintenance/sync"][field] = value
    result = plan(request)
    change = next(c for c in result["changes"] if c.get("field") == field)
    assert change["task_id"] == "acme-maintenance/sync" and change["after"] == value
    assert change["before"] == request["baseline"]["tasks"][0][field]
    if field == "enabled":
        assert change["blocked"] and change["reason_code"] == "disabled_task_requires_review"


def test_unknown_tasks_produce_adoption_proposals_only():
    request = example_request()
    request["baseline"]["tasks"].append({"name": "AcmeManual", "enabled": False})
    result = plan(request)
    assert result["adopt_proposals"][0]["name"] == "AcmeManual"
    assert result["adopt_proposals"][0]["automatic"] is False
    assert not any(c.get("name") == "AcmeManual" for c in result["changes"])
    assert len(result["task_specs"]) == 1


def test_absent_and_bad_legacy_sources_do_not_pass_parity():
    request = example_request()
    request["baseline"] = {"task_names": "$TaskNames = @()", "task_health": {"tasks": "bad"}}
    result = plan(request)
    assert result["parity"]["task_names"]["status"] == "not-checked"
    assert result["parity"]["task_health"]["status"] == "invalid"
    assert result["parity"]["categories"]["status"] == "not-checked"
    assert result["parity"]["scheduler"]["status"] == "not-checked"
    assert result["check_coverage"]["matched"] == 0


def test_same_named_checks_are_compared_as_a_multiset():
    request = example_request()
    request["baseline"]["task_health"]["tasks"][0]["max_age_hours"] = 99
    result = plan(request)
    assert result["parity"]["task_health"]["status"] == "different"
    assert result["check_coverage"]["matched"] == 1
    assert len(result["check_coverage"]["checks"]) == 2


def test_baseline_revision_covers_raw_snapshots_and_input_revision_covers_bindings():
    request = example_request()
    first = plan(request)
    request["baseline"]["task_names"] += "# changed bytes\n"
    second = plan(request)
    assert second["baseline_revision"] != first["baseline_revision"]
    request["bindings"]["tasks"]["acme-maintenance/sync"]["timeout_seconds"] += 1
    third = plan(request)
    assert third["baseline_revision"] == second["baseline_revision"]
    assert third["input_revision"] != second["input_revision"]


def test_json_cli_reads_explicit_files_without_importing_web_server(tmp_path):
    generate(tmp_path)
    source = tmp_path
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    env = dict(os.environ, PYTHONPATH=str(ROOT / "scripts"), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([
        sys.executable, "-B", "-m", "task_console", "plan", "--component", str(source / ".console.json"),
        "--bindings", str(source / "bindings.example.json"), "--machine", str(source / "machine.console.example.json"),
        "--baseline", str(source / "baseline.example.json"),
    ], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["parity"]["scheduler"]["status"] == "equal"
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before


def test_cli_errors_are_json_and_do_not_echo_invalid_input(capsys, monkeypatch):
    import io
    assert importlib.util.find_spec("task_console.__main__"), "JSON CLI is required"
    main = importlib.import_module("task_console.__main__").main
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"private-example":"unclosed'))
    assert main(["plan"]) == 2
    result = capsys.readouterr()
    assert json.loads(result.out)["error"]["code"] == "invalid_json"
    assert "private-example" not in result.out


def test_generated_digest_verifies_actual_file_bytes():
    result = plan(example_request())
    for generated in result["generated_files"].values():
        digest = hashlib.sha256(generated["content"].encode("utf-8")).hexdigest()
        assert generated["digest"] == "sha256:" + digest


def test_check_coverage_distinguishes_unreadable_from_different():
    request = example_request()
    request["baseline"]["task_health"] = None
    checks = plan(request)["check_coverage"]["checks"]
    assert {c["state"] for c in checks} == {"not-checked"}
    assert {c["reason_code"] for c in checks} == {"missing_source"}


def test_duplicate_json_keys_fail_instead_of_selecting_the_last_value(capsys, monkeypatch):
    import io
    main = importlib.import_module("task_console.__main__").main
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"schemaVersion": 2, "schemaVersion": 1}'))
    assert main(["plan"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "duplicate_json_key"


def test_examples_are_generated_byte_for_byte(tmp_path):
    generate(tmp_path)
    manifest = json.loads((ROOT / ".dataclass.json").read_text(encoding="utf-8"))
    for relative in manifest["fixture"]:
        assert (ROOT / relative).read_bytes() == (tmp_path / Path(relative).name).read_bytes()


def test_identical_checks_cannot_reuse_one_legacy_observation():
    request = example_request()
    binding = request["bindings"]["tasks"]["acme-maintenance/sync"]
    binding["checks"][1]["legacy"] = deepcopy(binding["checks"][0]["legacy"])
    request["baseline"]["task_health"]["tasks"].pop()
    result = plan(request)
    assert result["check_coverage"]["declared"] == 2
    assert result["check_coverage"]["matched"] == 1
    assert result["parity"]["task_health"]["status"] == "different"


def test_bad_health_code_shape_is_a_contract_error_not_an_uncaught_type_error():
    from task_console.contracts import ContractError
    request = example_request()
    request["bindings"]["tasks"]["acme-maintenance/sync"]["checks"][0]["legacy"]["ok_codes"] = 3
    with pytest.raises(ContractError) as error:
        plan(request)
    assert error.value.code == "invalid_monitor"
