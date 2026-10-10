"""起名建议:摘要只收人打的字且有上限,答案经过规整,路由只读不写,模型永远是假的。

所有转录都由 tools/make_fixtures.py 现场生成,写在 tmp_path 里。
"""
import json
import threading

import pytest

from tools.make_fixtures import synthetic_mixed_conversation
from test_console_security import srv, call, TOKEN, S, _api_route_literals  # noqa: F401

TS = S.title_suggest
# autouse 的替身会盖掉它;接线测试要的是原函数,在那之前取下来。
REAL_CALLER = TS._llm_caller
ROUTE = "/api/convo/suggest-title"


class FakeResult:
    """llmcall.Result 的最小替身:真假值、text、data、provider、error。"""

    def __init__(self, text="", data=None, provider="synthetic-provider", ok=True, error=None):
        self.text, self.data, self.provider, self.ok, self.error = text, data, provider, ok, error

    def __bool__(self):
        return self.ok


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    """任何一条用例忘了注入假模型,都在这里炸掉,而不是去调真的。"""
    def refuse(*args, **kwargs):
        raise AssertionError("测试里不许调真模型")
    monkeypatch.setattr(TS, "_llm_caller", refuse)


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    project = root / "C--Acme-source"
    project.mkdir(parents=True)
    sid, text = synthetic_mixed_conversation(701, human=3, title="Acme 旧名字")
    path = project / (sid + ".jsonl")
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("TASK_CONSOLE_SESSIONS", str(root))
    return root, project, sid, path


def stub(monkeypatch, answer):
    """注入一个假模型:记下它收到的提示,按 llmcall 的约定用 extract 校验 answer。"""
    seen = []

    def fake(prompt, extract, timeout):
        seen.append({"prompt": prompt, "timeout": timeout})
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, FakeResult):
            return answer
        data = extract(answer)
        return FakeResult(text=answer, data=data) if data is not None else FakeResult(ok=False, error="extract rejected")
    monkeypatch.setattr(TS, "_llm_caller", fake)
    return seen


# ---------- 规整 ----------

@pytest.mark.parametrize("raw,expected", [
    ("修复会话删除", "修复会话删除"),
    ("  “修复会话删除”  ", "修复会话删除"),
    ("标题：整理控制台改名", "整理控制台改名"),
    ("Title: 整理控制台改名。", "整理控制台改名"),
    ("《AcmeCorp 发布计划》", "AcmeCorp 发布计划"),
    ("「重构 task-console 列表」！", "重构 task-console 列表"),
    ("'示例  会话   起名'", "示例 会话 起名"),
])
def test_normalize_accepts_and_cleans(raw, expected):
    assert TS.normalize_title(raw) == expected


@pytest.mark.parametrize("raw", [
    None, "", "   ", "“”", "标题：",
    "第一行\n第二行",
    "Only an English title",
    "超" * (TS.TITLE_MAX + 1),
    "带控制\x07字符",
])
def test_normalize_rejects(raw):
    assert TS.normalize_title(raw) is None


def test_normalize_length_limit_is_exact():
    """负对照:正好 TITLE_MAX 个字能过,多一个就不过。上面那条 too-long 不是碰巧被别的规则拒的。"""
    assert TS.normalize_title("长" * TS.TITLE_MAX) == "长" * TS.TITLE_MAX
    assert TS.normalize_title("长" * (TS.TITLE_MAX + 1)) is None


# ---------- 摘要 ----------

def test_digest_keeps_only_what_a_person_typed(tmp_path):
    sid, text = synthetic_mixed_conversation(702, human=4)
    path = tmp_path / (sid + ".jsonl")
    path.write_text(text, encoding="utf-8")
    digest = TS.collect_digest(path)
    assert len(digest["messages"]) == 4
    assert all(m.startswith("Synthetic human ask") for m in digest["messages"])
    noise = ("injected reminder", "Synthetic echo", "tool output", "sidechain", "Synthetic reply",
             "Synthetic skill body", "Synthetic compact summary")
    for leaked in noise:
        assert leaked not in digest["text"], leaked
    # 负对照:那些东西确实在转录里,摘要里没有不是因为生成器没写。
    for present in noise:
        assert present in text, present
    # 两个标志各自单独钉住:isMeta 和 isCompactSummary 的记录本身是普通字符串内容,
    # 只靠 typed_text / looks_injected 会把它们当成人打的字。
    rows = [json.loads(line) for line in text.splitlines()]
    assert any(r.get("isMeta") and isinstance(r["message"]["content"], str) for r in rows)
    assert any(r.get("isCompactSummary") and isinstance(r["message"]["content"], str) for r in rows)


def test_digest_keeps_every_message_when_records_have_no_uuid(tmp_path):
    """没有 uuid 的记录不许互相撞键:8 条不同的消息进来,8 条都在。"""
    sid, text = synthetic_mixed_conversation(709, human=8, uuids=False)
    assert '"uuid"' not in text
    path = tmp_path / (sid + ".jsonl")
    path.write_text(text, encoding="utf-8")
    digest = TS.collect_digest(path)
    assert [m.split(":")[0] for m in digest["messages"]] == [f"Synthetic human ask {n:03d}" for n in range(8)]


def test_digest_is_bounded_and_takes_the_head_and_the_tail(tmp_path):
    sid, text = synthetic_mixed_conversation(703, human=50, text_chars=2000)
    path = tmp_path / (sid + ".jsonl")
    path.write_text(text, encoding="utf-8")
    digest = TS.collect_digest(path)
    asks = [m.split(":")[0] for m in digest["messages"]]
    assert asks == [f"Synthetic human ask {n:03d}" for n in list(range(6)) + list(range(44, 50))]
    assert digest["skipped"] == 38
    assert all(len(m) <= TS.MESSAGE_CHARS for m in digest["messages"])
    assert len(digest["text"]) <= TS.DIGEST_CHARS
    assert "中间省略 38 条" in digest["text"]
    # 合计上限单独量:默认的条数乘每条上限碰不到 6000,所以把合计调小,看它真的截断。
    small = TS.collect_digest(path, total_chars=1500)
    assert len(small["text"]) <= 1500 and 0 < small["text"].count("Synthetic human ask") < 12


def test_digest_reads_only_the_head_and_tail_of_a_large_file(tmp_path, monkeypatch):
    """大文件只读头尾两段:中间的消息看不到,而且一次 read 不会超过两段之和。"""
    sid, text = synthetic_mixed_conversation(704, human=200)
    path = tmp_path / (sid + ".jsonl")
    path.write_text(text, encoding="utf-8")
    size = path.stat().st_size
    reads = []
    real_open = open

    class Spy:
        def __init__(self, fh):
            self.fh = fh

        def read(self, n=-1):
            data = self.fh.read(n)
            reads.append(len(data))
            return data

        def __getattr__(self, name):
            return getattr(self.fh, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.fh.close()

    monkeypatch.setattr(TS, "open", lambda p, mode="r", *a, **k: Spy(real_open(p, mode, *a, **k)), raising=False)
    digest = TS.collect_digest(path, head_bytes=4000, tail_bytes=4000)
    assert size > 8000 * 10 and digest["partial"] is True
    assert sum(reads) <= 8000 and reads, reads
    numbers = [int(m.split(":")[0].rsplit(" ", 1)[1]) for m in digest["messages"]]
    assert numbers[0] == 0 and numbers[-1] == 199
    assert not any(50 <= n <= 150 for n in numbers)


def test_digest_takes_the_name_hint_from_the_regions_it_read(tmp_path):
    sid, text = synthetic_mixed_conversation(707, human=2, title="Acme 改过的名字")
    path = tmp_path / (sid + ".jsonl")
    path.write_text(text + json.dumps({"type": "ai-title", "aiTitle": "Acme 自动标题"}) + "\n", encoding="utf-8")
    assert TS.collect_digest(path)["title"] == "Acme 改过的名字"
    sid, text = synthetic_mixed_conversation(708, human=2)
    path.write_text(text + json.dumps({"type": "ai-title", "aiTitle": "Acme 自动标题"}) + "\n", encoding="utf-8")
    assert TS.collect_digest(path)["title"] == "Acme 自动标题"
    path.write_text(text, encoding="utf-8")
    assert TS.collect_digest(path)["title"] is None


def test_digest_with_no_typed_text_is_empty(tmp_path):
    sid, text = synthetic_mixed_conversation(705, human=0)
    path = tmp_path / (sid + ".jsonl")
    path.write_text(text + json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "x", "content": "Synthetic tool output"}]}}) + "\n", encoding="utf-8")
    assert TS.collect_digest(path)["messages"] == []


# ---------- 提示与调用 ----------

def test_prompt_carries_the_digest_and_the_current_title():
    prompt = TS.build_prompt("1. Synthetic human ask", "Acme 旧名字")
    assert "1. Synthetic human ask" in prompt and "Acme 旧名字" in prompt and "中文标题" in prompt
    assert "当前名称" not in TS.build_prompt("1. Synthetic human ask", None)


def test_default_caller_uses_judge_mode_and_the_extractor(monkeypatch):
    """真调用的接线:mode=judge、extract 是传进来的那个。用假的 llmcall.call 量,不碰真模型。"""
    import llmcall
    seen = {}

    def fake_call(prompt, **kwargs):
        seen.update(kwargs, prompt=prompt)
        return FakeResult(text="示例")
    monkeypatch.setattr(llmcall, "call", fake_call)
    REAL_CALLER("synthetic prompt", TS.normalize_title, 5)
    assert seen["prompt"] == "synthetic prompt" and seen["mode"] == "judge"
    assert seen["extract"] is TS.normalize_title and seen["timeout"] == 5


def test_suggest_without_human_text_never_calls_the_model(monkeypatch):
    seen = stub(monkeypatch, "不该被调用")
    with pytest.raises(TS.SuggestError) as error:
        TS.suggest({"messages": [], "text": ""})
    assert error.value.code == "no_content" and error.value.status == 422 and seen == []


# ---------- 路由 ----------

def test_route_is_in_the_token_sweep():
    _src, routes = _api_route_literals()
    assert ROUTE in routes


def test_route_rejects_without_token_and_from_a_rebinding_host(srv, sessions, monkeypatch):
    seen = stub(monkeypatch, "示例名称")
    _, project, sid, _ = sessions
    body = {"id": sid, "expectedProject": project.name}
    assert call(srv, "POST", ROUTE, body=body)[0] == 403
    assert call(srv, "POST", ROUTE, host="attacker.example:80", token=TOKEN, body=body)[0] == 400
    assert seen == []


def test_route_reads_the_body_before_refusing_a_bad_token(srv, sessions, monkeypatch):
    """没带令牌也先把正文读掉再回 403:不读就关连接,客户端常收到的是连接被重置而不是 403。"""
    seen = stub(monkeypatch, "示例名称")
    drained = []
    real = S.Handler._drain

    def spy(self):
        drained.append(self.path)
        return real(self)
    monkeypatch.setattr(S.Handler, "_drain", spy)
    _, project, sid, _ = sessions
    st, _ = call(srv, "POST", ROUTE, body={"id": sid, "expectedProject": project.name, "pad": "x" * 5000})
    assert st == 403
    assert drained == [ROUTE]
    assert seen == []


def test_route_reports_a_transcript_it_cannot_read_as_busy(srv, sessions, monkeypatch):
    """读转录撞上 OSError(被别的进程锁着、权限):回 409 busy,让人稍后重试,不是 500。"""
    seen = stub(monkeypatch, "示例名称")

    def locked(path, **kwargs):
        raise PermissionError("synthetic sharing violation")
    monkeypatch.setattr(TS, "collect_digest", locked)
    _, project, sid, _ = sessions
    st, data = call(srv, "POST", ROUTE, token=TOKEN, body={"id": sid, "expectedProject": project.name})
    payload = json.loads(data)
    assert (st, payload["code"]) == (409, "busy") and "PermissionError" in payload["error"]
    assert seen == []


def test_route_rejects_unknown_keys_and_oversize_bodies(srv, sessions, monkeypatch):
    seen = stub(monkeypatch, "示例名称")
    _, project, sid, _ = sessions
    st, data = call(srv, "POST", ROUTE, token=TOKEN, body={"id": sid, "expectedProject": project.name, "title": "x"})
    assert (st, json.loads(data)["code"]) == (400, "bad_body")
    st, data = call(srv, "POST", ROUTE, token=TOKEN, body={"id": sid, "expectedProject": "x" * (S.CONVO_BODY_MAX + 10)})
    assert (st, json.loads(data)["code"]) == (413, "too_large")
    # 正对照:没超上限的同形正文照常解析,走到形状闸。
    st, data = call(srv, "POST", ROUTE, token=TOKEN, body={"id": "x" * (S.CONVO_BODY_MAX - 200)})
    assert (st, json.loads(data)["code"]) == (400, "bad_id")
    assert seen == []


def test_route_returns_a_suggestion_and_writes_nothing(srv, sessions, monkeypatch):
    root, project, sid, path = sessions
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    seen = stub(monkeypatch, "「整理 Acme 小部件」")
    st, data = call(srv, "POST", ROUTE, token=TOKEN, body={"id": sid, "expectedProject": project.name})
    assert st == 200, data
    assert json.loads(data) == {"title": "整理 Acme 小部件", "provider": "synthetic-provider"}
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    prompt = seen[0]["prompt"]
    assert "Synthetic human ask 000" in prompt and "Synthetic human ask 002" in prompt
    assert "Acme 旧名字" in prompt
    assert "injected reminder" not in prompt and "tool output" not in prompt


def test_route_without_expected_project_still_works(srv, sessions, monkeypatch):
    _, _, sid, _ = sessions
    stub(monkeypatch, "示例名称")
    st, data = call(srv, "POST", ROUTE, token=TOKEN, body={"id": sid})
    assert st == 200 and json.loads(data)["title"] == "示例名称"


def test_route_refuses_a_session_that_moved(srv, sessions, monkeypatch):
    seen = stub(monkeypatch, "示例名称")
    _, _, sid, _ = sessions
    st, data = call(srv, "POST", ROUTE, token=TOKEN, body={"id": sid, "expectedProject": "C--Acme-other"})
    assert (st, json.loads(data)["code"]) == (409, "conflict")
    st, data = call(srv, "POST", ROUTE, token=TOKEN, body={"id": sid, "expectedProject": 7})
    assert (st, json.loads(data)["code"]) == (400, "bad_project")
    assert seen == []


def test_route_no_content(srv, sessions, monkeypatch):
    root, project, _, _ = sessions
    sid, text = synthetic_mixed_conversation(706, human=0)
    (project / (sid + ".jsonl")).write_text(text, encoding="utf-8")
    seen = stub(monkeypatch, "示例名称")
    st, data = call(srv, "POST", ROUTE, token=TOKEN, body={"id": sid, "expectedProject": project.name})
    payload = json.loads(data)
    assert (st, payload["code"]) == (422, "no_content") and "用户" in payload["error"]
    assert seen == []


@pytest.mark.parametrize("answer,status,code", [
    (FakeResult(ok=False, error="all providers failed"), 503, "llm_unavailable"),
    (RuntimeError("synthetic transport failure"), 503, "llm_unavailable"),
    ("Only an English title", 503, "llm_unavailable"),
    (FakeResult(text="第一行\n第二行", data=None), 502, "bad_answer"),
])
def test_route_chain_failures(srv, sessions, monkeypatch, answer, status, code):
    _, project, sid, _ = sessions
    stub(monkeypatch, answer)
    st, data = call(srv, "POST", ROUTE, token=TOKEN, body={"id": sid, "expectedProject": project.name})
    payload = json.loads(data)
    assert (st, payload["code"]) == (status, code), data
    assert "名称" in payload["error"] and "title" not in payload


def test_route_unknown_session_and_unset_root(srv, sessions, monkeypatch):
    stub(monkeypatch, "示例名称")
    st, data = call(srv, "POST", ROUTE, token=TOKEN, body={"id": "00000000-0000-4000-8000-0000000000ff"})
    assert (st, json.loads(data)["code"]) == (400, "not_found")
    monkeypatch.delenv("TASK_CONSOLE_SESSIONS")
    st, data = call(srv, "POST", ROUTE, token=TOKEN, body={"id": "00000000-0000-4000-8000-0000000000ff"})
    payload = json.loads(data)
    assert (st, payload["code"]) == (400, "unavailable") and "TASK_CONSOLE_SESSIONS" in payload["error"]


def test_waiting_for_the_model_does_not_hold_the_conversation_lock(srv, sessions, monkeypatch):
    """模型要等几十秒。等的时候别的线程必须拿得到对话锁,否则整个会话页跟着卡住。"""
    _, project, sid, _ = sessions
    free = []

    def probe(box):
        got = S._CONVO_ACCESS.acquire(timeout=2)
        box.append(got)
        if got:
            S._CONVO_ACCESS.release()

    def fake(prompt, extract, timeout):
        box = []
        worker = threading.Thread(target=probe, args=(box,))
        worker.start()
        worker.join()
        free.append(bool(box and box[0]))
        return FakeResult(text="示例名称", data="示例名称")
    monkeypatch.setattr(TS, "_llm_caller", fake)
    st, _ = call(srv, "POST", ROUTE, token=TOKEN, body={"id": sid, "expectedProject": project.name})
    assert st == 200 and free == [True]
