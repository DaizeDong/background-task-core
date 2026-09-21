"""Shared explicit authority entry for HTTP, CLI and compatibility delegates.

Construction never discovers a home, registers a task, or selects Python code
from configuration. The only executable adapters are bundled runtime adapters.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

# The old server/maint entrypoints import sibling modules as top-level modules.
# Resolve the fixed installed package, never an import named by runtime JSON.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = 'task_console'
from . import registration
from .contracts import ContractError
from .scheduler_windows import task_name

ENVIRONMENT = {
    'runtime_config': 'TASK_CONSOLE_RUNTIME_CONFIG',
    'private_root': 'TASK_CONSOLE_PRIVATE_ROOT',
    'state_root': 'TASK_CONSOLE_STATE_ROOT',
    'vault_root': 'TASK_CONSOLE_VAULT_ROOT',
}
VERBS = ('enable', 'disable', 'run', 'stop', 'retire')


def load_runtime(*, runtime_config=None, private_root=None, state_root=None,
                 vault_root=None, environ=None, for_recovery=False):
    """Load all four explicit arguments, or all four environment settings.

    Partial explicit arguments never mix with environment settings. Configured
    but missing/corrupt/stale authority is an error, never a legacy fallback.
    """
    values = dict(runtime_config=runtime_config, private_root=private_root,
                  state_root=state_root, vault_root=vault_root)
    if not any(value is not None for value in values.values()):
        env = os.environ if environ is None else environ
        values = {key: env.get(variable) for key, variable in ENVIRONMENT.items()}
    if not any(value is not None for value in values.values()):
        raise ContractError('runtime_not_configured', 'runtime')
    if not all(isinstance(value, str) and value.strip() for value in values.values()):
        raise ContractError('incomplete_runtime_configuration', 'runtime')
    if not all(Path(value).is_absolute() for value in values.values()):
        raise ContractError('absolute_runtime_paths_required', 'runtime')
    config_path = values.pop('runtime_config')
    from .runtime import RuntimeConfig, create_runtime
    runtime = create_runtime(RuntimeConfig.read(config_path, **values))
    # Recovery validates its pinned journal/input references and can repair a
    # missing pointer without the submitted source. All other entries validate
    # current authority; no last-known-good runtime is cached.
    if not for_recovery:
        registration._compile(runtime.load())
    return runtime


def validate(name, verb, reason=''):
    task_name(name)
    if verb not in VERBS:
        raise ContractError('invalid_operation', 'verb')
    if verb == 'retire' and (not isinstance(reason, str) or not reason.strip()):
        raise ContractError('no_reason', 'reason')


def error_result(exc, *, name='', verb=''):
    """Complete safe errors; never echo transport exceptions or private XML."""
    code = exc.code if isinstance(exc, ContractError) else 'runtime_failed'
    field = exc.field if isinstance(exc, ContractError) else 'runtime'
    result = {'schemaVersion': 1, 'ok': False, 'name': name, 'verb': verb,
            'message': code.replace('_', ' '), 'error': {'code': code, 'field': field},
            'before': None, 'after': None, 'payload_success': None}
    shortfalls = {
        'reviewed_rebinding_required': 'Review each task ID with source_revision, target_revision, target_principal, generation, approved=true and a nonempty reason; regenerate the exact restore plan.',
        'legacy_principal_conversion_required': 'Legacy XML principal differs from the reviewed declaration. Review an explicit declaration migration/rebinding before restore; task names cannot establish ownership.',
        'auth_pending': 'Password-based principal needs the credential owner\'s submission adapter. No password was requested and no task was registered by this refusal.',
        'ownership_required': 'Reviewed adoption/ownership evidence for this exact task ID, literal root path, writer, epoch and observed definition is required.',
        'legacy_declarative_conversion_required': 'Mutating legacy callbacks cannot advance recoverable receipts. Use the built-in controller transaction or review a declaration conversion.',
    }
    if code in shortfalls:
        result['shortfalls'] = [shortfalls[code]]
    return result


def exit_code(result):
    if result.get('ok') is True:
        return 0
    return 3 if result.get('transaction_id') or result.get('status') == 'cleanup_uncertain' else 2


class Controller:
    def __init__(self, runtime):
        from .runtime import dispatch_adapter
        self.runtime = runtime
        self.dispatch = dispatch_adapter(runtime)

    def export_tasks(self):
        from .export_restore import export_tasks
        return export_tasks(self.runtime)

    def restore_plan(self, archive, task_ids, *, rebinding=None):
        from .export_restore import restore_plan
        return restore_plan(self.runtime, archive, task_ids, rebinding=rebinding)

    def restore(self, plan, approval):
        from .export_restore import restore
        return restore(self.runtime, plan, approval)

    def restore_recover(self, transaction_id, approval):
        from .export_restore import restore_recover
        return restore_recover(self.runtime, transaction_id, approval)

    def _linked(self, task_ids):
        from .linked_items import report
        return report(self.runtime, task_ids, self.runtime.load())

    def _scheduler_control(self, name, verb):
        snapshot = registration._read(self.runtime.scheduler, name)
        return self.runtime.scheduler.control(name, verb, snapshot)

    def action(self, name, verb, *, reason='', legacy=None):
        validate(name, verb, reason)
        from llmcall.process import execution_scope
        # Registration also verifies protected before-images, publishes projections,
        # and commits authority. Multiple native round trips can exceed one minute.
        budget = 60 if verb in ('run', 'stop') else 300
        with execution_scope(timeout=budget) as control:
            if control.is_set():
                raise ContractError('control_cancelled', 'runtime')
            callback = legacy
            if callback is None and verb in ('run', 'stop'):
                callback = lambda: self._scheduler_control(name, verb)
            result = self.dispatch(name, verb, reason=reason, legacy=callback)
        if not isinstance(result, dict) or type(result.get('ok')) is not bool:
            raise ContractError('invalid_control_result', 'runtime')
        result = dict(result)
        result.update(schemaVersion=1, name=name, verb=verb)
        result.setdefault('message', (verb + ' applied') if result['ok'] else 'Control incomplete; inspect status')
        result.setdefault('before', None)
        result.setdefault('after', None)
        if verb == 'retire':
            result.setdefault('done', ['scheduler', 'projections'] if result['ok'] else [])
            result.setdefault('backups', [])
            result.setdefault('reason', reason.strip())
            if result.get('transaction_id'):
                result['recovery_transaction'] = result['transaction_id']
        if verb in ('run', 'stop'):
            result['payload_success'] = None
        return result

    def retire_plan(self, name, reason):
        validate(name, 'retire', reason)
        bundle = self.runtime.load()
        specs = registration._compile(bundle)['task_specs']
        selected = next((spec for spec in specs if spec['name'] == name), None)
        if selected is None:
            raise ContractError('ownership_required', 'task_id')
        task_id = selected['task_id']
        proposal = registration.build_plan(self.runtime, [task_id], operation='retire', reason=reason,
                    legacy_transaction=task_id not in bundle['request']['machine']['migrated_tasks'])
        result = {'name': name, 'reason': reason, 'changes': len(proposal['changes']['files']) + 1,
                  'blocked': [], 'steps': [{'step': 'disable', 'state': 'will-change',
                  'detail': 'Disable and verify the owned task'},
                  {'step': 'projections', 'state': 'will-change',
                   'detail': 'Remove active projections and retain a retirement tombstone'}],
                  'registration_plan': proposal}
        items = proposal['linked_work_items']
        result['linked_work_items'] = None if items is None else items[task_id]
        result['linked_work_items_status'] = proposal['linked_work_items_status']
        result['linked_work_items_code'] = proposal['linked_work_items_code']
        return result


def action(name, verb, *, reason='', runtime=None, legacy=None):
    validate(name, verb, reason)
    return Controller(runtime if runtime is not None else load_runtime()).action(
        name, verb, reason=reason, legacy=legacy)


def retire_plan(name, reason, *, runtime=None):
    validate(name, 'retire', reason)
    return Controller(runtime if runtime is not None else load_runtime()).retire_plan(name, reason)
