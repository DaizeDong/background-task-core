"""Run history and health: exit-code normalisation, health verdicts, freshness, the health log,
the Task Scheduler event log, the console database and its ingester, and the component status
reader. Each former module keeps its own section below, marked with a `# ---- name` line.

CLI: `python -m task_console ingest ...` and `python -m task_console status ...`.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import datetime, date, timedelta, time as clock_time
from pathlib import Path

# ---- rcnorm ------------------------------------------------------------------------------------
# Normalise a Windows task return code. Its own module on purpose.
#
# Both server.py (rendering) and console_ingest.py (writing) need this. Importing it from server.py
# would close a cycle the moment server.py imports the store, so it lives alone and both import it.
#
# TWO RULES, both learned from measurement rather than reasoning:
#
# 1. THE EVENT LOG WRAPS WIN32 CODES AS HRESULTS. A task's LastTaskResult reads 0x2, and event 201 in
#    the Operational log reports the same outcome as 2147942402 (0x80070000 | 2). Comparing a task's
#    declared ok_codes -- small integers like 2, 3, 4 -- against the wrapped form never matches, so
#    every task that encodes a verdict in its exit code would read as 0% success while looking
#    entirely plausible. Measured 2026-09-01: a task whose declared ok code was 2 arrived here as
#    2147942402, and its success rate read 0% until the unwrap was added. The signed-int32 spelling
#    of the same value is also accepted, which is how some readers hand it back.
#
# 2. MISSING IS NOT ZERO. Event 100 (task started) carries no return code at all. Returning 0 for it
#    writes a fabricated SUCCESS, inflating the success rate by one row per start. None means "this
#    event has no return code" and the caller must skip it, not count it.

E_ACCESSDENIED_FACILITY = 0x80070000


def norm_rc(v):
    """Return an int, or None when there is no return code to speak of."""
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    try:
        n = int(str(v), 0)
    except (TypeError, ValueError):
        return None
    if n < 0:
        n += 1 << 32                      # the signed-int32 spelling of the same value
    if E_ACCESSDENIED_FACILITY <= n <= 0x8007FFFF:
        return n & 0xFFFF
    return n


# ---- winps -------------------------------------------------------------------------------------
# 这台机器上的 powershell.exe 解析成哪一条路径。**只有这一份实现。**
#
# 先用钉死的绝对路径,那个文件不在时才退回裸名字 `powershell.exe`。
#
# ⚠ 为什么先用绝对路径而不是裸名字:PATH 上可能只有 WindowsApps 的执行别名,
# 它**能被找到却跑不了任何东西**,在计划任务里表现为静默吊死。
# 不要为了「简洁」把绝对路径那一档去掉。


PINNED = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


def powershell() -> str:
    return PINNED if os.path.isfile(PINNED) else "powershell.exe"


# pythonw 下每个控制台子进程都要新分配控制台:实测单次 4.5 秒,并发时全部超时。这个标志免掉它。
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_ps(script, args=None, timeout: int = 90) -> tuple[int, str, str]:
    """Run a PowerShell script file and return (rc, stdout, stderr).

    Output is decoded as UTF-8, not the locale: PowerShell 5.1 in a non-interactive session emits
    the ANSI codepage by default and Chinese labels come back as mojibake. collect.ps1 pins its side.
    """
    p = subprocess.run(
        [powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script)] + list(args or []),
        capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    return (p.returncode,
            p.stdout.decode("utf-8", "replace").strip(),
            p.stderr.decode("utf-8", "replace").strip())


# ---- health ------------------------------------------------------------------------------------
# Pure, versioned health evaluation. Observations never grant execution authority.
#
# Collectors supply scheduler/process/artifact observations from their existing private
# bindings. This module does not read paths, run commands, or update run receipts.


SCHEMA_VERSION = 1
RC_NOT_RUN, RC_RUNNING = 0x41303, 0x41301
OK_CODE_KEYS = ("ok_codes", "ok_exit_codes")
SEVERITY = {"up": 0, "running": 0, "paused": 1, "unknown": 2,
            "grace": 3, "never": 4, "down": 5}
HEALTH = {"up": "healthy", "running": "unknown", "paused": "unknown",
          "unknown": "unknown", "grace": "degraded", "never": "unhealthy", "down": "unhealthy"}
EXECUTION_STATES = {"pending", "running", "completed", "failed", "cancelled", "deferred", "busy", "unknown"}


def _count(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def declared_ok_codes(spec: dict) -> list[int]:
    out = []
    for key in OK_CODE_KEYS:
        for value in spec.get(key) or ():
            code = norm_rc(value)
            if code is not None and code not in out:
                out.append(code)
    return out


def ok_codes(spec: dict) -> set[int]:
    return {0, *declared_ok_codes(spec)}


def worst_of(*states: str) -> str:
    return max(states or ("unknown",), key=lambda state: SEVERITY.get(state, 2))


def epoch(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            result = float(value)
        else:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                return None  # No machine-local timezone guesses in a portable report.
            result = stamp.timestamp()
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _age(stamp, limit, grace, now, slack=360):
    stamp = epoch(stamp)
    if stamp is None:
        return "unknown", "missing_timestamp", None
    age = (now - stamp) / 3600
    if age < -slack / 3600:
        return "unknown", "clock_skew", age
    if limit is None:
        return "unknown", "missing_limit", age
    if age <= limit:
        return "up", "fresh", max(0, age)
    grace = max(limit * .2, 1) if grace is None else grace
    return ("grace", "grace", age) if age <= limit + grace else ("down", "stale", age)


def coverage(checked: int, expected: int) -> dict:
    _count(checked, "checked")
    _count(expected, "expected")
    expected = max(expected, checked)
    ratio = checked / expected if expected else 0.0
    return {"checked": checked, "expected": expected, "ratio": round(ratio, 4),
            "state": "zero" if not expected else "complete" if checked == expected else "partial",
            "tone": "ok" if expected and checked == expected else "warn" if checked else "bad"}


def resource_verdict(value, *, warning: float, critical: float) -> dict:
    """Backend threshold result; clients only render tone/attention/overage."""
    if value is None:
        return {"state": "unknown", "tone": "warn", "attention": True, "overage": None}
    tone = "bad" if value >= critical else "warn" if value >= warning else "ok"
    return {"state": {"ok": "healthy", "warn": "degraded", "bad": "unhealthy"}[tone],
            "tone": tone, "attention": tone != "ok", "overage": max(0, value - critical)}


def _observer_state(observer, spec, now):
    if observer.get("kind") not in ("process", "heartbeat") or observer.get("subject") != "workload":
        return "unknown", "missing_workload_observer"
    observed = epoch(observer.get("observed_at"))
    if observed is None or observed > now + 360:
        return "unknown", "clock_skew" if observed is not None else "missing_observer_timestamp"
    if now - observed > observer.get("max_age_seconds", spec.get("heartbeat_max_age_seconds", 300)):
        return "down", "observer_stale"
    if observer.get("alive") is False and observer.get("termination_confirmed") is True:
        return "down", "terminated"
    if observer.get("alive") is True:
        return "up", "workload_observed"
    return "unknown", "observer_unknown"


def _legacy_check(spec, row, artifact, now):
    reasons, codes = [], []
    raw = row.get("last_rc")
    rc = norm_rc(raw)
    art_v, age = "unknown", None
    if spec.get("artifact"):
        art_v, reason, age = _age(artifact.get("mtime"), spec.get("artifact_max_age_hours"),
                                   spec.get("grace_hours"), now)
        codes.append(reason)
        if reason == "clock_skew":
            reasons.append("产物时间戳在未来 (clock skew)")
        elif age is None:
            reasons.append(artifact.get("reason") or "产物不可读")
        else:
            reasons.append(f"产物 {age:.1f}h 前更新")
    run_v, run_reason, _ = _age(row.get("last_run"), spec.get("max_age_hours"),
                                spec.get("grace_hours"), now)
    codes.append(run_reason)
    clears = spec.get("artifact_written_only_on_success") and art_v == "up"
    artifact_time, run_time = epoch(artifact.get("mtime")), epoch(row.get("last_run"))
    if (clears and spec.get("exit_code_is_authoritative") is not False
            and artifact_time is not None and run_time is not None and artifact_time < run_time):
        clears = False
    if spec.get("exit_code_is_authoritative"):
        clears = False
    if rc not in (None, RC_RUNNING, RC_NOT_RUN) and rc not in ok_codes(spec) and not clears:
        return "down", reasons + [f"退出码 {raw} 不在允许集 {sorted(ok_codes(spec))}"], codes + ["exit_failed"]
    if spec.get("artifact_cannot_prove_success") and art_v == "up":
        state = worst_of(run_v, "unknown" if rc is None else "up")
        reasons.append("产物只能证明它还在跑,证明不了跑对了")
    else:
        state = worst_of(art_v, run_v) if spec.get("artifact") else run_v
        if rc is None and not clears:
            state = worst_of(state, "unknown")
    if row.get("missed_runs") and state not in ("unknown", "running"):
        state = worst_of(state, "grace")
        reasons.append(f"漏跑 {row['missed_runs']} 次")
        codes.append("missed_runs")
    return state, reasons, codes


def evaluate_task(spec: dict, observations: dict, now) -> dict:
    """Evaluate a TaskSpec plus passive observations, returning report schemaVersion=1.

    Accepted observations: scheduler, artifacts keyed by declared path, current_run
    (or execution), last_success, checks, observer and optional health coverage.
    A last_run v1 receipt is preserved verbatim and can supply execution facts.
    """
    if is_dataclass(spec) and not isinstance(spec, type):
        spec = asdict(spec)
    now = epoch(now)
    if now is None:
        raise ValueError("now must be epoch seconds or a timezone-aware timestamp")
    if not isinstance(spec, dict) or not isinstance(observations, dict):
        raise ValueError("spec and observations must be objects")
    for key in ("scheduler", "current_run", "execution", "last_success", "health", "observer", "artifacts", "external_checks"):
        if observations.get(key) is not None and not isinstance(observations[key], dict):
            raise ValueError(f"{key} observation must be an object")
    row = observations.get("scheduler") or {}
    last_run = observations.get("last_run")
    current = deepcopy(observations.get("current_run") or observations.get("execution") or {})
    if not current and isinstance(last_run, dict):
        current = deepcopy(last_run.get("execution") or last_run)
    raw = current.get("raw_exit_code", current.get("exit_code", current.get("exitCode", row.get("last_rc"))))
    domain = current.get("code_domain", observations.get("code_domain", row.get("code_domain", "scheduler" if row else "process")))
    code = norm_rc(raw)
    execution_state = current.get("state", "unknown")
    if execution_state not in EXECUTION_STATES:
        execution_state = "unknown"
    if not current:
        if row.get("state") == "Running" or code == RC_RUNNING:
            execution_state = "running"
        elif code == RC_NOT_RUN:
            execution_state = "pending"
        elif code is not None:
            execution_state = "completed" if code in ok_codes(spec) else "failed"
    started = current.get("started_at", row.get("last_run"))
    reason_codes, reasons = [], []
    forced = None
    kind = spec.get("kind", "oneshot")
    if kind not in ("oneshot", "daemon"):
        raise ValueError("task kind must be oneshot or daemon")
    observer = observations.get("observer") or {}
    # A detached scheduler action describes the launcher, never the workload.
    detached = bool(spec.get("detached") or spec.get("watched_elsewhere"))
    if detached:
        execution_state = "unknown"
    observer_actual = observer.get("kind") in ("process", "heartbeat") and observer.get("subject") == "workload"
    if row.get("state") == "Disabled" or spec.get("enabled") is False:
        forced = "paused"
        reason_codes.append("disabled")
    elif detached and not observer_actual:
        execution_state, forced = "unknown", "unknown"
        reason_codes.append("missing_workload_observer")
    elif observations.get("query_failed"):
        forced = "unknown"
        reason_codes.append("query_failed")
    elif code == RC_NOT_RUN and not current:
        forced = "never"
        reason_codes.append("never_run")
    elif execution_state in ("busy", "deferred"):
        forced = "unknown"
        reason_codes.append("skipped_busy" if execution_state == "busy" else "deferred")
    elif not row and not current and not observations.get("checks") and not observer_actual and not observations.get("external_checks"):
        forced = "unknown"
        reason_codes.append("missing_source")

    if observer_actual and forced is None:
        observer_state, observer_reason = _observer_state(observer, spec, now)
        if observer_state == "up":
            execution_state = "running"
        else:
            forced = observer_state
            reason_codes.append(observer_reason)
            if observer_reason == "terminated":
                execution_state = "failed"

    # A completed receipt can also have an impossible timestamp after a clock reset.
    if forced not in ("down", "paused") and any(
            epoch(current.get(key)) is not None and epoch(current[key]) > now + 360
            for key in ("started_at", "finished_at")):
        forced = "unknown"
        reason_codes.append("clock_skew")

    if execution_state == "running" and forced is None and kind == "oneshot":
        start_epoch = epoch(started)
        deadline = epoch(current.get("deadline_at"))
        timeout = spec.get("timeout_seconds")
        if start_epoch is not None and start_epoch > now + 360:
            forced = "unknown"
            reason_codes.append("clock_skew")
        elif (deadline is not None and now > deadline) or (timeout is not None and start_epoch is not None and now - start_epoch > timeout):
            forced = "down"
            reason_codes.append("overdue")  # No termination is inferred.
        elif timeout is not None and start_epoch is None and deadline is None:
            forced = "unknown"
            reason_codes.append("missing_start")
        elif "kind" in spec and timeout is None and deadline is None:
            forced = "unknown"
            reason_codes.append("missing_deadline")
        else:
            forced = "running"
            reason_codes.append("running")

    definitions = spec.get("checks")
    legacy = definitions is None
    definitions = definitions if definitions is not None else [{"id": spec.get("check") or "default"}]
    supplied = observations.get("checks") or []
    if not isinstance(definitions, list) or not all(isinstance(d, dict) for d in definitions):
        raise ValueError("check declarations must be an array of objects")
    if not isinstance(supplied, list) or not all(isinstance(c, dict) for c in supplied):
        raise ValueError("check observations must be an array of objects")
    checks, seen = [], set()
    for index, definition in enumerate(definitions):
        check_id = definition.get("id") or f"missing-id-{index}"
        matches = [check for check in supplied if check.get("id", check.get("check_id")) == check_id]
        check = deepcopy(matches[0]) if len(matches) == 1 else {}
        legacy_config = definition.get("legacy") or {}
        if not isinstance(legacy_config, dict):
            raise ValueError("legacy check binding must be an object")
        cspec = {**spec, **legacy_config, **definition}
        cstate, why, codes = "unknown", [], []
        if check_id in seen or len(matches) > 1:
            codes.append("ambiguous_check")
        elif isinstance(definition.get("watched_elsewhere"), dict):
            binding = definition["watched_elsewhere"]
            external = (observations.get("external_checks") or {}).get(binding.get("observation_ref")) or {}
            if (not isinstance(external, dict) or external.get("component") != binding.get("component")
                    or external.get("check_id") != binding.get("check_id")):
                codes.append("missing_external_observation")
            else:
                external_observer = external.get("observer") or {}
                if not isinstance(external_observer, dict):
                    raise ValueError("external observer must be an object")
                cstate, reason = _observer_state(external_observer, cspec, now)
                codes.append(reason)
                if cstate == "up":
                    cstate = {"healthy": "up", "degraded": "grace", "unhealthy": "down"}.get(external.get("state"), "unknown")
                check = deepcopy(external)
        elif check:
            check_run = check.get("run_id")
            run_id = observations.get("run_id", current.get("run_id"))
            if run_id and check_run and check_run != run_id:
                codes.append("different_run")
            elif epoch(check.get("observed_at")) is not None and epoch(check["observed_at"]) > now + 360:
                codes.append("clock_skew")
            else:
                cstate = {"healthy": "up", "degraded": "grace", "unhealthy": "down"}.get(check.get("state"), "unknown")
                unknown_reason = {"error": "query_failed", "missing": "missing_check", "zero": "zero_coverage"}.get(check.get("state"), "unchecked")
                codes.append(check.get("reason_code") or ("observed" if cstate != "unknown" else unknown_reason))
                limit = cspec.get("max_age_hours")
                if cspec.get("max_age_seconds") is not None:
                    limit = cspec["max_age_seconds"] / 3600
                if limit is not None:
                    age_state, age_reason, _ = _age(check.get("observed_at", current.get("finished_at")),
                                                    limit, cspec.get("grace_hours"), now)
                    cstate = worst_of(cstate, age_state)
                    codes.append(age_reason)
        elif kind == "daemon" and observer_actual and (
                definition.get("id") == observer.get("check_id") or legacy and not spec.get("artifact")):
            cstate = forced or ("up" if observer.get("alive") is True else "unknown")
            codes.append("workload_observed" if cstate == "up" else "observer_unknown")
        elif legacy or legacy_config or definition.get("artifact") or definition.get("max_age_hours") is not None:
            artifact = (observations.get("artifacts") or {}).get(cspec.get("artifact"), {})
            cstate, why, codes = _legacy_check(cspec, row, artifact, now)
        else:
            codes.append("missing_check")
        seen.add(check_id)
        expected = max(1, _count(definition.get("expected", 1), "check expected"),
                       _count(check.get("expected", 1), "observed expected"))
        checked = min(expected, _count(check.get("checked", expected if cstate != "unknown" else 0), "check checked"))
        if cstate == "unknown":
            checked = 0
        if codes and codes[0] in ("ambiguous_check", "different_run", "clock_skew"):
            checked = 0
        if checked == 0:
            cstate = "unknown"
            if check and check.get("checked") == 0:
                codes.append("zero_coverage")
        checks.append(dict(check, id=check_id, check_id=check_id, state=HEALTH[cstate],
                           observed_state=check.get("state"),
                           legacy_state=cstate, required=definition.get("required", True),
                           checked=checked, expected=expected, reason_codes=codes, reasons=why))

    # Unexpected observations are visible but cannot replace missing declared checks.
    unexpected = [deepcopy(c) for c in supplied if c.get("id", c.get("check_id")) not in seen]
    checked = sum(c["checked"] for c in checks)
    expected = sum(c["expected"] for c in checks) + len(unexpected)
    if unexpected:
        reason_codes.append("unexpected_checks")
    reported = observations.get("health") or {}
    expected = max(expected, _count(reported.get("expected", 0), "report expected"),
                   _count(spec.get("expected", 0), "task expected"))
    if reported.get("checked") is not None:
        reported_checked = _count(reported["checked"], "report checked")
        if reported_checked > reported.get("expected", expected):
            raise ValueError("report checked exceeds expected")
        # Component counts can describe many subchecks summarized by named checks.
        # Missing or ambiguous named checks never borrow aggregate coverage.
        if checks and not unexpected and all(c["checked"] == c["expected"] for c in checks):
            checked = min(expected, reported_checked)
        else:
            checked = min(checked, reported_checked)
    if forced in ("unknown", "running", "paused"):
        # Retained check receipts do not establish coverage of this active/failed read.
        checked = 0
    cov = coverage(checked, expected)
    state = worst_of(*(c["legacy_state"] for c in checks if c["required"]))
    if any(c["state"] in ("degraded", "unhealthy") for c in checks if not c["required"]):
        state = worst_of(state, "grace")
    if forced is not None:
        state = forced
    elif kind == "daemon":
        # Scheduler running alone cannot waive daemon freshness checks.
        if observer_actual and state == "unknown" and not definitions:
            state = "up" if observer.get("alive") is True else "unknown"
        elif not observer_actual and not any(c.get("artifact") for c in definitions) and not spec.get("artifact") and not supplied:
            state = "unknown"
            reason_codes.append("missing_workload_observer")
    elif execution_state in ("failed", "cancelled") and not legacy and current:
        state = "down"
        reason_codes.append("execution_failed")
    elif not legacy and current and code is not None and code not in ok_codes(spec) and execution_state == "completed":
        state = "down"
        reason_codes.append("exit_failed")
    if state == "up" and (not expected or checked < expected):
        state = "unknown"
        reason_codes.append("zero_coverage" if not expected else "incomplete_coverage")
    for check in checks:
        reasons.extend(check["reasons"])
        reason_codes.extend(check["reason_codes"])
    reasons = list(dict.fromkeys(reasons + reason_codes))
    return {"schemaVersion": SCHEMA_VERSION, "component": spec.get("component"),
            "task_id": spec.get("task_id", spec.get("id", spec.get("name"))), "kind": kind,
            "run_id": observations.get("run_id", current.get("run_id")),
            "execution": dict(current, state=execution_state, exit_code=code,
                              raw_exit_code=raw, code_domain=domain, started_at=started,
                              code_subject="launcher" if detached else current.get("code_subject", "task")),
            "current_run": deepcopy(current) or None,
            "last_success": deepcopy(observations.get("last_success")),
            "last_success_run_id": observations.get("last_success_run_id", (observations.get("last_success") or {}).get("run_id")),
            "last_run_v1": deepcopy(last_run), "observer": deepcopy(observer) or None,
            "health": {"state": HEALTH[state], "checked": checked, "expected": expected},
            "coverage": cov, "checks": checks, "unexpected_checks": unexpected,
            "state": state, "verdict": HEALTH[state], "reason_codes": list(dict.fromkeys(reason_codes)),
            "reasons": reasons}


# ---- freshness ---------------------------------------------------------------------------------
# The page's artifact-freshness view: reads declared artifacts' mtimes and judges each
# declaration with health.evaluate_task.

DOWN, NEVER, UNKNOWN = "down", "never", "unknown"


def newest_mtime(path: str):
    """产物可以是一个文件,也可以是一个目录(比如一个按年份分目录的归档)。
    目录取里面最新那个文件的 mtime;空目录返回 None,**不是 0**:0 会被读成 1970 年,
    那是一个看起来很确定的错误答案。

    读不到就返回 None 并附上原因。返回 (mtime, reason)。"""
    p = os.path.expanduser(path)
    try:
        if os.path.isdir(p):
            best = None
            for name in os.listdir(p):
                try:
                    m = os.stat(os.path.join(p, name)).st_mtime
                except OSError:
                    continue
                if best is None or m > best:
                    best = m
            return (best, None) if best is not None else (None, "产物目录是空的")
        return os.stat(p).st_mtime, None
    except OSError as e:
        return None, f"读不到产物: {e.__class__.__name__}"


def evaluate(decls, rows, now, mtime_of=None):
    """Judge every declaration against its scheduler row and declared artifacts."""
    mtime_of = mtime_of or newest_mtime
    out = []
    for index, raw_decl in enumerate(decls):
        decl = raw_decl if isinstance(raw_decl, dict) else {}
        name = decl.get("name")
        row = rows.get(name) or {}
        artifacts = {}
        for check in [decl, *(decl.get("checks") or [])]:
            path = check.get("artifact") or (check.get("legacy") or {}).get("artifact")
            if path:
                mtime, reason = mtime_of(path)
                artifacts[path] = {"mtime": mtime, "reason": reason}
        obs = dict(row.get("observations") or {}, scheduler=row, artifacts=artifacts)
        result = evaluate_task(decl, obs, now)
        if not name:
            result.update(state=UNKNOWN, verdict="unknown", reasons=["missing task name"],
                          reason_codes=["invalid_declaration"])
        out.append(dict(result, name=name or f"[invalid declaration {index + 1}]",
                        check=decl.get("check"), label=decl.get("label") or name,
                        last_run=row.get("last_run"), next_run=row.get("next_run"),
                        last_rc=row.get("last_rc"), missed_runs=row.get("missed_runs"),
                        artifact=decl.get("artifact"), artifact_max_age_hours=decl.get("artifact_max_age_hours"),
                        cannot_prove_success=bool(decl.get("artifact_cannot_prove_success"))))
    counts = {}
    for item in out:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
    judged = sum(value for key, value in counts.items() if key != UNKNOWN)
    total = len(out)
    cov = coverage(judged, total)
    return {"schemaVersion": SCHEMA_VERSION,
            "tasks": sorted(out, key=lambda t: (-SEVERITY.get(t["state"], 2), t["name"])),
            "summary": {"total": total, "counts": counts, "judged": judged,
                        "coverage": cov["ratio"], "coverageVerdict": cov,
                        "bad": counts.get(DOWN, 0) + counts.get(NEVER, 0),
                        "attention": sum(counts.get(k, 0) for k in (DOWN, NEVER, UNKNOWN)),
                        "attentionStates": [DOWN, NEVER, UNKNOWN]}}


# ---- history -----------------------------------------------------------------------------------
# Reconstruct per-task history from a health monitor's log.
#
# WHAT THIS IS AND IS NOT. The Windows Task Scheduler keeps no run history unless its Operational
# event log is explicitly enabled, and on a machine where it was never turned on there is nothing to
# recover: history starts the day you enable it. What DOES exist is the log of a health monitor that
# polls every task on a fixed cadence and writes one verdict line per task per poll. That is an
# OBSERVATION series, not a run series, and the difference matters enough to name every time:
#
#   observed health   the fraction of POLLS in which a task looked healthy. A task that fails once
#                     and stays broken for a day contributes ~24 unhealthy observations, not one.
#                     So this measures "how much of the time it was in a bad state", which is a real
#                     and useful quantity, but it is NOT a per-run success rate and must never be
#                     labelled as one.
#
# ONE OBSERVATION PER TASK PER HOUR. Some tasks are written to the log more than once per poll (a
# multi-layer job reports per layer), which would silently give them a two or three times larger
# denominator than everything else and make their health percentage incomparable. Observations are
# therefore deduplicated on (task, hour) before anything is counted.
#
# The log format is the only thing this module knows about the monitor:
#     [YYYY-MM-DD HH:MM:SS]   <TaskName> : <verdict text>
# Anything that does not match is skipped, and the count of skipped lines is reported rather than
# swallowed, so a format change shows up as a number instead of as a quietly emptier chart.


LINE = re.compile(r"^\[(\d{4})-(\d{2})-(\d{2}) (\d{2}):\d{2}:\d{2}\]\s{2,}(\S[^:]*?) : (.+)$")
LASTRUN = re.compile(r"last (\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2} [AP]M)")


def classify(verdict: str) -> str:
    if verdict.startswith("OK"):
        return "ok"
    if "FAILED" in verdict or "NOT REGISTERED" in verdict:
        return "bad"
    if "STALE" in verdict or "MISSING" in verdict:
        return "stale"
    if "NEUTRAL" in verdict:
        return "neutral"
    return "other"


def load(path: str | os.PathLike | None, days: int = 45) -> dict:
    """Return per-task history, or a dict carrying only a reason when there is none."""
    if not path:
        return {"available": False,
                "reason": "没有设 TASK_CONSOLE_HISTORY,所以没有历史。这是「未提供」,不是「没有问题」。",
                "tasks": {}, "days": []}
    p = Path(os.path.expanduser(str(path)))
    if not p.exists():
        return {"available": False, "reason": f"历史日志不存在: {p}", "tasks": {}, "days": []}

    cutoff = date.today() - timedelta(days=days - 1)
    seen: set[tuple[str, str, str]] = set()          # (task, day, hour) dedup, see the header
    per: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    matched = skipped = 0

    try:
        # utf-8-SIG, not utf-8. The monitor writes this log with a BOM (EF BB BF, verified on the
        # live file), and a plain utf-8 read turns the very first line into one that starts with
        # U+FEFF, so the first observation of the whole series silently fails to match LINE.
        fh = p.open(encoding="utf-8-sig", errors="replace")
    except Exception as e:
        return {"available": False, "reason": f"读不了历史日志: {e}", "tasks": {}, "days": []}

    with fh:
        for raw in fh:
            m = LINE.match(raw.rstrip())
            if not m:
                if raw.strip():
                    skipped += 1
                continue
            y, mo, d, hh, task, verdict = m.groups()
            day = f"{y}-{mo}-{d}"
            try:
                if date(int(y), int(mo), int(d)) < cutoff:
                    continue
            except ValueError:
                skipped += 1
                continue
            task = task.strip()
            key = (task, day, hh)
            if key in seen:
                continue
            seen.add(key)
            matched += 1
            per[task][day][classify(verdict)] += 1

    if not matched:
        # An empty result and a broken parser look identical from the outside, so say which.
        return {"available": False,
                "reason": (f"历史日志里没有能解析的判定行(跳过 {skipped} 行)。"
                           f"要么格式变了,要么这个窗口内没有记录。"),
                "tasks": {}, "days": []}

    all_days = sorted({d for t in per.values() for d in t})
    out: dict[str, dict] = {}
    for task, days_map in per.items():
        tot = Counter()
        for c in days_map.values():
            tot.update(c)
        n = sum(tot.values())
        # neutral (never-run, disabled on purpose) is excluded from the denominator: counting a
        # deliberately-off task as unhealthy would drag its score down for doing what you asked.
        judged = n - tot["neutral"]
        out[task] = {
            "obs": n,
            "judged": judged,
            "ok": tot["ok"],
            "bad": tot["bad"],
            "stale": tot["stale"],
            "neutral": tot["neutral"],
            # other 是判词表认不出来的那些。它原来只进分母不出现在任何字段里,
            # 于是监控器换一种措辞(FAIL 而不是 FAILED、DEGRADED、WARN)之后,
            # 每一行会显示 health 0.0% 而 ok / bad / stale 全是 0:
            # **同一行里两个自称权威的数字互相矛盾,而没有任何字段说明观察去哪了**;
            # skipped 也不会涨,因为行本身是解析成功的。
            # 判据里凡有兜底桶,兜底桶就必须能被看见。
            "other": tot.get("other", 0),
            "health": round(100.0 * tot["ok"] / judged, 1) if judged else None,
            "byDay": {d: {"ok": c["ok"], "bad": c["bad"], "stale": c["stale"],
                          "neutral": c["neutral"], "n": sum(c.values())}
                      for d, c in days_map.items()},
        }
    return {
        "available": True,
        "reason": None,
        "source": str(p),
        "matched": matched,
        "skipped": skipped,
        "days": all_days,
        "tasks": out,
        # 主通路(数据库)在这两个键上交出摄入器的最后运行时刻和一个新鲜度判定。
        # 这条通路直接解析日志,压根不经过摄入器, 但「不适用」和「键不在」不能长得一样:
        # 键不在时,页面顶栏那段判定代码会静默地什么都不画,而那和「摄入器好好的」
        # 在屏幕上是同一个样子。所以这里显式说出「这条路上没有摄入这回事」。
        "lastIngest": None,
        "ingest": {"state": "n/a", "ageHours": None, "source": None, "at": None,
                   "failed": [],
                   "why": "当前走的是直接解析监控日志的回落通路,不经过摄入器,"
                          "所以没有摄入新鲜度这件事。数据新鲜度看日志本身的最后一行。"},
        "caveat": "检查通过率按巡检记录统计，同一任务可被重复检查。",
    }


# ---- timeline ----------------------------------------------------------------------------------
# Expand a task's triggers into the times it is due TODAY, for a 24 hour timeline.
#
# The scheduler exposes NextRunTime, which is one point. Laying out a day needs every point, so the
# triggers are expanded here from their structured form. Expanding a summarised string like
# "Daily/PT5M @21:00" back into times would be guessing, which is why collect.ps1 emits the fields.
#
# WHAT IS AND IS NOT ON THE TIMELINE. Only triggers with a clock time can be placed: Daily, Weekly,
# and a one-off Time trigger, each optionally repeating on an interval. Logon, Boot, Registration,
# Idle, Event and SessionStateChange triggers fire on a condition, not at an hour, and are reported
# separately as event-driven rather than dropped, because a task nobody can see on the timeline is a
# task nobody remembers exists.
#
# DENSE TASKS ARE A BAND, NOT 288 DOTS. A five-minute task has 288 occurrences a day. Drawing them
# individually turns the row into a solid line that says less than one bar plus a count, so a task
# whose occurrences exceed a threshold is emitted as a span with a count and the interval named.
#
# DISABLED TASKS ARE EXCLUDED. A disabled task has triggers but will not fire, and showing it as
# scheduled would be stating something false about today.


# Task Scheduler's DaysOfWeek bitmask. Sunday is bit 0, which does not match Python's Monday=0.
_DOW_BIT = {6: 1, 0: 2, 1: 4, 2: 8, 3: 16, 4: 32, 5: 64}  # python weekday() -> mask bit

_DUR = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")

# Above this many occurrences in a day, a row is drawn as a band instead of individual marks.
DENSE_THRESHOLD = 24
# 一天最多展开这么多个刻度。撞上限本身不是错误,但**必须说出来**:
# 见下面 truncated 那一段。
TICK_CAP = 5000


def parse_duration(s: str | None) -> int | None:
    """ISO-8601 duration to seconds. PT0S means unlimited in Task Scheduler, not zero, so it is
    returned as None rather than 0: a caller that treated it as zero would draw nothing."""
    if not s:
        return None
    m = _DUR.match(s.strip())
    if not m:
        return None
    d, h, mi, sec = (int(x) if x else 0 for x in m.groups())
    total = d * 86400 + h * 3600 + mi * 60 + sec
    return total or None


def parse_start(s: str | None) -> datetime | None:
    if not s:
        return None
    txt = s.strip()
    # Trim a timezone offset: everything here is local wall-clock, and the scheduler stores the
    # boundary in local time already. Keeping the offset would shift every row by the UTC delta.
    txt = re.sub(r"(Z|[+-]\d{2}:?\d{2})$", "", txt)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(txt, fmt)
        except ValueError:
            continue
    return None


def occurs_today(t: dict, today: date) -> bool:
    """Does this trigger's recurrence rule land on today at all."""
    st = parse_start(t.get("start"))
    if not st:
        return False
    if st.date() > today:
        return False
    end = parse_start(t.get("end"))
    if end and end.date() < today:
        return False

    kind = (t.get("kind") or "").lower()
    if kind == "daily":
        n = t.get("days") or 1
        return ((today - st.date()).days % max(n, 1)) == 0
    if kind == "weekly":
        mask = t.get("dow") or 0
        if not (mask & _DOW_BIT[today.weekday()]):
            return False
        n = t.get("weeks") or 1
        weeks_between = ((today - st.date()).days) // 7
        return (weeks_between % max(n, 1)) == 0
    if kind == "time":
        # A one-off, unless it repeats. A repeating one-off with no duration runs indefinitely,
        # which is how the minute-cadence tasks on this machine are actually defined.
        if t.get("interval"):
            return True
        return st.date() == today
    return False  # logon / boot / idle / event / registration: no clock time


def expand(task: dict, now: datetime | None = None) -> dict:
    """Return {'spans': [...], 'points': [...], 'eventDriven': [...]} for one task, today."""
    now = now or datetime.now()
    today = now.date()
    points: list[str] = []
    spans: list[dict] = []
    event_driven: list[str] = []
    unknown_kinds: list[str] = []

    if task.get("state") == "Disabled":
        return {"spans": [], "points": [], "eventDriven": [], "skipped": "disabled"}

    for t in task.get("triggersRaw") or []:
        if not t.get("enabled", True):
            continue
        kind = (t.get("kind") or "").lower()
        if kind in ("logon", "boot", "registration", "idle", "event", "sessionstatechange"):
            event_driven.append(t.get("kind") or "?")
            continue
        # 白名单式的分类必须有 else 分支。月度触发器,以及 MSFT_TaskTrigger 基类那种
        # kind 为空串的,原来两边都不匹配 : occurs_today 的最后一行 return False,
        # 于是整个任务在今日时间轴上**完全消失**,而且没有任何计数说「有几个我不认识」。
        # 这跟本模块开头写的规矩正相反:「一个没人能在时间轴上看到的任务,
        # 就是一个没人记得它存在的任务」。
        if kind not in ("daily", "weekly", "time"):
            unknown_kinds.append(t.get("kind") or "(空)")
            continue
        if not occurs_today(t, today):
            continue
        st = parse_start(t["start"])
        base = datetime.combine(today, clock_time(st.hour, st.minute, st.second))
        iv = parse_duration(t.get("interval"))
        if not iv:
            points.append(base.strftime("%H:%M"))
            continue
        dur = parse_duration(t.get("duration"))
        day_end = datetime.combine(today, clock_time(23, 59, 59))

        if dur:
            # A bounded repetition restarts from the trigger time each day it fires, so today's
            # window is [today at the boundary time, +duration].
            first, end_dt = base, min(base + timedelta(seconds=dur), day_end)
        else:
            # UNBOUNDED repetition. This is the case that was wrong: the repetition has been running
            # continuously since StartBoundary, so today it covers the WHOLE day, at a phase set by
            # that original boundary. Anchoring the band at the boundary's time-of-day truncated
            # every such task to "starts at 17:14 today", which is simply false. Measured: a PT5M
            # task whose boundary was set at 17:14 two months ago reported 82 occurrences where the
            # real number is 288, and another was cut to begin at 08:25 instead of midnight.
            # Occurrences today are every t in [00:00, 23:59] with (t - start) a multiple of iv.
            origin = parse_start(t["start"])
            day_start = datetime.combine(today, clock_time(0, 0, 0))
            elapsed = (day_start - origin).total_seconds()
            if elapsed >= 0:
                k = -(-elapsed // iv)                      # ceil, first tick at or after midnight
                first = origin + timedelta(seconds=k * iv)
            else:
                first = origin                             # boundary is later today
            end_dt = day_end

        n = 0
        cur = first
        marks = []
        while cur <= end_dt and n < TICK_CAP:
            marks.append(cur)
            cur += timedelta(seconds=iv)
            n += 1
        # 撞上限这件事必须说出来。原来撞了之后照样报一个确定的 count 和一个确定的 to:
        # 一个十秒级的任务会声称「00:00 到 13:53 共 5000 次」,而真实是全天 8640 次。
        # **一个数了一半却报出确定数字的结果,比不报还糟**(sysinfo 模块开头写着同一句)。
        truncated = (n >= TICK_CAP and cur <= end_dt)
        if not marks:
            continue
        if len(marks) > DENSE_THRESHOLD:
            spans.append({
                "from": marks[0].strftime("%H:%M"),
                "to": (day_end if truncated else marks[-1]).strftime("%H:%M"),
                "count": len(marks),
                "truncated": truncated,
                "every": t.get("interval"),
            })
        else:
            points.extend(m.strftime("%H:%M") for m in marks)

    return {"spans": spans, "points": sorted(set(points)),
            "eventDriven": sorted(set(event_driven)),
            # 认不出的类型照样出现在行里,像 eventDriven 那样。
            "unknownTriggers": sorted(set(unknown_kinds))}


def build(tasks: dict, runs_by_task: dict | None = None, now: datetime | None = None) -> dict:
    """Timeline payload for every task, plus today's ACTUAL runs where they are known."""
    now = now or datetime.now()
    today = now.date().isoformat()
    rows = []
    for name, t in tasks.items():
        e = expand(t, now)
        actual = []
        if runs_by_task:
            r = runs_by_task.get(name) or {}
            actual = [x for x in (r.get("todayRuns") or [])]
        # unknownTriggers 也算「这一行有东西」。
        # ⚠ 这个过滤条件原来只看 points / spans / eventDriven / actual 四项,拼出来的 row
        # 里也没有 unknownTriggers 这个键, 而 expand() 上面那行注释白纸黑字写着
        # 「认不出的类型照样出现在行里,像 eventDriven 那样」。注释断言的行为在代码里不存在,
        # 而它声称修好的正是同一个缺陷。
        # 后果:一个只配了 Monthly 触发器(或 CimClassName 解析出空 kind)的任务,
        # points/spans/eventDriven 全空 -> 直接 continue -> **今日时间轴上一行都没有**,
        # 而页面上没有任何一处说「有 1 个触发器我不认识」。
        # 屏幕表现与「这个任务今天本来就不该跑」逐像素相同。
        unknown = e.get("unknownTriggers") or []
        if e.get("skipped") or (not e["points"] and not e["spans"]
                                and not e["eventDriven"] and not unknown and not actual):
            continue
        rows.append({
            "name": name,
            "points": e["points"], "spans": e["spans"],
            "eventDriven": e["eventDriven"], "unknownTriggers": unknown,
            "actual": actual,
            "sk": t.get("sk"),
        })
    rows.sort(key=lambda r: (r["points"][0] if r["points"]
                             else (r["spans"][0]["from"] if r["spans"] else "zz")))
    return {
        "date": today,
        "now": now.strftime("%H:%M"),
        "rows": rows,
        "note": ("按触发器展开的今日计划。事件驱动的触发器(登录/开机/空闲/事件)没有时钟时间,"
                 "单独标出而不是丢掉。分钟级任务画成条带加次数,288 个点连成一条实线反而什么也没说。"
                 "已停用的任务不画:它有触发器但不会触发,画上去就是对今天做了一个假陈述。"),
    }


# ---- evtlog ------------------------------------------------------------------------------------
# TaskScheduler 运行日志的读法:EvtQuery + 渲染 XML(pywin32),这是唯一的读取器。
#
# 比 Get-WinEvent 快三个数量级:慢的是每条事件都去解析一次 provider 元数据。
# 读不到时返回 enabled=False 并说明原因,不装作成功:一条空的运行日志和一段读不到的
# 运行日志在界面上不能长得一样。


# TaskScheduler 的 Operational 日志。102 是任务级判决,201 是动作级返回码,两者可以不一致。
CHANNEL = "Microsoft-Windows-TaskScheduler/Operational"
WANTED = (100, 102, 111, 201, 203, 329)
# 计数事件 → 汇总字段。201(动作返回码)不计数,按 rc 归一后另收。
EVENT_FIELDS = {100: "starts", 102: "done", 111: "killed", 203: "failStart", 329: "timedOut"}
ACTION_RC = 201

_NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"


def _is_no_more(e) -> bool:
    """EvtNext 读完时抛 ERROR_NO_MORE_ITEMS(259),那是正常结束不是失败。
    有些 pywin32 版本只有消息文本,所以按词边界匹配 259(`12590` 不算)。"""
    if getattr(e, "winerror", None) == 259:
        return True
    return re.search(r"(?<!\d)259(?!\d)", str(e)) is not None


def channel_enabled() -> tuple[bool | None, str | None]:
    """通道配置里的 IsEnabled。None 表示问不出来(和「关着」不是一回事)。

    关着的通道只要文件还在,EvtQuery 照样打得开并返回零条,所以要单独问。
    """
    try:
        import win32evtlog
        cfg = win32evtlog.EvtOpenChannelConfig(CHANNEL)
        val = win32evtlog.EvtGetChannelConfigProperty(
            cfg, win32evtlog.EvtChannelConfigEnabled)
        # 这个 API 在不同 pywin32 版本上要么直接给 bool,要么给 (值, 类型) 元组。
        if isinstance(val, tuple):
            val = val[0]
        return bool(val), None
    except Exception as e:
        return None, f"读不到通道配置: {e.__class__.__name__}"


def log_stats() -> tuple[int | None, int | None]:
    """(通道里最老的记录号, 通道里的记录总数)。问不出来就是 (None, None)。

    第一个数是**日志被清空的探测器**:EventRecordID 在日志被清空后从 1 重新开始,
    不看它的话,重新开始的记录号会长得像已经摄入过的旧记录号,新事件被去重逻辑整批丢掉,
    而两边的计数都还是「正常」的。摄入器把它存进 runlog_ingest,下一轮拿来比。
    """
    try:
        import win32evtlog
        h = win32evtlog.EvtOpenLog(CHANNEL, win32evtlog.EvtOpenChannelPath)
        out = []
        for prop in (win32evtlog.EvtLogOldestRecordNumber,
                     win32evtlog.EvtLogNumberOfLogRecords):
            v = win32evtlog.EvtGetLogInfo(h, prop)
            # 这个 API 返回 (值, 类型) 元组。
            out.append(int(v[0] if isinstance(v, tuple) else v))
        return out[0], out[1]
    except Exception:
        return None, None


def _xpath(ids, since: _dt.datetime | None) -> str:
    cond = " or ".join(f"EventID={i}" for i in ids)
    if since is None:
        return f"*[System[({cond})]]"
    # 事件日志的 XPath 只接受 UTC 的 ISO 时间。传本地时间会静默少取或多取一段。
    stamp = since.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return f"*[System[({cond}) and TimeCreated[@SystemTime>='{stamp}']]]"


def _parse(xml: str) -> dict | None:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    sysel = root.find(f"{_NS}System")
    if sysel is None:
        return None

    def txt(tag):
        el = sysel.find(f"{_NS}{tag}")
        return el.text if el is not None else None

    tc = sysel.find(f"{_NS}TimeCreated")
    stamp = tc.get("SystemTime") if tc is not None else None
    name = rc = None
    data = root.find(f"{_NS}EventData")
    if data is not None:
        for d in data.findall(f"{_NS}Data"):
            n = d.get("Name")
            if n == "TaskName":
                name = d.text
            elif n in ("ResultCode", "ReturnCode"):
                rc = d.text
    if not name or not stamp:
        return None
    try:
        # 事件里是 UTC,面板上其余时间都是本地时间,不转会让时间轴整体偏移。
        t = _dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None
    try:
        eid = int(txt("EventID") or 0)
        rid = int(txt("EventRecordID") or 0)
    except (TypeError, ValueError):
        return None
    return {"task": name.lstrip("\\"), "id": eid, "rid": rid,
            "t": t.strftime("%Y-%m-%d %H:%M:%S"), "rc": rc}


def read(days: int, max_events: int) -> dict:
    import win32evtlog

    since = _dt.datetime.now().astimezone() - _dt.timedelta(days=days)

    on, why = channel_enabled()
    if on is False:
        return {"enabled": False,
                "reason": "任务运行历史日志是关闭的。开启它是开始记录,不是恢复记录。",
                "events": []}
    # 问不出来不能当成开着。
    if on is None:
        return {"enabled": False, "reason": why or "读不到通道配置", "events": []}
    try:
        q = win32evtlog.EvtQuery(
            CHANNEL, win32evtlog.EvtQueryReverseDirection | win32evtlog.EvtQueryChannelPath,
            _xpath(WANTED, since))
    except Exception as e:
        # 日志不存在、没开启、或没权限。三种都不是「没有事件」。
        return {"enabled": False, "reason": f"打不开事件日志: {e.__class__.__name__}",
                "events": []}

    rows, dropped, broke = [], 0, None
    while len(rows) < max_events:
        try:
            batch = win32evtlog.EvtNext(q, 100)
        except Exception as e:
            # 读到一半失败要报出来,不能和读完了混成同一个 break。
            if not _is_no_more(e):
                broke = e.__class__.__name__
            break
        if not batch:
            break
        for ev in batch:
            try:
                xml = win32evtlog.EvtRender(ev, win32evtlog.EvtRenderEventXml)
            except Exception:
                dropped += 1
                continue
            row = _parse(xml)
            if row is None:
                dropped += 1
                continue
            rows.append(row)
            if len(rows) >= max_events:
                break

    oldest_rid, record_count = log_stats()
    return {
        "enabled": True,
        "reason": (f"事件读到一半失败({broke}),下面的条数是不完整的" if broke else None),
        "partial": bool(broke),
        "since": since.strftime("%Y-%m-%d"),
        "oldest": rows[-1]["t"] if rows else None,
        "count": len(rows),
        # 解析不了的事件要报出来。悄悄丢掉会让「这台机器没记录」和「我读不懂它记的东西」
        # 变成同一个空列表。
        "dropped": dropped,
        "truncated": len(rows) >= max_events,
        # 下面三个只有摄入器用:去重的水位线、日志被清空的探测器、通道里的记录总数。
        "maxRecordId": max((r["rid"] for r in rows if r.get("rid")), default=0),
        "oldestRecordId": oldest_rid,
        "recordCount": record_count,
        "events": rows,
    }


# ---- console_store -----------------------------------------------------------------------------
# Read side of the task-console database, plus the one place that decides where the file lives.
#
# WHERE THE FILE LIVES, and why this module refuses rather than guesses. The database holds real-run
# output: what ran on this machine and when. Under this fleet's data boundary that can never sit in
# the public repo, not even gitignored, because .gitignore is advisory and `git add -f` walks straight
# through it. Resolution order:
#
#     1. TASK_CONSOLE_DB, if set. An explicit operator override.
#     2. <companion>/data/task-console/console.sqlite3, where <companion> comes from the shared
#        datadir resolver.
#     3. UNINITIALISED. Not a fallback into the repo, not a temp file: a stated condition with
#        instructions. A repo-relative fallback is how real data ends up in a public repo, and it is
#        the specific defect this boundary exists to prevent.
#
# Resolution is a path decision only: no Git or GitHub calls happen at runtime. The one check kept
# is a pure path comparison that refuses a destination inside this payload's own source tree. Keeping
# DATA out of public repositories is enforced at commit and push time by the fleet-guards hooks.
#
# READS DEGRADE, WRITES DO NOT. Readers open the file read-only through a URI and, when it is absent
# or unreadable, return a state object carrying a code (NO_COMPANION, NO_RESOLVER, STORAGE_REFUSED,
# NO_FILE or CORRUPT) and a Chinese sentence explaining it. They never return zeros. "No data" and "zero runs" are different claims and only
# one of them is ours to make.


HERE = Path(__file__).resolve().parent
SKILL = "task-console"
DB_NAME = "console.sqlite3"

# A wheel carries the source-owned contract beside the installed package.
_checkout = HERE.parents[1]
SOURCE_ROOT = (_checkout if (_checkout / ".git").exists()
               and (_checkout / "pyproject.toml").is_file() else HERE)
_datadir = None


def _load_datadir():
    global _datadir
    if _datadir is None:
        try:
            from fleet_guards.runtime import companion_resolver
        except ImportError as error:
            raise ValueError('fleet_guards is not installed; install it: '
                             'pip install -e "$(git config --get fleet.guards)"') from error
        _datadir = companion_resolver(source_root=SOURCE_ROOT)
    return _datadir


def companion_root():
    """The companion root from the fleet_guards resolver, or None when uninitialized."""
    root = _load_datadir().resolve_companion_root(SKILL)
    return Path(root).absolute() if root else None


def outside_source(destination):
    """Refuse a DATA destination inside this payload's own source tree. Pure path comparison."""
    path = Path(destination).expanduser().absolute()
    _load_datadir().assert_outside_own_repo(path, SKILL)
    return path


def check_write_path(destination):
    """Path-only check for an external configuration leaf; creates nothing, runs no Git.

    Refuses unsafe lexical forms and links (Guards filesystem rules) and any destination inside
    this payload's own source tree, including its Git metadata.
    """
    from fleet_guards import filesystem

    path = filesystem.validate_path(Path(destination).expanduser().absolute())
    return outside_source(path)


class DbState:
    """Why there is no database, in a form the page can render verbatim."""

    def __init__(self, code: str, message: str, path: str | None = None):
        self.code, self.message, self.path = code, message, path

    def as_dict(self):
        return {"available": False, "code": self.code, "reason": self.message, "path": self.path}


def resolve_db(create_parent: bool = False):
    """Return the database path or a distinct unavailable state; never write on reads."""
    try:
        if os.environ.get("TASK_CONSOLE_DB"):
            p = Path(os.environ["TASK_CONSOLE_DB"]).expanduser().absolute()
        else:
            root = companion_root()
            if root is None:
                return None, DbState("NO_COMPANION",
                    "尚未配置伴生仓。请按 CONFIG.md 设置私有伴生仓。")
            p = root / "data" / "task-console" / DB_NAME
        p = outside_source(p)
        if create_parent:
            p.parent.mkdir(parents=True, exist_ok=True)
        return p, None
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        code = "NO_RESOLVER" if "fleet_guards is not installed" in str(error) else "STORAGE_REFUSED"
        return None, DbState(code, "存储位置无效: " + str(error))


def validate_read_schema(connection):
    """Check the declared schema version and columns without reading or changing runtime rows."""
    expected = sqlite3.connect(":memory:")
    try:
        expected.executescript((HERE / "schema.sql").read_text(encoding="utf-8"))
        if connection.execute("PRAGMA user_version").fetchone()[0] != expected.execute("PRAGMA user_version").fetchone()[0]:
            raise sqlite3.DatabaseError("database schema version does not match this payload")
        tables = expected.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        for (table,) in tables:
            literal = '"' + table.replace('"', '""') + '"'
            required = {row[1] for row in expected.execute("PRAGMA table_info(" + literal + ")")}
            present = {row[1] for row in connection.execute("PRAGMA table_info(" + literal + ")")}
            if not required <= present:
                raise sqlite3.DatabaseError("database lacks required columns in " + table)
    finally:
        expected.close()


def connect_ro():
    """Read-only connection, or (None, DbState). Read-only is not a nicety: the console serves HTTP
    and must never be able to write, so a bug there cannot corrupt the ingester's file."""
    p, st = resolve_db()
    if st:
        return None, st
    if not p.exists():
        return None, DbState("NO_FILE",
                             f"数据库还没建。跑一次 python -m task_console ingest --backfill 就会从现有日志重建。",
                             str(p))
    con = None
    try:
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        validate_read_schema(con)
        return con, None
    except sqlite3.DatabaseError as e:
        # 调用方拿不到这个连接,显式关掉,不依赖引用计数的时机。
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
        return None, DbState("CORRUPT",
                             f"数据库打不开或结构不对({e})。请保留原文件并核对一致性备份。"
                             "--backfill 只能重读仍可用的源日志，不能完整恢复历史配置和事件。", str(p))


# --------------------------------------------------------------------------- queries
def health_by_day(con, days: int = 45) -> dict:
    rows = con.execute(
        "SELECT task, day, klass, COUNT(*) n FROM health_obs "
        "WHERE day >= date('now','localtime',?) GROUP BY task, day, klass",
        (f"-{days - 1} day",)).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        d = out.setdefault(r["task"], {}).setdefault(r["day"],
                                                     {"ok": 0, "bad": 0, "stale": 0, "neutral": 0, "n": 0})
        d[r["klass"]] = d.get(r["klass"], 0) + r["n"]
        d["n"] += r["n"]
    return out


def health_totals(con, days: int = 45) -> dict:
    rows = con.execute(
        "SELECT task, klass, COUNT(*) n FROM health_obs "
        "WHERE day >= date('now','localtime',?) GROUP BY task, klass",
        (f"-{days - 1} day",)).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["task"], {})[r["klass"]] = r["n"]
    for t, c in out.items():
        n = sum(c.values())
        judged = n - c.get("neutral", 0)
        c["obs"], c["judged"] = n, judged
        c["health"] = round(100.0 * c.get("ok", 0) / judged, 1) if judged else None
    return out


def run_totals(con, days: int = 60) -> dict:
    rows = con.execute(
        "SELECT task, event_id, rc_norm, COUNT(*) n FROM run_event "
        "WHERE day >= date('now','localtime',?) GROUP BY task, event_id, rc_norm",
        (f"-{days - 1} day",)).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        t = out.setdefault(r["task"], {**dict.fromkeys(EVENT_FIELDS.values(), 0), "rcs": {}})
        i, n = r["event_id"], r["n"]
        if i in EVENT_FIELDS:
            t[EVENT_FIELDS[i]] += n
        elif i == ACTION_RC and r["rc_norm"] is not None:
            rc = str(r["rc_norm"])
            t["rcs"][rc] = t["rcs"].get(rc, 0) + n
    return out


def runs_today(con) -> dict:
    rows = con.execute(
        "SELECT task, substr(ts,12,5) hm FROM run_event "
        "WHERE event_id = 100 AND day = date('now','localtime') ORDER BY ts").fetchall()
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r["task"], []).append(r["hm"])
    return {k: sorted(set(v)) for k, v in out.items()}


# 摄入器停摆多久算「要人管」。这两个数不是随手定的档位,它们量的是**丢失风险**:
# Windows 的任务运行日志是滚动缓冲,实测约 5-8 天覆盖一轮,所以没被摄入的运行历史
# 过了那个窗口就是永久没了, 不是「晚点补」,是补不回来。
#   24h  这台机器上任何一条自动流水线的正常间隔都远小于一天。超过一天没摄入,
#        要么排班没了,要么在连续失败。此时还追得回来,所以是「注意」不是「严重」。
#   96h  进入丢失边缘。按实测最短的那个覆盖窗口(5 天)留一天余量,过了这条线
#        就要按「已经开始丢」处理。
INGEST_WARN_HOURS = 24.0
INGEST_LOSS_HOURS = 96.0


def ingest_verdict(last_ingest: dict | None, now=None) -> dict:
    """摄入器还活着吗:一个判定,不是一个时间戳。

    取最旧的那一条流水线判定:任何一条停了,页面上就有一部分数字停在那一刻。
    没有任何摄入记录时是 `never`,不是 `ok`。
    """
    import datetime as _dt

    now = _dt.datetime.now() if now is None else now

    def _parse(s):
        # finished_at 只由 console_ingest.now() 写入,格式固定。
        try:
            return _dt.datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    parsed = [(k, v, _parse(v["at"])) for k, v in (last_ingest or {}).items()
              if isinstance(v, dict) and v.get("at")]
    parsed = [(k, v, t) for k, v, t in parsed if t is not None]
    if not parsed:
        return {"state": "never", "ageHours": None, "source": None, "at": None,
                "failed": [], "why": "摄入器没有留下任何运行记录 —— 库里的数字要么是"
                                     "手动灌的,要么根本没有。跑一次 python -m task_console ingest。"}
    failed = sorted(k for k, v, _t in parsed if v.get("ok") is False)

    parsed.sort(key=lambda x: x[2])
    src, row, at = parsed[0]
    age = (now - at).total_seconds() / 3600.0

    if failed:
        state = "failed"
        why = ("摄入器最近一次运行是失败的(" + "、".join(failed)
               + ")。这些数字停在上一次成功那一刻,而页面其余部分看不出异常。")
    elif age >= INGEST_LOSS_HOURS:
        state = "loss"
        why = (f"{src} 已经 {age / 24:.1f} 天没摄入。Windows 运行日志是滚动缓冲,"
               f"实测约 5-8 天覆盖一轮,所以这段历史很可能已经永久没了。")
    elif age >= INGEST_WARN_HOURS:
        state = "stale"
        why = (f"{src} 已经 {age:.0f} 小时没摄入,页面上的运行与健康数字都停在那时候。"
               f"再拖到 {INGEST_LOSS_HOURS / 24:.0f} 天就进丢失窗口了。")
    else:
        state = "ok"
        why = None

    return {"state": state, "ageHours": round(age, 2), "source": src,
            "at": row.get("at"), "failed": failed, "why": why}


def db_coverage(con) -> dict:
    """What the database actually covers, so the page can say it instead of implying it."""
    def one(sql, *a):
        r = con.execute(sql, a).fetchone()
        return dict(r) if r else {}
    h = one("SELECT MIN(day) a, MAX(day) b, COUNT(*) n FROM health_obs")
    r = one("SELECT MIN(day) a, MAX(day) b, COUNT(*) n FROM run_event")
    ing = con.execute("SELECT source, MAX(finished_at) t, ok FROM ingest_run "
                      "WHERE finished_at IS NOT NULL GROUP BY source").fetchall()
    return {
        "health": {"from": h.get("a"), "to": h.get("b"), "rows": h.get("n", 0)},
        "runs": {"from": r.get("a"), "to": r.get("b"), "rows": r.get("n", 0)},
        "lastIngest": {x["source"]: {"at": x["t"], "ok": bool(x["ok"])} for x in ing},
    }


# ---- console_ingest ----------------------------------------------------------------------------
# Fill the task-console database. THE ONLY WRITER.
#
# Runs out of band, not from the web request. That split is most of the point: reading the Windows
# Operational event log costs 109 seconds end to end and was on the request path, so every page load
# paid for it. Here nobody is waiting on it.
#
# How often it runs belongs to the machine, not to this repo. The health half and the run-event
# half are driven separately: `--skip-runlog` is what the high-frequency caller passes.
#
# WHY INGESTION IS NOT OPTIONAL. The Operational log is a circular buffer. Measured on this machine:
# 635 events an hour, 64 MB capacity, so roughly FIVE DAYS before the oldest records are overwritten.
# Anything not ingested before then is gone and cannot be recovered from anywhere. That turns this
# script from an optimisation into the thing that makes long-range history exist at all.
#
# CONCURRENCY. One writer, readers are read-only, WAL. Writes take BEGIN IMMEDIATE so two ingesters
# cannot interleave, and busy_timeout gives a reader time to finish rather than failing it.
#
# DEGRADATION. A source that cannot be read is recorded as a failed ingest_run row and the script
# exits non-zero. It never writes a partial pass and calls it done, because an ingest that silently
# half-ran and one that succeeded produce the same empty-looking chart later.


HERE = Path(__file__).resolve().parent
SCHEMA = HERE / "schema.sql"

# 一次摄入最多读多少条事件。比页面那边(server.RUNLOG_MAX_EVENTS)大是刻意的:
# 页面是一次点击要等的东西,摄入是后台补历史的东西。
MAX_EVENTS = 500000
COLLECT = HERE / "collect.ps1"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def open_rw():
    p, st = resolve_db(create_parent=True)
    if st:
        print(f"[{st.code}] {st.message}", file=sys.stderr)
        return None, None
    con = sqlite3.connect(str(p), timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    return con, p


def begin(con):
    con.execute("BEGIN IMMEDIATE")


def rollback(con):
    """回滚,并容忍没有活动事务(begin 自己失败时就没有)。"""
    try:
        con.execute("ROLLBACK")
    except Exception:
        pass


def note_run(con, source, started, ok, added, msg=""):
    con.execute("INSERT INTO ingest_run(started_at,finished_at,ok,source,added,note) VALUES(?,?,?,?,?,?)",
                (started, now(), 1 if ok else 0, source, added, msg[:500]))


# --------------------------------------------------------------------------- health
def ingest_health(con, path: str, full: bool) -> tuple[bool, int, str]:
    started = now()
    p = Path(os.path.expanduser(path))
    if not p.exists():
        note_run(con, "health", started, False, 0, f"log missing: {p}")
        return False, 0, f"健康日志不存在: {p}"

    # Rotation/truncation detector. The monitor only ever appends, so a changed head or a shrunk
    # file means the log was replaced by hand, and a watermark against the old one would skip rows.
    head = p.open("rb").read(512)
    sig = hashlib.sha256(head).hexdigest()[:16]
    size = p.stat().st_size
    prev = con.execute("SELECT * FROM health_ingest WHERE id=1").fetchone()
    rotated = bool(prev) and (prev["head_sig"] != sig or (prev["size_bytes"] or 0) > size)
    if rotated:
        full = True

    h = load(str(p), days=100000 if full else 60)
    if not h.get("available"):
        note_run(con, "health", started, False, 0, h.get("reason", "")[:400])
        return False, 0, h.get("reason", "")

    added = 0
    try:
        begin(con)
        if full:
            con.execute("DELETE FROM health_obs")
        # history.load gives per-day class counts; re-parse for the hour, which it drops.
        for task, day, hour, klass, verdict, lastrun in _iter_health_rows(p, h):
            cur = con.execute(
                "INSERT OR IGNORE INTO health_obs(task,day,hour,klass,verdict,last_run) "
                "VALUES(?,?,?,?,?,?)", (task, day, hour, klass, verdict, lastrun))
            added += cur.rowcount
            con.execute("INSERT INTO task_seen(task,first_seen,last_seen) VALUES(?,?,?) "
                        "ON CONFLICT(task) DO UPDATE SET last_seen=excluded.last_seen",
                        (task, f"{day} {hour:02d}:00:00", f"{day} {hour:02d}:00:00"))
        con.execute(
            "INSERT INTO health_ingest(id,source_path,head_sig,size_bytes,last_ts,last_ingest_at) "
            "VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "source_path=excluded.source_path, head_sig=excluded.head_sig, "
            "size_bytes=excluded.size_bytes, last_ts=excluded.last_ts, "
            "last_ingest_at=excluded.last_ingest_at",
            (str(p), sig, size, h.get("days", [None])[-1] if h.get("days") else None, now()))
        note_run(con, "health", started, True, added,
                 "full rebuild (rotation detected)" if rotated else ("full" if full else "incremental"))
        con.execute("COMMIT")
    except Exception as e:
        rollback(con)
        note_run(con, "health", started, False, 0, str(e)[:400])
        return False, 0, str(e)
    return True, added, ""


def _iter_health_rows(p: Path, h: dict):
    """Re-walk the log keeping the HOUR, which history.load deduplicates on and then discards."""
    seen = set()
    with p.open(encoding="utf-8-sig", errors="replace") as fh:
        for raw in fh:
            m = LINE.match(raw.rstrip())
            if not m:
                continue
            y, mo, d, hh, task, verdict = m.groups()
            task, day, hour = task.strip(), f"{y}-{mo}-{d}", int(hh)
            key = (task, day, hour)
            if key in seen:
                continue
            seen.add(key)
            lr = LASTRUN.search(verdict)
            yield task, day, hour, classify(verdict), verdict[:300], lr.group(1) if lr else None


# --------------------------------------------------------------------------- run log
def ingest_runlog(con, days: int) -> tuple[bool, int, str]:
    started = now()
    try:
        pass
        raw = read(days=days, max_events=MAX_EVENTS)
    except Exception as e:
        raw = {"enabled": False, "reason": f"读运行日志失败: {e.__class__.__name__}: {e}"}
    if not raw.get("enabled"):
        why = raw.get("reason") or "运行历史日志是关闭的"
        note_run(con, "runlog", started, False, 0, why[:400])
        return False, 0, why

    # enabled=True 不等于读成功:中途失败时带着 reason(和 partial=True)交回半批事件。
    # 一条都没读到就是失败;读到一些就入库,但不算成功,也不写成功戳。
    if raw.get("reason") and not (raw.get("events") or []):
        why = raw.get("reason")
        note_run(con, "runlog", started, False, 0, f"读取失败: {why}"[:400])
        return False, 0, why
    partial = bool(raw.get("partial")) or bool(raw.get("reason"))

    oldest = raw.get("oldestRecordId")
    prev = con.execute("SELECT * FROM runlog_ingest WHERE id=1").fetchone()
    epoch = prev["log_epoch"] if prev else 1
    # THE CLEAR DETECTOR. EventRecordID restarts at 1 when the Operational log is cleared. Without
    # this, INSERT OR IGNORE on (record_id) would silently drop every new event whose id collided
    # with an old one, and the only symptom would be a chart that quietly got emptier.
    if prev and oldest is not None and prev["oldest_record_id"] is not None and oldest < prev["oldest_record_id"]:
        epoch = epoch + 1

    added = 0
    try:
        begin(con)
        for e in raw.get("events") or []:
            rid = e.get("rid")
            if rid is None:
                continue
            ts = e["t"]
            rc_raw = e.get("rc")
            cur = con.execute(
                "INSERT OR IGNORE INTO run_event(log_epoch,record_id,task,event_id,ts,day,hour,rc_raw,rc_norm) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (epoch, int(rid), e["task"], int(e["id"]), ts, ts[:10], int(ts[11:13]),
                 rc_raw, norm_rc(rc_raw)))
            added += cur.rowcount
        con.execute(
            "INSERT INTO runlog_ingest(id,log_epoch,max_record_id,oldest_record_id,record_count,last_ingest_at) "
            "VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "log_epoch=excluded.log_epoch, max_record_id=excluded.max_record_id, "
            "oldest_record_id=excluded.oldest_record_id, record_count=excluded.record_count, "
            "last_ingest_at=excluded.last_ingest_at",
            (epoch, raw.get("maxRecordId") or 0, oldest, raw.get("recordCount"), now()))
        note_run(con, "runlog", started, not partial, added,
                 f"{'⚠部分读取: ' + str(raw.get('reason')) + ' ' if partial else ''}"
                 f"epoch={epoch} oldest={oldest} "
                 f"events={len(raw.get('events') or [])}"
                 + ("" if oldest is not None else " ⚠oldestRecordId 读不到,日志清空探测器这一轮是关的"))
        con.execute("COMMIT")
    except Exception as ex:
        rollback(con)
        note_run(con, "runlog", started, False, 0, str(ex)[:400])
        return False, 0, str(ex)
    if not partial:
        _stamp_runlog_success(added)
    return (not partial), added, (str(raw.get("reason")) if partial else "")


def _stamp_runlog_success(added: int) -> None:
    """Touch a file that ONLY a successful runlog ingest writes.

    The task-creation spec requires a declared artifact that a failing run cannot produce, because
    a monitor watching an artifact that the failure path also writes is watching nothing. Everything
    else this script touches fails that test: the .sqlite3 is written on every run including the
    ones that record a failure, and stdout is not a file.

    last_ingest_at inside runlog_ingest is already success-only -- the failure path ROLLBACKs -- but
    it lives in the database, and the health monitor reads file mtimes, not SQL. So the same fact is
    mirrored where the monitor can see it.

    Written AFTER the COMMIT on purpose. A stamp written before the transaction lands would claim a
    success that a rollback could still take away.
    """
    try:
        p, _ = resolve_db()
        if not p:
            return
        stamp = Path(p).parent / "runlog-ingest-ok.txt"
        stamp.write_text("%s added=%d" % (now(), added) + chr(10), encoding="utf-8")
    except Exception:
        # A stamp that cannot be written must not fail an ingest that DID succeed. The monitor will
        # see a stale artifact and say so, which is the correct outcome: the work happened, the
        # evidence did not.
        pass


def _watermark_from_file(out: Path) -> tuple[int, int]:
    """从 JSONL 末尾读回最后一条的 (log_epoch, record_id)。读不出来就是 (0, 0)。

    只读文件尾部,不整读:这个文件按设计会长到几十 MB,而每轮摄入都要问它一次。
    (0, 0) 表示「问不出来」,那会让这一轮从 meta 的水位线开始 ——
    对一个空文件是正确的,对一个读不动的文件是保守的(宁可重导也不漏导:
    重复行可以事后按 (epoch, record_id) 去重,丢掉的事件找不回来)。
    """
    try:
        size = out.stat().st_size
    except OSError:
        return 0, 0
    if not size:
        return 0, 0
    try:
        with out.open("rb") as fh:
            # 一行 JSON 约 120 字节,8KB 足够兜住最后一行;文件更短就整读。
            fh.seek(max(0, size - 8192))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return 0, 0
    for line in reversed(tail.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            return int(d["e"]), int(d["r"])   # 键名是缩写的,见 export 那段的 json.dumps
        except Exception:
            # 最后一行可能是上一次写到一半的残行。往前再找一条,
            # 而不是把整个文件判成读不出来。
            continue
    return 0, 0


# --------------------------------------------------------------------------- durable export
RUN_EVENTS_DIR = "run-events"


def _watermark_from_months(out_dir: Path) -> tuple[int, int]:
    """The highest (log_epoch, record_id) in the two newest monthly files; (0, 0) when none.

    Two files, not one: the first export of a new month can land before the last events of the
    previous month are exported, and record ids grow with time, not with the file name.
    """
    try:
        months = sorted(p for p in out_dir.glob("????-??.jsonl") if p.is_file())
    except OSError:
        return 0, 0
    return max((_watermark_from_file(p) for p in months[-2:]), default=(0, 0))


def export_run_events(con) -> tuple[bool, int, str]:
    """Append new run events to monthly JSONL files beside the database (run-events/YYYY-MM.jsonl).

    The OS event log rotates, so this normalized export preserves events beyond its retained window.
    Keep it versioned in the private companion. Preserve the current database too: this export omits
    rc_raw, task configuration history and ingest attempts, and --backfill cannot reconstruct them
    all. No complete JSONL-to-database restore command is currently provided. The append-only JSONL
    avoids versioning every change to the database binary without claiming to replace that database.

    幂等靠的是水位线 = 最后一条已导出记录的 (log_epoch, record_id)。

    ⚠ 水位线的**权威是这个文件本身,不是 meta 表**。这一点必须这样定,因为
    「追加文件」和「提交水位线」不可能原子完成:
      - 先写文件后提交:提交失败(锁超时、崩溃)时文件里已经有那批行,而 meta 还停在旧值,
        下一轮把同一批**再导一遍** —— 而这是运行事件唯一的持久副本,
        按它重建历史的人会得到翻倍的启动次数,文件里也没有任何去重键校验。
      - 先提交后写文件:崩在中间就是 meta 说导过了而文件里没有,那批事件**永久丢失**。
    两种顺序各自会坏,所以不靠顺序:每次从文件末尾读回真实水位线,
    与 meta 里那份取较大者。meta 从此只是一个缓存,坏了不影响正确性。
    """
    started = now()
    p, st = resolve_db()
    if st:
        return False, 0, st.message
    out_dir = Path(p).parent / RUN_EVENTS_DIR

    row = con.execute("SELECT value FROM meta WHERE key='export_watermark'").fetchone()
    mark = row["value"] if row else "0:0"
    try:
        m_epoch, m_rid = (int(x) for x in mark.split(":"))
    except Exception:
        m_epoch, m_rid = 0, 0

    # 文件说了算。上一轮如果写进去了但没来得及提交水位线,meta 会落后,
    # 而这里读回来的那一条会把它顶上去,于是同一批不会被导第二遍。
    f_epoch, f_rid = _watermark_from_months(out_dir)
    if (f_epoch, f_rid) > (m_epoch, m_rid):
        m_epoch, m_rid = f_epoch, f_rid

    rows = con.execute(
        "SELECT log_epoch,record_id,task,event_id,ts,rc_raw,rc_norm FROM run_event "
        "WHERE log_epoch > ? OR (log_epoch = ? AND record_id > ?) "
        "ORDER BY log_epoch, record_id", (m_epoch, m_epoch, m_rid)).fetchall()
    if not rows:
        note_run(con, "export", started, True, 0, "nothing new")
        return True, 0, ""

    # One file per calendar month of the event time (run-events/YYYY-MM.jsonl): a month that is over
    # never changes again, so its history diff stays small and the current file stays short.
    by_month: dict[str, list[str]] = {}
    for r in rows:
        by_month.setdefault(str(r["ts"])[:7], []).append(json.dumps({
            "e": r["log_epoch"], "r": r["record_id"], "t": r["task"],
            "i": r["event_id"], "ts": r["ts"],
            "rc": r["rc_norm"],           # normalised; NULL when the event carries none
        }, ensure_ascii=False, sort_keys=True) + "\n")
    out = out_dir
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        for month, lines in sorted(by_month.items()):
            with (out_dir / f"{month}.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
                fh.writelines(lines)
    except Exception as e:
        note_run(con, "export", started, False, 0, str(e)[:400])
        return False, 0, str(e)

    last = rows[-1]
    try:
        begin(con)
        con.execute("INSERT INTO meta(key,value) VALUES('export_watermark',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (f"{last['log_epoch']}:{last['record_id']}",))
        note_run(con, "export", started, True, len(rows), str(out))
        con.execute("COMMIT")
    except Exception as e:
        rollback(con)
        # 失败也要记一行;note_run 可能因同一把锁失败,所以它也要能安静收场。
        try:
            note_run(con, "export", started, False, 0, str(e)[:400])
            con.commit()
        except Exception:
            pass
        return False, 0, str(e)
    return True, len(rows), ""


# --------------------------------------------------------------------------- task settings
def ingest_tasks(con) -> tuple[bool, int, str]:
    started = now()
    rc, out, err = run_ps(COLLECT, timeout=300)
    if rc != 0 or not out:
        note_run(con, "tasks", started, False, 0, (err or out)[:400])
        return False, 0, f"采集任务失败: {err or out}"
    try:
        raw = json.loads(out)
    except Exception as e:
        note_run(con, "tasks", started, False, 0, str(e)[:400])
        return False, 0, str(e)

    added = 0
    try:
        begin(con)
        for t in raw.get("tasks", []):
            settings = {k: t.get(k) for k in ("catchup", "retries", "timeout", "multi",
                                              "refuseOnBattery", "stopOnBattery", "runLevel", "userId")}
            trg = json.dumps(t.get("triggersRaw") or [], ensure_ascii=False, sort_keys=True)
            blob = json.dumps({"s": settings, "t": trg, "st": t.get("state")},
                              ensure_ascii=False, sort_keys=True)
            h = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
            cur = con.execute(
                "INSERT INTO task_meta_version(task,config_hash,first_seen,last_seen,state,triggers,settings) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(task,config_hash) DO UPDATE SET last_seen=excluded.last_seen",
                (t["name"], h, now(), now(), t.get("state"), trg,
                 json.dumps(settings, ensure_ascii=False, sort_keys=True)))
            added += 1 if cur.rowcount and cur.lastrowid else 0
            con.execute("INSERT INTO task_seen(task,first_seen,last_seen) VALUES(?,?,?) "
                        "ON CONFLICT(task) DO UPDATE SET last_seen=excluded.last_seen",
                        (t["name"], now(), now()))
        note_run(con, "tasks", started, True, added, f"{len(raw.get('tasks', []))} tasks")
        con.execute("COMMIT")
    except Exception as e:
        rollback(con)
        note_run(con, "tasks", started, False, 0, str(e)[:400])
        return False, 0, str(e)
    return True, added, ""


def ingest_main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ingest into the task-console database")
    ap.add_argument("--backfill", action="store_true",
                    help="rebuild health observations from the whole log rather than the recent window")
    ap.add_argument("--health-log", default=os.environ.get("TASK_CONSOLE_HISTORY"))
    ap.add_argument("--run-days", type=int, default=60)
    ap.add_argument("--skip-runlog", action="store_true", help="the slow one; skip for a quick pass")
    a = ap.parse_args(argv)

    con, path = open_rw()
    if not con:
        return 2
    print(f"db: {path}")

    ok_all, msgs = True, []
    if a.health_log:
        ok, n, msg = ingest_health(con, a.health_log, a.backfill)
        print(f"  health : {'ok' if ok else 'FAIL'}  +{n}  {msg}")
        ok_all &= ok
        if msg: msgs.append(msg)
    else:
        print("  health : skipped (no --health-log / TASK_CONSOLE_HISTORY). 这是跳过,不是通过。")
        ok_all = False

    if not a.skip_runlog:
        ok, n, msg = ingest_runlog(con, a.run_days)
        print(f"  runlog : {'ok' if ok else 'FAIL'}  +{n}  {msg}")
        ok_all &= ok
        if msg: msgs.append(msg)
    else:
        print("  runlog : skipped (--skip-runlog). 这是跳过,不是通过;运行历史这一轮没有更新。")

    ok, n, msg = ingest_tasks(con)
    print(f"  tasks  : {'ok' if ok else 'FAIL'}  +{n}  {msg}")
    ok_all &= ok

    ok, n, msg = export_run_events(con)
    print(f"  export : {'ok' if ok else 'FAIL'}  +{n}  {msg}")
    ok_all &= ok

    cov = db_coverage(con)
    print(f"  覆盖   : 健康 {cov['health']['rows']} 行 {cov['health']['from']}..{cov['health']['to']}"
          f" · 运行 {cov['runs']['rows']} 行 {cov['runs']['from']}..{cov['runs']['to']}")
    con.close()
    return 0 if ok_all else 1


# ---- component_status --------------------------------------------------------------------------
# Read-only JSON adapters for component observations and SMITH catalog v1.
#
# Only the caller-selected snapshot is opened. Paths, entrypoints and commands in
# that document are data, never read instructions or executable authority.


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
        result = evaluate(snapshot["declarations"], snapshot.get("rows") or {}, now,
                                    mtime_of=lambda path: (artifacts.get(path, {}).get("mtime"),
                                                           artifacts.get(path, {}).get("reason", "missing captured artifact")))
        result["read_only"] = True
        result["coverage"] = coverage(result["summary"]["judged"], result["summary"]["total"])
        result["catalog"] = read_catalog(lambda: snapshot.get("catalog"))
        result["authority"] = "observation_only"
        return result
    inputs = snapshot.get("tasks", [])
    if not isinstance(inputs, list):
        raise ValueError("tasks must be an array")
    tasks = []
    for item in inputs:
        if not isinstance(item, dict) or not isinstance(item.get("spec"), dict):
            raise ValueError("each task needs a spec and observations/read response")
        obs = item["observations"]
        if not isinstance(obs, dict) or obs.get("schemaVersion", 1) != 1:
            raise ValueError("unsupported component read response")
        task = evaluate_task(item["spec"], obs, now)
        task["name"] = item["spec"].get("name")
        tasks.append(task)
    expected = max(len(inputs), snapshot.get("expected", len(inputs)))
    cov = coverage(sum(t["verdict"] != "unknown" for t in tasks), expected)
    return {"schemaVersion": 1, "read_only": True, "observed_at": epoch(now),
            "tasks": tasks, "coverage": cov, "catalog": read_catalog(lambda: snapshot.get("catalog")),
            "authority": "observation_only", "captured_at": snapshot.get("observed_at"),
            "authority_generation": snapshot.get("authority_generation"),
            "reason_code": "zero_coverage" if not expected else None}


def read_snapshot(path: str | Path) -> dict:
    with Path(path).expanduser().open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def read_catalog(reader):
    """Catalog evidence cannot invalidate independently captured task evidence."""
    try:
        return catalog_view(reader())
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return {**catalog_view(None), 'reason': f'Catalog read failed: {type(exc).__name__}',
                'coverage': {'status': 'partial'}, 'reason_code': 'query_failed'}


def read_configured(now=None, env=None) -> dict:
    """Environment paths are startup/private bindings; HTTP supplies no path parameter."""
    env = os.environ if env is None else env
    path = env.get("TASK_CONSOLE_STATUS_SNAPSHOT")
    catalog_path = env.get("TASK_CONSOLE_CATALOG_SNAPSHOT")
    result = {"schemaVersion": 1, "read_only": True, "available": False,
              "tasks": [], "coverage": coverage(0, 0), "authority": "observation_only",
              "reason": "Component snapshot not configured; unchecked", "catalog": catalog_view(None)}
    if path:
        try:
            result.update(evaluate_snapshot(read_snapshot(path), now), available=True, reason=None)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result.update(reason=f"Component snapshot read failed: {type(exc).__name__}", reason_code="query_failed")
    if catalog_path:
        result['catalog'] = read_catalog(lambda: read_snapshot(catalog_path))
    return result


def status_main(argv=None) -> int:
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
