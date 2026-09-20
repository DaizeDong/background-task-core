"""Generate the public console examples from synthetic constants only."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path


def example_request() -> dict:
    manifest = {
        "schemaVersion": 1, "component": "acme-maintenance", "read": "acme-status",
        "tasks": [{
            "id": "sync", "kind": "oneshot", "entrypoint": "acme-sync",
            "schedule_hint": {"type": "interval", "minutes": 60},
            "timeout_seconds": 300, "concurrency_key": "acme-destination",
            "checks": [{"id": "output", "required": True}, {"id": "dependencies", "required": False}],
        }],
    }
    runtime = {
        "name": "AcmeSync", "enabled": False,
        "argv": ["C:/Acme/runtime/python.exe", "C:/Acme/source/sync.py", "--check"],
        "cwd": "C:/Acme/source", "timezone": "UTC",
        "trigger": {"type": "daily", "at": "03:15"},
        "principal": {"user_id": "AcmeService", "logon_type": "InteractiveToken", "run_level": "LeastPrivilege"},
        "power": {"disallow_start_on_batteries": True, "stop_on_batteries": False, "wake_to_run": False},
        "timeout_seconds": 420, "concurrency": {"policy": "IgnoreNew"},
    }
    checks = [
        {"id": "output", "legacy": {"label": "Synthetic output", "max_age_hours": 26,
                                    "artifact": "C:/Acme/data/output.json", "ok_exit_codes": [0, 2]}},
        {"id": "dependencies", "legacy": {"label": "Synthetic dependencies", "max_age_hours": 48}},
    ]
    binding = dict(runtime, source_root="C:/Acme/source", backup=True, category="maintenance", checks=checks)
    categories = [{"id": "maintenance", "name": "Maintenance", "desc": "Synthetic maintenance tasks"}]
    baseline_health = [{"name": "AcmeSync", **c["legacy"]} for c in checks]
    return {
        "schemaVersion": 1, "components": [manifest],
        "bindings": {"schemaVersion": 1, "tasks": {"acme-maintenance/sync": binding}},
        "machine": {"schemaVersion": 1, "authority_epoch": 0, "migrated_tasks": [],
                    "overrides": {}, "categories": categories},
        "baseline": {
            "tasks": [deepcopy(runtime)],
            "task_names": "$TaskNames = @( 'AcmeSync' )\n",
            "task_health": {"tasks": baseline_health},
            "categories": {"categories": [{"name": "Maintenance", "desc": "Synthetic maintenance tasks", "tasks": ["AcmeSync"]}]},
        },
    }


def installation_request() -> dict:
    """Two installations of an unchanged public manifest, supplied by a catalog caller."""
    request = example_request()
    manifest = request["components"][0]
    binding = request["bindings"]["tasks"].pop("acme-maintenance/sync")
    request["components"] = []
    request["bindings"]["installations"] = {}
    request["baseline"]["tasks"] = []
    for namespace, scope, name in (("acme-user", "user", "AcmeUserSync"),
                                   ("acme-project", "project", "AcmeProjectSync")):
        request["components"].append({"namespace": namespace, "manifest": deepcopy(manifest)})
        request["bindings"]["installations"][namespace] = {
            "component": manifest["component"],
            "identity": {"marketplace": "acme-market", "scope": scope, "client": "acme-client",
                         "source_id": "acme-source-" + scope,
                         "metadata": {"selected_version": "1.0", "origin": {"kind": "synthetic"}}},
        }
        request["bindings"]["tasks"][namespace + "/acme-maintenance/sync"] = dict(deepcopy(binding), name=name)
    return request


def powershell_repair_cases() -> dict[str, str]:
    """Synthetic grammar probes; parse these strings, never execute them."""
    base = "$TaskNames = @('AcmeA'); "
    cases = {
        "braced": base + "${TaskNames} += 'AcmeB'",
        "scoped": base + "${script:TaskNames} = @('AcmeB')",
        "comment": base + "${TaskNames} <# gap #> += 'AcmeB'",
        "continuation": base + "${TaskNames} `\n += 'AcmeB'",
        "index": base + "${TaskNames}[0] <# gap #> = 'AcmeB'",
        "plain_comment": base + "$TaskNames <# gap #> += 'AcmeB'",
        "plain_continuation": base + "$TaskNames `\r\n += 'AcmeB'",
        "increment": base + "${TaskNames}[0]++",
        "prefix": base + "++${TaskNames}[0]",
        "mixed_case": base + "${tAsKnAmEs} -= 'AcmeB'",
        "global": base + "${global:TaskNames} <# outer <# inner #> #> = @('AcmeB')",
        "local": base + "${local:TaskNames} `\r\n <# gap #> += 'AcmeB'",
        "read_reference": base + "$other = ${TaskNames}[0]",
    }
    for quote in ("'", '"'):
        cases["here_" + quote] = "$message = (@" + quote + "\nexample\n" + quote + "@).Trim()\n" + base
        cases["here_mutation_" + quote] = base + "$message = @" + quote + "\nexample\n" + quote + "@; ${TaskNames} += 'AcmeB'"
    return cases


def category_description_case() -> dict:
    """Private-description import shape, using only generated synthetic values."""
    request = installation_request()
    tasks = request["bindings"]["tasks"]
    for index, binding in enumerate(tasks.values(), 1):
        binding["description"] = f"Synthetic task {index}: check output; $(literal text)"
    request["baseline"]["categories"] = {"categories": [{
        "name": "Maintenance", "desc": "Synthetic maintenance tasks",
        "tasks": [binding["name"] for binding in tasks.values()],
        "taskDesc": {binding["name"]: binding["description"] for binding in tasks.values()},
    }]}
    return request


def health_case() -> dict:
    """Synthetic component observation shared by CLI, HTTP and panel regressions."""
    now = 1_800_000_000
    return {"now": now, "spec": {"component": "acme-maintenance", "id": "sync", "kind": "oneshot",
            "timeout_seconds": 300, "checks": [{"id": "output"}, {"id": "dependencies"}]},
            "observations": {"schemaVersion": 1, "run_id": "acme-current",
                "execution": {"state": "completed", "exit_code": 0, "code_domain": "process",
                              "started_at": now - 60, "finished_at": now - 10},
                "last_success": {"run_id": "acme-previous", "finished_at": now - 3600},
                "checks": [{"id": "output", "state": "healthy", "run_id": "acme-current"},
                           {"id": "dependencies", "state": "healthy", "run_id": "acme-current"}]}}


def catalog_snapshot() -> dict:
    """SMITH's documented v1 record shape, generated without scanning a machine."""
    dimensions = ("declared", "enabled", "cached", "installed", "resolved", "discovered", "compatible")
    records = []
    for kind, name, client in (("skill", "acme-skill", "shared"), ("plugin", "acme-plugin@acme-market", "codex"),
                               ("mcp_binding", "acme-connector", "codex")):
        records.append({"source_id": "synthetic:" + name, "kind": kind, "registry_key": name,
                        "origin": {"kind": "synthetic", "client": client}, "relative_path": name,
                        "version": "1.0", "source_hash": None, "dependencies": [],
                        "status": {key: "unknown" for key in dimensions},
                        "entrypoints": [] if kind == "mcp_binding" else [
                            {"kind": "agent_template" if kind == "plugin" else "skill", "name": name,
                             "client": client, "status": {"discovered": "yes"}}]})
    return {"schema_version": 1, "observed_at": "2027-01-15T08:00:00Z", "records": records,
            "coverage": {"skill_roots": {"status": "checked", "expected": 2, "checked": 2},
                         "plugin_registries": {"status": "partial", "expected": 2, "checked": 1}},
            "problems": [{"reason": "synthetic missing registry"}]}


def legacy_health_snapshot() -> dict:
    now = health_case()["now"]
    return {"schemaVersion": 1, "declarations": [
        {"name": "AcmeSync", "check": "output", "max_age_hours": 24,
         "artifact": "synthetic-output", "artifact_max_age_hours": 24, "ok_exit_codes": [2]}],
        "rows": {"AcmeSync": {"state": "Ready", "last_rc": "0x80070002", "last_run": now - 60}},
        "artifact_observations": {"synthetic-output": {"mtime": now - 60}}}


def console_page_fixture() -> dict:
    """Synthetic task page fields for local browser checks, without scheduler access."""
    row = {"name": "AcmeSync", "state": "Ready", "sk": "ok", "sl": "Completed",
           "issues": [], "desc": "Synthetic output check", "retries": 0, "timeout": "PT5M",
           "inAllow": True, "inHealth": True, "triggers": "daily", "catchup": True,
           "lastRun": "2027-01-15T08:00:00Z", "nextRun": "2027-01-16T08:00:00Z"}
    return {"groups": [{"cat": "Synthetic", "desc": "Generated test tasks", "rows": [row]}],
            "summary": {"total": 1, "bad": 0, "issues": 0, "disabled": 0,
                        "generated": "synthetic", "allowChecked": True},
            "history": {"available": False, "reason": "synthetic unchecked", "days": []},
            "runlog": {"available": False, "reason": "synthetic unchecked"},
            "timeline": {"rows": []}, "scores": [], "warnings": [],
            "freshness": {"tasks": [], "summary": {"total": 0, "counts": {}, "coverage": 0},
                          "reason": "synthetic unchecked"}}


def review_pipeline_case() -> dict:
    """Synthetic receipts for UI evidence boundaries, never copied from a live run."""
    return {"available": True, "tasks": [
        {"component": "example-sync", "name": "SyncClaudeToCodex",
         "task_id": "synthetic/sync", "verdict": "degraded", "run_id": None,
         "last_run_v1": {"version": 1, "mode": "apply", "status": "degraded",
                         "finished_at": "2020-01-01T00:01:00Z", "remaining_changes": 0,
                         "findings": [{"area": "skills", "name": "synthetic-skill", "status": "blocked"},
                                      {"area": "memory", "status": "partial"}]}},
        {"component": "example-backup", "name": "SyncClaudeConfig",
         "task_id": "synthetic/backup", "verdict": "healthy", "run_id": None,
         "checks": [{"check_id": name, "state": "healthy"} for name in
                    ("changelog", "journal", "fleet-check", "memory-doctor", "diff-review")]}
    ]}


def operations_case() -> dict:
    """Small UI interactions fixture, generated independently of runtime data."""
    conversations = []
    for index, name in enumerate(("Acme project", "Sample project")):
        conversations.append({"cwd": "/synthetic/" + name, "count": 3, "humanish": 1,
            "bytes": 2048, "newest": 1_800_000_000, "truncated": True, "shown": [
                {"title": name + " planning", "titleFrom": "rename", "file": f"/synthetic/{index}.jsonl",
                 "humanSeen": 3, "partial": False, "ageHours": 1, "bytes": 1024,
                 "preview": "synthetic searchable content"}]})
    return {"conversations": {"available": True, "summary": {"files": 6, "humanish": 2,
                "bytes": 4096, "groups": 2}, "groups": conversations},
            "skills": [{"name": "Acme", "chars": 10, "archived": False},
                       {"name": "Sample", "chars": 30, "archived": True}],
            "repository": {"name": "acme", "state": "clean", "identity": {"state": "mismatch"},
                           "unpushedKnown": False, "behindKnown": False}}


def generate(output: Path) -> None:
    request = example_request()
    output.mkdir(parents=True, exist_ok=True)
    documents = {".console.json": request["components"][0], "bindings.example.json": request["bindings"],
                 "machine.console.example.json": request["machine"], "baseline.example.json": request["baseline"],
                 "installations.request.example.json": installation_request()}
    for name, data in documents.items():
        (output / name).write_text(json.dumps(data, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "examples" / "console")
    generate(parser.parse_args().out)
