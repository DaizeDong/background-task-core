"""Authority storage: file resources, journals, the protected vault, authority history and the
runtime that ties them to the scheduler.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from fleet_guards import filesystem as fs

from . import registry as reg
from .registry import Conflict, fingerprint, VARIABLE_REFERENCE, anchored_path, stray_percent
from .scheduler import WindowsScheduler

# ---- runtime_storage ---------------------------------------------------------------------------
# Persistent transaction storage in explicitly trusted, cooperative directories.
#
# Atomic publication, bounded reads and path rules belong to fleet_guards. Windows
# directory-entry power-loss durability and hostile ancestor swaps are not promised.
# No constructor provisions directories. Vault objects are retained, never GC'd here.


LIMIT = 8 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode('ascii')


def decode(data):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise Conflict('duplicate_json_key', 'storage')
            out[key] = value
        return out
    try:
        return json.loads(data, object_pairs_hook=unique)
    except (ValueError, UnicodeError, TypeError):
        raise Conflict('invalid_storage', 'storage') from None


def read_json(path):
    try:
        return decode(fs.read_bounded(path, LIMIT))
    except (OSError, ValueError):
        raise Conflict('storage_unreadable', 'storage') from None


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value):
        raise Conflict('invalid_identifier', 'storage')
    return value


class ResourceLocks:
    def __init__(self, root):
        self.root = fs.validate_path(root)

    @contextmanager
    def hold(self, keys):
        if not keys or any(not isinstance(k, str) or not k for k in keys):
            raise Conflict('invalid_lock_set', 'locks')
        with ExitStack() as stack:
            for key in sorted(set(keys)):
                path = fs.validate_path(self.root / (digest(key.casefold().encode()) + '.lock'))
                fs.create_no_replace(path, b'0')
                stream = stack.enter_context(path.open('r+b'))
                try:
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    raise Conflict('busy', 'locks') from None
                # Closing the descriptor releases its kernel lock after a crash.
            yield


class JournalStore:
    def __init__(self, root):
        self.root = fs.validate_path(root)

    def _path(self, key):
        return fs.validate_path(self.root / (identifier(key) + '.json'))

    def _validate(self, key, record):
        if (not isinstance(record, dict) or record.get('schemaVersion') != 1
                or record.get('transaction_id') != key
                or record.get('status') not in ('preparing', 'publishing', 'recovering',
                                              'conflict', 'committing', 'committed', 'rolled_back')
                or not isinstance(record.get('locks'), list)
                or 'authority' not in record['locks']
                or any(not isinstance(k, str) for k in record['locks'])
                or not isinstance(record.get('steps'), list)):
            raise Conflict('journal_invalid', 'journal')
        for step in record['steps']:
            if (step.get('kind') not in ('files', 'scheduler')
                    or not isinstance(step.get('token'), str)
                    or not step['token'].startswith(key + ':')
                    or step.get('phase') not in ('staging', 'staged', 'publishing', 'done', 'undoing', 'undone')):
                raise Conflict('journal_invalid', 'journal')
        def references(value):
            if isinstance(value, dict):
                for item in value.values():
                    references(item)
            elif isinstance(value, list):
                for item in value:
                    references(item)
            elif isinstance(value, str) and value.startswith('cred:') and value.split(':')[-1] != key:
                raise Conflict('reference_owner_mismatch', 'journal')
        references(record)
        return record

    def create(self, key, record):
        data = encode(self._validate(key, record))
        if len(data) > LIMIT or not fs.create_no_replace(self._path(key), data):
            raise Conflict('journal_create_conflict', 'journal')

    def save(self, key, record):
        self.load(key)  # Missing or corrupt ownership evidence cannot be adopted.
        data = encode(self._validate(key, record))
        if len(data) > LIMIT:
            raise Conflict('journal_limit', 'journal')
        fs.atomic_replace(self._path(key), data)

    def load(self, key):
        return self._validate(key, read_json(self._path(key)))

    def list(self):
        if not self.root.exists():
            return []  # Unprovisioned read-only status.
        fs.validate_path(self.root)
        try:
            paths = list(self.root.iterdir())
        except OSError:
            raise Conflict('journal_enumeration_failed', 'journal') from None
        records = []
        for path in sorted(paths):
            if path.name.startswith('.fleet-guards-'):
                raise Conflict('journal_write_interrupted', 'journal')
            if path.suffix != '.json':
                raise Conflict('journal_unknown_entry', 'journal')
            records.append(self.load(path.stem))
        return records

    def pending(self, keys):
        return [r['transaction_id'] for r in self.list() if set(keys) & set(r['locks'])
                and (r['status'] not in ('committed', 'rolled_back') or not r.get('cleaned'))]


class ProtectedVault:
    """Per-object SecureString .cred storage; protector is supplied by trusted code.

    References bind the storage domain, immutable object and plaintext envelope.
    DPAPI rewrapping by CONFIG preserves references because ciphertext is not the
    identity. Loss, replacement or copying between domains fails closed.
    """
    def __init__(self, root, domain, protector):
        self.root = fs.validate_path(root)
        self.domain = identifier(domain)
        self.protector = protector

    def put(self, key, snapshot):
        if not isinstance(key, str) or not re.match(r'^[a-f0-9]{32}:', key):
            raise Conflict('vault_owner_required', 'vault')
        value = deepcopy(snapshot)
        binary = isinstance(value.get('value'), bytes)
        if binary:
            value['value'] = base64.b64encode(value['value']).decode('ascii')
        obj = uuid.uuid4().hex
        envelope = encode({'domain': self.domain, 'object': obj, 'owner': key,
                           'binary': binary, 'snapshot': value})
        ref = ':'.join(('cred', self.domain, obj, digest(envelope), key.split(':')[0]))
        data = self.protector.protect(envelope)
        if not isinstance(data, bytes) or len(data) > LIMIT:
            raise Conflict('secure_storage_failed', 'vault')
        path = self.root / ('background-task-core-' + self.domain + '-' + obj + '.cred')
        if not fs.create_no_replace(path, data):
            raise Conflict('secure_storage_conflict', 'vault')
        if self.get(ref) != snapshot:
            raise Conflict('secure_storage_unverified', 'vault')
        return ref

    def get(self, ref):
        try:
            prefix, domain, obj, checksum, owner = ref.split(':')
            if prefix != 'cred' or domain != self.domain or not re.fullmatch('[a-f0-9]{64}', checksum):
                raise ValueError()
            identifier(obj)
            path = self.root / ('background-task-core-' + self.domain + '-' + obj + '.cred')
            raw = self.protector.unprotect(fs.read_bounded(path, LIMIT))
            value = decode(raw)
            if (digest(raw) != checksum or value['domain'] != domain or value['object'] != obj
                    or not value['owner'].startswith(identifier(owner) + ':')):
                raise ValueError()
            snapshot = value['snapshot']
            if value['binary']:
                snapshot['value'] = base64.b64decode(snapshot['value'], validate=True)
            return snapshot
        except Conflict as exc:
            if exc.code in ('transport_timeout', 'transport_cancelled', 'transport_cleanup_failed'):
                raise
            raise Conflict('secure_reference_unavailable', 'vault') from None
        except Exception:
            raise Conflict('secure_reference_unavailable', 'vault') from None


class FileResources:
    def __init__(self, paths, stage_root):
        self.paths = {str(fs.validate_path(p)) for p in paths}
        self.root = fs.validate_path(stage_root)

    def _path(self, key):
        path = fs.validate_path(key)
        if str(path) not in self.paths:
            raise Conflict('unowned_resource_path', 'files')
        return path

    def read(self, key):
        path = self._path(key)
        try:
            before = fs.identity(path)
            data = fs.read_bounded(path, LIMIT)
            if fs.identity(path) != before:
                raise Conflict('resource_changed', 'files')
            return {'state': 'present', 'identity': json.dumps(before), 'value': data}
        except FileNotFoundError:
            return {'state': 'absent'}

    def _stage(self, token):
        if not re.fullmatch(r'[a-f0-9]{32}:\d+(?::undo)?', token):
            raise Conflict('invalid_stage_owner', 'files')
        return self.root / (digest(token.encode()) + '.json')

    def staging_path(self, key, token):
        """The existing deterministic payload path; token/key validation is shared."""
        self._stage(token)
        return self._path(key).with_name('.t12-stage-' + digest(token.encode()))

    def prepare(self, key, value, token):
        path = self._path(key)
        record_path = self._stage(token)
        stage = self.staging_path(key, token)
        record = {'token': token, 'key': str(path), 'stage': str(stage), 'identity': None,
                  'digest': None if value is None else digest(value)}
        if not fs.create_no_replace(record_path, encode(record)):
            existing = read_json(record_path)
            if any(existing.get(k) != record[k] for k in ('token', 'key', 'stage', 'digest')):
                raise Conflict('stage_owner_lost', 'files')
            if value is None:
                if existing.get('identity') is not None or stage.exists():
                    raise Conflict('stage_owner_lost', 'files')
                return {'state': 'absent'}
            # A receipt without identity cannot prove ownership of an orphan.
            if (existing.get('identity') is None or not stage.exists()
                    or fs.identity(stage) != existing['identity']
                    or fs.read_bounded(stage, LIMIT) != value):
                raise Conflict('stage_owner_lost', 'files')
            return {'state': 'present', 'identity': json.dumps(existing['identity']), 'value': value}
        if value is None:
            return {'state': 'absent'}
        opened = fs.create_no_replace_with_identity(stage, value)
        if opened is None:
            raise Conflict('stage_conflict', 'files')
        record['identity'] = opened
        fs.atomic_replace(record_path, encode(record))
        return {'state': 'present', 'identity': json.dumps(opened), 'value': value}

    def _find(self, key, desired):
        for path in self.root.glob('*.json'):
            record = read_json(path)
            if record['key'] == key and (record['identity'] ==
                    (json.loads(desired['identity']) if desired['state'] == 'present' else None)):
                return path, record
        raise Conflict('stage_receipt_missing', 'files')

    def publish(self, key, expected, desired):
        path = self._path(key)
        record_path, record = self._find(str(path), desired)
        if self.read(key) != expected:
            raise Conflict('resource_changed', 'files')
        detached = path.with_name('.t12-detached-' + digest(record['token'].encode()))
        record['expected'] = fingerprint(expected)
        record['detached'] = str(detached)
        fs.atomic_replace(record_path, encode(record))
        if expected['state'] == 'present':
            if not fs.detach_if_matches(path, expected['value'], json.loads(expected['identity']), detached):
                raise Conflict('conditional_detach_failed', 'files')
        if desired['state'] == 'present':
            stage = fs.validate_path(record['stage'])
            if (fs.identity(stage) != record['identity'] or fs.read_bounded(stage, LIMIT) != desired['value']
                    or not fs.link_no_replace(stage, path)):
                raise Conflict('conditional_create_failed', 'files')
        fs.sync_directory(path.parent)

    def recover_incomplete(self, key, expected, desired):
        """Restore a proven detached before-image in an observed empty vacancy."""
        if self.read(key)['state'] != 'absent' or expected['state'] != 'present':
            return False
        _, record = self._find(str(self._path(key)), desired)
        if record.get('expected') != fingerprint(expected) or 'detached' not in record:
            return False
        retained = fs.validate_path(record['detached'])
        if (fs.identity(retained) != json.loads(expected['identity'])
                or fs.read_bounded(retained, LIMIT) != expected['value']):
            raise Conflict('detached_owner_lost', 'files')
        return fs.link_no_replace(retained, self._path(key))

    def cleanup(self, token):
        record_path = self._stage(token)
        if not record_path.exists():
            return
        record = read_json(record_path)
        key = self._path(record['key'])
        if record['token'] != token:
            raise Conflict('stage_owner_lost', 'files')
        for role, prefix in (('stage', '.t12-stage-'), ('detached', '.t12-detached-')):
            if role not in record:
                continue
            path = fs.validate_path(record[role])
            if path != key.with_name(prefix + digest(token.encode())):
                raise Conflict('stage_owner_lost', 'files')
            if path.exists():
                # Staging must have a durable identity. Interrupted preparation
                # before its receipt is ambiguous and is retained for review.
                if role == 'stage' and (record['identity'] is None or fs.identity(path) != record['identity']
                                       or digest(fs.read_bounded(path, LIMIT)) != record['digest']):
                    raise Conflict('stage_owner_lost', 'files')
                if role == 'detached':
                    snap = {'state': 'present', 'identity': json.dumps(fs.identity(path)),
                            'value': fs.read_bounded(path, LIMIT)}
                    if fingerprint(snap) != record.get('expected'):
                        raise Conflict('stage_owner_lost', 'files')
                path.unlink()
                fs.sync_directory(path.parent)
        record_path.unlink()
        fs.sync_directory(record_path.parent)


# ---- authority_history -------------------------------------------------------------------------
# Verify unchanged, initially adopted proofs for explicitly reviewed absent tasks.


def _fail():
    raise reg.Conflict('historical_task_evidence_invalid', 'historical_task_ids')


def _without_epoch(proof):
    return {key: value for key, value in proof.items() if key != 'epoch'}


def _proofs(receipt, expected):
    ownership = receipt.get('ownership')
    authority = receipt.get('authority')
    if (not isinstance(ownership, dict) or not isinstance(authority, dict)
            or type(authority.get('authority_epoch')) is not int):
        _fail()
    names = []
    for proof in ownership.values():
        if not isinstance(proof, dict) or not isinstance(proof.get('name'), str):
            _fail()
        names.append(proof['name'].casefold())
    if len(names) != len(set(names)):
        _fail()
    for task_id, original in expected.items():
        proof = ownership.get(task_id)
        if (not isinstance(proof, dict) or type(proof.get('epoch')) is not int
                or proof.get('epoch') != authority.get('authority_epoch')
                or _without_epoch(proof) != original):
            _fail()


def _bootstrap(runtime, receipt, journal, expected, inputs):
    from . import lifecycle as adoption

    bootstrap = journal.get('bootstrap')
    if not isinstance(bootstrap, dict) or 'completion' in journal:
        _fail()
    plan = bootstrap.get('plan')
    if not isinstance(plan, dict):
        _fail()
    adoption._approval(plan, receipt.get('bootstrap_plan_revision'))
    if (receipt.get('authority') != {'authority_epoch': 0, 'migrated_tasks': []}
            or bootstrap.get('input_reference', receipt.get('input_reference')) != receipt.get('input_reference')):
        _fail()
    rows = plan.get('tasks')
    if (not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
            or len({row.get('task_id') for row in rows}) != len(rows)):
        _fail()
    indexed = {row.get('task_id'): row for row in rows}
    if inputs['request'] != inputs['submitted']:
        _fail()
    specs = {spec['task_id']: spec for spec in reg._compile({'request': inputs['request']})['task_specs']}
    if set(indexed) != set(specs) or set(indexed) != set(receipt['ownership']):
        _fail()
    for task_id, original in expected.items():
        row, spec = indexed[task_id], specs[task_id]
        proof = {'name': row.get('name'), 'task_path': row.get('task_path'), 'writer': 'legacy',
                 'identity': row.get('identity'), 'enabled': row.get('enabled'),
                 'state': row.get('state'), 'receipt_kind': 'observed-root-task-definition'}
        if (proof != original or proof['name'] != spec['name'] or proof['task_path'] != '\\'
                or proof['state'] != 'present' or not isinstance(proof['identity'], str)):
            _fail()
    config = runtime.authority.config
    paths = [str(config.adoption), str(config.state_root / 'receipts' / (receipt['generation'] + '.json')),
             str(runtime.authority.pointer)]
    steps = journal['steps']
    if len(steps) != 3 or [step.get('key') for step in steps] != paths:
        _fail()
    for step in steps:
        if (step.get('kind') != 'files' or step.get('phase') != 'done'
                or runtime.vault.get(step['before']) != {'state': 'absent'}):
            _fail()
    # Protected publication images bind the bootstrap receipt independently of
    # editable journal prose, including the original protected input reference.
    for step in steps[:2]:
        if reg._document(runtime.vault.get(step['after'])) != receipt:
            _fail()
    pointer = {'generation': receipt['generation'], 'receipt_digest': digest(encode(receipt))}
    if reg._document(runtime.vault.get(steps[2]['after'])) != pointer:
        _fail()
    return reg.revision(inputs)


def _predecessor(runtime, previous, journal, previous_inputs):
    """Check the protected before-state against the explicitly reviewed predecessor."""
    before = runtime.authority._inputs(journal['input_before'])
    if isinstance(journal.get('reconcile'), dict):
        reference = journal['reconcile']['evidence']['receipt']
        snapshot = runtime.vault.get(reference)
        if (snapshot.get('state') != 'present' or snapshot.get('identity') != 'authority-receipt-v1'
                or decode(snapshot['value']) != previous or before != previous_inputs):
            _fail()
    else:
        pass
        request = _submitted_changes(previous_inputs['submitted'], before['submitted'], previous_inputs['request'])
        request['machine'].update(previous['authority'])
        if before['request'] != request:
            _fail()
        for path, reference in journal.get('file_before', {}).items():
            if reg.fingerprint(runtime.vault.get(reference)) != previous['file_ownership'].get(path):
                _fail()
        names = {proof['name']: proof for proof in previous['ownership'].values()}
        for name, reference in journal.get('scheduler_before', {}).items():
            snapshot = runtime.vault.get(reference)
            if name not in names or snapshot.get('identity') != names[name]['identity']:
                _fail()
    return reg.revision(before)


def validate(runtime, receipt, task_ids, reviewed_chain):
    """Verify an explicitly reviewed history sequence and its protected evidence."""
    expected = {task_id: _without_epoch(receipt['ownership'][task_id]) for task_id in task_ids}
    config = runtime.authority.config
    pointer = {'generation': receipt['generation'], 'receipt_digest': digest(encode(receipt))}
    seen, files, inputs_seen = set(), {}, {}
    first = deepcopy(receipt)
    child = None
    while True:
        if not isinstance(pointer, dict) or set(pointer) != {'generation', 'receipt_digest'}:
            _fail()
        generation = identifier(pointer['generation'])
        if generation in seen or len(seen) >= len(reviewed_chain):
            _fail()
        reviewed = reviewed_chain[len(seen)]
        if {key: reviewed[key] for key in ('generation', 'receipt_digest')} != pointer:
            _fail()
        seen.add(generation)
        receipt_path = config.state_root / 'receipts' / (generation + '.json')
        journal_path = config.state_root / 'journals' / (generation + '.json')
        resources = FileResources([receipt_path, journal_path], config.state_root / 'stages')
        documents = []
        for path in (receipt_path, journal_path):
            snapshot = reg._read(resources, str(path))
            if snapshot['state'] != 'present':
                _fail()
            documents.append(decode(snapshot['value']))
            files[str(path)] = reg.fingerprint(snapshot)
        current, journal = documents
        if not isinstance(current, dict) or files[str(journal_path)] != reviewed['journal_fingerprint']:
            _fail()
        runtime.journal._validate(generation, journal)
        if (current.get('generation') != generation or current.get('domain') != config.domain
                or digest(encode(current)) != pointer['receipt_digest']
                or (len(seen) == 1 and current != first)
                or journal.get('status') not in ('committed', 'rolled_back')
                or journal.get('cleaned') is not True):
            _fail()
        _proofs(current, expected)
        inputs = runtime.authority._inputs(current['input_reference'])
        machine = inputs['request']['machine']
        if current['authority'] != {key: machine.get(key) for key in ('authority_epoch', 'migrated_tasks')}:
            _fail()
        inputs_seen[generation] = {'effective': reg.revision(inputs)}
        if child is not None:
            inputs_seen[child['transaction_id']]['before'] = _predecessor(runtime, current, child, inputs)
        if 'bootstrap' in journal:
            if journal['status'] != 'committed' or len(seen) != len(reviewed_chain):
                _fail()
            inputs_revision = _bootstrap(runtime, current, journal, expected, inputs)
            return {'task_ids': sorted(task_ids), 'adoption_generation': generation,
                    'initial_inputs_revision': inputs_revision, 'inputs': inputs_seen, 'files': files}
        completion = journal.get('completion')
        reference = journal.get('input_after' if journal['status'] == 'committed' else 'input_before')
        if (not isinstance(completion, dict) or set(completion) != {'status', 'receipt', 'previous'}
                or completion['status'] != journal['status'] or completion['receipt'] != current
                or reference != current.get('input_reference')):
            _fail()
        pointer = completion['previous']
        child = journal


# ---- runtime -----------------------------------------------------------------------------------
# Explicit runtime composition and generation authority. Import performs no I/O.


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
    source_path: Path
    # What a scheduled action names to reach each launcher: the %NAME%-anchored
    # launcher_root_reference plus the launcher's relative path, so the registered
    # task names no machine path.
    launcher_arguments: dict

    def launcher_argument(self, task_id):
        return self.launcher_arguments.get(task_id)

    @classmethod
    def read(cls, path, *, private_root, state_root, vault_root):
        """Roots are trusted caller arguments, never inferred from declarations."""
        private = fs.validate_path(private_root)
        state = fs.validate_path(state_root)
        vault = fs.validate_path(vault_root)
        source_path = fs.validate_path(path, root=private)
        document = read_json(source_path)
        required = {'schemaVersion', 'domain', 'desired', 'adoption', 'paths', 'active_xml', 'launchers',
                    'launcher_root_reference'}
        if set(document) != required or document['schemaVersion'] != 1:
            raise reg.Conflict('invalid_runtime_config', 'runtime')
        reference = document['launcher_root_reference']
        if (not isinstance(reference, str) or not VARIABLE_REFERENCE.match(reference)
                or not anchored_path(reference) or stray_percent(reference)
                or any(c in reference for c in '\r\n\x00"')):
            raise reg.Conflict('invalid_launcher_root_reference', 'runtime')
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
        # The same relative paths that resolve() placed under the private root, in Windows form.
        arguments = {k: reference.rstrip('\\/') + '\\' + v.replace('/', '\\').strip('\\')
                     for k, v in document['launchers'].items()}
        return cls(private, state, vault, identifier(document['domain']), desired, adoption, paths, exports, launchers,
                   source_path, arguments)


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
        pass
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
            # Reconcile journals record scheduler_before only when they review Scheduler drift.
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
            # A task adopted in this transaction has no proof yet; the loop below fills it in.
            request = self._inputs(reference)['request']
            for lock in journal['locks']:
                task_id = lock[len('task:'):] if lock.startswith('task:') else None
                if task_id and task_id not in receipt['ownership']:
                    name = request['bindings']['tasks'][task_id]['name']
                    current = reg._read(self.scheduler, name)
                    receipt['ownership'][task_id] = {
                        'name': name, 'task_path': '\\', 'identity': None, 'writer': 'legacy',
                        'state': current.get('state'), 'enabled': current.get('value', {}).get('enabled')}
            # Preserve unselected proofs, advancing their epoch consistently.
            for proof in receipt['ownership'].values():
                proof['epoch'] = machine['authority_epoch']
            for task_id, proof in receipt['ownership'].items():
                if 'task:' + task_id in journal['locks']:
                    current = reg._read(self.scheduler, proof['name'])
                    reg._idle(current)
                    expected = expected_tasks.get(proof['name'])
                    if expected is None and 'scheduler_before' not in journal and status == 'rolled_back':
                        # An unchanged old task has no step before-image. Its
                        # existing authority proof must still match exactly.
                        if current.get('identity') != proof['identity']:
                            raise reg.Conflict('receipt_task_changed', 'authority')
                        expected = current
                    if expected is None or not reg._same('scheduler', current, expected):
                        raise reg.Conflict('receipt_task_changed', 'authority')
                    proof['identity'] = current.get('identity')
                    proof['writer'] = 'declarations' if task_id in machine['migrated_tasks'] else 'legacy'
                    proof['receipt_kind'] = 'observed-root-task-definition'
            for path in expected_files:
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
        for path in journal.get('file_before', receipt['file_ownership']):
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


def create_runtime(config):
    """Compose the Windows adapters; JSON cannot select imports or executables."""
    from .integrations import read_linked_items
    from .scheduler import COMTransport, DPAPIProtector
    from .scheduler import render
    resources = list(config.paths.values()) + list(config.launchers.values()) + [p for p in config.active_xml.values() if p]
    files = FileResources(resources, config.state_root / 'stages')
    scheduler = WindowsScheduler(COMTransport())
    journal = JournalStore(config.state_root / 'journals')
    vault = ProtectedVault(config.vault_root, config.domain, DPAPIProtector())
    authority = GenerationAuthority(config, files, scheduler, journal, vault)
    return reg.Runtime(authority.load, files, scheduler, journal, vault,
                       ResourceLocks(config.state_root / 'locks'),
                       lambda spec, old, enabled: render(spec, old, enabled, config.launcher_argument(spec['task_id'])),
                       authority, read_linked_items)


def export_registration_recovery(runtime, output, *, launcher_binding=None):
    """Capture authority, pending transactions and their protected reference closure.

    This is recovery evidence, not a task-registration restore operation. Local
    ciphertext and filesystem identities remain bound to the original host.
    """
    import tempfile
    import zipfile
    from .runs import check_write_path

    output = check_write_path(output)
    config = runtime.authority.config
    if any(output.is_relative_to(root) for root in (config.state_root, config.vault_root)):
        raise ValueError('Recovery output must be outside active state and vault roots')
    members, sources, documents, refs = {}, {}, [], set()

    def capture(path, member, *, optional=False):
        path = fs.validate_path(path)
        snapshot = FileResources([path], config.state_root / 'stages').read(str(path))
        if path in sources and sources[path] != snapshot:
            raise Conflict('recovery_source_changed', 'storage')
        sources[path] = snapshot
        documents.append({'source': str(path), 'member': member, 'state': snapshot['state']})
        if snapshot['state'] == 'absent':
            if not optional:
                raise Conflict('recovery_source_missing', 'storage')
            return None
        members[member] = snapshot['value']
        return snapshot['value']

    def references(value):
        if isinstance(value, dict):
            for item in value.values():
                references(item)
        elif isinstance(value, list):
            for item in value:
                references(item)
        elif isinstance(value, str) and value.startswith('cred:'):
            if not re.fullmatch(r'cred:[a-f0-9]{32}:[a-f0-9]{32}:[a-f0-9]{64}:[a-f0-9]{32}', value):
                raise Conflict('secure_reference_unavailable', 'vault')
            refs.add(value)

    with runtime.locks.hold(['authority']):
        receipt = runtime.authority.receipt()
        pointer = runtime.authority._pointer()
        raw_pointer = capture(config.state_root / 'current.json', 'state/current.json', optional=pointer is None)
        if (decode(raw_pointer) if raw_pointer is not None else None) != pointer:
            raise Conflict('recovery_source_changed', 'storage')
        if pointer is not None:
            raw_receipt = capture(config.state_root / 'receipts' / (pointer['generation'] + '.json'),
                                  'state/receipts/' + pointer['generation'] + '.json')
            if decode(raw_receipt) != receipt:
                raise Conflict('recovery_source_changed', 'storage')
        references(receipt)
        paths = [config.source_path, config.desired, config.adoption]
        paths += list(map(Path, config.paths.values())) + list(map(Path, config.launchers.values()))
        paths += [Path(path) for path in config.active_xml.values() if path]
        for path in dict.fromkeys(paths):
            raw = capture(path, 'private/' + path.relative_to(config.private_root).as_posix(),
                          optional=path not in (config.source_path, config.desired, config.adoption))
            if path == config.adoption and pointer is None and decode(raw) != receipt:
                raise Conflict('recovery_source_changed', 'storage')
        if launcher_binding is not None:
            capture(launcher_binding, 'launcher-binding.json')
        pending = []
        for journal in runtime.journal.list():
            if journal['status'] in ('committed', 'rolled_back') and journal.get('cleaned') is True:
                continue
            tx = journal['transaction_id']
            pending.append(tx)
            raw = capture(config.state_root / 'journals' / (tx + '.json'), 'state/journals/' + tx + '.json')
            if decode(raw) != journal:
                raise Conflict('recovery_source_changed', 'storage')
            references(journal)
            for step in journal['steps']:
                if step['kind'] != 'files':
                    continue
                for token in (step['token'], step['token'] + ':undo'):
                    stage_path = runtime.files._stage(token)
                    raw = capture(stage_path, 'state/stages/' + stage_path.name, optional=True)
                    if raw is None:
                        continue
                    record = decode(raw)
                    key = runtime.files._path(step['key'])
                    for role, prefix in (('stage', '.t12-stage-'), ('detached', '.t12-detached-')):
                        if role in record:
                            path = key.with_name(prefix + digest(token.encode()))
                            if str(path) != record[role]:
                                raise Conflict('stage_owner_lost', 'files')
                            capture(path, 'private/' + path.relative_to(config.private_root).as_posix(), optional=True)
        captured_refs = set()
        while refs - captured_refs:
            ref = sorted(refs - captured_refs)[0]
            _, domain, obj, _, _ = ref.split(':')
            filename = 'background-task-core-' + domain + '-' + obj + '.cred'
            capture(config.vault_root / filename, 'vault/' + filename)
            snapshot = runtime.vault.get(ref)
            references(snapshot)
            if snapshot.get('identity') == 'authority-receipt-v1':
                references(decode(snapshot['value']))
            captured_refs.add(ref)
        if output in sources:
            raise ValueError('Recovery output would overwrite an input')
        manifest = {'schema_version': 1, 'kind': 'background-task-core-registration-recovery',
                    'generation': receipt['generation'], 'domain': config.domain,
                    'documents': documents, 'pending_journals': pending,
                    'protected_references': sorted(captured_refs), 'machine_bound': True,
                    'restore_limit': 'Ciphertext needs the original Windows account and machine. '
                                     'Copied files do not preserve filesystem identities. '
                                     'This capture does not register tasks or restore terminal authority history.'}
        members['manifest.json'] = encode(manifest)
        with tempfile.NamedTemporaryFile(prefix='registration-', suffix='.zip', dir=output.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
        try:
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in sorted(members.items()):
                    archive.writestr(name, data)
            # The authority lock serializes runtime writers; snapshot comparison
            # also catches externally edited declaration and launcher files.
            for path, snapshot in sources.items():
                if FileResources([path], config.state_root / 'stages').read(str(path)) != snapshot:
                    raise Conflict('recovery_source_changed', 'storage')
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    return {'status': 'captured', 'output': str(output), 'generation': receipt['generation'],
            'pending_journals': len(pending), 'protected_references': len(captured_refs),
            'machine_bound': True}


def backup_database(output, *, source=None):
    """Back up every SQLite table, including committed WAL data, without ingesting."""
    import sqlite3
    import tempfile
    import zipfile
    from contextlib import closing
    from .runs import check_write_path, resolve_db

    if source is None:
        source, unavailable = resolve_db()
        if unavailable is not None:
            raise ValueError(unavailable.message)
    source = check_write_path(source)
    output = check_write_path(output)
    if source == output or str(output) in {str(source) + suffix for suffix in ('-wal', '-shm', '-journal')}:
        raise ValueError('Backup output must not overwrite the live database or its sidecars')
    with tempfile.NamedTemporaryFile(prefix='database-', suffix='.sqlite3', dir=output.parent,
                                     delete=False) as stream:
        temporary = Path(stream.name)
    compressed = None
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
            with closing(sqlite3.connect(temporary)) as backup:
                original.backup(backup)
                # Make the backup standalone even when the source uses WAL.
                backup.execute('PRAGMA journal_mode=DELETE')
        if output.suffix == '.zip':
            with tempfile.NamedTemporaryFile(prefix='database-', suffix='.zip', dir=output.parent,
                                             delete=False) as stream:
                compressed = Path(stream.name)
            with zipfile.ZipFile(compressed, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(temporary, 'console.sqlite3')
            os.replace(compressed, output)
        else:
            os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
        if compressed is not None:
            compressed.unlink(missing_ok=True)
    return {'status': 'captured', 'output': str(output), 'bytes': output.stat().st_size}


def backup_main(argv=None):
    """Create an on-demand private database or registration recovery snapshot."""
    import argparse
    from datetime import datetime, timezone
    from .runs import companion_root, check_write_path

    parser = argparse.ArgumentParser(description=backup_main.__doc__)
    parser.add_argument('kind', choices=('database', 'registration'))
    parser.add_argument('--output', help='Private output file; defaults to companion data/recovery')
    parser.add_argument('--source', help='Database override')
    parser.add_argument('--launcher-binding', help='Launcher binding to include in registration backup')
    for name in ('runtime-config', 'private-root', 'state-root', 'vault-root'):
        parser.add_argument('--' + name)
    args = parser.parse_args(argv)
    if args.output:
        output = check_write_path(args.output)
    else:
        root = companion_root()
        if root is None:
            raise ValueError('Configure the private companion before creating backups; see CONFIG.md')
        directory = check_write_path(root / 'data' / 'recovery')
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        output = directory / (stamp + ('-console.sqlite3.zip' if args.kind == 'database' else '-registration.zip'))
    if args.kind == 'database':
        result = backup_database(output, source=args.source)
    else:
        from .lifecycle import load_runtime
        runtime = load_runtime(runtime_config=args.runtime_config, private_root=args.private_root,
                               state_root=args.state_root, vault_root=args.vault_root, for_recovery=True)
        result = export_registration_recovery(runtime, output, launcher_binding=args.launcher_binding)
    print(json.dumps(result, sort_keys=True))
    return 0
