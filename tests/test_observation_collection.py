"""Synthetic passive collection, publication and existing PowerShell integration."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tools"))
from make_fixtures import example_request
from task_console import component_status, observations
from task_console.registration import Conflict
from task_console.runtime_storage import ResourceLocks

NOW = 1_800_000_000
TASK = "acme-maintenance/sync"


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture
def case(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    for name in list(os.environ):
        if name.startswith(("TASK_CONSOLE_", "SCHEDULE_", "TASK_RUN_ID")):
            monkeypatch.delenv(name, raising=False)
    request = example_request()  # Public synthetic generator, never a live declaration.
    binding = request["bindings"]["tasks"][TASK]
    binding.update(enabled=True, source_root=str(tmp_path), cwd=str(tmp_path),
                   argv=[str(tmp_path / "python.exe"), str(tmp_path / "acme.py")])
    for check in binding["checks"]:
        check["legacy"].pop("artifact", None)
    private = tmp_path / "private"
    private.mkdir()
    config = {"schemaVersion": 1, "request": str(write(tmp_path / "request.json", request)),
              "private_dir": str(private), "snapshot": str(private / "snapshot.json"),
              "home": str(home), "max_age_seconds": 60}
    path = write(tmp_path / "binding.json", config)
    capture = {"observed_at": NOW, "enumerated": 1, "tasks": [
        {"name": "AcmeSync", "state": "Ready", "rcRaw": 0, "lastRunEpoch": NOW - 10,
         "nextRunEpoch": NOW + 3600, "missedRuns": 0, "infoError": None}]}
    return config, path, request, capture


def verdict(snapshot, now=NOW):
    return component_status.evaluate_snapshot(snapshot, now)["tasks"][0]


@pytest.mark.parametrize("migrated", [False, True])
def test_compiles_then_publishes_same_reader_input(case, monkeypatch, migrated):
    config, path, request, capture = case
    if migrated:
        request["machine"].update(authority_epoch=7, migrated_tasks=[TASK])
        write(Path(config["request"]), request)
    before = deepcopy(request)
    before_bytes = Path(config["request"]).read_bytes()
    result = observations.publish(path, capture, now=NOW)
    assert result["tasks"] == 1
    assert json.loads(Path(config["request"]).read_text()) == before
    assert Path(config["request"]).read_bytes() == before_bytes
    from task_console.registration import revision
    assert json.loads(Path(config["snapshot"]).read_text())["input_revision"] == revision(request)
    monkeypatch.setenv("TASK_CONSOLE_STATUS_SNAPSHOT", config["snapshot"])
    status = component_status.read_configured(NOW)
    assert status["available"] and status["tasks"][0]["verdict"] == "healthy"
    assert status["tasks"][0]["execution"]["code_domain"] == "scheduler"
    assert len(status["tasks"][0]["checks"]) == 3


@pytest.mark.parametrize("problem", ["missing", "query_failed", "no_clock", "partial"])
def test_absent_and_partial_evidence_is_unknown(case, problem):
    config, path, request, capture = case
    if problem == "missing":
        capture["tasks"] = []
    elif problem == "query_failed":
        capture["scheduler_error"] = "query_failed"
    elif problem == "no_clock":
        capture.pop("observed_at")
    else:
        capture["tasks"][0]["infoError"] = "access_denied"
    report = verdict(observations.collect(config, capture, now=NOW))
    assert report["verdict"] == "unknown"
    assert report["coverage"]["checked"] == 0


def test_retained_snapshot_expires_at_reader_time(case):
    config, _, _, capture = case
    snapshot = observations.collect(config, capture, now=NOW)
    assert verdict(snapshot)["verdict"] == "healthy"
    assert verdict(snapshot, NOW + 61)["verdict"] != "healthy"
    assert verdict(snapshot, NOW + 4000)["verdict"] == "unhealthy"


def test_missing_and_stale_artifact(case):
    config, _, request, capture = case
    artifact = Path(config["private_dir"]) / "output.json"
    legacy = request["bindings"]["tasks"][TASK]["checks"][0]["legacy"]
    legacy.update(artifact=str(artifact), artifact_max_age_hours=1)
    write(Path(config["request"]), request)
    assert verdict(observations.collect(config, capture, now=NOW))["verdict"] == "unknown"
    artifact.write_text("synthetic")
    os.utime(artifact, (NOW - 7201, NOW - 7201))
    assert verdict(observations.collect(config, capture, now=NOW))["verdict"] == "unhealthy"


@pytest.mark.parametrize("corruption", ["duplicate", "schema", "receipt", "row", "zero"])
def test_corrupt_input_preserves_previous_snapshot(case, corruption):
    config, path, request, capture = case
    observations.publish(path, capture, now=NOW)
    output = Path(config["snapshot"])
    before = output.read_bytes()
    if corruption == "duplicate":
        Path(config["request"]).write_text('{"schemaVersion":1,"schemaVersion":1}')
    elif corruption == "schema":
        request["components"][0]["schemaVersion"] = 7
        write(Path(config["request"]), request)
    elif corruption == "receipt":
        receipt = Path(config["private_dir"]) / "receipt.json"
        receipt.write_text("{bad")
        config["tasks"] = {TASK: {"receipt": {"path": str(receipt), "format": "stdout-report"}}}
        write(path, config)
    elif corruption == "row":
        capture["tasks"].append(deepcopy(capture["tasks"][0]))
    else:
        capture["enumerated"] = 0
    with pytest.raises(ValueError):
        observations.publish(path, capture, now=NOW)
    assert output.read_bytes() == before


def test_one_writer_and_failed_atomic_replace_preserve_prior(case, monkeypatch):
    config, path, _, capture = case
    observations.publish(path, capture, now=NOW)
    output = Path(config["snapshot"])
    before = output.read_bytes()
    locks = ResourceLocks(Path(config["private_dir"]) / ".observation-locks")
    with locks.hold(["observation:" + str(output).casefold()]):
        with pytest.raises(Conflict, match="busy"):
            observations.publish(path, capture, now=NOW)
    def fail(*args):
        raise OSError("synthetic publication failure")
    monkeypatch.setattr(observations.fs, "atomic_replace", fail)
    with pytest.raises(OSError):
        observations.publish(path, capture, now=NOW)
    assert output.read_bytes() == before


def local_evidence(config, capture):
    executable = str(Path(config["home"]) / "acme-runtime.exe")
    capture["local"] = {"observed_at": NOW, "process_query": "checked", "listener_query": "checked",
                        "processes": [{"pid": 123, "executable": executable, "command_line": "acme-script"}],
                        "listeners": [{"pid": 123, "port": 12345, "address": "127.0.0.1"}]}
    return {"executable": executable, "command_contains": ["acme-script"]}


def test_daemon_process_and_listener_do_not_prove_remote_job(case):
    config, _, request, capture = case
    request["components"][0]["tasks"][0]["kind"] = "daemon"
    write(Path(config["request"]), request)
    rule = local_evidence(config, capture)
    config["tasks"] = {TASK: {"process": {**rule, "check_id": "output"}}}
    config["local_listeners"] = {"AcmeBridge": {**rule, "port": 12345}}
    snapshot = observations.collect(config, capture, now=NOW)
    report = component_status.evaluate_snapshot(snapshot, NOW)
    assert report["tasks"][0]["execution"]["state"] == "running"
    assert report["tasks"][1]["verdict"] == "healthy"
    assert snapshot["tasks"][1]["observations"]["remote_job_success"] == "unchecked"
    assert report["tasks"][1]["observer"]["scope"] == "local_listener_only"
    capture["local"]["listeners"][0]["pid"] = 124
    changed = component_status.evaluate_snapshot(observations.collect(config, capture, now=NOW), NOW)
    assert changed["tasks"][1]["verdict"] == "unknown"
    assert component_status.evaluate_snapshot(snapshot, NOW + 4000)["tasks"][1]["verdict"] != "healthy"


def test_detached_launcher_success_never_completes_payload(case):
    config, _, _, capture = case
    config["tasks"] = {TASK: {"detached": True}}
    report = verdict(observations.collect(config, capture, now=NOW))
    assert report["execution"]["state"] == "unknown"
    assert report["execution"]["code_subject"] == "launcher"
    assert report["verdict"] == "unknown"
    config["tasks"][TASK]["process"] = local_evidence(config, capture)
    report = verdict(observations.collect(config, capture, now=NOW))
    assert report["execution"]["state"] == "running"
    assert report["execution"]["code_subject"] == "launcher"
    assert report["verdict"] != "healthy"


def test_receipt_missing_partial_run_mismatch_and_command_data(case, monkeypatch):
    config, _, _, capture = case
    path = Path(config["private_dir"]) / "stdout.json"
    config["tasks"] = {TASK: {"receipt": {"path": str(path), "format": "stdout-report"}}}
    assert verdict(observations.collect(config, capture, now=NOW))["verdict"] == "unknown"
    report = {"schemaVersion": 1, "task_id": TASK, "observed_at": NOW, "run_id": "acme-run",
              "checks": [{"id": "output", "state": "healthy", "run_id": "previous"}],
              "command": "must-never-execute", "path": "must-never-read"}
    write(path, report)
    def fail(*args, **kwargs):
        pytest.fail("collection attempted a process launch")
    monkeypatch.setattr(subprocess, "Popen", fail)
    result = verdict(observations.collect(config, capture, now=NOW))
    assert result["verdict"] == "unknown" and "different_run" in result["reason_codes"]
    report["task_id"] = "other/task"
    write(path, report)
    with pytest.raises(ValueError, match="receipt_task_mismatch"):
        observations.collect(config, capture, now=NOW)


def test_catalog_uses_smith_interface_and_missing_is_unchecked(case, monkeypatch):
    config, _, _, capture = case
    catalog_path = Path(config["private_dir"]) / "catalog.json"
    config["catalog_snapshot"] = str(catalog_path)
    assert component_status.evaluate_snapshot(observations.collect(config, capture, now=NOW), NOW)["catalog"]["available"] is False
    config.pop("catalog_snapshot")
    request_path = write(catalog_path, {"skill_roots": []})
    config["catalog_request"] = str(request_path)
    from skill_smith import catalog
    calls = []
    def discover(request):
        calls.append(request)
        return {"schema_version": 1, "records": [], "coverage": {"status": "unchecked"}}
    monkeypatch.setattr(catalog, "discover", discover)
    observations.collect(config, capture, now=NOW)
    assert calls == [{"skill_roots": []}]


def test_catalog_refresh_reuses_snapshot_and_revalidates_request_digest(case, monkeypatch):
    config, path, _, capture = case
    config["catalog_request"] = str(write(Path(config["private_dir"]) / "catalog-request.json", {}))
    write(path, config)
    from skill_smith import catalog
    calls = []
    def discover(request):
        calls.append(request)
        return {"schema_version": 1, "observed_at": NOW, "records": [], "coverage": {"status": "unchecked"}}
    monkeypatch.setattr(catalog, "discover", discover)
    observations.publish(path, capture, now=NOW)
    observations.publish(path, capture, now=NOW + 1)
    assert len(calls) == 1
    write(Path(config["catalog_request"]), {"skill_roots": []})
    observations.publish(path, capture, now=NOW + 2)
    assert len(calls) == 2
    observations.publish(path, capture, now=NOW + 301)
    assert len(calls) == 3


def test_private_destination_and_no_global_home_fallback(case):
    config, path, _, capture = case
    config["snapshot"] = str(Path(config["private_dir"]).parent / "outside.json")
    write(path, config)
    with pytest.raises(ValueError):
        observations.publish(path, capture, now=NOW)
    config.pop("home")
    assert observations.artifact_fact("~/not-real.json", config)["mtime"] is None


def test_unbound_local_subjects_remain_visible_and_unchecked(case):
    config, _, _, capture = case
    config["local_listeners"] = {"AcmePush": None, "AcmeBridge": None}
    status = component_status.evaluate_snapshot(observations.collect(config, capture, now=NOW), NOW)
    assert len(status["tasks"]) == 3
    for task in status["tasks"][1:]:
        assert task["verdict"] == "unknown" and task["coverage"]["checked"] == 0


def test_old_v1_receipt_is_retained_but_cannot_claim_new_run(case):
    config, _, _, capture = case
    path = Path(config["private_dir"]) / "last-run.json"
    write(path, {"version": 1, "exit_code": 0, "started_at": NOW - 5000,
                 "finished_at": NOW - 4000, "run_id": "old-operation"})
    config["tasks"] = {TASK: {"receipt": {"path": str(path), "format": "last-run-v1"}}}
    snapshot = observations.collect(config, capture, now=NOW)
    assert snapshot["tasks"][0]["observations"]["previous_receipt"]["last_run"]["run_id"] == "old-operation"
    assert verdict(snapshot)["verdict"] == "unknown"
    assert verdict(snapshot)["run_id"] is None


def test_publication_cannot_overwrite_a_bound_receipt(case):
    config, path, _, capture = case
    receipt = Path(config["snapshot"])
    write(receipt, {"version": 1, "started_at": NOW - 10, "finished_at": NOW, "exit_code": 0})
    before = receipt.read_bytes()
    config["tasks"] = {TASK: {"receipt": {"path": str(receipt), "format": "last-run-v1"}}}
    write(path, config)
    with pytest.raises(ValueError, match="snapshot_overwrites_receipt"):
        observations.publish(path, capture, now=NOW)
    assert receipt.read_bytes() == before


def test_dispatcher_is_unknown_and_disabled_declaration_stays_disabled(case):
    config, _, request, capture = case
    request["components"][0]["tasks"][0]["kind"] = "dispatcher"
    write(Path(config["request"]), request)
    assert verdict(observations.collect(config, capture, now=NOW))["execution"]["code_subject"] == "launcher"
    request["bindings"]["tasks"][TASK]["enabled"] = False
    write(Path(config["request"]), request)
    task = verdict(observations.collect(config, capture, now=NOW))
    assert task["state"] == "paused" and "disabled" in task["reason_codes"]


def test_stdin_transport_bom_preserves_payload_and_uses_real_decoder(case, monkeypatch, capsys):
    config, binding, _, capture = case
    capture["tasks"][0]["infoError"] = "\u4e2d\u6587\U0001f680\ufeffpayload"
    raw = b"\xef\xbb\xbf" + json.dumps(capture, ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"))
    assert observations.main(["--binding", str(binding)]) == 0
    assert json.loads(capsys.readouterr().out)["published"] is True
    snapshot = json.loads(Path(config["snapshot"]).read_text())
    assert snapshot["tasks"][0]["observations"]["scheduler"]["query_error"] == capture["tasks"][0]["infoError"]


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell collector contract")
@pytest.mark.parametrize("scenario", ["ordinary", "unicode", "failure", "raw_only"])
def test_existing_collect_entrypoint_invokes_adapter_once_without_task_or_payload_starts(case, scenario):
    config, binding_path, _, capture = case
    if scenario == "failure":
        observations.publish(binding_path, capture, now=NOW)
        before = Path(config["snapshot"]).read_bytes()
        config["request"] = str(Path(config["private_dir"]) / "synthetic-private-command-secret.json")
        write(binding_path, config)
    script = Path(config["private_dir"]) / "collector-harness.ps1"
    source = r'''
param([string]$Collector)
$ErrorActionPreference = 'Stop'
$global:AcmeCollectorQueries = 0
function Get-ScheduledTask {
  $global:AcmeCollectorQueries++
  if ($global:AcmeCollectorQueries -gt 1) { throw 'second tick/query' }
  [pscustomobject]@{TaskName='AcmeSync'; Description='synthetic'; State='Ready'; Triggers=@();
    Actions=@([pscustomobject]@{Execute='must-not-execute'; Arguments='synthetic'});
    Settings=[pscustomobject]@{StartWhenAvailable=$false; RestartCount=0; ExecutionTimeLimit='PT7M';
      MultipleInstances='IgnoreNew'; DisallowStartIfOnBatteries=$false; StopIfGoingOnBatteries=$false};
    Principal=[pscustomobject]@{RunLevel='Limited'; UserId='AcmeService'}}
}
function Get-ScheduledTaskInfo {
  [pscustomobject]@{LastTaskResult=0; LastRunTime=(Get-Date).AddMinutes(-1);
    NextRunTime=(Get-Date).AddHours(1); NumberOfMissedRuns=0}
}
function Get-CimInstance { throw 'synthetic query denied' }
function Get-Process { @() }
function Get-NetTCPConnection { throw 'synthetic query denied' }
function Start-ScheduledTask { throw 'forbidden start' }
function Register-ScheduledTask { throw 'forbidden registration' }
function Start-Process { throw 'forbidden payload' }
& $Collector
'''
    label = "\u4e2d\u6587\U0001f680\ufeffpayload"
    if scenario == "unicode":
        source = source.replace("$ErrorActionPreference = 'Stop'", "$ErrorActionPreference = 'Stop'\n"
                                "$originalInputEncoding = [Console]::InputEncoding\n"
                                "[Console]::InputEncoding = New-Object System.Text.UTF8Encoding $true")
        source = source.replace("Description='synthetic'", "Description='" + label + "'")
        source = source.replace("function Get-ScheduledTaskInfo {", "function Get-ScheduledTaskInfo {\nthrow '" + label + "'")
        source = source.replace("& $Collector", "try { & $Collector } finally { [Console]::InputEncoding = $originalInputEncoding }")
    script.write_text(source, encoding="utf-8-sig")
    env = dict(os.environ, TASK_CONSOLE_OBSERVATION_BINDING=str(binding_path),
               TASK_CONSOLE_OBSERVATION_PYTHON=sys.executable)
    if scenario == "raw_only":
        env.pop("TASK_CONSOLE_OBSERVATION_BINDING")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script),
                             "-Collector", str(REPO / "scripts/task_console/collect.ps1")],
                            env=env, capture_output=True, encoding="utf-8", timeout=45)
    if scenario == "failure":
        assert result.returncode != 0
        assert "observation reader failed (worker_failed)" in result.stderr
        assert "synthetic-private-command" not in result.stderr + result.stdout
        assert Path(config["snapshot"]).read_bytes() == before
        return
    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)
    assert rows["tasks"][0]["name"] == "AcmeSync"
    assert "local" not in rows
    if scenario == "unicode":
        assert rows["tasks"][0]["description"] == label
        snapshot = json.loads(Path(config["snapshot"]).read_text())
        assert snapshot["tasks"][0]["observations"]["scheduler"]["query_error"] == label
    else:
        assert rows["tasks"][0]["lastRunEpoch"] > 0
    if scenario == "raw_only":
        assert not Path(config["snapshot"]).exists()
        return
    assert Path(config["snapshot"]).is_file()
    assert json.loads(Path(config["snapshot"]).read_text())["scheduled_task_count"] == 1


@pytest.mark.parametrize("raw", [b'{"tasks":[],"tasks":[]}', b'{}{}', b'{} trailing',
                               b'\xef\xbb\xbf\xef\xbb\xbf{}', b'{"x":"\xff"}'])
def test_transport_rejects_duplicate_extra_document_and_invalid_utf8(case, monkeypatch, capsys, raw):
    config, binding, _, capture = case
    observations.publish(binding, capture, now=NOW)
    before = Path(config["snapshot"]).read_bytes()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"))
    assert observations.main(["--binding", str(binding)]) == 2
    assert json.loads(capsys.readouterr().err)["published"] is False
    assert Path(config["snapshot"]).read_bytes() == before


def test_capture_size_is_bytes_and_checked_before_decode(monkeypatch):
    monkeypatch.setattr(observations, "LIMIT", 10)
    with pytest.raises(observations.CollectionFailure, match="input_limit"):
        observations.read_capture(io.BytesIO('"中文中文"'.encode("utf-8")))
    assert observations.read_capture(io.BytesIO(b'"12345678"')) == "12345678"


def test_regular_capture_file_mode_uses_installed_module(case):
    config, binding, _, capture = case
    path = write(Path(config["private_dir"]) / "capture.json", capture)
    result = subprocess.run([sys.executable, "-I", "-B", "-m", "task_console.observations",
                             "--binding", str(binding), "--capture", str(path)],
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["published"] is True
    assert Path(config["snapshot"]).is_file()


def fake_catalog_worker(tmp_path, mode):
    """Change only the catalog query; execute the actual installed worker/decoder."""
    script = tmp_path / "synthetic_catalog.py"
    heartbeat = tmp_path / "heartbeat"
    source = r'''
import importlib.abc, os, pathlib, subprocess, sys, time
mode, heartbeat_path = sys.argv[1:]
if mode == 'dependency':
    class Missing(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == 'skill_smith':
                raise ModuleNotFoundError('synthetic-private-command --secret')
    sys.meta_path.insert(0, Missing())
else:
    from skill_smith import catalog
    def fake_discover(request):
        child = "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); end=time.monotonic()+12\nwhile time.monotonic()<end:\n with p.open('ab') as f: f.write(b'x')\n time.sleep(0.02)"
        subprocess.Popen([sys.executable, '-I', '-B', '-c', child, heartbeat_path],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.monotonic() + 3
        heartbeat = pathlib.Path(heartbeat_path)
        while not heartbeat.exists() or heartbeat.stat().st_size == 0:
            if time.monotonic() > end: raise RuntimeError('heartbeat startup failed')
            time.sleep(0.01)
        if mode in ('stdout', 'stderr'):
            stream = sys.stdout.buffer if mode == 'stdout' else sys.stderr.buffer
            while True:
                stream.write(b'synthetic-private-command' * 4096)
                stream.flush()
        time.sleep(30)
    catalog.discover = fake_discover
from task_console import observations
raise SystemExit(observations.main(['--worker']))
'''
    script.write_text(source, encoding="ascii")
    return [sys.executable, "-I", "-B", str(script), mode, str(heartbeat)], heartbeat


@pytest.mark.skipif(os.name != "nt", reason="Windows shared Job containment")
@pytest.mark.parametrize("mode,reason", [("hung", "worker_timeout"), ("stdout", "worker_output_limit"),
                                       ("stderr", "worker_output_limit"), ("dependency", "dependency_unavailable")])
def test_actual_adapter_catalog_failure_retains_status_and_cleans_child(case, monkeypatch, capsys, mode, reason):
    config, binding, _, capture = case
    observations.publish(binding, capture, now=NOW)
    output = Path(config["snapshot"])
    before = output.read_bytes()
    config["catalog_request"] = str(write(Path(config["private_dir"]) / "catalog.json", {}))
    write(binding, config)
    command, heartbeat = fake_catalog_worker(Path(config["private_dir"]), mode)
    run = observations.process.run
    calls = []
    def substitute_catalog_worker(cmd, prompt, timeout, **kwargs):
        calls.append(cmd)
        assert cmd[:3] == [sys.executable, "-I", "-B"]
        assert cmd[-1] == "--worker"
        assert kwargs["max_input_bytes"] == kwargs["max_output_bytes"] == observations.LIMIT
        kwargs["max_output_bytes"] = 4096
        return run(command, prompt, min(timeout, 6), **kwargs)
    monkeypatch.setattr(observations.process, "run", substitute_catalog_worker)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(capture).encode()), encoding="utf-8"))
    started = time.monotonic()
    assert observations.main(["--binding", str(binding), "--receipt-stdout"]) == 2
    diagnostic = capsys.readouterr()
    assert json.loads(diagnostic.out)["reason_code"] == reason
    assert "synthetic-private-command" not in diagnostic.out + diagnostic.err
    assert len(calls) == 1 and time.monotonic() - started < 8
    assert output.read_bytes() == before
    if mode != "dependency":
        assert heartbeat.exists(), "negative control: the descendant must really run"
        size = heartbeat.stat().st_size
        assert size > 0
        time.sleep(0.2)
        assert heartbeat.stat().st_size == size, "descendant survived shared Job cleanup"


def test_deadline_exhaustion_does_not_launch_or_publish(case, monkeypatch, capsys):
    config, binding, _, capture = case
    observations.publish(binding, capture, now=NOW)
    before = Path(config["snapshot"]).read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail("expired collection launched a worker")
    monkeypatch.setattr(observations.process, "run", forbidden)
    assert observations.main(["--binding", str(binding), "--deadline-ms", "1"]) == 2
    assert json.loads(capsys.readouterr().err)["reason_code"] == "worker_timeout"
    assert Path(config["snapshot"]).read_bytes() == before


@pytest.mark.parametrize("raw", [b'{"schemaVersion":1,"schemaVersion":1}', b'{}{}', b'\xff'])
def test_worker_output_uses_real_single_document_duplicate_key_decoder(case, monkeypatch, raw):
    config, binding, _, capture = case
    observations.publish(binding, capture, now=NOW)
    before = Path(config["snapshot"]).read_bytes()
    def output(*args, **kwargs):
        return observations.process.ProcessOutput("", None, True, "success", returncode=0,
                                                   stdout_bytes=raw, stderr_bytes=b"")
    monkeypatch.setattr(observations.process, "run", output)
    with pytest.raises(ValueError):
        observations.publish(binding, capture, now=NOW, collect_snapshot=observations.collect_supervised)
    assert Path(config["snapshot"]).read_bytes() == before


def test_missing_initial_dependency_emits_fixed_receipt_before_import(case):
    config, _, _, _ = case
    bootstrap = Path(config["private_dir"]) / "missing_dependency.py"
    bootstrap.write_text('''import importlib.abc, runpy, sys
class Missing(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'fleet_guards':
            raise ModuleNotFoundError('synthetic-private-command --secret')
sys.meta_path.insert(0, Missing())
sys.argv = ['task_console.observations', '--receipt-stdout']
runpy.run_module('task_console.observations', run_name='__main__')
''', encoding="ascii")
    result = subprocess.run([sys.executable, "-I", "-B", str(bootstrap)], capture_output=True, timeout=8)
    assert result.returncode == 2
    assert json.loads(result.stdout) == {"schemaVersion": 1, "published": False,
                                       "reason_code": "dependency_unavailable"}
    assert result.stderr == b""


@pytest.mark.skipif(os.name != "nt", reason="Windows kill-on-owner-death Job containment")
def test_actual_observation_supervisor_death_closes_worker_job(case):
    config, binding, _, capture = case
    observations.publish(binding, capture, now=NOW)
    before = Path(config["snapshot"]).read_bytes()
    config["catalog_request"] = str(write(Path(config["private_dir"]) / "catalog.json", {}))
    write(binding, config)
    command, heartbeat = fake_catalog_worker(Path(config["private_dir"]), "hung")
    owner = Path(config["private_dir"]) / "synthetic_owner.py"
    owner.write_text('''import json, os, pathlib, sys, threading, time
from task_console import observations
command = json.loads(sys.argv[1])
heartbeat = pathlib.Path(command[-1])
run = observations.process.run
def substitute(cmd, prompt, timeout, **kwargs):
    return run(command, prompt, min(timeout, 8), **kwargs)
observations.process.run = substitute
def die():
    end = time.monotonic() + 6
    while time.monotonic() < end:
        if heartbeat.exists() and heartbeat.stat().st_size:
            os._exit(93)
        time.sleep(0.01)
threading.Thread(target=die, daemon=True).start()
raise SystemExit(observations.main(['--binding', sys.argv[2]]))
''', encoding="ascii")
    result = subprocess.run([sys.executable, "-I", "-B", str(owner), json.dumps(command), str(binding)],
                            input=json.dumps(capture).encode(), capture_output=True, timeout=12)
    assert result.returncode == 93, result.stderr
    assert heartbeat.exists() and heartbeat.stat().st_size > 0
    time.sleep(0.1)
    size = heartbeat.stat().st_size
    time.sleep(0.2)
    assert heartbeat.stat().st_size == size
    assert Path(config["snapshot"]).read_bytes() == before
