"""Pure Scheduler XML and hidden launcher rendering; never writes or executes."""
from copy import deepcopy
from decimal import Decimal, localcontext
import base64
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET

from .registration import Conflict

NS = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
ET.register_namespace('', NS)
MARKER = 'task-console-v1:'
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
    from .runtime_windows import split_arguments
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


def launcher_bytes(argv, cwd, *, scheduler_name=None):
    if (not argv or any(not isinstance(a, str) or any(c in a for c in '\r\n\x00%') for a in argv)
            or any(c in cwd for c in '\r\n\x00')):
        raise Conflict('invalid_launcher_arguments', 'launcher')
    # Same Run(command, 0, True) and exit propagation as hide-task-windows.ps1.
    # The renderer also preserves cwd, which a Scheduler Exec normally supplies.
    command = subprocess.list2cmdline(argv).replace('"', '""')
    workdir = cwd.replace('"', '""')
    context = ''
    if scheduler_name is not None:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', scheduler_name):
            raise Conflict('invalid_scheduler_context_name', 'launcher')
        context = ('Set taskEnv = shell.Environment("Process")\r\n'
                   'If taskEnv("TASK_SCHEDULER_NAME") <> "" And '
                   f'taskEnv("TASK_SCHEDULER_NAME") <> "{scheduler_name}" Then WScript.Quit 2\r\n'
                   f'taskEnv("TASK_SCHEDULER_NAME") = "{scheduler_name}"\r\n')
    source = ('\' Generated by task-console; transaction-owned.\r\n'
              'Set shell = CreateObject("WScript.Shell")\r\n'
              f'shell.CurrentDirectory = "{workdir}"\r\n'
              f'{context}'
              f'WScript.Quit shell.Run("{command}", 0, True)\r\n')
    return b'\xff\xfe' + source.encode('utf-16-le')


def render(spec, old, enabled, launcher=None):
    from .compiler import validate_trigger_shape
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
        if 'trigger' not in old['value']['spec']:
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
        if spec['timezone'] != 'UTC':
            raise Conflict('timezone_requires_xml_passthrough', 'scheduler')
        triggers = node(root, 'Triggers')
        triggers.clear()
        entries = spec['trigger'] if isinstance(spec['trigger'], list) else [spec['trigger']]
        for trigger in entries:
            kind = trigger.get('type')
            if kind in ('daily', 'weekly'):
                item = ET.SubElement(triggers, '{' + NS + '}CalendarTrigger')
                at = trigger['at'] if len(trigger['at']) == 8 else trigger['at'] + ':00'
                text(item, 'StartBoundary', '2000-01-01T' + at + 'Z')
                text(item, 'Enabled', True)
                if kind == 'daily':
                    text(node(item, 'ScheduleByDay'), 'DaysInterval', 1)
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
                text(item, 'StartBoundary', '2000-01-01T00:00:00Z')
                text(item, 'Enabled', True)
            elif kind == 'logon':
                item = ET.SubElement(triggers, '{' + NS + '}LogonTrigger')
                text(item, 'Enabled', True)
                text(item, 'UserId', spec['principal']['user_id'])
            else:
                raise Conflict('trigger_requires_xml_passthrough', 'scheduler')
    actions = node(root, 'Actions')
    argv = spec['argv'] if launcher is None else ['wscript.exe', launcher]
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
    text(action, 'Arguments', subprocess.list2cmdline(argv[1:]))
    text(action, 'WorkingDirectory', spec['cwd'])
    return {'xml': ET.tostring(root, encoding='unicode'), 'spec': spec, 'enabled': enabled, 'running': False}
