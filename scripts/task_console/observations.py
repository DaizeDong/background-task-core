"""One passive collection pass for collect.ps1 and the existing health monitor.

Only startup bindings select paths. Reports and Scheduler actions are data, never
commands. Effective declaration validation belongs to registration; verdicts belong to
health. There is no timer, task registration, DB, or notification here.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time

try:
    from fleet_guards import filesystem as fs, datadir
    from llmcall import process
    from . import registration, component_status, health
    from .runtime_storage import ResourceLocks, decode
except ImportError:
    if __name__ == "__main__":
        stream = sys.stdout if "--receipt-stdout" in sys.argv else sys.stderr
        print(json.dumps({"schemaVersion": 1, "published": False,
                          "reason_code": "dependency_unavailable"}), file=stream)
        raise SystemExit(2) from None
    raise

LIMIT = 16 * 1024 * 1024
COLLECTION_TIMEOUT = 75
PERIODIC_TIMEOUT = 95


class CollectionFailure(ValueError):
    """A fixed transport reason; child output must never become diagnostic text."""


def read_capture(stream):
    raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise CollectionFailure("input_limit")
    # Strip only an optional transport BOM. U+FEFF inside JSON strings is data.
    return decode(raw.decode("utf-8-sig"))


def encode_document(value):
    payload = json.dumps(value, ensure_ascii=True, allow_nan=False).encode("ascii")
    if len(payload) > LIMIT:
        raise CollectionFailure("input_limit")
    return payload


def collect_supervised(binding, capture, *, now=None, bundle=None):
    """The shared process owner bounds compilation, artifact reads and catalog work."""
    now = time.time() if now is None else now
    request = {"binding": binding, "capture": capture, "now": now}
    if bundle is not None:
        request["bundle"] = bundle
    payload = encode_document(request)
    result = process.run(
        [sys.executable, "-I", "-B", "-X", "utf8", "-m", "task_console.observations", "--worker"],
        payload, COLLECTION_TIMEOUT, context=process.resolve_context(),
        max_input_bytes=LIMIT, max_output_bytes=LIMIT)
    if result.error:
        if result.error == "exit_nonzero" and result.stderr_bytes:
            try:
                failure = decode(result.stderr_bytes.decode("utf-8"))
            except (ValueError, TypeError, KeyError):
                failure = None
            if isinstance(failure, dict) and failure.get("reason_code") == "dependency_unavailable":
                raise CollectionFailure("dependency_unavailable")
        code = result.error if result.error in {
            "timeout", "output_limit", "input_limit", "cleanup_failed", "launch_failed", "cancelled"
        } else "failed"
        raise CollectionFailure("worker_" + code)
    if result.stdout_bytes is None:
        raise CollectionFailure("worker_failed")
    snapshot = decode(result.stdout_bytes.decode("utf-8"))
    component_status.evaluate_snapshot(snapshot, now)
    return snapshot


def read_json(path):
    return decode(fs.read_bounded(path, LIMIT).decode("utf-8-sig"))


def absolute(value):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError("explicit_absolute_path_required")
    return fs.validate_path(value)


def load_binding(path):
    binding = read_json(absolute(str(path)))
    allowed = {"schemaVersion", "request", "private_dir", "snapshot", "home",
               "catalog_request", "catalog_snapshot", "tasks", "local_listeners",
               "max_age_seconds", "catalog_max_age_seconds", "runtime", "catalog_pins"}
    if (not isinstance(binding, dict) or type(binding.get("schemaVersion")) is not int
            or binding["schemaVersion"] != 1 or set(binding) - allowed):
        raise ValueError("invalid_collection_binding")
    if ("request" in binding) == ("runtime" in binding):
        raise ValueError("choose_one_authority_source")
    if "runtime" in binding:
        runtime = binding["runtime"]
        if not isinstance(runtime, dict) or set(runtime) != {
                "runtime_config", "private_root", "state_root", "vault_root"}:
            raise ValueError("invalid_observation_runtime")
        for value in runtime.values():
            absolute(value)
    else:
        absolute(binding["request"])
    for key in ("private_dir", "snapshot"):
        absolute(binding.get(key))
    private = absolute(binding["private_dir"])
    if not private.is_dir():
        raise ValueError("private_directory_must_exist")
    output = fs.validate_path(binding["snapshot"], root=private)
    datadir.assert_outside_own_repo(output, "task-console", consumer_root=Path(__file__).parent)
    if output == absolute(str(path)):
        raise ValueError("snapshot_overwrites_binding")
    if output == private or ("request" in binding and output == absolute(binding["request"])):
        raise ValueError("invalid_snapshot_destination")
    if "runtime" in binding and any(output.is_relative_to(absolute(value))
                                   for value in binding["runtime"].values()):
        raise ValueError("snapshot_overwrites_runtime")
    if "home" in binding:
        absolute(binding["home"])
    if "catalog_request" in binding and "catalog_snapshot" in binding:
        raise ValueError("choose_one_catalog_source")
    for key in ("catalog_request", "catalog_snapshot"):
        if key in binding:
            if absolute(binding[key]) == output:
                raise ValueError("snapshot_overwrites_input")
    age = binding.get("max_age_seconds", 300)
    if type(age) not in (int, float) or not 0 < age <= 86400:
        raise ValueError("invalid_collection_max_age")
    catalog_age = binding.get("catalog_max_age_seconds", 300)
    if type(catalog_age) not in (int, float) or not 0 < catalog_age <= 86400:
        raise ValueError("invalid_catalog_max_age")
    if not isinstance(binding.get("tasks", {}), dict):
        raise ValueError("invalid_task_observation_bindings")
    if not isinstance(binding.get("local_listeners", {}), dict):
        raise ValueError("invalid_local_listener_bindings")
    pins = binding.get("catalog_pins", {})
    if not isinstance(pins, dict) or (pins and "catalog_request" not in binding):
        raise ValueError("invalid_catalog_pins")
    for source, digest in pins.items():
        if (absolute(source) == output or not isinstance(digest, str)
                or not re.fullmatch("[a-f0-9]{64}", digest)):
            raise ValueError("invalid_catalog_pin")
    return binding


def load_runtime(binding):
    """Use the existing runtime composition without environment or export fallback."""
    from .runtime import RuntimeConfig, create_runtime
    values = dict(binding["runtime"])
    path = values.pop("runtime_config")
    return create_runtime(RuntimeConfig.read(path, **values))


def authority_identity(bundle):
    generation = bundle.get("authority_generation")
    if not isinstance(generation, str) or not re.fullmatch("[a-f0-9]{32}", generation):
        raise CollectionFailure("authority_unavailable")
    request = bundle["request"]
    data = json.dumps(request, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("ascii")
    return {"authority_generation": generation,
            "input_revision": registration.revision(request),
            "effective_request_sha256": hashlib.sha256(data).hexdigest(),
            "authority_epoch": request["machine"]["authority_epoch"]}


def load_current(runtime):
    # Caller holds the existing authority lock. Constructors never provision it.
    if runtime.journal.pending(["authority"]):
        raise CollectionFailure("authority_pending")
    bundle = runtime.load()
    identity = authority_identity(bundle)
    # Only the exact effective request crosses the worker boundary. Ownership,
    # vault references and launchers are not observation inputs.
    return {"request": deepcopy(bundle["request"]), **identity}


def check_catalog_pins(binding):
    for path, expected in binding.get("catalog_pins", {}).items():
        if hashlib.sha256(fs.read_bounded(absolute(path), LIMIT)).hexdigest() != expected:
            raise CollectionFailure("catalog_input_changed")


def runtime_config_digest(binding):
    path = absolute(binding["runtime"]["runtime_config"])
    return hashlib.sha256(fs.read_bounded(path, LIMIT)).hexdigest()


def capture_scheduler(powershell):
    """One bounded invocation of the existing passive Windows collector."""
    executable = absolute(str(powershell))
    if not executable.is_file():
        raise CollectionFailure("collector_unavailable")
    result = process.run(
        [str(executable), "-NoProfile", "-NonInteractive", "-File",
         str(Path(__file__).with_name("collect.ps1")), "-CaptureOnly"],
        None, PERIODIC_TIMEOUT, context=process.resolve_context(),
        max_input_bytes=0, max_output_bytes=LIMIT)
    if result.error or result.stdout_bytes is None:
        reason = result.error if result.error in {
            "timeout", "output_limit", "cleanup_failed", "launch_failed", "cancelled"} else "failed"
        raise CollectionFailure("capture_" + reason)
    return decode(result.stdout_bytes.decode("utf-8-sig"))


def artifact_path(value, binding):
    if value.startswith(("~/", "~\\")):
        if "home" not in binding:
            raise ValueError("artifact_home_unbound")
        value = str(absolute(binding["home"]) / value[2:])
    # No environment or global-home fallback, including Windows %VAR% syntax.
    return absolute(value)


def artifact_fact(value, binding):
    try:
        path = artifact_path(value, binding)
        if not path.is_dir():
            return {"mtime": path.stat().st_mtime, "reason": None}
        newest, count = None, 0
        def fail(error):
            raise error
        for root, dirs, files in os.walk(path, onerror=fail, followlinks=False):
            for name in [*dirs, *files]:
                count += 1
                if count > 10000:
                    raise ValueError("artifact_scan_limit")
                fs.validate_path(Path(root) / name, root=path)
            for name in files:
                stamp = (Path(root) / name).stat().st_mtime
                newest = stamp if newest is None else max(newest, stamp)
        return {"mtime": newest, "reason": None if newest is not None else "artifact_directory_empty"}
    except (OSError, ValueError) as exc:
        return {"mtime": None, "reason": "artifact_read_failed:" + type(exc).__name__}


def scheduler_rows(capture):
    if not isinstance(capture, dict) or not isinstance(capture.get("tasks"), list):
        raise ValueError("invalid_scheduler_capture")
    if capture.get("scheduler_error"):
        return {}
    if type(capture.get("enumerated")) is not int or capture["enumerated"] <= 0:
        raise ValueError("scheduler_zero_enumeration")
    rows = {}
    for row in capture["tasks"]:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise ValueError("invalid_scheduler_row")
        if row["name"] in rows:
            raise ValueError("duplicate_scheduler_row")
        for key in ("lastRunEpoch", "nextRunEpoch"):
            if row.get(key) is not None and health.epoch(row[key]) is None:
                raise ValueError("invalid_scheduler_timestamp")
        rows[row["name"]] = {"state": row.get("state"), "last_rc": row.get("rcRaw"),
                             "code_domain": "scheduler", "last_run": row.get("lastRunEpoch"),
                             "next_run": row.get("nextRunEpoch"), "missed_runs": row.get("missedRuns"),
                             "query_error": row.get("infoError")}
    return rows


def process_fact(rule, capture, max_age):
    allowed = {"executable", "command_contains", "port", "address", "check_id"}
    if not isinstance(rule, dict) or set(rule) - allowed:
        raise ValueError("invalid_process_binding")
    executable = str(absolute(rule.get("executable")))
    contains = rule.get("command_contains", [])
    if not isinstance(contains, list) or any(not isinstance(x, str) or not x for x in contains):
        raise ValueError("invalid_process_match")
    port = rule.get("port")
    if port is not None and (type(port) is not int or not 0 < port < 65536):
        raise ValueError("invalid_listener_port")
    evidence = capture.get("local") or {}
    stamp = health.epoch(evidence.get("observed_at"))
    result = {"kind": "process", "subject": "workload", "observed_at": stamp,
              "max_age_seconds": max_age, "alive": None, "scope": "local_process",
              "remote_job_success": "unchecked"}
    if evidence.get("process_query") != "checked" or stamp is None:
        return result, "process_unchecked"
    processes = evidence.get("processes")
    if not isinstance(processes, list):
        raise ValueError("invalid_process_capture")
    matches = [p for p in processes if isinstance(p, dict)
               and str(p.get("executable", "")).casefold() == executable.casefold()
               and all(x.casefold() in str(p.get("command_line", "")).casefold() for x in contains)]
    if len(matches) != 1 or type(matches[0].get("pid")) is not int:
        return result, "process_missing_or_ambiguous"
    result.update(pid=matches[0]["pid"], alive=True)
    if port is not None:
        result["scope"] = "local_listener_only"
        if evidence.get("listener_query") != "checked":
            result["alive"] = None
            return result, "listener_unchecked"
        listeners = evidence.get("listeners")
        if not isinstance(listeners, list):
            raise ValueError("invalid_listener_capture")
        result["alive"] = any(p.get("pid") == result["pid"] and p.get("port") == port
                              and p.get("address") == rule.get("address", "127.0.0.1")
                              for p in listeners if isinstance(p, dict)) or None
        return result, "local_listener_observed" if result["alive"] else "listener_missing"
    return result, "local_process_observed"


def receipt_fact(rule, task_id):
    if not isinstance(rule, dict) or set(rule) - {"path", "format", "max_age_seconds"}:
        raise ValueError("invalid_receipt_binding")
    path = absolute(rule.get("path"))
    try:
        report = read_json(path)
    except FileNotFoundError:
        return {}, "receipt_missing"
    if not isinstance(report, dict):
        raise ValueError("invalid_receipt")
    if rule.get("format") == "stdout-report":
        # A captured reader stdout document, never a command to run that reader.
        if type(report.get("schemaVersion")) is not int or report["schemaVersion"] != 1:
            raise ValueError("invalid_receipt_schema")
        if report.get("task_id") != task_id:
            raise ValueError("receipt_task_mismatch")
        keys = {"run_id", "current_run", "execution", "last_success", "last_success_run_id",
                "last_run", "checks", "health", "observer"}
        result = {key: deepcopy(report[key]) for key in keys if key in report}
    elif rule.get("format") == "last-run-v1":
        if report.get("schemaVersion", report.get("version")) != 1:
            raise ValueError("invalid_last_run_schema")
        result = {"last_run": deepcopy(report)}
        if "execution" not in report:
            result["current_run"] = {key: report[key] for key in
                                     ("run_id", "started_at", "finished_at") if key in report}
            result["current_run"].update(state="completed" if health.epoch(report.get("finished_at")) else "unknown",
                                         raw_exit_code=report.get("exit_code"), code_domain="process")
    else:
        raise ValueError("unknown_receipt_format")
    result["receipt_observed_at"] = report.get("observed_at", report.get("finished_at"))
    return result, "receipt_read"


def freshness_check(spec, obs, check_id, stamp, max_age, reason="observed"):
    if any(c["id"] == check_id for c in spec["checks"]):
        raise ValueError("reserved_observation_check_id")
    spec["checks"].append({"id": check_id, "required": True, "max_age_seconds": max_age})
    obs.setdefault("checks", []).append({"id": check_id,
        "state": "healthy" if stamp is not None else "unknown", "observed_at": stamp,
        "reason_code": reason})


def collect(binding, capture, *, now=None, bundle=None):
    now = time.time() if now is None else now
    if bundle is None:
        if "runtime" in binding:
            raise CollectionFailure("authority_unavailable")
        bundle = {"request": read_json(absolute(binding["request"]))}
    plan = registration.compile_effective(bundle)
    specs = plan["task_specs"]
    policies = binding.get("tasks", {})
    if set(policies) - {s["task_id"] for s in specs}:
        raise ValueError("unknown_observation_task")
    output = absolute(binding["snapshot"])
    for rule in policies.values():
        if isinstance(rule, dict) and isinstance(rule.get("receipt"), dict):
            if output == absolute(rule["receipt"].get("path")):
                raise ValueError("snapshot_overwrites_receipt")
    for spec in specs:
        for check in spec["checks"]:
            path = check.get("artifact") or (check.get("legacy") or {}).get("artifact")
            if path:
                try:
                    source = artifact_path(path, binding)
                except (OSError, ValueError):
                    continue  # Recorded as unavailable by artifact_fact below.
                if output.is_relative_to(source):
                    raise ValueError("snapshot_overwrites_artifact")
    rows = scheduler_rows(capture)
    stamp = health.epoch(capture.get("observed_at"))
    max_age = binding.get("max_age_seconds", 300)
    tasks = []
    for compiled in specs:
        spec = {key: deepcopy(compiled[key]) for key in
                ("component", "task_id", "name", "kind", "enabled", "checks", "timeout_seconds")}
        # Dispatcher completion describes dispatch, not completion of queued work.
        if spec["kind"] == "dispatcher":
            spec.update(kind="oneshot", declared_kind="dispatcher", detached=True)
        rule = policies.get(spec["task_id"], {})
        if not isinstance(rule, dict) or set(rule) - {"detached", "process", "receipt"}:
            raise ValueError("invalid_task_observation_binding")
        if "detached" in rule:
            if type(rule["detached"]) is not bool:
                raise ValueError("invalid_detached_binding")
            spec["detached"] = rule["detached"]
        row = rows.get(spec["name"], {})
        obs = {"schemaVersion": 1, "scheduler": row, "artifacts": {}}
        if capture.get("scheduler_error") or row.get("query_error") or stamp is None:
            obs["query_failed"] = True
        for check in spec["checks"]:
            path = check.get("artifact") or (check.get("legacy") or {}).get("artifact")
            if path:
                obs["artifacts"][path] = artifact_fact(path, binding)
        if "receipt" in rule:
            receipt, reason = receipt_fact(rule["receipt"], spec["task_id"])
            execution = receipt.get("current_run", receipt.get("execution", {}))
            receipt_start = health.epoch(execution.get("started_at"))
            scheduler_start = health.epoch(row.get("last_run"))
            if receipt_start is not None and scheduler_start is not None and receipt_start < scheduler_start - 1:
                # Retain the historical document without using it as this run's receipt.
                obs["previous_receipt"] = receipt
                receipt = {}
                reason = "receipt_precedes_scheduler_run"
            obs.update(receipt)
            receipt_age = rule["receipt"].get("max_age_seconds", max_age)
            if type(receipt_age) not in (int, float) or not 0 < receipt_age <= 31536000:
                raise ValueError("invalid_receipt_max_age")
            freshness_check(spec, obs, "collector.receipt", health.epoch(obs.pop("receipt_observed_at", None)), receipt_age, reason)
        if "process" in rule:
            observer, reason = process_fact(rule["process"], capture, max_age)
            obs["observer"] = observer
            check_id = rule["process"].get("check_id")
            if check_id:
                if check_id not in {c["id"] for c in spec["checks"]}:
                    raise ValueError("unknown_process_check")
                obs.setdefault("checks", []).append({"id": check_id,
                    "state": "healthy" if observer["alive"] else "unknown",
                    "observed_at": observer["observed_at"], "reason_code": reason})
        if spec["kind"] == "daemon" and not obs.get("observer") and not obs["artifacts"]:
            obs["query_failed"] = True
        freshness_check(spec, obs, "collector.scheduler", stamp, max_age)
        if not row:
            obs["query_failed"] = True
        tasks.append({"spec": spec, "observations": obs})
    for name, rule in binding.get("local_listeners", {}).items():
        if not isinstance(name, str) or not name or (rule is not None and "port" not in rule):
            raise ValueError("invalid_local_subject")
        if rule is None:
            observer = {"kind": "process", "subject": "workload", "alive": None,
                        "observed_at": None, "scope": "local_listener_only"}
            reason = "listener_binding_unconfigured"
        else:
            observer, reason = process_fact(rule, capture, max_age)
        spec = {"component": "local-listeners", "task_id": "local-listeners/" + name,
                "name": name, "kind": "daemon", "checks": []}
        obs = {"schemaVersion": 1, "observer": observer, "remote_job_success": "unchecked"}
        freshness_check(spec, obs, "local-listener", observer["observed_at"] if observer["alive"] else None,
                        max_age, reason)
        tasks.append({"spec": spec, "observations": obs})
    catalog, catalog_hash = None, None
    check_catalog_pins(binding)
    if "catalog_request" in binding:
        request = read_json(absolute(binding["catalog_request"]))
        catalog_hash = hashlib.sha256(json.dumps(
            {"request": request, "pins": binding.get("catalog_pins", {})},
            sort_keys=True, allow_nan=False).encode()).hexdigest()
        try:
            previous = read_json(absolute(binding["snapshot"]))
        except FileNotFoundError:
            previous = {}
        if not isinstance(previous, dict) or not isinstance(previous.get("catalog") or {}, dict):
            raise ValueError("invalid_previous_catalog_snapshot")
        prior_catalog = previous.get("catalog") or {}
        prior_stamp = health.epoch(prior_catalog.get("observed_at"))
        if (previous.get("catalog_request_sha256") == catalog_hash and prior_stamp is not None
                and 0 <= now - prior_stamp <= binding.get("catalog_max_age_seconds", 300)):
            catalog = prior_catalog
        else:
            from skill_smith.catalog import discover
            catalog = discover(request)
        check_catalog_pins(binding)
    elif "catalog_snapshot" in binding:
        try:
            catalog = read_json(absolute(binding["catalog_snapshot"]))
        except FileNotFoundError:
            pass
    snapshot = {"schemaVersion": 1, "observed_at": now, "expected": len(tasks), "tasks": tasks,
                "catalog": catalog, "catalog_request_sha256": catalog_hash,
                "input_revision": plan["input_revision"],
                "scheduled_task_count": len(specs), "authority": "observation_only"}
    if "runtime" in binding:
        identity = authority_identity(bundle)
        if plan["input_revision"] != identity["input_revision"]:
            raise CollectionFailure("authority_changed")
        snapshot.update(identity)
    component_status.evaluate_snapshot(snapshot, now)  # Validate before any publication.
    return snapshot


def publish(binding_path, capture, *, now=None, collect_snapshot=collect, require_runtime=False):
    binding = load_binding(binding_path)
    if require_runtime and "runtime" not in binding:
        raise CollectionFailure("runtime_binding_required")
    output = absolute(binding["snapshot"])
    config_digest = runtime_config_digest(binding) if "runtime" in binding else None
    runtime = load_runtime(binding) if "runtime" in binding else None
    # The established owner supplies crash-released kernel locks and guarded FS.
    lock_key = "observation:" + str(output).casefold()
    with ResourceLocks(Path(binding["private_dir"]) / ".observation-locks").hold([lock_key]):
        bundle = None
        if runtime is not None:
            with runtime.locks.hold(["authority"]):
                bundle = load_current(runtime)
        check_catalog_pins(binding)
        facts = capture() if callable(capture) else capture
        kwargs = {"now": now}
        if bundle is not None:
            kwargs["bundle"] = bundle
        snapshot = collect_snapshot(binding, facts, **kwargs)
        payload = encode_document(snapshot)
        # Release authority during slow discovery, then compare under its lock.
        # A concurrent migration invalidates this pass; never publish mixed epochs.
        with runtime.locks.hold(["authority"]) if runtime is not None else nullcontext():
            if runtime is not None:
                if runtime_config_digest(binding) != config_digest:
                    raise CollectionFailure("binding_changed")
                identity = authority_identity(bundle)
                if (authority_identity(load_current(runtime)) != identity
                        or any(snapshot.get(k) != v for k, v in identity.items())):
                    raise CollectionFailure("authority_changed")
            if load_binding(binding_path) != binding:
                raise CollectionFailure("binding_changed")
            check_catalog_pins(binding)
            if process.remaining_timeout(COLLECTION_TIMEOUT) <= 0:
                raise CollectionFailure("worker_timeout")
            fs.atomic_replace(output, payload)
    receipt = {"schemaVersion": 1, "published": True, "tasks": snapshot["scheduled_task_count"],
               "subjects": snapshot["expected"], "sha256": hashlib.sha256(payload).hexdigest()}
    if bundle is not None:
        receipt.update(authority_identity(bundle))
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", type=Path)
    parser.add_argument("--capture", type=Path, help="omitted reads collector JSON on stdin")
    parser.add_argument("--periodic", action="store_true", help="collect once using current runtime authority")
    parser.add_argument("--powershell", type=Path, help="explicit Windows PowerShell executable for periodic collection")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--receipt-stdout", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--deadline-ms", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.worker:
            if args.binding or args.capture or args.periodic or args.powershell or args.receipt_stdout or args.deadline_ms is not None:
                raise ValueError("invalid_worker_arguments")
            request = read_capture(sys.stdin.buffer)
            if (not isinstance(request, dict) or not {"binding", "capture", "now"} <= set(request)
                    or set(request) - {"binding", "capture", "now", "bundle"}):
                raise ValueError("invalid_worker_request")
            snapshot = collect(request["binding"], request["capture"], now=request["now"], bundle=request.get("bundle"))
            sys.stdout.buffer.write(encode_document(snapshot))
            return 0
        if args.binding is None:
            raise ValueError("binding_required")
        if ((args.periodic and (args.capture or not args.powershell))
                or (args.powershell and not args.periodic)):
            raise ValueError("invalid_periodic_arguments")
        timeout = PERIODIC_TIMEOUT if args.periodic else COLLECTION_TIMEOUT
        if args.deadline_ms is not None:
            timeout = min(timeout, args.deadline_ms / 1000 - time.time())
        if not math.isfinite(timeout) or timeout <= 0:
            raise CollectionFailure("worker_timeout")
        with process.execution_scope(timeout=timeout):
            if args.periodic:
                capture = lambda: capture_scheduler(args.powershell)
            elif args.capture:
                capture = read_json(absolute(str(args.capture)))
            else:
                capture = read_capture(sys.stdin.buffer)
            receipt = publish(args.binding, capture, collect_snapshot=collect_supervised,
                              require_runtime=args.periodic)
        print(json.dumps(receipt))
        return 0
    except Exception as exc:
        # No source text, process command line or credential-bearing paths in logs.
        reason = ("dependency_unavailable" if isinstance(exc, ImportError) else
                  str(exc) if isinstance(exc, CollectionFailure) else "collection_failed")
        print(json.dumps({"schemaVersion": 1, "published": False, "reason_code": reason}),
              file=sys.stdout if args.receipt_stdout else sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
