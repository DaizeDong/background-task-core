"""Read-only effective authority regression tests using generated synthetic data."""
from copy import deepcopy
import builtins
import io
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tools"))
from make_fixtures import example_request, installation_request
from task_console import compiler, component_status, observations, registration
from task_console.contracts import ContractError


@pytest.mark.parametrize("count, mode", [(0, "legacy"), (1, "mixed"), (2, "declarations")])
def test_effective_authority_preserves_specs_revision_and_input(count, mode):
    request = installation_request()
    ids = list(request["bindings"]["tasks"])
    migrated = list(reversed(ids))[:count]
    request["machine"].update(authority_epoch=7, migrated_tasks=migrated)
    bundle = {"request": request}
    before = deepcopy(bundle)
    compatibility = registration._compile(bundle)
    result = registration.compile_effective(bundle)
    assert bundle == before
    assert result["authority"] == {
        "mode": mode, "epoch": 7, "migrated_tasks": migrated,
        "task_modes": {task: "declarations" if task in migrated else "legacy" for task in ids},
    }
    assert result["input_revision"] == registration.revision(request)
    assert result["task_specs"] == compatibility["task_specs"]
    assert result["parity"] == compatibility["parity"]
    assert result["check_coverage"] == compatibility["check_coverage"]
    assert result["generated_files"] == compatibility["generated_files"]
    assert result["mode"] == "read-only" and result["applicable"] is False
    assert all(item["replacement_safe"] is False for item in result["generated_files"].values())
    assert registration._compile(bundle) == compatibility
    assert compatibility["authority"] == {"mode": "legacy", "epoch": 7, "migrated_tasks": []}
    if migrated:
        assert result["input_revision"] != compatibility["input_revision"]
        with pytest.raises(ContractError, match="unsupported_authority"):
            compiler.plan(request)
    result["authority"]["migrated_tasks"].append("synthetic/change")
    result["task_specs"][0]["checks"].clear()
    assert bundle == before


def test_epoch_zero_matches_existing_plan_except_explicit_task_modes():
    request = example_request()
    result = registration.compile_effective({"request": request})
    assert result["authority"].pop("task_modes") == {"acme-maintenance/sync": "legacy"}
    assert result == compiler.plan(request)


@pytest.mark.parametrize("epoch, migrated, code", [
    (True, [], "invalid_type"), (-1, [], "invalid_authority"),
    (1.5, [], "invalid_type"), (1, True, "invalid_type"),
    (1, [True], "invalid_authority"), (1, [""], "invalid_authority"),
    (1, ["acme-maintenance/sync"] * 2, "invalid_authority"),
    (1, ["acme-maintenance/unknown"], "unknown_migrated_task"),
])
def test_invalid_authority_uses_existing_owner_refusal(epoch, migrated, code, monkeypatch):
    request = example_request()
    request["machine"].update(authority_epoch=epoch, migrated_tasks=migrated)
    bundle = {"request": request}
    before = deepcopy(bundle)
    for compile_request in (registration._compile, registration.compile_effective):
        with pytest.raises(ContractError) as error:
            compile_request(bundle)
        assert error.value.code == code
    monkeypatch.setattr(observations, "read_json", lambda path: request)
    with pytest.raises(ContractError) as error:
        observations.collect({"request": "C:/Acme/private/request.json"}, {})
    assert error.value.code == code
    assert bundle == before


@pytest.mark.parametrize("field", ["machine", "authority_epoch", "migrated_tasks"])
def test_missing_explicit_authority_is_not_inferred(field):
    request = example_request()
    source = request if field == "machine" else request["machine"]
    source.pop(field)
    with pytest.raises(ContractError) as error:
        registration.compile_effective({"request": request})
    assert error.value.code == "missing_field"
    assert error.value.field == field


def test_public_read_api_requires_no_io_or_runtime(monkeypatch):
    request = example_request()
    request["machine"].update(authority_epoch=3, migrated_tasks=["acme-maintenance/sync"])
    def forbidden(*args, **kwargs):
        pytest.fail("effective compilation attempted I/O or runtime access")
    with monkeypatch.context() as patch:
        for module, name in ((builtins, "open"), (io, "open"), (os, "open"),
                             (subprocess, "Popen"), (socket, "socket"), (registration, "_use")):
            patch.setattr(module, name, forbidden)
        result = registration.compile_effective({"request": request})
    assert result["authority"]["epoch"] == 3


def test_collect_migrated_request_preserves_revision_status_and_check_scope(monkeypatch):
    request = example_request()
    task_id = "acme-maintenance/sync"
    binding = request["bindings"]["tasks"][task_id]
    binding["enabled"] = True
    for check in binding["checks"]:
        check["legacy"].pop("artifact", None)
    monkeypatch.setattr(observations, "read_json", lambda path: request)
    config = {"request": "C:/Acme/private/request.json", "snapshot": "C:/Acme/private/snapshot.json"}
    now = 1_800_000_000
    capture = {"observed_at": now, "enumerated": 1, "tasks": [
        {"name": "AcmeSync", "state": "Ready", "rcRaw": 0, "lastRunEpoch": now - 10,
         "nextRunEpoch": now + 3600, "missedRuns": 0, "infoError": None}]}
    legacy = observations.collect(config, capture, now=now)
    request["machine"].update(authority_epoch=7, migrated_tasks=[task_id])
    before = deepcopy(request)
    snapshot = observations.collect(config, capture, now=now)
    assert request == before
    assert snapshot["input_revision"] == registration.revision(request)
    assert snapshot["input_revision"] != legacy["input_revision"]
    assert snapshot["tasks"] == legacy["tasks"]
    assert snapshot["expected"] == snapshot["scheduled_task_count"] == 1
    assert snapshot["authority"] == "observation_only"
    status = component_status.evaluate_snapshot(snapshot, now)
    assert status["tasks"][0]["verdict"] == "healthy"
    assert len(status["tasks"][0]["checks"]) == 3
