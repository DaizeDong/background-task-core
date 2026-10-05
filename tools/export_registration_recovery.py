"""Export the current registration reference closure as machine-bound ciphertext."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

from storage_retention import REPO, ordinary_path, private_data_root

LIMIT = 8 * 1024 * 1024
TOTAL_LIMIT = 128 * 1024 * 1024
IDENTIFIER = re.compile(r"[a-f0-9]{32}")
REFERENCE = re.compile(r"cred:([a-f0-9]{32}):([a-f0-9]{32}):([a-f0-9]{64}):([a-f0-9]{32})")
STATUSES = {"preparing", "publishing", "recovering", "conflict", "committing", "committed", "rolled_back"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    # This is the registration receipt's persisted encoding, including spaces.
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("ascii")


def decode(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate registration JSON key")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique)


def absolute_path(value):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Explicit absolute recovery paths required")
    path = ordinary_path(path)
    if path == REPO or REPO in path.parents:
        raise ValueError("Registration recovery DATA cannot use the public tool repository")
    return path


def read(path):
    path = ordinary_path(path)
    before = path.stat()
    if before.st_size > LIMIT:
        raise ValueError("Registration recovery input exceeds the bounded file limit")
    data = path.read_bytes()
    after = path.stat()
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if len(data) > LIMIT or any(getattr(before, k) != getattr(after, k) for k in fields):
        raise ValueError("Registration recovery source changed during read")
    return data


def verify_output_boundary(output):
    """Allow transient non-worktree staging; require PRIVATE for any worktree."""
    output = ordinary_path(output)
    for parent in output.parents:
        if (parent / ".git").exists():
            private_data_root(parent)
            break


@contextmanager
def authority_lock(state):
    # Use the same lock key/path as runtime_storage.ResourceLocks.
    path = ordinary_path(state / "locks" / (digest(b"authority") + ".lock"))
    with path.open("r+b") as stream:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def references(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)
    elif isinstance(value, str) and value.startswith("cred:"):
        if not REFERENCE.fullmatch(value):
            raise ValueError("Malformed protected registration reference")
        yield value


def collect(private, state, vault):
    """Follow current authority and incomplete journals, never terminal history."""
    sources = {}
    members = {}
    documents = []

    def document(path, member, role):
        raw = read(path)
        sources[path] = raw
        members[member] = raw
        documents.append({"role": role, "source": str(path), "member": member,
                          "bytes": len(raw), "sha256": digest(raw)})
        value = decode(raw)
        if not isinstance(value, dict):
            raise ValueError("Registration documents must be JSON objects")
        return value

    pointer = document(state / "current.json", "state/current.json", "current-pointer")
    if (set(pointer) != {"generation", "receipt_digest"}
            or not isinstance(pointer["generation"], str)
            or not IDENTIFIER.fullmatch(pointer["generation"])):
        raise ValueError("Invalid current registration pointer")
    generation = pointer["generation"]
    receipt = document(state / "receipts" / (generation + ".json"),
                       "state/receipts/" + generation + ".json", "current-receipt")
    domain = receipt.get("domain")
    if (receipt.get("schemaVersion") != 1 or receipt.get("generation") != generation
            or not isinstance(domain, str) or not IDENTIFIER.fullmatch(domain)
            or digest(encode(receipt)) != pointer["receipt_digest"]):
        raise ValueError("Current registration receipt does not match its pointer")
    if not isinstance(receipt.get("input_reference"), str):
        raise ValueError("Current registration input reference is missing")
    refs = set(references(receipt))
    if receipt["input_reference"] not in refs:
        raise ValueError("Current registration input reference is invalid")
    journal_paths = sorted((state / "journals").glob("*.json"))
    if len(journal_paths) > 4096:
        raise ValueError("Registration journal inventory exceeds the bounded limit")
    pending = []
    for path in journal_paths:
        raw = read(path)
        sources[path] = raw
        journal = decode(raw)
        if (not isinstance(journal, dict) or journal.get("schemaVersion") != 1
                or journal.get("transaction_id") != path.stem
                or not IDENTIFIER.fullmatch(path.stem) or journal.get("status") not in STATUSES):
            raise ValueError("Invalid registration journal inventory")
        if journal["status"] in {"committed", "rolled_back"} and journal.get("cleaned") is True:
            continue
        pending.append(path.stem)
        document(path, "state/journals/" + path.name, "pending-journal")
        refs.update(references(journal))
    if len(refs) > 1024:
        raise ValueError("Registration reference closure exceeds the bounded limit")
    protected = []
    for ref in sorted(refs):
        match = REFERENCE.fullmatch(ref)
        if match.group(1) != domain:
            raise ValueError("Protected reference belongs to another registration domain")
        filename = "task-console-" + domain + "-" + match.group(2) + ".cred"
        path = vault / filename
        raw = read(path)  # Missing references are errors before the output is replaced.
        if not raw:
            raise ValueError("Protected registration ciphertext is empty")
        sources[path] = raw
        member = "vault/" + filename
        if member in members:
            raise ValueError("Aliased protected registration references")
        members[member] = raw
        protected.append({"reference": ref, "source": str(path), "member": member,
                          "bytes": len(raw), "sha256": digest(raw)})
    if sum(len(raw) for raw in members.values()) > TOTAL_LIMIT:
        raise ValueError("Current registration recovery exceeds the bounded archive input limit")
    manifest = {"schema_version": 1, "kind": "task-console-current-registration",
                "generation": generation, "domain": domain,
                "source_roots": {"private": str(private), "state": str(state), "vault": str(vault)},
                "documents": documents, "protected_references": protected,
                "pending_journals": pending, "missing_references": 0,
                "machine_bound": True,
                "restore_limit": "DPAPI ciphertext requires the original Windows account and machine. "
                                 "This archive does not prove cross-machine restoration or restore task registrations."}
    return manifest, members, sources, journal_paths


def export(private_root, state_root, vault_root, output):
    private, state, vault, output = map(absolute_path, (private_root, state_root, vault_root, output))
    if any(not p.is_dir() for p in (private, state, vault)):
        raise ValueError("Configured registration roots must exist")
    if any(not ordinary_path(state / name).is_dir() for name in ("journals", "receipts", "locks")):
        raise ValueError("Configured registration state is incomplete")
    if state == vault or state in vault.parents or vault in state.parents:
        raise ValueError("State and vault roots must be disjoint")
    if any(output == p or p in output.parents for p in (private, state, vault)):
        raise ValueError("Recovery output must be separate from its source roots")
    verify_output_boundary(output)
    with authority_lock(state):
        manifest, members, sources, journals = collect(private, state, vault)
        members["manifest.json"] = encode(manifest)
        # The caller owns the destination directory and its private backup policy.
        with tempfile.NamedTemporaryFile(prefix="registration-", suffix=".zip", dir=output.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, raw in sorted(members.items()):
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, raw)
            with zipfile.ZipFile(temporary) as archive:
                if set(archive.namelist()) != set(members) or archive.testzip() is not None:
                    raise ValueError("Registration recovery archive verification failed")
                if any(archive.read(name) != raw for name, raw in members.items()):
                    raise ValueError("Registration recovery archive content differs")
            if (sorted((state / "journals").glob("*.json")) != journals
                    or any(read(path) != raw for path, raw in sources.items())):
                raise ValueError("Registration sources changed before recovery publication")
            raw = temporary.read_bytes()
            verify_output_boundary(output)
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    return {"schema_version": 1, "status": "captured", "generation": manifest["generation"],
            "required_refs": len(manifest["protected_references"]),
            "required_bytes": sum(r["bytes"] for r in manifest["protected_references"]),
            "pending_journals": len(manifest["pending_journals"]),
            "archive_bytes": len(raw), "archive_sha256": digest(raw), "machine_bound": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("private-root", "state-root", "vault-root", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    private_data_root(absolute_path(args.private_root))
    print(json.dumps(export(args.private_root, args.state_root, args.vault_root, args.output), sort_keys=True))


if __name__ == "__main__":
    main()
