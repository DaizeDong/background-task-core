"""Disk-backed synthetic transports for restart tests, never production adapters.

The fake vault deliberately stores synthetic values in JSON. It is not encryption
or a proposal for the runtime vault. Only generated test inputs may enter it.
"""
import base64
from copy import deepcopy
import json
from pathlib import Path
import sys

from test_registration_transaction import Journal, Locks, Objects, Vault, api, rig


def write(path, value):
    def encode(value):
        if isinstance(value, bytes):
            return {"synthetic_bytes": base64.b64encode(value).decode("ascii")}
        raise TypeError(type(value).__name__)
    path.write_text(json.dumps(value, default=encode), encoding="utf-8")


def read(path):
    def decode(value):
        if set(value) == {"synthetic_bytes"}:
            return base64.b64decode(value["synthetic_bytes"])
        return value
    return json.loads(path.read_text(encoding="utf-8"), object_hook=decode)


class DiskObjects(Objects):
    def __init__(self, path, root=None):
        super().__init__(root)
        self.path = path
        if path.exists():
            state = read(path)
            self.items, self.stages, self.calls = state["items"], state["stages"], state["calls"]

    def flush(self):
        write(self.path, {"items": self.items, "stages": self.stages, "calls": self.calls})

    def prepare(self, key, value, token):
        result = super().prepare(key, value, token)
        self.flush()
        return result

    def publish(self, key, expected, desired):
        if self.root is None and self.read(key).get("value", {}).get("running"):
            raise api().Conflict("busy", "scheduler")
        super().publish(key, expected, desired)
        self.flush()

    def cleanup(self, token):
        super().cleanup(token)
        self.flush()


class DiskJournal(Journal):
    def __init__(self, path):
        self.path = path
        self.items = read(path) if path.exists() else {}

    def save(self, key, value):
        super().save(key, value)
        write(self.path, self.items)


class DiskVault(Vault):
    def __init__(self, path):
        self.path = path
        self.items = read(path) if path.exists() else {}

    def put(self, key, value):
        ref = super().put(key, value)
        write(self.path, self.items)
        return ref


def render(spec, old, enabled):
    return {"xml": "<Task>synthetic prepared XML</Task>", "enabled": enabled,
            "running": False, "spec": deepcopy(spec)}


def reopen(root):
    return api().Runtime(lambda: read(root / "input.json"),
                         DiskObjects(root / "files-state.json", root),
                         DiskObjects(root / "scheduler-state.json"),
                         DiskJournal(root / "journal-state.json"),
                         DiskVault(root / "vault-state.json"), Locks(), render)


def initialize(root, *, enable=False, absent_retire=False):
    from test_registration_transaction import migrate
    initial, bundle, ids = rig(root)
    if absent_retire:
        migrate(initial, bundle, ids)
        initial.scheduler.items.clear()
        bundle["ownership"][ids[0]]["identity"] = None
    if enable:
        bundle["request"]["bindings"]["tasks"][ids[0]]["enabled"] = True
    write(root / "input.json", bundle)
    for name, adapter in (("files", initial.files), ("scheduler", initial.scheduler)):
        write(root / (name + "-state.json"), {"items": adapter.items, "stages": {}, "calls": []})
    return reopen(root), ids


if __name__ == "__main__":
    import os
    root, boundary, operation = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    position = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    runtime = reopen(root)
    ids = list(runtime.load()["request"]["bindings"]["tasks"])
    hits = []
    def crash(point):
        if point == boundary:
            hits.append(point)
            if len(hits) == position:
                os._exit(73)
    runtime.checkpoint = crash
    reg = api()
    plan = reg.build_plan(runtime, ids, operation=operation, migrate=operation == "apply",
                          approve_enable=True, reason="Synthetic retirement" if operation == "retire" else "")
    reg.apply(plan, plan["input_revision"], runtime=runtime)
    raise SystemExit("crash boundary was not reached")
