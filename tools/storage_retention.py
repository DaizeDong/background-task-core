"""Plan explicit artifact retirement while preserving console state and recovery inputs."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat

REPO = Path(__file__).resolve().parents[1]
PROTECTED = ("launcher-binding.json", "task-console", "profile-rollback", "maintenance", "storage", "storage-retention.json")
MAX_RECORD_BYTES = 262144
MAX_RETIREMENTS = 64


class RetentionError(ValueError):
    pass


def ordinary_path(path):
    path = Path(os.path.abspath(path))
    for item in reversed((path, *path.parents)):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RetentionError("Reparse points and symbolic links are not retirement inputs")
        if not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode):
            raise RetentionError("Retirement accepts ordinary files and directories only")
    return path


def relative_path(root, value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise RetentionError("Use a nonempty relative POSIX path")
    parts = PurePosixPath(value).parts
    if (PureWindowsPath(value).drive or value.startswith("/") or
            any(p in (".", "..") or ":" in p or p.endswith((" ", ".")) for p in value.split("/"))):
        raise RetentionError("Retirement path must stay strictly within the selected data root")
    path = ordinary_path(root.joinpath(*parts))
    if path == root or root not in path.parents:
        raise RetentionError("Retirement path escaped the selected data root")
    return path


def snapshot(path):
    """Fingerprint metadata without following links or retaining a per-file inventory."""
    digest = hashlib.sha256()
    count = size = 0

    def visit(item, info):
        nonlocal count, size
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RetentionError("A retirement tree contains a reparse point or symbolic link")
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(item) as children:
                entries = sorted(children, key=lambda entry: entry.name)
            for entry in entries:
                # Windows DirEntry.stat can return zero for inode and link count.
                visit(Path(entry.path), os.stat(entry.path, follow_symlinks=False))
        elif stat.S_ISREG(info.st_mode):
            if info.st_nlink != 1:
                raise RetentionError("A retirement file has another hard link")
            count += 1
            size += info.st_size
        else:
            raise RetentionError("A retirement tree contains a special file")
        digest.update(json.dumps([str(item.relative_to(path)), info.st_mode,
                                  info.st_size, info.st_mtime_ns, info.st_dev,
                                  info.st_ino], separators=(",", ":")).encode())

    visit(path, path.lstat())
    return {"files": count, "bytes": size, "fingerprint": digest.hexdigest()}


def plan(data_root, record):
    root = ordinary_path(data_root)
    if not root.is_dir() or root == REPO or REPO in root.parents:
        raise RetentionError("Use an existing data directory outside the tool")
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        raise RetentionError("Unsupported retirement record")
    rows = record.get("retirements")
    if not isinstance(rows, list) or len(rows) > MAX_RETIREMENTS:
        raise RetentionError("Retirement records must be bounded")
    protected = [relative_path(root, p) for p in record.get("protected_paths", [])]
    candidates = []
    results = []
    for row in rows:
        if not isinstance(row, dict) or row.get("completed") is not True or row.get("source_reconciled") is not True:
            raise RetentionError("Unfinished work or unreconciled source cannot be retired")
        if row.get("active_writer") is not False:
            raise RetentionError("A writer must be confirmed inactive before retirement")
        path = relative_path(root, row.get("path"))
        top = path.relative_to(root).parts[0].casefold()
        if top in PROTECTED or top.startswith("declarations-"):
            raise RetentionError("Core console state and restoration inputs cannot be retired")
        if any(path == p or path in p.parents or p in path.parents for p in protected):
            raise RetentionError("Retirement overlaps a protected input")
        result = relative_path(root, row.get("final_deliverable"))
        if result == path or path in result.parents or not result.is_file():
            raise RetentionError("Keep the final result outside the retired tree")
        if result.stat().st_size > MAX_RECORD_BYTES:
            raise RetentionError("Keep a bounded final result, not another complete archive")
        if hashlib.sha256(result.read_bytes()).hexdigest() != row.get("final_sha256"):
            raise RetentionError("The retained final result changed")
        if any(path == p or path in p.parents or p in path.parents for p in candidates):
            raise RetentionError("Retirement entries overlap")
        candidates.append(path)
        results.append(result)
    if any(result == candidate or candidate in result.parents
           for result in results for candidate in candidates):
        raise RetentionError("A retained final result is inside another retirement entry")
    entries = []
    for row, path in zip(rows, candidates):
        if not path.exists():
            continue
        entries.append({"path": str(path), "relative_path": row["path"], **snapshot(path)})
    return {"schema_version": 1, "data_root": str(root), "entries": entries,
            "files": sum(e["files"] for e in entries), "bytes": sum(e["bytes"] for e in entries)}


def private_data_root(value):
    """Prove the selected destination before any real-state operation."""
    root = ordinary_path(value)
    guard = REPO / "guards/tools/data_boundary.py"
    if not guard.is_file():
        raise RetentionError("Initialize the pinned guards submodule first")
    spec = importlib.util.spec_from_file_location("retention_data_boundary", guard)
    boundary = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(boundary)
    boundary.prove_private_companion(str(root))
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True)
    args = parser.parse_args()
    root = private_data_root(args.data_root)
    record_path = relative_path(root, "storage-retention.json")
    if record_path.stat().st_size > MAX_RECORD_BYTES:
        raise RetentionError("Retirement record is too large")
    print(json.dumps(plan(root, json.loads(record_path.read_text(encoding="utf-8-sig"))), sort_keys=True))


if __name__ == "__main__":
    main()
