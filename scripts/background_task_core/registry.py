"""Task declarations and the registration authority: contracts, the backup allowlist parser,
component compilation, the plan compiler, registration transactions and the creation check.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Callable
from xml.etree import ElementTree

from .runs import OK_CODE_KEYS, declared_ok_codes

# ---- contracts ---------------------------------------------------------------------------------
# Version-one contracts for the read-only declaration producer.


SCHEMA_VERSION = 1
# %NAME% names an environment variable that the hidden launcher's WScript.Shell.Run expands when
# the task runs, so a declaration can say where things are without naming one machine's paths.
VARIABLE_REFERENCE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")
SCHEDULER_FIELDS = ("name", "enabled", "argv", "cwd", "timezone", "trigger", "principal",
                    "power", "timeout_seconds", "concurrency", "xml_passthrough")


class ContractError(ValueError):
    """A safe diagnostic describing the field, never echoing its supplied value."""

    def __init__(self, code: str, field: str):
        self.code, self.field = code, field
        super().__init__(f"{code}: {field}")

    def to_dict(self) -> dict:
        return {"code": self.code, "field": self.field}


def require(data: dict, field: str, kind: type):
    if field not in data:
        raise ContractError("missing_field", field)
    value = data[field]
    if type(value) is not kind:
        raise ContractError("invalid_type", field)
    if kind is str and (not value.strip() or any(ord(c) < 32 for c in value)):
        raise ContractError("invalid_value", field)
    return value


def versioned(data, field: str) -> dict:
    if not isinstance(data, dict):
        raise ContractError("invalid_type", field)
    if type(data.get("schemaVersion")) is not int or data["schemaVersion"] != SCHEMA_VERSION:
        raise ContractError("unsupported_schema", field)
    return data


def stray_percent(text: str) -> bool:
    """True when a '%' is not part of a %NAME% reference (Run would still expand it)."""
    return "%" in VARIABLE_REFERENCE.sub("", text)


def anchored_path(text: str) -> bool:
    """An absolute path, or one that starts with a %NAME% reference followed by a separator or nothing."""
    if PureWindowsPath(text).is_absolute() or PurePosixPath(text).is_absolute():
        return True
    match = VARIABLE_REFERENCE.match(text)
    return match is not None and text[match.end():match.end() + 1] in ("", "\\", "/")


def fields(data: dict, allowed: set[str], field: str) -> None:
    if data.keys() - allowed:
        raise ContractError("unknown_field", field)


@dataclass(frozen=True)
class TaskSpec:
    component: str
    task_id: str
    kind: str
    entrypoint: str
    read: str
    schedule_hint: dict | None
    concurrency_key: str
    name: str
    enabled: bool
    argv: list[str]
    cwd: str
    source_root: str
    timezone: str
    trigger: dict | list[dict]
    principal: dict
    power: dict
    timeout_seconds: int
    recommended_timeout_seconds: int
    concurrency: dict
    backup: bool
    category: str
    checks: list[dict]
    xml_passthrough: dict | None = None
    credential_ref: str | None = None
    installation_namespace: str | None = None
    installation_identity: dict | None = None
    description: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---- allowlist ---------------------------------------------------------------------------------
# Read and edit literal PowerShell TaskNames arrays without evaluating a script.
#
# parse_names returns a set, or (None, reason) for absent, malformed and empty
# arrays. literal_names is strict: it distinguishes an empty array from a failed
# read so retirement remains idempotent after removing the last name.


class AllowlistError(ValueError):
    """The script has no unambiguous literal TaskNames assignment."""


@dataclass(frozen=True)
class _Literal:
    value: str
    start: int
    end: int


def _comment_end(text: str, pos: int) -> int:
    if text.startswith("<#", pos):
        depth, pos = 1, pos + 2
        while pos < len(text) and depth:
            if text.startswith("<#", pos):
                depth += 1
                pos += 2
            elif text.startswith("#>", pos):
                depth -= 1
                pos += 2
            else:
                pos += 1
        if depth:
            raise AllowlistError("Unterminated block comment")
        return pos
    end = text.find("\n", pos)
    return len(text) if end < 0 else end


def _string(text: str, pos: int, *, literal: bool) -> _Literal:
    start, quote, value = pos, text[pos], []
    pos += 1
    escapes = {"0": "\0", "a": "\a", "b": "\b", "f": "\f", "n": "\n",
               "r": "\r", "t": "\t", "v": "\v"}
    while pos < len(text):
        char = text[pos]
        if char == quote:
            if pos + 1 < len(text) and text[pos + 1] == quote:
                value.append(quote)
                pos += 2
                continue
            return _Literal("".join(value), start, pos + 1)
        if quote == '"' and char == "`":
            pos += 1
            if pos >= len(text):
                break
            char = escapes.get(text[pos], text[pos])
        elif quote == '"' and char == "$" and literal:
            raise AllowlistError("Interpolation is not a literal task name")
        value.append(char)
        pos += 1
    raise AllowlistError("Unterminated string")


def _trivia(text: str, pos: int) -> int:
    while pos < len(text):
        if text[pos].isspace() or text[pos] == "\ufeff":
            pos += 1
        elif text.startswith("`\r\n", pos):
            pos += 3
        elif text.startswith("`\n", pos):
            pos += 2
        elif text[pos] == "#" or text.startswith("<#", pos):
            pos = _comment_end(text, pos)
        else:
            break
    return pos


_TASK_VARIABLE = re.compile(
    r"\$(?:\{(?:(?:script|global|local|private):)?TaskNames\}|"
    r"(?:(?:script|global|local|private):)?TaskNames\b)", re.I)


def _mutates_task_names(text: str, pos: int) -> bool:
    prefix = text[pos:pos + 2] in ("++", "--")
    start = _trivia(text, pos + 2) if prefix else pos
    variable = _TASK_VARIABLE.match(text, start)
    if variable is None:
        return False
    if prefix:
        return True
    end = _trivia(text, variable.end())
    # Follow index expressions without interpreting them. Strings and comments
    # may themselves contain brackets, so a flat bracket regex is insufficient.
    while end < len(text) and text[end] == "[":
        depth, end = 1, end + 1
        while end < len(text) and depth:
            end = _trivia(text, end)
            if end >= len(text):
                break
            if text[end] in "\"'":
                end = _string(text, end, literal=False).end
                continue
            if text[end] == "[":
                depth += 1
            elif text[end] == "]":
                depth -= 1
            end += 1
        if depth:
            raise AllowlistError("Unterminated TaskNames index")
        end = _trivia(text, end)
    return re.match(r"(?:[+*/%\-]?=|\+\+|--)", text[end:]) is not None


def _parse(text: str):
    assignment = re.compile(r"\$TaskNames\b\s*=\s*@\(", re.I)
    found = None
    pos = 0
    while pos < len(text):
        if text[pos:pos + 2] in ("@'", '@"'):
            quote = text[pos + 1]
            end = re.search(r"(?m)^" + re.escape(quote + "@"), text[pos + 2:])
            if not end:
                raise AllowlistError("Unterminated here-string")
            pos += 2 + end.end()
            continue
        if text[pos] == "#" or text.startswith("<#", pos):
            pos = _comment_end(text, pos)
            continue
        if text[pos] in "\"'":
            pos = _string(text, pos, literal=False).end
            continue
        match = assignment.match(text, pos)
        if not match:
            if _mutates_task_names(text, pos):
                raise AllowlistError("Nonliteral TaskNames assignment or mutation")
            pos += 1
            continue
        if found is not None:
            raise AllowlistError("Multiple TaskNames assignments")
        start, body_start = pos, match.end()
        pos, names, commas = body_start, [], []
        need_value = True
        while True:
            previous = pos
            pos = _trivia(text, pos)
            if pos >= len(text):
                raise AllowlistError("Unterminated TaskNames array")
            if text[pos] == ")":
                if need_value and names:
                    raise AllowlistError("Trailing comma in TaskNames")
                break
            if not need_value:
                if text[pos] == ",":
                    commas.append((len(names) - 1, pos))
                    pos += 1
                    need_value = True
                    continue
                if "\n" not in text[previous:pos]:
                    raise AllowlistError("Expected a comma or newline between names")
            if text[pos] not in "\"'":
                raise AllowlistError("TaskNames accepts quoted literals only")
            name = _string(text, pos, literal=True)
            if not name.value or any(ord(c) < 32 for c in name.value):
                raise AllowlistError("Empty or control-character task name")
            names.append(name)
            pos = name.end
            need_value = False
        end = pos + 1
        tail = _trivia(text, end)
        if tail < len(text) and "\n" not in text[end:tail] and text[tail] != ";":
            raise AllowlistError("Executable expression after TaskNames array")
        found = start, body_start, pos, end, names, commas
        pos = end
    if found is None:
        raise AllowlistError("找不到 $TaskNames = @( ... ) 这个块")
    return found


def literal_names(text: str) -> list[str]:
    """Strict reader: preserve order/duplicates; raise on an unchecked source."""
    return [name.value for name in _parse(text)[4]]


def parse_names(text: str) -> tuple[set[str] | None, str | None]:
    try:
        names = set(literal_names(text))
    except AllowlistError as exc:
        return None, str(exc)
    if not names:
        return None, "$TaskNames 里解析出 0 个名字,判为未检查而不是全部缺失"
    return names, None


def remove_name(text: str, name: str) -> tuple[str, bool]:
    """Remove exact literals and their separators, preserving comments."""
    try:
        _, _, _, _, names, commas = _parse(text)
    except AllowlistError:
        return text, False
    removed = {i for i, token in enumerate(names) if token.value == name}
    if not removed:
        return text, False
    kept = set(range(len(names))) - removed
    spans = [(token.start, token.end) for i, token in enumerate(names) if i in removed]
    spans.extend((pos, pos + 1) for i, pos in commas
                 if i not in kept or not any(j > i for j in kept))
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + text[end:]
    return text, True


def render_names(names: list[str]) -> str:
    """Render data as single-quoted literals; never interpolate PowerShell."""
    text = "$TaskNames = @(\n" + ",\n".join(
        "    '" + name.replace("'", "''") + "'" for name in names) + "\n)\n"
    literal_names(text)
    return text


def closing_paren(text: str) -> int:
    """Position of the closing parenthesis of the TaskNames array."""
    return _parse(text)[2]


# ---- components --------------------------------------------------------------------------------
# Compile explicit component declarations and private bindings, without I/O.


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
    elif kind in ("daily", "weekly", "monthly"):
        at = require(trigger, "at", str)
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?", at):
            raise ContractError("invalid_value", "trigger.at")
        if kind == "weekly":
            days = require(trigger, "days", list)
            if not days or any(d not in ("mon", "tue", "wed", "thu", "fri", "sat", "sun") for d in days):
                raise ContractError("invalid_value", "trigger.days")
        if kind == "monthly":
            days = require(trigger, "days", list)
            if not days or any(type(d) is not int or not 1 <= d <= 31 for d in days):
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
        if stray_percent(binding[key]):
            raise ContractError("invalid_variable_reference", key)
        if not anchored_path(binding[key]):
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
    if any(stray_percent(arg) for arg in argv):
        raise ContractError("invalid_variable_reference", "argv")
    if not anchored_path(argv[0]):
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


# ---- compiler ----------------------------------------------------------------------------------
# Pure read-only planning against caller-supplied legacy and Scheduler snapshots.
#
# Generated files are declared-task projections, never replacement instructions.
# This module neither imports the web server nor launches a scheduler or payload.


def _plan_json(value) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _revision(value) -> str:
    return "sha256:" + hashlib.sha256(_plan_json(value).encode("utf-8")).hexdigest()


def validate_trigger_shape(trigger):
    """Only accept declaration fields that have defined rendering semantics."""
    _trigger(trigger)
    entries = trigger if isinstance(trigger, list) else [trigger]
    shapes = {'daily': {'type', 'at'}, 'weekly': {'type', 'at', 'days'}, 'monthly': {'type', 'at', 'days'},
              'interval': {'type', 'seconds', 'minutes'}, 'logon': {'type'},
              'xml': {'type', 'xml', 'owner', 'reason'}}
    for entry in entries:
        fields(entry, shapes[entry['type']], 'trigger')
        if entry['type'] in ('weekly', 'monthly') and len(set(entry['days'])) != len(entry['days']):
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
                parsed, reason = parse_names(before)
                if parsed is None:
                    parity[key] = {"status": "not-checked", "reason_code": "unreadable_allowlist", "reason": reason}
                    continue
                before, after = sorted(parsed), sorted(literal_names(after))
            elif key == "task_health":
                old = _rows(before, "tasks")
                if any(not isinstance(row.get("name"), str) or not row["name"] for row in old):
                    raise ContractError("missing_field", "name")
                before = sorted(_plan_json(_health_row(row)) for row in old)
                after = sorted(_plan_json(_health_row(row)) for row in after["tasks"])
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
    names = render_names([task["name"] for task in specs if task["backup"]])
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
        available.update(_plan_json(_health_row(row)) for row in _rows(baseline["task_health"], "tasks"))
    for row in rows:
        key = _plan_json(_health_row(row))
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


# ---- registration ------------------------------------------------------------------------------
# Explicit, journaled compensation for task registration.
#
# runtime.create_runtime supplies the durable adapters and the private declaration
# bundle; importing this module performs no I/O. The Scheduler and filesystem do not
# share an atomic commit. See docs/task-registration.md for adapter durability, lock
# and staging requirements.


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
    authority: object
    linked_work_items: Callable
    checkpoint: Callable = lambda point: None

    def finish(self, journal, status):
        self.authority.finish(self, journal, status)

    def prepare_inputs(self, journal, bundle, selected, outputs):
        self.authority.prepare_inputs(journal, bundle, selected, outputs)


def _use(runtime):
    if runtime is None:
        raise Conflict("runtime_adapter_required", "runtime")
    return runtime


def _validate_control(spec, snapshot):
    """A migrated task's live definition must still match its declaration."""
    pass
    previous = snapshot['value']['spec']
    if any(previous.get(key) != spec.get(key) for key in SCHEDULER_FIELDS if key != 'enabled'):
        raise Conflict('declaration_apply_required', 'scheduler')


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
    return plan(request)


def compile_effective(bundle):
    """TaskSpecs of the supplied effective request plus that request's revision. No I/O."""
    compiled = _compile(bundle)
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
        from .scheduler import same_candidate
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
    if "scheduler_before" not in journal:
        inputs = runtime.authority._inputs(journal.get("input_before"))
        ids = {key.removeprefix("task:") for key in journal["locks"] if key.startswith("task:")}
        bindings = inputs['request']['bindings']['tasks']
        if not ids or not ids <= bindings.keys():
            raise Conflict("scheduler_scope_unknown", "journal")
        names.update(bindings[task_id]['name'] for task_id in ids)
    names.update(s["key"] for s in journal["steps"] if s["kind"] == "scheduler")
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
        # Old journals recorded changed files only. Unchanged files still need
        # a receipt fingerprint; absence of a before-image alone proves nothing.
        receipt = runtime.authority.receipt()
        config = runtime.authority.config
        paths = list(config.paths.values()) + list(config.launchers.values())
        paths += [path for path in config.active_xml.values() if path]
        for path in paths:
            if path not in expected:
                current = _read(runtime.files, path)
                if fingerprint(current) != receipt['file_ownership'].get(path):
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
    extra_paths, launcher_paths = [], set()
    for task_id in ids:
        launcher = bundle.get('launchers', {}).get(task_id)
        if launcher is not None:
            if not isinstance(launcher, str) or launcher.casefold() in {p.casefold() for p in paths.values()}:
                raise Conflict('invalid_launcher_path', 'launcher')
            extra_paths.append(launcher)
            launcher_paths.add(launcher.casefold())
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
    owned = require(bundle, "file_ownership", dict)
    for path, snapshot in file_before.items():
        # A launcher newly listed in the runtime configuration has no receipt entry yet. Its
        # declared absence is the approval; the transaction records its first published image.
        if path not in owned and path.casefold() in launcher_paths and snapshot.get("state") == "absent":
            continue
        if owned.get(path) != fingerprint(snapshot):
            raise Conflict("file_ownership_changed", "files")
    old_tombstones = _document(file_before[paths["tombstones"]])
    if not isinstance(old_tombstones, dict):
        raise Conflict("invalid_tombstones", "tombstones")
    before, selected = {}, []
    for task_id in ids:
        spec = deepcopy(specs[task_id])
        from .scheduler import task_name
        task_name(spec["name"])
        proof = require(bundle, "ownership", dict).get(task_id)
        writer = "declarations" if task_id in migrated else "legacy"
        snapshot = _read(runtime.scheduler, spec["name"])
        if proof is None and (intent.get("replace_identity") is not None
                              or (intent["operation"] == "apply" and snapshot["state"] == "absent")):
            # Adopting an unmanaged task (the reviewed identity names the live definition) or
            # creating a declared task whose name the Scheduler verifiably does not hold: the
            # receipt gains its ownership proof when the transaction completes.
            proof = {"name": spec["name"], "task_path": "\\", "epoch": epoch, "writer": writer, "identity": None}
        if not isinstance(proof, dict) or any(proof.get(k) != v for k, v in {
            "name": spec["name"], "task_path": "\\", "epoch": epoch, "writer": writer}.items()):
            raise Conflict("ownership_required", "ownership")
        if writer == "legacy" and not intent["migrate"] and not intent.get('legacy_transaction') and intent['operation'] != 'restore':
            raise Conflict("legacy_authority", "task_id")
        if proof.get("identity") != snapshot.get("identity") or "identity" not in proof:
            # A definition edited outside the authority may be replaced by the declaration when
            # the operator names the exact drifted definition they reviewed. Its before-image is
            # kept like any other, so compensation restores it.
            if not ("identity" in proof and snapshot["state"] == "present"
                    and intent["operation"] == "apply" and len(ids) == 1
                    and intent.get("replace_identity") == snapshot.get("identity")):
                raise Conflict("scheduler_ownership_changed", "ownership")
        if snapshot["state"] == "present":
            current = snapshot["value"]
            if not isinstance(current, dict) or type(current.get("running")) is not bool or type(current.get("enabled")) is not bool:
                raise Conflict("incomplete_observation", "scheduler")
            if current["running"]:
                raise Conflict("busy", "scheduler")
            if writer != 'legacy' and intent['operation'] in ('enable', 'disable'):
                _validate_control(spec, snapshot)
            if spec["enabled"] and not current["enabled"] and not intent["approve_enable"] and intent["operation"] not in ("disable", "retire", "restore"):
                raise Conflict("explicit_enable_required", "enabled")
        if task_id in old_tombstones and intent["operation"] != "retire":
            raise Conflict("retired_task", "task_id")
        if intent["operation"] in ("retire", "disable", "enable"):
            spec["enabled"] = intent["operation"] == "enable"
        if intent.get('legacy_transaction') and snapshot['state'] == 'absent' and intent['operation'] != 'retire':
            raise Conflict('task_missing', 'scheduler')
        if intent['operation'] == 'restore':
            from .lifecycle import validate_restore_target
            spec['enabled'] = validate_restore_target(bundle, intent, spec, snapshot, scheduler=runtime.scheduler)
        _budget(spec)
        if snapshot['state'] == 'absent' and intent['operation'] == 'apply':
            pass
            if inspect(runtime, bundle, specs, task_id)['matches']:
                raise Conflict('equivalent_task_exists', task_id)
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
        from .integrations import report
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


def build_plan(runtime, task_ids, *, operation="apply", migrate=False, approve_enable=False, reason="",
               legacy_transaction=False, replace_identity=None):
    """Read-only explicit plan. The result contains fingerprints, never raw XML."""
    intent = {"task_ids": list(task_ids), "operation": operation, "migrate": migrate,
              "approve_enable": approve_enable, "reason": reason}
    if replace_identity is not None:
        if (operation != "apply" or not isinstance(replace_identity, str)
                or not re.fullmatch(r"task-definition:[a-f0-9]{64}", replace_identity) or not reason.strip()):
            raise Conflict("invalid_replace_identity", "intent")
        intent["replace_identity"] = replace_identity
    if legacy_transaction:
        if operation not in ('enable', 'disable', 'retire') or migrate:
            raise Conflict('invalid_legacy_transaction', 'intent')
        intent['legacy_transaction'] = True
    if type(migrate) is not bool or type(approve_enable) is not bool or not isinstance(reason, str):
        raise Conflict("invalid_intent", "intent")
    return _assemble(_use(runtime), intent)[0]


def creation_check(runtime, task_id):
    """Find an existing declared action before a skill proposes a new Scheduler identity."""
    pass
    runtime = _use(runtime)
    with runtime.locks.hold(['authority']):
        if runtime.journal.pending(['authority']):
            raise Conflict('pending_transaction', 'authority')
        bundle = deepcopy(runtime.load())
        _authority(bundle)
        specs = {spec['task_id']: spec for spec in _compile(bundle)['task_specs']}
        return inspect(runtime, bundle, specs, task_id)


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
    # A name that stays listed is left where it is: removing and re-appending it changed the file
    # on every apply, so an unchanged task never compared equal.
    listed = {spec["name"].casefold() for spec in selected
              if spec['task_id'] in projected_ids and spec["backup"] and not retiring}
    try:
        present = {name.casefold() for name in literal_names(text)}
        for name in literal_names(text):
            if name.casefold() in names and name.casefold() not in listed:
                text, _ = remove_name(text, name)
        for spec in selected:
            if spec["name"].casefold() in listed and spec["name"].casefold() not in present:
                close = closing_paren(text)
                text = text[:close] + "\n    '" + spec["name"].replace("'", "''") + "'\n" + text[close:]
        literal_names(text)
    except AllowlistError as exc:
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
    from .scheduler import launcher_bytes
    for spec in selected:
        launcher = bundle.get('launchers', {}).get(spec['task_id'])
        if launcher is not None and spec['task_id'] not in legacy_ids:
            result[launcher] = None if retiring else launcher_bytes(spec['argv'], spec['cwd'])
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
    journal["status"] = "conflict" if conflicts else 'recovering'
    _save(runtime, journal)
    # Keep staged evidence for conflict resolution; cleanup is resumable separately.
    if not conflicts:
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
        runtime.prepare_inputs(journal, bundle, selected, outputs)
        # Publish the journal only after it can prove every original resource.
        # A crash immediately after create must not leave recovery without the
        # before-images needed to verify the unchanged Scheduler definitions.
        journal["scheduler_before"] = {key: _secure(runtime, tx + ":scheduler:" + str(i), value)
                                       for i, (key, value) in enumerate(before.items())}
        journal["file_before"] = {key: _secure(runtime, tx + ":file:" + str(i), value)
                                  for i, (key, value) in enumerate(file_before.items())}
        runtime.journal.create(tx, journal)
        runtime.checkpoint("journal_created")
        try:
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


def _ordinary_recovery_shape(journal):
    require(journal, 'input_revision', str)
    if 'linked_work_items' not in journal:
        raise Conflict('missing_field', 'linked_work_items')
    # A failed or unconfigured linked reader is explicitly represented by None.
    if journal['linked_work_items'] is not None:
        require(journal, 'linked_work_items', dict)


def recover(transaction_id, *, runtime=None):
    runtime = _use(runtime)
    if runtime.vault is None:
        raise Conflict("secure_storage_required", "vault")
    journal = runtime.journal.load(transaction_id)
    versioned(journal, "journal")
    if journal["transaction_id"] != transaction_id:
        raise Conflict("journal_identity_mismatch", "journal")
    if 'reconcile' in journal and not isinstance(journal['reconcile'], dict):
        raise Conflict('journal_operation_unknown', 'journal')
    if isinstance(journal.get('reconcile'), dict):
        # Accepting external file edits requires the exact reviewed approval;
        # these current before-images must never enter ordinary compensation.
        from .lifecycle import _reconcile_result as reconciliation_result
        return reconciliation_result(journal)
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
    # A missing operation marker must not turn an acceptance journal into an
    # ordinary compensating transaction. These are required standard fields.
    _ordinary_recovery_shape(journal)
    with runtime.locks.hold(journal["locks"]):
        journal = runtime.journal.load(transaction_id)
        if 'reconcile' in journal or 'bootstrap' in journal:
            raise Conflict('journal_operation_changed', 'journal')
        _ordinary_recovery_shape(journal)
        if journal['status'] == 'committing':
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


def dispatch(name, verb, *, reason="", runtime=None):
    """Run/stop act on the Scheduler under the authority and task locks.

    Enable, disable and retire are registration transactions; a legacy-writer
    task goes through the same transaction with its bindings left as reviewed.
    """
    runtime = _use(runtime)
    from .scheduler import task_name
    task_name(name)
    if verb not in ('enable', 'disable', 'run', 'stop', 'retire'):
        raise Conflict('invalid_operation', 'operation')
    with runtime.locks.hold(["authority"]):
        if runtime.journal.pending(["authority"]):
            raise Conflict("recovery_required", "journal")
        bundle = runtime.load()
        epoch, migrated = _authority(bundle)
        matches = [s for s in _compile(bundle)["task_specs"] if s["name"].casefold() == name.casefold()]
        if not matches:
            raise Conflict('ownership_required', 'task_id')
        spec = matches[0]
        declared = spec['task_id'] in migrated
        proof = bundle.get('ownership', {}).get(spec['task_id'])
        if (not isinstance(proof, dict) or proof.get('name') != spec['name']
                or proof.get('task_path') != '\\' or proof.get('epoch') != epoch
                or proof.get('writer') != ('declarations' if declared else 'legacy')
                or 'identity' not in proof):
            raise Conflict('ownership_required', 'task_id')
        with runtime.locks.hold(['task:' + spec['task_id']]):
            snapshot = _read(runtime.scheduler, proof['name'])
            if snapshot.get('identity') != proof['identity']:
                raise Conflict('scheduler_ownership_changed', 'ownership')
            if verb in ('run', 'stop'):
                if declared:
                    if snapshot['state'] != 'present':
                        raise Conflict('task_missing', 'scheduler')
                    _validate_control(spec, snapshot)
                    if verb == 'run' and not spec['enabled']:
                        raise Conflict('task_disabled', 'scheduler')
                return runtime.scheduler.control(proof['name'], verb, snapshot)
    # apply acquires the full lock set and revalidates the declaration.
    plan = build_plan(runtime, [spec['task_id']], operation=verb, approve_enable=verb == 'enable',
                      reason=reason, legacy_transaction=not declared)
    result = apply(plan, plan['input_revision'], runtime=runtime)
    if verb in ('enable', 'disable') and result.get('ok'):
        value = snapshot.get('value', {})
        result['before'] = ('Running' if value.get('running') else
                            'Ready' if value.get('enabled') else 'Disabled') if snapshot['state'] == 'present' else '(gone)'
        result['after'] = 'Ready' if verb == 'enable' else 'Disabled'
    return result


# ---- creation ----------------------------------------------------------------------------------
# Read-only action reuse checks shared by skill callers and registration.


def _live_spec(value):
    """Legacy Scheduler observations carry argv only; recover scope from their XML."""
    spec = value.get('spec')
    if not isinstance(spec, dict):
        raise ContractError('creation_action_unknown', 'scheduler')
    if spec.get('principal'):
        return spec
    from .scheduler import NS, parse
    root = parse(value['xml'])
    ns = {'t': NS}
    principals = root.findall('t:Principals/t:Principal', ns)
    actions = root.findall('t:Actions/t:Exec', ns)
    if len(principals) != 1 or len(actions) != 1:
        raise ContractError('creation_action_unknown', 'scheduler')
    principal = {field: principals[0].findtext('t:' + tag, namespaces=ns)
                 for field, tag in (('user_id', 'UserId'), ('logon_type', 'LogonType'),
                                    ('run_level', 'RunLevel'))}
    if principals[0].find('t:RunLevel', ns) is None:
        principal['run_level'] = 'LeastPrivilege'  # Task Scheduler schema default
    if not all(principal.values()):
        raise ContractError('creation_action_unknown', 'principal')
    return {**spec, 'principal': principal,
            'cwd': actions[0].findtext('t:WorkingDirectory', namespaces=ns)}


def _action(spec):
    argv, principal = spec.get('argv'), spec.get('principal')
    if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
        raise ContractError('creation_action_unknown', 'argv')
    if not isinstance(principal, dict) or not principal:
        raise ContractError('creation_action_unknown', 'principal')
    cwd = spec.get('cwd')
    if cwd is not None and not isinstance(cwd, str):
        raise ContractError('creation_action_unknown', 'cwd')
    path = lambda value: value.replace('\\', '/').rstrip('/').casefold()
    return {'argv': [path(argv[0]), *argv[1:]], 'cwd': path(cwd or ''), 'principal': principal}


def inspect(runtime, bundle, specs, task_id):
    pass
    if task_id not in specs:
        raise Conflict('invalid_task_selection', 'task_id')
    proposed = specs[task_id]
    target = _action(proposed)
    matches, observed = [], {}
    current = None
    for other_id, spec in specs.items():
        snapshot = _read(runtime.scheduler, spec['name'])
        observed[spec['name']] = fingerprint(_scheduler_config(snapshot))
        if other_id == task_id:
            current = snapshot
            continue
        existing = snapshot.get('value')
        if existing is not None:
            equivalent = _action(_live_spec(existing)) == target
        else:
            equivalent = _action(spec) == target
        if equivalent:
            matches.append({'task_id': other_id, 'name': spec['name'],
                            'state': snapshot['state'], 'enabled': existing.get('enabled') if existing else False,
                            'running': existing.get('running') if existing else False})
    return {'schemaVersion': 1, 'task_id': task_id,
            'decision': 'reuse' if matches else 'update' if current['state'] == 'present' else 'create',
            'matches': matches, 'checked_tasks': len(specs), 'coverage': 'declared_tasks',
            'inventory_revision': revision(observed), 'input_revision': revision(bundle)}
