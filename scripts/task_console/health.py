"""Pure, versioned health evaluation. Observations never grant execution authority.

Collectors supply scheduler/process/artifact observations from their existing private
bindings. This module does not read paths, run commands, or update run receipts.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import datetime
import math

try:
    from .rcnorm import norm_rc
except ImportError:  # Existing script entrypoints import siblings directly.
    from rcnorm import norm_rc

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
