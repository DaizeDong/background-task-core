"""Task identity, explicit machine facts and monitoring survive compilation."""
from copy import deepcopy
import importlib
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))
from make_fixtures import example_request


def compiler():
    assert importlib.util.find_spec("task_console.components"), "component compiler is required"
    return importlib.import_module("task_console.components")


def compile_request(request):
    return compiler().compile_tasks(request["components"], request["bindings"], request["machine"])


def test_machine_facts_are_not_replaced_with_recommended_defaults():
    request = example_request()
    before = deepcopy(request)
    tasks = compile_request(request)
    assert len(tasks) == 1
    task = tasks[0].to_dict()
    assert task["task_id"] == "acme-maintenance/sync"
    assert task["name"] == "AcmeSync" and task["enabled"] is False
    assert task["trigger"] == {"type": "daily", "at": "03:15"}
    assert task["schedule_hint"] == {"type": "interval", "minutes": 60}
    assert task["timeout_seconds"] == 420
    assert task["concurrency"] == {"policy": "IgnoreNew"}
    assert task["principal"]["logon_type"] == "InteractiveToken"
    assert task["power"]["stop_on_batteries"] is False
    assert [c["id"] for c in task["checks"]] == ["output", "dependencies"]
    assert request == before


def test_machine_override_and_xml_preserve_owner_and_bytes():
    request = example_request()
    xml = {"xml": '<Task version="1.4"><Settings><RestartOnFailure/></Settings></Task>',
           "owner": "acme-operator", "reason": "Preserve scheduler-specific settings"}
    request["machine"]["overrides"]["acme-maintenance/sync"] = {
        "trigger": {"type": "xml", **xml}, "xml_passthrough": xml, "timeout_seconds": 0}
    task = compile_request(request)[0].to_dict()
    assert task["trigger"] == {"type": "xml", **xml}
    assert task["xml_passthrough"] == xml and task["timeout_seconds"] == 0


@pytest.mark.parametrize("mutation, code", [
    (lambda r: r["components"][0].update(schemaVersion=2), "unsupported_schema"),
    (lambda r: r["components"][0]["tasks"].append(deepcopy(r["components"][0]["tasks"][0])), "duplicate_task"),
    (lambda r: r["components"][0]["tasks"][0]["checks"].append({"id": "output", "required": False}), "duplicate_check"),
    (lambda r: r["bindings"]["tasks"].clear(), "missing_binding"),
    (lambda r: r["bindings"]["tasks"]["acme-maintenance/sync"].update(argv="python sync.py"), "invalid_argv"),
    (lambda r: r["bindings"]["tasks"]["acme-maintenance/sync"].pop("enabled"), "missing_field"),
    (lambda r: r["bindings"]["tasks"]["acme-maintenance/sync"].update(enabled="false"), "invalid_type"),
    (lambda r: r["bindings"]["tasks"]["acme-maintenance/sync"].update(trigger={"type": "cron", "expression": "* * * * *"}), "unsupported_trigger"),
    (lambda r: r["bindings"]["tasks"]["acme-maintenance/sync"].update(trigger={"type": "xml", "xml": "<Task/>"}), "missing_field"),
    (lambda r: r["machine"]["overrides"].update({"unknown/task": {"enabled": True}}), "unknown_task"),
    (lambda r: r["machine"].update(migrated_tasks=["acme-maintenance/sync"]), "unsupported_authority"),
])
def test_ambiguous_or_incomplete_inputs_are_explicit_errors(mutation, code):
    request = example_request()
    mutation(request)
    module = compiler()
    with pytest.raises(module.ContractError) as error:
        compile_request(request)
    assert error.value.code == code


def test_two_existing_daemons_keep_distinct_bindings_and_external_monitoring():
    request = example_request()
    manifest = request["components"][0]
    manifest["tasks"] = [dict(manifest["tasks"][0], id=i, kind="daemon") for i in ("worker", "worker2")]
    binding = request["bindings"]["tasks"].pop("acme-maintenance/sync")
    request["bindings"]["tasks"] = {
        "acme-maintenance/" + i: dict(deepcopy(binding), name=name, checks=[
            {"id": "output", "watched_elsewhere": {"component": "acme-monitor", "check_id": i,
                                                   "observation_ref": "acme://observations/" + i}}])
        for i, name in (("worker", "AcmeRunner"), ("worker2", "AcmeRunner2"))}
    tasks = [t.to_dict() for t in compile_request(request)]
    assert [t["name"] for t in tasks] == ["AcmeRunner", "AcmeRunner2"]
    assert all(t["kind"] == "daemon" for t in tasks)
    assert [t["checks"][0]["watched_elsewhere"]["check_id"] for t in tasks] == ["worker", "worker2"]
    assert tasks[0]["checks"][1]["binding_state"] == "unbound"


def test_duplicate_os_names_cannot_overwrite_another_task():
    request = example_request()
    request["components"][0]["tasks"].append(dict(request["components"][0]["tasks"][0], id="other"))
    request["bindings"]["tasks"]["acme-maintenance/other"] = deepcopy(request["bindings"]["tasks"]["acme-maintenance/sync"])
    with pytest.raises(compiler().ContractError) as error:
        compile_request(request)
    assert error.value.code == "duplicate_name"


def test_exported_multiline_xml_round_trips_without_reformatting():
    request = example_request()
    xml = '<Task version="1.4">\r\n  <Triggers/>\r\n  <Settings/>\r\n</Task>'
    request["bindings"]["tasks"]["acme-maintenance/sync"]["xml_passthrough"] = {
        "xml": xml, "owner": "acme-operator", "reason": "Keep exported settings"}
    assert compile_request(request)[0].to_dict()["xml_passthrough"]["xml"] == xml


def test_empty_task_set_cannot_claim_full_compilation():
    request = example_request()
    request["components"][0]["tasks"] = []
    request["bindings"]["tasks"] = {}
    with pytest.raises(compiler().ContractError) as error:
        compile_request(request)
    assert error.value.code == "missing_tasks"
