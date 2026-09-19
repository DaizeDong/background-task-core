"""Disposable control transport/CLI driver. Never imports a live Scheduler adapter."""
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT.parent / 'fleet-guards'))
sys.path.insert(0, str(ROOT.parent / 'llmcall'))
from t12_runtime_support import SyntheticScheduler, FakeProtector, setup, encode, read_json, fs
from task_console import registration as reg
from task_console.runtime import RuntimeConfig, create_runtime


class ControlScheduler(SyntheticScheduler):
    def __call__(self, operation, request):
        if operation not in ('run', 'stop', 'enable', 'disable'):
            return super().__call__(operation, request)
        assert request['TaskPath'] == '\\'
        self.calls.append((operation, deepcopy(request)))
        tasks = read_json(self.path)
        name = request['TaskName']
        current = tasks[name]
        assert reg._same('scheduler', current, request['expected'])
        # Simulate an immediately completed run; it still proves no payload outcome.
        if operation == 'stop':
            current['value']['running'] = False
        elif operation in ('enable', 'disable'):
            current['value']['enabled'] = operation == 'enable'
        fs.atomic_replace(self.path, encode(tasks))
        return {'ok': True, 'acknowledged': True, 'running': current['value']['running'],
                'enabled': current['value']['enabled']}


def configured(root):
    root = Path(root)
    config = RuntimeConfig.read(root / 'private/config.json', private_root=root / 'private',
                              state_root=root / 'state', vault_root=root / 'vault')
    return create_runtime(config, transport=ControlScheduler(root), protector=FakeProtector())


def fixture_runtime(root, *, migrated=False, enabled=False, name='AcmeSync'):
    _, task_id = setup(root, enabled=enabled, name=name)
    runtime = configured(root)
    if migrated:
        proposal = reg.build_plan(runtime, [task_id], migrate=True, approve_enable=enabled)
        result = reg.apply(proposal, proposal['input_revision'], runtime=runtime)
        assert result['ok'], result
    return runtime, task_id


if __name__ == '__main__':
    from task_console.__main__ import main
    # This explicit Python-only injection is in tests, never runtime JSON/env.
    raise SystemExit(main(sys.argv[2:], runtime=configured(sys.argv[1])))
