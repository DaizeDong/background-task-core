"""删除计划任务:预览绑定、受管与不受管两条路、分类配置逐字节改写、后续钩子的五种结果。

全部是合成任务名(AcmeSync、Acme Backup Daily)和 tmp_path 里现造的文件。计划程序、控制器、
钩子都在这个模块已经划好的边界上替换:三条 PowerShell 各是一个函数,控制器是 controller 模块,
钩子是一个真跑的合成 Python 脚本。没有一条用例会碰真实的计划程序或任何真实文件。
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import textwrap
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "task_console"))
sys.path.insert(0, str(ROOT))

import maint  # noqa: E402
import task_delete as D  # noqa: E402

REAL_AUTHORITY = D._authority  # fixture 会替换它;需要真实那一份的用例从这里取
NAME = "AcmeSync"
SPACED = "Acme Backup Daily"
XML = '<?xml version="1.0" encoding="UTF-16"?>\n<Task><Actions><Exec><Command>C:\\Acme\\sync.exe</Command></Exec></Actions></Task>'
ACTIONS = [{"execute": "C:\\Acme\\sync.exe", "arguments": "--daily", "workingDirectory": "C:\\Acme"}]


def described(name=NAME, state="Ready", xml=XML):
    return {"name": name, "state": state, "xml": xml,
            "sha256": hashlib.sha256(xml.encode("utf-8")).hexdigest(), "actions": ACTIONS}


class Scheduler:
    """合成计划程序:记下每一次调用,删掉之后 _presence 才报 absent。"""

    def __init__(self, names=(NAME, SPACED)):
        self.tasks = {n: described(n) for n in names}
        self.calls = []

    def describe(self, name):
        self.calls.append(("describe", name))
        return self.tasks.get(name)

    def unregister(self, name, sha):
        self.calls.append(("unregister", name, sha))
        if self.tasks.get(name, {}).get("sha256") != sha:
            return False, "definition_changed"
        del self.tasks[name]
        return True, ""

    def presence(self, name):
        self.calls.append(("presence", name))
        return "present" if name in self.tasks else "absent"

    def live(self):
        return [{"name": n} for n in self.tasks]

    def irreversible(self):
        return [c for c in self.calls if c[0] == "unregister"]


@pytest.fixture
def world(tmp_path, monkeypatch):
    D._PLANS.clear()
    sched = Scheduler()
    monkeypatch.setattr(D, "_describe", sched.describe)
    monkeypatch.setattr(D, "_unregister", sched.unregister)
    monkeypatch.setattr(D, "_presence", sched.presence)
    # 没配控制器、并且显式允许没有控制器时删除:见 test_an_unconfigured_authority_refuses_unless_explicitly_allowed。
    monkeypatch.setattr(D, "_authority", lambda env: None)
    archive = tmp_path / "deleted-archive"
    archive.mkdir()
    categories = tmp_path / "categories.json"
    write_categories(categories, default_categories())
    env = {D.ARCHIVE_ENV: str(archive)}
    return SimpleNamespace(sched=sched, archive=archive, categories=categories, env=env, tmp=tmp_path)


def default_categories(verdict="remove"):
    return {"categories": [
        {"name": "同步", "tasks": [NAME, "AcmeOther"],
         "taskDesc": {NAME: "同步：合成说明", "AcmeOther": "其他"},
         "taskInfo": {NAME: {"title": "Acme 同步", "verdict": verdict}, "AcmeOther": {"title": "其他"}}},
        {"name": "备份", "tasks": [SPACED], "taskInfo": {SPACED: {"title": "Acme 备份", "verdict": "keep"}}}]}


def write_categories(path, doc, *, bom=False, ascii_=False, indent=2, newline="\n", tail="\n"):
    text = json.dumps(doc, ensure_ascii=ascii_, indent=indent).replace("\n", newline) + tail
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))


def plan(w, name=NAME, reason="合成清理", env=None):
    return D.plan(name, reason, live=w.sched.live, categories_path=w.categories, env=w.env if env is None else env)


def apply(w, preview, name=None, env=None):
    return D.apply(preview["token"], name or preview["name"], live=w.sched.live,
                   categories_path=w.categories, env=w.env if env is None else env)


def hook(w, body, *, name="hook.py"):
    """在 tmp 里写一个合成钩子。它把收到的请求追加到 requests.jsonl,再照 body 回答。"""
    script = w.tmp / name
    log = w.tmp / "requests.jsonl"
    header = ("import json, sys\n"
              "request = json.loads(sys.stdin.buffer.read().decode('utf-8'))\n"
              f"with open({str(log)!r}, 'a', encoding='utf-8') as fh:\n"
              "    fh.write(json.dumps(request, ensure_ascii=False) + '\\n')\n")
    script.write_text(header + textwrap.dedent(body).strip() + "\n", encoding="utf-8")
    return script


def requests(w):
    log = w.tmp / "requests.jsonl"
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


OK_HOOK = """
reply = {"schema": 1, "ok": True, "steps": [{"id": "health", "title": "移出健康清单", "target": "task-health.json",
         "status": "planned" if request["mode"] == "plan" else "ok", "detail": "合成"}], "blocking": [], "notes": []}
sys.stdout.buffer.write(json.dumps(reply, ensure_ascii=False).encode("utf-8"))
"""


# ---------------------------------------------------------------- 名字与在册


def test_a_name_with_spaces_passes_the_controller_gate_that_safe_name_would_refuse(world):
    # 负对照:旧那道 maint.SAFE_NAME 会把它挡掉,所以这条用例证明的确实是换了闸门。
    assert not maint.SAFE_NAME.match(SPACED)
    preview = plan(world, SPACED)
    assert preview["ok"] and preview["name"] == SPACED and preview["token"]


@pytest.mark.parametrize("bad", ["", "   ", "Acme\\Sync", "Acme/Sync", "Acme*", "Acme?", "Acme[1]", "Acme\x07", None, 7])
def test_bad_names_are_refused_before_any_scheduler_read(world, bad):
    with pytest.raises(maint.Refused) as error:
        plan(world, bad)
    assert error.value.code == "bad_name"
    assert world.sched.calls == []


def test_a_name_that_is_not_live_is_refused_without_reading_its_definition(world):
    with pytest.raises(maint.Refused) as error:
        plan(world, "AcmeGhost")
    assert error.value.code == "not_live"
    assert world.sched.calls == []


def test_a_reason_is_required(world):
    for empty in ("", "   ", None):
        with pytest.raises(maint.Refused) as error:
            plan(world, reason=empty)
        assert error.value.code == "no_reason"


def test_a_running_task_is_refused_with_a_stop_first_message(world):
    world.sched.tasks[NAME]["state"] = "Running"
    with pytest.raises(maint.Refused) as error:
        plan(world)
    assert error.value.code == "task_running" and "先停止" in str(error.value)


def test_apply_refuses_in_read_only_mode_before_touching_anything(world):
    preview = plan(world)
    with pytest.raises(maint.Refused) as error:
        apply(world, preview, env=dict(world.env, TASK_CONSOLE_READ_ONLY="1"))
    assert error.value.code == "read_only"
    assert world.sched.irreversible() == []


# ---------------------------------------------------------------- 令牌


def test_a_token_is_single_use(world):
    preview = plan(world)
    assert apply(world, preview)["status"] == "partial"  # 没配钩子:见下面「钩子没配」那条
    with pytest.raises(maint.Refused) as error:
        apply(world, preview)
    assert error.value.code == "stale_plan"
    assert len(world.sched.irreversible()) == 1


def test_an_expired_token_is_refused(world, monkeypatch):
    preview = plan(world)
    D._PLANS[preview["token"]]["expires"] = 0
    with pytest.raises(maint.Refused) as error:
        apply(world, preview)
    assert error.value.code == "stale_plan"
    assert world.sched.irreversible() == []


def test_a_token_for_one_name_cannot_delete_another_and_is_spent(world):
    preview = plan(world)
    with pytest.raises(maint.Refused) as error:
        apply(world, preview, name=SPACED)
    assert error.value.code == "name_mismatch"
    with pytest.raises(maint.Refused) as again:
        apply(world, preview)
    assert again.value.code == "stale_plan"
    assert world.sched.irreversible() == []


@pytest.mark.parametrize("change", ["definition", "categories", "hook"])
def test_anything_the_plan_depended_on_changing_refuses_with_plan_changed(world, change):
    if change == "hook":
        world.env[D.HOOK_ENV] = str(hook(world, OK_HOOK))
    preview = plan(world)
    if change == "definition":
        world.sched.tasks[NAME] = described(xml=XML.replace("sync.exe", "sync2.exe"))
    elif change == "categories":
        # 换一种仍能原样重写的写法:字节变了而文件照样可改,拒绝只能来自指纹。
        # (多一个空格会让它改不了,那是另一条拦阻,见 unrewritable_categories。)
        write_categories(world.categories, default_categories(), indent=4)
    else:
        hook(world, OK_HOOK.replace("合成", "已变"))
    before = world.categories.read_bytes()
    with pytest.raises(maint.Refused) as error:
        apply(world, preview)
    assert error.value.code == "plan_changed"
    assert world.sched.irreversible() == []
    assert world.categories.read_bytes() == before
    assert list(world.archive.iterdir()) == []


# ---------------------------------------------------------------- 受管理的任务


class FakeRuntime:
    def __init__(self, root, state=None):
        self.authority = SimpleNamespace(config=SimpleNamespace(private_root=root, state_root=state or root.parent / "state"))
        self.journals = {}
        self.journal = SimpleNamespace(load=lambda tx: self.journals[tx])

    def load(self):
        return {"bundle": "synthetic"}


@pytest.fixture
def managed(world, monkeypatch):
    root = world.tmp / "private"
    runtime = FakeRuntime(root)
    seen = {"plan": [], "action": []}
    monkeypatch.setattr(D, "_authority", lambda env: runtime)
    monkeypatch.setattr(D.task_control.registration, "_compile",
                        lambda bundle: {"task_specs": [{"name": NAME, "task_id": "acme/sync"}]})

    def retire_plan(name, reason, runtime=None):
        seen["plan"].append((name, reason))
        return {"registration_plan": {"plan_revision": "sha256:plan-1", "changes": {"files": [
            {"path": str(root / "projections" / "task-health.json"), "operation": "publish"}]}},
            "linked_work_items": [], "linked_work_items_status": "available"}

    def action(name, verb, reason="", runtime=None):
        seen["action"].append((name, verb, reason))
        del world.sched.tasks[name]
        runtime.journals["tx-1"] = {"status": "committed", "steps": [
            {"kind": "scheduler", "key": name},
            {"kind": "files", "key": str(root / "projections" / "task-health.json")},
            {"kind": "files", "key": str(root / "tombstones.json")}]}
        return {"ok": True, "status": "committed", "transaction_id": "tx-1"}

    monkeypatch.setattr(D.task_control, "retire_plan", retire_plan)
    monkeypatch.setattr(D.task_control, "action", action)
    return SimpleNamespace(runtime=runtime, seen=seen, root=root)


def test_a_managed_task_goes_through_the_authority_with_the_real_reason(world, managed):
    world.env[D.HOOK_ENV] = str(hook(world, OK_HOOK))
    preview = plan(world, reason="AcmeSync 已被新流程取代")
    assert preview["managed"] and preview["authority"]["planRevision"] == "sha256:plan-1"
    assert [s["id"] for s in preview["steps"]][0] == "authority.retire"
    result = apply(world, preview)
    # 预览和执行都带真实原因,不是服务器那条预览里的占位 'x'。
    assert managed.seen["plan"] == [(NAME, "AcmeSync 已被新流程取代")] * 2
    assert managed.seen["action"] == [(NAME, "retire", "AcmeSync 已被新流程取代")]
    assert result["status"] == "ok" and result["ok"] is True
    # 投影之外还有这次事务在状态根下的三份记录:钩子只提交这里列出的文件,漏了它们,
    # 下一次删除就会被「上一个事务没提交」拦在预览上。
    files = ["projections/task-health.json", "tombstones.json",
             "../state/current.json", "../state/journals/tx-1.json", "../state/receipts/tx-1.json"]
    assert result["authority"] == {"transactionId": "tx-1", "status": "committed", "files": files}
    sent = requests(world)[-1]
    assert sent["mode"] == "apply" and sent["task"]["managed"] is True
    assert sent["authority"] == {"transaction_id": "tx-1", "files": files}
    assert sent["task"]["actions"] == ACTIONS
    # 受管理的任务不经这里导出存档、也不经这里删除:那是控制器的事。
    assert world.sched.irreversible() == [] and list(world.archive.iterdir()) == []


def test_a_managed_task_is_never_downgraded_to_the_unmanaged_path_when_the_authority_refuses(world, managed, monkeypatch):
    def refuse(name, reason, runtime=None):
        raise D.task_control.ContractError("ownership_required", "task_id")
    monkeypatch.setattr(D.task_control, "retire_plan", refuse)
    with pytest.raises(maint.Refused) as error:
        plan(world)
    assert error.value.code == "ownership_required"
    assert world.sched.irreversible() == []


def test_an_authority_that_reports_committed_while_the_task_is_still_there_is_failed(world, managed, monkeypatch):
    monkeypatch.setattr(D.task_control, "action",
                        lambda name, verb, reason="", runtime=None: {"ok": True, "status": "committed", "transaction_id": "tx-1"})
    result = apply(world, plan(world))
    assert result["status"] == "failed" and result["ok"] is False
    assert "仍有这个任务" in result["steps"][0]["detail"]
    assert [s["status"] for s in result["steps"][1:]] == ["skipped", "skipped"]


def test_an_unreadable_journal_is_its_own_failed_step_and_makes_the_result_partial(world, managed):
    world.env[D.HOOK_ENV] = str(hook(world, OK_HOOK))
    preview = plan(world)
    managed.runtime.journal = SimpleNamespace(load=lambda tx: (_ for _ in ()).throw(OSError("gone")))
    result = apply(world, preview)
    steps = {s["id"]: s["status"] for s in result["steps"]}
    assert steps["authority.retire"] == "ok" and steps["authority.readback"] == "failed"
    assert result["status"] == "partial" and result["ok"] is False


def _deleting_action(world, managed, reply=None, error=None):
    """合成控制器:先真把任务从合成计划程序里删掉、写好已提交的事务日志,再按给定的样子回报。"""
    def action(name, verb, reason="", runtime=None):
        del world.sched.tasks[name]
        managed.runtime.journals["tx-1"] = {"status": (reply or {}).get("journal", "committed"), "steps": [
            {"kind": "files", "key": str(managed.root / "projections" / "task-health.json")}]}
        if error is not None:
            raise error
        return {key: value for key, value in reply.items() if key != "journal"}
    return action


def test_a_committed_transaction_whose_cleanup_failed_is_a_deletion_and_the_cleanup_still_runs(world, managed, monkeypatch):
    # registration._result: ok = committed 且 cleaned。清理暂存失败时 ok 是 false,但任务已经删了。
    world.env[D.HOOK_ENV] = str(hook(world, OK_HOOK))
    preview = plan(world)
    monkeypatch.setattr(D.task_control, "action", _deleting_action(world, managed, {
        "ok": False, "status": "committed", "cleanup_error": "cleanup_required", "transaction_id": "tx-1"}))
    result = apply(world, preview)
    steps = {s["id"]: s for s in result["steps"]}
    assert result["status"] == "partial" and result["ok"] is False
    assert steps["authority.retire"]["status"] == "ok"
    assert steps["authority.cleanup"]["status"] == "failed" and "recover" in steps["authority.cleanup"]["detail"]
    assert steps["categories.edit"]["status"] == "ok" and steps["followup.apply"]["status"] == "ok"
    assert NAME not in world.categories.read_text(encoding="utf-8")
    assert [r["mode"] for r in requests(world)] == ["plan", "plan", "apply"]
    assert "没有删除" not in result["message"]


@pytest.mark.parametrize("shape", ["committing", "exception"])
def test_an_authority_failure_after_the_task_is_gone_is_partial_never_not_deleted(world, managed, monkeypatch, shape):
    # 停在 committing(completion_required),或者 300 秒期限在 COM 删除之后才到(control_cancelled)。
    world.env[D.HOOK_ENV] = str(hook(world, OK_HOOK))
    preview = plan(world)
    if shape == "committing":
        action = _deleting_action(world, managed, {"ok": False, "status": "committing", "journal": "committing",
                                                   "failure": {"code": "completion_required"}, "transaction_id": "tx-1"})
    else:
        action = _deleting_action(world, managed, error=D.task_control.ContractError("control_cancelled", "runtime"))
    monkeypatch.setattr(D.task_control, "action", action)
    result = apply(world, preview)
    steps = {s["id"]: s for s in result["steps"]}
    assert result["status"] == "partial" and result["ok"] is False
    assert steps["authority.retire"]["status"] == "failed"
    assert "已没有这个任务" in steps["authority.retire"]["detail"]
    assert ("completion_required" if shape == "committing" else "control_cancelled") in steps["authority.retire"]["detail"]
    assert steps["categories.edit"]["status"] == "ok"
    assert requests(world)[-1]["mode"] == "apply"


def test_an_authority_failure_with_the_task_still_there_is_failed_and_says_it_read_back(world, managed, monkeypatch):
    world.env[D.HOOK_ENV] = str(hook(world, OK_HOOK))
    preview = plan(world)
    monkeypatch.setattr(D.task_control, "action", lambda name, verb, reason="", runtime=None: {
        "ok": False, "status": "rolled_back", "failure": {"code": "scheduler_changed"}, "transaction_id": "tx-1"})
    result = apply(world, preview)
    assert result["status"] == "failed" and "读回确认计划程序里仍有这个任务" in result["message"]
    assert [r["mode"] for r in requests(world)] == ["plan", "plan"]


def test_when_presence_cannot_be_read_after_the_authority_the_outcome_is_unknown_not_failed(world, managed, monkeypatch):
    preview = plan(world)
    monkeypatch.setattr(D.task_control, "action", _deleting_action(
        world, managed, error=D.task_control.ContractError("control_cancelled", "runtime")))

    def unreadable(name):
        raise maint.Refused("无法确认任务是否还在: enumerated_zero", "state_unreadable")
    monkeypatch.setattr(D, "_presence", unreadable)
    result = apply(world, preview)
    assert result["status"] == "unknown" and result["ok"] is False
    assert result["message"].startswith("无法确认是否已删除") and "没有删除" not in result["message"]
    assert any("刷新页面核对" in r for r in result["remaining"])


def test_an_unconfigured_authority_refuses_unless_explicitly_allowed(world, monkeypatch):
    seen = []

    def unconfigured(**kw):
        seen.append(kw)
        raise D.task_control.ContractError("runtime_not_configured", "runtime")
    monkeypatch.setattr(D.task_control, "load_runtime", unconfigured)
    monkeypatch.setattr(D, "_authority", REAL_AUTHORITY)
    # 一个少了控制器配置的控制台进程,不能把控制器拥有的任务当成「不受管理」直接注销。
    with pytest.raises(maint.Refused) as error:
        plan(world)
    assert error.value.code == "authority_not_configured"
    assert seen[-1]["environ"] is world.env and world.sched.calls[-1][0] == "describe"
    allowed = dict(world.env, **{D.WITHOUT_AUTHORITY_ENV: "1"})
    preview = plan(world, env=allowed)
    assert preview["managed"] is False and D.WITHOUT_AUTHORITY in preview["notes"]

    def broken(**kw):
        raise D.task_control.ContractError("incomplete_runtime_configuration", "runtime")
    monkeypatch.setattr(D.task_control, "load_runtime", broken)
    with pytest.raises(maint.Refused) as error:
        D._authority(allowed)
    assert error.value.code == "incomplete_runtime_configuration"
    assert world.sched.irreversible() == []


def test_the_files_sent_to_the_hook_resolve_to_the_transactions_real_state_records(world, monkeypatch):
    """和私有钩子的契约:authority.files 相对私有根,钩子按 normpath(私有根 / 路径) 解析。
    用真的 JournalStore 写一份事务日志,证明交出去的路径落在它真正写下的文件上,而不是一份手喂的清单。"""
    from task_console.runtime_storage import JournalStore
    private = world.tmp / "data" / "declarations" / "bootstrap"
    state = world.tmp / "data" / "task-console" / "registration"
    for directory in (private / "projections", state / "journals", state / "receipts"):
        directory.mkdir(parents=True)
    tx = "0123456789abcdef0123456789abcdef"
    runtime = FakeRuntime(private, state)
    runtime.journal = JournalStore(state / "journals")
    runtime.journal.create(tx, {"schemaVersion": 1, "transaction_id": tx, "status": "committed",
                                "locks": ["authority"], "steps": [
                                    {"kind": "files", "key": str(private / "projections" / "task-health.json"),
                                     "token": tx + ":files:0", "phase": "done"}]})
    (state / "current.json").write_text("{}", encoding="utf-8")
    (state / "receipts" / f"{tx}.json").write_text("{}", encoding="utf-8")
    world.sched.tasks.pop(NAME)  # 控制器已经删掉了它
    monkeypatch.setattr(D.task_control, "action", lambda name, verb, reason="", runtime=None: {
        "ok": True, "status": "committed", "transaction_id": tx})
    steps, authority, presence = D._scheduler_managed(NAME, "合成", runtime)
    assert presence == "absent" and {s["id"]: s["status"] for s in steps}["authority.readback"] == "ok"
    resolved = {Path(os.path.normpath(private / f)) for f in authority["files"]}
    assert resolved == {private / "projections" / "task-health.json", state / "current.json",
                        state / "journals" / f"{tx}.json", state / "receipts" / f"{tx}.json"}
    assert all(not os.path.isabs(f) and "\\" not in f for f in authority["files"])
    assert "../../task-console/registration/journals/" + tx + ".json" in authority["files"]


# ---------------------------------------------------------------- 不受管理的任务


def test_an_unmanaged_delete_without_the_archive_binding_is_blocked_and_gets_no_token(world):
    env = {}
    preview = plan(world, env=env)
    assert preview["applicable"] is False and preview["token"] is None
    assert any("TASK_CONSOLE_DELETED_ARCHIVE" in reason for reason in preview["blocking"])
    assert preview["steps"][0]["status"] == "blocked"


def test_an_archive_inside_a_git_worktree_is_refused(world):
    repo = world.tmp / "acme-repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "archive").mkdir()
    preview = plan(world, env={D.ARCHIVE_ENV: str(repo / "archive")})
    assert preview["token"] is None and any("git" in reason for reason in preview["blocking"])


def test_an_unmanaged_delete_exports_first_then_unregisters_by_data_and_verifies_absence(world, monkeypatch):
    order = []
    real = world.sched.unregister

    def unregister(name, sha):
        # 删除那一刻,存档必须已经落盘并且读得回来。
        order.append(sorted(p.suffix for p in world.archive.iterdir()))
        return real(name, sha)
    monkeypatch.setattr(D, "_unregister", unregister)
    preview = plan(world, SPACED, reason="合成：被新任务取代")
    result = apply(world, preview)
    assert order == [[".json", ".xml"]]
    assert ("unregister", SPACED, described(SPACED)["sha256"]) in world.sched.calls
    assert world.sched.calls[-1] == ("presence", SPACED)
    xml_file = next(world.archive.glob("*.xml"))
    assert xml_file.name.startswith("Acme_Backup_Daily-")
    assert xml_file.read_bytes().decode("utf-16") == XML
    receipt = json.loads(next(world.archive.glob("*.json")).read_text(encoding="utf-8"))
    assert receipt["reason"] == "合成：被新任务取代" and receipt["actions"] == ACTIONS and receipt["name"] == SPACED
    assert {s["id"]: s["status"] for s in result["steps"]}["scheduler.unregister"] == "ok"


def test_a_failed_unregister_is_failed_and_nothing_after_it_runs(world, monkeypatch):
    world.env[D.HOOK_ENV] = str(hook(world, OK_HOOK))
    preview = plan(world)
    before = world.categories.read_bytes()
    monkeypatch.setattr(D, "_unregister", lambda name, sha: (False, "Access is denied."))
    result = apply(world, preview)
    assert result["status"] == "failed" and result["ok"] is False
    assert world.categories.read_bytes() == before
    assert [r["mode"] for r in requests(world)] == ["plan", "plan"]  # 预览一次、执行前复核一次,没有 apply
    assert "读回确认任务仍在" in result["message"]


def test_an_unregister_that_timed_out_after_removing_the_task_still_runs_the_cleanup(world, monkeypatch):
    world.env[D.HOOK_ENV] = str(hook(world, OK_HOOK))
    preview = plan(world)

    def timed_out(name, sha):
        del world.sched.tasks[name]  # Unregister-ScheduledTask 已经生效,只是回报没赶上
        return False, "TimeoutExpired"
    monkeypatch.setattr(D, "_unregister", timed_out)
    result = apply(world, preview)
    steps = {s["id"]: s for s in result["steps"]}
    assert result["status"] == "partial" and "没有删除" not in result["message"]
    assert steps["scheduler.unregister"]["status"] == "failed" and "已没有这个任务" in steps["scheduler.unregister"]["detail"]
    assert steps["categories.edit"]["status"] == "ok" and requests(world)[-1]["mode"] == "apply"


def test_an_unregister_whose_readback_is_unreadable_is_unknown(world, monkeypatch):
    preview = plan(world)
    monkeypatch.setattr(D, "_unregister", lambda name, sha: (False, "TimeoutExpired"))

    def unreadable(name):
        raise maint.Refused("无法确认任务是否还在: rc=1", "state_unreadable")
    monkeypatch.setattr(D, "_presence", unreadable)
    result = apply(world, preview)
    assert result["status"] == "unknown" and "任务仍在" not in result["message"]


def test_the_powershell_bridge_passes_the_name_as_data(monkeypatch):
    seen = []

    def run(argv, **kw):
        seen.append((argv, kw))
        return SimpleNamespace(returncode=0, stdout=b"unregistered\n", stderr=b"")
    monkeypatch.setattr(D.subprocess, "run", run)
    hostile = "Acme'; Remove-Item -Recurse C:\\ ; '"
    D._unregister(hostile, "ab" * 32)
    monkeypatch.setattr(D.subprocess, "run", lambda argv, **kw: seen.append((argv, kw)) or
                        SimpleNamespace(returncode=0, stdout=b"absent", stderr=b""))
    assert D._presence(hostile) == "absent"
    for argv, kw in seen:
        assert all(hostile not in part for part in argv)
        assert kw["env"]["TC_NAME"] == hostile
    assert seen[0][1]["env"]["TC_SHA256"] == "ab" * 32


def test_presence_that_cannot_be_read_raises_instead_of_guessing(monkeypatch):
    monkeypatch.setattr(D.subprocess, "run", lambda argv, **kw: SimpleNamespace(returncode=1, stdout=b"", stderr=b""))
    with pytest.raises(maint.Refused) as error:
        D._presence(NAME)
    assert error.value.code == "state_unreadable"


def test_describe_refuses_xml_that_did_not_survive_the_transport(monkeypatch):
    reply = {"present": True, "name": NAME, "state": "Ready", "xml": XML, "sha256": "0" * 64, "actions": []}
    monkeypatch.setattr(D.subprocess, "run", lambda argv, **kw: SimpleNamespace(
        returncode=0, stdout=json.dumps(reply).encode("utf-8"), stderr=b""))
    with pytest.raises(maint.Refused) as error:
        D._describe(NAME)
    assert error.value.code == "state_unreadable"
    reply["sha256"] = hashlib.sha256(XML.encode("utf-8")).hexdigest()
    reply["actions"] = {"execute": "C:\\Acme\\a.exe", "arguments": None, "workingDirectory": None}
    monkeypatch.setattr(D.subprocess, "run", lambda argv, **kw: SimpleNamespace(
        returncode=0, stdout=json.dumps(reply).encode("utf-8"), stderr=b""))
    assert D._describe(NAME)["actions"] == [{"execute": "C:\\Acme\\a.exe", "arguments": None, "workingDirectory": None}]


def _code_strings(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
            and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
            and isinstance(n.value, str) and id(n) not in docs]


def test_every_task_cmdlet_in_the_delete_bridge_pins_the_root_path():
    cmdlets = ("Get-ScheduledTask", "Export-ScheduledTask", "Unregister-ScheduledTask")
    checked, bad = 0, []
    for text in _code_strings(D.__file__):
        for cmd in cmdlets:
            start = 0
            while (i := text.find(cmd, start)) >= 0:
                start = i + len(cmd)
                segment = text[i:].split(";", 1)[0].split("|", 1)[0]
                checked += 1
                if "-TaskPath '\\'" not in segment:
                    bad.append(segment)
    assert checked >= 6, f"只扫到 {checked} 处,扫描器大概没在工作"
    assert not bad, bad


# ---------------------------------------------------------------- 分类配置


FORMATS = [dict(bom=False, ascii_=False, indent=2, newline="\n", tail="\n"),
           dict(bom=True, ascii_=False, indent=2, newline="\n", tail="\n"),
           dict(bom=True, ascii_=True, indent=4, newline="\r\n", tail="\r\n"),
           dict(bom=False, ascii_=True, indent=2, newline="\n", tail=""),
           dict(bom=False, ascii_=False, indent=None, newline="\n", tail="\n")]


@pytest.mark.parametrize("fmt", FORMATS)
def test_the_category_edit_preserves_the_files_exact_format(world, fmt):
    write_categories(world.categories, default_categories(), **fmt)
    digest = hashlib.sha256(world.categories.read_bytes()).hexdigest()
    step = D._edit_categories(world.categories, NAME, digest)
    assert step["status"] == "ok", step
    expected = default_categories()
    cat = expected["categories"][0]
    cat["tasks"].remove(NAME)
    del cat["taskDesc"][NAME]
    del cat["taskInfo"][NAME]
    reference = world.tmp / "reference.json"
    write_categories(reference, expected, **fmt)
    assert world.categories.read_bytes() == reference.read_bytes()


def unrewritable_categories(w):
    raw = json.dumps(default_categories(), ensure_ascii=False, indent=2).replace('"tasks": [', '"tasks":  [', 1)
    w.categories.write_bytes(raw.encode("utf-8"))
    return w.categories.read_bytes()


def test_a_category_file_that_does_not_round_trip_is_never_rewritten_and_gets_no_token(world):
    before = unrewritable_categories(world)
    preview = plan(world)
    step = next(s for s in preview["steps"] if s["id"] == "categories.edit")
    assert step["status"] == "blocked"
    # 列着它却改不了是拦阻原因,不是一条备注:发了令牌,计划程序那一步就会在一份改不了的配置面前先做完。
    assert any("原样重写" in b and "重新预览" in b for b in preview["blocking"])
    assert preview["applicable"] is False and preview["token"] is None
    assert world.categories.read_bytes() == before
    assert world.sched.irreversible() == []


# 和私有钩子同一套规矩的合成钩子:plan 模式不看 categories.edited,只看实时分类配置里还列不列着它;
# apply 模式里还列着就整次拦下(退出 2)。这正是两边对不上时会把一次删除留成半成品的那个组合。
CONTRACT_HOOK = """
doc = json.loads(open(request["categories"]["path"], encoding="utf-8").read())
listed = any(isinstance(t, str) and t.casefold() == request["task"]["name"].casefold()
             for c in doc["categories"] for t in c.get("tasks", []))
if request["mode"] == "apply" and listed:
    reply = {"schema": 1, "ok": False, "steps": [], "notes": [],
             "blocking": ["实时分类表里仍列着这个任务（控制台没有改掉它），整次清理一处都没改"]}
    sys.stdout.buffer.write(json.dumps(reply, ensure_ascii=False).encode("utf-8")); sys.exit(2)
status = ("planned" if listed else "skipped") if request["mode"] == "plan" else "ok"
reply = {"schema": 1, "ok": True, "blocking": [], "notes": [],
         "steps": [{"id": "categories.mirror", "title": "同步分类表镜像", "target": "mirror", "status": status, "detail": "合成"}]}
sys.stdout.buffer.write(json.dumps(reply, ensure_ascii=False).encode("utf-8"))
"""


def test_an_unrewritable_map_is_refused_before_the_hook_could_strand_the_cleanup(world):
    world.env[D.HOOK_ENV] = str(hook(world, CONTRACT_HOOK))
    unrewritable_categories(world)
    preview = plan(world)
    # 钩子的预览照样说「将同步」:它不看 edited。所以只能由控制台在发令牌之前拦下。
    assert preview["followup"]["state"] == "ok"
    assert preview["token"] is None and any("原样重写" in b for b in preview["blocking"])
    assert [r["mode"] for r in requests(world)] == ["plan"]
    assert world.sched.irreversible() == []
    # 照拦阻原因说的做:手动删掉条目(写法随意),重新预览就能删,钩子的 apply 也不再拦。
    doc = default_categories()
    cat = doc["categories"][0]
    cat["tasks"].remove(NAME)
    del cat["taskDesc"][NAME], cat["taskInfo"][NAME]
    raw = json.dumps(doc, ensure_ascii=False, indent=2).replace('"tasks": [', '"tasks":  [', 1)
    world.categories.write_bytes(raw.encode("utf-8"))
    result = apply(world, plan(world))
    assert result["status"] == "ok" and result["followup"]["state"] == "ok"
    assert requests(world)[-1]["mode"] == "apply"


def test_a_task_missing_from_the_category_map_needs_no_edit(world):
    world.categories.write_text(json.dumps({"categories": [{"name": "其他", "tasks": ["AcmeOther"]}]}), encoding="utf-8")
    world.env[D.HOOK_ENV] = str(hook(world, OK_HOOK))
    preview = plan(world, SPACED)
    step = next(s for s in preview["steps"] if s["id"] == "categories.edit")
    assert step["status"] == "skipped" and not any("分类配置" in n for n in preview["notes"])
    result = apply(world, preview)
    assert {s["id"]: s["status"] for s in result["steps"]}["categories.edit"] == "ok"
    assert result["status"] == "ok"


@pytest.mark.parametrize("kind,status", [("trailing_comma", "blocked"), ("not_utf8", "blocked"),
                                         ("no_array", "blocked"), ("missing", "skipped")])
def test_a_category_map_that_was_not_read_is_never_reported_as_cleaned(world, kind, status):
    # 一份带尾逗号却仍然列着 AcmeSync 的配置:旧代码把「读不了」记成 ok,整次删除报「全部清理完了」。
    raw = json.dumps(default_categories(), ensure_ascii=False, indent=2)
    data = {"trailing_comma": (raw[:raw.rindex("]") + 1] + ",\n}").encode("utf-8"),
            "not_utf8": raw.encode("utf-8").replace("合成说明".encode("utf-8"), b"\xff\xfe", 1),
            "no_array": json.dumps({"groups": [{"tasks": [NAME]}]}).encode("utf-8")}.get(kind)
    if data is None:
        world.categories.unlink()
    else:
        assert NAME.encode("utf-8") in data  # 文件里确实还列着这个任务
        world.categories.write_bytes(data)
    world.env[D.HOOK_ENV] = str(hook(world, OK_HOOK))
    preview = plan(world)
    step = next(s for s in preview["steps"] if s["id"] == "categories.edit")
    assert step["status"] == status and any("没有核对分类配置" in n for n in preview["notes"])
    result = apply(world, preview)
    assert {s["id"]: s["status"] for s in result["steps"]}["categories.edit"] == status
    assert result["status"] == "partial" and result["ok"] is False
    assert any("没有核对分类配置" in n for n in result["notes"])
    assert "都已清理" not in result["message"]
    if data is not None:
        assert world.categories.read_bytes() == data
    assert requests(world)[-1]["categories"]["edited"] is False


def test_a_case_variant_entry_in_the_category_map_is_removed_like_the_hook_would(world):
    doc = default_categories(verdict="fix")
    cat = doc["categories"][0]
    cat["tasks"] = ["acmesync", "AcmeOther"]
    cat["taskDesc"] = {"ACMESYNC": "同步：合成说明", "AcmeOther": "其他"}
    cat["taskInfo"] = {"AcmeSYNC": {"title": "Acme 同步", "verdict": "fix"}, "AcmeOther": {"title": "其他"}}
    write_categories(world.categories, doc)
    world.env[D.HOOK_ENV] = str(hook(world, OK_HOOK))
    preview = plan(world)
    step = next(s for s in preview["steps"] if s["id"] == "categories.edit")
    assert step["status"] == "planned" and preview["categories"]["verdict"] == "fix"
    result = apply(world, preview)
    assert result["status"] == "ok"
    assert "acmesync" not in world.categories.read_text(encoding="utf-8").casefold()
    assert requests(world)[-1]["categories"]["edited"] is True


# ---------------------------------------------------------------- 后续钩子:五种结果


REQUEST = {"schema": 1, "mode": "plan", "task": {"name": NAME, "managed": False, "actions": ACTIONS},
           "reason": "合成", "categories": {"path": None, "edited": False}, "authority": None}


def _reply(ok, blocking=(), status="planned"):
    return {"schema": 1, "ok": ok, "steps": [{"id": "s", "title": "合成", "target": "t", "status": status, "detail": "d"}],
            "blocking": list(blocking), "notes": ["请手动改一处合成说明"]}


HOOKS = {
    "ok": f"sys.stdout.buffer.write(json.dumps({_reply(True)!r}).encode('utf-8'))",
    "blocked": f"sys.stdout.buffer.write(json.dumps({_reply(False, ['备份仓有未提交改动'])!r}).encode('utf-8')); sys.exit(2)",
    "failed": f"sys.stdout.buffer.write(json.dumps({_reply(False, status='failed')!r}).encode('utf-8')); sys.exit(1)",
    "unreadable": "sys.stdout.write('not json')",
}


def test_not_configured_blocked_failed_unreadable_and_ok_are_five_distinct_outcomes(world):
    states = {"not_configured": D._run_hook("plan", REQUEST, {})["state"]}
    for expected, body in HOOKS.items():
        script = hook(world, body, name=f"hook_{expected}.py")
        states[expected] = D._run_hook("plan", REQUEST, {D.HOOK_ENV: str(script)})["state"]
    assert states == {"not_configured": "not_configured", "ok": "ok", "blocked": "blocked",
                      "failed": "failed", "unreadable": "unreadable"}
    assert len(set(states.values())) == 5


@pytest.mark.parametrize("body", [
    "sys.stdout.buffer.write(json.dumps(dict(" + repr(_reply(True)) + ", schema=2)).encode('utf-8'))",
    "sys.stdout.buffer.write(json.dumps(" + repr(_reply(True)) + ").encode('utf-8')); sys.exit(3)",
    "sys.stdout.buffer.write(json.dumps(" + repr(_reply(False)) + ").encode('utf-8'))",
    "sys.stdout.buffer.write(json.dumps(" + repr(_reply(True, status='done')) + ").encode('utf-8'))",
    "sys.stdout.buffer.write(b'{' + b' ' * (1 << 20) + b'}')",
    "sys.stdout.buffer.write(json.dumps(" + repr(_reply(False)) + ").encode('utf-8')); sys.exit(2)",
])
def test_anything_off_protocol_is_unreadable_never_success(world, body):
    script = hook(world, body)
    assert D._run_hook("apply", REQUEST, {D.HOOK_ENV: str(script)})["state"] == "unreadable"


def test_a_hook_that_times_out_is_unreadable(world, monkeypatch):
    monkeypatch.setattr(D, "HOOK_PLAN_TIMEOUT", 1)
    script = hook(world, "import time; time.sleep(5)")
    assert D._run_hook("plan", REQUEST, {D.HOOK_ENV: str(script)})["state"] == "unreadable"


def test_the_hook_runs_isolated_without_bytecode_from_its_own_directory(world, monkeypatch):
    seen = []
    monkeypatch.setattr(D.subprocess, "run", lambda argv, **kw: seen.append((argv, kw)) or SimpleNamespace(
        returncode=0, stdout=json.dumps(_reply(True)).encode("utf-8"), stderr=b""))
    script = world.tmp / "acme_hook.py"
    script.write_text("", encoding="utf-8")
    D._run_hook("plan", REQUEST, {D.HOOK_ENV: str(script)})
    argv, kw = seen[0]
    assert argv == [sys.executable, "-B", "-I", str(script)]
    assert kw["cwd"] == str(world.tmp) and kw["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert json.loads(kw["input"].decode("utf-8")) == REQUEST


@pytest.mark.parametrize("state,body", [("not_configured", None), ("failed", HOOKS["failed"]),
                                        ("unreadable", HOOKS["unreadable"])])
def test_a_scheduler_delete_whose_follow_up_did_not_finish_is_partial_never_ok(world, monkeypatch, state, body):
    if body is not None:
        world.env[D.HOOK_ENV] = str(hook(world, HOOKS["ok"]))
    preview = plan(world)
    if body is not None:
        # 预览时钩子是好的;执行时 apply 那一下才失败。plan 模式的回答保持不变,指纹才对得上。
        hook(world, f"""
if request["mode"] == "plan":
    {HOOKS['ok']}
else:
    {body}
""")
    result = apply(world, preview)
    assert result["followup"]["state"] == state
    assert result["status"] == "partial" and result["ok"] is False
    assert {s["id"]: s["status"] for s in result["steps"]}["scheduler.unregister"] == "ok"
    assert result["remaining"]
    if state == "not_configured":
        assert any("未配置删除后续钩子" in r for r in result["remaining"])
        assert any("TASK_CONSOLE_DELETE_FOLLOWUP" in n for n in preview["notes"])


def test_ok_only_when_every_step_and_the_hook_are_ok(world):
    world.env[D.HOOK_ENV] = str(hook(world, OK_HOOK))
    result = apply(world, plan(world))
    assert result["status"] == "ok" and result["ok"] is True and result["remaining"] == []
    sent = requests(world)[-1]
    assert sent["mode"] == "apply" and sent["categories"] == {"path": str(world.categories), "edited": True}
    assert sent["authority"] == {"transaction_id": None, "files": []}
    assert NAME not in json.dumps(json.loads(world.categories.read_text(encoding="utf-8")))


def test_a_blocking_hook_at_plan_time_yields_no_token(world):
    world.env[D.HOOK_ENV] = str(hook(world, HOOKS["blocked"]))
    preview = plan(world)
    assert preview["token"] is None and "备份仓有未提交改动" in preview["blocking"]


def test_a_preflight_block_at_apply_time_prevents_every_irreversible_step(world):
    flag = world.tmp / "block-now"
    world.env[D.HOOK_ENV] = str(hook(world, f"""
import os
if os.path.exists({str(flag)!r}):
    {HOOKS['blocked']}
{HOOKS['ok']}
"""))
    preview = plan(world)
    assert preview["token"]
    flag.touch()
    before = world.categories.read_bytes()
    with pytest.raises(maint.Refused) as error:
        apply(world, preview)
    assert error.value.code == "followup_blocked"
    assert world.sched.irreversible() == []
    assert world.categories.read_bytes() == before
    assert list(world.archive.iterdir()) == []
    assert all(r["mode"] == "plan" for r in requests(world))


def test_a_hook_that_blocks_after_the_task_is_gone_says_so_and_hands_back_a_retry(world):
    # 预览时钩子放行,执行到钩子那一下时夜间窗口到了:任务已经删了,钩子一处没清理。
    window = world.tmp / "quiet-window"
    script = hook(world, f"""
import os
if request["mode"] == "apply" and os.path.exists({str(window)!r}):
    {HOOKS['blocked']}
{OK_HOOK}
""")
    world.env[D.HOOK_ENV] = str(script)
    preview = plan(world)
    window.touch()
    result = apply(world, preview)
    assert result["status"] == "partial" and {s["id"]: s["status"] for s in result["steps"]}["scheduler.unregister"] == "ok"
    step = next(s for s in result["steps"] if s["id"] == "followup.apply")
    assert step["status"] == "blocked" and "任务已经删除" in step["detail"] and "什么都没改" not in step["detail"]
    assert "什么都没改" not in result["followup"]["message"]
    # 要处理的是钩子拦下的原因,不是被拦下后记成 skipped 的那些步骤名。
    assert "后续清理没有运行：备份仓有未提交改动" in result["remaining"]
    assert not any("合成" in r and "后续清理没有完成" in r for r in result["remaining"])
    retry = result["followupRetry"]
    sent = requests(world)[-1]
    assert sent["mode"] == "apply" and retry["request"] == sent
    assert retry["command"] == f'"{D._python_for_console()}" -B -I "{script}" < "<请求文件>"'
    assert retry["howto"] in result["remaining"] and "不要「重新删除一次」" in retry["howto"]
    # 结果里那份请求原样喂回钩子,就是一次合乎协议的重跑。
    window.unlink()
    rerun = D._run_hook("apply", retry["request"], world.env)
    assert rerun["state"] == "ok" and requests(world)[-1] == sent


def test_remaining_lists_only_follow_up_steps_that_did_not_get_done(world):
    reply = {"schema": 1, "ok": False, "blocking": [], "notes": [], "steps": [
        {"id": "push.backup", "title": "推送 acme-backup", "target": "t", "status": "failed", "detail": "合成：远端拒绝"},
        {"id": "bootstrap.migration", "title": "从第 15 步脚本的 FORBID_TASKS 移除", "target": "t", "status": "skipped",
         "detail": "不在 FORBID_TASKS 里"},
        {"id": "commit.authority", "title": "提交登记权威的事务文件", "target": "t", "status": "skipped",
         "detail": "这个任务不归登记权威管"}]}
    world.env[D.HOOK_ENV] = str(hook(world, f"""
if request["mode"] == "plan":
    {HOOKS['ok']}
else:
    sys.stdout.buffer.write(json.dumps({reply!r}, ensure_ascii=False).encode("utf-8")); sys.exit(1)
"""))
    result = apply(world, plan(world))
    assert result["status"] == "partial" and result["followup"]["state"] == "failed"
    # skipped 是钩子判定不适用,它自己也算通过;列进「还没完成」只会让人去找不存在的改动。
    assert result["remaining"] == ["后续清理没有完成：推送 acme-backup：合成：远端拒绝"]
    # 钩子做了一半时不给原样重跑:它会撞上自己留下的改动。
    assert result["followupRetry"] is None


def test_the_retry_command_never_names_the_windowless_interpreter(monkeypatch):
    monkeypatch.setattr(D.sys, "executable", "C:\\Acme\\Python\\pythonw.exe")
    assert D._python_for_console() == "C:\\Acme\\Python\\python.exe"
    monkeypatch.setattr(D.sys, "executable", "C:\\Acme\\Python\\python.exe")
    assert D._python_for_console() == "C:\\Acme\\Python\\python.exe"


# ---------------------------------------------------------------- 预警


def test_warnings_name_a_verdict_that_is_not_remove_and_an_unreadable_pipeline_list(world, monkeypatch):
    write_categories(world.categories, default_categories(verdict="fix"))
    monkeypatch.setattr(D, "_pipeline_names", lambda: None)
    codes = {w["code"] for w in plan(world)["warnings"]}
    assert {"pipeline_unchecked", "verdict_not_remove"} <= codes
    write_categories(world.categories, default_categories(verdict="remove"))
    monkeypatch.setattr(D, "_pipeline_names", lambda: set())
    # 不受管理的任务没有地方查关联待办:那一条永远在,其余都不该响。
    warnings = plan(world)["warnings"]
    assert [w["code"] for w in warnings] == ["linked_work_items_unchecked"]
    assert "不归任务控制器管" in warnings[0]["message"]


@pytest.mark.parametrize("listed", [NAME, NAME.lower()])
def test_a_pipeline_task_is_blocked_on_the_backend_too(world, monkeypatch, listed):
    # 页面只在三个标签页上都不给按钮还不够:按钮之外发来的请求也不能拿到令牌。
    monkeypatch.setattr(D, "_pipeline_names", lambda: {listed})
    preview = plan(world)
    assert preview["token"] is None and D.PIPELINE_BLOCK in preview["blocking"]


def test_linked_todos_that_were_not_read_are_unchecked_not_zero(world, managed, monkeypatch):
    statuses = {}
    for status, items in (("unavailable", None), ("error", None), ("available", []), ("available", ["acme-todo-1"])):
        def retire_plan(name, reason, runtime=None, status=status, items=items):
            return {"registration_plan": {"plan_revision": "sha256:plan-1", "changes": {"files": []}},
                    "linked_work_items": items, "linked_work_items_status": status}
        monkeypatch.setattr(D.task_control, "retire_plan", retire_plan)
        statuses[(status, len(items or []))] = {w["code"] for w in plan(world)["warnings"]}
    assert "linked_work_items_unchecked" in statuses[("unavailable", 0)]
    assert "linked_work_items_unchecked" in statuses[("error", 0)]
    assert not {"linked_work_items_unchecked", "linked_work_items"} & statuses[("available", 0)]
    assert "linked_work_items" in statuses[("available", 1)]


def test_the_pipeline_list_is_read_from_the_page_itself():
    names = D._pipeline_names()
    assert names, "从 pipelines.js 里一个名字都没读到,预警就永远不会响"
    assert all(isinstance(n, str) and n for n in names)


# ---------------------------------------------------------------- HTTP


@pytest.fixture
def http_server(monkeypatch):
    import http.client
    import threading
    from http.server import ThreadingHTTPServer
    import server

    class Handler(server.Handler):
        token = "synthetic-browser-token"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    Handler.allowed_hosts = {f"127.0.0.1:{port}"}
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def post(path, body, token="synthetic-browser-token", raw=None):
        client = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        data = raw if raw is not None else json.dumps(body).encode("utf-8")
        client.request("POST", path, data, {"X-Console-Token": token, "Content-Type": "application/json"})
        response = client.getresponse()
        result = response.status, json.loads(response.read() or b"{}")
        client.close()
        return result
    yield SimpleNamespace(post=post, server=server)
    httpd.shutdown()
    thread.join()
    httpd.server_close()


def test_http_plan_binds_the_live_enumeration_and_the_pages_own_category_file(http_server, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setenv("TASK_CONSOLE_CATEGORIES", str(tmp_path / "acme-categories.json"))
    monkeypatch.setattr(D, "plan", lambda name, reason, **kw: seen.append((name, reason, kw)) or {"ok": True})
    assert http_server.post("/api/task/delete/plan", {"name": SPACED, "reason": "合成"})[0] == 200
    name, reason, kw = seen[0]
    assert (name, reason) == (SPACED, "合成")
    assert kw["live"] is http_server.server.live_tasks
    assert kw["categories_path"] == tmp_path / "acme-categories.json" == http_server.server.categories_path()


@pytest.mark.parametrize("code,status", [("bad_name", 400), ("not_live", 400), ("task_running", 409),
                                         ("plan_changed", 409), ("enumeration_failed", 500)])
def test_http_maps_refusals_to_status_codes(http_server, monkeypatch, code, status):
    def refuse(*a, **kw):
        raise maint.Refused("合成拒绝", code)
    monkeypatch.setattr(D, "apply", refuse)
    reply = http_server.post("/api/task/delete/apply", {"name": NAME, "token": "synthetic"})
    assert reply == (status, {"ok": False, "error": "合成拒绝", "code": code})


@pytest.mark.parametrize("status,code", [("ok", 200), ("partial", 200), ("failed", 500), ("unknown", 500)])
def test_http_reports_a_failed_scheduler_step_as_500_and_partial_as_200(http_server, monkeypatch, status, code):
    monkeypatch.setattr(D, "apply", lambda *a, **kw: {"ok": status == "ok", "status": status})
    assert http_server.post("/api/task/delete/apply", {"name": NAME, "token": "synthetic"})[0] == code


def test_http_refuses_unknown_fields_oversize_bodies_and_read_only(http_server, monkeypatch):
    monkeypatch.setattr(D, "plan", lambda *a, **kw: pytest.fail("reached the module"))
    monkeypatch.setattr(D, "apply", lambda *a, **kw: pytest.fail("reached the module"))
    assert http_server.post("/api/task/delete/plan", {"name": NAME, "reason": "x", "path": "D:/Acme"})[0] == 400
    assert http_server.post("/api/task/delete/plan", {"name": NAME, "reason": ["x"]})[0] == 400
    assert http_server.post("/api/task/delete/plan", None, raw=json.dumps({"name": "x" * 9000}).encode()) == (
        413, {"ok": False, "error": "请求体超过 8192 字节", "code": "too_large"})
    monkeypatch.setenv("TASK_CONSOLE_READ_ONLY", "1")
    assert http_server.post("/api/task/delete/plan", {"name": NAME, "reason": "x"})[0] == 403
    assert http_server.post("/api/task/delete/apply", {"name": NAME, "token": "x"})[0] == 403
