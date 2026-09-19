"""Compile explicit component declarations and private bindings, without I/O."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import PurePosixPath, PureWindowsPath
import re
from xml.etree import ElementTree

from .contracts import ContractError, TaskSpec, fields, require, versioned
from .freshness import OK_CODE_KEYS

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_BINDING_FIELDS = {"name", "enabled", "argv", "cwd", "source_root", "timezone", "trigger", "principal",
                   "power", "timeout_seconds", "concurrency", "backup", "category", "checks",
                   "xml_passthrough", "credential_ref", "description"}


def _identifier(data: dict, field: str) -> str:
    value = require(data, field, str)
    if not _ID.fullmatch(value):
        raise ContractError("invalid_id", field)
    return value


def _nonnegative(value: int, field: str) -> None:
    if value < 0:
        raise ContractError("invalid_value", field)


def _xml(data: dict) -> None:
    if "xml" not in data:
        raise ContractError("missing_field", "xml")
    xml = data["xml"]
    if not isinstance(xml, str) or not xml.strip():
        raise ContractError("invalid_type", "xml")
    require(data, "owner", str)
    require(data, "reason", str)
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
        raise ContractError("invalid_xml", "xml")
    try:
        ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ContractError("invalid_xml", "xml") from exc


def _trigger(trigger) -> None:
    if isinstance(trigger, list):
        for item in trigger:
            if not isinstance(item, dict):
                raise ContractError("invalid_type", "trigger")
            _trigger(item)
        return
    if not isinstance(trigger, dict):
        raise ContractError("invalid_type", "trigger")
    kind = require(trigger, "type", str)
    if kind == "xml":
        _xml(trigger)
    elif kind == "interval":
        units = [key for key in ("seconds", "minutes") if key in trigger]
        if len(units) != 1 or require(trigger, units[0], int) <= 0:
            raise ContractError("invalid_value", "trigger.interval")
    elif kind in ("daily", "weekly"):
        at = require(trigger, "at", str)
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?", at):
            raise ContractError("invalid_value", "trigger.at")
        if kind == "weekly":
            days = require(trigger, "days", list)
            if not days or any(d not in ("mon", "tue", "wed", "thu", "fri", "sat", "sun") for d in days):
                raise ContractError("invalid_value", "trigger.days")
    elif kind != "logon":
        raise ContractError("unsupported_trigger", "trigger.type")


def _checks(declarations: list, bindings: list) -> list[dict]:
    declared = {}
    for check in declarations:
        if not isinstance(check, dict):
            raise ContractError("invalid_type", "checks")
        fields(check, {"id", "required"}, "checks")
        check_id = _identifier(check, "id")
        require(check, "required", bool)
        if check_id in declared:
            raise ContractError("duplicate_check", "checks.id")
        declared[check_id] = check
    bound = {}
    for check in bindings:
        if not isinstance(check, dict):
            raise ContractError("invalid_type", "checks")
        fields(check, {"id", "legacy", "watched_elsewhere"}, "checks")
        check_id = _identifier(check, "id")
        if check_id not in declared:
            raise ContractError("unknown_check", "checks.id")
        if check_id in bound:
            raise ContractError("duplicate_check", "checks.id")
        methods = [key for key in ("legacy", "watched_elsewhere") if key in check]
        if len(methods) != 1:
            raise ContractError("invalid_monitor", "checks")
        method = methods[0]
        config = require(check, method, dict)
        if method == "legacy":
            if not config or {"name", "task_id", "component", "check_id"} & config.keys():
                raise ContractError("invalid_monitor", "checks.legacy")
            if any(config.get(key) is not None and not isinstance(config[key], list) for key in OK_CODE_KEYS):
                raise ContractError("invalid_monitor", "checks.legacy.codes")
        else:
            for key in ("component", "check_id", "observation_ref"):
                require(config, key, str)
        bound[check_id] = {**check, "binding_state": method}
    return [{**check, **bound.get(check_id, {"binding_state": "unbound"})}
            for check_id, check in declared.items()]


def _binding(binding: dict) -> None:
    fields(binding, _BINDING_FIELDS, "binding")
    for key in ("name", "cwd", "source_root", "timezone", "category"):
        require(binding, key, str)
    for key in ("cwd", "source_root"):
        if not (PureWindowsPath(binding[key]).is_absolute() or PurePosixPath(binding[key]).is_absolute()):
            raise ContractError("absolute_path_required", key)
    for key in ("enabled", "backup"):
        require(binding, key, bool)
    for key in ("principal", "power", "concurrency"):
        if not require(binding, key, dict):
            raise ContractError("invalid_value", key)
    if binding["concurrency"].get("policy") not in ("IgnoreNew", "Parallel", "Queue", "StopExisting"):
        raise ContractError("invalid_value", "concurrency.policy")
    _nonnegative(require(binding, "timeout_seconds", int), "timeout_seconds")
    argv = binding.get("argv")
    if (not isinstance(argv, list) or not argv or not isinstance(argv[0], str) or not argv[0]
            or any(not isinstance(arg, str) or "\0" in arg for arg in argv)):
        raise ContractError("invalid_argv", "argv")
    if not (PureWindowsPath(argv[0]).is_absolute() or PurePosixPath(argv[0]).is_absolute()):
        raise ContractError("absolute_path_required", "argv.0")
    if "trigger" not in binding:
        raise ContractError("missing_field", "trigger")
    _trigger(binding["trigger"])
    require(binding, "checks", list)
    if "xml_passthrough" in binding:
        _xml(require(binding, "xml_passthrough", dict))
    if "credential_ref" in binding:
        require(binding, "credential_ref", str)
    if "description" in binding:
        if not isinstance(binding["description"], str) or "\0" in binding["description"]:
            raise ContractError("invalid_description", "binding.description")


def _installed_manifests(manifests: list, bindings: dict) -> list:
    """Join explicit private namespaces to caller-supplied catalog descriptors.

    The wrapper belongs to the request, never the public manifest. Catalog
    discovery and identity normalization remain the caller's responsibility.
    """
    installations = require(bindings, "installations", dict) if "installations" in bindings else {}
    result, used, identities = [], set(), set()
    for item in deepcopy(manifests):
        namespace, identity = None, None
        if isinstance(item, dict) and ("namespace" in item or "manifest" in item):
            fields(item, {"namespace", "manifest"}, "components.installation")
            namespace = _identifier(item, "namespace")
            if namespace in used:
                raise ContractError("duplicate_namespace", "components.namespace")
            used.add(namespace)
            if namespace not in installations:
                raise ContractError("missing_installation", "bindings.installations")
            descriptor = installations[namespace]
            if not isinstance(descriptor, dict):
                raise ContractError("invalid_type", "bindings.installations")
            fields(descriptor, {"component", "identity"}, "bindings.installations")
            manifest = require(item, "manifest", dict)
            if _identifier(descriptor, "component") != _identifier(manifest, "component"):
                raise ContractError("installation_component_mismatch", "bindings.installations.component")
            identity = require(descriptor, "identity", dict)
            key = tuple(require(identity, field, str) for field in ("marketplace", "scope", "client", "source_id"))
            try:
                json.dumps(identity, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ContractError("invalid_json", "bindings.installations.identity") from exc
            if key in identities:
                raise ContractError("duplicate_installation_identity", "bindings.installations.identity")
            identities.add(key)
        else:
            manifest = item
        result.append((manifest, namespace, identity))
    if installations.keys() - used:
        raise ContractError("unused_installation", "bindings.installations")
    legacy = {manifest.get("component") for manifest, namespace, _ in result
              if namespace is None and isinstance(manifest, dict) and isinstance(manifest.get("component"), str)}
    if any(manifest.get("component") in legacy for manifest, namespace, _ in result if namespace is not None):
        raise ContractError("ambiguous_component", "components")
    return result


def compile_tasks(manifests: list[dict], bindings: dict, machine: dict) -> list[TaskSpec]:
    """Bind each declared task once; no discovery, scheduling or process startup."""
    if not isinstance(manifests, list) or not manifests:
        raise ContractError("missing_components", "components")
    bindings, machine = deepcopy(bindings), deepcopy(machine)
    versioned(bindings, "bindings")
    versioned(machine, "machine")
    fields(bindings, {"schemaVersion", "tasks", "installations"}, "bindings")
    fields(machine, {"schemaVersion", "authority_epoch", "migrated_tasks", "overrides", "categories"}, "machine")
    tasks = require(bindings, "tasks", dict)
    overrides = require(machine, "overrides", dict)
    _nonnegative(require(machine, "authority_epoch", int), "authority_epoch")
    if require(machine, "migrated_tasks", list):
        raise ContractError("unsupported_authority", "migrated_tasks")
    categories = set()
    for category in require(machine, "categories", list):
        if not isinstance(category, dict):
            raise ContractError("invalid_type", "categories")
        fields(category, {"id", "name", "desc"}, "categories")
        category_id = _identifier(category, "id")
        require(category, "name", str)
        if "desc" in category:
            require(category, "desc", str)
        if category_id in categories:
            raise ContractError("duplicate_category", "categories.id")
        categories.add(category_id)
    result, seen, names, components = [], set(), set(), set()
    for manifest, namespace, identity in _installed_manifests(manifests, bindings):
        versioned(manifest, "component")
        fields(manifest, {"schemaVersion", "component", "read", "tasks"}, "component")
        component = _identifier(manifest, "component")
        component_key = (namespace, component)
        if component_key in components:
            raise ContractError("duplicate_component", "component")
        components.add(component_key)
        reader = require(manifest, "read", str)
        for task in require(manifest, "tasks", list):
            if not isinstance(task, dict):
                raise ContractError("invalid_type", "tasks")
            fields(task, {"id", "kind", "entrypoint", "schedule_hint", "timeout_seconds", "concurrency_key", "checks"}, "tasks")
            task_id = component + "/" + _identifier(task, "id")
            if namespace is not None:
                task_id = namespace + "/" + task_id
            if task_id in seen:
                raise ContractError("duplicate_task", "tasks.id")
            seen.add(task_id)
            kind = require(task, "kind", str)
            if kind not in ("oneshot", "daemon", "dispatcher"):
                raise ContractError("invalid_value", "tasks.kind")
            _nonnegative(require(task, "timeout_seconds", int), "timeout_seconds")
            if "schedule_hint" in task:
                _trigger(require(task, "schedule_hint", dict))
            if task_id not in tasks:
                raise ContractError("missing_binding", "bindings.tasks")
            binding = tasks[task_id]
            if not isinstance(binding, dict) or not isinstance(overrides.get(task_id, {}), dict):
                raise ContractError("invalid_type", "binding")
            binding = {**binding, **overrides.get(task_id, {})}
            _binding(binding)
            if binding["category"] not in categories:
                raise ContractError("unknown_category", "category")
            if binding["name"].casefold() in names:
                raise ContractError("duplicate_name", "name")
            names.add(binding["name"].casefold())
            binding["checks"] = _checks(require(task, "checks", list), binding["checks"])
            result.append(TaskSpec(component=component, task_id=task_id, kind=kind,
                                   entrypoint=require(task, "entrypoint", str), read=reader,
                                   schedule_hint=task.get("schedule_hint"),
                                   recommended_timeout_seconds=task["timeout_seconds"],
                                   installation_namespace=namespace, installation_identity=identity,
                                   concurrency_key=require(task, "concurrency_key", str), **binding))
    if (tasks.keys() | overrides.keys()) - seen:
        raise ContractError("unknown_task", "bindings.tasks/overrides")
    if not result:
        raise ContractError("missing_tasks", "components.tasks")
    return result
