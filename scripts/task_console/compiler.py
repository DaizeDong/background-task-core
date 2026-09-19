"""Pure read-only planning against caller-supplied legacy and Scheduler snapshots.

Generated files are declared-task projections, never replacement instructions.
This module neither imports the web server nor launches a scheduler or payload.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json

from . import allowlist
from .components import compile_tasks, _trigger
from .contracts import ContractError, SCHEDULER_FIELDS, fields, require, versioned
from .freshness import OK_CODE_KEYS, declared_ok_codes


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _revision(value) -> str:
    return "sha256:" + hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def validate_trigger_shape(trigger):
    """Only accept declaration fields that have defined rendering semantics."""
    _trigger(trigger)
    entries = trigger if isinstance(trigger, list) else [trigger]
    shapes = {'daily': {'type', 'at'}, 'weekly': {'type', 'at', 'days'},
              'interval': {'type', 'seconds', 'minutes'}, 'logon': {'type'},
              'xml': {'type', 'xml', 'owner', 'reason'}}
    for entry in entries:
        fields(entry, shapes[entry['type']], 'trigger')
        if entry['type'] == 'weekly' and len(set(entry['days'])) != len(entry['days']):
            raise ContractError('duplicate_day', 'trigger.days')


def _health_row(row: dict) -> dict:
    result = {k: v for k, v in row.items() if k not in ("task_id", "component", "check_id", *OK_CODE_KEYS)}
    if any(key in row for key in OK_CODE_KEYS):
        result[OK_CODE_KEYS[0]] = declared_ok_codes(row)
    return result


def _rows(data, key: str) -> list:
    rows = data.get(key) if isinstance(data, dict) else data
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ContractError("invalid_type", key)
    return rows


def _compare(before, after) -> dict:
    return {"status": "equal" if before == after else "different", "before": before, "after": after}


def _legacy_parity(baseline: dict, health: dict, names: str, categories: dict) -> dict:
    parity = {}
    for key, after in (("task_names", names), ("task_health", health), ("categories", categories)):
        before = baseline.get(key)
        if before is None:
            parity[key] = {"status": "not-checked", "reason_code": "missing_source"}
            continue
        try:
            if key == "task_names":
                if not isinstance(before, str):
                    raise ContractError("invalid_type", key)
                parsed, reason = allowlist.parse_names(before)
                if parsed is None:
                    parity[key] = {"status": "not-checked", "reason_code": "unreadable_allowlist", "reason": reason}
                    continue
                before, after = sorted(parsed), sorted(allowlist.literal_names(after))
            elif key == "task_health":
                old = _rows(before, "tasks")
                if any(not isinstance(row.get("name"), str) or not row["name"] for row in old):
                    raise ContractError("missing_field", "name")
                before = sorted(_json(_health_row(row)) for row in old)
                after = sorted(_json(_health_row(row)) for row in after["tasks"])
            else:
                before, after = _rows(before, "categories"), after["categories"]
                for row in before:
                    require(row, "name", str)
                    if any(not isinstance(name, str) for name in require(row, "tasks", list)):
                        raise ContractError("invalid_type", "categories.tasks")
            parity[key] = _compare(before, after)
        except (ContractError, TypeError, ValueError):
            parity[key] = {"status": "invalid", "reason_code": "malformed_source"}
    return parity


def _scheduler_parity(baseline: dict, specs: list[dict]) -> tuple[dict, list, list]:
    observed = baseline.get("tasks")
    if observed is None:
        return {"status": "not-checked", "reason_code": "missing_source"}, [], []
    if (not isinstance(observed, list) or any(not isinstance(row, dict) or not isinstance(row.get("name"), str)
                                             or not row["name"] for row in observed)):
        return {"status": "invalid", "reason_code": "malformed_source"}, [], []
    by_name = {row["name"].casefold(): row for row in observed}
    if len(by_name) != len(observed):
        return {"status": "invalid", "reason_code": "duplicate_name"}, [], []
    changes, missing, absent = [], [], 0
    for task in specs:
        old = by_name.get(task["name"].casefold())
        if old is None:
            absent += 1
            changes.append({"kind": "scheduler-proposal", "operation": "create", "task_id": task["task_id"],
                            "name": task["name"], "before": None, "after": deepcopy(task), "blocked": True,
                            "reason_code": "task_absent_requires_review"})
            continue
        for field in SCHEDULER_FIELDS:
            if field not in old and field != "xml_passthrough":
                missing.append({"task_id": task["task_id"], "field": field, "reason_code": "field_not_observed"})
            elif old.get(field) != task[field]:
                blocked = field == "enabled" and old[field] is False and task[field] is True
                changes.append({"kind": "scheduler-proposal", "task_id": task["task_id"], "name": task["name"],
                                "field": field, "before": old.get(field), "after": task[field], "blocked": blocked,
                                "reason_code": "disabled_task_requires_review" if blocked else "field_drift"})
    owned = {task["name"].casefold() for task in specs}
    adopt = [{"name": row["name"], "automatic": False, "observation": row,
              "required_inputs": ["component task declaration", "private binding", "monitoring checks"]}
             for row in observed if row["name"].casefold() not in owned]
    status = "not-checked" if missing else "different" if changes else "equal"
    return {"status": status, "missing": missing, "absent_tasks": absent,
            "compared_tasks": len(specs) - absent - len({m["task_id"] for m in missing}),
            "unmanaged_tasks": len(adopt)}, changes, adopt


def plan(request: dict) -> dict:
    """Return JSON-compatible projections and comparisons; perform no writes/I/O."""
    versioned(request, "request")
    fields(request, {"schemaVersion", "components", "bindings", "machine", "baseline"}, "request")
    try:
        input_revision = _revision(request)
    except (TypeError, ValueError) as exc:
        raise ContractError("invalid_json", "request") from exc
    request = deepcopy(request)
    specs = [task.to_dict() for task in compile_tasks(require(request, "components", list),
             require(request, "bindings", dict), require(request, "machine", dict))]
    for spec in specs:
        validate_trigger_shape(spec['trigger'])
    baseline = require(request, "baseline", dict)
    fields(baseline, {"tasks", "task_names", "task_health", "categories"}, "baseline")
    rows, coverage = [], []
    for task in specs:
        for check in task["checks"]:
            entry = {"task_id": task["task_id"], "check_id": check["id"], "required": check["required"],
                     "state": check["binding_state"]}
            if "legacy" in check:
                rows.append({**check["legacy"], "name": task["name"], "component": task["component"],
                             "task_id": task["task_id"], "check_id": check["id"]})
            if "watched_elsewhere" in check:
                entry["watched_elsewhere"] = check["watched_elsewhere"]
            coverage.append(entry)
    health = {"tasks": rows}
    names = allowlist.render_names([task["name"] for task in specs if task["backup"]])
    categories = {"categories": [{k: v for k, v in cat.items() if k != "id"} | {
        "tasks": [task["name"] for task in specs if task["category"] == cat["id"]]}
        for cat in request["machine"]["categories"]]}
    for descriptor, category in zip(request["machine"]["categories"], categories["categories"]):
        descriptions = {task["name"]: task["description"] for task in specs
                        if task["category"] == descriptor["id"] and task["description"] is not None}
        if descriptions:
            category["taskDesc"] = descriptions
    parity = _legacy_parity(baseline, health, names, categories)
    parity["scheduler"], changes, adopt = _scheduler_parity(baseline, specs)
    for key, comparison in parity.items():
        if key != "scheduler" and comparison["status"] == "different":
            changes.append({"kind": "projection-difference", "projection": key, **comparison})
    available = Counter()
    if parity["task_health"]["status"] in ("equal", "different"):
        available.update(_json(_health_row(row)) for row in _rows(baseline["task_health"], "tasks"))
    for row in rows:
        key = _json(_health_row(row))
        entry = next(c for c in coverage if (c["task_id"], c["check_id"]) == (row["task_id"], row["check_id"]))
        if parity["task_health"]["status"] not in ("equal", "different"):
            entry["state"] = "not-checked"
            entry["reason_code"] = parity["task_health"]["reason_code"]
        else:
            entry["state"] = "matched" if available[key] else "not-matched"
        if available[key]:
            available[key] -= 1
    generated = {}
    for name, content in (("task-health.json", json.dumps(health, ensure_ascii=True, indent=2) + "\n"),
                          ("TaskNames.ps1", names), ("categories.json", json.dumps(categories, ensure_ascii=True, indent=2) + "\n")):
        generated[name] = {"content": content, "digest": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
                           "scope": "declared-tasks-only",
                           "replacement_safe": False, "owners": [task["task_id"] for task in specs]}
    return {"schemaVersion": 1, "mode": "read-only", "applicable": False,
            "authority": {"mode": "legacy", "epoch": request["machine"]["authority_epoch"], "migrated_tasks": []},
            "baseline_revision": _revision(baseline), "input_revision": input_revision,
            "task_specs": specs, "changes": changes, "generated_files": generated, "parity": parity,
            "check_coverage": {"declared": len(coverage), "matched": sum(c["state"] == "matched" for c in coverage),
                               "evaluated": 0, "checks": coverage}, "adopt_proposals": adopt}
