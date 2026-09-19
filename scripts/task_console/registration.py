"""Explicit, journaled compensation for task registration.

No default transport or storage path exists. The controller supplies durable
adapters and a private declaration bundle; importing this module performs no I/O.
The Scheduler and filesystem do not share an atomic commit. See docs/task-registration.md
for adapter durability, lock and staging requirements before production wiring.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Callable
import uuid

from . import allowlist
from .compiler import plan as compile_plan
from .contracts import ContractError, require, versioned


class Conflict(ContractError):
    """Safe refusal; never include XML, credentials, or transport error text."""


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def revision(value):
    return "sha256:" + hashlib.sha256(_json(value).encode()).hexdigest()


def _snapshot(value):
    if not isinstance(value, dict) or value.get("state") not in ("present", "absent"):
        raise Conflict("query_unknown", "resource")
    if value["state"] == "absent":
        if set(value) != {"state"}:
            raise Conflict("invalid_observation", "resource")
    elif not isinstance(value.get("identity"), str) or not value["identity"] or "value" not in value:
        raise Conflict("incomplete_observation", "resource")
    return value


def fingerprint(snapshot):
    snapshot = deepcopy(_snapshot(snapshot))
    if isinstance(snapshot.get("value"), bytes):
        snapshot["value"] = {"bytes_sha256": hashlib.sha256(snapshot["value"]).hexdigest()}
    return revision(snapshot)


@dataclass
class Runtime:
    load: Callable
    files: object
    scheduler: object
    journal: object
    vault: object
    locks: object
    render: Callable
    checkpoint: Callable = lambda point: None


_runtime = None


def configure(runtime):
    """Explicit embedding hook. There is intentionally no auto-discovered adapter."""
    global _runtime
    _runtime = runtime


def _use(runtime):
    runtime = runtime or _runtime
    if runtime is None:
        raise Conflict("runtime_adapter_required", "runtime")
    return runtime


def _authority(bundle):
    request = require(bundle, "request", dict)
    machine = versioned(require(request, "machine", dict), "machine")
    epoch = require(machine, "authority_epoch", int)
    migrated = require(machine, "migrated_tasks", list)
    if epoch < 0 or any(not isinstance(x, str) or not x for x in migrated) or len(set(migrated)) != len(migrated):
        raise Conflict("invalid_authority", "machine")
    if not set(migrated) <= set(require(require(request, "bindings", dict), "tasks", dict)):
        raise Conflict("unknown_migrated_task", "migrated_tasks")
    return epoch, migrated


def _compile(bundle):
    # T11 remains read-only and rejects cutover input. Authority is validated by
    # T12, then its pure compiler is reused on a copy solely for TaskSpec/projection
    # construction. Its applicable/replacement_safe flags never authorize writes.
    _authority(bundle)
    request = deepcopy(bundle["request"])
    request["machine"]["migrated_tasks"] = []
    return compile_plan(request)


def compile_effective(bundle):
    """Read TaskSpecs with the supplied request's validated authority and revision.

    The runtime owner supplies the effective request; this API performs no I/O or
    receipt discovery. Authority describes each task's writer, never permission
    to apply the returned projections. The private compatibility plan is unchanged.
    """
    compiled = _compile(bundle)
    epoch, migrated = _authority(bundle)
    migrated_ids = set(migrated)
    task_modes = {spec["task_id"]: "declarations" if spec["task_id"] in migrated_ids else "legacy"
                  for spec in compiled["task_specs"]}
    mode = "legacy" if not migrated else "declarations" if len(migrated) == len(task_modes) else "mixed"
    compiled["authority"] = {"mode": mode, "epoch": epoch, "migrated_tasks": list(migrated),
                             "task_modes": task_modes}
    compiled["input_revision"] = revision(bundle["request"])
    return compiled


def _read(adapter, key):
    try:
        return deepcopy(_snapshot(adapter.read(key)))
    except ContractError:
        raise
    except Exception as exc:
        raise Conflict("query_failed", "resource") from exc


def _scheduler_config(snapshot):
    """Stable configuration excludes the independently observed execution state."""
    snapshot = deepcopy(_snapshot(snapshot))
    if snapshot["state"] == "present":
        value = snapshot["value"]
        if not isinstance(value, dict) or any(type(value.get(k)) is not bool for k in ("enabled", "running")):
            raise Conflict("incomplete_observation", "scheduler")
        value.pop("running")
    return snapshot


def _same(kind, current, expected):
    if kind == "scheduler":
        from .runtime_xml import same_candidate
        return (_scheduler_config(current) == _scheduler_config(expected)
                or same_candidate(current, expected))
    return current == expected


def _idle(snapshot):
    _scheduler_config(snapshot)
    if snapshot.get("value", {}).get("running"):
        raise Conflict("busy", "scheduler")


def _verify_files(runtime, expected):
    for path, snapshot in expected.items():
        if _read(runtime.files, path) != snapshot:
            raise Conflict("final_readback_mismatch", "files")


def _safely_disabled(runtime, journal):
    names = set(journal.get("scheduler_before", {}))
    names.update(s["key"] for s in journal["steps"] if s["kind"] == "scheduler")
    if "scheduler_before" not in journal:
        # Earlier v1 journals may contain no Scheduler step for an already
        # disabled task. Their locked IDs still identify the recovery scope.
        ids = {key.removeprefix("task:") for key in journal["locks"] if key.startswith("task:")}
        selected = [s for s in _compile(runtime.load())["task_specs"] if s["task_id"] in ids]
        if not ids or {s["task_id"] for s in selected} != ids:
            raise Conflict("scheduler_scope_unknown", "journal")
        names.update(s["name"] for s in selected)
    for name in sorted(names):
        current = _read(runtime.scheduler, name)
        _idle(current)
        if current.get("value", {}).get("enabled"):
            raise Conflict("supporting_resources_deferred", "scheduler")


def _restored_files(runtime, journal):
    expected = {key: runtime.vault.get(ref) for key, ref in journal.get("file_before", {}).items()}
    for step in journal["steps"]:
        if step["kind"] == "files":
            ref = step.get("undo", step["before"]) if step["phase"] == "undone" else step["before"]
            expected[step["key"]] = runtime.vault.get(ref)
    if "file_before" not in journal:
        # Earlier journals only recorded changed files. The pinned input still
        # carries approved fingerprints for relevant unchanged resources.
        bundle = runtime.load()
        for path in bundle["paths"].values():
            if path not in expected:
                current = _read(runtime.files, path)
                if fingerprint(current) != bundle["file_ownership"].get(path):
                    raise Conflict("file_ownership_changed", "files")
                expected[path] = current
    return expected


def _decode(snapshot):
    if snapshot["state"] != "present" or not isinstance(snapshot["value"], bytes):
        raise Conflict("file_baseline_required", "files")
    try:
        return snapshot["value"].decode("utf-8-sig")
    except UnicodeError as exc:
        raise Conflict("invalid_encoding", "files") from exc


def _document(snapshot):
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise Conflict("duplicate_json_key", "files")
                result[key] = value
            return result
        return json.loads(_decode(snapshot), object_pairs_hook=unique)
    except (ValueError, TypeError) as exc:
        raise Conflict("invalid_json", "files") from exc


def _encode(document):
    return (json.dumps(document, ensure_ascii=True, indent=2, allow_nan=False) + "\n").encode("ascii")


def _budget(spec):
    budget = spec["concurrency"].get("gate_budget")
    if budget is None:
        return
    if not isinstance(budget, dict) or budget.get("held_by_payload") is not True:
        raise Conflict("invalid_gate_budget", "concurrency")
    values = [budget.get(k) for k in ("wait_seconds", "execution_seconds", "cleanup_seconds")]
    if any(type(v) is not int or v <= 0 for v in values):
        raise Conflict("invalid_gate_budget", "concurrency")
    if spec["timeout_seconds"] and sum(values) > spec["timeout_seconds"]:
        raise Conflict("gate_exceeds_timeout", "concurrency")


def _assemble(runtime, intent):
    bundle = deepcopy(runtime.load())
    epoch, migrated = _authority(bundle)
    compiled = _compile(bundle)
    specs = {s["task_id"]: s for s in compiled["task_specs"]}
    ids = intent["task_ids"]
    if not ids or len(ids) != len(set(ids)) or not set(ids) <= specs.keys():
        raise Conflict("invalid_task_selection", "task_ids")
    if intent["operation"] not in ("apply", "retire", "enable", "disable", "restore"):
        raise Conflict("invalid_operation", "operation")
    if 'legacy_transaction' in intent and (intent['legacy_transaction'] is not True
            or intent['operation'] not in ('enable', 'disable', 'retire') or intent['migrate']):
        raise Conflict('invalid_legacy_transaction', 'intent')
    if intent['operation'] == 'restore' and (intent['migrate'] or intent.get('legacy_transaction')
            or not isinstance(intent.get('restore_tasks'), dict) or set(intent['restore_tasks']) != set(ids)
            or not isinstance(intent.get('rebinding'), dict) or not set(intent['rebinding']) <= set(ids)):
        raise Conflict('invalid_restore_intent', 'intent')
    if intent["operation"] == "retire" and not intent["reason"].strip():
        raise Conflict("reason_required", "reason")
    paths = require(bundle, "paths", dict)
    roles = {"bindings", "machine", "task-health.json", "TaskNames.ps1", "categories.json", "tombstones"}
    if set(paths) != roles or any(not isinstance(p, str) or not p for p in paths.values()):
        raise Conflict("invalid_resource_paths", "paths")
    if len({p.casefold() for p in paths.values()}) != len(paths):
        raise Conflict("aliased_resources", "paths")
    extra_paths = []
    for task_id in ids:
        launcher = bundle.get('launchers', {}).get(task_id)
        if launcher is not None:
            if not isinstance(launcher, str) or launcher.casefold() in {p.casefold() for p in paths.values()}:
                raise Conflict('invalid_launcher_path', 'launcher')
            extra_paths.append(launcher)
    if intent["operation"] == "retire":
        exports = require(bundle, "active_xml", dict)
        for task_id in ids:
            if task_id not in exports:
                raise Conflict("active_xml_inventory_required", "active_xml")
            path = exports[task_id]
            if path is not None:
                if not isinstance(path, str) or not path or path.casefold() in {p.casefold() for p in paths.values()}:
                    raise Conflict("invalid_active_xml_path", "active_xml")
                extra_paths.append(path)
    file_before = {p: _read(runtime.files, p) for p in list(paths.values()) + extra_paths}
    for path, snapshot in file_before.items():
        if require(bundle, "file_ownership", dict).get(path) != fingerprint(snapshot):
            raise Conflict("file_ownership_changed", "files")
    old_tombstones = _document(file_before[paths["tombstones"]])
    if not isinstance(old_tombstones, dict):
        raise Conflict("invalid_tombstones", "tombstones")
    before, selected = {}, []
    for task_id in ids:
        spec = deepcopy(specs[task_id])
        from .scheduler_windows import task_name
        task_name(spec["name"])
        proof = require(bundle, "ownership", dict).get(task_id)
        writer = "declarations" if task_id in migrated else "legacy"
        if not isinstance(proof, dict) or any(proof.get(k) != v for k, v in {
            "name": spec["name"], "task_path": "\\", "epoch": epoch, "writer": writer}.items()):
            raise Conflict("ownership_required", "ownership")
        if writer == "legacy" and not intent["migrate"] and not intent.get('legacy_transaction') and intent['operation'] != 'restore':
            raise Conflict("legacy_authority", "task_id")
        snapshot = _read(runtime.scheduler, spec["name"])
        if proof.get("identity") != snapshot.get("identity") or "identity" not in proof:
            raise Conflict("scheduler_ownership_changed", "ownership")
        if snapshot["state"] == "present":
            current = snapshot["value"]
            if not isinstance(current, dict) or type(current.get("running")) is not bool or type(current.get("enabled")) is not bool:
                raise Conflict("incomplete_observation", "scheduler")
            if current["running"]:
                raise Conflict("busy", "scheduler")
            if writer != 'legacy' and intent['operation'] in ('enable', 'disable') and hasattr(runtime, 'validate_control'):
                runtime.validate_control(spec, snapshot)
            if spec["enabled"] and not current["enabled"] and not intent["approve_enable"] and intent["operation"] not in ("disable", "retire", "restore"):
                raise Conflict("explicit_enable_required", "enabled")
        if task_id in old_tombstones and intent["operation"] != "retire":
            raise Conflict("retired_task", "task_id")
        if intent["operation"] in ("retire", "disable", "enable"):
            spec["enabled"] = intent["operation"] == "enable"
        if intent.get('legacy_transaction') and snapshot['state'] == 'absent' and intent['operation'] != 'retire':
            raise Conflict('task_missing', 'scheduler')
        if intent['operation'] == 'restore':
            from .export_restore import validate_restore_target
            spec['enabled'] = validate_restore_target(bundle, intent, spec, snapshot, scheduler=runtime.scheduler)
        _budget(spec)
        before[spec["name"]] = snapshot
        selected.append(spec)
    baselines = {"files": {p: fingerprint(s) for p, s in file_before.items()},
                 "scheduler": {p: fingerprint(_scheduler_config(s)) for p, s in before.items()}}
    public = {"schemaVersion": 1, "mode": "mutation", "applicable": True,
              "intent": deepcopy(intent), "input_revision": revision(bundle),
              "baseline_revision": revision(baselines), "baselines": baselines,
              "authority_epoch": epoch, "task_ids": ids,
              "linked_work_items": {i: bundle.get("linked_work_items", {}).get(i, []) for i in ids}}
    if intent['operation'] == 'retire':
        from .linked_items import report
        linked = report(runtime, ids, bundle)
        public.update(linked_work_items=linked['items'], linked_work_items_status=linked['status'],
                      linked_work_items_code=linked.get('code'))
    outputs = _outputs(bundle, compiled, selected, file_before, intent, "preview", linked=public['linked_work_items'])
    public["changes"] = {"files": [{"path": p, "operation": "remove" if data is None else "publish",
                                    "content_digest": None if data is None else hashlib.sha256(data).hexdigest()}
                                   for p, data in outputs.items() if file_before[p].get("value") != data],
                         "tasks": [{"task_id": s["task_id"], "name": s["name"], "enabled": s["enabled"],
                                    "check_ids": [c["id"] for c in s["checks"]],
                                    "scheduler_semantics": (
                                        {"xml_version": "1.4", "use_unified_scheduling_engine": True}
                                        if before[s["name"]]["state"] == "absent" and not s.get("xml_passthrough")
                                        and intent["operation"] not in ("retire", "restore")
                                        else {"preserve_existing_xml": True})} for s in selected]}
    public["plan_revision"] = revision(public)
    return public, bundle, compiled, selected, before, file_before


def build_plan(runtime, task_ids, *, operation="apply", migrate=False, approve_enable=False, reason="", legacy_transaction=False):
    """Read-only explicit plan. The result contains fingerprints, never raw XML."""
    intent = {"task_ids": list(task_ids), "operation": operation, "migrate": migrate,
              "approve_enable": approve_enable, "reason": reason}
    if legacy_transaction:
        if operation not in ('enable', 'disable', 'retire') or migrate:
            raise Conflict('invalid_legacy_transaction', 'intent')
        intent['legacy_transaction'] = True
    if type(migrate) is not bool or type(approve_enable) is not bool or not isinstance(reason, str):
        raise Conflict("invalid_intent", "intent")
    return _assemble(_use(runtime), intent)[0]


def _outputs(bundle, compiled, selected, before, intent, tx, *, linked=None):
    paths = bundle["paths"]
    ids = {s["task_id"] for s in selected}
    retiring = intent["operation"] == "retire"
    bindings = versioned(_document(before[paths["bindings"]]), "bindings")
    machine = versioned(_document(before[paths["machine"]]), "machine")
    desired_bindings, desired_machine = bundle["request"]["bindings"], bundle["request"]["machine"]
    legacy_ids = {s['task_id'] for s in selected if (intent.get('legacy_transaction') or intent['operation'] == 'restore')
                  and s['task_id'] not in desired_machine['migrated_tasks']}
    projected_ids = ids if retiring else ids - legacy_ids
    names = {s['name'].casefold() for s in selected if s['task_id'] in projected_ids}
    if (machine.get("authority_epoch"), machine.get("migrated_tasks")) != (
            desired_machine["authority_epoch"], desired_machine["migrated_tasks"]):
        raise Conflict("active_authority_changed", "machine")
    require(bindings, "tasks", dict)
    require(machine, "overrides", dict)
    if machine.get("categories") != desired_machine["categories"]:
        raise Conflict("category_migration_required", "machine.categories")
    for spec in selected:
        legacy = spec['task_id'] in legacy_ids
        if not legacy:
            bindings["tasks"][spec["task_id"]] = deepcopy(desired_bindings["tasks"][spec["task_id"]])
        namespace = spec["installation_namespace"]
        if namespace is not None and not legacy:
            desired_install = desired_bindings["installations"][namespace]
            old_install = bindings.get("installations", {}).get(namespace)
            if old_install != desired_install and any(s["installation_namespace"] == namespace and s["task_id"] not in ids
                                                      for s in compiled["task_specs"]):
                raise Conflict("installation_migration_required", "installations")
            bindings.setdefault("installations", {})[namespace] = deepcopy(desired_install)
        if legacy:
            pass  # Preserve reviewed legacy bindings; only enabled state advances.
        elif spec["task_id"] in desired_machine["overrides"]:
            machine["overrides"][spec["task_id"]] = deepcopy(desired_machine["overrides"][spec["task_id"]])
        else:
            machine["overrides"].pop(spec["task_id"], None)
        bindings["tasks"][spec["task_id"]]["enabled"] = spec["enabled"]
        override = machine["overrides"].get(spec["task_id"], {})
        if "enabled" in override:
            override["enabled"] = spec["enabled"]
    if intent["migrate"] and ids - set(machine["migrated_tasks"]):
        machine["migrated_tasks"] += [s["task_id"] for s in selected if s["task_id"] not in machine["migrated_tasks"]]
        machine["authority_epoch"] += 1
    health = _document(before[paths["task-health.json"]])
    rows = require(health, "tasks", list)
    if any(not isinstance(row, dict) or not isinstance(row.get("name"), str) for row in rows):
        raise Conflict("invalid_health", "task-health.json")
    health["tasks"] = [row for row in rows if row["name"].casefold() not in names]
    projected = json.loads(compiled["generated_files"]["task-health.json"]["content"])
    if not retiring:
        health["tasks"] += [r for r in projected["tasks"] if r["task_id"] in projected_ids]
    text = _decode(before[paths["TaskNames.ps1"]])
    try:
        for name in allowlist.literal_names(text):
            if name.casefold() in names:
                text, _ = allowlist.remove_name(text, name)
        for spec in selected:
            if spec['task_id'] in projected_ids and spec["backup"] and not retiring:
                close = allowlist.find_block(text).start(3)
                text = text[:close] + "\n    '" + spec["name"].replace("'", "''") + "'\n" + text[close:]
        allowlist.literal_names(text)
    except allowlist.AllowlistError as exc:
        raise Conflict("invalid_allowlist", "TaskNames.ps1") from exc
    categories = _document(before[paths["categories.json"]])
    cats = require(categories, "categories", list)
    for cat in cats:
        members = require(cat, "tasks", list)
        if any(not isinstance(n, str) for n in members):
            raise Conflict("invalid_categories", "categories")
        cat["tasks"] = [n for n in members if n.casefold() not in names]
        if "taskDesc" in cat:
            descriptions = require(cat, "taskDesc", dict)
            if any(not isinstance(k, str) or not isinstance(v, str) for k, v in descriptions.items()):
                raise Conflict("invalid_categories", "categories.taskDesc")
            cat["taskDesc"] = {k: v for k, v in descriptions.items() if k.casefold() not in names}
    if not retiring:
        for descriptor in machine["categories"]:
            members = [s["name"] for s in selected if s['task_id'] in projected_ids and s["category"] == descriptor["id"]]
            matching = [c for c in cats if c.get("name") == descriptor["name"]]
            if len(matching) > 1:
                raise Conflict("ambiguous_category", "categories")
            if matching:
                matching[0]["tasks"] += members
            elif members:
                cats.append({k: v for k, v in descriptor.items() if k != "id"} | {"tasks": members})
            descriptions = {s["name"]: s["description"] for s in selected
                            if s['task_id'] in projected_ids and s["category"] == descriptor["id"] and s.get("description") is not None}
            if descriptions:
                target = next(c for c in cats if c.get("name") == descriptor["name"])
                target.setdefault("taskDesc", {}).update(descriptions)
    tombstones = _document(before[paths["tombstones"]])
    if retiring:
        for spec in selected:
            tombstones.setdefault(spec["task_id"], {"task_id": spec["task_id"], "reason": intent["reason"],
                "transaction_id": tx, "retired": True, "xml_before_image": "journal:" + tx,
                "linked_work_items": linked.get(spec['task_id']) if linked is not None else None})
    outputs = {"bindings": _encode(bindings), "machine": _encode(machine),
               "task-health.json": _encode(health), "TaskNames.ps1": text.encode("utf-8"),
               "categories.json": _encode(categories), "tombstones": _encode(tombstones)}
    result = {paths[role]: data for role, data in outputs.items()}
    if legacy_ids == ids and not retiring:
        # Legacy enable/disable/restore changes no unrelated projection or launcher.
        for role in ('task-health.json', 'TaskNames.ps1', 'categories.json', 'tombstones'):
            result[paths[role]] = before[paths[role]]['value']
    from .runtime_xml import launcher_bytes
    for spec in selected:
        launcher = bundle.get('launchers', {}).get(spec['task_id'])
        if launcher is not None and spec['task_id'] not in legacy_ids:
            context_name = spec['name'] if spec['name'] in (
                'ccModelRefresh', 'codexgModelRefresh', 'DemandMiningEOD', 'CcDailyTriage') else None
            result[launcher] = None if retiring else launcher_bytes(
                spec['argv'], spec['cwd'], scheduler_name=context_name)
    if retiring:
        for task_id in ids:
            if bundle["active_xml"][task_id] is not None:
                result[bundle["active_xml"][task_id]] = None
    return result


def _save(runtime, journal):
    runtime.journal.save(journal["transaction_id"], deepcopy(journal))


def _secure(runtime, key, snapshot):
    ref = runtime.vault.put(key, snapshot)
    if not isinstance(ref, str) or not ref or len(ref) > 512 or any(c in ref for c in "<>\r\n") or runtime.vault.get(ref) != snapshot:
        raise Conflict("secure_storage_unverified", "vault")
    return ref


def _stage(runtime, journal, kind, key, before, value):
    token = journal["transaction_id"] + ":" + str(len(journal["steps"]))
    step = {"kind": kind, "key": key, "token": token, "phase": "staging",
            "before": _secure(runtime, token + ":before", before)}
    journal["steps"].append(step)
    _save(runtime, journal)  # stage cleanup token is durable BEFORE preparation
    adapter = getattr(runtime, kind)
    after = _snapshot(adapter.prepare(key, value, token))
    step["after"] = _secure(runtime, token + ":after", after)
    step["phase"] = "staged"
    _save(runtime, journal)
    runtime.checkpoint("staged")
    return after


def _publish(runtime, journal, step):
    adapter = getattr(runtime, step["kind"])
    before, after = (runtime.vault.get(step[k]) for k in ("before", "after"))
    current = _read(adapter, step["key"])
    if step["kind"] == "scheduler":
        _idle(current)
    if not _same(step["kind"], current, before):
        raise Conflict("resource_changed", "resource")
    step["phase"] = "publishing"
    _save(runtime, journal)
    runtime.checkpoint("intent")
    adapter.publish(step["key"], before, after)
    runtime.checkpoint("published")
    current = _read(adapter, step["key"])
    if step["kind"] == "scheduler":
        _idle(current)
    if not _same(step["kind"], current, after):
        raise Conflict("readback_mismatch", "resource")
    step["phase"] = "done"
    _save(runtime, journal)


def _cleanup(runtime, journal):
    try:
        for step in journal["steps"]:
            adapter = getattr(runtime, step["kind"])
            adapter.cleanup(step["token"])
            adapter.cleanup(step["token"] + ":undo")
            runtime.checkpoint("cleanup")
    except Exception:
        journal["cleanup_error"] = "cleanup_required"
        _save(runtime, journal)
        return
    journal["cleaned"] = True
    journal.pop("cleanup_error", None)
    _save(runtime, journal)


def _result(journal):
    return {"schemaVersion": 1, "transaction_id": journal["transaction_id"],
            "ok": journal["status"] == "committed" and journal.get("cleaned", False),
            "failure": journal.get("failure"), "cleanup_error": journal.get("cleanup_error"),
            "status": journal["status"], "conflicts": journal.get("conflicts", []),
            "linked_work_items": journal["linked_work_items"], "cleaned": journal.get("cleaned", False),
            **{key: journal[key] for key in ('linked_work_items_status', 'linked_work_items_code') if key in journal}}


def _mark_undone(runtime, journal, step, before):
    step["phase"] = "undone"
    if "undo" in step:
        for prior in journal["steps"][:journal["steps"].index(step)]:
            if (prior["kind"], prior["key"]) == (step["kind"], step["key"]) and "after" in prior:
                if runtime.vault.get(prior["after"]) == before:
                    prior["after"] = step["undo"]
    _save(runtime, journal)


def _rollback(runtime, journal):
    journal["status"] = "recovering"
    _save(runtime, journal)
    conflicts = []
    try:
        if revision(runtime.load()) != journal["input_revision"]:
            raise Conflict("input_changed", "input_revision")
    except Exception:
        journal["status"] = "conflict"
        journal["conflicts"] = [{"code": "input_changed", "kind": "authority"}]
        _save(runtime, journal)
        return _result(journal)
    for step in reversed(journal["steps"]):
        if step["phase"] in ("staging", "staged", "undone"):
            if step["kind"] == "files":
                try:
                    expected = runtime.vault.get(step.get("undo", step["before"]))
                    if _read(runtime.files, step["key"]) != expected:
                        raise Conflict("concurrent_edit", "resource")
                except Exception as exc:
                    conflicts.append({"kind": "files", "key": step["key"],
                                      "code": exc.code if isinstance(exc, ContractError) else "recovery_failed"})
            continue
        adapter = getattr(runtime, step["kind"])
        try:
            before, after = (runtime.vault.get(step[k]) for k in ("before", "after"))
            current = _read(adapter, step["key"])
            if step["kind"] == "scheduler":
                _idle(current)
            undo = runtime.vault.get(step["undo"]) if "undo" in step else None
            if _same(step["kind"], current, before) or (undo is not None and _same(step["kind"], current, undo)):
                _mark_undone(runtime, journal, step, before)
                continue
            if not _same(step["kind"], current, after):
                if (step['kind'] == 'files' and step['phase'] in ('publishing', 'undoing')
                        and hasattr(adapter, 'recover_incomplete')):
                    _safely_disabled(runtime, journal)
                    if adapter.recover_incomplete(step['key'], before, after):
                        _mark_undone(runtime, journal, step, before)
                        continue
                raise Conflict("concurrent_edit", "resource")
            # A conflict in any file forbids re-enabling the original Scheduler
            # object with a partly restored binding/launcher generation.
            if step["kind"] == "files":
                # Disabling does not stop a running instance. Keep its complete
                # supporting generation until an idle, disabled state is known.
                _safely_disabled(runtime, journal)
            elif before.get("value", {}).get("enabled"):
                if conflicts:
                    raise Conflict("unsafe_reenable", "scheduler")
                try:
                    _verify_files(runtime, _restored_files(runtime, journal))
                except Conflict as exc:
                    raise Conflict("unsafe_reenable", "scheduler") from exc
            if undo is None:
                step['phase'] = 'undoing'
                _save(runtime, journal)  # Durable token/before-image before undo preparation.
                runtime.checkpoint('undo_prepare_intent')
                undo = adapter.prepare(step["key"], before.get("value"), step["token"] + ":undo")
                step["undo"] = _secure(runtime, step["token"] + ":undo", undo)
            step["phase"] = "undoing"
            _save(runtime, journal)
            runtime.checkpoint("undo_intent")
            adapter.publish(step["key"], current, undo)
            runtime.checkpoint("undone")
            current = _read(adapter, step["key"])
            if step["kind"] == "scheduler":
                _idle(current)
            if not _same(step["kind"], current, undo):
                raise Conflict("undo_readback_mismatch", "resource")
            # A later Scheduler enable step restores the disabled image with a
            # new transport identity. Earlier steps must recognize that exact
            # undo image, including on another recovery after a process crash.
            _mark_undone(runtime, journal, step, before)
        except Exception as exc:
            conflicts.append({"kind": step["kind"], "key": step["key"],
                              "code": exc.code if isinstance(exc, ContractError) else "recovery_failed"})
    if not conflicts:
        try:
            _verify_files(runtime, _restored_files(runtime, journal))
        except Exception as exc:
            conflicts.append({"kind": "files", "code": exc.code if isinstance(exc, ContractError) else "recovery_failed"})
    journal["conflicts"] = conflicts
    journal["status"] = "conflict" if conflicts else ('recovering' if hasattr(runtime, 'finish') else 'rolled_back')
    _save(runtime, journal)
    # Keep staged evidence for conflict resolution; cleanup is resumable separately.
    if not conflicts:
        if hasattr(runtime, 'finish'):
            runtime.finish(journal, 'rolled_back')
        _cleanup(runtime, journal)
    return _result(journal)


def apply(plan, expected_revision, *, runtime=None, restore_approval=None):
    runtime = _use(runtime)
    if not isinstance(plan, dict) or plan.get("mode") != "mutation" or plan.get("applicable") is not True:
        raise Conflict("mutation_plan_required", "plan")
    versioned(plan, "plan")
    for key, kind in (("intent", dict), ("baselines", dict), ("task_ids", list), ("input_revision", str)):
        require(plan, key, kind)
    require(plan["baselines"], "files", dict)
    if plan['intent'].get('operation') == 'restore' and (not restore_approval or restore_approval != plan.get('plan_revision')):
        raise Conflict('restore_approval_required', 'plan_revision')
    for key, kind in (("task_ids", list), ("operation", str), ("migrate", bool), ("approve_enable", bool)):
        require(plan["intent"], key, kind)
    if not isinstance(plan["intent"].get("reason"), str):
        raise Conflict("invalid_intent", "reason")
    if runtime.vault is None:
        raise Conflict("secure_storage_required", "vault")
    # One authority-generation lock plus all target locks, acquired in stable
    # order by the adapter. Recovery uses exactly the same lock set.
    keys = ["authority"] + ["task:" + i for i in plan["task_ids"]] + ["file:" + p for p in plan["baselines"]["files"]]
    with runtime.locks.hold(sorted(keys)):
        if runtime.journal.pending(sorted(keys)):
            raise Conflict("recovery_required", "journal")
        fresh, bundle, compiled, selected, before, file_before = _assemble(runtime, plan["intent"])
        if fresh["input_revision"] != expected_revision or plan["input_revision"] != expected_revision:
            raise Conflict("input_changed", "input_revision")
        if fresh != plan:
            raise Conflict("plan_changed", "plan_revision")
        tx = uuid.uuid4().hex
        outputs = _outputs(bundle, compiled, selected, file_before, plan["intent"], tx, linked=plan['linked_work_items'])
        journal = {"schemaVersion": 1, "transaction_id": tx, "status": "preparing",
                   "input_revision": expected_revision, "locks": sorted(keys), "steps": [],
                   "linked_work_items": plan["linked_work_items"], "cleaned": False}
        for key in ('linked_work_items_status', 'linked_work_items_code'):
            if key in plan:
                journal[key] = plan[key]
        if plan['intent']['operation'] == 'restore':
            journal['restore'] = {'task_ids': plan['task_ids'], 'approval': plan['plan_revision']}
        if hasattr(runtime, 'prepare_inputs'):
            runtime.prepare_inputs(journal, bundle, selected, outputs)
        runtime.journal.create(tx, journal)
        runtime.checkpoint("journal_created")
        try:
            journal["scheduler_before"] = {key: _secure(runtime, tx + ":scheduler:" + str(i), value)
                                           for i, (key, value) in enumerate(before.items())}
            journal["file_before"] = {key: _secure(runtime, tx + ":file:" + str(i), value)
                                      for i, (key, value) in enumerate(file_before.items())}
            _save(runtime, journal)
            if plan["intent"]["operation"] == "retire":
                journal["retired_xml"] = {s["task_id"]: _secure(runtime, tx + ":retired:" + str(i), before[s["name"]])
                                          for i, s in enumerate(selected)}
                _save(runtime, journal)
            disabled = {}
            for spec in selected:
                old = before[spec["name"]]
                if plan["intent"]["operation"] == "retire" and old["state"] == "absent":
                    disabled[spec["name"]] = old
                    continue
                if plan['intent']['operation'] == 'restore':
                    source = plan['intent']['restore_tasks'][spec['task_id']]['snapshot']
                    if spec['task_id'] in bundle['request']['machine']['migrated_tasks']:
                        value = runtime.render(deepcopy(spec), deepcopy(source), False)
                    else:
                        value = deepcopy(source['value'])
                        value.update(enabled=False, running=False)
                elif plan["intent"]["operation"] in ("enable", "disable", "retire") and old["state"] == "present":
                    value = deepcopy(old["value"])
                    value["enabled"] = False
                else:
                    value = runtime.render(deepcopy(spec), deepcopy(old), False)
                if value.get("enabled") is not False or value.get("running") is not False:
                    raise Conflict("disabled_preparation_required", "scheduler")
                disabled[spec["name"]] = (old if old.get("value") == value else
                    _stage(runtime, journal, "scheduler", spec["name"], old, value))
            for path, data in outputs.items():
                if file_before[path].get("value") != data:
                    _stage(runtime, journal, "files", path, file_before[path], data)
            if plan["intent"]["operation"] == "retire":
                for spec in selected:
                    if disabled[spec["name"]]["state"] == "present":
                        _stage(runtime, journal, "scheduler", spec["name"], disabled[spec["name"]], None)
            for spec in selected:
                if spec["enabled"]:
                    value = deepcopy(disabled[spec["name"]]["value"])
                    value["enabled"] = True
                    _stage(runtime, journal, "scheduler", spec["name"], disabled[spec["name"]], value)
            if revision(runtime.load()) != expected_revision:
                raise Conflict("input_changed", "input_revision")
            journal["status"] = "publishing"
            _save(runtime, journal)
            expected_scheduler = deepcopy(before)
            expected_files = deepcopy(file_before)
            for step in journal["steps"]:
                if step["kind"] == "files":
                    expected_files[step["key"]] = runtime.vault.get(step["after"])
            for step in journal["steps"]:
                if revision(runtime.load()) != expected_revision:
                    raise Conflict("input_changed", "input_revision")
                for name, expected in expected_scheduler.items():
                    current = _read(runtime.scheduler, name)
                    _idle(current)
                    if not _same("scheduler", current, expected):
                        raise Conflict("scheduler_changed", "scheduler")
                if step["kind"] == "scheduler" and runtime.vault.get(step["after"]).get("value", {}).get("enabled"):
                    # Includes unchanged resources. Locks/quiescence must span
                    # this readback and activation; arbitrary editors are not atomic.
                    _verify_files(runtime, expected_files)
                _publish(runtime, journal, step)
                if step["kind"] == "scheduler":
                    expected_scheduler[step["key"]] = runtime.vault.get(step["after"])
            # Revalidate readback of every final resource before the commit marker.
            _verify_files(runtime, expected_files)
            for name, expected in expected_scheduler.items():
                current = _read(runtime.scheduler, name)
                _idle(current)
                if not _same("scheduler", current, expected):
                    raise Conflict("final_readback_mismatch", "scheduler")
            if revision(runtime.load()) != expected_revision:
                raise Conflict("input_changed", "input_revision")
            if hasattr(runtime, 'finish'):
                runtime.finish(journal, 'committed')
            journal["status"] = "committed"
            _save(runtime, journal)
            runtime.checkpoint("committed")
        except Exception as exc:
            if journal['status'] == 'committing':
                # The receipt/pointer may already be published. Resume this
                # completion boundary explicitly; never compensate across it.
                journal['failure'] = {'code': 'completion_required'}
                _save(runtime, journal)
                return _result(journal)
            journal["failure"] = exc.to_dict() if isinstance(exc, ContractError) else {"code": "transport_failed"}
            _save(runtime, journal)
            return _rollback(runtime, journal)
        _cleanup(runtime, journal)
        return _result(journal)


def recover(transaction_id, *, runtime=None):
    runtime = _use(runtime)
    if runtime.vault is None:
        raise Conflict("secure_storage_required", "vault")
    journal = runtime.journal.load(transaction_id)
    versioned(journal, "journal")
    if journal["transaction_id"] != transaction_id:
        raise Conflict("journal_identity_mismatch", "journal")
    if isinstance(journal.get("bootstrap"), dict):
        # Initial-authority journals are recognized before any rollback, cleanup
        # or result construction. This recovery neither knows those authority
        # paths nor carries the explicit publication approval they require, so
        # it refuses safely and names the command and transaction that do.
        incomplete = journal["status"] != "committed" or not journal.get("cleaned", False)
        return {"schemaVersion": 1, "transaction_id": transaction_id, "kind": "initial-adoption",
                "status": journal["status"], "cleaned": journal.get("cleaned", False),
                "ok": not incomplete, "conflicts": [], "linked_work_items": None,
                "cleanup_error": journal.get("cleanup_error"),
                "failure": {"code": "bootstrap_recovery_required", "field": "journal"} if incomplete else None,
                "action_required": "adoption-resume" if incomplete else None,
                "message": ("complete this initial authority transaction with the explicitly approved "
                            "adoption-resume command and this transaction_id") if incomplete else None}
    with runtime.locks.hold(journal["locks"]):
        journal = runtime.journal.load(transaction_id)
        if journal['status'] == 'committing' and hasattr(runtime, 'finish'):
            runtime.finish(journal, journal['completion']['status'])
            _cleanup(runtime, journal)
            return _result(journal)
        if journal["status"] in ("committed", "rolled_back"):
            if not journal.get("cleaned"):
                _cleanup(runtime, journal)
            return _result(journal)
        return _rollback(runtime, journal)


def retire(task_id, expected_revision, *, reason, runtime=None):
    runtime = _use(runtime)
    plan = build_plan(runtime, [task_id], operation="retire", reason=reason)
    return apply(plan, expected_revision, runtime=runtime)


def control(name, verb, *, reason="", runtime=None):
    """Legacy bridge: None means positively established nonmigrated authority."""
    runtime = _use(runtime)
    bundle = runtime.load()
    epoch, migrated = _authority(bundle)
    compiled = _compile(bundle)  # malformed declarations never fall back
    matches = [s for s in compiled["task_specs"] if s["name"].casefold() == name.casefold()]
    if not matches:
        raise Conflict('ownership_required', 'task_id')
    if matches[0]["task_id"] not in migrated:
        proof = bundle.get('ownership', {}).get(matches[0]['task_id'])
        if (not isinstance(proof, dict) or proof.get('name') != matches[0]['name']
                or proof.get('task_path') != '\\' or proof.get('epoch') != epoch
                or proof.get('writer') != 'legacy' or 'identity' not in proof):
            raise Conflict('ownership_required', 'task_id')
        if _read(runtime.scheduler, proof['name']).get('identity') != proof['identity']:
            raise Conflict('scheduler_ownership_changed', 'ownership')
        return None
    if verb not in ("enable", "disable", "retire"):
        raise Conflict("migrated_control_required", "operation")
    plan = build_plan(runtime, [matches[0]["task_id"]], operation=verb,
                      approve_enable=verb == "enable", reason=reason)
    return apply(plan, plan["input_revision"], runtime=runtime)


def dispatch(name, verb, *, legacy, reason="", runtime=None):
    """Hold authority lock through a legacy action, preventing cutover races."""
    runtime = _use(runtime)
    with runtime.locks.hold(["authority"]):
        if runtime.journal.pending(["authority"]):
            raise Conflict("recovery_required", "journal")
        bundle = runtime.load()
        _, migrated = _authority(bundle)
        specs = _compile(bundle)["task_specs"]
        matches = [s for s in specs if s["name"].casefold() == name.casefold()]
        if not matches:
            raise Conflict('ownership_required', 'task_id')
        proof = bundle.get('ownership', {}).get(matches[0]['task_id'])
        epoch, _ = _authority(bundle)
        if (not isinstance(proof, dict) or proof.get('name') != matches[0]['name']
                or proof.get('task_path') != '\\' or proof.get('epoch') != epoch
                or proof.get('writer') != ('declarations' if matches[0]['task_id'] in migrated else 'legacy')
                or 'identity' not in proof):
            raise Conflict('ownership_required', 'task_id')
        from .scheduler_windows import task_name
        task_name(name)
        if verb not in ('enable', 'disable', 'run', 'stop', 'retire'):
            raise Conflict('invalid_operation', 'operation')
        spec = matches[0]
        # The authority lock is first for every writer. Task locks cover the
        # complete callback, including old projection edits and readback.
        with runtime.locks.hold(['task:' + spec['task_id']]):
            snapshot = _read(runtime.scheduler, proof['name'])
            if snapshot.get('identity') != proof['identity']:
                raise Conflict('scheduler_ownership_changed', 'ownership')
            if spec['task_id'] not in migrated:
                if verb in ('enable', 'disable', 'retire'):
                    # Arbitrary mutating callbacks cannot provide recoverable
                    # after-images. Explicitly convert built-in controls below.
                    if legacy is not None:
                        raise Conflict('legacy_declarative_conversion_required', 'operation')
                else:
                    return legacy()
            if verb in ('run', 'stop'):
                if snapshot['state'] != 'present':
                    raise Conflict('task_missing', 'scheduler')
                if hasattr(runtime, 'validate_control'):
                    runtime.validate_control(spec, snapshot)
                if verb == 'run' and not spec['enabled']:
                    raise Conflict('task_disabled', 'scheduler')
                return runtime.scheduler.control(proof['name'], verb, snapshot)
    # apply will acquire the full lock set and revalidate the declaration again.
    if spec['task_id'] not in migrated:
        plan = build_plan(runtime, [spec['task_id']], operation=verb,
                          approve_enable=verb == 'enable', reason=reason, legacy_transaction=True)
        result = apply(plan, plan['input_revision'], runtime=runtime)
    else:
        result = control(name, verb, reason=reason, runtime=runtime)
    if result is None:
        raise Conflict("authority_changed", "task_id")
    if verb in ('enable', 'disable') and result.get('ok'):
        value = snapshot.get('value', {})
        result['before'] = ('Running' if value.get('running') else
                            'Ready' if value.get('enabled') else 'Disabled') if snapshot['state'] == 'present' else '(gone)'
        result['after'] = 'Ready' if verb == 'enable' else 'Disabled'
    return result
