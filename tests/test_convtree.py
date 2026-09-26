#!/usr/bin/env python3
"""对话链(convtree)的测试。全部是在 tmp_path 里现造的合成转录,没有一行真实数据。

最值钱的几条判据,每条都配了负对照或正对照:

**并行工具调用不是分叉。** 一次回复调两个工具,在 parentUuid 上长成 1→2。
把它当分叉,一份真实转录报出的分叉数翻好几倍,真的分叉淹在里面。
负对照:把第二块换成另一个 message.id,同样的形状必须被判成真分叉。

**压缩边界的 logicalParentUuid 可能指向边界之后。** 照着走会绕回边界自己,
压缩前的整段历史从链上消失,而页面照样渲染出一条「完整」的链。

**分叉必须是一份新文件,源文件一个字节都不变。** 每条分叉用例都比对源文件字节。

**id 先按形状拒绝,再碰文件系统。**
"""
import gzip
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "scripts", "task_console"))

import convtree as T  # noqa: E402
from maint import Refused  # noqa: E402

SID = "11111111-1111-4111-8111-111111111111"
CWD = "C:/work/example-project"


def U(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def ts(n: int) -> str:
    return f"2026-01-01T00:{n // 60:02d}:{n % 60:02d}Z"


def rec(**kw):
    kw.setdefault("sessionId", SID)
    kw.setdefault("cwd", CWD)
    kw.setdefault("isSidechain", False)
    return kw


def human(n, p, text):
    return rec(type="user", uuid=U(n), parentUuid=p, timestamp=ts(n),
               promptSource="typed", message={"role": "user", "content": text})


def asst(n, p, mid, block):
    return rec(type="assistant", uuid=U(n), parentUuid=p, timestamp=ts(n),
               message={"id": mid, "role": "assistant", "model": "model-x", "content": [block]})


def text(t):
    return {"type": "text", "text": t}


def think(t):
    return {"type": "thinking", "thinking": t}


def tool(tid, name="Bash", **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp or {"command": "echo hi"}}


def result(n, p, tid, body="ok", agent=None):
    r = rec(type="user", uuid=U(n), parentUuid=p, timestamp=ts(n),
            sourceToolAssistantUUID=p,
            message={"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": body}]})
    if agent:
        r["toolUseResult"] = {"agentId": agent, "status": "completed"}
    return r


def system(n, p, subtype="turn_duration"):
    return rec(type="system", uuid=U(n), parentUuid=p, timestamp=ts(n), subtype=subtype,
               content="")


def attach(n, p):
    return rec(type="attachment", uuid=U(n), parentUuid=p, timestamp=ts(n),
               attachment={"type": "example_attachment"})


def boundary(n, lp, anchor, uuids, all_uuids=None):
    return rec(type="system", subtype="compact_boundary", uuid=U(n), parentUuid=None,
               logicalParentUuid=lp, timestamp=ts(n), content="Conversation compacted",
               compactMetadata={"trigger": "auto", "preTokens": 999000, "postTokens": 42000,
                                "preservedMessages": {"anchorUuid": anchor, "uuids": uuids,
                                                      "allUuids": all_uuids or uuids}})


def summary(n, p, t="synthetic summary of earlier work"):
    return rec(type="user", uuid=U(n), parentUuid=p, timestamp=ts(n), isCompactSummary=True,
               isVisibleInTranscriptOnly=True, message={"role": "user", "content": t})


def write(tmp_path, records, sid=SID, proj="proj-a", extra_lines=()):
    d = tmp_path / proj
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{sid}.jsonl"
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    body += "".join(x + "\n" for x in extra_lines)
    f.write_bytes(body.encode("utf-8"))
    return f


def path_uuids(r):
    out = []
    for t in r["turns"]:
        if t["type"] == "marker":
            out.append(t["u"])
        else:
            out += [s["u"] for s in t["steps"]]
    return out


@pytest.fixture(autouse=True)
def _fresh_cache():
    T._CACHE.clear()
    yield
    T._CACHE.clear()


# ---------- 并行工具:伪分叉 ----------

def _parallel(second_mid="m1"):
    return [
        human(1, None, "run two things"),
        asst(2, U(1), "m1", tool("t1")),
        asst(3, U(2), second_mid, tool("t2")),
        result(4, U(2), "t1"),
        result(5, U(3), "t2"),
        asst(6, U(4), "m2", text("both done")),
    ]


def test_parallel_tool_pseudo_fork_is_not_a_fork(tmp_path):
    write(tmp_path, _parallel())
    r = T.chain(SID, root=str(tmp_path))
    assert r["forks"] == 0
    # 叶子那一侧的上溯只经过 U4 → U2,U3 和 U5 是靠回复组补全拉进来的。
    assert path_uuids(r) == [U(1), U(2), U(3), U(4), U(5), U(6)]
    assert r["pathLen"] == 6


def test_the_same_shape_with_a_different_message_is_a_fork(tmp_path):
    """负对照:第二块不属于同一次回复时,同样的 1→2 形状必须被判成真分叉。
    否则上面那条可能只是「什么都不报分叉」。"""
    write(tmp_path, _parallel(second_mid="m9"))
    r = T.chain(SID, root=str(tmp_path))
    assert r["forks"] == 1


# ---------- 真分叉 ----------

def _branch():
    return [
        human(1, None, "first question"),
        asst(2, U(1), "m1", text("answer")),
        system(3, U(2)),
        human(4, U(3), "abandoned follow-up"),
        asst(5, U(4), "m2", text("abandoned answer")),
        human(6, U(3), "edited follow-up"),
        asst(7, U(6), "m3", text("kept answer")),
    ]


def test_real_branch_reports_alternatives(tmp_path):
    write(tmp_path, _branch())
    r = T.chain(SID, root=str(tmp_path))
    assert r["forks"] == 1
    assert r["leaf"] == U(7) and r["leafIsDefault"] is True
    forks = [f for t in r["turns"] for f in t["forks"]]
    assert len(forks) == 1 and forks[0]["u"] == U(3)
    alts = {a["u"]: a for a in forks[0]["alternatives"]}
    assert alts[U(4)] == {"u": U(4), "size": 2, "leaf": U(5), "leafLineIndex": 4,
                          "preview": "abandoned follow-up", "active": False}
    assert alts[U(6)]["active"] is True and alts[U(6)]["leaf"] == U(7)
    assert U(4) not in path_uuids(r)
    step = next(s for t in r["turns"] for s in t["steps"] if s["u"] == U(3))
    assert step["fork"] == 2


def test_choosing_the_other_leaf_switches_the_active_branch(tmp_path):
    write(tmp_path, _branch())
    r = T.chain(SID, leaf=U(5), root=str(tmp_path))
    assert r["leafIsDefault"] is False
    assert path_uuids(r) == [U(1), U(2), U(3), U(4), U(5)]
    alts = {a["u"]: a["active"] for t in r["turns"] for f in t["forks"] for a in f["alternatives"]}
    assert alts == {U(4): True, U(6): False}


def test_turns_split_at_each_human_and_every_node_is_one_step(tmp_path):
    write(tmp_path, [attach(1, None)] + [
        human(2, U(1), "q1"), asst(3, U(2), "m1", think("hmm")), asst(4, U(2), "m1", text("a1")),
        human(5, U(4), "q2"), asst(6, U(5), "m2", text("a2"))])
    r = T.chain(SID, root=str(tmp_path))
    turns = [t for t in r["turns"] if t["type"] == "turn"]
    assert [t["k"] for t in turns] == [0, 1, 2]
    assert turns[0]["human"] is None and [s["kind"] for s in turns[0]["steps"]] == ["attachment"]
    assert turns[1]["human"]["preview"] == "q1"
    assert turns[1]["counts"] == {"human": 1, "thinking": 1, "text": 1}
    ids = path_uuids(r)
    assert sorted(ids) == sorted(set(ids)) and len(ids) == r["pathLen"] == 6


def test_duplicate_uuid_rewrite_is_collapsed_not_a_fork(tmp_path):
    """同一 uuid 整行重写一遍(真实转录里出现过上百次,父节点相同)。
    两份都进孩子表的话父节点就「有两个孩子」,每一处都报成真分叉。"""
    recs = _parallel()
    recs.append(dict(recs[1]))    # U2 的完整复本,写在后面
    recs.append(asst(7, U(6), "m3", text("after the rewrite")))
    write(tmp_path, recs)
    r = T.chain(SID, root=str(tmp_path))
    assert r["duplicateUuids"] == 1
    assert r["forks"] == 0
    assert r["chainEntries"] == 7
    assert path_uuids(r).count(U(2)) == 1


# ---------- 压缩 ----------

def _compacted(lp, post_parent=None):
    """U1..U4 压缩前;U3,U4 被保留;B=U5;摘要 U6;U7 附件;U8 人;U9 助手。"""
    return [
        human(1, None, "old question"),
        asst(2, U(1), "m1", text("old answer")),
        human(3, U(2), "kept question"),
        asst(4, U(3), "m2", text("kept answer")),
        boundary(5, lp, U(6), [U(3), U(4)]),
        summary(6, U(5)),
        attach(7, U(6)),
        human(8, post_parent or U(7), "after compaction"),
        asst(9, U(8), "m3", text("fresh answer")),
    ]


def test_logical_parent_after_the_boundary_falls_back_to_preserved(tmp_path):
    """lp 指向边界之后(U7)。照它走会绕回边界,整段压缩前历史消失。

    边界正前方塞了一条不在保留列表里的孤立附件 U11:它让第二级(保留列表)和
    第三级(紧挨着的前一行)给出**不同**的答案。没有它两级答案相同,
    拆掉第二级这条用例照样绿, 投毒时实测过。"""
    recs = _compacted(lp=U(7))
    recs.insert(4, attach(11, None))
    write(tmp_path, recs)
    r = T.chain(SID, root=str(tmp_path))
    assert path_uuids(r) == [U(n) for n in range(1, 10)]
    assert r["compactions"] == 1
    kinds = [(t["type"], t.get("kind")) for t in r["turns"]]
    assert ("marker", "compact") in kinds and ("marker", "summary") in kinds
    m = next(t for t in r["turns"] if t.get("kind") == "compact")
    assert (m["trigger"], m["preTokens"], m["postTokens"]) == ("auto", 999000, 42000)
    assert r["warnings"] == []


def test_logical_parent_pointing_after_but_not_descendant_is_rejected(tmp_path):
    """lp 指向边界之后写下的一条孤立记录(不是边界的后代)。只有「写在 B 之前」这道闸
    挡得住它;后代检查对它无效。"""
    recs = _compacted(lp=U(12))
    recs.append(attach(12, None))
    write(tmp_path, recs)
    r = T.chain(SID, leaf=U(9), root=str(tmp_path))   # U12 是最后一行,默认叶子会是它
    assert path_uuids(r)[:4] == [U(1), U(2), U(3), U(4)]
    assert U(12) not in path_uuids(r)


def test_logical_parent_that_descends_from_the_boundary_is_rejected(tmp_path):
    """lp 指向一条写在 B 之前、却挂在 B 的摘要下面的记录(父指针向前引用)。
    「写在之前」这道闸放它过去,只有后代检查挡得住;不挡就绕回 B,压缩前历史丢失。"""
    recs = _compacted(lp=U(13))
    recs.insert(4, attach(13, U(6)))
    write(tmp_path, recs)
    r = T.chain(SID, root=str(tmp_path))
    assert path_uuids(r)[:4] == [U(1), U(2), U(3), U(4)]
    assert not any("环" in w for w in r["warnings"])


def test_logical_parent_before_the_boundary_is_used(tmp_path):
    """正对照:lp 合法时走 lp,不走保留列表。这里 lp=U2 而保留列表的末端是 U4,
    所以 U3/U4 不在链上;若实现无视 lp,U3/U4 会出现。"""
    write(tmp_path, _compacted(lp=U(2)))
    r = T.chain(SID, root=str(tmp_path))
    assert path_uuids(r) == [U(1), U(2), U(5), U(6), U(7), U(8), U(9)]


def test_no_usable_pointer_falls_back_to_the_previous_line(tmp_path):
    recs = _compacted(lp=U(99))
    recs[4]["compactMetadata"]["preservedMessages"] = {"anchorUuid": U(6), "uuids": [],
                                                       "allUuids": []}
    write(tmp_path, recs)
    r = T.chain(SID, root=str(tmp_path))
    assert path_uuids(r)[:4] == [U(1), U(2), U(3), U(4)]


# ---------- 孤根、坏行 ----------

def test_dangling_parent_is_an_orphan_root_not_an_error(tmp_path):
    write(tmp_path, [system(1, U(500), "away_summary"), human(2, U(1), "hello"),
                     asst(3, U(2), "m1", text("hi"))])
    r = T.chain(SID, root=str(tmp_path))
    assert r["danglingParents"] == 1
    assert path_uuids(r) == [U(1), U(2), U(3)]
    assert any("父节点不在文件里" in w for w in r["warnings"])


def test_unparseable_lines_are_counted_not_dropped(tmp_path):
    write(tmp_path, [human(1, None, "hello"), asst(2, U(1), "m1", text("hi"))],
          extra_lines=["{not json", "[1, 2]", "", '{"type":"mode","sessionId":"x"}'])
    r = T.chain(SID, root=str(tmp_path))
    assert r["badLines"] == 2            # 空行不算坏行,合法的非链行也不算
    assert r["lines"] == 6
    assert r["chainEntries"] == 2
    assert any("不是合法 JSON" in w for w in r["warnings"])


def test_titles_prefer_the_last_custom_title(tmp_path):
    write(tmp_path, [human(1, None, "hello")], extra_lines=[
        json.dumps({"type": "ai-title", "aiTitle": "Auto Name", "sessionId": SID}),
        json.dumps({"type": "custom-title", "customTitle": "Old Name", "sessionId": SID}),
        json.dumps({"type": "custom-title", "customTitle": "Example Session", "sessionId": SID})])
    assert T.chain(SID, root=str(tmp_path))["title"] == "Example Session"


# ---------- 定位与形状闸 ----------

@pytest.mark.parametrize("bad", ["../etc", "..", "11111111-1111-4111-8111-11111111111",
                                 "11111111-1111-4111-8111-111111111111/../x", "", None,
                                 "*", "11111111-1111-4111-8111-11111111111?"])
def test_non_uuid_ids_are_refused_before_the_filesystem(bad, monkeypatch):
    # 根目录没配:如果形状闸不在最前面,这里会得到 Unavailable/available:False 而不是 bad_id。
    monkeypatch.setenv("TASK_CONSOLE_SESSIONS", "")
    for fn in (lambda: T.locate(bad), lambda: T.chain(bad), lambda: T.node(bad, U(1)),
               lambda: T.export_md(bad, U(1)), lambda: T.fork(bad, U(1))):
        with pytest.raises(Refused) as ei:
            fn()
        assert ei.value.code == "bad_id"


@pytest.mark.parametrize("bad", ["../../x", "a/b", "a\\b", "x" * 81, "a.b"])
def test_traversal_subagent_ids_are_refused(tmp_path, bad):
    write(tmp_path, [human(1, None, "hello")])
    with pytest.raises(Refused) as ei:
        T.chain(SID, sub=bad, root=str(tmp_path))
    assert ei.value.code == "bad_sub"


def test_unset_root_is_not_checked(monkeypatch):
    # setenv 成空串而不是 delenv:这个变量没有默认落点,空串和没设走的是同一条「未检查」,
    # 而 delenv 这个写法一旦被抄到有默认落点的变量上,就会启用真机上的真实文件。
    monkeypatch.setenv("TASK_CONSOLE_SESSIONS", "")
    r = T.chain(SID)
    assert r["available"] is False and "TASK_CONSOLE_SESSIONS" in r["reason"]
    assert T.node(SID, U(1))["available"] is False
    with pytest.raises(Refused) as ei:
        T.fork(SID, U(1))
    assert ei.value.code == "unavailable"


def test_missing_and_ambiguous_sessions(tmp_path):
    with pytest.raises(Refused) as ei:
        T.chain(SID, root=str(tmp_path))
    assert ei.value.code == "not_found"
    write(tmp_path, [human(1, None, "a")], proj="proj-a")
    write(tmp_path, [human(1, None, "b")], proj="proj-b")
    with pytest.raises(Refused) as ei:
        T.chain(SID, root=str(tmp_path))
    assert ei.value.code == "ambiguous"


def test_leaf_not_in_file_is_refused(tmp_path):
    write(tmp_path, _branch())
    for bad in (U(404), "not-a-uuid"):
        with pytest.raises(Refused) as ei:
            T.chain(SID, leaf=bad, root=str(tmp_path))
        assert ei.value.code == "bad_leaf"


# ---------- 子代理 ----------

def _with_subagents(tmp_path):
    recs = [human(1, None, "delegate"),
            asst(2, U(1), "m1", tool("tA", name="Agent", description="look around")),
            result(3, U(2), "tA", agent="abc123"),
            asst(4, U(3), "m2", tool("tB", name="Agent", description="second")),
            result(5, U(4), "tB"),
            asst(6, U(5), "m3", text("done"))]
    write(tmp_path, recs)
    sd = tmp_path / "proj-a" / SID / "subagents"
    (sd / "workflows" / "wf_1").mkdir(parents=True)
    sub_recs = [rec(type="user", uuid=U(101), parentUuid=None, isSidechain=True, agentId="abc123",
                    timestamp=ts(1), message={"role": "user", "content": "sub task"}),
                rec(type="assistant", uuid=U(102), parentUuid=U(101), isSidechain=True,
                    timestamp=ts(2), message={"id": "s1", "content": [text("sub answer")]})]
    (sd / "agent-abc123.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in sub_recs), encoding="utf-8")
    (sd / "agent-abc123.meta.json").write_text(json.dumps(
        {"agentType": "general", "description": "look around", "toolUseId": "tA"}), encoding="utf-8")
    with gzip.open(sd / "workflows" / "wf_1" / "agent-def456.jsonl.gz", "wb") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in sub_recs).encode("utf-8"))
    # tB 的子代理只能靠 meta 的 toolUseId 连上(结果里没有 agentId)
    (sd / "workflows" / "wf_1" / "agent-def456.meta.json").write_text(
        json.dumps({"toolUseId": "tB"}), encoding="utf-8")


def test_subagents_are_listed_and_linked(tmp_path):
    _with_subagents(tmp_path)
    r = T.chain(SID, root=str(tmp_path))
    subs = {s["agentId"]: s for s in r["subagents"]}
    assert subs["abc123"]["toolUseId"] == "tA" and subs["abc123"]["description"] == "look around"
    assert subs["abc123"]["file"] == f"{SID}/subagents/agent-abc123.jsonl"
    assert subs["def456"]["gz"] is True
    steps = {s["u"]: s for t in r["turns"] for s in t["steps"]}
    assert steps[U(2)]["agentId"] == "abc123" and steps[U(2)]["agentFile"] is True
    assert steps[U(4)]["agentId"] == "def456"
    assert steps[U(3)]["name"] == "Agent"


def test_subagent_plain_and_gz_are_readable(tmp_path):
    _with_subagents(tmp_path)
    for sub in ("abc123", "def456"):
        r = T.chain(SID, sub=sub, root=str(tmp_path))
        assert r["sub"] == sub and path_uuids(r) == [U(101), U(102)]
        assert r["turns"][0]["human"]["preview"] == "sub task"
        n = T.node(SID, U(102), sub=sub, root=str(tmp_path))
        assert n["text"] == "sub answer"
        md = T.export_md(SID, U(102), sub=sub, root=str(tmp_path))
        assert "sub answer" in md["text"]
    with pytest.raises(Refused) as ei:
        T.chain(SID, sub="nope", root=str(tmp_path))
    assert ei.value.code == "not_found"


def test_subagent_fork_is_refused(tmp_path):
    _with_subagents(tmp_path)
    with pytest.raises(Refused) as ei:
        T.fork(SID, U(102), sub="abc123", root=str(tmp_path))
    assert ei.value.code == "no_sub_fork"


# ---------- node ----------

def test_node_reads_one_line_by_offset(tmp_path):
    write(tmp_path, _parallel())
    n = T.node(SID, U(2), root=str(tmp_path))
    assert n["kind"] == "tool" and n["tools"][0]["name"] == "Bash"
    assert json.loads(n["tools"][0]["input"]) == {"command": "echo hi"}
    assert n["messageId"] == "m1" and n["raw"] and n["truncated"] is False
    r = T.node(SID, U(4), root=str(tmp_path))
    assert r["results"] == [{"tool_use_id": "t1", "isError": False, "text": "ok"}]
    with pytest.raises(Refused) as ei:
        T.node(SID, U(404), root=str(tmp_path))
    assert ei.value.code == "bad_node"


def test_node_truncates_each_field_and_never_ships_a_huge_raw_line(tmp_path):
    big = "x" * (T.RAW_MAX + 10)
    write(tmp_path, [human(1, None, "q"), asst(2, U(1), "m1", text(big))])
    n = T.node(SID, U(2), root=str(tmp_path))
    assert len(n["text"]) == T.FIELD_MAX and n["truncated"] is True
    assert n["truncatedFields"] == ["text"]
    assert n["raw"] is None and n["rawTruncated"] is True


# ---------- export_md ----------

def test_export_range_and_rendering(tmp_path):
    recs = _compacted(lp=U(7))
    recs.insert(8, asst(10, U(8), "m3", think("private reasoning")))
    recs[-1] = asst(9, U(8), "m3", text("fresh answer"))
    write(tmp_path, recs)
    full = T.export_md(SID, U(9), root=str(tmp_path))
    t = full["text"]
    assert "### 用户 [" in t and "### Claude [" in t
    assert "⟂ 上下文在这里被压缩" in t and "<details><summary>压缩摘要</summary>" in t
    assert "synthetic summary of earlier work" in t
    assert "private reasoning" not in t
    assert full["turns"] == 3 and full["nodes"] == 10
    # 没有改过名时标题退到第一条真人消息,文件名跟着它走
    assert full["filename"] == f"old-question-{U(1)[:8]}-{U(9)[:8]}.md"
    part = T.export_md(SID, U(9), frm=U(8), include_thinking=True, root=str(tmp_path))
    assert "old answer" not in part["text"] and "private reasoning" in part["text"]
    assert part["nodes"] == 3


def test_export_tools_are_opt_in(tmp_path):
    write(tmp_path, _parallel())
    no = T.export_md(SID, U(6), root=str(tmp_path))["text"]
    yes = T.export_md(SID, U(6), include_tools=True, root=str(tmp_path))["text"]
    assert "🔧" not in no and "> 🔧 Bash: echo hi" in yes


def test_export_refuses_nodes_off_the_path_and_reversed_ranges(tmp_path):
    write(tmp_path, _branch())
    with pytest.raises(Refused) as ei:
        T.export_md(SID, U(5), root=str(tmp_path))      # 在被放弃的那一支上
    assert ei.value.code == "not_on_path"
    with pytest.raises(Refused) as ei:
        T.export_md(SID, U(7), frm=U(4), root=str(tmp_path))
    assert ei.value.code == "not_on_path"
    with pytest.raises(Refused) as ei:
        T.export_md(SID, U(2), frm=U(7), root=str(tmp_path))
    assert ei.value.code == "bad_range"
    # 正对照:同一个节点换一片叶子就在链上
    assert T.export_md(SID, U(5), leaf=U(5), root=str(tmp_path))["nodes"] == 5


def test_export_filename_is_ascii_safe(tmp_path):
    write(tmp_path, [human(1, None, "q")], extra_lines=[
        json.dumps({"type": "custom-title", "customTitle": "示例 / Demo: Plan!", "sessionId": SID})])
    fn = T.export_md(SID, U(1), root=str(tmp_path))["filename"]
    assert fn == f"demo-plan-{U(1)[:8]}-{U(1)[:8]}.md"
    assert fn.isascii()


# ---------- fork ----------

def _read(f):
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()]


def test_fork_splices_preserved_messages_after_the_summary(tmp_path):
    """压缩后第一条消息接在摘要上(Claude Code 的真实形状)。分叉要照加载时的重链拼:
    [边界, 摘要, 被保留消息, 其余]。压缩前没被保留的 U1/U2 不能进来。"""
    src = write(tmp_path, _compacted(lp=U(7)), extra_lines=[
        json.dumps({"type": "content-replacement", "sessionId": SID, "replacements": [{"k": 1}]}),
        json.dumps({"type": "content-replacement", "sessionId": SID, "replacements": [{"k": 2}]})])
    before = src.read_bytes()
    r = T.fork(SID, U(9), root=str(tmp_path))
    assert src.read_bytes() == before
    out = _read(tmp_path / "proj-a" / f"{r['newId']}.jsonl")
    chain_lines = [o for o in out if "uuid" in o]
    assert [o["uuid"] for o in chain_lines] == [U(5), U(6), U(3), U(4), U(7), U(8), U(9)]
    assert r["emitted"] == 7 and r["fromBoundary"] == U(5)
    assert r["lines"] == len(out) == 10
    # parentUuid 线性化
    prev = None
    for o in chain_lines:
        assert o["parentUuid"] == prev
        prev = o["uuid"]
    # 每一行的 sessionId 都换成了新的,不止文件名
    assert all(o["sessionId"] == r["newId"] for o in out)
    assert all(o["forkedFrom"] == {"sessionId": SID, "messageUuid": o["uuid"]} for o in chain_lines)
    assert all(o["isSidechain"] is False for o in chain_lines)
    tail = out[-3:]
    assert tail[0] == {"type": "content-replacement", "sessionId": r["newId"],
                       "replacements": [{"k": 1}, {"k": 2}]}
    assert tail[1]["type"] == "custom-title" and tail[1]["customTitle"].endswith(f"(fork @ {U(9)[:8]})")
    assert tail[2] == {"type": "last-prompt", "leafUuid": U(9), "explicit": True,
                       "sessionId": r["newId"]}
    assert r["cwd"] == CWD and r["command"] == f"cd '{CWD}'; claude --resume {r['newId']}"
    assert r["approxTokens"] > 0


def test_fork_stops_when_the_walk_crosses_into_preserved_messages(tmp_path):
    """另一种真实形状:压缩后第一条消息直接接在被保留消息的末端(U4)上。
    不拦的话上溯会顺着 U4 → U3 → U2 → U1 走进压缩前的全部历史。"""
    src = write(tmp_path, _compacted(lp=U(7), post_parent=U(4)))
    before = src.read_bytes()
    r = T.fork(SID, U(9), root=str(tmp_path))
    out = [o["uuid"] for o in _read(tmp_path / "proj-a" / f"{r['newId']}.jsonl") if "uuid" in o]
    assert U(1) not in out and U(2) not in out
    assert out == [U(5), U(6), U(3), U(4), U(8), U(9)]
    assert src.read_bytes() == before


def test_fork_before_any_compaction_keeps_the_raw_chain(tmp_path):
    src = write(tmp_path, _compacted(lp=U(7)))
    before = src.read_bytes()
    r = T.fork(SID, U(4), root=str(tmp_path))
    out = [o["uuid"] for o in _read(tmp_path / "proj-a" / f"{r['newId']}.jsonl") if "uuid" in o]
    assert out == [U(1), U(2), U(3), U(4)] and r["fromBoundary"] is None
    assert src.read_bytes() == before


def test_fork_at_a_tool_use_includes_its_results(tmp_path):
    src = write(tmp_path, _parallel())
    before = src.read_bytes()
    r = T.fork(SID, U(2), root=str(tmp_path))
    out = [o["uuid"] for o in _read(tmp_path / "proj-a" / f"{r['newId']}.jsonl") if "uuid" in o]
    assert out == [U(1), U(2), U(3), U(4), U(5)]
    assert r["leafUuid"] == U(5)
    assert src.read_bytes() == before


def test_fork_rewrites_snake_case_session_id_too(tmp_path):
    recs = [human(1, None, "q"), asst(2, U(1), "m1", text("a"))]
    recs[1]["session_id"] = SID
    write(tmp_path, recs)
    r = T.fork(SID, U(2), root=str(tmp_path))
    out = _read(tmp_path / "proj-a" / f"{r['newId']}.jsonl")
    assert out[1]["session_id"] == r["newId"]
    assert SID not in json.dumps([{k: v for k, v in o.items() if k != "forkedFrom"} for o in out])


def test_fork_refuses_nodes_off_the_path(tmp_path):
    write(tmp_path, _branch())
    with pytest.raises(Refused) as ei:
        T.fork(SID, U(5), root=str(tmp_path))
    assert ei.value.code == "not_on_path"
    with pytest.raises(Refused) as ei:
        T.fork(SID, "../../x", root=str(tmp_path))
    assert ei.value.code == "bad_uuid"
    # 只剩源文件,没有留下任何半截产物
    assert [p.name for p in (tmp_path / "proj-a").iterdir()] == [f"{SID}.jsonl"]


def test_fork_creates_the_target_exclusively(tmp_path, monkeypatch):
    src = write(tmp_path, _branch())
    before = src.read_bytes()
    fixed = "22222222-2222-4222-8222-222222222222"
    monkeypatch.setattr(T._uuid, "uuid4", lambda: fixed)
    target = tmp_path / "proj-a" / f"{fixed}.jsonl"
    target.write_bytes(b"pre-existing\n")
    with pytest.raises(Refused) as ei:
        T.fork(SID, U(7), root=str(tmp_path))
    assert ei.value.code == "exists"
    assert target.read_bytes() == b"pre-existing\n"
    names = sorted(p.name for p in (tmp_path / "proj-a").iterdir())
    assert names == sorted([f"{SID}.jsonl", f"{fixed}.jsonl"]), "临时文件没清掉"
    # 正对照:目标不存在时同一个 id 能写进去
    target.unlink()
    r = T.fork(SID, U(7), root=str(tmp_path))
    assert r["newId"] == fixed and target.is_file()
    assert src.read_bytes() == before


def test_big_fork_without_boundary_warns(tmp_path, monkeypatch):
    write(tmp_path, [human(1, None, "q"), asst(2, U(1), "m1", text("y" * 5000))])
    monkeypatch.setattr(T, "BIG_FORK_BYTES", 1000)
    r = T.fork(SID, U(2), root=str(tmp_path))
    assert any("超出模型上下文" in w for w in r["warnings"])


# ---------- 缓存与可序列化 ----------

def test_index_cache_follows_the_file(tmp_path):
    f = write(tmp_path, _branch())
    assert T.chain(SID, root=str(tmp_path))["cached"] is False
    assert T.chain(SID, root=str(tmp_path))["cached"] is True
    with open(f, "ab") as fh:
        fh.write((json.dumps(asst(8, U(7), "m4", text("more"))) + "\n").encode())
    r = T.chain(SID, root=str(tmp_path))
    assert r["cached"] is False and r["leaf"] == U(8)
    assert len(T._CACHE) <= T.CACHE_SLOTS


PUBLIC_API = {"chain", "node", "export_md", "fork", "locate", "shape"}


def test_the_public_api_is_exactly_the_known_set():
    """整类闸的入口:模块里每个公开函数都必须在下面那条 json.dumps 用例的名单里。
    新加一个公开函数而不加进名单,这条先红,而不是悄悄不被检查。"""
    import types
    found = {n for n, v in vars(T).items() if isinstance(v, types.FunctionType)
             and not n.startswith("_") and v.__module__ == T.__name__}
    assert found == PUBLIC_API


def test_every_public_return_survives_json_dumps(tmp_path):
    _with_subagents(tmp_path)
    root = str(tmp_path)
    loc = T.locate(SID, root=root)
    vals = {"chain": [T.chain(SID, root=root), T.chain(SID, sub="def456", root=root)],
            "node": [T.node(SID, U(2), root=root)],
            "export_md": [T.export_md(SID, U(6), include_tools=True, root=root)],
            "fork": [T.fork(SID, U(6), root=root)],
            "shape": [T.shape(SID, sub="abc123", leaf=U(1), u=U(2))]}
    assert set(vals) | {"locate"} == PUBLIC_API
    # locate 是内部定位器,返回 Path;它不直接进 HTTP 响应,这里钉住「它不进」这件事:
    # 四条路由的返回里没有任何一处是 locate 的原样返回。
    assert isinstance(loc["path"], os.PathLike)
    for name, vs in vals.items():
        for v in vs:
            json.dumps(v, ensure_ascii=False)


# ---------- 分叉:和 Claude Code 加载期重链逐节点对齐 ----------
# 下面几条每一条都是一个「分叉拼出了模型当时根本没看到的东西」的形状。
# 期望值不是凭感觉写的:是照 Wrr 的规则(uuids[0] 挂摘要,uuids[i] 挂 uuids[i-1],
# 摘要的其他孩子挂 uuids 最后一个)手工上溯一遍得到的。

def _fork_uuids(tmp_path, r):
    return [o["uuid"] for o in _read(tmp_path / "proj-a" / f"{r['newId']}.jsonl") if "uuid" in o]


def test_fork_after_rewinding_to_a_preserved_message_takes_only_its_prefix(tmp_path):
    """压缩后回退到被保留的 A2 再往下聊。Claude Code 从 H9 上溯是 H9 → A2 → 摘要 → 边界,
    被放弃的 H3/A4 不在里面。整段拼上被保留消息的话,它们会夹在 A2 和 H9 之间。"""
    recs = [human(1, None, "q1"), asst(2, U(1), "m1", text("a1")),
            human(3, U(2), "abandoned q"), asst(4, U(3), "m2", text("abandoned a")),
            boundary(5, U(4), U(6), [U(2), U(3), U(4)]), summary(6, U(5)),
            human(7, U(6), "after"), asst(8, U(7), "m3", text("after a")),
            human(9, U(2), "rewound q"), asst(10, U(9), "m4", text("rewound a"))]
    src = write(tmp_path, recs)
    before = src.read_bytes()
    r = T.fork(SID, U(10), root=str(tmp_path))
    assert _fork_uuids(tmp_path, r) == [U(5), U(6), U(2), U(9), U(10)]
    assert r["fromBoundary"] == U(5)
    # 边界的元数据跟着改成只列带上的那一段:否则加载期重链会去找不存在的 U3/U4。
    b = next(o for o in _read(tmp_path / "proj-a" / f"{r['newId']}.jsonl") if o.get("uuid") == U(5))
    pm = b["compactMetadata"]["preservedMessages"]
    assert pm["uuids"] == [U(2)] and pm["allUuids"] == [U(2)]
    assert src.read_bytes() == before


def test_fork_through_the_summary_keeps_the_whole_preserved_block_untouched(tmp_path):
    """正对照:从摘要那一侧走到边界时,被保留消息整段都在,元数据一个字不改。
    没有它,上面那条可能只是「永远只带第一条被保留消息」。"""
    write(tmp_path, _compacted(lp=U(7)))
    r = T.fork(SID, U(9), root=str(tmp_path))
    out = _read(tmp_path / "proj-a" / f"{r['newId']}.jsonl")
    b = next(o for o in out if o.get("uuid") == U(5))
    assert b["compactMetadata"]["preservedMessages"]["uuids"] == [U(3), U(4)]
    assert "forkTrimmedPreserved" not in b


def test_fork_does_not_move_a_pre_boundary_block_after_the_preserved_ones(tmp_path):
    """被保留段从一次回复的第二块开始(A3),第一块 A2 同 message.id 但不在保留列表里。
    给被保留段补回复组的话,A2 会被按行号排到 A5 之后、H8 之前。"""
    recs = [human(1, None, "q"), asst(2, U(1), "m1", think("t")), asst(3, U(2), "m1", text("a")),
            human(4, U(3), "q2"), asst(5, U(4), "m2", text("a2")),
            boundary(6, U(5), U(7), [U(3), U(4), U(5)]), summary(7, U(6)),
            human(8, U(7), "after"), asst(9, U(8), "m3", text("after a"))]
    write(tmp_path, recs)
    r = T.fork(SID, U(9), root=str(tmp_path))
    assert _fork_uuids(tmp_path, r) == [U(6), U(7), U(3), U(4), U(5), U(8), U(9)]


def test_fork_keeps_attachments_between_the_boundary_and_the_summary_in_place(tmp_path):
    """摘要不一定紧跟边界:中间可以隔几条附件,摘要挂在最后一条附件上。
    Claude Code 看到的是 边界, 附件, 附件, 摘要, 被保留…,附件不能被挪到被保留消息后面。"""
    recs = [human(1, None, "q"), asst(2, U(1), "m1", text("a")),
            boundary(3, U(2), U(6), [U(1), U(2)]), attach(4, U(3)), attach(5, U(4)),
            summary(6, U(5)), human(7, U(6), "after"), asst(8, U(7), "m2", text("after a"))]
    write(tmp_path, recs)
    r = T.fork(SID, U(8), root=str(tmp_path))
    assert _fork_uuids(tmp_path, r) == [U(3), U(4), U(5), U(6), U(1), U(2), U(7), U(8)]


def test_fork_stops_at_a_message_listed_only_in_all_uuids(tmp_path):
    """allUuids 是 uuids 的超集。边界之后的一条结果挂在只出现在 allUuids 里的 A4 上:
    只按 uuids 认的话,上溯穿过 A4 一路走进压缩前的全部历史。"""
    recs = [human(1, None, "old q"), asst(2, U(1), "m1", text("old a")),
            human(3, U(2), "kept q"), asst(4, U(3), "m2", tool("t1")),
            boundary(5, U(4), U(6), [U(3)], all_uuids=[U(3), U(4)]), summary(6, U(5)),
            result(7, U(4), "t1"), asst(8, U(7), "m3", text("done"))]
    write(tmp_path, recs)
    r = T.fork(SID, U(8), root=str(tmp_path))
    assert _fork_uuids(tmp_path, r) == [U(5), U(6), U(3), U(4), U(7), U(8)]
    assert r["fromBoundary"] == U(5)


def test_fork_drops_a_filled_in_tool_use_that_never_got_a_result(tmp_path):
    """同一次回复的第二块(tool_use)不是后面那条用户消息的祖先,文件里也没有它的结果。
    补进来的话,分叉里是一个紧跟着用户消息、没有结果的 tool_use。"""
    recs = [human(1, None, "q"), asst(2, U(1), "m1", think("t")), asst(3, U(2), "m1", tool("t1")),
            human(4, U(2), "next"), asst(5, U(4), "m2", text("a"))]
    write(tmp_path, recs)
    r = T.fork(SID, U(5), root=str(tmp_path))
    assert _fork_uuids(tmp_path, r) == [U(1), U(2), U(4), U(5)]


def test_fork_keeps_a_filled_in_tool_use_whose_result_is_there(tmp_path):
    """正对照:并行工具调用里补进来的第二块有结果,它必须留着。"""
    write(tmp_path, _parallel())
    r = T.fork(SID, U(6), root=str(tmp_path))
    assert _fork_uuids(tmp_path, r) == [U(1), U(2), U(3), U(4), U(5), U(6)]


# ---------- 分叉菜单:「当前」只有一个 ----------

def test_fork_menu_marks_exactly_one_branch_as_current(tmp_path):
    """分叉点是一条助手行,下面两条真分支,外加同一次回复里一个没下文的工具调用块。
    那一块靠回复组补全进了显示链,按显示链判「当前」的话它永远亮着,菜单里两个「当前」。"""
    recs = [human(1, None, "q"), asst(2, U(1), "m1", text("a")),
            asst(3, U(2), "m1", tool("t9")),
            human(4, U(2), "branch one"), asst(5, U(4), "m2", text("one")),
            human(6, U(2), "branch two"), asst(7, U(6), "m3", text("two"))]
    write(tmp_path, recs)
    r = T.chain(SID, root=str(tmp_path))
    fk = next(f for t in r["turns"] for f in t["forks"] if f["u"] == U(2))
    assert [(a["u"], a["active"]) for a in fk["alternatives"]] == [(U(4), False), (U(6), True)]


# ---------- 还在写的会话 ----------

def test_an_unterminated_last_line_is_an_incomplete_tail_not_a_bad_line(tmp_path):
    f = write(tmp_path, [human(1, None, "hello"), asst(2, U(1), "m1", text("hi"))])
    with open(f, "ab") as fh:
        fh.write(b'{"type":"user","uuid":"' + U(3).encode() + b'","parentUu')
    r = T.chain(SID, root=str(tmp_path))
    assert r["badLines"] == 0 and r["incompleteTail"] is True
    assert not any("不是合法 JSON" in w for w in r["warnings"])
    assert any("还没写完" in w for w in r["warnings"])


def test_the_same_fragment_with_a_newline_is_a_bad_line(tmp_path):
    """负对照:同样的半截内容一旦带着换行,它就不是「正在写」,而是真坏了。"""
    write(tmp_path, [human(1, None, "hello")],
          extra_lines=['{"type":"user","uuid":"' + U(3) + '","parentUu'])
    r = T.chain(SID, root=str(tmp_path))
    assert r["badLines"] == 1 and r["incompleteTail"] is False


# ---------- 第三级前驱是猜的,要说出来 ----------

def test_guessed_predecessor_is_reported(tmp_path):
    recs = _compacted(lp=U(99))
    recs[4]["compactMetadata"]["preservedMessages"] = {"anchorUuid": U(6), "uuids": [],
                                                       "allUuids": []}
    write(tmp_path, recs)
    r = T.chain(SID, root=str(tmp_path))
    assert any("没有可用的前驱指针" in w and U(5)[:8] in w for w in r["warnings"])


def test_a_real_predecessor_is_not_reported_as_guessed(tmp_path):
    write(tmp_path, _compacted(lp=U(2)))
    r = T.chain(SID, root=str(tmp_path))
    assert not any("前驱指针" in w for w in r["warnings"])


# ---------- 写的位置和给人粘贴的命令 ----------

def test_relative_root_is_refused(tmp_path, monkeypatch):
    write(tmp_path, [human(1, None, "q")])
    monkeypatch.chdir(tmp_path)
    r = T.chain(SID, root=".")
    assert r["available"] is False and "绝对路径" in r["reason"]
    with pytest.raises(Refused) as ei:
        T.fork(SID, U(1), root=".")
    assert ei.value.code == "unavailable"


def test_fork_refuses_to_write_inside_a_git_worktree(tmp_path):
    """会话根目录落在一个仓里时,分叉写出的真实对话会成为那个仓的未跟踪文件。"""
    repo = tmp_path / "some-repo"
    (repo / ".git").mkdir(parents=True)
    root = repo / "sessions"
    src = write(root, [human(1, None, "q"), asst(2, U(1), "m1", text("a"))])
    before = src.read_bytes()
    with pytest.raises(Refused) as ei:
        T.fork(SID, U(2), root=str(root))
    assert ei.value.code == "inside_repo"
    assert [p.name for p in (root / "proj-a").iterdir()] == [f"{SID}.jsonl"]
    assert src.read_bytes() == before


def test_fork_outside_any_worktree_is_allowed(tmp_path):
    """正对照:同样的布局,只是上面没有 .git。"""
    root = tmp_path / "not-a-repo" / "sessions"
    write(root, [human(1, None, "q"), asst(2, U(1), "m1", text("a"))])
    assert T.fork(SID, U(2), root=str(root))["newId"]


@pytest.mark.parametrize("cwd", ["C:/work/a$(calc)b", "C:/work/a`b", "C:/work/it's",
                                 "C:/work/a\nb"])
def test_resume_command_never_embeds_a_hostile_cwd(tmp_path, cwd):
    recs = [human(1, None, "q"), asst(2, U(1), "m1", text("a"))]
    for x in recs:
        x["cwd"] = cwd
    write(tmp_path, recs)
    r = T.fork(SID, U(2), root=str(tmp_path))
    assert r["command"] == f"claude --resume {r['newId']}"
    assert any("没有把 cd 拼进命令" in w for w in r["warnings"])


def test_resume_command_quotes_an_ordinary_cwd_literally(tmp_path):
    """正对照:普通目录(含空格和括号)照样拼进单引号里的 cd。"""
    recs = [human(1, None, "q"), asst(2, U(1), "m1", text("a"))]
    for x in recs:
        x["cwd"] = "C:/work/My Project (x)"
    write(tmp_path, recs)
    r = T.fork(SID, U(2), root=str(tmp_path))
    assert r["command"] == f"cd 'C:/work/My Project (x)'; claude --resume {r['newId']}"


# ---------- 读的边界 ----------

def test_subagent_meta_behind_a_junction_out_of_the_root_is_not_read(tmp_path):
    _winapi = pytest.importorskip("_winapi")
    root = tmp_path / "root"
    write(root, [human(1, None, "q")])
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "agent-zz.jsonl").write_text(json.dumps(human(1, None, "x")) + "\n", encoding="utf-8")
    (outside / "agent-zz.meta.json").write_text(json.dumps({"description": "OUTSIDE-ROOT"}),
                                                encoding="utf-8")
    wf = root / "proj-a" / SID / "subagents" / "workflows"
    wf.mkdir(parents=True)
    try:
        _winapi.CreateJunction(str(outside), str(wf / "w"))
    except (OSError, AttributeError) as e:
        pytest.skip(f"建不了目录联接: {e}")
    r = T.chain(SID, root=str(root))
    assert "OUTSIDE-ROOT" not in json.dumps(r, ensure_ascii=False)
    # 根外那份转录本身也不进清单:列出来就是一个点开必然 outside_root 的链接。
    assert "zz" not in {s["agentId"] for s in r["subagents"]}
    assert r["subagentMetaUnreadable"] >= 1
    # 正对照:同一份 meta 放在根里就读得到。
    inside = root / "proj-a" / SID / "subagents"
    (inside / "agent-yy.jsonl").write_text(json.dumps(human(1, None, "x")) + "\n", encoding="utf-8")
    (inside / "agent-yy.meta.json").write_text(json.dumps({"description": "INSIDE"}), encoding="utf-8")
    T._CACHE.clear()
    assert "INSIDE" in json.dumps(T.chain(SID, root=str(root)), ensure_ascii=False)


def test_a_symlinked_subagent_file_gets_the_full_containment_check(tmp_path, monkeypatch):
    """目录归属按目录缓存之后,能单独把一个文件带出根目录的只剩文件级符号链接。
    这条钉住「是链接就退回逐个完整检查」。

    这台机器上没有提权建不了符号链接(WinError 1314),一条会被跳过的用例什么都不证明,
    所以把「这个文件是链接」和「它解析到根外」换成打桩:被测的是分支,不是操作系统。"""
    root = tmp_path / "root"
    write(root, [human(1, None, "q")])
    sd = root / "proj-a" / SID / "subagents"
    sd.mkdir(parents=True)
    for a in ("zz", "yy"):
        (sd / f"agent-{a}.jsonl").write_text(json.dumps(human(1, None, "x")) + "\n", encoding="utf-8")
    real_is_symlink, real_inside = T.Path.is_symlink, T._inside
    full = []
    monkeypatch.setattr(T.Path, "is_symlink",
                        lambda self: self.name == "agent-zz.jsonl" or real_is_symlink(self))

    def inside(base, p):
        full.append(p.name)
        return False if p.name == "agent-zz.jsonl" else real_inside(base, p)
    monkeypatch.setattr(T, "_inside", inside)
    r = T.chain(SID, root=str(root))
    assert {s["agentId"] for s in r["subagents"]} == {"yy"}
    assert r["subagentMetaUnreadable"] == 1
    # 目录只解析一次,普通文件不再逐个解析
    assert full.count("subagents") == 1 and "agent-yy.jsonl" not in full


def test_node_response_has_a_total_budget_across_fields(tmp_path, monkeypatch):
    blocks = [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "r" * 100} for i in range(3)]
    write(tmp_path, [human(1, None, "q"),
                     rec(type="user", uuid=U(2), parentUuid=U(1), timestamp=ts(2),
                         message={"role": "user", "content": blocks})])
    monkeypatch.setattr(T, "RESPONSE_MAX", 150)
    n = T.node(SID, U(2), root=str(tmp_path))
    assert len(n["results"]) == 2 and "omitted" in n["truncatedFields"] and n["truncated"]
    monkeypatch.setattr(T, "RESPONSE_MAX", 10_000)
    assert len(T.node(SID, U(2), root=str(tmp_path))["results"]) == 3


def test_export_times_are_local_like_the_page(tmp_path):
    from datetime import datetime
    write(tmp_path, [human(1, None, "q"), asst(2, U(1), "m1", text("a"))])
    t = T.export_md(SID, U(2), root=str(tmp_path))["text"]
    want = datetime.fromisoformat(ts(1).replace("Z", "+00:00")).astimezone()
    assert f"### 用户 [{want.strftime('%Y-%m-%d %H:%M:%S %z')}]" in t
    assert ts(1) not in t


# ---------- 两套编号:物理行号 vs 字节偏移 ----------

def test_line_index_is_the_physical_line_even_with_blank_and_bad_lines(tmp_path):
    """链条目前面塞空行和坏行:物理行号和「第几个链条目」从此分家。
    只用干净 fixture 的测试里这两个数恰好相等,换错了也看不出来。"""
    d = tmp_path / "proj-a"
    d.mkdir()
    f = d / f"{SID}.jsonl"
    body = ("\n" + "{broken\n" + json.dumps(human(1, None, "q")) + "\n\n"
            + json.dumps(asst(2, U(1), "m1", text("a"))) + "\n")
    f.write_bytes(body.encode("utf-8"))
    r = T.chain(SID, root=str(tmp_path))
    steps = {s["u"]: s for t in r["turns"] for s in t["steps"]}
    assert steps[U(1)]["lineIndex"] == 2 and steps[U(2)]["lineIndex"] == 4
    assert "line" not in steps[U(1)], "一个叫 line 的字段说不清自己是哪一套编号"
    n = T.node(SID, U(2), root=str(tmp_path))
    raw = f.read_bytes()
    assert n["lineIndex"] == 4
    assert raw[n["byteOffset"]: n["byteOffset"] + n["byteLength"]].rstrip(b"\n") == \
        json.dumps(asst(2, U(1), "m1", text("a"))).encode("utf-8")
    assert raw.splitlines()[n["lineIndex"]] == raw[n["byteOffset"]:].splitlines()[0]


# ---------- 形状闸:一份规则,码各自钉死 ----------

@pytest.mark.parametrize("kw,code", [
    ({"sid": "x"}, "bad_id"),
    ({"sid": SID, "sub": "a/b"}, "bad_sub"),
    ({"sid": SID, "leaf": "../x"}, "bad_leaf"),
    ({"sid": SID, "u": "..\\x"}, "bad_uuid"),
    ({"sid": SID, "to": "*"}, "bad_uuid"),
    ({"sid": SID, "at": 5}, "bad_uuid"),
    ({"sid": SID, "required": ("to",), "to": None}, "bad_uuid"),
    ({"sid": SID, "required": ("u",)}, "bad_uuid"),
])
def test_shape_names_the_exact_gate(kw, code):
    with pytest.raises(Refused) as ei:
        T.shape(**kw)
    assert ei.value.code == code


@pytest.mark.parametrize("kw", [
    {"sid": SID},
    {"sid": SID.upper(), "sub": "a1_b-2", "leaf": U(1), "u": U(2)},
    {"sid": SID, "sub": "", "leaf": None, "to": "", "frm": None},
    {"sid": SID, "sub": "x" * 80},
    {"sid": SID, "required": ("to",), "to": U(3), "frm": None},
])
def test_shape_lets_the_legitimate_forms_through(kw):
    """放行样本:一道会误报的闸等于被关掉的闸。"""
    assert T.shape(**kw) is None


def test_bad_leaf_shape_is_refused_before_the_filesystem(monkeypatch):
    """leaf 的形状以前在建完索引之后才判,等于先把整份转录读一遍再说不对。"""
    def spy(*a, **k):
        raise AssertionError("形状不对的 leaf 走到了文件系统")
    monkeypatch.setattr(T, "locate", spy)
    for fn in (lambda: T.chain(SID, leaf="../x"), lambda: T.export_md(SID, U(1), leaf="../x"),
               lambda: T.fork(SID, U(1), leaf="../x")):
        with pytest.raises(Refused) as ei:
            fn()
        assert ei.value.code == "bad_leaf"
