"""Windows Task Scheduler adapters: task-name rules, the scheduler adapter, task XML rendering and
normalisation, and the COM/DPAPI transports.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from collections import OrderedDict
from copy import deepcopy
from decimal import Decimal, localcontext

from .registry import Conflict, fingerprint, _idle, _same, _scheduler_config, _snapshot, VARIABLE_REFERENCE, stray_percent

# ---- scheduler_windows -------------------------------------------------------------------------
# Pinned Windows Scheduler transport contract, with no implicit live runner.
#
# The injected transport receives an operation and a JSON object, never a command
# string. It must bind TaskName literally and implement the CAS/ownership check in
# the same locked operation. Missing, truncated, or failed query replies are errors.


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
            # Reuse COM's typed parser/defaults. This never opens or writes
            # another task, and verifies that normalization loses no input.
            prepared = self.prepare(key, snapshot['value'], 'readback')
            snapshot['candidate_identity'] = prepared['candidate_identity']
        return deepcopy(snapshot)

    def prepare(self, key, value, token):
        pass
        if value is not None:
            value = validate_value(normalize(value))
        result = self._call("prepare", key, value=value, token=token)
        snapshot = _snapshot(result.get("snapshot"))
        if snapshot["state"] == "present":
            validate_value(snapshot["value"])
            observed, desired = deepcopy(snapshot['value']), deepcopy(value)
            if not preserves_xml(desired.pop('xml'), observed.pop('xml')) or observed != desired:
                raise Conflict("preparation_changed_settings", "scheduler")
            snapshot['candidate_identity'] = snapshot['identity']
            snapshot['prepared'] = True
        elif value is not None:
            raise Conflict("preparation_missing", "scheduler")
        return deepcopy(snapshot)

    def publish(self, key, expected, desired):
        pass
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
        if verb not in ('run', 'stop'):
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
        after = 'Running' if reply['running'] else ('Ready' if value['enabled'] else 'Disabled')
        result.update(acknowledged=True, after=after)
        if verb == 'run':
            return {**result, 'status': 'run_requested', 'message': 'Scheduler accepted run; payload outcome unknown'}
        return {**result, 'ok': not reply['running'],
                'status': 'cleanup_uncertain' if reply['running'] else 'scheduler_idle',
                'message': 'Stop requested; cleanup uncertain' if reply['running'] else 'Scheduler idle; payload cleanup and linked work unverified'}


# ---- runtime_xml -------------------------------------------------------------------------------
# Pure Scheduler XML and hidden launcher rendering; never writes or executes.


NS = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
ET.register_namespace('', NS)
MARKER = 'background-task-core-v1:'
_UNORDERED_FIELDS = {'{' + NS + '}' + name for name in (
    'Task', 'RegistrationInfo', 'Principal', 'Settings', 'IdleSettings',
    'RestartOnFailure', 'NetworkSettings', 'Exec', 'Repetition',
    'TimeTrigger', 'CalendarTrigger', 'LogonTrigger', 'BootTrigger',
    'RegistrationTrigger', 'EventTrigger', 'SessionStateChangeTrigger')}
_ELEMENT_ONLY = _UNORDERED_FIELDS | {'{' + NS + '}' + name for name in (
    'Actions', 'Triggers', 'Principals', 'ScheduleByDay', 'ScheduleByWeek',
    'ScheduleByMonth', 'ScheduleByMonthDayOfWeek', 'DaysOfWeek', 'DaysOfMonth',
    'Months', 'Weeks', 'RequiredPrivileges', 'ValueQueries')}
_SCHEDULE_FIELDS = {
    '{' + NS + '}' + parent: {'{' + NS + '}' + child for child in children}
    for parent, children in (
        ('ScheduleByWeek', ('WeeksInterval', 'DaysOfWeek')),
        ('ScheduleByMonth', ('Months', 'DaysOfMonth')),
    )
}


class _TaskTreeBuilder(ET.TreeBuilder):
    def __init__(self):
        super().__init__(insert_comments=True, insert_pis=True)
        self.depth = 0

    def start(self, tag, attrs):
        self.depth += 1
        return super().start(tag, attrs)

    def end(self, tag):
        item = super().end(tag)
        self.depth -= 1
        return item

    def comment(self, text):
        if not self.depth:
            raise Conflict('outside_task_metadata_requires_preservation', 'scheduler')
        return super().comment(text)

    def pi(self, target, text=None):
        if not self.depth:
            raise Conflict('outside_task_metadata_requires_preservation', 'scheduler')
        return super().pi(target, text)


def parse(xml):
    if not isinstance(xml, str) or len(xml) > 4 * 1024 * 1024 or re.search(r'<!\s*(DOCTYPE|ENTITY)', xml, re.I):
        raise Conflict('invalid_xml', 'scheduler')
    try:
        root = ET.fromstring(xml, parser=ET.XMLParser(target=_TaskTreeBuilder()))
    except ET.ParseError:
        raise Conflict('invalid_xml', 'scheduler') from None
    if root.tag != '{' + NS + '}Task':
        raise Conflict('scheduler_xml_namespace_required', 'scheduler')
    return root


def node(parent, name):
    child = parent.find('{' + NS + '}' + name)
    return child if child is not None else ET.SubElement(parent, '{' + NS + '}' + name)


def text(parent, name, value):
    node(parent, name).text = str(value).lower() if isinstance(value, bool) else str(value)


def _representation(root):
    """Only fixed-length duration spellings and unambiguous action context.

    Defaults and schema ordering belong to ITaskDefinition.XmlText. Months and
    years have no fixed duration and are deliberately not equated with days.
    Unknown nodes, attributes, mixed content and collection order are retained.
    """
    ns = {'t': NS}
    paths = ['t:Settings/t:ExecutionTimeLimit', 't:Settings/t:DeleteExpiredTaskAfter',
             't:Settings/t:RestartOnFailure/t:Interval',
             't:Settings/t:IdleSettings/t:Duration', 't:Settings/t:IdleSettings/t:WaitTimeout']
    for field in ('ExecutionTimeLimit', 'Delay', 'RandomDelay', 'Repetition/t:Interval', 'Repetition/t:Duration'):
        paths.append('t:Triggers/*/t:' + field)
    pattern = r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?'
    for path in paths:
        for item in root.findall(path, ns):
            match = re.fullmatch(pattern, item.text or '')
            if match and any(v is not None for v in match.groups()):
                # Set precision from the input, never round a long deadline.
                with localcontext() as context:
                    context.prec = max(28, len(item.text) + 12)
                    seconds = sum(Decimal(v or '0') * factor for v, factor in zip(match.groups(), (24 * 60 * 60, 60 * 60, 60, 1)))
                    item.text = 'PT' + format(seconds.normalize(), 'f') + 'S'
    principals = root.findall('t:Principals/t:Principal', ns)
    actions = root.find('t:Actions', ns)
    if actions is not None and 'Context' not in actions.attrib and len(principals) == 1 and principals[0].get('id'):
        actions.set('Context', principals[0].get('id'))
    return root


def canonical(xml):
    root = _representation(parse(xml))
    for parent in root.iter():
        # Only known element-only content has ignorable indentation. Whitespace
        # in unknown/mixed XML may carry meaning and remains part of identity.
        if parent.tag in _ELEMENT_ONLY:
            if parent.text is not None and not parent.text.strip():
                parent.text = None
            for child in parent:
                if child.tail is not None and not child.tail.strip():
                    child.tail = None
    return ET.canonicalize(ET.tostring(root, encoding='unicode'), with_comments=True)


def preserves_xml(expected, actual):
    """COM may materialize defaults/reorder known schema fields, never lose input.

    Action, trigger and principal collections retain their length and order.
    Every explicitly supplied attribute and leaf value must survive exactly.
    """
    def contains(before, after):
        if before.tag != after.tag or any(after.get(k) != v for k, v in before.attrib.items()):
            return False
        if (before.tail or '').strip() and before.tail != after.tail:
            return False
        # An empty schema container is not an explicit empty scalar. Continue
        # through its field/collection rules even when COM adds indentation.
        if not len(before) and before.tag not in _ELEMENT_ONLY:
            return not len(after) and (before.text or '') == (after.text or '')
        if (before.text or '').strip() and before.text != after.text:
            return False
        if not (before.text or '').strip() and (after.text or '').strip():
            return False
        if before.tag not in _ELEMENT_ONLY:
            if before.text != after.text or [c.tail for c in before] != [c.tail for c in after]:
                return False
        # COM reorders these schedule fields, not their ordered day/month
        # members. Unknown children or metadata keep strict sequence matching.
        fields = _SCHEDULE_FIELDS.get(before.tag, set())
        schedule_fields = (bool(fields) and len(before) == len(after)
                           and not (before.text or '').strip()
                           and all(c.tag in fields and not (c.tail or '').strip()
                                   for c in (*before, *after)))
        if before.tag not in _UNORDERED_FIELDS and not schedule_fields:
            return len(before) == len(after) and all(contains(a, b) for a, b in zip(before, after))
        used = set()
        for child in before:
            match = next((i for i, candidate in enumerate(after)
                          if i not in used and contains(child, candidate)), None)
            if match is None:
                return False
            used.add(match)
        return True
    return contains(_representation(parse(expected)), _representation(parse(actual)))


def same_candidate(current, expected):
    """A durable prepared candidate may match native representation changes.

    Observed-to-observed comparisons stay exact. The candidate identity is made
    by COM preparation before publication and independently by readback; it is
    never adopted from an arbitrary post-write object. Raw XML is kept for CAS.
    """
    if not (current.get('prepared') is True or expected.get('prepared') is True):
        return False
    if current.get('state') != 'present' or expected.get('state') != 'present':
        return False
    candidate = expected.get('candidate_identity')
    if not isinstance(candidate, str) or not re.fullmatch(r'task-definition:[a-f0-9]{64}', candidate):
        return False
    if current.get('candidate_identity') != candidate:
        return False
    def rest(snapshot):
        value = deepcopy(snapshot)
        for field in ('prepared', 'candidate_identity', 'identity'):
            value.pop(field, None)
        value['value'].pop('xml', None)
        value['value'].pop('running', None)
        return value
    return rest(current) == rest(expected)


def _split_command(command):
    """Use Windows' maintained parser; never approximate legacy argument syntax."""
    pass
    return split_arguments(command)


def embedded(root):
    data = root.find('{' + NS + '}Data')
    if data is not None and (data.text or '').startswith(MARKER):
        try:
            return json.loads(base64.b64decode(data.text[len(MARKER):], validate=True))
        except Exception:
            raise Conflict('invalid_embedded_spec', 'scheduler') from None
    return {'original_data': data.text if data is not None else None}


def normalize(value):
    value = deepcopy(value)
    root = parse(value['xml'])
    flag = value['enabled']
    if type(flag) is not bool:
        raise Conflict('invalid_enabled', 'scheduler')
    text(node(root, 'Settings'), 'Enabled', flag)
    payload = embedded(root)
    spec = deepcopy(value.get('spec', payload.get('spec')))
    if spec is None:
        actions = root.findall('./{' + NS + '}Actions/{' + NS + '}Exec')
        if not actions:
            raise Conflict('exec_action_required', 'scheduler')
        action = actions[0]
        spec = {'argv': [action.findtext('{' + NS + '}Command')] +
                _split_command(action.findtext('{' + NS + '}Arguments') or '')}
    spec['enabled'] = flag
    value['spec'] = spec
    # Storage may reorder dictionaries while retaining the exact XML. Keep
    # opaque Data bytes when the normalized spec has the same JSON value;
    # JSON comparison also distinguishes booleans and numeric representations.
    if ('spec' in payload and
            json.dumps(payload['spec'], sort_keys=True, ensure_ascii=True) !=
            json.dumps(spec, sort_keys=True, ensure_ascii=True)):
        payload['spec'] = spec
        node(root, 'Data').text = MARKER + base64.b64encode(json.dumps(payload, ensure_ascii=True).encode()).decode()
    value['xml'] = ET.tostring(root, encoding='unicode')
    return {k: value[k] for k in ('xml', 'spec', 'enabled', 'running')}


def observation(name, value):
    # Actual query XML is preserved byte-for-byte for the COM conditional check.
    normalized = normalize(value)
    normalized['xml'] = value['xml']
    root = parse(value['xml'])
    flags = root.findall('./{' + NS + '}Settings/{' + NS + '}Enabled')
    # XSD boolean collapses XML whitespace; absence defaults to true, empty does not.
    declared = 'true' if not flags else (flags[0].text or '').strip(' \t\r\n')
    if (len(flags) > 1 or declared not in ('true', 'false', '1', '0')
            or (declared in ('true', '1')) != value['enabled']):
        raise Conflict('enabled_readback_mismatch', 'scheduler')
    payload = embedded(root)
    if 'spec' in payload and payload['spec'].get('enabled') != value['enabled']:
        raise Conflict('enabled_readback_mismatch', 'scheduler')
    identity = hashlib.sha256(('\\' + name.casefold() + '\n' + canonical(value['xml'])).encode()).hexdigest()
    return {'state': 'present', 'identity': 'task-definition:' + identity, 'value': normalized}


def _command_line(argv):
    # list2cmdline, except that an argument holding a %NAME% reference is quoted whole: its
    # expanded value may contain spaces that the literal reference does not show.
    return ' '.join('"' + a + '"' if VARIABLE_REFERENCE.search(a) and '"' not in a and not a.endswith('\\')
                    else subprocess.list2cmdline([a]) for a in argv)


def launcher_bytes(argv, cwd):
    # WScript.Shell.Run expands %NAME% in its command line, so a well-formed reference is how a
    # declaration names a path through an environment variable. Any other '%' would be expanded
    # by accident and refuses, as do newline and NUL.
    if (not argv or any(not isinstance(a, str) or any(c in a for c in '\r\n\x00') or stray_percent(a) for a in argv)
            or any(c in cwd for c in '\r\n\x00') or stray_percent(cwd)):
        raise Conflict('invalid_launcher_arguments', 'launcher')
    # Same Run(command, 0, True) and exit propagation as hide-task-windows.ps1.
    # The renderer also preserves cwd, which a Scheduler Exec normally supplies.
    command = _command_line(argv).replace('"', '""')
    workdir = '"' + cwd.replace('"', '""') + '"'
    if VARIABLE_REFERENCE.search(cwd):
        # CurrentDirectory takes its string as given; only Run expands references by itself.
        workdir = 'shell.ExpandEnvironmentStrings(' + workdir + ')'
    chdir = f'shell.CurrentDirectory = {workdir}\r\n'
    run = f'WScript.Quit shell.Run("{command}", 0, True)\r\n'
    # A reference can expand to a folder or file that does not exist. The statement then raises a
    # runtime error, which wscript //B swallows and exits 0, so a statement that expands one turns
    # its error into exit 1 itself. A launcher without references renders as before.
    if VARIABLE_REFERENCE.search(cwd):
        chdir = 'On Error Resume Next\r\n' + chdir + 'If Err.Number <> 0 Then WScript.Quit 1\r\nOn Error GoTo 0\r\n'
    if any(VARIABLE_REFERENCE.search(a) for a in argv):
        run = ('On Error Resume Next\r\n'
               f'rc = shell.Run("{command}", 0, True)\r\n'
               'If Err.Number <> 0 Then WScript.Quit 1\r\n'
               'On Error GoTo 0\r\n'
               'WScript.Quit rc\r\n')
    source = ('\' Generated by background-task-core; transaction-owned.\r\n'
              'Set shell = CreateObject("WScript.Shell")\r\n'
              f'{chdir}'
              f'{run}')
    return b'\xff\xfe' + source.encode('utf-16-le')


def render(spec, old, enabled, launcher=None):
    from .registry import validate_trigger_shape
    validate_trigger_shape(spec['trigger'])
    spec = deepcopy(spec)
    spec['enabled'] = enabled
    passthrough = spec.get('xml_passthrough')
    if passthrough:
        xml = passthrough.get('xml')
        if not xml:
            raise Conflict('xml_passthrough_bytes_required', 'scheduler')
        root = parse(xml)
    elif old['state'] == 'present':
        root = parse(old['value']['xml'])
        # A live definition without an embedded trigger keeps its schedule unless the declaration
        # states one explicitly; only an xml trigger would have to be inferred from the old XML.
        declared = spec['trigger'] if isinstance(spec['trigger'], list) else [spec['trigger']]
        if 'trigger' not in old['value']['spec'] and any(t.get('type') == 'xml' for t in declared):
            raise Conflict('adoption_requires_xml_passthrough', 'scheduler')
    else:
        root = ET.Element('{' + NS + '}Task', {'version': '1.4'})
        # New definitions explicitly request the engine used by Win10/11 for
        # these v1.4 tasks. Existing/passthrough XML never inherits this choice.
        text(node(root, 'Settings'), 'UseUnifiedSchedulingEngine', True)
    payload = embedded(root)
    payload['spec'] = spec
    node(root, 'Data').text = MARKER + base64.b64encode(json.dumps(payload, ensure_ascii=True).encode()).decode()
    text(node(root, 'RegistrationInfo'), 'URI', '\\' + spec['name'])
    principals = node(root, 'Principals')
    principal = node(principals, 'Principal')
    principal.set('id', principal.get('id', 'Author'))
    for field, tag in (('user_id', 'UserId'), ('logon_type', 'LogonType'), ('run_level', 'RunLevel')):
        if field not in spec['principal']:
            raise Conflict('principal_field_required', 'scheduler')
        text(principal, tag, spec['principal'][field])
    if spec['principal']['logon_type'] in ('Password', 'InteractiveTokenOrPassword'):
        raise Conflict('auth_pending', 'credential_ref')
    if (spec['principal']['logon_type'] not in ('InteractiveToken', 'S4U', 'ServiceAccount')
            or spec['principal']['run_level'] not in ('LeastPrivilege', 'HighestAvailable')
            or set(spec['principal']) - {'user_id', 'logon_type', 'run_level'}):
        raise Conflict('principal_requires_explicit_adapter', 'scheduler')
    settings = node(root, 'Settings')
    for field, tag in (('disallow_start_on_batteries', 'DisallowStartIfOnBatteries'),
                       ('stop_on_batteries', 'StopIfGoingOnBatteries'), ('wake_to_run', 'WakeToRun')):
        if field not in spec['power']:
            raise Conflict('power_field_required', 'scheduler')
        if type(spec['power'][field]) is not bool:
            raise Conflict('invalid_power_boolean', 'scheduler')
        text(settings, tag, spec['power'][field])
    text(settings, 'Enabled', enabled)
    text(settings, 'ExecutionTimeLimit', 'PT' + str(spec['timeout_seconds']) + 'S')
    text(settings, 'MultipleInstancesPolicy', spec['concurrency']['policy'])
    # Preserve complete triggers when unchanged or explicitly supplied as XML.
    previous = old.get('value', {}).get('spec', {})
    if not passthrough and (old['state'] == 'absent' or any(previous.get(k) != spec[k] for k in ('trigger', 'timezone'))):
        # 'local' writes boundaries without an offset, which the Scheduler runs at machine-local
        # wall-clock time across DST; any other zone needs reviewed XML passthrough.
        if spec['timezone'] not in ('UTC', 'local'):
            raise Conflict('timezone_requires_xml_passthrough', 'scheduler')
        zone = 'Z' if spec['timezone'] == 'UTC' else ''
        triggers = node(root, 'Triggers')
        triggers.clear()
        entries = spec['trigger'] if isinstance(spec['trigger'], list) else [spec['trigger']]
        for trigger in entries:
            kind = trigger.get('type')
            if kind in ('daily', 'weekly', 'monthly'):
                item = ET.SubElement(triggers, '{' + NS + '}CalendarTrigger')
                at = trigger['at'] if len(trigger['at']) == 8 else trigger['at'] + ':00'
                text(item, 'StartBoundary', '2000-01-01T' + at + zone)
                text(item, 'Enabled', True)
                if kind == 'daily':
                    text(node(item, 'ScheduleByDay'), 'DaysInterval', 1)
                elif kind == 'monthly':
                    schedule = node(item, 'ScheduleByMonth')
                    days = node(schedule, 'DaysOfMonth')
                    for day in trigger['days']:
                        ET.SubElement(days, '{' + NS + '}Day').text = str(day)
                    months = node(schedule, 'Months')
                    for month in ('January', 'February', 'March', 'April', 'May', 'June', 'July',
                                  'August', 'September', 'October', 'November', 'December'):
                        node(months, month)
                else:
                    schedule = node(item, 'ScheduleByWeek')
                    text(schedule, 'WeeksInterval', 1)
                    days = node(schedule, 'DaysOfWeek')
                    names = dict(zip(('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'),
                                     ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')))
                    for day in trigger['days']:
                        node(days, names[day])
            elif kind == 'interval':
                item = ET.SubElement(triggers, '{' + NS + '}TimeTrigger')
                unit = 'seconds' if 'seconds' in trigger else 'minutes'
                text(node(item, 'Repetition'), 'Interval', 'PT' + str(trigger[unit]) + ('S' if unit == 'seconds' else 'M'))
                text(item, 'StartBoundary', '2000-01-01T00:00:00' + zone)
                text(item, 'Enabled', True)
            elif kind == 'logon':
                item = ET.SubElement(triggers, '{' + NS + '}LogonTrigger')
                text(item, 'Enabled', True)
                text(item, 'UserId', spec['principal']['user_id'])
            else:
                raise Conflict('trigger_requires_xml_passthrough', 'scheduler')
    actions = node(root, 'Actions')
    # The Scheduler expands the launcher's %NAME% reference when the task starts. If it names
    # nothing, wscript without //B opens a modal "cannot find script file" box and the run hangs
    # until its time limit; with //B the same failure exits 1 at once.
    argv = spec['argv'] if launcher is None else ['wscript.exe', '//B', '//Nologo', launcher]
    executable_actions = [item for item in actions if isinstance(item.tag, str)]
    if executable_actions and executable_actions[0].tag != '{' + NS + '}Exec':
        raise Conflict('exec_action_required', 'scheduler')
    if len(executable_actions) > 1:
        # Keep the complete reviewed action list if its primary Exec is unchanged.
        # A generated launcher or an implicit primary-action edit is ambiguous.
        action = executable_actions[0]
        existing = [action.findtext('{' + NS + '}Command')] + _split_command(action.findtext('{' + NS + '}Arguments') or '')
        if (action.tag != '{' + NS + '}Exec' or existing != argv
                or action.findtext('{' + NS + '}WorkingDirectory') != spec['cwd']):
            raise Conflict('multiple_actions_require_preservation', 'scheduler')
        return {'xml': ET.tostring(root, encoding='unicode'), 'spec': spec, 'enabled': enabled, 'running': False}
    action = node(actions, 'Exec')
    text(action, 'Command', argv[0])
    text(action, 'Arguments', _command_line(argv[1:]))
    text(action, 'WorkingDirectory', spec['cwd'])
    return {'xml': ET.tostring(root, encoding='unicode'), 'spec': spec, 'enabled': enabled, 'running': False}


# ---- runtime_windows ---------------------------------------------------------------------------
# Fixed, bounded COM/SecureString transport. Only explicit run requests start tasks.


LIMIT = 8 * 1024 * 1024


def split_arguments(command):
    """Decode Exec Arguments via CommandLineToArgvW without executing them."""
    if not isinstance(command, str) or '\x00' in command or len(command) > LIMIT:
        raise Conflict('invalid_argv_quoting', 'scheduler')
    if os.name != 'nt':
        raise Conflict('native_argv_parser_required', 'scheduler')
    import ctypes
    from ctypes import wintypes
    native = ctypes.WinDLL('shell32', use_last_error=True).CommandLineToArgvW
    native.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    native.restype = ctypes.POINTER(wintypes.LPWSTR)
    free = ctypes.WinDLL('kernel32', use_last_error=True).LocalFree
    free.argtypes = [wintypes.HLOCAL]
    free.restype = wintypes.HLOCAL
    count = ctypes.c_int()
    # argv[0] has special parsing rules; supply a fixed executable placeholder.
    values = native('background-task-core.exe ' + command, ctypes.byref(count))
    if not values:
        raise Conflict('native_argv_parser_failed', 'scheduler')
    try:
        return [values[i] for i in range(1, count.value)]
    finally:
        free(ctypes.cast(values, wintypes.HLOCAL))


def powershell(script, request, *, timeout=25):
    """Only bundled source is code. All caller values cross stdin as JSON data."""
    if os.name != 'nt':
        raise Conflict('windows_required', 'transport')
    from . import runs as winps
    executable = winps.powershell()
    command = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    data = json.dumps(request, ensure_ascii=True, allow_nan=False).encode('ascii')
    if len(data) > LIMIT:
        raise Conflict('request_limit', 'transport')
    try:
        from llmcall import process as shared_process
        captured = shared_process.run(
            [str(executable), '-NoProfile', '-NonInteractive', '-InputFormat', 'Text',
             '-OutputFormat', 'Text', '-EncodedCommand', command],
            data, timeout, context=shared_process.resolve_context(),
            max_input_bytes=LIMIT, max_output_bytes=LIMIT,
        )
        if captured.error or captured.outcome != 'success':
            code = {
                'input_limit': 'request_limit', 'invalid_timeout': 'transport_timeout',
                'timeout': 'transport_timeout', 'cancelled': 'transport_cancelled',
                'cleanup_failed': 'transport_cleanup_failed',
            }.get(captured.error, 'bounded_transport_failed')
            raise Conflict(code, 'transport')
        if (captured.returncode != 0 or captured.stderr_bytes != b''
                or captured.stdout_bytes is None):
            raise Conflict('bounded_transport_failed', 'transport')
        result = json.loads(captured.stdout_bytes.decode('utf-8-sig'))
        if not isinstance(result, dict) or result.get('ok') is not True:
            code = result.get('code') if isinstance(result, dict) else None
            if code in ('busy', 'auth_pending', 'scheduler_changed', 'task_exists', 'unsupported_operation'):
                raise Conflict(code, 'scheduler')
            if code == 'scheduler_failure' and result.get('stage') in ('connect', 'folder', 'definition', 'xml', 'operation'):
                raise Conflict('scheduler_failure', result['stage'])
            raise ValueError()
        return result
    except Conflict:
        raise
    except Exception:
        raise Conflict('bounded_transport_failed', 'transport') from None


_PROTECT = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
  $r = [Console]::In.ReadToEnd() | ConvertFrom-Json
  if ($r.text -isnot [string]) { throw 'type' }
  # UTF-16LE bytes protected with CurrentUser/null entropy are the unkeyed
  # ConvertFrom-SecureString .cred format, without SecureString's 65536 limit.
  # Reserve framing and DPAPI overhead inside the unchanged 8 MiB transport.
  $plainLimit = 2096128
  $cipherLimit = 8387584
  Add-Type -AssemblyName System.Security
  $scope = [Security.Cryptography.DataProtectionScope]::CurrentUser
  $unicode = New-Object System.Text.UnicodeEncoding($false, $false, $true)
  if ($r.operation -eq 'protect') {
    if ($r.text.Length -gt $plainLimit) { throw 'limit' }
    $bytes = $unicode.GetBytes($r.text)
    try {
      $cipher = [Security.Cryptography.ProtectedData]::Protect($bytes, $null, $scope)
      $result = [BitConverter]::ToString($cipher).Replace('-', '').ToLowerInvariant()
      if ($result.Length -gt $cipherLimit) { throw 'limit' }
    } finally { if ($null -ne $bytes) { [Array]::Clear($bytes, 0, $bytes.Length) } }
  } elseif ($r.operation -eq 'unprotect') {
    if ($r.text.Length -gt $cipherLimit -or $r.text.Length -eq 0 -or
        $r.text.Length % 2 -ne 0 -or $r.text -cmatch '[^0-9a-fA-F]') { throw 'cipher' }
    $cipher = [Runtime.Remoting.Metadata.W3cXsd2001.SoapHexBinary]::Parse($r.text).Value
    $bytes = [Security.Cryptography.ProtectedData]::Unprotect($cipher, $null, $scope)
    try {
      if ($bytes.Length % 2 -ne 0 -or $bytes.Length -gt (2 * $plainLimit)) { throw 'limit' }
      $result = $unicode.GetString($bytes)
    } finally { if ($null -ne $bytes) { [Array]::Clear($bytes, 0, $bytes.Length) } }
  } else { throw 'operation' }
  @{ok=$true; text=$result} | ConvertTo-Json -Compress
} catch { @{ok=$false; code='protector_failed'} | ConvertTo-Json -Compress }
'''


class DPAPIProtector:
    """CurrentUser DPAPI in CONFIG's unkeyed SecureString .cred byte format."""
    # ASCII envelope -> UTF-16LE -> DPAPI -> hex: approximately 4x expansion.
    # Match _PROTECT; reserve 4096 bytes for DPAPI overhead and JSON framing.
    PLAIN_LIMIT = (LIMIT - 4096) // 4
    CIPHER_LIMIT = LIMIT - 1024
    CACHE_LIMIT = 4 * LIMIT

    def __init__(self):
        self._cache = OrderedDict()
        self._cache_size = 0
        self._control = None

    @staticmethod
    def _validate(data, *, cipher):
        if not isinstance(data, bytes):
            raise Conflict('protector_invalid', 'vault')
        limit = DPAPIProtector.CIPHER_LIMIT if cipher else DPAPIProtector.PLAIN_LIMIT
        if len(data) > limit:
            raise Conflict('protector_limit', 'vault')
        try:
            text = data.decode('ascii')
        except UnicodeError:
            raise Conflict('protector_invalid', 'vault') from None
        if cipher and (not data or len(data) % 2 or
                       data.translate(None, b'0123456789abcdefABCDEF')):
            raise Conflict('protector_invalid', 'vault')
        return text

    def _convert(self, operation, data):
        text = self._validate(data, cipher=operation == 'unprotect')
        if operation == 'unprotect':
            text = self._unprotect(text)
        else:
            result = powershell(_PROTECT, {'operation': operation, 'text': text})
            if not isinstance(result, dict) or not isinstance(result.get('text'), str):
                raise Conflict('protector_invalid', 'vault')
            text = result['text']
        try:
            output = text.encode('ascii')
        except UnicodeError:
            raise Conflict('protector_invalid', 'vault') from None
        self._validate(output, cipher=operation == 'protect')
        return output

    @staticmethod
    def _unprotect(text):
        if os.name != 'nt':
            raise Conflict('windows_required', 'transport')
        try:
            import win32crypt
        except ImportError:
            raise Conflict('protector_dependency_unavailable', 'vault') from None
        try:
            # Null entropy and CRYPTPROTECT_UI_FORBIDDEN preserve the unkeyed
            # CurrentUser SecureString format without an interactive prompt.
            _, plain = win32crypt.CryptUnprotectData(bytes.fromhex(text), None, None, None, 1)
        except Exception:
            raise Conflict('protector_failed', 'vault') from None
        if not isinstance(plain, bytes) or len(plain) % 2:
            raise Conflict('protector_invalid', 'vault')
        if len(plain) > 2 * DPAPIProtector.PLAIN_LIMIT:
            raise Conflict('protector_limit', 'vault')
        try:
            return plain.decode('utf-16-le')
        except UnicodeError:
            raise Conflict('protector_invalid', 'vault') from None

    def protect(self, data):
        return self._convert('protect', data)

    def unprotect(self, data):
        # Vault.get still rereads and validates each file and plaintext envelope.
        # Reuse only an already decrypted ciphertext within this operation.
        from llmcall.process import current_control, remaining_timeout
        control = current_control()
        if control is not self._control:
            self._cache.clear()
            self._cache_size = 0
            self._control = control
        if control.is_set():
            raise Conflict('transport_cancelled', 'transport')
        if remaining_timeout(25) <= 0:
            raise Conflict('transport_timeout', 'transport')
        self._validate(data, cipher=True)
        key = hashlib.sha256(data).digest()
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        value = self._convert('unprotect', data)
        if control.is_set():
            raise Conflict('transport_cancelled', 'transport')
        if remaining_timeout(25) <= 0:
            raise Conflict('transport_timeout', 'transport')
        while self._cache and (len(self._cache) >= 64 or self._cache_size + len(value) > self.CACHE_LIMIT):
            _, previous = self._cache.popitem(last=False)
            self._cache_size -= len(previous)
        self._cache[key] = value
        self._cache_size += len(value)
        return value


_SCHEDULER = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
  $stage = 'connect'
  $r = [Console]::In.ReadToEnd() | ConvertFrom-Json
  if ($r.schemaVersion -ne 1 -or $r.TaskPath -cne '\' -or
      [string]::IsNullOrWhiteSpace($r.TaskName) -or $r.TaskName -match '[\\/\x00-\x1f*?\[\]]') { throw 'scope' }
  $service = New-Object -ComObject 'Schedule.Service'
  $service.Connect()
  $stage = 'folder'
  if ($r.operation -ne 'prepare') { $folder = $service.GetFolder('\') }
  function Read-Exact {
    try { $task = $folder.GetTask([string]$r.TaskName) }
    catch {
      $failure = $_.Exception
      while ($null -ne $failure.InnerException) { $failure = $failure.InnerException }
      if ($failure.HResult -eq -2147024894) { return $null }
      throw
    }
    if ($task.Path -ine ('\' + [string]$r.TaskName) -or $task.State -eq 0) { throw 'scope' }
    return $task
  }
  function Reply-Task($task) {
    if ($null -eq $task) { return @{state='absent'} }
    return @{state='present'; xml=[string]$task.Xml; enabled=[bool]$task.Enabled;
             running=([int]$task.State -in @(2,4) -or $task.GetInstances(0).Count -gt 0);
             path=[string]$task.Path}
  }
  if ($r.operation -eq 'query') {
    @{ok=$true; observation=(Reply-Task (Read-Exact))} | ConvertTo-Json -Depth 20 -Compress
  } elseif ($r.operation -in @('run', 'stop')) {
    $stage = 'operation'
    $task = Read-Exact
    if ($null -eq $task -or $r.expected.state -ne 'present' -or
        [string]$task.Xml -cne [string]$r.expected.value.xml -or
        [bool]$task.Enabled -ne [bool]$r.expected.value.enabled) {
      @{ok=$false; code='scheduler_changed'} | ConvertTo-Json -Compress
    } else {
      $before = Reply-Task $task
      if ($r.operation -eq 'run') {
        if (-not $before.enabled -or $before.running) {
          @{ok=$false; code='busy'} | ConvertTo-Json -Compress
          exit
        }
        # This is the only payload-starting operation; query/prepare/publish
        # never call it. The caller must explicitly request run under authority.
        $instance = $task.Run($null)
        if ($null -eq $instance) { throw 'missing run acknowledgement' }
      } else {
        if ($before.running) { $task.Stop(0) }
      }
      $after = Reply-Task (Read-Exact)
      if ($r.operation -eq 'stop') {
        $watch = [Diagnostics.Stopwatch]::StartNew()
        while ($after.state -eq 'present' -and $after.running -and $watch.ElapsedMilliseconds -lt 2000) {
          Start-Sleep -Milliseconds 100
          $after = Reply-Task (Read-Exact)
        }
      }
      if ($after.state -ne 'present') { throw 'missing control readback' }
      if ([string]$after.xml -cne [string]$before.xml -or $after.enabled -ne $before.enabled) {
        throw 'changed control readback'
      }
      @{ok=$true; acknowledged=$true; running=[bool]$after.running} | ConvertTo-Json -Compress
    }
  } elseif ($r.operation -eq 'prepare') {
    $stage = 'definition'
    $definition = $service.NewTask(0)
    $stage = 'xml'
    $definition.XmlText = [string]$r.xml
    if ([int]$definition.Principal.LogonType -in @(1,6)) {
      @{ok=$false; code='auth_pending'} | ConvertTo-Json -Compress
    } else {
      @{ok=$true; xml=[string]$definition.XmlText} | ConvertTo-Json -Compress
    }
  } elseif ($r.operation -eq 'publish') {
    $task = Read-Exact
    if ($null -ne $task -and ([int]$task.State -in @(2,4) -or $task.GetInstances(0).Count -gt 0)) {
      @{ok=$false; code='busy'} | ConvertTo-Json -Compress
    } elseif (($r.expected.state -eq 'absent' -and $null -ne $task) -or
              ($r.expected.state -eq 'present' -and ($null -eq $task -or
               [string]$task.Xml -cne [string]$r.expected.value.xml -or
               [bool]$task.Enabled -ne [bool]$r.expected.value.enabled))) {
      @{ok=$false; code='scheduler_changed'} | ConvertTo-Json -Compress
    } elseif ($r.desired.state -eq 'absent') {
      if ($null -ne $task) { $folder.DeleteTask([string]$r.TaskName, 0) }
      @{ok=$true} | ConvertTo-Json -Compress
    } else {
      $definition = $service.NewTask(0)
      $definition.XmlText = [string]$r.desired.value.xml
      if ([int]$definition.Principal.LogonType -in @(1,6)) {
        @{ok=$false; code='auth_pending'} | ConvertTo-Json -Compress
      } else {
        # TASK_CREATE or TASK_UPDATE, never CREATE_OR_UPDATE. Suppress registration triggers.
        $flags = 4 + 32
        if ($r.expected.state -eq 'absent') { $flags = 2 + 32 }
        $registered = $folder.RegisterTaskDefinition([string]$r.TaskName, $definition,
            $flags, [string]$definition.Principal.UserId, $null, [int]$definition.Principal.LogonType, $null)
        @{ok=$true; observation=(Reply-Task $registered)} | ConvertTo-Json -Depth 20 -Compress
      }
    }
  } else { @{ok=$false; code='unsupported_operation'} | ConvertTo-Json -Compress }
} catch { @{ok=$false; code='scheduler_failure'; stage=$stage} | ConvertTo-Json -Compress }
'''


class COMTransport:
    """Fixed root-task registration and explicit bounded controls; no enumeration."""
    def __call__(self, operation, request):
        pass
        if operation == 'cleanup':
            return {'ok': True}  # Preparation owns no Scheduler object.
        if operation == 'prepare':
            if request['value'] is None:
                return {'ok': True, 'snapshot': {'state': 'absent'}}
            value = normalize(request['value'])
            reply = powershell(_SCHEDULER, {**request, 'operation': operation, 'xml': value['xml']})
            value['xml'] = reply['xml']
            return {'ok': True, 'snapshot': observation(request['TaskName'], value)}
        reply = powershell(_SCHEDULER, {**request, 'operation': operation})
        if operation == 'query':
            raw = reply['observation']
            if raw['state'] == 'absent':
                return {'ok': True, 'snapshot': raw}
            if raw['path'].casefold() != ('\\' + request['TaskName']).casefold():
                raise Conflict('scheduler_scope_mismatch', 'scheduler')
            return {'ok': True, 'snapshot': observation(request['TaskName'], raw)}
        return reply
