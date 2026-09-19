"""Synthetic transaction tests; no operating-system Scheduler transport."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.make_fixtures import example_request, installation_request


def api():
    from scripts.task_console import registration
    return registration


class Crash(BaseException):
    pass


class Objects:
    def __init__(self, root=None):
        self.items, self.stages, self.calls = {}, {}, []
        self.root = root
        self.fail = None

    def read(self, key):
        value = deepcopy(self.items.get(key, {"state": "absent"}))
        if self.root and value["state"] == "present":
            value["value"] = (self.root / key).read_bytes()
        return value

    def prepare(self, key, value, token):
        result = {"state": "absent"} if value is None else {
            "state": "present", "identity": token, "value": deepcopy(value)}
        self.stages[token] = deepcopy(result)
        return result

    def publish(self, key, expected, desired):
        if self.fail == key:
            self.fail = None
            raise OSError("synthetic publication failure")
        if self.read(key) != expected:
            raise api().Conflict("resource_changed", "resource")
        self.calls.append(key)
        if self.root:
            path = self.root / key
            if desired["state"] == "absent":
                path.unlink(missing_ok=True)
            elif expected["state"] == "absent":
                with path.open("xb") as stream:
                    stream.write(desired["value"])
            else:
                path.write_bytes(desired["value"])
        self.items[key] = deepcopy(desired)

    def cleanup(self, token):
        self.stages.pop(token, None)

    def seed(self, key, value):
        snapshot = self.prepare(key, value, "seed:" + key)
        self.publish(key, {"state": "absent"}, snapshot)
        self.stages.clear()
        self.calls.clear()


class Vault:
    def __init__(self):
        self.items = {}

    def put(self, key, value):
        self.items[key] = deepcopy(value)
        return "secure:" + key

    def get(self, ref):
        return deepcopy(self.items[ref.removeprefix("secure:")])


class Journal:
    def __init__(self):
        self.items = {}

    def create(self, key, value):
        if key in self.items:
            raise api().Conflict("transaction_exists", "transaction_id")
        self.save(key, value)

    def save(self, key, value):
        json.dumps(value)  # ordinary journal must be JSON, never XML/bytes
        self.items[key] = deepcopy(value)

    def load(self, key):
        return deepcopy(self.items[key])

    def pending(self, keys):
        return [key for key, value in self.items.items() if set(value["locks"]) & set(keys)
                and (value["status"] not in ("committed", "rolled_back") or not value.get("cleaned"))]


class Locks:
    def __init__(self):
        self.active = set()

    @contextmanager
    def hold(self, keys):
        if self.active & set(keys):
            raise api().Conflict("busy", "locks")
        self.active.update(keys)
        try:
            yield
        finally:
            self.active.difference_update(keys)


def rig(tmp_path, *, installed=False):
    reg = api()
    request = installation_request() if installed else example_request()
    ids = list(request["bindings"]["tasks"])
    files, scheduler = Objects(tmp_path), Objects()
    paths = {"bindings": "bindings.json", "machine": "machine.json",
             "task-health.json": "health.json", "TaskNames.ps1": "names.ps1",
             "categories.json": "categories.json", "tombstones": "retired.json"}
    baseline = request["baseline"]
    for role, value in (("bindings", request["bindings"]), ("machine", request["machine"]),
                        ("task-health.json", baseline["task_health"]),
                        ("TaskNames.ps1", baseline["task_names"]),
                        ("categories.json", baseline["categories"]), ("tombstones", {})):
        files.seed(paths[role], value.encode() if isinstance(value, str) else json.dumps(value).encode())
    ownership = {}
    for task_id, binding in request["bindings"]["tasks"].items():
        key = binding["name"]
        scheduler.seed(key, {"xml": "<Task>synthetic credential XML</Task>", "enabled": False,
                             "running": False, "spec": deepcopy(binding)})
        ownership[task_id] = {"name": key, "task_path": "\\", "epoch": 0,
                              "writer": "legacy", "identity": "seed:" + key}
    bundle = {"request": request, "ownership": ownership, "paths": paths,
              "file_ownership": {key: reg.fingerprint(files.read(key)) for key in paths.values()},
              "active_xml": {key: None for key in ids},
              "linked_work_items": {key: ["synthetic-work-1"] for key in ids}}

    def render(spec, old, enabled):
        return {"xml": "<Task>synthetic prepared XML</Task>", "enabled": enabled,
                "running": False, "spec": deepcopy(spec)}

    runtime = reg.Runtime(lambda: deepcopy(bundle), files, scheduler, Journal(), Vault(), Locks(), render)
    return runtime, bundle, ids


def test_rejects_t11_projection(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    from scripts.task_console.compiler import plan
    with pytest.raises(reg.Conflict, match="mutation_plan_required"):
        reg.apply(plan(bundle["request"]), "ignored", runtime=runtime)


def test_apply_migrates_without_enabling_and_preserves_checks(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path, installed=True)
    plan = reg.build_plan(runtime, ids, migrate=True)
    result = reg.apply(plan, plan["input_revision"], runtime=runtime)
    assert result["status"] == "committed"
    machine = json.loads(runtime.files.read("machine.json")["value"])
    assert machine["migrated_tasks"] == ids
    assert machine["authority_epoch"] == 1
    health = json.loads(runtime.files.read("health.json")["value"])["tasks"]
    assert sum(row.get("task_id") in ids for row in health) == 4
    assert all(not runtime.scheduler.read(bundle["ownership"][key]["name"])["value"]["enabled"] for key in ids)
    assert "<Task>" not in json.dumps(runtime.journal.items)


def test_generated_failure_compensates_successful_registration(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    original = deepcopy(runtime.scheduler.items)
    plan = reg.build_plan(runtime, ids, migrate=True)
    runtime.files.fail = "health.json"
    result = reg.apply(plan, plan["input_revision"], runtime=runtime)
    assert result["status"] == "rolled_back"
    assert runtime.scheduler.read("AcmeSync")["value"] == original["AcmeSync"]["value"]
    assert not runtime.files.stages


def test_namespace_revision_and_fresh_baselines_are_checked(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path, installed=True)
    plan = reg.build_plan(runtime, ids, migrate=True)
    bundle["request"]["bindings"]["installations"]["acme-user"]["identity"]["metadata"]["selected_version"] = "2.0"
    with pytest.raises(reg.Conflict, match="input_changed"):
        reg.apply(plan, plan["input_revision"], runtime=runtime)
    assert not runtime.scheduler.calls


def refresh(runtime, bundle):
    """Simulate the controller loading the published generation and its receipts."""
    bundle["request"]["machine"] = json.loads(runtime.files.read("machine.json")["value"])
    bundle["request"]["bindings"] = json.loads(runtime.files.read("bindings.json")["value"])
    for path in bundle["file_ownership"]:
        bundle["file_ownership"][path] = api().fingerprint(runtime.files.read(path))
    for task_id, proof in bundle["ownership"].items():
        proof["epoch"] = bundle["request"]["machine"]["authority_epoch"]
        proof["writer"] = "declarations" if task_id in bundle["request"]["machine"]["migrated_tasks"] else "legacy"
        proof["identity"] = runtime.scheduler.read(proof["name"]).get("identity")


def migrate(runtime, bundle, ids):
    reg = api()
    plan = reg.build_plan(runtime, ids, migrate=True)
    assert reg.apply(plan, plan["input_revision"], runtime=runtime)["status"] == "committed"
    refresh(runtime, bundle)


def test_retire_archives_xml_lists_work_and_is_idempotent(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    migrate(runtime, bundle, ids)
    runtime.files.seed("active.xml", b"<Task>synthetic secret</Task>")
    bundle["active_xml"][ids[0]] = "active.xml"
    bundle["file_ownership"]["active.xml"] = reg.fingerprint(runtime.files.read("active.xml"))
    plan = reg.build_plan(runtime, ids, operation="retire", reason="synthetic retirement")
    result = reg.retire(ids[0], plan["input_revision"], reason="synthetic retirement", runtime=runtime)
    assert result["status"] == "committed"
    assert result["linked_work_items"][ids[0]] == ["synthetic-work-1"]
    assert not (tmp_path / "active.xml").exists()
    assert json.loads((tmp_path / "health.json").read_text())["tasks"] == []
    assert "<Task>" not in json.dumps(runtime.journal.items)
    tombstone = json.loads((tmp_path / "retired.json").read_text())[ids[0]]
    assert tombstone["xml_before_image"].startswith("journal:")
    refresh(runtime, bundle)
    runtime.scheduler.calls.clear()
    runtime.files.calls.clear()
    plan = reg.build_plan(runtime, ids, operation="retire", reason="synthetic retirement")
    assert reg.retire(ids[0], plan["input_revision"], reason="synthetic retirement", runtime=runtime)["status"] == "committed"
    assert runtime.scheduler.calls == runtime.files.calls == []


def test_enable_updates_binding_before_final_scheduler_enable(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    migrate(runtime, bundle, ids)
    seen = []
    original = runtime.scheduler.publish
    def publish(key, expected, desired):
        if desired.get("value", {}).get("enabled"):
            seen.append(json.loads((tmp_path / "bindings.json").read_text())["tasks"][ids[0]]["enabled"])
        original(key, expected, desired)
    runtime.scheduler.publish = publish
    assert reg.control("AcmeSync", "enable", runtime=runtime)["status"] == "committed"
    assert seen == [True]
    assert runtime.scheduler.read("AcmeSync")["value"]["enabled"] is True


def test_file_concurrent_edit_is_preserved_on_compensation(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    original = runtime.files.publish
    def publish(key, expected, desired):
        if key == "health.json":
            (tmp_path / "machine.json").write_bytes(b"concurrent user edit")
            raise OSError("synthetic failure")
        original(key, expected, desired)
    runtime.files.publish = publish
    plan = reg.build_plan(runtime, ids, migrate=True)
    result = reg.apply(plan, plan["input_revision"], runtime=runtime)
    assert result["status"] == "conflict"
    assert (tmp_path / "machine.json").read_bytes() == b"concurrent user edit"
    assert result["conflicts"][0]["code"] == "concurrent_edit"


@pytest.mark.parametrize("state", ["busy", "stale", "malformed", "budget"])
def test_refusals_are_specific(tmp_path, state):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    if state == "busy":
        runtime.scheduler.items["AcmeSync"]["value"]["running"] = True
        expected = "busy"
    elif state == "stale":
        (tmp_path / "health.json").write_bytes(b"{}")
        expected = "file_ownership_changed"
    elif state == "malformed":
        bundle["request"]["machine"]["migrated_tasks"] = ["unknown/task/id"]
        expected = "unknown_migrated_task"
    else:
        bundle["request"]["bindings"]["tasks"][ids[0]]["concurrency"]["gate_budget"] = {
            "held_by_payload": True, "wait_seconds": 400, "execution_seconds": 400, "cleanup_seconds": 10}
        expected = "gate_exceeds_timeout"
    with pytest.raises(reg.ContractError, match=expected):
        reg.build_plan(runtime, ids, migrate=True)


def test_new_task_no_replace_and_success_with_lost_reply(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    runtime.scheduler.items.clear()
    bundle["ownership"][ids[0]]["identity"] = None
    plan = reg.build_plan(runtime, ids, migrate=True)
    original = runtime.scheduler.publish
    calls = []
    def publish(key, expected, desired):
        original(key, expected, desired)
        if not calls:
            calls.append(key)
            raise OSError("reply lost after successful registration")
    runtime.scheduler.publish = publish
    result = reg.apply(plan, plan["input_revision"], runtime=runtime)
    assert result["status"] == "rolled_back"
    assert runtime.scheduler.read("AcmeSync") == {"state": "absent"}


def test_scheduler_transport_pins_path_and_rejects_unknown_or_wildcards():
    from scripts.task_console.scheduler_windows import WindowsScheduler
    reg = api()
    seen = []
    def transport(op, payload):
        seen.append((op, payload))
        return {"ok": True}
    scheduler = WindowsScheduler(transport)
    with pytest.raises(reg.Conflict, match="query_unknown"):
        scheduler.read("AcmeSync")
    assert seen[0][1]["TaskPath"] == "\\"
    for bad in ["*", "[Acme]", "Acme/Child", "Acme\\Child"]:
        with pytest.raises(reg.Conflict, match="invalid_task_name"):
            scheduler.read(bad)
    assert len(seen) == 1


def test_cli_requires_runtime_and_accepts_apply_plan(tmp_path, capsys):
    from scripts.task_console.__main__ import main
    reg = api()
    assert main(["recover", "--transaction-id", "synthetic"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "runtime_not_configured"
    runtime, bundle, ids = rig(tmp_path)
    plan = reg.build_plan(runtime, ids, migrate=True)
    target = tmp_path / "plan.json"
    target.write_text(json.dumps(plan))
    assert main(["apply", "--request", str(target), "--expected-revision", plan["input_revision"]], runtime=runtime) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "committed"


@pytest.mark.parametrize("boundary", ["staged", "intent", "published"])
@pytest.mark.parametrize("position", range(1, 7))
def test_each_publication_boundary_recovers(tmp_path, boundary, position):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    plan = reg.build_plan(runtime, ids, migrate=True)
    hits = []
    def fault(point):
        if point == boundary:
            hits.append(point)
            if len(hits) == position:
                raise Crash()
    runtime.checkpoint = fault
    try:
        reg.apply(plan, plan["input_revision"], runtime=runtime)
    except Crash:
        pass
    assert len(hits) >= position, "fault must actually reach its boundary"
    runtime.checkpoint = lambda point: None
    tx = next(iter(runtime.journal.items))
    assert reg.recover(tx, runtime=runtime)["status"] == "rolled_back"
    assert reg.recover(tx, runtime=runtime)["status"] == "rolled_back"


@pytest.mark.parametrize("boundary", ["undo_intent", "undone"])
def test_crash_during_undo_after_enable_recovers(tmp_path, boundary):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    bundle["request"]["bindings"]["tasks"][ids[0]]["enabled"] = True
    plan = reg.build_plan(runtime, ids, migrate=True, approve_enable=True)
    original = runtime.scheduler.publish
    def publish(key, expected, desired):
        original(key, expected, desired)
        if desired.get("value", {}).get("enabled"):
            raise OSError("lost enable reply")
    runtime.scheduler.publish = publish
    hits = []
    def fault(point):
        if point == boundary and not hits:
            hits.append(point)
            raise Crash()
    runtime.checkpoint = fault
    with pytest.raises(Crash):
        reg.apply(plan, plan["input_revision"], runtime=runtime)
    runtime.checkpoint = lambda point: None
    runtime.scheduler.publish = original
    tx = next(iter(runtime.journal.items))
    assert reg.recover(tx, runtime=runtime)["status"] == "rolled_back"


def test_pending_transaction_blocks_new_writer(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    plan = reg.build_plan(runtime, ids, migrate=True)
    def fault(point):
        if point == "journal_created":
            raise Crash()
    runtime.checkpoint = fault
    with pytest.raises(Crash):
        reg.apply(plan, plan["input_revision"], runtime=runtime)
    runtime.checkpoint = lambda point: None
    with pytest.raises(reg.Conflict, match="recovery_required"):
        reg.apply(plan, plan["input_revision"], runtime=runtime)


def test_malformed_allowlist_does_not_produce_applicable_plan(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    (tmp_path / "names.ps1").write_bytes(b"$TaskNames = @(Get-Content 'synthetic')")
    bundle["file_ownership"]["names.ps1"] = reg.fingerprint(runtime.files.read("names.ps1"))
    with pytest.raises(reg.Conflict, match="invalid_allowlist"):
        reg.build_plan(runtime, ids, migrate=True)


def test_selecting_one_installation_preserves_other_bindings(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path, installed=True)
    original = json.loads((tmp_path / "bindings.json").read_text())["tasks"][ids[1]]
    bundle["request"]["bindings"]["tasks"][ids[1]]["timeout_seconds"] = 999
    plan = reg.build_plan(runtime, [ids[0]], migrate=True)
    assert reg.apply(plan, plan["input_revision"], runtime=runtime)["status"] == "committed"
    assert json.loads((tmp_path / "bindings.json").read_text())["tasks"][ids[1]] == original


def test_declaration_edit_during_transaction_is_conflict(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    plan = reg.build_plan(runtime, ids, migrate=True)
    def edit(point):
        if point == "published":
            bundle["request"]["components"][0]["tasks"][0]["timeout_seconds"] = 777
    runtime.checkpoint = edit
    result = reg.apply(plan, plan["input_revision"], runtime=runtime)
    assert result["status"] == "conflict"
    assert result["conflicts"] == [{"code": "input_changed", "kind": "authority"}]
    assert not runtime.scheduler.read("AcmeSync")["value"]["enabled"]


def test_concurrent_creator_is_not_removed_by_rollback(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    runtime.scheduler.items.clear()
    bundle["ownership"][ids[0]]["identity"] = None
    plan = reg.build_plan(runtime, ids, migrate=True)
    original = runtime.scheduler.publish
    external = {"state": "present", "identity": "external-owner", "value": {"enabled": False, "running": False}}
    def race(key, expected, desired):
        runtime.scheduler.items[key] = deepcopy(external)
        original(key, expected, desired)
    runtime.scheduler.publish = race
    result = reg.apply(plan, plan["input_revision"], runtime=runtime)
    assert result["status"] == "conflict"
    assert runtime.scheduler.read("AcmeSync") == external


def test_unknown_recovery_query_does_not_become_absence(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    plan = reg.build_plan(runtime, ids, migrate=True)
    def fault(point):
        if point == "published":
            raise Crash()
    runtime.checkpoint = fault
    with pytest.raises(Crash):
        reg.apply(plan, plan["input_revision"], runtime=runtime)
    runtime.checkpoint = lambda point: None
    runtime.scheduler.read = lambda key: None
    tx = next(iter(runtime.journal.items))
    result = reg.recover(tx, runtime=runtime)
    assert result["status"] == "conflict"
    assert result["conflicts"][0]["code"] == "query_unknown"


def test_crash_inside_prepare_discovers_stage_cleanup(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    original = runtime.files.prepare
    def prepare(key, value, token):
        original(key, value, token)
        raise Crash()
    runtime.files.prepare = prepare
    plan = reg.build_plan(runtime, ids, migrate=True)
    with pytest.raises(Crash):
        reg.apply(plan, plan["input_revision"], runtime=runtime)
    assert runtime.files.stages
    runtime.files.prepare = original
    tx = next(iter(runtime.journal.items))
    assert reg.recover(tx, runtime=runtime)["status"] == "rolled_back"
    assert not runtime.files.stages


def test_dispatch_keeps_legacy_writer_under_authority_lock(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    def legacy():
        assert "authority" in runtime.locks.active
        return {"legacy": True}
    assert reg.dispatch("AcmeSync", "stop", legacy=legacy, runtime=runtime) == {"legacy": True}
    bundle["request"]["machine"]["migrated_tasks"] = "malformed"
    called = []
    with pytest.raises(reg.ContractError, match="invalid_type"):
        reg.dispatch("AcmeSync", "disable", legacy=lambda: called.append(1), runtime=runtime)
    assert called == []


def test_legacy_retire_injected_controller_never_calls_old_plan(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "task_console"))
    import retire
    runtime, bundle, ids = rig(tmp_path)
    migrate(runtime, bundle, ids)
    monkeypatch.setattr(retire, "plan", lambda *args: pytest.fail("legacy planner called for migrated task"))
    def controller(name, verb, **kwargs):
        return api().dispatch(name, verb, runtime=runtime, **kwargs)
    assert retire.apply("AcmeSync", "synthetic retirement", controller=controller)["status"] == "committed"


def test_disabled_task_reenabled_concurrently_cannot_retire_successfully(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    migrate(runtime, bundle, ids)
    original = runtime.files.publish
    def publish(key, expected, desired):
        original(key, expected, desired)
        runtime.scheduler.items["AcmeSync"]["value"]["enabled"] = True
    runtime.files.publish = publish
    plan = reg.build_plan(runtime, ids, operation="retire", reason="synthetic retirement")
    result = reg.apply(plan, plan["input_revision"], runtime=runtime)
    assert result["status"] != "committed"
    assert result["failure"]["code"] == "scheduler_changed"
    assert runtime.journal.pending(["authority"])
    # An external enable prevents supporting-resource restoration. Once the
    # external writer disables the task, explicit recovery can finish safely.
    assert json.loads((tmp_path / "health.json").read_text())["tasks"] == []
    runtime.files.publish = original
    runtime.scheduler.items["AcmeSync"]["value"]["enabled"] = False
    assert reg.recover(result["transaction_id"], runtime=runtime)["status"] == "rolled_back"
    assert "AcmeSync" in (tmp_path / "health.json").read_text()


def test_cleanup_failure_is_visible_and_recovery_retries(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    original = runtime.files.cleanup
    runtime.files.cleanup = lambda token: (_ for _ in ()).throw(OSError("synthetic cleanup failure"))
    plan = reg.build_plan(runtime, ids, migrate=True)
    result = reg.apply(plan, plan["input_revision"], runtime=runtime)
    assert result["status"] == "committed"
    assert result["ok"] is False
    assert result["cleaned"] is False
    assert result["cleanup_error"] == "cleanup_required"
    runtime.files.cleanup = original
    recovered = reg.recover(result["transaction_id"], runtime=runtime)
    assert recovered["ok"] is True
    assert recovered["cleaned"] is True
    assert recovered["cleanup_error"] is None


def test_orchestration_uses_windows_adapter_with_structured_fake_transport(tmp_path):
    from scripts.task_console.scheduler_windows import WindowsScheduler
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    backend = runtime.scheduler
    seen = []
    def transport(operation, payload):
        assert payload["TaskPath"] == "\\"
        seen.append(operation)
        if operation == "query":
            return {"ok": True, "snapshot": backend.read(payload["TaskName"])}
        if operation == "prepare":
            assert isinstance(payload["value"]["spec"]["argv"], list)
            return {"ok": True, "snapshot": backend.prepare(payload["TaskName"], payload["value"], payload["token"])}
        if operation == "publish":
            backend.publish(payload["TaskName"], payload["expected"], payload["desired"])
        elif operation == "cleanup":
            backend.cleanup(payload["token"])
        else:
            pytest.fail("unexpected Scheduler operation")
        return {"ok": True}
    runtime.scheduler = WindowsScheduler(transport)
    plan = reg.build_plan(runtime, ids, migrate=True)
    assert reg.apply(plan, plan["input_revision"], runtime=runtime)["status"] == "committed"
    assert set(seen) == {"query", "prepare", "publish", "cleanup"}


def test_comment_only_name_is_not_membership(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    value = b"$TaskNames = @('AcmeOther') # AcmeSync\n"
    runtime.files.items["names.ps1"]["value"] = value
    (tmp_path / "names.ps1").write_bytes(value)
    bundle["file_ownership"]["names.ps1"] = reg.fingerprint(runtime.files.read("names.ps1"))
    plan = reg.build_plan(runtime, ids, migrate=True)
    assert reg.apply(plan, plan["input_revision"], runtime=runtime)["status"] == "committed"
    from scripts.task_console.allowlist import literal_names
    text = (tmp_path / "names.ps1").read_text()
    assert literal_names(text) == ["AcmeOther", "AcmeSync"]
    assert "# AcmeSync" in text


@pytest.mark.parametrize("boundary", ["journal_created", "staged", "intent", "published", "committed", "cleanup"])
def test_crash_recovery_is_repeatable(tmp_path, boundary):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    plan = reg.build_plan(runtime, ids, migrate=True)
    hit = []
    def fault(point):
        if point == boundary and not hit:
            hit.append(point)
            raise Crash()
    runtime.checkpoint = fault
    with pytest.raises(Crash):
        reg.apply(plan, plan["input_revision"], runtime=runtime)
    runtime.checkpoint = lambda point: None
    tx = next(iter(runtime.journal.items))
    first = reg.recover(tx, runtime=runtime)
    second = reg.recover(tx, runtime=runtime)
    assert first["status"] == second["status"]
    assert first["status"] == ("committed" if boundary in ("committed", "cleanup") else "rolled_back")
    assert not runtime.files.stages


def test_unknown_query_and_missing_vault_fail_closed(tmp_path):
    reg = api()
    runtime, bundle, ids = rig(tmp_path)
    runtime.scheduler.items["AcmeSync"] = {"state": "unknown"}
    with pytest.raises(reg.Conflict, match="query_unknown"):
        reg.build_plan(runtime, ids, migrate=True)
    second_root = tmp_path / "second"
    second_root.mkdir()
    runtime, bundle, ids = rig(second_root)
    plan = reg.build_plan(runtime, ids, migrate=True)
    runtime.vault = None
    with pytest.raises(reg.Conflict, match="secure_storage_required"):
        reg.apply(plan, plan["input_revision"], runtime=runtime)
    assert not runtime.scheduler.calls
