"""Explicit runtime composition and generation authority. Import performs no I/O."""
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import re

from fleet_guards import filesystem as fs
from . import registration as reg
from .runtime_storage import (FileResources, JournalStore, ProtectedVault, ResourceLocks,
                              digest, encode, identifier, read_json)
from .scheduler_windows import WindowsScheduler


def _submitted_changes(previous, submitted, effective):
    """Apply only explicit submission deltas; unchanged fields retain controls."""
    if previous == submitted:
        return deepcopy(effective)
    if not all(isinstance(v, dict) for v in (previous, submitted, effective)):
        return deepcopy(submitted)
    result = deepcopy(effective)
    for key in previous.keys() - submitted.keys():
        result.pop(key, None)
    for key, value in submitted.items():
        if key not in previous:
            result[key] = deepcopy(value)
        elif previous[key] != value:
            result[key] = _submitted_changes(previous[key], value, result.get(key))
    return result


@dataclass(frozen=True)
class RuntimeConfig:
    private_root: Path
    state_root: Path
    vault_root: Path
    domain: str
    desired: Path
    adoption: Path
    paths: dict
    active_xml: dict
    launchers: dict

    @classmethod
    def read(cls, path, *, private_root, state_root, vault_root):
        """Roots are trusted caller arguments, never inferred from declarations."""
        private = fs.validate_path(private_root)
        state = fs.validate_path(state_root)
        vault = fs.validate_path(vault_root)
        document = read_json(fs.validate_path(path, root=private))
        if set(document) != {'schemaVersion', 'domain', 'desired', 'adoption', 'paths', 'active_xml', 'launchers'} or document['schemaVersion'] != 1:
            raise reg.Conflict('invalid_runtime_config', 'runtime')
        def resolve(value):
            if not isinstance(value, str) or not value:
                raise reg.Conflict('invalid_resource_path', 'runtime')
            return fs.validate_path(private / value, root=private)
        paths = {k: str(resolve(v)) for k, v in document['paths'].items()}
        exports = {k: None if v is None else str(resolve(v)) for k, v in document['active_xml'].items()}
        launchers = {k: str(resolve(v)) for k, v in document['launchers'].items()}
        desired, adoption = resolve(document['desired']), resolve(document['adoption'])
        resources = list(paths.values()) + list(launchers.values()) + [p for p in exports.values() if p]
        if (len({p.casefold() for p in resources}) != len(resources)
                or str(desired).casefold() in {p.casefold() for p in resources}
                or str(adoption).casefold() in {p.casefold() for p in resources}
                or any(Path(p).is_relative_to(state) or Path(p).is_relative_to(vault) for p in resources)
                or any(p.is_relative_to(state) or p.is_relative_to(vault) for p in (desired, adoption))
                or state.is_relative_to(vault) or vault.is_relative_to(state)
                or desired == adoption):
            raise reg.Conflict('aliased_runtime_paths', 'runtime')
        return cls(private, state, vault, identifier(document['domain']), desired, adoption, paths, exports, launchers)


class GenerationAuthority:
    def __init__(self, config, files, scheduler, journal, vault):
        self.config, self.files, self.scheduler, self.journal = config, files, scheduler, journal
        self.vault = vault
        self.pointer = config.state_root / 'current.json'

    def _pointer(self):
        if not self.pointer.exists():
            return None
        pointer = read_json(self.pointer)
        if set(pointer) != {'generation', 'receipt_digest'}:
            raise reg.Conflict('authority_pointer_invalid', 'authority')
        identifier(pointer['generation'])
        return pointer

    def receipt(self):
        pointer = self._pointer()
        if pointer is None:
            if any(r['status'] in ('committed', 'rolled_back') for r in self.journal.list()):
                raise reg.Conflict('authority_pointer_lost', 'authority')
            receipt = read_json(self.config.adoption)
        else:
            receipt = read_json(self.config.state_root / 'receipts' / (pointer['generation'] + '.json'))
            if digest(encode(receipt)) != pointer['receipt_digest'] or receipt.get('generation') != pointer['generation']:
                raise reg.Conflict('authority_receipt_changed', 'authority')
        if (receipt.get('schemaVersion') != 1 or receipt.get('domain') != self.config.domain
                or not isinstance(receipt.get('ownership'), dict)
                or not isinstance(receipt.get('file_ownership'), dict)
                or not isinstance(receipt.get('authority'), dict)):
            raise reg.Conflict('authority_receipt_invalid', 'authority')
        identifier(receipt.get('generation'))
        for proof in receipt['ownership'].values():
            if (not isinstance(proof, dict) or proof.get('task_path') != '\\'
                    or 'identity' not in proof or (proof['identity'] is not None and
                    not re.fullmatch(r'task-definition:[a-f0-9]{64}', proof['identity']))):
                raise reg.Conflict('task_receipt_invalid', 'authority')
        return receipt

    def _inputs(self, reference):
        snapshot = self.vault.get(reference)
        if (snapshot.get('state') != 'present' or snapshot.get('identity') != 'effective-input-v1'
                or not isinstance(snapshot.get('value'), dict)
                or set(snapshot['value']) != {'submitted', 'request'}
                or any(not isinstance(v, dict) for v in snapshot['value'].values())):
            raise reg.Conflict('input_generation_invalid', 'authority')
        return snapshot['value']

    def load(self):
        submitted = read_json(self.config.desired)
        receipt = self.receipt()
        if 'input_reference' in receipt:
            inputs = self._inputs(receipt['input_reference'])
            request = _submitted_changes(inputs['submitted'], submitted, inputs['request'])
        else:
            if self._pointer() is not None:
                raise reg.Conflict('input_generation_missing', 'authority')
            request = deepcopy(submitted)  # Explicit initial adoption only.
        # Desired definitions never supply the current migrated set or ownership.
        request['machine']['authority_epoch'] = receipt['authority']['authority_epoch']
        request['machine']['migrated_tasks'] = receipt['authority']['migrated_tasks']
        return {'request': request, 'submitted_request': submitted,
                'ownership': receipt['ownership'], 'paths': self.config.paths,
                'active_xml': self.config.active_xml, 'file_ownership': receipt['file_ownership'],
                'launchers': self.config.launchers, 'linked_work_items': {},
                'authority_generation': receipt['generation']}

    def prepare_inputs(self, journal, bundle, selected, outputs):
        """Pin both input generations before any publication, without rereading projections."""
        before = deepcopy(bundle['request'])
        after = deepcopy(before)
        from .runtime_storage import decode
        machine = decode(outputs[self.config.paths['machine']])
        for key in ('authority_epoch', 'migrated_tasks'):
            after['machine'][key] = machine[key]
        for spec in selected:
            task_id = spec['task_id']
            after['bindings']['tasks'][task_id]['enabled'] = spec['enabled']
            override = after['machine']['overrides'].get(task_id, {})
            if 'enabled' in override:
                override['enabled'] = spec['enabled']
        for phase, request in (('before', before), ('after', after)):
            snapshot = {'state': 'present', 'identity': 'effective-input-v1',
                        'value': {'submitted': bundle['submitted_request'], 'request': request}}
            journal['input_' + phase] = self.vault.put(
                journal['transaction_id'] + ':input-' + phase, snapshot)

    def finish(self, runtime, journal, status):
        """Persist a receipt and conditional pointer before the terminal marker.

        A crash in this boundary resumes completion, not compensation. Every
        receipt binds observed filesystem identities and root task definitions.
        """
        if 'completion' not in journal:
            expected_files = {key: runtime.vault.get(ref) for key, ref in journal.get('file_before', {}).items()}
            expected_tasks = {key: runtime.vault.get(ref) for key, ref in journal.get('scheduler_before', {}).items()}
            for step in journal['steps']:
                reference = step.get('after') if status == 'committed' else step.get('undo', step['before'])
                if reference is not None:
                    (expected_files if step['kind'] == 'files' else expected_tasks)[step['key']] = runtime.vault.get(reference)
            # Rollback may have multiple Scheduler steps; the earliest restored
            # configuration is authoritative, with its final readback identity.
            if status == 'rolled_back':
                expected_files = reg._restored_files(runtime, journal)
                for step in reversed(journal['steps']):
                    if step['kind'] == 'scheduler':
                        expected_tasks[step['key']] = runtime.vault.get(step.get('undo', step['before']))
            reg._verify_files(runtime, expected_files)
            for name, expected in expected_tasks.items():
                current = reg._read(self.scheduler, name)
                reg._idle(current)
                if not reg._same('scheduler', current, expected):
                    raise reg.Conflict('receipt_task_changed', 'authority')
            receipt = deepcopy(self.receipt())
            reference = journal.get('input_after' if status == 'committed' else 'input_before')
            if reference is None:
                raise reg.Conflict('input_generation_missing', 'authority')
            machine = self._inputs(reference)['request']['machine']
            receipt['input_reference'] = reference
            receipt['generation'] = journal['transaction_id']
            receipt['authority'] = {k: machine[k] for k in ('authority_epoch', 'migrated_tasks')}
            # Preserve unselected proofs, advancing their epoch consistently.
            for proof in receipt['ownership'].values():
                proof['epoch'] = machine['authority_epoch']
            for task_id, proof in receipt['ownership'].items():
                if 'task:' + task_id in journal['locks']:
                    current = reg._read(self.scheduler, proof['name'])
                    reg._idle(current)
                    expected = expected_tasks.get(proof['name'])
                    if expected is None or not reg._same('scheduler', current, expected):
                        raise reg.Conflict('receipt_task_changed', 'authority')
                    proof['identity'] = current.get('identity')
                    proof['writer'] = 'declarations' if task_id in machine['migrated_tasks'] else 'legacy'
                    proof['receipt_kind'] = 'observed-root-task-definition'
            for path in journal.get('file_before', {}):
                current = reg._read(self.files, path)
                if current != expected_files[path]:
                    raise reg.Conflict('receipt_resource_changed', 'authority')
                receipt['file_ownership'][path] = reg.fingerprint(current)
            journal['completion'] = {'status': status, 'receipt': receipt, 'previous': self._pointer()}
            journal['status'] = 'committing'
            self.journal.save(journal['transaction_id'], journal)
            runtime.checkpoint('receipt_intent')
        completion = journal['completion']
        receipt = completion['receipt']
        inputs = self._inputs(receipt['input_reference'])
        if any(inputs['request']['machine'][key] != value for key, value in receipt['authority'].items()):
            raise reg.Conflict('input_authority_mismatch', 'authority')
        for path in journal.get('file_before', {}):
            if reg.fingerprint(reg._read(self.files, path)) != receipt['file_ownership'][path]:
                raise reg.Conflict('receipt_resource_changed', 'authority')
        for task_id, proof in receipt['ownership'].items():
            if 'task:' + task_id in journal['locks']:
                current = reg._read(self.scheduler, proof['name'])
                reg._idle(current)
                if current.get('identity') != proof['identity']:
                    raise reg.Conflict('receipt_task_changed', 'authority')
        path = self.config.state_root / 'receipts' / (identifier(receipt['generation']) + '.json')
        data = encode(receipt)
        if not fs.create_no_replace(path, data) and read_json(path) != receipt:
            raise reg.Conflict('receipt_conflict', 'authority')
        runtime.checkpoint('receipt_written')
        target = {'generation': receipt['generation'], 'receipt_digest': digest(data)}
        current = self._pointer()
        if current != target:
            if current != completion['previous']:
                raise reg.Conflict('authority_pointer_changed', 'authority')
            if current is None:
                if not fs.create_no_replace(self.pointer, encode(target)):
                    raise reg.Conflict('authority_pointer_changed', 'authority')
            else:
                fs.atomic_replace(self.pointer, encode(target))
        runtime.checkpoint('pointer_published')
        journal['status'] = completion['status']
        self.journal.save(journal['transaction_id'], journal)


def create_runtime(config, *, transport=None, protector=None, checkpoint=lambda point: None, linked_reader=None):
    """Trusted Python injection for tests; JSON cannot select imports or executables."""
    from .runtime_windows import COMTransport, DPAPIProtector
    from .runtime_xml import render
    resources = list(config.paths.values()) + list(config.launchers.values()) + [p for p in config.active_xml.values() if p]
    files = FileResources(resources, config.state_root / 'stages')
    scheduler = WindowsScheduler(transport if transport is not None else COMTransport())
    journal = JournalStore(config.state_root / 'journals')
    vault = ProtectedVault(config.vault_root, config.domain, protector if protector is not None else DPAPIProtector())
    authority = GenerationAuthority(config, files, scheduler, journal, vault)
    runtime = reg.Runtime(authority.load, files, scheduler, journal, vault,
                          ResourceLocks(config.state_root / 'locks'),
                          lambda spec, old, enabled: render(spec, old, enabled, config.launchers.get(spec['task_id'])),
                          checkpoint)
    runtime.finish = lambda record, status: authority.finish(runtime, record, status)
    runtime.authority = authority
    runtime.prepare_inputs = authority.prepare_inputs
    from .linked_items import read_linked_items
    runtime.linked_work_items = linked_reader if linked_reader is not None else read_linked_items
    def validate_control(spec, snapshot):
        from .contracts import SCHEDULER_FIELDS
        previous = snapshot['value']['spec']
        if any(previous.get(key) != spec.get(key) for key in SCHEDULER_FIELDS if key != 'enabled'):
            raise reg.Conflict('declaration_apply_required', 'scheduler')
    runtime.validate_control = validate_control
    return runtime


def dispatch_adapter(runtime):
    """Contract for HTTP/standalone wrappers: enclose the entire legacy action."""
    def dispatch(name, verb, *, legacy, reason=''):
        return reg.dispatch(name, verb, legacy=legacy, reason=reason, runtime=runtime)
    return dispatch
