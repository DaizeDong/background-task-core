"""Health policy regressions use generated synthetic observations only."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "task_console"))
sys.path.insert(0, str(ROOT))
from tools.make_fixtures import health_case, legacy_health_snapshot
import health as H
import freshness as F


def evaluate(case):
    return H.evaluate_task(case["spec"], case["observations"], case["now"])


def test_completed_and_missing_check_keep_denominator():
    case = health_case()
    assert evaluate(case)["verdict"] == "healthy"
    case["observations"]["checks"].pop()
    result = evaluate(case)
    assert result["verdict"] == "unknown"
    assert result["coverage"]["expected"] == 2
    assert result["coverage"]["checked"] == 1
    assert [c["check_id"] for c in result["checks"]] == ["output", "dependencies"]


@pytest.mark.parametrize("state,reason", [("busy", "skipped_busy"), ("deferred", "deferred")])
def test_busy_and_deferred_preserve_last_success(state, reason):
    case = health_case()
    case["observations"]["execution"]["state"] = state
    before = deepcopy(case)
    result = evaluate(case)
    assert result["execution"]["state"] == state
    assert reason in result["reason_codes"]
    assert result["last_success"] == before["observations"]["last_success"]
    assert case == before


def test_running_oneshot_missing_output_and_overdue_are_distinct():
    case = health_case()
    case["observations"]["execution"]["state"] = "running"
    case["observations"]["checks"] = []
    assert evaluate(case)["state"] == "running"
    case["observations"]["execution"]["started_at"] = case["now"] - 301
    result = evaluate(case)
    assert result["state"] == "down" and result["execution"]["state"] == "running"
    assert "overdue" in result["reason_codes"]
    assert "timed_out" not in str(result)


def test_daemon_is_not_exempt_from_missing_checks():
    case = health_case()
    case["spec"]["kind"] = "daemon"
    case["observations"]["execution"]["state"] = "running"
    case["observations"]["checks"] = []
    assert evaluate(case)["verdict"] == "unknown"


def test_detached_launcher_requires_actual_observer():
    case = health_case()
    case["spec"].update(kind="daemon", detached=True, watched_elsewhere="acme-observer")
    assert "missing_workload_observer" in evaluate(case)["reason_codes"]
    case["observations"]["observer"] = {"kind": "process", "subject": "launcher", "alive": True,
                                         "observed_at": case["now"]}
    assert evaluate(case)["verdict"] == "unknown"
    case["observations"]["observer"]["subject"] = "workload"
    assert evaluate(case)["verdict"] == "healthy"
    case["observations"]["observer"]["observed_at"] -= 1000
    assert "observer_stale" in evaluate(case)["reason_codes"]
    assert evaluate(case)["execution"]["state"] == "unknown"
    assert evaluate(case)["execution"]["code_subject"] == "launcher"


def test_termination_requires_positive_evidence():
    case = health_case()
    case["spec"]["kind"] = "daemon"
    case["observations"]["observer"] = {"kind": "heartbeat", "subject": "workload", "alive": False,
                                         "observed_at": case["now"]}
    assert evaluate(case)["verdict"] == "unknown"
    case["observations"]["observer"]["termination_confirmed"] = True
    assert "terminated" in evaluate(case)["reason_codes"]


def test_codes_keep_raw_domain_and_alias_meaning():
    case = health_case()
    case["spec"]["ok_exit_codes"] = [2]
    case["observations"]["execution"].update(exit_code="0x80070002", code_domain="hresult")
    result = evaluate(case)
    assert result["verdict"] == "healthy"
    assert result["execution"]["raw_exit_code"] == "0x80070002"
    assert result["execution"]["exit_code"] == 2
    assert result["execution"]["code_domain"] == "hresult"
    case["spec"].pop("ok_exit_codes")
    assert evaluate(case)["verdict"] == "unhealthy"


def test_old_run_v1_and_previous_success_are_read_only():
    case = health_case()
    obs = case["observations"]
    obs["last_run"] = {"schemaVersion": 1, **obs.pop("execution")}
    result = evaluate(case)
    assert result["last_run_v1"] == obs["last_run"]
    assert result["last_success_run_id"] == "acme-previous"


@pytest.mark.parametrize("change", ["duplicate", "old_run", "future", "zero"])
def test_untrusted_check_evidence_cannot_turn_green(change):
    case = health_case()
    checks = case["observations"]["checks"]
    if change == "duplicate":
        checks.append(deepcopy(checks[0]))
    elif change == "old_run":
        checks[0]["run_id"] = "acme-previous"
    elif change == "future":
        checks[0]["observed_at"] = case["now"] + 7200
    else:
        checks[0].update(checked=0, expected=5)
    assert evaluate(case)["verdict"] != "healthy"


def test_missing_source_and_invalid_declaration_do_not_shrink_coverage():
    snapshot = legacy_health_snapshot()
    result = F.evaluate(snapshot["declarations"] + [{}], {}, health_case()["now"],
                        mtime_of=lambda _: (None, "missing"))
    assert result["summary"]["total"] == 2
    assert result["summary"]["coverage"] == 0


def test_future_run_timestamp_is_not_recent():
    snapshot = legacy_health_snapshot()
    snapshot["rows"]["AcmeSync"]["last_run"] = health_case()["now"] + 7200
    result = F.evaluate(snapshot["declarations"], snapshot["rows"], health_case()["now"],
                        mtime_of=lambda _: (health_case()["now"], None))
    assert result["tasks"][0]["state"] == "unknown"


@pytest.mark.parametrize("value,tone", [(None,"warn"), (0,"ok"), (85,"warn"), (95,"bad")])
def test_backend_resource_verdict(value, tone):
    assert H.resource_verdict(value, warning=85, critical=95)["tone"] == tone


def test_report_subcheck_coverage_does_not_hide_a_missing_named_check():
    case = health_case()
    case["observations"]["health"] = {"checked": 27, "expected": 27}
    assert evaluate(case)["coverage"]["checked"] == 27
    case["observations"]["checks"].pop()
    result = evaluate(case)
    assert result["coverage"]["expected"] == 27
    assert result["coverage"]["checked"] < 27


@pytest.mark.parametrize("state,reason", [("error", "query_failed"), ("missing", "missing_check"), ("zero", "zero_coverage")])
def test_check_failure_kinds_remain_distinct(state, reason):
    case = health_case()
    case["observations"]["checks"][0]["state"] = state
    result = evaluate(case)
    assert reason in result["checks"][0]["reason_codes"]
    assert result["checks"][0]["observed_state"] == state


def test_completed_future_receipt_is_unknown():
    case = health_case()
    case["observations"]["execution"]["finished_at"] = case["now"] + 7200
    assert "clock_skew" in evaluate(case)["reason_codes"]
    assert evaluate(case)["verdict"] == "unknown"


def test_declared_oneshot_without_deadline_is_unknown():
    case = health_case()
    case["spec"].pop("timeout_seconds")
    case["observations"]["execution"]["state"] = "running"
    assert "missing_deadline" in evaluate(case)["reason_codes"]


def test_old_success_artifact_does_not_clear_new_failure():
    snapshot = legacy_health_snapshot()
    declaration = snapshot["declarations"][0]
    declaration["artifact_written_only_on_success"] = True
    snapshot["rows"]["AcmeSync"]["last_rc"] = 1
    result = F.evaluate([declaration], snapshot["rows"], health_case()["now"],
                        mtime_of=lambda _: (health_case()["now"] - 120, None))
    assert result["tasks"][0]["verdict"] == "unhealthy"


def test_daemon_passive_workload_observer_can_supply_default_check():
    case = health_case()
    case["spec"].update(kind="daemon", watched_elsewhere="synthetic-observer")
    case["spec"].pop("checks")
    case["observations"]["checks"] = []
    case["observations"]["observer"] = {"kind": "heartbeat", "subject": "workload", "alive": True,
                                         "observed_at": case["now"]}
    assert evaluate(case)["verdict"] == "healthy"


@pytest.mark.parametrize("count", [-1, True, "1", 1.5])
def test_malformed_coverage_is_a_reader_error(count):
    case = health_case()
    case["observations"]["checks"][0]["checked"] = count
    with pytest.raises(ValueError):
        evaluate(case)


def test_optional_check_failure_still_degrades_health():
    case = health_case()
    case["spec"]["checks"][1]["required"] = False
    case["observations"]["checks"][1]["state"] = "unhealthy"
    assert evaluate(case)["verdict"] == "degraded"


def test_malformed_current_run_is_a_reader_error():
    case = health_case()
    case["observations"]["current_run"] = "invalid"
    with pytest.raises(ValueError):
        evaluate(case)


def test_t11_compiled_check_bindings_are_consumed_without_rescanning():
    from tools.make_fixtures import example_request
    from scripts.task_console.components import compile_tasks
    request = example_request()
    compiled = compile_tasks(request["components"], request["bindings"], request["machine"])[0]
    assert H.evaluate_task(compiled, {}, health_case()["now"])["state"] == "paused"
    spec = compiled.to_dict()
    spec["enabled"] = True
    spec["checks"][0]["legacy"]["artifact_max_age_hours"] = 24
    now = health_case()["now"]
    artifact = spec["checks"][0]["legacy"]["artifact"]
    observations = {"scheduler": {"state": "Ready", "last_rc": 0, "last_run": now - 60},
                    "artifacts": {artifact: {"mtime": now - 30}}}
    result = H.evaluate_task(spec, observations, now)
    assert result["task_id"] == "acme-maintenance/sync"
    assert result["verdict"] == "healthy"
    assert result["coverage"]["expected"] == 2


def test_external_check_binding_needs_matching_actual_observation():
    case = health_case()
    binding = {"component": "acme-observer", "check_id": "heartbeat", "observation_ref": "acme-observation"}
    case["spec"]["checks"][1]["watched_elsewhere"] = binding
    assert "missing_external_observation" in evaluate(case)["reason_codes"]
    case["observations"]["external_checks"] = {"acme-observation": {
        "component": "acme-observer", "check_id": "heartbeat", "state": "healthy",
        "observer": {"kind": "heartbeat", "subject": "workload", "alive": True, "observed_at": case["now"]}}}
    assert evaluate(case)["verdict"] == "healthy"
    case["observations"]["external_checks"]["acme-observation"]["observer"]["subject"] = "launcher"
    assert evaluate(case)["verdict"] == "unknown"


def test_pre_normalized_report_keeps_original_raw_code():
    case = health_case()
    case["spec"]["ok_codes"] = [2]
    case["observations"]["execution"].update(raw_exit_code="0x80070002", exit_code=2, code_domain="hresult")
    assert evaluate(case)["execution"]["raw_exit_code"] == "0x80070002"


def test_unexpected_checks_stay_in_coverage_and_are_visible():
    case = health_case()
    case["observations"]["checks"].append({"id": "acme-extra", "state": "unhealthy"})
    result = evaluate(case)
    assert result["coverage"]["expected"] == 3
    assert result["verdict"] != "healthy"
    assert result["unexpected_checks"][0]["id"] == "acme-extra"


def test_stale_structured_check_is_not_healthy():
    case = health_case()
    case["spec"]["checks"][0]["max_age_hours"] = 1
    case["observations"]["checks"][0]["observed_at"] = case["now"] - 4 * 3600
    assert "stale" in evaluate(case)["reason_codes"]
    assert evaluate(case)["verdict"] == "unhealthy"


def test_failed_read_does_not_borrow_coverage_from_retained_checks():
    case = health_case()
    case["observations"]["query_failed"] = True
    result = evaluate(case)
    assert result["verdict"] == "unknown"
    assert result["coverage"]["checked"] == 0
    assert result["coverage"]["expected"] == 2
