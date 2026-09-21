"""Review reproductions against real orchestration and synthetic transports."""
from copy import deepcopy
import json

import pytest

from test_registration_transaction import Crash, api, migrate, refresh, rig
from tools.make_fixtures import category_description_case


def test_pending_migration_blocks_legacy_dispatch_inside_authority_lock(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    plan = reg.build_plan(runtime, ids, migrate=True)

    def crash(point):
        if point == "published":
            raise Crash()

    runtime.checkpoint = crash
    with pytest.raises(Crash):
        reg.apply(plan, plan["input_revision"], runtime=runtime)
    assert runtime.journal.pending(["authority"])
    assert bundle["request"]["machine"]["migrated_tasks"] == []
    calls = []
    pending = runtime.journal.pending

    def locked_pending(keys):
        assert "authority" in runtime.locks.active
        return pending(keys)

    runtime.journal.pending = locked_pending
    with pytest.raises(reg.Conflict, match="recovery_required"):
        reg.dispatch("AcmeSync", "enable", legacy=lambda: calls.append("enable"), runtime=runtime)
    assert calls == []


@pytest.mark.parametrize("changed", ["health.json", "retired.json"])
def test_complete_file_generation_verified_before_enable(tmp_path, changed):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    bundle["request"]["bindings"]["tasks"][ids[0]]["enabled"] = True
    # The tombstone is already in canonical form, so it has no publication step.
    (tmp_path / "retired.json").write_bytes(b"{}\n")
    bundle["file_ownership"]["retired.json"] = reg.fingerprint(runtime.files.read("retired.json"))
    plan = reg.build_plan(runtime, ids, migrate=True, approve_enable=True)
    if changed == "retired.json":
        assert changed not in [row["path"] for row in plan["changes"]["files"]]
    publish_file, publish_task = runtime.files.publish, runtime.scheduler.publish
    enables, edits = [], []

    def edit(key, expected, desired):
        publish_file(key, expected, desired)
        if key == "categories.json":
            (tmp_path / changed).write_bytes(b'{"synthetic_edit":true}')
            edits.append(key)

    def observe_enable(key, expected, desired):
        if desired.get("value", {}).get("enabled"):
            enables.append(key)
        publish_task(key, expected, desired)

    runtime.files.publish, runtime.scheduler.publish = edit, observe_enable
    result = reg.apply(plan, plan["input_revision"], runtime=runtime)
    assert edits
    assert enables == []
    assert result["status"] == "conflict"
    assert (tmp_path / changed).read_bytes() == b'{"synthetic_edit":true}'


def test_running_task_defers_supporting_resource_rollback(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    bundle["request"]["bindings"]["tasks"][ids[0]]["enabled"] = True
    plan = reg.build_plan(runtime, ids, migrate=True, approve_enable=True)
    publish = runtime.scheduler.publish

    def start_running(key, expected, desired):
        publish(key, expected, desired)
        if desired.get("value", {}).get("enabled"):
            runtime.scheduler.items[key]["value"]["running"] = True

    runtime.scheduler.publish = start_running
    result = reg.apply(plan, plan["input_revision"], runtime=runtime)
    assert result["status"] == "conflict"
    assert runtime.scheduler.read("AcmeSync")["value"]["running"] is True
    assert json.loads(runtime.files.read("bindings.json")["value"])["tasks"][ids[0]]["enabled"] is True
    assert json.loads(runtime.files.read("machine.json")["value"])["migrated_tasks"] == ids
    assert runtime.journal.pending(["authority"])
    assert result["cleaned"] is False


def test_retiring_absent_task_keeps_it_absent_and_removes_export(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    migrate(runtime, bundle, ids)
    runtime.files.seed("active.xml", b"<Task />")
    bundle["active_xml"][ids[0]] = "active.xml"
    bundle["file_ownership"]["active.xml"] = reg.fingerprint(runtime.files.read("active.xml"))
    runtime.scheduler.items.clear()
    runtime.scheduler.calls.clear()
    bundle["ownership"][ids[0]]["identity"] = None
    plan = reg.build_plan(runtime, ids, operation="retire", reason="Synthetic retirement")
    result = reg.apply(plan, plan["input_revision"], runtime=runtime)
    assert result["status"] == "committed"
    assert runtime.scheduler.read("AcmeSync") == {"state": "absent"}
    assert runtime.scheduler.calls == []
    assert runtime.files.read("active.xml") == {"state": "absent"}
    assert ids[0] in json.loads(runtime.files.read("retired.json")["value"])
    assert json.loads(runtime.files.read("health.json")["value"])["tasks"] == []
    assert "AcmeSync" not in runtime.files.read("names.ps1")["value"].decode()


def test_private_descriptions_project_without_changing_executable_fields():
    from scripts.task_console.compiler import plan
    from scripts.task_console.contracts import SCHEDULER_FIELDS
    request = category_description_case()
    original = deepcopy(request)
    result = plan(request)
    projected = json.loads(result["generated_files"]["categories.json"]["content"])
    assert projected == request["baseline"]["categories"]
    assert result["parity"]["categories"]["status"] == "equal"
    assert result["applicable"] is False
    assert result["generated_files"]["categories.json"]["replacement_safe"] is False
    assert "description" not in SCHEDULER_FIELDS
    assert request == original
    for spec in result["task_specs"]:
        binding = request["bindings"]["tasks"][spec["task_id"]]
        assert spec["description"] == binding["description"]
        assert spec["argv"] == binding["argv"]


def test_registration_updates_selected_description_and_preserves_other_metadata(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path, installed=True)
    request = category_description_case()
    baseline = deepcopy(request["baseline"]["categories"])
    baseline["categories"][0]["presentation"] = {"color": "synthetic-blue"}
    baseline["presentation"] = {"order": "synthetic"}
    (tmp_path / "categories.json").write_text(json.dumps(baseline), encoding="utf-8")
    bundle["file_ownership"]["categories.json"] = reg.fingerprint(runtime.files.read("categories.json"))
    bundle["request"] = request
    selected = request["bindings"]["tasks"][ids[0]]
    unselected = request["bindings"]["tasks"][ids[1]]
    selected["description"] += " updated"
    unselected["description"] += " NOT selected"
    plan = reg.build_plan(runtime, [ids[0]], migrate=True)
    assert reg.apply(plan, plan["input_revision"], runtime=runtime)["status"] == "committed"
    result = json.loads(runtime.files.read("categories.json")["value"])
    expected = deepcopy(baseline)
    expected["categories"][0]["tasks"] = [unselected["name"], selected["name"]]
    expected["categories"][0]["taskDesc"][selected["name"]] = selected["description"]
    assert result == expected
    refresh(runtime, bundle)
    plan = reg.build_plan(runtime, [ids[0]], operation="retire", reason="Synthetic retirement")
    assert reg.apply(plan, plan["input_revision"], runtime=runtime)["status"] == "committed"
    retired = json.loads(runtime.files.read("categories.json")["value"])
    expected["categories"][0]["tasks"].remove(selected["name"])
    del expected["categories"][0]["taskDesc"][selected["name"]]
    assert retired == expected


@pytest.mark.parametrize("value", [None, {}, [], 5, "synthetic\0invalid"])
def test_private_description_rejects_nontext_and_nul(value):
    from scripts.task_console.compiler import plan
    request = category_description_case()
    next(iter(request["bindings"]["tasks"].values()))["description"] = value
    with pytest.raises(api().ContractError, match="invalid_description"):
        plan(request)


def test_description_machine_override_remains_data_and_preserves_namespace():
    from scripts.task_console.compiler import plan
    request = category_description_case()
    task_id = next(iter(request["bindings"]["tasks"]))
    description = request["bindings"]["tasks"][task_id]["description"] + "\nsecond line"
    request["machine"]["overrides"][task_id] = {"description": description}
    result = plan(request)
    spec = next(s for s in result["task_specs"] if s["task_id"] == task_id)
    assert spec["installation_namespace"] == task_id.split("/")[0]
    assert spec["description"] == description
    assert spec["argv"] == request["bindings"]["tasks"][task_id]["argv"]
    request["machine"]["overrides"][task_id]["command"] = description
    with pytest.raises(api().ContractError, match="unknown_field"):
        plan(request)


def test_description_is_not_accepted_in_public_task_manifest():
    from scripts.task_console.compiler import plan
    request = category_description_case()
    request["components"][0]["manifest"]["tasks"][0]["description"] = "Synthetic private text"
    with pytest.raises(api().ContractError, match="unknown_field"):
        plan(request)


def test_windows_adapter_reports_volatile_running_as_busy(tmp_path):
    from scripts.task_console.scheduler_windows import WindowsScheduler
    runtime, _, _ = rig(tmp_path)
    expected = runtime.scheduler.read("AcmeSync")
    current = deepcopy(expected)
    current["value"]["running"] = True
    calls = []

    def transport(operation, payload):
        calls.append(operation)
        assert operation == "query"
        return {"ok": True, "snapshot": current}

    with pytest.raises(api().Conflict, match="busy"):
        WindowsScheduler(transport).publish("AcmeSync", expected, expected)
    assert calls == ["query"]


def test_older_journal_without_scheduler_step_still_defers_files_when_running(tmp_path):
    runtime, bundle, ids = rig(tmp_path)
    migrate(runtime, bundle, ids)
    plan = api().build_plan(runtime, ids, operation="retire", reason="Synthetic retirement")

    def crash(point):
        if point == "published":
            raise Crash()

    runtime.checkpoint = crash
    with pytest.raises(Crash):
        api().apply(plan, plan["input_revision"], runtime=runtime)
    tx, journal = next(reversed(runtime.journal.items.items()))
    # New journals prepare Scheduler deletion before publishing the first file.
    # Model the old format only after proving those staged steps never ran.
    assert all(s['phase'] == 'staged' for s in journal['steps'] if s['kind'] == 'scheduler')
    journal['steps'] = [s for s in journal['steps'] if s['kind'] == 'files']
    journal.pop("scheduler_before")
    journal.pop("file_before")
    runtime.journal.save(tx, journal)
    support = {key: runtime.files.read(key) for key in bundle["paths"].values()}
    runtime.scheduler.items["AcmeSync"]["value"]["running"] = True
    runtime.checkpoint = lambda point: None
    result = api().recover(tx, runtime=runtime)
    assert result["status"] == "conflict"
    assert {key: runtime.files.read(key) for key in support} == support
    runtime.scheduler.items["AcmeSync"]["value"]["running"] = False
    assert api().recover(tx, runtime=runtime)["status"] == "rolled_back"
