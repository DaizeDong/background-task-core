"""修复工单:鉴权在派发之前、只读拒绝、只走绑定的待办 CLI、先 ensure 再按工作记录里的 revision 提交、
没有 agent 选项就不提交、第二次点击还给原工单、说明里有限制原文和事实而浏览器只能带一句备注、
浏览器塞不进一个不在册的任务名。

照 tests/test_work_action_broker.py 的做法:待办主人的 CLI 在 subprocess 边界上替换,
工作记录在 work_status.read_configured 上替换,提交在 work_actions.submit 上替换。全部合成数据。
"""
import http.client
import json
import re
import subprocess
from types import SimpleNamespace

import pytest

from test_panel_parity import server, synthetic_server  # noqa: F401  (fixture)
import maint
import task_repair as R
import work_actions
import work_status

NAME = "Acme Report Sync"
TOKEN = "synthetic-browser-token"


def payload():
    """任务页那份载荷的合成版:一个在册任务,带健康结论和运行日志里的返回码分布。"""
    row = {"name": NAME, "state": "Ready", "sk": "bad", "sl": "失败 0x1", "rcRaw": 1, "rcHex": "0x1",
           "lastRun": "2030-01-02 03:00", "nextRun": "2030-01-03 03:00", "missedRuns": 0, "infoError": None,
           "triggers": "Daily @03:00", "desc": "合成：生成 Acme 报告",
           "info": {"title": "Acme 报告同步", "advice": "查一下返回码", "verdict": "fix", "asOf": "2030-01-01"},
           "actions": [{"exec": "C:\\Acme\\report.exe", "args": "--sync", "cwd": "C:\\Acme"}],
           "artifact": "C:\\Acme\\out\\report.json", "artifactMax": 26, "okCodes": "2",
           "issues": [["warn", "漏火不补跑:错过的触发直接跳过"]],
           "runs": {"rcs": {"0": 3, "2": 4, "1": 5}, "starts": 12, "failStart": 1, "timedOut": 0,
                    "killed": 0, "successRate": 58.3, "judged": 12},
           "hist": {"obs": 10, "ok": 6, "bad": 4, "stale": 0, "health": 60.0}}
    return {"groups": [{"cat": "报表", "rows": [row]}],
            "freshness": {"tasks": [{"name": NAME, "label": "Acme 报告", "state": "down", "verdict": "bad",
                                     "reasons": ["合成：产物 30 小时没更新"], "reason_codes": ["artifact_stale"],
                                     "artifact": "C:\\Acme\\out\\report.json", "artifact_max_age_hours": 26},
                                    {"name": "AcmeOther", "state": "ok"}]},
            "runlog": {"countScope": "最近 30 天"}, "summary": {"generated": "2030-01-02 09:00:00"}}


def facts(name):
    return R.facts_from_payload(payload(), name)


def feed(*items):
    return {"schemaVersion": 1, "available": True, "items": list(items), "events": [], "sources": [],
            "coverage": {"total": len(items)}}


def order_item(item_id="acme-repair-1", *, task=NAME, state="pending", offer=True, enabled=True,
               current=None, updated="2030-01-02T00:00:00Z", note="合成备注", source=R.SOURCE):
    summary = R._marker_line(task) + "\n" + R.NOTE_PREFIX + note + "\n\n修复对象：合成"
    offers = [{"id": "agent", "kind": "agent", "label": "接着处理", "enabled": enabled}] if offer else []
    return {"id": item_id, "title": f"修复计划任务：合成（{task}）", "state": state, "role": "tracked_item",
            "source": source, "summary": summary, "updated_at": updated,
            "actions": {"available": True, "revision": "sha256:rev-" + item_id, "offers": offers,
                        "links": [], "current": current}}


@pytest.fixture
def owner(tmp_path, monkeypatch):
    """绑定好的合成待办主人:CLI 与库都是 tmp 里的空文件,subprocess 在边界上替换。"""
    cli = tmp_path / "reminder.py"
    cli.write_text("# synthetic", encoding="utf-8")
    db = tmp_path / "work.sqlite3"
    db.touch()
    env = {"TASK_CONSOLE_REMINDER_CLI": str(cli), "TASK_CONSOLE_REMINDER_DB": str(db),
           "TASK_CONSOLE_ACTION_WORKSPACE": str(tmp_path / "work")}
    state = SimpleNamespace(env=env, cli=cli, db=db, ensure=[], submit=[], feeds=[feed()])

    def run(argv, **kw):
        state.ensure.append((argv, kw))
        state.feeds.append(feed(order_item()))
        return SimpleNamespace(returncode=0, stdout=json.dumps(
            {"api_version": 1, "schema_version": 1, "ok": True, "item": {"id": "acme-repair-1"},
             "decision": "created"}, ensure_ascii=False).encode("utf-8"), stderr=b"")
    monkeypatch.setattr(R.subprocess, "run", run)
    monkeypatch.setattr(work_status, "read_configured", lambda env: state.feeds[-1])
    monkeypatch.setattr(work_actions, "submit", lambda request, env=None: state.submit.append(request) or
                        {"schemaVersion": 1, "ok": True, "status": "queued", "action": {"id": "receipt-1"}, "wakeup": True})
    return state


def description_of(call):
    argv = call[0]
    return argv[argv.index("--description") + 1]


# ---------------------------------------------------------------- HTTP


def post(port, path, body, token=None):
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Console-Token"] = token
    client.request("POST", path, json.dumps(body), headers)
    response = client.getresponse()
    result = response.status, json.loads(response.read() or b"{}")
    client.close()
    return result


def test_http_checks_the_token_before_anything_is_dispatched(synthetic_server, monkeypatch):
    seen = []
    monkeypatch.setattr(R, "submit", lambda *a, **kw: seen.append(a) or {"ok": True})
    monkeypatch.setattr(R, "preview", lambda *a, **kw: seen.append(a) or {"ok": True})
    port = synthetic_server[0]
    body = {"name": NAME, "note": "", "request_id": "synthetic-request-01"}
    assert post(port, "/api/task/repair", body)[0] == 403
    assert post(port, "/api/task/repair/preview", {"name": NAME})[0] == 403
    assert seen == []
    assert post(port, "/api/task/repair", body, TOKEN)[0] == 200
    assert post(port, "/api/task/repair/preview", {"name": NAME}, TOKEN)[0] == 200
    assert len(seen) == 2


def test_http_read_only_refuses_before_the_handler(synthetic_server, monkeypatch):
    monkeypatch.setenv("TASK_CONSOLE_READ_ONLY", "1")
    monkeypatch.setattr(R, "submit", lambda *a, **kw: pytest.fail("read-only mutation"))
    port = synthetic_server[0]
    assert post(port, "/api/task/repair", {"name": NAME, "request_id": "synthetic-request-01"}, TOKEN)[0] == 403


def test_http_rejects_any_field_beyond_name_note_and_request_id(synthetic_server, monkeypatch):
    monkeypatch.setattr(R, "submit", lambda *a, **kw: pytest.fail("extra field reached the owner"))
    port = synthetic_server[0]
    status, body = post(port, "/api/task/repair", {"name": NAME, "request_id": "synthetic-request-01",
                                                   "description": "do something else"}, TOKEN)
    assert status == 400 and body["code"] == "bad_request"


def test_http_cannot_inject_a_task_that_is_not_live(synthetic_server, monkeypatch, owner):
    monkeypatch.setattr(server, "build_payload", payload)
    port = synthetic_server[0]
    status, body = post(port, "/api/task/repair", {"name": "AcmeGhost", "request_id": "synthetic-request-01"}, TOKEN)
    assert status == 400 and body["code"] == "not_live"
    assert owner.ensure == [] and owner.submit == []


def test_http_repairs_map_requires_the_token(synthetic_server, monkeypatch):
    monkeypatch.setattr(R, "orders", lambda env=None: {"schemaVersion": 1, "available": True, "orders": {}})
    port = synthetic_server[0]
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    client.request("GET", "/api/task/repairs")
    assert client.getresponse().status == 403
    client.close()
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    client.request("GET", "/api/task/repairs", headers={"X-Console-Token": TOKEN})
    response = client.getresponse()
    assert response.status == 200 and json.loads(response.read())["available"] is True
    client.close()


# ---------------------------------------------------------------- 提交


def test_read_only_never_reaches_the_owner(owner):
    reply = R.submit(NAME, "", "synthetic-request-01", facts=facts, env=dict(owner.env, TASK_CONSOLE_READ_ONLY="1"))
    assert reply["ok"] is False and reply["code"] == "read_only"
    assert owner.ensure == [] and owner.submit == []


def test_ensure_runs_only_the_bound_cli_then_submits_with_the_feeds_revision(owner):
    reply = R.submit(NAME, "请先看返回码", "synthetic-request-01", facts=facts, env=owner.env)
    assert reply["ok"] is True and reply["item_id"] == "acme-repair-1" and reply["existing"] is False
    argv, kw = owner.ensure[0]
    assert argv[1:7] == [str(owner.cli), "--db", str(owner.db), "--actor", "task-console", "ensure"]
    assert not kw.get("shell") and kw["env"]["SCHEDULE_DB_PATH"] == str(owner.db)
    assert argv[argv.index("--source") + 1] == "task-console-repair"
    assert argv[argv.index("--title") + 1] == f"修复计划任务：Acme 报告同步（{NAME}）"
    assert "--distinct-reason" in argv
    ext = json.loads(argv[argv.index("--ext") + 1])
    assert ext == {"x_task_console_repair": {"schema": 1, "task": NAME}} and "task_console" not in ext
    assert owner.submit == [{"item_id": "acme-repair-1", "action_id": "agent",
                             "revision": "sha256:rev-acme-repair-1", "request_id": owner.submit[0]["request_id"]}]
    assert re.fullmatch(r"[A-Za-z0-9_-]{8,120}", owner.submit[0]["request_id"])


def test_the_idempotency_key_follows_task_and_request_not_the_wall_clock(owner):
    R.submit(NAME, "", "synthetic-request-01", facts=facts, env=owner.env)
    owner.feeds.append(feed())
    R.submit(NAME, "", "synthetic-request-01", facts=facts, env=owner.env)
    owner.feeds.append(feed())
    R.submit(NAME, "", "synthetic-request-02", facts=facts, env=owner.env)
    keys = [argv[argv.index("--idempotency-key") + 1] for argv, _ in owner.ensure]
    assert keys[0] == keys[1] != keys[2]


@pytest.mark.parametrize("item", [order_item(offer=False), order_item(enabled=False)])
def test_no_submit_when_the_agent_offer_is_missing_or_disabled(owner, monkeypatch, item):
    def run(argv, **kw):
        owner.ensure.append((argv, kw))
        owner.feeds.append(feed(item))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "item": {"id": item["id"]},
                                                                "decision": "created"}).encode(), stderr=b"")
    monkeypatch.setattr(R.subprocess, "run", run)
    reply = R.submit(NAME, "", "synthetic-request-01", facts=facts, env=owner.env)
    assert reply["ok"] is False and reply["code"] == "agent_offer_unavailable"
    assert owner.submit == []


def test_a_second_click_returns_the_existing_order_instead_of_creating_another(owner):
    running = order_item(current={"id": "receipt-1", "state": "running", "summary": "", "work_item_id": "w1"})
    owner.feeds = [feed(running)]
    reply = R.submit(NAME, "", "synthetic-request-02", facts=facts, env=owner.env)
    assert reply["ok"] is True and reply["existing"] is True and reply["item_id"] == "acme-repair-1"
    assert reply["order"]["action"]["state"] == "running"
    assert owner.ensure == [] and owner.submit == []


def test_an_existing_order_that_was_never_dispatched_is_dispatched_not_duplicated(owner):
    owner.feeds = [feed(order_item())]
    reply = R.submit(NAME, "", "synthetic-request-02", facts=facts, env=owner.env)
    assert reply["existing"] is True and owner.ensure == []
    assert [r["item_id"] for r in owner.submit] == ["acme-repair-1"]


def test_two_concurrent_submits_for_one_task_file_one_order_and_one_agent_run(owner, monkeypatch):
    # 两个标签页各带一个请求号同时点修复。慢一点的 ensure 把「查」和「建」之间的窗口拉开:
    # 不串行的话,两边都在对方落库前查到「没有工单」,各开一张、各交一次 Agent。
    import threading
    import time
    created = []

    def slow_ensure(argv, **kw):
        owner.ensure.append((argv, kw))
        time.sleep(0.5)
        created.append(order_item(f"acme-repair-{len(created) + 1}"))
        owner.feeds.append(feed(*created))
        return SimpleNamespace(returncode=0, stdout=json.dumps(
            {"ok": True, "item": {"id": created[-1]["id"]}, "decision": "created"}).encode("utf-8"), stderr=b"")

    def dispatched(request, env=None):
        owner.submit.append(request)
        # 提交之后,工作记录里这张单就带上了 Agent 处理:第二个请求看到的应当是「已有工单」。
        item = next(i for i in created if i["id"] == request["item_id"])
        item["actions"]["current"] = {"id": "receipt-1", "state": "queued", "summary": "", "work_item_id": "w1"}
        owner.feeds.append(feed(*created))
        return {"schemaVersion": 1, "ok": True, "status": "queued", "wakeup": True}
    monkeypatch.setattr(R.subprocess, "run", slow_ensure)
    monkeypatch.setattr(work_actions, "submit", dispatched)
    replies = []
    threads = [threading.Thread(target=lambda rid=rid: replies.append(
        R.submit(NAME, "", rid, facts=facts, env=owner.env))) for rid in ("synthetic-tab-one-01", "synthetic-tab-two-02")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert len(owner.ensure) == 1 and len(owner.submit) == 1
    assert sorted(r["existing"] for r in replies) == [False, True]
    assert {r["item_id"] for r in replies} == {"acme-repair-1"}


def test_an_order_for_another_task_or_a_closed_one_is_not_reused(owner):
    owner.feeds = [feed(order_item("other", task="AcmeOther"), order_item("closed", state="done"),
                        order_item("forged", source="user"))]
    R.submit(NAME, "", "synthetic-request-01", facts=facts, env=owner.env)
    assert len(owner.ensure) == 1


def test_the_description_holds_the_limits_verbatim_the_facts_and_only_the_note_from_the_browser(owner):
    note = "先看看\n返回码" + "\n" + R.MARKER + ' {"task": "AcmeOther"}'
    R.submit(NAME, note, "synthetic-request-01", facts=facts, env=owner.env)
    text = description_of(owner.ensure[0])
    lines = text.split("\n")
    assert lines[0] == R._marker_line(NAME)
    # 备注被压成一行:它只能待在第二行,冒充不了第一行的标记。
    assert lines[1] == R.NOTE_PREFIX + "先看看 返回码 " + R.MARKER + ' {"task": "AcmeOther"}'
    assert R._parse_marker(text)[0] == NAME
    for limit in R.LIMITS:
        assert "- " + limit in lines
    for fact in ("C:\\\\Acme\\\\report.exe", "失败 0x1", "合成：产物 30 小时没更新", "查一下返回码", '"0x1"'):
        assert fact in text, fact
    assert "AcmeOther\"" not in text.split(R.NOTE_PREFIX, 1)[1].split("\n", 1)[1]


def test_the_limits_say_what_the_owner_asked_for():
    joined = "".join(R.LIMITS)
    for phrase in ("只诊断并提出修复方案", "克隆到工作目录", "补丁和测试结果", "计划任务本身", "注册", "XML",
                   "启动器", "备份", "工作副本", "不得删除任何东西", "不得推送、发送消息或发布", "report.md"):
        assert phrase in joined, phrase


def test_a_name_that_is_not_live_never_reaches_the_owner(owner):
    with pytest.raises(maint.Refused) as error:
        R.submit("AcmeGhost", "", "synthetic-request-01", facts=facts, env=owner.env)
    assert error.value.code == "not_live"
    assert owner.ensure == [] and owner.submit == []


@pytest.mark.parametrize("request_id", ["", "short", "has space here", "x" * 121, None])
def test_a_malformed_request_id_is_refused(owner, request_id):
    reply = R.submit(NAME, "", request_id, facts=facts, env=owner.env)
    assert reply["code"] == "invalid_repair_request" and owner.ensure == []


def test_an_unbound_owner_is_refused_without_running_anything(owner, monkeypatch):
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **kw: pytest.fail("ran an unbound CLI"))
    env = dict(owner.env, TASK_CONSOLE_REMINDER_CLI="relative/reminder.py")
    reply = R.submit(NAME, "", "synthetic-request-01", facts=facts, env=env)
    assert reply["ok"] is False and reply["code"] == "work_binding_invalid"


def test_an_owner_refusal_is_passed_through_and_nothing_is_submitted(owner, monkeypatch):
    monkeypatch.setattr(R.subprocess, "run", lambda argv, **kw: SimpleNamespace(
        returncode=1, stdout=b"", stderr=json.dumps({"ok": False, "error_code": "ERR_CONFLICT"}).encode()))
    reply = R.submit(NAME, "", "synthetic-request-01", facts=facts, env=owner.env)
    assert reply["code"] == "ERR_CONFLICT" and owner.submit == []


def test_an_unreadable_owner_reply_is_uncertain_not_success(owner, monkeypatch):
    monkeypatch.setattr(R.subprocess, "run", lambda argv, **kw: SimpleNamespace(returncode=0, stdout=b"{\"ok\": true}", stderr=b""))
    reply = R.submit(NAME, "", "synthetic-request-01", facts=facts, env=owner.env)
    assert reply["ok"] is False and reply["uncertain"] is True and owner.submit == []


# ---------------------------------------------------------------- 事实与工单表


def test_facts_come_from_the_console_payload_and_respect_declared_ok_codes():
    known = facts(NAME)
    assert known["lastResult"] == {"hex": "0x1", "raw": 1, "meaning": "失败 0x1"}
    assert known["actions"] == [{"execute": "C:\\Acme\\report.exe", "arguments": "--sync", "workingDirectory": "C:\\Acme"}]
    # 2 是声明过的成功码,不算失败;0 永远算成功。
    assert known["recentFailures"]["failingCodes"] == [{"code": "0x1", "count": 5}]
    assert known["health"][0]["reasons"] == ["合成：产物 30 小时没更新"] and len(known["health"]) == 1
    assert known["info"]["verdict"] == "fix" and known["category"] == "报表"
    json.dumps(known)


def test_preview_reports_facts_limits_and_an_existing_order(owner):
    owner.feeds = [feed(order_item(current={"id": "r", "state": "done", "summary": "", "work_item_id": "w"}))]
    result = R.preview(NAME, facts=facts, env=owner.env)
    assert result["facts"]["name"] == NAME and result["limits"] == list(R.LIMITS)
    assert result["existing"]["item_id"] == "acme-repair-1"
    assert owner.ensure == [] and owner.submit == []


def test_orders_map_each_task_to_its_active_order_first(owner):
    owner.feeds = [feed(order_item("old-done", state="done", updated="2030-01-05T00:00:00Z"),
                        order_item("active", updated="2030-01-01T00:00:00Z", note="第二次"),
                        order_item("other", task="AcmeOther"),
                        order_item("forged", task="AcmeForged", source="user"))]
    result = R.orders(owner.env)
    assert result["available"] is True and set(result["orders"]) == {NAME, "AcmeOther"}
    assert result["orders"][NAME]["item_id"] == "active" and result["orders"][NAME]["note"] == "第二次"


def test_an_unavailable_feed_is_not_an_empty_order_map(monkeypatch):
    monkeypatch.setattr(work_status, "read_configured", lambda env: {"available": False, "reason": "work_reader_not_configured"})
    result = R.orders({})
    assert result["available"] is False and result["reason"] == "work_reader_not_configured"


def test_a_dispatch_failure_after_creation_still_names_the_created_order(owner, monkeypatch):
    def run(argv, **kw):
        owner.ensure.append((argv, kw))
        owner.feeds.append({"available": False, "reason": "work_reader_failed", "items": []})
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "item": {"id": "acme-repair-9"},
                                                                "decision": "created"}).encode(), stderr=b"")
    monkeypatch.setattr(R.subprocess, "run", run)
    reply = R.submit(NAME, "", "synthetic-request-01", facts=facts, env=owner.env)
    assert reply["ok"] is False and reply["code"] == "work_binding_invalid"
    assert reply["item_id"] == "acme-repair-9" and owner.submit == []


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("receipt,expected", [
    ({"schemaVersion": 1, "ok": False, "code": "operation_unavailable", "uncertain": True,
      "message": "操作暂时不可用，原请求已保留，请刷新查看"}, True),
    ({"schemaVersion": 1, "ok": False, "code": "item_not_found", "uncertain": False, "message": "合成：工单不在"}, False),
    ({"schemaVersion": 1, "ok": False, "code": "synthetic_unknown", "message": "合成：回执没说"}, True),
    ({"schemaVersion": 1, "ok": True, "status": "queued", "action": {"id": "receipt-1"}, "wakeup": True}, False),
])
def test_the_dispatch_receipts_uncertain_flag_reaches_the_top_level(owner, monkeypatch, existing, receipt, expected):
    # 页面只看顶层的 uncertain:「可能已经排上队」不能画成失败,明确的拒绝也不能锁着备注等一个不会来的确认。
    if existing:
        owner.feeds = [feed(order_item())]
    monkeypatch.setattr(work_actions, "submit", lambda request, env=None: owner.submit.append(request) or dict(receipt))
    reply = R.submit(NAME, "", "synthetic-request-01", facts=facts, env=owner.env)
    assert reply["existing"] is existing and reply["ok"] is receipt["ok"]
    assert reply["uncertain"] is expected
    assert len(owner.submit) == 1


def test_a_missing_agent_offer_is_a_definite_refusal_at_the_top_level(owner, monkeypatch):
    def run(argv, **kw):
        owner.ensure.append((argv, kw))
        owner.feeds.append(feed(order_item(enabled=False)))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "item": {"id": "acme-repair-1"},
                                                                "decision": "created"}).encode(), stderr=b"")
    monkeypatch.setattr(R.subprocess, "run", run)
    reply = R.submit(NAME, "", "synthetic-request-01", facts=facts, env=owner.env)
    assert reply["code"] == "agent_offer_unavailable" and reply["uncertain"] is False
