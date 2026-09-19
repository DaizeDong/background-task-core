"""T12 review regressions: synthetic resources, real filesystem and child exits."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from t12_runtime_support import setup, configured, reg, fs, encode, read_json, example_request
from task_console.runtime_xml import _split_command, render


def apply(runtime, task_id, **options):
    plan = reg.build_plan(runtime, [task_id], **options)
    result = reg.apply(plan, plan['input_revision'], runtime=runtime)
    assert result['status'] == 'committed', result
    return result


@pytest.mark.parametrize('arguments,expected', [('"a""b"', ['a"b']), ('"""x"""', ['"x"']),
                                             ('"" "a b" tail\\', ['', 'a b', 'tail\\'])])
def test_native_noncanonical_arguments(arguments, expected):
    assert _split_command(arguments) == expected


def test_controls_advance_immutable_inputs_and_merge_new_submission(tmp_path):
    runtime, task_id = setup(tmp_path)
    apply(runtime, task_id, migrate=True)
    original = (tmp_path / 'private/desired.json').read_bytes()
    assert reg.control('AcmeSync', 'enable', runtime=configured(tmp_path))['status'] == 'committed'
    fresh = configured(tmp_path)
    assert fresh.load()['request']['bindings']['tasks'][task_id]['enabled'] is True
    apply(fresh, task_id)
    assert configured(tmp_path).scheduler.read('AcmeSync')['value']['enabled'] is True
    assert (tmp_path / 'private/desired.json').read_bytes() == original
    submitted = read_json(tmp_path / 'private/desired.json')
    submitted['bindings']['tasks'][task_id]['argv'].append('--synthetic-change')
    fs.atomic_replace(tmp_path / 'private/desired.json', encode(submitted))
    fresh = configured(tmp_path)
    binding = fresh.load()['request']['bindings']['tasks'][task_id]
    assert binding['enabled'] is True
    assert binding['argv'][-1] == '--synthetic-change'
    with pytest.raises(reg.Conflict, match='declaration_apply_required'):
        reg.control('AcmeSync', 'disable', runtime=fresh)
    apply(fresh, task_id)
    assert reg.control('AcmeSync', 'disable', runtime=configured(tmp_path))['status'] == 'committed'
    assert configured(tmp_path).load()['request']['bindings']['tasks'][task_id]['enabled'] is False
    # A projection cannot become desired input even if its bytes remain parseable.
    projected = read_json(tmp_path / 'private/bindings.json')
    projected['tasks'][task_id]['enabled'] = True
    fs.atomic_replace(tmp_path / 'private/bindings.json', encode(projected))
    assert configured(tmp_path).load()['request']['bindings']['tasks'][task_id]['enabled'] is False


UNDO_CHILD = '''
import os, sys
sys.path.insert(0, sys.argv[2])
from t12_runtime_support import configured, reg, fs
from pathlib import Path
runtime = configured(sys.argv[1])
task_id = next(iter(runtime.load()['ownership']))
prepare = runtime.files.prepare
if sys.argv[3] == 'known':
    def dying_prepare(key, value, token):
        snapshot = prepare(key, value, token)
        if token.endswith(':undo'):
            os._exit(73)
        return snapshot
    runtime.files.prepare = dying_prepare
else:
    create = fs.create_no_replace_with_identity
    def dying_prepare(key, value, token):
        if token.endswith(':undo'):
            def dying_create(path, data, *args):
                result = create(path, data, *args)
                if Path(path).name.startswith('.t12-stage-'):
                    os._exit(73)
                return result
            fs.create_no_replace_with_identity = dying_create
        return prepare(key, value, token)
    runtime.files.prepare = dying_prepare
seen = 0
def checkpoint(point):
    global seen
    if point == 'published':
        seen += 1
        if seen == 2:
            raise RuntimeError('synthetic rollback')
runtime.checkpoint = checkpoint
plan = reg.build_plan(runtime, [task_id], migrate=True)
reg.apply(plan, plan['input_revision'], runtime=runtime)
raise AssertionError('death boundary not reached')
'''


@pytest.mark.parametrize('identity', ['known', 'unknown'])
def test_prejournal_undo_child_death_and_fresh_recovery(tmp_path, identity):
    runtime, _ = setup(tmp_path)
    before = {p: runtime.files.read(p).get('value') for p in runtime.files.paths}
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path))
    child = subprocess.run([sys.executable, '-B', '-c', UNDO_CHILD, str(tmp_path),
                            str(Path(__file__).parent), identity], env=env,
                           capture_output=True, text=True, timeout=120)
    assert child.returncode == 73, child.stdout + child.stderr
    fresh = configured(tmp_path)
    journal = fresh.journal.list()[0]
    stages = [read_json(p) for p in (tmp_path / 'state/stages').glob('*.json')]
    undo = next(s for s in stages if s['token'].endswith(':undo'))
    assert (undo['identity'] is not None) == (identity == 'known')
    assert not any('undo' in step for step in journal['steps'])
    for _ in range(2):
        result = reg.recover(journal['transaction_id'], runtime=configured(tmp_path))
        if identity == 'known':
            assert result['status'] == 'rolled_back' and result['cleaned'], result
            assert {p: configured(tmp_path).files.read(p).get('value') for p in before} == before
            assert configured(tmp_path).journal.pending(['authority']) == []
        else:
            assert result['status'] == 'conflict' and not result['cleaned']
            assert Path(undo['stage']).exists()
            assert configured(tmp_path).journal.pending(['authority'])


@pytest.mark.parametrize('trigger', [
    {'type': 'daily', 'at': '03:15', 'enabled': False},
    {'type': 'daily', 'at': '03:15', 'start_boundary': '2030-01-01T03:15:00Z'},
    {'type': 'weekly', 'at': '03:15', 'days': ['mon'], 'weeks_interval': 2},
    {'type': 'weekly', 'at': '03:15', 'days': ['mon', 'mon']},
    {'type': 'interval', 'minutes': 2, 'duration': 'PT1H'},
    {'type': 'logon', 'delay': 'PT1M'},
])
def test_supported_trigger_rejects_unrendered_fields(trigger):
    request = example_request()
    task_id = next(iter(request['bindings']['tasks']))
    request['bindings']['tasks'][task_id]['trigger'] = trigger
    with pytest.raises(reg.ContractError):
        reg._compile({'request': request})


def test_renderer_cannot_embed_unrendered_trigger_fields():
    spec = reg._compile({'request': example_request()})['task_specs'][0]
    spec['trigger']['enabled'] = False
    with pytest.raises(reg.ContractError):
        render(spec, {'state': 'absent'}, False)


def test_input_reference_loss_never_falls_back_to_projections(tmp_path):
    runtime, task_id = setup(tmp_path)
    apply(runtime, task_id, migrate=True)
    reference = configured(tmp_path).authority.receipt()['input_reference']
    _, domain, obj, _, _ = reference.split(':')
    (tmp_path / 'vault' / ('task-console-' + domain + '-' + obj + '.cred')).unlink()
    with pytest.raises(reg.Conflict, match='secure_reference_unavailable'):
        configured(tmp_path).load()


def test_supported_trigger_metadata_uses_reviewed_full_xml_passthrough(tmp_path):
    from task_console.runtime_xml import parse, NS
    runtime, _ = setup(tmp_path)
    old = runtime.scheduler.read('AcmeSync')
    xml = old['value']['xml'].replace('<Enabled>true</Enabled>', '<Enabled>false</Enabled>')
    request = example_request()
    binding = next(iter(request['bindings']['tasks'].values()))
    binding['trigger'] = {'type': 'xml', 'xml': xml, 'owner': 'synthetic', 'reason': 'reviewed trigger'}
    binding['xml_passthrough'] = {'xml': xml, 'owner': 'synthetic', 'reason': 'reviewed task'}
    spec = reg._compile({'request': request})['task_specs'][0]
    result = render(spec, old, False)
    assert parse(result['xml']).findtext('./{' + NS + '}Triggers/{' + NS + '}CalendarTrigger/{' + NS + '}Enabled') == 'false'


def test_native_parser_matches_synthetic_child_argv():
    argv = ['a"b', '', 'space value', 'tail\\', '?', '$literal; &']
    child = subprocess.run([sys.executable, '-c', 'import json,sys; print(json.dumps(sys.argv[1:]))', *argv],
                           capture_output=True, text=True, timeout=15)
    assert child.returncode == 0
    assert _split_command(subprocess.list2cmdline(argv)) == json.loads(child.stdout) == argv


@pytest.mark.parametrize('point', ['receipt_intent', 'pointer_published'])
def test_control_completion_child_death_retains_effective_input(tmp_path, point):
    runtime, task_id = setup(tmp_path)
    apply(runtime, task_id, migrate=True)
    driver = Path(__file__).with_name('t12_runtime_support.py')
    child = subprocess.run([sys.executable, '-B', str(driver), str(tmp_path), 'enable', point],
                           capture_output=True, text=True, timeout=120)
    assert child.returncode == 73, child.stdout + child.stderr
    fresh = configured(tmp_path)
    pending = fresh.journal.pending(['authority'])
    assert len(pending) == 1
    source = tmp_path / 'private/desired.json'
    submitted = source.read_bytes()
    source.unlink()
    result = reg.recover(pending[0], runtime=fresh)
    assert result['status'] == 'committed' and result['cleaned'], result
    fs.atomic_replace(source, submitted)
    assert configured(tmp_path).load()['request']['bindings']['tasks'][task_id]['enabled'] is True


@pytest.mark.parametrize('change', ['bytes', 'identity', 'resource'])
def test_resume_stage_requires_matching_ownership(tmp_path, change):
    from task_console.runtime_storage import FileResources
    key, other = tmp_path / 'owned.txt', tmp_path / 'other.txt'
    files = FileResources([key, other], tmp_path / 'stages')
    token = 'a' * 32 + ':0:undo'
    files.prepare(str(key), b'synthetic', token)
    record = read_json(files._stage(token))
    stage = Path(record['stage'])
    if change == 'bytes':
        stage.write_bytes(b'changed')
    elif change == 'identity':
        stage.rename(tmp_path / 'retained.txt')
        stage.write_bytes(b'synthetic')
    with pytest.raises(reg.Conflict, match='stage_owner_lost'):
        files.prepare(str(other if change == 'resource' else key), b'synthetic', token)
    assert stage.exists()
