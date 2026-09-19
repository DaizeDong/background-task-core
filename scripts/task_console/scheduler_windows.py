"""Pinned Windows Scheduler transport contract, with no implicit live runner.

The injected transport receives an operation and a JSON object, never a command
string. It must bind TaskName literally and implement the CAS/ownership check in
the same locked operation. Missing, truncated, or failed query replies are errors.
"""
from copy import deepcopy
import re
import xml.etree.ElementTree as ET

from .registration import Conflict, fingerprint, _idle, _same, _scheduler_config, _snapshot


def task_name(name):
    if not isinstance(name, str) or not name.strip() or re.search(r'[\\/\x00-\x1f*?\[\]]', name):
        raise Conflict("invalid_task_name", "TaskName")
    return name


def validate_value(value):
    if not isinstance(value, dict) or type(value.get("enabled")) is not bool or type(value.get("running")) is not bool:
        raise Conflict("incomplete_observation", "scheduler")
    xml = value.get("xml")
    if not isinstance(xml, str) or re.search(r"<!\s*(DOCTYPE|ENTITY)", xml, re.I):
        raise Conflict("invalid_xml", "scheduler")
    try:
        ET.fromstring(xml)
    except ET.ParseError as exc:
        raise Conflict("invalid_xml", "scheduler") from exc
    spec = value.get("spec")
    if not isinstance(spec, dict) or not isinstance(spec.get("argv"), list) or not spec["argv"]:
        raise Conflict("argv_array_required", "scheduler")
    if any(not isinstance(arg, str) or "\x00" in arg for arg in spec["argv"]):
        raise Conflict("invalid_argv", "scheduler")
    return value


class WindowsScheduler:
    """Literal root registration and bounded explicit control transport.

    prepare must use the existing installation/launcher implementation in an
    isolated staging directory. It must not register a task or execute a payload.
    publish must create new tasks with no-replace semantics, preserve all XML
    settings, and refuse a running target. All replies must be fully normalized.
    """

    def __init__(self, transport):
        if not callable(transport):
            raise Conflict("scheduler_transport_required", "transport")
        self.transport = transport

    def _call(self, operation, name=None, **payload):
        request = {"schemaVersion": 1, "TaskPath": "\\", **payload}
        if name is not None:
            request["TaskName"] = task_name(name)
        try:
            result = self.transport(operation, deepcopy(request))
        except Conflict:
            raise
        except Exception as exc:
            raise Conflict("scheduler_transport_failed", "scheduler") from exc
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise Conflict("scheduler_transport_failed", "scheduler")
        return result

    def read(self, key):
        result = self._call("query", key)
        # Absence must be explicitly established by a successful scoped query.
        snapshot = _snapshot(result.get("snapshot"))
        if snapshot["state"] == "present":
            validate_value(snapshot["value"])
            if getattr(self.transport, 'native_normalization', False):
                # Reuse COM's typed parser/defaults. This never opens or writes
                # another task, and verifies that normalization loses no input.
                prepared = self.prepare(key, snapshot['value'], 'readback')
                snapshot['candidate_identity'] = prepared['candidate_identity']
                snapshot.pop('prepared', None)
        return deepcopy(snapshot)

    def prepare(self, key, value, token):
        if value is not None and getattr(self.transport, 'concrete', False):
            from .runtime_xml import normalize
            value = normalize(value)
        if value is not None:
            validate_value(value)
        result = self._call("prepare", key, value=value, token=token)
        snapshot = _snapshot(result.get("snapshot"))
        if snapshot["state"] == "present":
            validate_value(snapshot["value"])
            observed, desired = deepcopy(snapshot['value']), deepcopy(value)
            if getattr(self.transport, 'concrete', False):
                from .runtime_xml import preserves_xml
                if not preserves_xml(desired.pop('xml'), observed.pop('xml')):
                    raise Conflict('preparation_changed_settings', 'scheduler')
            if observed != desired:
                raise Conflict("preparation_changed_settings", "scheduler")
            if getattr(self.transport, 'native_normalization', False):
                snapshot['candidate_identity'] = snapshot['identity']
                snapshot['prepared'] = True
        elif value is not None:
            raise Conflict("preparation_missing", "scheduler")
        return deepcopy(snapshot)

    def publish(self, key, expected, desired):
        from .runtime_xml import same_candidate
        current = self.read(key)
        _idle(current)
        if not (_same("scheduler", current, expected) or same_candidate(current, expected)):
            raise Conflict("scheduler_changed", "scheduler")
        # Only a proven prepared predecessor can substitute its observed bytes.
        # Ordinary before-images still cross COM byte-for-byte, rejecting edits.
        compare = current if expected.get('prepared') is True else expected
        self._call("publish", key, expected=compare, desired=desired,
                   expected_fingerprint=fingerprint(_scheduler_config(compare)),
                   create_no_replace=expected["state"] == "absent")
        current = self.read(key)
        _idle(current)
        if not (_same("scheduler", current, desired) or same_candidate(current, desired)):
            raise Conflict("scheduler_readback_mismatch", "scheduler")
        return deepcopy(current)

    def cleanup(self, token):
        self._call("cleanup", token=token)

    def control(self, key, verb, expected):
        """Caller holds authority/task locks. Acknowledgement is not payload success."""
        if verb not in ('enable', 'disable', 'run', 'stop'):
            raise Conflict('invalid_operation', 'operation')
        current = self.read(key)
        if not _same('scheduler', current, expected):
            raise Conflict('scheduler_changed', 'scheduler')
        if current['state'] != 'present':
            raise Conflict('task_missing', 'scheduler')
        value = current['value']
        before = 'Running' if value['running'] else ('Ready' if value['enabled'] else 'Disabled')
        result = {'ok': True, 'before': before, 'after': before,
                  'acknowledged': False, 'payload_success': None}
        if verb == 'stop':
            result['payload_cleanup'] = 'unverified'
        if verb == 'run':
            if not value['enabled']:
                raise Conflict('task_disabled', 'scheduler')
            if value['running']:
                return {**result, 'status': 'already_running', 'message': 'Task already running; no new run requested'}
        if verb == 'stop' and not value['running']:
            return {**result, 'status': 'scheduler_idle', 'message': 'Scheduler is idle; linked work is not cancelled'}
        try:
            reply = self._call(verb, key, expected=current)
            if type(reply.get('running')) is not bool or reply.get('acknowledged') is not True:
                raise Conflict('incomplete_control_reply', 'scheduler')
        except Conflict as exc:
            # The transport may have performed the operation before losing its
            # reply. Never retry an uncertain run or claim a stopped payload.
            return {**result, 'ok': False, 'status': 'cleanup_uncertain',
                    'acknowledged': None, 'message': 'Control outcome uncertain; explicit observation required',
                    'failure': exc.code, 'after': None}
        after = 'Running' if reply['running'] else ('Disabled' if verb == 'disable' or not value['enabled'] and verb != 'enable' else 'Ready')
        result.update(acknowledged=True, after=after)
        if verb == 'run':
            return {**result, 'status': 'run_requested', 'message': 'Scheduler accepted run; payload outcome unknown'}
        if verb == 'stop':
            return {**result, 'ok': not reply['running'],
                    'status': 'cleanup_uncertain' if reply['running'] else 'scheduler_idle',
                    'message': 'Stop requested; cleanup uncertain' if reply['running'] else 'Scheduler idle; payload cleanup and linked work unverified'}
        if reply.get('enabled') is not (verb == 'enable'):
            return {**result, 'ok': False, 'status': 'cleanup_uncertain', 'message': 'Enabled state readback failed'}
        return {**result, 'status': 'applied', 'message': verb + ' applied'}
