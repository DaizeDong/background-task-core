"""Read-only JSON adapters for component observations and SMITH catalog v1.

Only the caller-selected snapshot is opened. Paths, entrypoints and commands in
that document are data, never read instructions or executable authority.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import time

try:
    from . import health, freshness
except ImportError:
    import health
    import freshness

DIMENSIONS = ("declared", "enabled", "cached", "installed", "resolved", "discovered", "compatible")
STATES = {"yes", "no", "unknown", "not_applicable"}


def catalog_view(snapshot: dict | None) -> dict:
    """Consume skill_smith.catalog output, without importing or rerunning discovery."""
    if snapshot is None:
        return {"available": False, "reason": "Catalog not configured; unchecked",
                "records": [], "coverage": {"status": "unchecked"}, "statistics": {}}
    if not isinstance(snapshot, dict) or type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != 1:
        raise ValueError("unsupported catalog schema_version")
    records = snapshot.get("records")
    if not isinstance(records, list):
        raise ValueError("catalog records must be an array")
    out, statistics = [], {}
    for record in records:
        if not isinstance(record, dict) or not record.get("source_id") or not record.get("kind"):
            raise ValueError("catalog record needs source_id and kind")
        item = deepcopy(record)
        item["status"] = {key: (record.get("status") or {}).get(key, "unknown") for key in DIMENSIONS}
        if any(value not in STATES for value in item["status"].values()):
            raise ValueError("invalid catalog status dimension")
        # Authentication belongs to each binding, never to the owning plugin.
        if record["kind"] in ("mcp_binding", "app_connector"):
            auth = (record.get("status") or {}).get("authenticated", "unknown")
            item["status"]["authenticated"] = auth if auth in STATES else "unknown"
        for entry in item.get("entrypoints", []):
            entry["source_id"] = record["source_id"]
            entry["status"] = {key: (entry.get("status") or {}).get(key, "unknown") for key in DIMENSIONS}
        statistics[record["kind"]] = statistics.get(record["kind"], 0) + 1
        out.append(item)
    return {"schema_version": 1, "available": True, "observed_at": snapshot.get("observed_at"),
            "records": out, "statistics": statistics, "coverage": deepcopy(snapshot.get("coverage") or {"status": "unchecked"}),
            "problems": deepcopy(snapshot.get("problems") or [])}


def evaluate_snapshot(snapshot: dict, now=None) -> dict:
    """Evaluate captured read responses. Exit status belongs to the reader, not health."""
    if not isinstance(snapshot, dict) or type(snapshot.get("schemaVersion")) is not int or snapshot["schemaVersion"] != 1:
        raise ValueError("unsupported component snapshot schemaVersion")
    now = time.time() if now is None else now
    if "declarations" in snapshot:
        artifacts = snapshot.get("artifact_observations") or {}
        result = freshness.evaluate(snapshot["declarations"], snapshot.get("rows") or {}, now,
                                    mtime_of=lambda path: (artifacts.get(path, {}).get("mtime"),
                                                           artifacts.get(path, {}).get("reason", "missing captured artifact")))
        result["read_only"] = True
        result["coverage"] = health.coverage(result["summary"]["judged"], result["summary"]["total"])
        result["catalog"] = catalog_view(snapshot.get("catalog"))
        result["authority"] = "observation_only"
        return result
    inputs = snapshot.get("tasks", [])
    if not isinstance(inputs, list):
        raise ValueError("tasks must be an array")
    tasks = []
    for item in inputs:
        if not isinstance(item, dict) or not isinstance(item.get("spec"), dict):
            raise ValueError("each task needs a spec and observations/read response")
        obs = item.get("observations", item.get("report", {}))
        if not isinstance(obs, dict) or obs.get("schemaVersion", 1) != 1:
            raise ValueError("unsupported component read response")
        task = health.evaluate_task(item["spec"], obs, now)
        task["name"] = item["spec"].get("name")
        tasks.append(task)
    expected = max(len(inputs), snapshot.get("expected", len(inputs)))
    cov = health.coverage(sum(t["verdict"] != "unknown" for t in tasks), expected)
    return {"schemaVersion": 1, "read_only": True, "observed_at": health.epoch(now),
            "tasks": tasks, "coverage": cov, "catalog": catalog_view(snapshot.get("catalog")),
            "authority": "observation_only", "captured_at": snapshot.get("observed_at"),
            "authority_generation": snapshot.get("authority_generation"),
            "reason_code": "zero_coverage" if not expected else None}


def read_snapshot(path: str | Path) -> dict:
    with Path(path).expanduser().open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def read_configured(now=None, env=None) -> dict:
    """Environment paths are startup/private bindings; HTTP supplies no path parameter."""
    env = os.environ if env is None else env
    path = env.get("TASK_CONSOLE_STATUS_SNAPSHOT")
    catalog_path = env.get("TASK_CONSOLE_CATALOG_SNAPSHOT")
    result = {"schemaVersion": 1, "read_only": True, "available": False,
              "tasks": [], "coverage": health.coverage(0, 0), "authority": "observation_only",
              "reason": "Component snapshot not configured; unchecked", "catalog": catalog_view(None)}
    if path:
        try:
            result.update(evaluate_snapshot(read_snapshot(path), now), available=True, reason=None)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result.update(reason=f"Component snapshot read failed: {type(exc).__name__}", reason_code="query_failed")
    if catalog_path:
        try:
            result["catalog"] = catalog_view(read_snapshot(catalog_path))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result["catalog"] = {**catalog_view(None), "reason": f"Catalog read failed: {type(exc).__name__}",
                                 "coverage": {"status": "partial"}, "reason_code": "query_failed"}
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", type=Path, help="captured JSON; omitted reads stdin")
    source.add_argument("--configured", action="store_true", help="read the controller's private snapshot binding")
    parser.add_argument("--now", help="epoch seconds or timezone-aware ISO timestamp")
    args = parser.parse_args(argv)
    try:
        now = args.now
        if now is not None:
            try:
                now = float(now)
            except ValueError:
                pass
        if args.configured:
            result = read_configured(now)
        else:
            snapshot = read_snapshot(args.input) if args.input else json.load(sys.stdin)
            result = evaluate_snapshot(snapshot, now)
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 2 if args.configured and not result.get('available') else 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"schemaVersion": 1, "read_only": True, "error": str(exc),
                          "reason_code": "reader_failed"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
