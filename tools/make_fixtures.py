"""Generate the public console examples from synthetic constants only."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path


def registration_recovery_case(root, *, pending=False):
    """Generate synthetic authority documents and opaque ciphertext placeholders."""
    import hashlib

    root = Path(root)
    private, state, vault = (root / name for name in ("private", "state", "vault"))
    for path in (private, state / "locks", state / "receipts", state / "journals", vault):
        path.mkdir(parents=True)
    (state / "locks" / (hashlib.sha256(b"authority").hexdigest() + ".lock")).write_bytes(b"0")
    generation, domain = "a" * 32, "b" * 32
    def protected(obj, owner):
        ref = "cred:" + ":".join((domain, obj, "c" * 64, owner))
        (vault / ("task-console-" + domain + "-" + obj + ".cred")).write_bytes(
            ("synthetic ciphertext " + obj).encode())
        return ref
    receipt = {"schemaVersion": 1, "generation": generation, "domain": domain,
               "input_reference": protected("d" * 32, generation),
               "authority": {"authority_epoch": 1, "migrated_tasks": []},
               "ownership": {}, "file_ownership": {}}
    encode = lambda v: json.dumps(v, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("ascii")
    (state / "receipts" / (generation + ".json")).write_bytes(encode(receipt))
    (state / "current.json").write_bytes(encode({"generation": generation,
        "receipt_digest": hashlib.sha256(encode(receipt)).hexdigest()}))
    owner = "e" * 32
    journal = {"schemaVersion": 1, "transaction_id": owner, "status": "publishing" if pending else "committed",
               "cleaned": not pending, "locks": ["authority"], "steps": [],
               "input_before": protected("f" * 32, owner)}
    (state / "journals" / (owner + ".json")).write_bytes(encode(journal))
    return private, state, vault, root / "recovery.zip"


def storage_retirement_case(root):
    """Create disposable work beside synthetic state and restoration inputs."""
    import hashlib

    root = Path(root)
    files = {
        "launcher-binding.json": '{"schemaVersion":1}',
        "task-console/console.sqlite3": "synthetic database placeholder",
        "task-console/console.sqlite3-wal": "synthetic pending transaction",
        "task-console/run-events.jsonl": '{"task":"AcmeTask"}\n',
        "task-console/registration/current.json": '{"generation":"example"}',
        "declarations-example/bootstrap/runtime-config.json": '{"example":true}',
        "storage/final-results.md": "Synthetic work complete; source is committed.\n",
        "maintenance/result.json": '{"synthetic":true}',
        "profile-rollback/original.json": '{"synthetic":true}',
        "work/completed/build-output.txt": "rebuildable synthetic output",
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    result = root / "storage/final-results.md"
    return {"schema_version": 1, "protected_paths": [], "retirements": [{
        "path": "work/completed", "completed": True, "source_reconciled": True,
        "active_writer": False, "final_deliverable": "storage/final-results.md",
        "final_sha256": hashlib.sha256(result.read_bytes()).hexdigest(),
    }]}


def synthetic_conversation(number=1, cwd="C:/Acme/project", title=None, turns=2):
    """Generate an in-memory transcript for temporary conversation tests."""
    sid = f"{number:08x}-0000-4000-8000-000000000001"
    records = []
    parent = None
    for index in range(turns):
        uid = f"{number:08x}-0000-4000-8000-{index + 2:012x}"
        records.append({"type": "user", "uuid": uid, "parentUuid": parent,
                        "sessionId": sid, "cwd": cwd,
                        "message": {"role": "user", "content": f"Synthetic question {number}.{index}"}})
        parent = uid
    if title:
        records.append({"type": "custom-title", "customTitle": title, "sessionId": sid})
    return sid, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records)


def synthetic_tool_conversation(number=500, turns=32, cwd="C:/Acme/source"):
    """A generated conversation with expandable tool/result chains for browser QA."""
    sid = f"{number:08x}-0000-4000-8000-000000000001"
    records, parent = [], None
    for turn in range(turns):
        content = [f"Synthetic request {turn}: inspect an example file",
                   [{"type": "tool_use", "id": f"call-{turn}", "name": "Read", "input": {"path": "example.txt"}}],
                   [{"type": "tool_result", "tool_use_id": f"call-{turn}", "content": "Example file content\n" * 40}],
                   [{"type": "text", "text": f"Synthetic response {turn}: inspected the example."}]]
        for step, value in enumerate(content):
            uid = f"{number:08x}-0000-4000-8000-{turn * 4 + step + 2:012x}"
            role = "user" if step % 2 == 0 else "assistant"
            records.append({"type": role, "uuid": uid, "parentUuid": parent, "sessionId": sid,
                "cwd": cwd, "message": {"role": role, "id": f"msg-{turn}-{step}", "content": value}})
            parent = uid
    records.append({"type": "custom-title", "customTitle": "Example tool conversation", "sessionId": sid})
    return sid, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records)


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


def add_creation_candidate(request):
    """A second declaration for the same synthetic action under a different name."""
    original = 'acme-maintenance/sync'
    duplicate = 'acme-maintenance/sync-copy'
    task = deepcopy(request['components'][0]['tasks'][0])
    task['id'] = 'sync-copy'
    request['components'][0]['tasks'].append(task)
    binding = deepcopy(request['bindings']['tasks'][original])
    binding['name'] = 'AcmeSyncCopy'
    request['bindings']['tasks'][duplicate] = binding
    return original, duplicate


def legacy_creation_xml(user_id='AcmeService'):
    """A pre-registration task has no embedded declaration in its XML."""
    from xml.sax.saxutils import escape
    return '''<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
<Principals><Principal id="Author"><UserId>''' + escape(user_id) + '''</UserId>
<LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
<Actions Context="Author"><Exec><Command>C:/Acme/runtime/python.exe</Command>
<Arguments>C:/Acme/source/sync.py --check</Arguments>
<WorkingDirectory>C:/Acme/source</WorkingDirectory></Exec></Actions></Task>'''


def work_action_case():
    """A synthetic todo with an owner-issued action offer."""
    return {"id": "acme-todo", "title": "Prepare Acme report", "state": "pending",
            "role": "tracked_item", "source": "user", "summary": "Synthetic report request",
            "actions": {"available": True, "revision": "sha256:synthetic",
                        "offers": [{"id": "agent", "kind": "agent", "label": "整理报告",
                                    "description": "根据待办内容处理", "enabled": True}],
                        "links": [], "current": None}}


def launch_case():
    """Registered task actions, including paths and arguments that must not be shortened."""
    return {"name": "Acme's sync", "taskPath": "\\Acme\\", "state": "Ready",
            "actions": [{"exec": "C:\\Acme Tools\\python.exe", "args": '-B "C:\\Acme\\sync.py" --check',
                         "cwd": "C:\\Acme"}, {"exec": "C:\\Acme\\verify.exe", "args": "--full", "cwd": ""}],
            "userId": "AcmeService", "runLevel": "LeastPrivilege", "multi": "IgnoreNew",
            "catchup": True, "refuseOnBattery": True, "stopOnBattery": False,
            "triggers": "daily 03:15", "timeout": "PT7M", "retries": 2}


def skill_delete_case(root):
    """Create a disposable skill; real user files are never used by deletion tests."""
    skill = Path(root) / "acme-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: acme-skill\ndescription: Synthetic tool\n---\n", encoding="utf-8")
    (skill / "guide.txt").write_text("Synthetic guide\n", encoding="utf-8")
    return skill


def plugin_delete_case():
    return {"name": "acme@example", "scope": "user", "version": "1.0", "enabled": True}


def plugin_inventory_case():
    return [{"id": "acme@example", "scope": "user", "enabled": True},
            {"id": "sample@example", "scope": "user", "enabled": False}]


def plugin_cli_sandbox(root):
    """An isolated CLI configuration containing synthetic plugins and no credentials."""
    root = Path(root)
    installed = root / 'plugins/cache/example/acme/1.0.0'
    linked = root / 'skills/sample'
    for path, name in ((installed, 'acme'), (linked, 'sample')):
        (path / '.claude-plugin').mkdir(parents=True)
        (path / '.claude-plugin/plugin.json').write_text(json.dumps({
            'name': name, 'version': '1.0.0', 'description': 'Generated test plugin'}), encoding='utf-8')
        (path / 'SKILL.md').write_text('---\nname: '+name+'\ndescription: Generated test skill\n---\n', encoding='utf-8')
    (root / 'plugins/installed_plugins.json').write_text(json.dumps({'version': 2, 'plugins': {
        'acme@example': [{'scope': 'user', 'installPath': str(installed), 'version': '1.0.0',
                         'installedAt': '2030-01-01T00:00:00Z', 'lastUpdated': '2030-01-01T00:00:00Z'}]}}), encoding='utf-8')
    (root / 'settings.json').write_text(json.dumps({'enabledPlugins': {
        'acme@example': True, 'sample@skills-dir': True}}), encoding='utf-8')
    return root


def maintenance_sandbox(root):
    """Synthetic state for exercising real maintenance writers in an isolated root."""
    root = plugin_cli_sandbox(root)
    (root / 'skills-archive').mkdir()
    (root / 'memory/archive').mkdir(parents=True)
    (root / 'memory/acme.md').write_text('---\nname: Acme\ndescription: Synthetic note\ntype: project\nmetadata:\n  status: active\n---\nGenerated content.\n', encoding='utf-8')
    (root / 'memory/MEMORY.md').write_text('- [Acme](acme.md): synthetic note\n', encoding='utf-8')
    (root / 'sessions').mkdir()
    (root / 'sessions/acme.jsonl').write_text('{"type":"synthetic"}\n', encoding='utf-8')
    (root / 'temp-cache/temp_git_1_acme').mkdir(parents=True)
    (root / 'temp-cache/temp_git_1_acme/content.txt').write_text('synthetic cache', encoding='utf-8')
    return root


def integration_feed_case():
    feed = work_feed_case()
    feed['sources'] = [{'source': 'example-mail', 'role': 'signal', 'state': 'notified', 'count': 1},
                       {'source': 'example-mail', 'role': 'tracked_item', 'state': 'pending', 'count': 2}]
    return feed


def integration_origins_case():
    feed = integration_feed_case()
    feed['sources'].append({'source': 'example-manual', 'role': 'tracked_item', 'state': 'done', 'count': 2})
    return feed


def work_feed_case():
    """Small composition fixture with deliberately non-equivalent work states."""
    rows=[]
    for item_id,role,state in [('active','agent_work','running'),('draft','agent_work','stalled'),
                              ('result','agent_work','done'),('reminder','tracked_item','pending'),
                              ('news','signal','pending')]:
        rows.append({'id':item_id,'role':role,'title':'Acme '+item_id,'summary':'Synthetic summary',
                     'state':state,'source':'synthetic','updated_at':'2030-01-02T00:00:00Z',
                     'execution':{'state':state,'evidence':'summary_only'} if role=='agent_work' else None})
    return {'schemaVersion':1,'available':True,'items':rows,'events':[],'sources':[],
            'coverage':{'total':5,'returned':5,'roles':{'agent_work':3,'tracked_item':1,'signal':1}}}


def linked_work_case():
    feed = work_feed_case()
    feed['items'][0]['origin_item_id'] = 'reminder'
    return feed


def usability_case():
    """Generated cases for filters, stale reads and trigger display boundaries."""
    feed=work_feed_case()
    for state in ('done','cancelled','snoozed','blocked'):
        item=deepcopy(feed['items'][3])
        item.update(id='tracked-'+state,state=state)
        feed['items'].append(item)
    feed['sources']=[{'source':'synthetic','count':len(feed['items'])}]
    feed['events']=[{'item_id':key,'title':'Acme event','event_type':'created'} for key in ('active','not-loaded')]
    feed['coverage'].update(total=len(feed['items']),returned=len(feed['items']),roles={'agent_work':3,'tracked_item':5,'signal':1},events_available=True,event_total=2)
    feed['observed_at']='2030-01-02T00:00:00Z'
    triggers=[
        {'kind':'Time','enabled':True,'start':'2030-01-02T10:15:00','interval':'PT5M'},
        {'kind':'Daily','enabled':False,'days':2,'start':'2030-01-02T08:00:00','interval':'PT1H','duration':'PT3H'},
        {'kind':'Weekly','weeks':2,'dow':10,'start':'2030-01-02T09:00:00','end':'2030-12-31T09:00:00'},
        {'kind':'AcmeCustom','start':'2030-01-02T09:00:00'},
    ]
    return {'feed':feed,'triggers':triggers,
            'cleanup':{'items':[{'rel':'acme.jsonl','bytes':1024}]},
            'partial_delete':{'ok':False,'deleted':1,'freed':1024,'stoppedAt':'sample.jsonl','error':'synthetic error'}}


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


def automation_info_case() -> dict:
    """Task metadata and a scheduler row, generated without machine observations."""
    return {
        "row": {"name": "AcmeSync", "description": "Legacy title: legacy summary",
                "state": "Ready", "rcRaw": 0, "catchup": True, "timeout": "PT5M",
                "stopOnBattery": False, "refuseOnBattery": False, "triggersRaw": []},
        "info": {"title": "同步示例文件", "summary": "检查并同步合成文件。",
                 "cadence": "每天 08:00", "status": "最近一次检查发现差异。",
                 "advice": "核对合成输出后重试。", "verdict": "fix", "asOf": "2026-10-01"},
    }


def categories_example() -> dict:
    """Portable category config with complete, partial and absent metadata."""
    return {
        "_comment": [
            "Generated by tools/make_fixtures.py. All names and descriptions are synthetic.",
            "Copy to your private TASK_CONSOLE_CATEGORIES file and replace the examples there.",
            "Tasks absent from every category remain visible in the uncategorized group.",
            "Optional taskDesc maps task names to legacy title:summary strings.",
            "Optional taskInfo maps task names to title, summary, cadence, status, advice, verdict and asOf.",
            "All taskInfo fields are optional strings. Unknown keys are dropped and named in a page warning.",
            "Non-object entries or non-string known fields cause the whole entry to be ignored; the warning names the task and its row says what is broken.",
            "An unknown verdict is kept as verdictUnrecognized and shown as unrecognized, never as not assessed.",
            "Verdicts: keep, fix, urgent, adjust, decide, remove, disabled (keep it disabled). No verdict, or an empty one, means not assessed.",
            "A taskInfo key that matches no scheduled task is named in a page warning.",
            "asOf convention: YYYY-MM-DD, the date status and advice were checked. It is not a live health result.",
        ],
        "categories": [
            {"name": "Bus and execution", "desc": "message intake and work-order execution",
             "tasks": ["AcmeBusIngestTick", "AcmeBusWorkTick"]},
            {"name": "Backup and sync", "desc": "config and corpus into version control",
             "tasks": ["AcmeConfigSync", "AcmeCorpusSync"],
             "taskDesc": {"AcmeConfigSync": "同步示例配置：检查合成文件的差异。",
                          "AcmeCorpusSync": "同步示例语料：保存合成文本。"},
             "taskInfo": {"AcmeConfigSync": automation_info_case()["info"],
                          "AcmeCorpusSync": {"title": "归档示例语料",
                                             "summary": "保存合成文本，便于核对改动。"}}},
            {"name": "Health and governance", "desc": "the layer that watches the other tasks",
             "tasks": ["AcmeHealthMonitor"]},
            {"name": "Personal monitors", "desc": "price, stock, timesheet",
             "tasks": ["AcmePriceWatch"]},
        ],
    }


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


def action_session_fixture(root):
    """Exact synthetic conversation identity for action-context tests."""
    session = '11111111-2222-4333-8444-555555555555'
    path = Path(root) / 'acme-project' / (session + '.jsonl')
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{'sessionId': session, 'type': 'user', 'uuid': 'synthetic-message',
             'message': {'content': 'Prepare the Acme report using synthetic inputs.'}},
            {'sessionId': 'unrelated-session', 'type': 'user', 'message': {'content': 'UNRELATED_CANARY'}}]
    path.write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
    return session, path


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


def generate_storage_schemas(output: Path, *, repo_layout=False) -> None:
    documents = {
        "launcher-binding.json.example": {"schemaVersion": 1, "runtime": {
            "InstalledRoot": "C:/Acme/runtime/installed", "StateRoot": "C:/AcmePrivate/registration"},
            "environment": {"TASK_CONSOLE_DB": "C:/AcmePrivate/console.sqlite3"}},
        "storage-retention.json.example": {"schema_version": 1, "protected_paths": [], "retirements": []},
        "task-console/run-events.jsonl.example": {"e": 1, "r": 1, "t": "AcmeTask", "i": 100,
                                                "ts": "2030-01-01 00:00:00", "rc": None},
    }
    for name, document in documents.items():
        path = output / (name if repo_layout else Path(name).name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((json.dumps(document, sort_keys=True) + "\n").encode("utf-8"))


def generate(output: Path, *, categories_output: Path | None = None) -> None:
    request = example_request()
    output.mkdir(parents=True, exist_ok=True)
    documents = {".console.json": request["components"][0], "bindings.example.json": request["bindings"],
                 "machine.console.example.json": request["machine"], "baseline.example.json": request["baseline"],
                 "installations.request.example.json": installation_request(),
                 "categories.example.json": categories_example()}
    for name, data in documents.items():
        category = name == "categories.example.json"
        destination = categories_output if category and categories_output is not None else output / name
        destination.write_bytes((json.dumps(data, ensure_ascii=not category, indent=2) + "\n").encode("utf-8"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="Write all generated fixtures into this directory")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    generate(args.out or root / "examples" / "console",
             categories_output=None if args.out else root / "scripts" / "task_console" / "categories.example.json")
    generate_storage_schemas(args.out or root, repo_layout=not args.out)
def consolidated_work_case():
    """Synthetic history, independent alarms and missing/cyclic parents."""
    return [
        {'id':'root','role':'tracked_item','title':'Collect synthetic parcel','state':'pending'},
        {'id':'old','role':'signal','title':'Parcel prepared','state':'cancelled','group_parent_id':'root'},
        {'id':'alarm','role':'tracked_item','title':'Leave for collection','state':'pending','group_parent_id':'root'},
        {'id':'orphan','role':'tracked_item','title':'Separate task','state':'pending','group_parent_id':'missing'},
        {'id':'cycle-a','role':'tracked_item','title':'Cycle A','state':'pending','group_parent_id':'cycle-b'},
        {'id':'cycle-b','role':'tracked_item','title':'Cycle B','state':'pending','group_parent_id':'cycle-a'},
    ]


def searchable_work_group_case():
    """Synthetic parent whose text differs from its earlier steps and alarm."""
    rows = consolidated_work_case()
    for row in rows:
        row['source'] = 'example-mail' if row['role'] == 'signal' else 'example-schedule'
    return {'available': True, 'items': rows, 'events': [], 'sources': [], 'coverage': {}}


def blocked_agent_case():
    return {'id':'queued-work','role':'agent_work','title':'Acme queued work','state':'pending',
            'execution':{'state':'queued','queue_reason':'cleanup_unconfirmed','blocked_by':['old-work']}}


def task_delete_plan_case(*, blocking=False):
    """A synthetic delete preview for an unmanaged task whose name contains spaces."""
    steps = [{"id": "archive.export", "title": "导出任务 XML 存档", "target": "D:\\AcmePrivate\\deleted-tasks",
              "status": "planned", "detail": "导出完整的任务定义和回执，写入后读回核对。"},
             {"id": "scheduler.unregister", "title": "从计划程序删除", "target": "计划程序根路径",
              "status": "planned", "detail": "删除前重新比对任务定义。"},
             {"id": "categories.edit", "title": "从分类配置中移除", "target": "C:\\Acme\\categories.json",
              "status": "planned", "detail": "移除 2 处（tasks、taskDesc、taskInfo）。"},
             {"id": "followup.apply", "title": "清理监控清单、备份与迁移计划", "target": "D:\\AcmePrivate\\followup.py",
              "status": "planned", "detail": "后续钩子的预览已就绪"}]
    reasons = ["没有设置删除存档目录（TASK_CONSOLE_DELETED_ARCHIVE），不受控制器管理的任务不能删除"] if blocking else []
    return {"ok": True, "name": "Acme Backup Daily", "reason": "被 AcmeSync 取代", "managed": False,
            "task": {"state": "Ready", "definitionSha256": "synthetic",
                     "actions": [{"execute": "C:\\Acme\\sync.exe", "arguments": "--daily", "workingDirectory": "C:\\Acme"}]},
            "steps": steps, "authority": None,
            "followup": {"state": "ok", "message": "后续钩子的预览已就绪", "blocking": [], "notes": [], "exitCode": 0,
                         "steps": [{"id": "health", "title": "移出健康监控清单", "target": "acme-health.json",
                                    "status": "planned", "detail": "合成步骤"}]},
            "categories": {"path": "C:\\Acme\\categories.json", "present": True, "editable": True, "verdict": "keep"},
            "blocking": reasons, "notes": ["合成说明：Acme 文档仍提到这个任务"],
            "warnings": [{"code": "verdict_not_remove", "verdict": "keep",
                          "message": "分类配置里对这个任务的建议不是「可删」，删除前请确认。"}],
            "applicable": not blocking, "token": None if blocking else "synthetic-delete-token",
            "expiresIn": None if blocking else 300}


def task_delete_result_case(status="partial"):
    """Synthetic delete results: ok, partial (no follow-up hook), failed (Scheduler refused),
    unknown (the readback after the delete command could not be read) and followup_blocked
    (the task is gone but the hook's apply preflight refused, so the result carries a retry)."""
    done = {"id": "scheduler.unregister", "title": "从计划程序删除", "target": "计划程序根路径",
            "status": "ok", "detail": "已删除，读回确认计划程序里已没有它"}
    if status == "followup_blocked":
        hook = "D:\\AcmePrivate\\followup.py"
        request = {"schema": 1, "mode": "apply",
                   "task": {"name": "AcmeSync", "managed": False,
                            "actions": [{"execute": "C:\\Acme\\sync.exe", "arguments": "--daily", "workingDirectory": "C:\\Acme"}]},
                   "reason": "合成清理", "categories": {"path": "D:\\AcmePrivate\\categories.json", "edited": True},
                   "authority": {"transaction_id": None, "files": []}}
        command = f'"C:\\Acme\\Python\\python.exe" -B -I "{hook}" < "<请求文件>"'
        followup = {"state": "blocked", "message": "任务已经删除，但后续钩子的预检没有放行，监控清单、备份和迁移计划一处都没有清理",
                    "steps": [], "blocking": ["合成：现在在夜间备份窗口里"], "notes": [], "exitCode": 2}
        remaining = ["后续清理没有运行：合成：现在在夜间备份窗口里", "单独重跑后续清理：合成说明 " + command]
        return {"ok": False, "status": "partial", "name": "AcmeSync", "managed": False, "authority": None,
                "steps": [done, {"id": "followup.apply", "title": "清理监控清单、备份与迁移计划", "target": hook,
                                 "status": "blocked", "detail": followup["message"]}],
                "followup": followup, "remaining": remaining, "notes": [],
                "followupRetry": {"hook": hook, "request": request, "command": command, "howto": remaining[1]},
                "message": "任务已从计划程序删除，但还有没完成的清理：" + "；".join(remaining)}
    if status == "unknown":
        return {"ok": False, "status": "unknown", "name": "AcmeSync", "managed": False, "authority": None,
                "followup": None, "notes": [],
                "steps": [dict(done, status="failed", detail="删除命令没有正常返回（TimeoutExpired），而且无法确认任务是否还在：合成")],
                "remaining": ["刷新页面核对计划程序里还有没有这个任务"],
                "message": "无法确认是否已删除：删除命令没有正常返回（TimeoutExpired），而且无法确认任务是否还在：合成"}
    if status == "failed":
        return {"ok": False, "status": "failed", "name": "AcmeSync", "managed": False, "authority": None,
                "followup": None, "notes": [],
                "steps": [dict(done, status="failed", detail="删除失败，任务仍在：合成拒绝"),
                          {"id": "followup.apply", "title": "清理监控清单、备份与迁移计划", "target": "未配置",
                           "status": "skipped", "detail": "任务没有删除，没有运行"}],
                "remaining": ["从计划程序删除：删除失败，任务仍在：合成拒绝"], "message": "没有删除：合成拒绝"}
    followup = ({"state": "ok", "message": "后续清理已完成", "steps": [], "blocking": [], "notes": [], "exitCode": 0}
                if status == "ok" else
                {"state": "not_configured", "message": "没有配置删除后续钩子", "steps": [], "blocking": [],
                 "notes": [], "exitCode": None})
    last = ({"id": "followup.apply", "title": "清理监控清单、备份与迁移计划", "target": "D:\\AcmePrivate\\followup.py",
             "status": "ok", "detail": "后续清理已完成"} if status == "ok" else
            {"id": "followup.apply", "title": "清理监控清单、备份与迁移计划", "target": "未配置",
             "status": "skipped", "detail": "没有配置删除后续钩子：需要手动处理。"})
    remaining = [] if status == "ok" else ["健康监控清单、备份和迁移计划没有清理（未配置删除后续钩子），需要手动处理"]
    return {"ok": status == "ok", "status": status, "name": "AcmeSync", "managed": False, "authority": None,
            "steps": [done, last], "followup": followup, "remaining": remaining, "notes": [],
            "message": "任务已删除，各项清理都已完成。" if status == "ok" else
                       "任务已从计划程序删除，但还有没完成的清理：" + remaining[0]}


def task_repair_preview_case(existing=None):
    """Synthetic facts for one task, as the repair preview returns them."""
    return {"ok": True, "name": "Acme Report Sync", "existing": existing,
            "work": {"available": True, "reason": None},
            "limits": ["只诊断并提出修复方案。", "不得删除任何东西。"],
            "facts": {"name": "Acme Report Sync", "category": "报表",
                      "info": {"title": "Acme 报告同步", "advice": "合成建议：先看返回码", "verdict": "fix", "asOf": "2030-01-01"},
                      "state": "Ready", "status": {"key": "bad", "label": "失败 0x1"},
                      "lastRun": "2030-01-02 03:00", "lastResult": {"hex": "0x1", "raw": 1, "meaning": "失败 0x1"},
                      "nextRun": "2030-01-03 03:00", "missedRuns": 0, "triggers": "Daily @03:00",
                      "actions": [{"execute": "C:\\Acme\\report.exe", "arguments": "--sync", "workingDirectory": "C:\\Acme"}],
                      "artifact": {"path": "C:\\Acme\\out\\report.json", "maxAgeHours": 26},
                      "health": [{"label": "Acme 报告", "state": "down", "reasons": ["合成：产物 30 小时没更新"]}],
                      "issues": [{"level": "warn", "text": "合成：漏火不补跑"}],
                      "recentFailures": {"window": "最近 30 天", "failingCodes": [{"code": "0x1", "count": 5}],
                                         "starts": 12, "successRate": 58.3},
                      "polls": None, "observedAt": "2030-01-02 09:00:00"}}


def console_backup_case(data_root):
    """Keep a synthetic writer open so committed changes remain in the WAL."""
    import sqlite3

    path = data_root / "task-console" / "console.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript('''
        CREATE TABLE events (id INTEGER PRIMARY KEY, task TEXT);
        CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT);
        CREATE INDEX events_task ON events(task);
        CREATE VIEW event_names AS SELECT task FROM events;
        INSERT INTO events VALUES (1, 'AcmeSync');
        INSERT INTO settings VALUES ('synthetic', 'enabled');
        PRAGMA user_version=1;
    ''')
    connection.commit()
    return connection


def scheduled_console_backup_case(root):
    """An isolated synthetic companion and local bare remote with recording Git hooks."""
    import subprocess

    repo = root / "companion"
    repo.mkdir()
    remote = root / "remote.git"

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, text=True, encoding="utf-8").stdout.strip()

    git("init", "--initial-branch=main")
    git("config", "user.name", "Acme Example")
    git("config", "user.email", "user1@example.com")
    # Fixture hooks record normal execution without using real owner identity policy.
    git("config", "core.hooksPath", ".git/hooks")
    for name in ("pre-commit", "pre-push"):
        hook = repo / ".git/hooks" / name
        hook.write_text('#!/bin/sh\nprintf "' + name + '\\n" >> "$(git rev-parse --git-path fixture-hooks-ran)"\n',
                        encoding="utf-8", newline="\n")
        hook.chmod(0o755)
    (repo / "README.md").write_text("Synthetic companion for AcmeSync.\n", encoding="utf-8")
    (repo / ".gitignore").write_text("*.sqlite3\n*.sqlite3-wal\n*.sqlite3-shm\n*.lock\n", encoding="utf-8")
    git("add", "README.md", ".gitignore")
    git("commit", "-m", "Initialize synthetic companion")
    git("init", "--bare", str(remote))
    git("remote", "add", "origin", str(remote))
    git("push", "-u", "origin", "main")
    connection = console_backup_case(repo / "data")
    return repo, connection


def task_repair_order_case(action_state="running", state="pending"):
    """One synthetic repair order as /api/task/repairs maps it to a task name."""
    action = None if action_state is None else {"id": "receipt-1", "state": action_state, "summary": "合成进度",
                                                "work_item_id": "acme-work-1", "updated_at": "2030-01-02T00:00:00Z"}
    return {"item_id": "acme-repair-1", "state": state, "note": "合成备注", "updated": "2030-01-02T00:00:00Z",
            "title": "修复计划任务：Acme 报告同步（Acme Report Sync）", "action": action}
