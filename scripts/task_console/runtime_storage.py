"""Persistent transaction storage in explicitly trusted, cooperative directories.

Atomic publication, bounded reads and path rules belong to fleet_guards. Windows
directory-entry power-loss durability and hostile ancestor swaps are not promised.
No constructor provisions directories. Vault objects are retained, never GC'd here.
"""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from fleet_guards import filesystem as fs
from .registration import Conflict, fingerprint

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

    get = load

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
        path = self.root / ('task-console-' + self.domain + '-' + obj + '.cred')
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
            path = self.root / ('task-console-' + self.domain + '-' + obj + '.cred')
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

    def prepared_snapshot(self, key, token):
        """Read a durably owned stage without provisioning or adopting an orphan."""
        path = self._path(key)
        record_path = self._stage(token)
        stage = self.staging_path(key, token)
        if not record_path.exists():
            if stage.exists():
                raise Conflict('stage_owner_lost', 'files')
            return None
        record = read_json(record_path)
        if (not isinstance(record, dict) or
                not {'token', 'key', 'stage', 'identity', 'digest'} <= set(record)):
            raise Conflict('stage_owner_lost', 'files')
        if any(record.get(k) != v for k, v in
               {'token': token, 'key': str(path), 'stage': str(stage)}.items()):
            raise Conflict('stage_owner_lost', 'files')
        snapshot = FileResources([stage], self.root).read(str(stage))
        if record.get('digest') is None:
            if record.get('identity') is not None or snapshot['state'] != 'absent':
                raise Conflict('stage_owner_lost', 'files')
        elif (snapshot['state'] != 'present' or record.get('identity') is None
              or snapshot['identity'] != json.dumps(record['identity'])
              or digest(snapshot['value']) != record['digest']):
            raise Conflict('stage_owner_lost', 'files')
        return snapshot

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
