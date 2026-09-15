"""llmstats 的用例。全部用合成 fixture,一条真实账本记录都不进这个文件。

每一道闸门都配一个**能让它失败**的对照,因为这个模块防的全是「绿得很好看」的
失败:一个丢行的解析器、一个把无 ts 记录算进窗口的时间窗、一个把 skipped 混进
failed 的链路表、一个部分写入的配置写入器,它们出错时都不会抛异常。

所以这里的断言形状是成对的:
- 坏行被计数 —— 对照:同样的坏行**不能**出现在返回的记录里,且恒等式要成立
- 无 ts 被排除 —— 对照:全无 ts 的账本得到的是 `0 条 + 排除 N 条`,不是 `N 条`
- skipped ≠ failed —— 对照:构造一条只可能命中 skipped 的记录,断言 failed 为 0
- 名字闸整批拒绝 —— 对照:拒绝之后去**读文件内容**,断言旧内容原样还在
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "scripts", "task_console"))

import llmstats  # noqa: E402


CHAIN = ["codexg", "codex", "cc", "claude"]
NEWLINE = chr(10)


# --------------------------------------------------------------------------
# 合成记录工厂。真实账本里的每一条都不许进这个文件,所以形状在这里手工复刻。
# --------------------------------------------------------------------------

def ok_rec(provider, chain=None, attempts=None, ms=1000, ts=None, mode="judge",
           prompt_chars=100, reply_chars=20, web=False, rid=None):
    chain = list(chain or CHAIN)
    if attempts is None:
        attempts = chain.index(provider) + 1
    r = {"provider": provider, "chain": chain, "mode": mode, "web": web,
         "prompt_chars": prompt_chars, "reply_chars": reply_chars,
         "ms": ms, "attempts": attempts, "ok": True}
    if ts is not None:
        r["ts"] = ts
    if rid is not None:
        r["id"] = rid
    return r


def fail_rec(chain=None, attempts=None, ts=None, mode="judge", error="chain budget exhausted"):
    chain = list(chain or CHAIN)
    r = {"provider": None, "chain": chain, "mode": mode, "web": False,
         "prompt_chars": 100, "reply_chars": 0,
         "attempts": len(chain) if attempts is None else attempts,
         "ok": False, "error": error}
    if ts is not None:
        r["ts"] = ts
    return r


def write_ledger(tmp_path, monkeypatch, lines):
    """把若干行(dict 直接 dump,str 原样写)落成账本并指过去。"""
    p = tmp_path / "ledger.jsonl"
    with p.open("w", encoding="utf-8", newline=NEWLINE) as f:
        for ln in lines:
            f.write((json.dumps(ln, ensure_ascii=False) if isinstance(ln, dict) else ln) + NEWLINE)
    monkeypatch.setenv(llmstats.ENV_LEDGER, str(p))
    return p


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """每个用例都跑在自己的目录里,并且把链相关的环境变量清干净。

    不清 `LLMCALL_CHAIN` 的话,开发机上恰好设了这个变量时整批链配置用例会
    测到另一个东西 —— 而且是全绿地测到。
    """
    monkeypatch.delenv(llmstats.ENV_CHAIN, raising=False)
    # 开发机上真的设了这两个时,整批伴生仓用例会测到真机的目录 —— 而且是全绿地测到。
    monkeypatch.delenv(llmstats.ENV_LLMCALL_DATA, raising=False)
    monkeypatch.delenv(llmstats.ENV_LLMCALL_CONFIG, raising=False)
    monkeypatch.setenv(llmstats.ENV_CHAIN_FILE, str(tmp_path / "chain.txt"))
    monkeypatch.setenv(llmstats.ENV_LEDGER, str(tmp_path / "ledger.jsonl"))


# --------------------------------------------------------------------------
# ledger_path
# --------------------------------------------------------------------------

def test_ledger_path_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(llmstats.ENV_LEDGER, str(tmp_path / "x.jsonl"))
    assert llmstats.ledger_path() == tmp_path / "x.jsonl"


def test_ledger_path_default_is_home_llmcall(monkeypatch):
    """对照:没设环境变量时**不能**还是 tmp 那个值,必须落到默认位置。"""
    monkeypatch.delenv(llmstats.ENV_LEDGER, raising=False)
    p = llmstats.ledger_path()
    assert p.name == "ledger.jsonl"
    assert p.parent.name == ".llmcall"


def test_ledger_path_does_not_raise_when_missing(tmp_path, monkeypatch):
    """找不到不许抛:存在性是调用方要显示的事实。"""
    monkeypatch.setenv(llmstats.ENV_LEDGER, str(tmp_path / "nope" / "ledger.jsonl"))
    assert llmstats.ledger_path().name == "ledger.jsonl"


# --------------------------------------------------------------------------
# read:坏行必须被计数,不许静默跳过
# --------------------------------------------------------------------------

def test_read_counts_malformed_and_does_not_hide_them(tmp_path, monkeypatch):
    write_ledger(tmp_path, monkeypatch, [
        ok_rec("codexg"),
        "{ 这不是 JSON",
        ok_rec("cc"),
        "[1, 2, 3]",          # 合法 JSON,但不是一条记录
        "",                   # 空行
        fail_rec(),
    ])
    recs, meta = llmstats.read()

    assert meta["parsed"] == 3
    assert meta["malformed"] == 2
    assert meta["blank"] == 1
    # 恒等式:页面可以拿它当场验算「有没有行被吞掉」
    assert meta["parsed"] + meta["malformed"] + meta["blank"] == meta["lines"]
    # 负对照:坏行绝不能混进结果里被当成记录
    assert len(recs) == 3
    assert all(isinstance(r, dict) and "chain" in r for r in recs)


def test_read_malformed_only_ledger_is_not_reported_as_empty(tmp_path, monkeypatch):
    """负对照:一个全是坏行的账本,和一个空账本,必须打印得不一样。"""
    write_ledger(tmp_path, monkeypatch, ["nope", "also nope", "{{{"])
    recs, meta = llmstats.read()
    assert recs == []
    assert meta["parsed"] == 0
    assert meta["malformed"] == 3      # 不是 0
    assert meta["lines"] == 3


def test_read_absolute_index_survives_bad_lines(tmp_path, monkeypatch):
    """`_i` 是物理行号:中间夹一条坏行时,后面的记录不能整体错位一格。"""
    write_ledger(tmp_path, monkeypatch, [
        ok_rec("codexg"), "garbage", ok_rec("cc"),
    ])
    recs, _ = llmstats.read()
    assert [r[llmstats.INDEX_KEY] for r in recs] == [1, 3]


def test_read_limit_keeps_tail_but_not_the_counts(tmp_path, monkeypatch):
    """limit 只截返回值,**不截统计**。两者一起变小就看不出被截过。"""
    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg", reply_chars=i) for i in range(10)])
    recs, meta = llmstats.read(limit=3)
    assert len(recs) == 3
    assert [r["reply_chars"] for r in recs] == [7, 8, 9]   # 保留的是最近的
    assert meta["parsed"] == 10                            # 统计仍是全量
    assert meta["returned"] == 3
    assert meta["truncated"] is True


def test_read_not_truncated_when_limit_is_generous(tmp_path, monkeypatch):
    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg")])
    _, meta = llmstats.read(limit=100)
    assert meta["truncated"] is False


def test_read_missing_file_is_not_an_error_but_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv(llmstats.ENV_LEDGER, str(tmp_path / "nope.jsonl"))
    recs, meta = llmstats.read()
    assert recs == []
    assert meta["exists"] is False
    assert meta["read_error"] is None      # 没装 ≠ 出错
    assert meta["lines"] == 0


def test_read_stamped_and_unstamped_are_counted_separately(tmp_path, monkeypatch):
    now = 1_700_000_000.0
    write_ledger(tmp_path, monkeypatch, [
        ok_rec("codexg"),                 # 老记录,没有 ts
        ok_rec("cc", ts=now),
        ok_rec("cc", ts=now + 60),
    ])
    _, meta = llmstats.read()
    assert meta["stamped"] == 2
    assert meta["unstamped"] == 1
    assert meta["first_ts"] == now
    assert meta["last_ts"] == now + 60


def test_read_all_unstamped_ledger_has_no_ts_range(tmp_path, monkeypatch):
    """负对照:全无 ts 时 first/last 必须是 None,不能是 0(0 会被画成 1970 年)。"""
    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg"), ok_rec("cc")])
    _, meta = llmstats.read()
    assert meta["first_ts"] is None and meta["last_ts"] is None
    assert meta["unstamped"] == 2


def test_read_rejects_negative_limit(tmp_path, monkeypatch):
    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg")])
    with pytest.raises(ValueError):
        llmstats.read(limit=-1)


# --------------------------------------------------------------------------
# aggregate:calls 为 0 时 cheap_share 必须是 None
# --------------------------------------------------------------------------

def test_aggregate_basic_counts():
    recs = [ok_rec("codexg"), ok_rec("codexg"), ok_rec("cc"), fail_rec()]
    a = llmstats.aggregate(recs)
    assert a["calls"] == 4
    assert a["ok"] == 3
    assert a["failed"] == 1
    assert a["served"] == {"codexg": 2, "cc": 1, llmstats.NONE_SERVED: 1}
    assert a["cheap_share"] == pytest.approx(0.5)


def test_aggregate_empty_cheap_share_is_none_not_zero():
    """负对照:空账本的 cheap_share 是 None。0 会被读成「便宜档一次都没用上」。"""
    a = llmstats.aggregate([])
    assert a["cheap_share"] is None
    assert a["calls"] == 0
    assert a["avg_ms"] is None
    assert a["prompt_chars"]["avg"] is None


def test_aggregate_all_expensive_cheap_share_is_really_zero():
    """对照面:真的一次便宜档都没用上时,它必须是 0.0 而不是 None。"""
    a = llmstats.aggregate([ok_rec("cc"), ok_rec("claude")])
    assert a["cheap_share"] == 0.0


def test_aggregate_avg_ms_only_over_records_that_have_ms():
    """老失败记录没有 ms。拿 calls 当分母会把均值系统性压低。"""
    recs = [ok_rec("codexg", ms=1000), ok_rec("codexg", ms=3000), fail_rec()]
    a = llmstats.aggregate(recs)
    assert a["ms_samples"] == 2
    assert a["avg_ms"] == pytest.approx(2000.0)   # 不是 4000/3


def test_aggregate_web_true_is_not_counted_as_a_number():
    """负对照:`web: true` 不许被当成 1 混进任何数值统计。"""
    a = llmstats.aggregate([ok_rec("codexg", web=True, prompt_chars=10)])
    assert a["web_calls"] == 1
    assert a["prompt_chars"]["total"] == 10


# --------------------------------------------------------------------------
# window:无 ts 必须被排除且单独计数
# --------------------------------------------------------------------------

def test_window_all_unstamped_yields_zero_in_window_and_n_excluded():
    """铁律的正面用例:全无 ts 的账本得到 `0 条 + 排除 N 条`,不是 `N 条`。"""
    now = 1_700_000_000.0
    recs = [ok_rec("codexg") for _ in range(5)]
    inside, excluded, outside = llmstats.window(recs, hours=24, now=now)
    assert inside == []
    assert excluded == 5
    assert outside == 0


def test_window_excluded_is_not_folded_into_out_of_window():
    """负对照:「没有时间戳」和「时间戳在窗外」是两个数,不许合并。

    合并之后,一份全无 ts 的账本和一份全是陈年记录的账本会打印成同一块板子,
    而前者要去修写侧,后者不用。
    """
    now = 1_700_000_000.0
    recs = [ok_rec("codexg"), ok_rec("cc", ts=now - 10 * 86400)]
    inside, excluded, outside = llmstats.window(recs, hours=24, now=now)
    assert (len(inside), excluded, outside) == (0, 1, 1)


def test_window_picks_only_records_inside():
    now = 1_700_000_000.0
    recs = [
        ok_rec("codexg", ts=now - 100),        # 窗内
        ok_rec("cc", ts=now - 3600 * 5),       # 窗外(超过 1 小时)
        ok_rec("cc", ts=now + 3600),           # 未来:也算窗外
        ok_rec("claude"),                      # 无 ts
    ]
    inside, excluded, outside = llmstats.window(recs, hours=1, now=now)
    assert [r["provider"] for r in inside] == ["codexg"]
    assert excluded == 1
    assert outside == 2


def test_window_rejects_nonpositive_hours():
    with pytest.raises(ValueError):
        llmstats.window([], hours=0)
    with pytest.raises(ValueError):
        llmstats.window([], hours=-1)


# --------------------------------------------------------------------------
# rungs:served / failed / skipped 是三件事
# --------------------------------------------------------------------------

def _by_name(rows):
    return {r["name"]: r for r in rows}


def test_rungs_skipped_is_not_failed():
    """核心负对照:构造只可能命中 skipped 的记录,断言 failed 恰好是 0。

    这两条记录的 chain 都不含 codexg(它压根没上场)。如果实现把「不在链里」
    误算成「试过没成」,codexg 的 failed 会是 2 —— 而那两个数会被读成完全
    相反的结论:一个是「便宜档坏了」,一个是「便宜档没被启用」。
    """
    recs = [ok_rec("cc", chain=["codex", "cc", "claude"]),
            ok_rec("cc", chain=["codex", "cc", "claude"])]
    row = _by_name(llmstats.rungs(recs, CHAIN))["codexg"]
    assert row["skipped"] == 2
    assert row["failed"] == 0
    assert row["served"] == 0
    assert row["never_in_chain"] is True


def test_rungs_failed_is_not_skipped():
    """反向对照:codexg 在链里、被试过、没成,此时 skipped 必须是 0。"""
    recs = [ok_rec("cc", chain=CHAIN, attempts=3)]     # codexg 与 codex 都试过了
    rows = _by_name(llmstats.rungs(recs, CHAIN))
    assert rows["codexg"]["failed"] == 1
    assert rows["codexg"]["skipped"] == 0
    assert rows["codex"]["failed"] == 1
    assert rows["cc"]["served"] == 1
    # claude 在链里但没轮到 —— 既不是 failed 也不是 skipped
    assert rows["claude"]["not_reached"] == 1
    assert rows["claude"]["failed"] == 0
    assert rows["claude"]["skipped"] == 0


def test_rungs_total_failure_marks_every_tried_rung_failed():
    rows = _by_name(llmstats.rungs([fail_rec(chain=CHAIN, attempts=4)], CHAIN))
    assert [rows[n]["failed"] for n in CHAIN] == [1, 1, 1, 1]
    assert sum(rows[n]["served"] for n in CHAIN) == 0


def test_rungs_partial_failure_does_not_blame_unreached_rungs():
    """预算在第二级就用完:后两级没轮到,不许记成 failed。"""
    rows = _by_name(llmstats.rungs([fail_rec(chain=CHAIN, attempts=2)], CHAIN))
    assert rows["codexg"]["failed"] == 1 and rows["codex"]["failed"] == 1
    assert rows["cc"]["failed"] == 0 and rows["cc"]["not_reached"] == 1
    assert rows["claude"]["failed"] == 0 and rows["claude"]["not_reached"] == 1


def test_rungs_unknown_provider_is_reported_not_dropped():
    """负对照:链里出现表外的名字时,它必须出现在结果里。

    悄悄丢掉一个不认识的 provider,和账本里真的没有它,打印出来一模一样。
    """
    recs = [ok_rec("mystery", chain=["mystery", "claude"])]
    rows = _by_name(llmstats.rungs(recs, CHAIN))
    assert "mystery" in rows
    assert rows["mystery"]["served"] == 1
    assert rows["mystery"]["in_known_chain"] is False
    assert rows["codexg"]["in_known_chain"] is True


def test_rungs_broken_attempts_still_counts_skipped():
    """attempts 坏了不影响「它不在这条链里」这个仍然确定的事实。"""
    bad = ok_rec("cc", chain=["cc", "claude"])
    bad["attempts"] = 99                      # 越界
    rows = _by_name(llmstats.rungs([bad], CHAIN))
    assert rows["codexg"]["skipped"] == 1
    assert rows["cc"]["served"] == 0          # 判不了就不判,不许猜
    assert llmstats.verify_rungs([bad])["unusable"] == 1


def test_verify_rungs_detects_a_poisoned_record():
    """闸门自己的负对照:判据不成立时 contradictions 必须变成非零。

    rungs 的每一个数都建在 `chain[attempts-1] == provider` 上。这条判据一旦
    在某批数据上不成立,那张表照样会打印得很漂亮,所以必须能被投毒测出来。
    """
    good = [ok_rec("cc", chain=CHAIN, attempts=3)]
    assert llmstats.verify_rungs(good)["contradictions"] == 0

    poisoned = dict(good[0])
    poisoned["provider"] = "claude"           # 和 chain[2] 对不上
    v = llmstats.verify_rungs([poisoned])
    assert v["contradictions"] == 1
    assert v["samples"]


# --------------------------------------------------------------------------
# runs:连续降级段
# --------------------------------------------------------------------------

def _seq(recs):
    for i, r in enumerate(recs, start=1):
        r[llmstats.INDEX_KEY] = i
    return recs


def test_runs_finds_a_contiguous_degraded_segment():
    recs = _seq([ok_rec("codexg")] * 0 + [
        ok_rec("codexg"), ok_rec("codexg"),
        ok_rec("cc", attempts=3), ok_rec("cc", attempts=3),
        ok_rec("cc", attempts=3), ok_rec("cc", attempts=3),
        ok_rec("codexg"),
    ])
    out = llmstats.runs(recs, min_len=3)
    assert len(out) == 1
    seg = out[0]
    assert seg["provider"] == "cc"
    assert seg["length"] == 4
    assert (seg["start_i"], seg["end_i"]) == (3, 6)
    assert (seg["start_offset"], seg["end_offset"]) == (2, 5)   # 下标比行号小 1


def test_runs_ignores_the_chain_head():
    """负对照:链首连续应答是正常状态。把它算成降级段,那张图就永远是满的。"""
    recs = _seq([ok_rec("codexg") for _ in range(50)])
    assert llmstats.runs(recs, min_len=3) == []


def test_runs_head_is_judged_per_record_not_by_a_global_set():
    """核心负对照:链首必须按**每条记录自己的链**判,不能用全局链首集合。

    这份账本里 codexg / codex / cc 都当过某条链的第一级。拿这三个名字组成的
    集合去判「是不是链首」,下面这四条 attempts=3 的降级调用会被当成正常,
    于是最要紧的那一段被静默吃掉 —— 而剩下的段看起来毫无异样。
    """
    recs = _seq(
        [ok_rec("cc", chain=["cc", "claude"])] * 2 +          # cc 是自己那条链的链首
        [ok_rec("cc", chain=CHAIN, attempts=3) for _ in range(4)]   # 真降级
    )
    out = llmstats.runs(recs, min_len=3)
    assert len(out) == 1
    assert out[0]["length"] == 4                 # 只有降级那四条,不含前面两条正常的
    assert out[0]["start_i"] == 3
    assert out[0]["start_offset"] == 2


def test_runs_a_normal_call_breaks_a_degraded_segment():
    """中间插一条正常的链首应答,必须把段打断,不能粘成一个假的长段。"""
    deg = lambda: ok_rec("cc", chain=CHAIN, attempts=3)
    recs = _seq([deg(), deg(), deg(), ok_rec("codexg"), deg(), deg(), deg()])
    out = llmstats.runs(recs, min_len=3)
    assert [s["length"] for s in out] == [3, 3]      # 不是一段 6
    assert [s["start_i"] for s in out] == [1, 5]
    assert [s["start_offset"] for s in out] == [0, 4]


def test_runs_reports_the_dominant_error_of_a_segment():
    recs = _seq([fail_rec(error="chain budget exhausted") for _ in range(4)]
                + [fail_rec(error="別的原因")])
    seg = llmstats.runs(recs, min_len=3)[0]
    assert seg["length"] == 5
    assert seg["error"] == "chain budget exhausted"       # 取最常见的那条


def test_runs_error_is_none_when_the_segment_has_no_errors():
    """对照:成功的降级段没有 error,必须是 None 而不是空串或上一段的文本。"""
    recs = _seq([ok_rec("cc", chain=CHAIN, attempts=3) for _ in range(3)])
    assert llmstats.runs(recs, min_len=3)[0]["error"] is None


def test_runs_error_is_clipped_to_120_chars():
    long_err = "x" * 500
    recs = _seq([fail_rec(error=long_err) for _ in range(3)])
    assert len(llmstats.runs(recs, min_len=3)[0]["error"]) == 120


def test_runs_respects_min_len():
    recs = _seq([ok_rec("cc", attempts=3), ok_rec("cc", attempts=3), ok_rec("codexg")])
    assert llmstats.runs(recs, min_len=3) == []
    assert len(llmstats.runs(recs, min_len=2)) == 1


def test_runs_keeps_total_failure_segments():
    recs = _seq([fail_rec(), fail_rec(), fail_rec()])
    out = llmstats.runs(recs, min_len=3)
    assert len(out) == 1 and out[0]["provider"] == llmstats.NONE_SERVED
    assert out[0]["error"] == "chain budget exhausted"


def test_runs_ts_is_none_when_records_have_no_ts():
    """铁律:无 ts 时不许用邻居的时间去补一个看起来合理的区间。"""
    recs = _seq([ok_rec("cc", attempts=3) for _ in range(4)])
    seg = llmstats.runs(recs, min_len=3)[0]
    assert seg["start_ts"] is None and seg["end_ts"] is None


def test_runs_ts_is_carried_when_present():
    now = 1_700_000_000.0
    recs = _seq([ok_rec("cc", attempts=3, ts=now + i) for i in range(3)])
    seg = llmstats.runs(recs, min_len=3)[0]
    assert seg["start_ts"] == now and seg["end_ts"] == now + 2


def test_runs_rejects_bad_min_len():
    with pytest.raises(ValueError):
        llmstats.runs([], min_len=0)


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------

def test_page_index_is_absolute_across_pages_and_filters(tmp_path, monkeypatch):
    """「点开第 N 条」在换页和换筛选之后必须仍然指向同一条。"""
    lines = [ok_rec("codexg") for _ in range(5)] + [ok_rec("cc", attempts=3) for _ in range(5)]
    write_ledger(tmp_path, monkeypatch, lines)
    recs, _ = llmstats.read()

    p2, total = llmstats.page(recs, offset=5, limit=5, known_chain=CHAIN)
    assert total == 10
    assert [r["i"] for r in p2] == [6, 7, 8, 9, 10]

    filtered, ftotal = llmstats.page(recs, provider="cc", known_chain=CHAIN)
    assert ftotal == 5
    assert [r["i"] for r in filtered] == [6, 7, 8, 9, 10]   # 同一批绝对行号


def test_page_total_is_after_filter_before_paging():
    recs = _seq([ok_rec("codexg") for _ in range(10)])
    rows, total = llmstats.page(recs, offset=0, limit=3, known_chain=CHAIN)
    assert len(rows) == 3
    assert total == 10          # 不是 3


def test_page_skipped_column_shows_who_never_played():
    recs = _seq([ok_rec("cc", chain=["codex", "cc", "claude"])])
    rows, _ = llmstats.page(recs, known_chain=CHAIN)
    assert rows[0]["skipped"] == ["codexg"]


def test_page_skipped_is_empty_when_full_chain_was_used():
    """对照:整条链都在场时 skipped 必须是空,不能永远显示点东西。"""
    recs = _seq([ok_rec("cc", chain=CHAIN, attempts=3)])
    rows, _ = llmstats.page(recs, known_chain=CHAIN)
    assert rows[0]["skipped"] == []


def test_page_ok_filter_both_directions():
    recs = _seq([ok_rec("codexg"), fail_rec()])
    assert llmstats.page(recs, ok=True, known_chain=CHAIN)[1] == 1
    assert llmstats.page(recs, ok=False, known_chain=CHAIN)[1] == 1
    assert llmstats.page(recs, known_chain=CHAIN)[1] == 2


def test_page_failure_row_keeps_error_and_has_no_ms():
    recs = _seq([fail_rec(error="chain budget exhausted")])
    row = llmstats.page(recs, known_chain=CHAIN)[0][0]
    assert row["ok"] is False
    assert row["error"] == "chain budget exhausted"
    assert row["ms"] is None
    assert row["served"] == llmstats.NONE_SERVED


def test_page_rejects_negative_offset():
    with pytest.raises(ValueError):
        llmstats.page([], offset=-1, known_chain=CHAIN)


# --------------------------------------------------------------------------
# chain_config:来源必须如实
# --------------------------------------------------------------------------

def test_chain_config_builtin_only_when_the_ledger_is_empty():
    """内置常量是最后兜底:只有一条记录都没有的全新机器才会看见它。"""
    c = llmstats.chain_config()
    assert c["effective"] == llmstats.DEFAULT_CHAIN
    assert c["source"] == "builtin"
    assert c["observed"] is None and c["observed_i"] is None
    assert c["shadowed_by_env"] is False
    assert c["file_value"] is None


def test_chain_config_reads_file(tmp_path):
    Path(os.environ[llmstats.ENV_CHAIN_FILE]).write_text("cc\nclaude\n", encoding="utf-8")
    c = llmstats.chain_config()
    assert c["effective"] == ("cc", "claude")
    assert c["source"] == "file"
    assert c["shadowed_by_env"] is False


def test_chain_config_env_shadows_file(monkeypatch):
    Path(os.environ[llmstats.ENV_CHAIN_FILE]).write_text("cc,claude\n", encoding="utf-8")
    monkeypatch.setenv(llmstats.ENV_CHAIN, "codexg,codex")
    c = llmstats.chain_config()
    assert c["effective"] == ("codexg", "codex")
    assert c["source"] == "env"
    assert c["file_value"] == ("cc", "claude")
    assert c["shadowed_by_env"] is True       # 那个输入框现在是假按钮


def test_chain_config_env_without_file_is_not_shadowing(monkeypatch):
    """负对照:文件不存在时说「被环境变量压住」会把用户送去改一个不存在的文件。"""
    monkeypatch.setenv(llmstats.ENV_CHAIN, "codexg,codex")
    c = llmstats.chain_config()
    assert c["source"] == "env"
    assert c["shadowed_by_env"] is False


def test_chain_config_env_equal_to_file_is_not_shadowing(monkeypatch):
    """两边一样时不算压住:那不是个假按钮,改了会照样生效。"""
    Path(os.environ[llmstats.ENV_CHAIN_FILE]).write_text("cc\nclaude\n", encoding="utf-8")
    monkeypatch.setenv(llmstats.ENV_CHAIN, "cc claude")
    c = llmstats.chain_config()
    assert c["shadowed_by_env"] is False


def test_chain_config_bad_file_is_not_silently_ignored():
    """负对照:文件里是垃圾时,必须和「文件不存在」打印得不一样。"""
    Path(os.environ[llmstats.ENV_CHAIN_FILE]).write_text("CC\n1bad\n", encoding="utf-8")
    c = llmstats.chain_config()
    assert c["effective"] == llmstats.DEFAULT_CHAIN
    assert c["source"] == "builtin"
    assert c["file_error"] and "CC" in c["file_error"]
    assert c["file_value"] == ("CC", "1bad")


def test_chain_config_bad_env_falls_back_to_file_and_says_why(monkeypatch):
    Path(os.environ[llmstats.ENV_CHAIN_FILE]).write_text("cc\nclaude\n", encoding="utf-8")
    monkeypatch.setenv(llmstats.ENV_CHAIN, "NOPE!")
    c = llmstats.chain_config()
    assert c["source"] == "file"
    assert c["env_error"]


def test_chain_config_ignores_comments_and_blank_lines():
    Path(os.environ[llmstats.ENV_CHAIN_FILE]).write_text(
        "# 顺序在这里改\ncodexg\n\n  cc  # 便宜档之后直接上 cc\n", encoding="utf-8")
    assert llmstats.chain_config()["effective"] == ("codexg", "cc")


# --------------------------------------------------------------------------
# write_chain:整批裁决 + 原子写
# --------------------------------------------------------------------------

def test_write_chain_roundtrip():
    out = llmstats.write_chain(["codexg", "cc"])
    assert out["effective"] == ("codexg", "cc")
    assert out["source"] == "file"
    assert llmstats.chain_config()["effective"] == ("codexg", "cc")


def test_write_chain_rejects_whole_batch_and_leaves_file_untouched():
    """名字闸的负对照:**去读文件内容**,断言旧顺序原样还在。

    只断言「抛了异常」是不够的 —— 一次部分写入完全可以先落盘再抛,而留下的
    那个文件既不是旧顺序也不是用户要的新顺序,且不会让任何东西报错。
    """
    fp = Path(os.environ[llmstats.ENV_CHAIN_FILE])
    llmstats.write_chain(["codexg", "cc"])
    before = fp.read_text(encoding="utf-8")

    with pytest.raises(llmstats.Refused) as ei:
        llmstats.write_chain(["codexg", "CC", "claude"])   # 第二个非法
    assert ei.value.code == "bad_name"                     # 钉住是哪一道闸挡的

    assert fp.read_text(encoding="utf-8") == before        # 一个字节都没动
    assert "claude" not in fp.read_text(encoding="utf-8")  # 合法的那几个也没进去
    assert llmstats.chain_config()["effective"] == ("codexg", "cc")


def test_write_chain_rejects_empty():
    fp = Path(os.environ[llmstats.ENV_CHAIN_FILE])
    with pytest.raises(llmstats.Refused) as ei:
        llmstats.write_chain([])
    assert ei.value.code == "empty"
    assert not fp.exists()


def test_write_chain_rejects_duplicates():
    """同一级出现两次会让 attempts 的推断变成多义的,整批拒绝。"""
    fp = Path(os.environ[llmstats.ENV_CHAIN_FILE])
    with pytest.raises(llmstats.Refused) as ei:
        llmstats.write_chain(["cc", "cc"])
    assert ei.value.code == "duplicate"
    assert not fp.exists()


def test_write_chain_rejects_a_bare_string():
    """负对照:传一整个字符串进来会被逐字符拆成链,那是一条静默的错。"""
    with pytest.raises(llmstats.Refused) as ei:
        llmstats.write_chain("codexg")
    assert ei.value.code == "not_a_list"


@pytest.mark.parametrize("bad", ["CC", "1cc", "cc.x", "cc/x", "", " cc", "c" * 33, "cc ", "中文"])
def test_write_chain_name_gate_rejects_each_shape(bad):
    fp = Path(os.environ[llmstats.ENV_CHAIN_FILE])
    with pytest.raises(llmstats.Refused) as ei:
        llmstats.write_chain(["codexg", bad])
    assert ei.value.code == "bad_name"
    assert not fp.exists()


@pytest.mark.parametrize("good", ["cc", "codexg", "c", "a-b_c9", "c" * 32])
def test_write_chain_name_gate_accepts_each_legal_shape(good):
    """闸门必须**有可能放行**:全拒的闸门和一个坏掉的闸门输出一样。"""
    assert llmstats.write_chain([good])["effective"] == (good,)


def test_write_chain_reports_env_shadowing_right_after_writing(monkeypatch):
    """写成功了但环境变量压着,用户必须当场看见,否则他以为自己改好了。"""
    monkeypatch.setenv(llmstats.ENV_CHAIN, "claude")
    out = llmstats.write_chain(["codexg", "cc"])
    assert out["shadowed_by_env"] is True
    assert out["effective"] == ("claude",)     # 真正生效的是环境变量那份
    assert out["file_value"] == ("codexg", "cc")


def test_write_chain_creates_parent_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(llmstats.ENV_CHAIN_FILE, str(tmp_path / "deep" / "nest" / "chain.txt"))
    llmstats.write_chain(["cc"])
    assert (tmp_path / "deep" / "nest" / "chain.txt").exists()


def test_write_chain_leaves_no_temp_files_behind(tmp_path, monkeypatch):
    monkeypatch.setenv(llmstats.ENV_CHAIN_FILE, str(tmp_path / "d" / "chain.txt"))
    llmstats.write_chain(["cc"])
    with pytest.raises(llmstats.Refused):
        llmstats.write_chain(["CC"])
    leftovers = [p.name for p in (tmp_path / "d").iterdir() if p.name != "chain.txt"]
    assert leftovers == []


# --------------------------------------------------------------------------
# 端到端:两种记录形状在同一个文件里长期共存
# --------------------------------------------------------------------------

def test_mixed_old_and_new_records_flow_through_everything(tmp_path, monkeypatch):
    """老记录(无 ts)与新记录(有 ts/id)混在一起,每一层都要给出可分辨的数。"""
    now = 1_700_000_000.0
    lines = (
        [ok_rec("codexg") for _ in range(3)] +                      # 老,无 ts
        [ok_rec("cc", attempts=3, ts=now + i, rid=f"r{i}") for i in range(4)] +
        [fail_rec(ts=now + 100)]
    )
    write_ledger(tmp_path, monkeypatch, lines)
    recs, meta = llmstats.read()

    assert (meta["parsed"], meta["stamped"], meta["unstamped"]) == (8, 5, 3)

    inside, excluded, _ = llmstats.window(recs, hours=24, now=now + 200)
    assert len(inside) == 5 and excluded == 3

    a = llmstats.aggregate(recs)
    assert a["calls"] == 8 and a["failed"] == 1
    assert a["cheap_share"] == pytest.approx(3 / 8)

    rows = _by_name(llmstats.rungs(recs, CHAIN))
    assert rows["codexg"]["served"] == 3
    assert rows["codexg"]["failed"] == 5      # 后五条里它都上场了、都没成
    assert rows["codexg"]["skipped"] == 0

    seg = llmstats.runs(recs, min_len=3)
    assert len(seg) == 1 and seg[0]["provider"] == "cc" and seg[0]["length"] == 4
    assert seg[0]["start_ts"] == now

    row = llmstats.page(recs, offset=3, limit=1, known_chain=CHAIN)[0][0]
    assert row["i"] == 4 and row["id"] == "r0"


# --------------------------------------------------------------------------
# write_chain 的长度闸与 code 分辨力
# --------------------------------------------------------------------------

def test_write_chain_rejects_an_absurdly_long_chain():
    fp = Path(os.environ[llmstats.ENV_CHAIN_FILE])
    names = ["p%d" % n for n in range(llmstats.MAX_CHAIN + 1)]
    with pytest.raises(llmstats.Refused) as ei:
        llmstats.write_chain(names)
    assert ei.value.code == "too_many"
    assert not fp.exists()


def test_write_chain_accepts_exactly_max_chain():
    """长度闸的正面对照:刚好到上限必须放行,否则它挡的是所有输入。"""
    names = ["p%d" % n for n in range(llmstats.MAX_CHAIN)]
    assert len(llmstats.write_chain(names)["effective"]) == llmstats.MAX_CHAIN


def test_every_write_chain_gate_has_its_own_code():
    """五道闸互相兜底,所以只断言「抛了 Refused」证明不了任何一道还活着。

    这里把五种非法输入一次喂完,断言它们给出**五个互不相同**的 code:放开其中
    任何一道,对应那条就会落到另一道闸上、拿回另一个 code,当场变红。
    """
    cases = {
        "not_a_list": "cc",
        "empty": [],
        "bad_name": ["cc", "CC"],
        "duplicate": ["cc", "cc"],
        "too_many": ["p%d" % n for n in range(llmstats.MAX_CHAIN + 1)],
    }
    got = {}
    for want, arg in cases.items():
        with pytest.raises(llmstats.Refused) as ei:
            llmstats.write_chain(arg)
        got[want] = ei.value.code
    assert got == {k: k for k in cases}


# --------------------------------------------------------------------------
# body:五种「没有」必须给出五种 reason
# --------------------------------------------------------------------------

def write_bodies(tmp_path, monkeypatch, day, entries):
    d = tmp_path / "bodies"
    d.mkdir(exist_ok=True)
    with (d / (day + ".jsonl")).open("w", encoding="utf-8", newline=NEWLINE) as f:
        for e in entries:
            f.write((json.dumps(e, ensure_ascii=False) if isinstance(e, dict) else e) + NEWLINE)
    monkeypatch.setenv(llmstats.ENV_BODIES, str(d))
    return d


def test_body_uninitialised_when_no_companion_repo_resolves(tmp_path, monkeypatch):
    """「伴生仓没配」要说自己找过哪里,不能只说一句「没有正文」。"""
    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg", rid="a1")])
    monkeypatch.setenv(llmstats.ENV_BODIES, str(tmp_path / "bodies"))   # 目录不存在
    out = llmstats.body(1)
    assert out["available"] is False
    assert out["reason_code"] == "uninitialised"
    assert "未初始化" in out["reason"]
    assert out["tried"]                       # 找过哪里必须说出来


def test_body_disabled_when_companion_is_there_but_has_no_files(tmp_path, monkeypatch):
    """核心负对照:伴生仓**在**但空的 → 功能没开,不是伴生仓没配。

    这两句话指向相反的动作:一个去写侧打开开关,一个去初始化伴生仓。
    合并之后,一台配好了仓只是没开录的机器会被送去重装伴生仓。
    """
    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg", rid="a1")])
    d = tmp_path / "bodies"
    d.mkdir()                                  # 目录在,但一个 .jsonl 都没有
    monkeypatch.setenv(llmstats.ENV_BODIES, str(d))
    out = llmstats.body(1)
    assert out["reason_code"] == "disabled"
    assert "未开启" in out["reason"]


def test_body_no_id_for_an_old_record(tmp_path, monkeypatch):
    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg")])        # 老记录,没有 id
    write_bodies(tmp_path, monkeypatch, "2026-09-15", [{"id": "a1", "prompt": "x", "reply": "y"}])
    out = llmstats.body(1)
    assert out["reason_code"] == "no_id"
    assert out["available"] is False


def test_body_not_found_when_id_is_not_in_any_file(tmp_path, monkeypatch):
    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg", rid="gone")])
    write_bodies(tmp_path, monkeypatch, "2026-09-15", [{"id": "other", "prompt": "x", "reply": "y"}])
    out = llmstats.body(1)
    assert out["reason_code"] == "not_found"
    assert out["files_scanned"] == 1


def test_body_no_record_for_an_index_that_is_not_in_the_ledger(tmp_path, monkeypatch):
    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg", rid="a1")])
    write_bodies(tmp_path, monkeypatch, "2026-09-15", [{"id": "a1", "prompt": "x", "reply": "y"}])
    out = llmstats.body(999)
    assert out["reason_code"] == "no_record"


def test_body_found(tmp_path, monkeypatch):
    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg", rid="a1"), ok_rec("cc", rid="a2")])
    write_bodies(tmp_path, monkeypatch, "2026-09-15", [
        {"id": "a1", "prompt": "问题一", "reply": "答案一"},
        {"id": "a2", "prompt": "问题二", "reply": "答案二"},
    ])
    out = llmstats.body(2)
    assert out["available"] is True
    assert (out["id"], out["prompt"], out["reply"]) == ("a2", "问题二", "答案二")
    assert out["truncated"] is False


def test_body_reason_codes_are_all_distinct(tmp_path, monkeypatch):
    """核心负对照:六种「没有」必须给出六个互不相同的 reason_code。

    合并成一句「没有正文」之后,一个开关没打开的台子会把人送去查保留策略。
    这条用例一次走完六条路径:任意两条被合并,集合就少一个元素,当场变红。
    """
    codes = set()
    d = tmp_path / "bodies"
    monkeypatch.setenv(llmstats.ENV_BODIES, str(d))

    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg", rid="a1")])
    codes.add(llmstats.body(1)["reason_code"])                       # uninitialised
    codes.add(llmstats.body(999)["reason_code"])                     # no_record

    d.mkdir()
    codes.add(llmstats.body(1)["reason_code"])                       # disabled

    (d / "2026-09-15.jsonl").write_text(
        json.dumps({"id": "zzz", "prompt": "p", "reply": "r"}) + NEWLINE, encoding="utf-8")
    codes.add(llmstats.body(1)["reason_code"])                       # not_found

    (d / "2026-09-16.jsonl").mkdir()                                 # 读不动的「文件」
    codes.add(llmstats.body(1)["reason_code"])                       # read_error

    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg")])          # 无 id
    codes.add(llmstats.body(1)["reason_code"])                       # no_id

    assert codes == {"uninitialised", "no_record", "disabled",
                     "not_found", "read_error", "no_id"}


def test_body_truncation_is_never_silent(tmp_path, monkeypatch):
    big = "字" * (llmstats.BODY_MAX_CHARS + 500)
    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg", rid="a1")])
    write_bodies(tmp_path, monkeypatch, "2026-09-15", [{"id": "a1", "prompt": big, "reply": "短"}])
    out = llmstats.body(1)
    assert out["truncated"] is True
    assert len(out["prompt"]) == llmstats.BODY_MAX_CHARS
    assert out["prompt_chars"] == llmstats.BODY_MAX_CHARS + 500     # 原始长度照实说
    assert out["reply"] == "短"


def test_body_short_content_is_not_marked_truncated(tmp_path, monkeypatch):
    """对照:没截断就不能立 truncated,否则这个标志永远为真等于没有。"""
    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg", rid="a1")])
    write_bodies(tmp_path, monkeypatch, "2026-09-15", [{"id": "a1", "prompt": "p", "reply": "r"}])
    assert llmstats.body(1)["truncated"] is False


def test_body_malformed_body_lines_are_counted_not_ignored(tmp_path, monkeypatch):
    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg", rid="a1")])
    write_bodies(tmp_path, monkeypatch, "2026-09-15",
                 ["{坏行", {"id": "nope", "prompt": "p", "reply": "r"}])
    out = llmstats.body(1)
    assert out["reason_code"] == "not_found"
    assert out["malformed"] == 1


def test_body_searches_newest_file_first(tmp_path, monkeypatch):
    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg", rid="dup")])
    d = tmp_path / "bodies"
    d.mkdir()
    (d / "2026-09-01.jsonl").write_text(
        json.dumps({"id": "dup", "prompt": "旧", "reply": "旧"}) + NEWLINE, encoding="utf-8")
    (d / "2026-09-15.jsonl").write_text(
        json.dumps({"id": "dup", "prompt": "新", "reply": "新"}) + NEWLINE, encoding="utf-8")
    monkeypatch.setenv(llmstats.ENV_BODIES, str(d))
    assert llmstats.body(1)["prompt"] == "新"


def test_body_accepts_prefetched_records(tmp_path, monkeypatch):
    """records 传进来时不许再去读一次账本:分页页面已经有这批记录了。"""
    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg", rid="a1")])
    write_bodies(tmp_path, monkeypatch, "2026-09-15", [{"id": "a1", "prompt": "p", "reply": "r"}])
    recs, _ = llmstats.read()
    monkeypatch.setenv(llmstats.ENV_LEDGER, str(tmp_path / "gone.jsonl"))   # 账本挪走
    assert llmstats.body(1, records=recs)["available"] is True


def test_body_unreadable_file_is_not_reported_as_not_found(tmp_path, monkeypatch):
    """负对照:正文文件读不动 ≠ 正文过期了。

    后者不用处理,前者要去查权限。把读不动的文件当成「里面没有」,一次权限
    问题就会被写成一句「已过保留期」,而那句话会让人停止追查。

    这里用一个**名字叫 x.jsonl 的目录**来造 OSError —— 它能被 iterdir 列出来、
    后缀也对,但 open() 必然失败,不依赖任何平台的权限语义。
    """
    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg", rid="a1")])
    d = tmp_path / "bodies"
    d.mkdir()
    (d / "2026-09-15.jsonl").mkdir()          # 是个目录,不是文件
    monkeypatch.setenv(llmstats.ENV_BODIES, str(d))

    out = llmstats.body(1)
    assert out["available"] is False
    assert out["reason_code"] == "read_error"     # 不是 not_found
    assert "读不动" in out["reason"]


def test_body_read_error_still_reports_how_much_was_scanned(tmp_path, monkeypatch):
    """扫了几个文件必须照实说:一个扫了 0 个文件的「找不到」什么也没证明。"""
    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg", rid="a1")])
    d = tmp_path / "bodies"
    d.mkdir()
    (d / "2026-09-15.jsonl").mkdir()
    (d / "2026-09-14.jsonl").write_text(
        json.dumps({"id": "other", "prompt": "p", "reply": "r"}) + NEWLINE, encoding="utf-8")
    monkeypatch.setenv(llmstats.ENV_BODIES, str(d))

    out = llmstats.body(1)
    assert out["reason_code"] == "read_error"
    assert out["files_scanned"] == 2


def test_body_rejects_a_non_integer_index():
    with pytest.raises(llmstats.Refused) as ei:
        llmstats.body("1")
    assert ei.value.code == "bad_index"


# --------------------------------------------------------------------------
# 服务端要用到的名字,一个都不能少
# --------------------------------------------------------------------------

def test_public_surface_is_complete():
    """服务端按名字调用。少一个就是一次 AttributeError,而那要等到线上点开才发现。"""
    for name in ("ledger_path", "read", "aggregate", "window", "rungs", "runs",
                 "page", "chain_config", "write_chain", "body", "Refused"):
        assert hasattr(llmstats, name), name


def test_page_row_carries_the_full_chain():
    recs = _seq([ok_rec("cc", chain=["codex", "cc", "claude"])])
    row = llmstats.page(recs, known_chain=CHAIN)[0][0]
    assert row["chain"] == ["codex", "cc", "claude"]


# --------------------------------------------------------------------------
# 伴生仓解析顺序:每一级都要有用例钉着
# --------------------------------------------------------------------------

def test_bodies_resolution_prefers_the_explicit_override(tmp_path, monkeypatch):
    """显式覆盖设了就用,**存不存在都用**。

    悄悄滑到下一级会让一个填错的环境变量看起来像「伴生仓没装」,
    于是人去重装仓,而毛病在那一行配置里。
    """
    monkeypatch.setenv(llmstats.ENV_BODIES, str(tmp_path / "nowhere"))
    monkeypatch.setenv(llmstats.ENV_LLMCALL_DATA, str(tmp_path / "dataroot"))
    (tmp_path / "dataroot" / "bodies").mkdir(parents=True)
    r = llmstats.bodies_resolution()
    assert r["source"] == llmstats.ENV_BODIES
    assert r["dir"] == str(tmp_path / "nowhere")        # 没有滑到存在的那一级


def test_bodies_resolution_uses_llmcall_data_dir(tmp_path, monkeypatch):
    (tmp_path / "dataroot" / "bodies").mkdir(parents=True)
    monkeypatch.setenv(llmstats.ENV_LLMCALL_DATA, str(tmp_path / "dataroot"))
    r = llmstats.bodies_resolution()
    assert r["source"] == llmstats.ENV_LLMCALL_DATA
    assert r["dir"] == str(tmp_path / "dataroot" / "bodies")


def test_bodies_resolution_adds_data_under_llmcall_config(tmp_path, monkeypatch):
    (tmp_path / "cfg" / "data" / "bodies").mkdir(parents=True)
    monkeypatch.setenv(llmstats.ENV_LLMCALL_CONFIG, str(tmp_path / "cfg"))
    assert llmstats.bodies_resolution()["dir"] == str(tmp_path / "cfg" / "data" / "bodies")


def test_bodies_resolution_does_not_double_the_data_segment(tmp_path, monkeypatch):
    """LLMCALL_CONFIG 已经指到 data 时不许再套一层,否则这一级永远解析不出东西。"""
    (tmp_path / "cfg" / "data" / "bodies").mkdir(parents=True)
    monkeypatch.setenv(llmstats.ENV_LLMCALL_CONFIG, str(tmp_path / "cfg" / "data"))
    assert llmstats.bodies_resolution()["dir"] == str(tmp_path / "cfg" / "data" / "bodies")


def test_bodies_resolution_order_data_dir_beats_config(tmp_path, monkeypatch):
    """两级都能解析出来时,前一级必须赢。顺序错了不会报错,只会读到另一个仓。"""
    (tmp_path / "dataroot" / "bodies").mkdir(parents=True)
    (tmp_path / "cfg" / "data" / "bodies").mkdir(parents=True)
    monkeypatch.setenv(llmstats.ENV_LLMCALL_DATA, str(tmp_path / "dataroot"))
    monkeypatch.setenv(llmstats.ENV_LLMCALL_CONFIG, str(tmp_path / "cfg"))
    assert llmstats.bodies_resolution()["source"] == llmstats.ENV_LLMCALL_DATA


def test_bodies_resolution_skips_a_level_that_does_not_exist(tmp_path, monkeypatch):
    """前一级指向不存在的目录时要往下走,而不是停在那里报「找不到」。"""
    (tmp_path / "cfg" / "data" / "bodies").mkdir(parents=True)
    monkeypatch.setenv(llmstats.ENV_LLMCALL_DATA, str(tmp_path / "gone"))
    monkeypatch.setenv(llmstats.ENV_LLMCALL_CONFIG, str(tmp_path / "cfg"))
    r = llmstats.bodies_resolution()
    assert r["source"] == llmstats.ENV_LLMCALL_CONFIG
    assert [x["is_dir"] for x in r["tried"]][:2] == [False, True]


def test_bodies_resolution_returns_none_and_does_not_raise(monkeypatch):
    """读可降级:解析不出来是 None,不是异常。

    在这里抛会让一台没装 llmcall 的机器打开控制台就是一次 500。
    """
    for name in (llmstats.ENV_BODIES, llmstats.ENV_LLMCALL_DATA, llmstats.ENV_LLMCALL_CONFIG):
        monkeypatch.delenv(name, raising=False)
    r = llmstats.bodies_resolution()
    assert r["dir"] is None and r["source"] is None
    # 一个环境变量都没设时,一级都不该走 —— 这里刻意不去猜家目录下的约定路径,
    # 理由见 _resolve_bodies 的 docstring。`tried` 为空本身就是那个决定的体征。
    assert r["tried"] == [], r["tried"]


def test_no_private_companion_name_is_hardcoded_anywhere(monkeypatch):
    """这个公开仓的源码里,不许出现机主私有伴生仓的名字。

    来历是一次真实拦截:PII 闸门在 `_resolve_bodies` 的一行上挡住了提交,判为跨仓链接。
    最值得记的地方在于**那段源码在此之前是干净的** —— 同一份文字,在那个私有仓被创建
    并登记进可见性表之后才成为泄漏。所以判据不是「这串字符看着敏不敏感」,
    而是「它此刻指不指得到一个真实存在的私有东西」,而后者会变。

    修法不是往 `.pii-allow` 里加豁免:那个文件顶上就写着机主自己的数据一条都不许进,
    而私有仓名正是机主自己的东西。修法是把它从公开仓里拿掉,靠环境变量被指过去。

    这条用例盯的是它被加回来 —— 加回来的形式一定是「顺手补一级默认路径,让它在我这台
    机器上也能工作」,那个动机在任何时候都成立,所以需要一道机械的闸而不是一句约定。
    """
    import glob as _glob
    import io as _io
    import os as _os
    import re as _re
    root = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                         "scripts", "task_console")
    # 形状而非具体名字:`~/.<任意名>-config` 与 `~/.<任意名>-data` 都是伴生仓的命名约定。
    pat = _re.compile(r"~/\.[A-Za-z0-9_-]+-(config|data)\b")
    hits = []
    for f in sorted(_glob.glob(_os.path.join(root, "*.py"))):
        src = _io.open(f, encoding="utf-8", errors="replace").read()
        for m in pat.finditer(src):
            hits.append(f"{_os.path.basename(f)}: {m.group(0)}")
    assert not hits, ("公开仓的源码里出现了伴生仓形状的家目录路径,"
                      "它会把一个私有仓的存在写进公开代码:\n  " + "\n  ".join(hits))

    # 负对照:这个判据必须认得出那个形状,否则它对任何源码都打印绿色。
    assert pat.search("cands.append(('~/.llmtool-config', home))"), "判据认不出它要防的形状"
    assert not pat.search("cands.append((ENV_LLMCALL_CONFIG, base / 'bodies'))")


# --------------------------------------------------------------------------
# page 的 attempts_detail
# --------------------------------------------------------------------------

def test_page_attempts_detail_is_passed_through_verbatim():
    detail = [{"name": "codexg", "ok": False, "ms": 18004, "error": "timeout after 18s"},
              {"name": "codex", "ok": True, "ms": 900, "error": None}]
    r = ok_rec("codex", attempts=2)
    r["attempts_detail"] = detail
    row = llmstats.page(_seq([r]), known_chain=CHAIN)[0][0]
    assert row["attempts_detail"] == detail


def test_page_attempts_detail_is_none_when_absent_not_an_empty_list():
    """核心负对照:老记录没有这个字段 → **None**,不许替换成 []。

    「没有逐级明细」和「有明细但是空的」是两件事。顶层的 error 只记最后一级的
    原因,而那一级往往正是因为预算被前面几级烧光才没真的试过 —— 一整段
    chain budget exhausted 里,真正的病因一个字都没留下。页面要能把这两种
    说成不同的话,替换成 [] 就永远说不出来了。
    """
    row = llmstats.page(_seq([ok_rec("codexg")]), known_chain=CHAIN)[0][0]
    assert row["attempts_detail"] is None
    assert row["attempts_detail"] != []


def test_page_attempts_detail_keeps_a_genuinely_empty_list():
    """对照面:写侧真的记了一个空数组时,它必须原样是 [],不能被当成缺失。"""
    r = ok_rec("codexg")
    r["attempts_detail"] = []
    row = llmstats.page(_seq([r]), known_chain=CHAIN)[0][0]
    assert row["attempts_detail"] == []
    assert row["attempts_detail"] is not None


# --------------------------------------------------------------------------
# meta.exists:文件不在 ≠ 文件在但没有调用
# --------------------------------------------------------------------------

def test_read_meta_exists_is_false_for_a_missing_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv(llmstats.ENV_LEDGER, str(tmp_path / "nope.jsonl"))
    recs, meta = llmstats.read()                 # 不抛
    assert recs == []
    assert meta["exists"] is False
    assert meta["path"]                          # 该去哪找,必须打得出来


def test_read_meta_exists_is_true_for_an_empty_ledger(tmp_path, monkeypatch):
    """负对照:存在但零字节的账本,`exists` 必须是 True 而 parsed 是 0。

    没有这一条,`exists` 就等于没加 —— 两种情况的数值字段全是 0,
    而「账本根本不在」会被渲染成一排绿色的零。
    """
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    monkeypatch.setenv(llmstats.ENV_LEDGER, str(p))
    recs, meta = llmstats.read()
    assert recs == []
    assert meta["exists"] is True
    assert meta["parsed"] == 0
    assert meta["bytes"] == 0


def test_read_meta_distinguishes_missing_from_empty(tmp_path, monkeypatch):
    """把两者并排比一次:除了 exists 与 path,其余字段确实一模一样。

    这正是为什么单看数值分不出来,也正是这个字段存在的理由。
    """
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    monkeypatch.setenv(llmstats.ENV_LEDGER, str(p))
    _, empty = llmstats.read()
    monkeypatch.setenv(llmstats.ENV_LEDGER, str(tmp_path / "nope.jsonl"))
    _, missing = llmstats.read()

    numeric = ("bytes", "lines", "parsed", "malformed", "stamped", "unstamped")
    assert [empty[k] for k in numeric] == [missing[k] for k in numeric]   # 数值分不出来
    assert empty["exists"] != missing["exists"]                           # 只有它分得出来


# --------------------------------------------------------------------------
# page 的 q:按 error 文本搜
# --------------------------------------------------------------------------

def _mixed_ten():
    """10 条记录,其中 3 条的 error 含 budget。"""
    recs = [ok_rec("codexg") for _ in range(7)]
    recs += [fail_rec(error="chain budget exhausted") for _ in range(3)]
    return _seq(recs)


def test_page_q_total_is_the_filtered_count():
    """负对照:`total` 必须是筛后的 3,不是全账本的 10。

    报成 10,分页器就会画出一个翻不到的尾巴 —— 点过去是空页,而没有任何东西报错。
    """
    rows, total = llmstats.page(_mixed_ten(), q="budget", known_chain=CHAIN)
    assert total == 3
    assert len(rows) == 3
    assert all("budget" in r["error"] for r in rows)


def test_page_q_is_case_insensitive():
    """独立用例:用大写去搜,必须搜得到小写的记录。

    真实账本里的 error 全是英文小写,一个忘了折大小写的实现在那份数据上
    永远看起来正常,只有别人用大写搜的时候才静默地什么都搜不到。
    """
    recs = _mixed_ten()
    assert llmstats.page(recs, q="BUDGET", known_chain=CHAIN)[1] == 3
    assert llmstats.page(recs, q="EXHAUSTED", known_chain=CHAIN)[1] == 3
    assert llmstats.page(recs, q="ChAiN bUdGeT", known_chain=CHAIN)[1] == 3   # 混合大小写
    # 对照:折了大小写也不能变成「什么都匹配」,一个真不存在的词仍须得 0
    assert llmstats.page(recs, q="BUDGETARY", known_chain=CHAIN)[1] == 0


def test_page_q_never_matches_successful_records():
    """成功记录没有 error,q 非空时一律不匹配 —— 这个框的标签就是「搜错误文本」。"""
    recs = _seq([ok_rec("codexg", reply_chars=5) for _ in range(5)])
    assert llmstats.page(recs, q="codexg", known_chain=CHAIN)[1] == 0
    assert llmstats.page(recs, q="e", known_chain=CHAIN)[1] == 0


def test_page_q_and_ok_true_is_empty_by_construction():
    """q 与 ok=True 同时给必然是空 —— 对照:ok=False 时搜得到。"""
    recs = _mixed_ten()
    assert llmstats.page(recs, q="budget", ok=True, known_chain=CHAIN)[1] == 0
    assert llmstats.page(recs, q="budget", ok=False, known_chain=CHAIN)[1] == 3


def test_page_q_empty_means_no_filter():
    """对照面:q 为 None 或空串时不筛,否则这个框一出现就把整张表清空。"""
    recs = _mixed_ten()
    assert llmstats.page(recs, q=None, known_chain=CHAIN)[1] == 10
    assert llmstats.page(recs, q="", known_chain=CHAIN)[1] == 10


def test_page_q_that_matches_nothing_is_really_zero():
    """闸门必须**有可能挡住**:搜一个不存在的词要真的得 0,不是悄悄退回全量。"""
    assert llmstats.page(_mixed_ten(), q="zzzz-no-such-error", known_chain=CHAIN)[1] == 0


def test_page_q_composes_with_provider_and_keeps_absolute_index():
    recs = _mixed_ten()
    rows, total = llmstats.page(recs, provider=llmstats.NONE_SERVED, q="budget",
                                known_chain=CHAIN)
    assert total == 3
    assert [r["i"] for r in rows] == [8, 9, 10]      # 绝对行号,不是 0,1,2


def test_page_q_paging_keeps_the_filtered_total():
    rows, total = llmstats.page(_mixed_ten(), offset=1, limit=1, q="budget",
                                known_chain=CHAIN)
    assert total == 3 and len(rows) == 1 and rows[0]["i"] == 9


# --------------------------------------------------------------------------
# runs 的 total_failure:两类不能混排
# --------------------------------------------------------------------------

def test_runs_flags_total_failure_segments():
    recs = _seq([fail_rec() for _ in range(3)])
    seg = llmstats.runs(recs, min_len=3)[0]
    assert seg["total_failure"] is True
    assert seg["provider"] == llmstats.NONE_SERVED


def test_runs_degraded_but_successful_segment_is_not_a_total_failure():
    """负对照:降级到 cc 但答上来了,不是整链失败。

    两者混成一个标志之后,页面就没法把它们分成两张表,而混排时最狠的那种
    会被更长、却远没那么致命的降级段压下去。
    """
    recs = _seq([ok_rec("cc", chain=CHAIN, attempts=3) for _ in range(4)])
    seg = llmstats.runs(recs, min_len=3)[0]
    assert seg["total_failure"] is False
    assert seg["provider"] == "cc"


def test_runs_the_two_kinds_are_separable_and_the_longest_is_not_the_worst():
    """这正是分表的理由:最长的段不是最狠的段。

    一段 6 连的整链失败(没有答案)和一段 20 连的降级(只是贵了),按长度排
    前者在后面。按 total_failure 分开之后,它才排得到自己那张表的第一位。
    """
    recs = _seq([ok_rec("cc", chain=CHAIN, attempts=3) for _ in range(20)]
                + [ok_rec("codexg")]                       # 打断
                + [fail_rec() for _ in range(6)])
    segs = llmstats.runs(recs, min_len=3)
    by_len = sorted(segs, key=lambda s: -s["length"])
    assert by_len[0]["length"] == 20 and by_len[0]["total_failure"] is False

    fails = [s for s in segs if s["total_failure"]]
    degraded = [s for s in segs if not s["total_failure"]]
    assert [s["length"] for s in fails] == [6]
    assert [s["length"] for s in degraded] == [20]


# --------------------------------------------------------------------------
# chain_config 的观测级:内置常量是会漂的拷贝,账本才是证据
# --------------------------------------------------------------------------

def test_chain_config_prefers_the_observed_chain_over_the_builtin_copy(tmp_path, monkeypatch):
    """核心负对照:账本最后一条的 chain 与内置常量**不同**时,以账本为准。

    这条在改之前必然是红的 —— 那时 chain_config 根本不看账本,顶着「内置默认」
    的徽章展示一份从写下第一天就在漂的拷贝,而没有任何东西会说它漂了。
    """
    other = ["codexg", "cc", "claude", "codex"]          # 刻意与 DEFAULT_CHAIN 不同
    assert tuple(other) != llmstats.DEFAULT_CHAIN
    write_ledger(tmp_path, monkeypatch, [ok_rec("codexg", chain=other)])

    c = llmstats.chain_config()
    assert c["effective"] == tuple(other)                # 不是常量
    assert c["effective"] != llmstats.DEFAULT_CHAIN
    assert c["source"] == "observed"
    assert c["observed"] == tuple(other)
    assert c["observed_i"] == 1                          # 能点过去看那一条


def test_chain_config_observes_the_last_record_not_the_first(tmp_path, monkeypatch):
    """取的是**最近一次**,不是第一次。顺序错了不会报错,只会长期展示一条旧链。"""
    write_ledger(tmp_path, monkeypatch, [
        ok_rec("codexg", chain=["codexg", "codex"]),
        ok_rec("cc", chain=["cc", "claude"]),
    ])
    c = llmstats.chain_config()
    assert c["observed"] == ("cc", "claude")
    assert c["observed_i"] == 2


def test_chain_config_observation_skips_bad_tail_lines(tmp_path, monkeypatch):
    """尾部是坏行时要继续往回找,而不是报「没有观测」然后落到常量。"""
    write_ledger(tmp_path, monkeypatch, [
        ok_rec("cc", chain=["cc", "claude"]),
        "{坏行",
        "[1,2,3]",
    ])
    c = llmstats.chain_config()
    assert c["source"] == "observed"
    assert c["observed"] == ("cc", "claude")
    assert c["observed_i"] == 1                          # 行号仍是物理行号


def test_chain_config_env_and_file_still_outrank_the_observation(tmp_path, monkeypatch):
    """观测排在配置之后:人显式配过的东西不能被一次调用观测顶掉。"""
    write_ledger(tmp_path, monkeypatch, [ok_rec("cc", chain=["cc", "claude"])])

    Path(os.environ[llmstats.ENV_CHAIN_FILE]).write_text(
        "codex" + NEWLINE + "claude" + NEWLINE, encoding="utf-8")
    c = llmstats.chain_config()
    assert c["source"] == "file" and c["effective"] == ("codex", "claude")
    assert c["observed"] == ("cc", "claude")             # 观测照样填,给页面对照用

    monkeypatch.setenv(llmstats.ENV_CHAIN, "claude")
    c = llmstats.chain_config()
    assert c["source"] == "env" and c["effective"] == ("claude",)
    assert c["observed"] == ("cc", "claude")


def test_chain_config_observation_is_filled_even_when_config_wins(tmp_path, monkeypatch):
    """负对照:`observed` 不是只在它生效时才填。

    「配置里写着 X,而最近一次真实调用走的是 Y」正是页面要说的那句话,
    只在 source=="observed" 时才填会让这句话永远说不出来。
    """
    write_ledger(tmp_path, monkeypatch, [ok_rec("cc", chain=["cc", "claude"])])
    Path(os.environ[llmstats.ENV_CHAIN_FILE]).write_text(
        "codexg" + NEWLINE + "codex" + NEWLINE, encoding="utf-8")
    c = llmstats.chain_config()
    assert c["source"] == "file"
    assert c["observed"] is not None
    assert c["observed"] != c["effective"]               # 两者不同,页面要能说出来


def test_chain_config_accepts_prefetched_records(tmp_path, monkeypatch):
    """传了 records 就不再扫账本 —— 服务端通常已经读过一遍了。"""
    write_ledger(tmp_path, monkeypatch, [ok_rec("cc", chain=["cc", "claude"])])
    recs, _ = llmstats.read()
    monkeypatch.setenv(llmstats.ENV_LEDGER, str(tmp_path / "gone.jsonl"))   # 账本挪走
    c = llmstats.chain_config(records=recs)
    assert c["source"] == "observed" and c["observed"] == ("cc", "claude")


def test_chain_config_empty_records_list_falls_back_to_builtin(tmp_path, monkeypatch):
    """传了空列表 = 真的没有记录,落到常量 —— 不许偷偷再去扫一遍文件。"""
    write_ledger(tmp_path, monkeypatch, [ok_rec("cc", chain=["cc", "claude"])])
    c = llmstats.chain_config(records=[])
    assert c["source"] == "builtin"
    assert c["observed"] is None


def test_page_uses_the_observed_chain_for_the_skipped_column(tmp_path, monkeypatch):
    """page 不传 known_chain 时,`skipped` 要按观测到的链算,不是按会漂的常量。"""
    write_ledger(tmp_path, monkeypatch, [
        ok_rec("cc", chain=["mystery", "cc", "claude"], attempts=2),
    ])
    recs, _ = llmstats.read()
    row = llmstats.page(recs)[0][0]
    assert row["skipped"] == []          # 观测链就是这条,没有谁缺席


# --------------------------------------------------------------------------
# 观测的 records 分支:和扫文件那条路是两套代码,必须各自有用例
# --------------------------------------------------------------------------
#
# 这四条是补出来的:原先只测了「不传 records、自己扫账本」那条路,于是
# 「传了 records 时取第一条而不是最近一条」「传了 records 时不报行号」两个变异体
# 活了下来 —— 同一件事的两条实现路径,只测其中一条等于另一条没有闸门。

def test_chain_config_records_branch_takes_the_last_record(tmp_path, monkeypatch):
    """传 records 时也要取**最近一条**,不是第一条。"""
    recs = _seq([
        ok_rec("codexg", chain=["codexg", "codex"]),
        ok_rec("cc", chain=["cc", "claude"]),
    ])
    c = llmstats.chain_config(records=recs)
    assert c["observed"] == ("cc", "claude")
    assert c["effective"] == ("cc", "claude")


def test_chain_config_records_branch_reports_the_absolute_index(tmp_path, monkeypatch):
    """传 records 时 `observed_i` 也必须是绝对行号 —— 不报就点不过去。"""
    recs = _seq([
        ok_rec("codexg", chain=["codexg", "codex"]),
        ok_rec("cc", chain=["cc", "claude"]),
        ok_rec("cc", chain=["cc", "claude"]),
    ])
    c = llmstats.chain_config(records=recs)
    assert c["observed_i"] == 3


def test_chain_config_records_branch_skips_records_without_a_chain(tmp_path, monkeypatch):
    """尾部记录缺 chain 时要继续往回找,不是报「没有观测」。"""
    bad = ok_rec("cc", chain=["cc", "claude"])
    del bad["chain"]
    recs = _seq([ok_rec("codexg", chain=["codexg", "codex"]), bad])
    c = llmstats.chain_config(records=recs)
    assert c["observed"] == ("codexg", "codex")
    assert c["observed_i"] == 1


def test_page_without_known_chain_uses_the_records_it_was_given(tmp_path, monkeypatch):
    """page 必须把手上的 records 传下去,而不是回头再扫一遍账本。

    负对照靠「账本挪走」来造:records 已经在手上,所以 skipped 那一列仍应按
    观测链算;回头扫文件的实现这时会落到内置常量,那一列就变成了另一个答案。
    """
    write_ledger(tmp_path, monkeypatch, [
        ok_rec("cc", chain=["mystery", "cc", "claude"], attempts=2)])
    recs, _ = llmstats.read()
    monkeypatch.setenv(llmstats.ENV_LEDGER, str(tmp_path / "gone.jsonl"))

    row = llmstats.page(recs)[0][0]
    assert row["skipped"] == []          # 按观测链算:这条链上谁都没缺席


# --------------------------------------------------------------------------
# runs 的两套编号:在干净账本上它们恰好相等,所以必须用脏账本测
# --------------------------------------------------------------------------

def test_runs_two_numbering_schemes_really_differ_on_a_dirty_ledger(tmp_path, monkeypatch):
    """核心负对照:夹着空行和坏行时,`start_i` 与 `start_offset` **必须不等**。

    这两个数在一个没有空行坏行的账本上恰好相等,所以一个只用干净 fixture 的
    测试永远发现不了它们是两套 —— 这正是它至今没被发现的原因。

    两个数还要各自都对:
    - `start_i` 是物理行号,能在账本里定位到那一条(body 收的就是它)
    - `start_offset` 是列表下标,喂给 page(offset=...) 拿到的第一行就是那一条
    """
    lines = [
        ok_rec("codexg"),            # 行 1
        "",                          # 行 2  空行
        "{坏行",                      # 行 3  坏行
        ok_rec("codexg"),            # 行 4
        "",                          # 行 5  空行
        ok_rec("cc", chain=CHAIN, attempts=3),   # 行 6  降级段起点
        ok_rec("cc", chain=CHAIN, attempts=3),   # 行 7
        ok_rec("cc", chain=CHAIN, attempts=3),   # 行 8
    ]
    write_ledger(tmp_path, monkeypatch, lines)
    recs, meta = llmstats.read()
    assert (meta["blank"], meta["malformed"], meta["parsed"]) == (2, 1, 5)

    seg = llmstats.runs(recs, min_len=3)[0]
    assert seg["length"] == 3

    # 两套编号确实不是一套
    assert seg["start_i"] == 6
    assert seg["start_offset"] == 2
    assert seg["start_i"] != seg["start_offset"]
    assert seg["end_i"] == 8 and seg["end_offset"] == 4

    # start_i 是物理行号:按它去账本里找,找到的就是那一条
    hit = [r for r in recs if r[llmstats.INDEX_KEY] == seg["start_i"]]
    assert len(hit) == 1
    assert hit[0] is recs[seg["start_offset"]]          # 两个数指向同一条记录

    # start_offset 喂给 page:第一行就是那一条,而它的 i 正是 start_i
    rows, _ = llmstats.page(recs, offset=seg["start_offset"], limit=1, known_chain=CHAIN)
    assert rows[0]["i"] == seg["start_i"] == 6


def test_runs_start_i_is_what_body_takes(tmp_path, monkeypatch):
    """`start_i` 和 `body(i)` 收的是同一套编号 —— 拿下标去查会查到另一条。"""
    lines = ["", "{坏行"] + [ok_rec("cc", chain=CHAIN, attempts=3, rid="b%d" % n)
                            for n in range(3)]
    write_ledger(tmp_path, monkeypatch, lines)
    d = tmp_path / "bodies"
    d.mkdir()
    (d / "2026-09-15.jsonl").write_text(
        json.dumps({"id": "b0", "prompt": "第一条", "reply": "r"}) + NEWLINE, encoding="utf-8")
    monkeypatch.setenv(llmstats.ENV_BODIES, str(d))

    recs, _ = llmstats.read()
    seg = llmstats.runs(recs, min_len=3)[0]
    assert seg["start_i"] == 3 and seg["start_offset"] == 0

    got = llmstats.body(seg["start_i"], records=recs)
    assert got["available"] is True and got["prompt"] == "第一条"

    # 负对照:拿下标去查,查到的不是这一条(这里干脆查不到,因为账本没有第 0 行)
    wrong = llmstats.body(seg["start_offset"], records=recs)
    assert wrong["available"] is False


def test_runs_no_longer_exposes_the_ambiguous_index_name():
    """`start_index` / `end_index` 必须**删掉**,不许留成别名。

    一个叫 index 的字段在两套编号并存时说不清自己是哪一套,而那正是它的毛病。
    留着别名,页面上那段旧代码会继续用它,这次改动等于没做。
    """
    recs = _seq([ok_rec("cc", chain=CHAIN, attempts=3) for _ in range(3)])
    seg = llmstats.runs(recs, min_len=3)[0]
    assert "start_index" not in seg
    assert "end_index" not in seg
    assert {"start_i", "end_i", "start_offset", "end_offset"} <= set(seg)


def test_runs_offsets_are_relative_to_the_list_you_passed(tmp_path, monkeypatch):
    """`start_offset` 是相对于传进来那个列表的。

    先过滤再传进来时,它只在你把同一个列表交给 page() 时才对得上,
    而 `start_i` 在任何情况下都指向账本里同一条记录。这条把这个区别钉住。
    """
    lines = [ok_rec("codexg") for _ in range(5)] + \
            [ok_rec("cc", chain=CHAIN, attempts=3) for _ in range(3)]
    write_ledger(tmp_path, monkeypatch, lines)
    recs, _ = llmstats.read()

    sub = [r for r in recs if not _is_head(r)]          # 只留降级的那几条
    seg = llmstats.runs(sub, min_len=3)[0]
    assert seg["start_offset"] == 0                    # 相对 sub
    assert seg["start_i"] == 6                         # 账本里仍是第 6 行

    rows, _ = llmstats.page(sub, offset=seg["start_offset"], limit=1, known_chain=CHAIN)
    assert rows[0]["i"] == seg["start_i"]              # 同一个列表,对得上


def _is_head(r):
    chain = r.get("chain") or []
    return bool(chain) and r.get("provider") == chain[0]


# --------------------------------------------------------------------------
# callers:三种「没有名字」里,有两种必须分开
# --------------------------------------------------------------------------

def named(caller, provider="codexg", ok=True):
    """带 caller 键的记录。caller=None 表示「键在,值是 null」。"""
    r = ok_rec(provider) if ok else fail_rec()
    r["caller"] = caller
    return r


def legacy(provider="codexg", ok=True):
    """存量记录:**根本没有 caller 这个键**。"""
    return ok_rec(provider) if ok else fail_rec()


def test_callers_splits_three_ways():
    """核心负对照:有值 / 值是 null / 没这个键,是**三项**,不是两项也不是一项。

    把后两种合并,一个刚上线的字段就会看起来像「推断全都失败了」,
    而那会把人送去修一个没坏的东西。
    """
    recs = ([named("em_tick.py") for _ in range(5)]
            + [named(None) for _ in range(3)]
            + [legacy() for _ in range(7)])
    out = llmstats.callers(recs)

    assert len(out) == 3
    names = [d["name"] for d in out]
    assert len(set(names)) == 3                      # 三个名字互不相同
    by = {d["name"]: d for d in out}
    assert by["em_tick.py"]["calls"] == 5
    assert by[llmstats.CALLER_UNKNOWN]["calls"] == 3
    assert by[llmstats.CALLER_LEGACY]["calls"] == 7
    assert llmstats.CALLER_UNKNOWN != llmstats.CALLER_LEGACY


def test_callers_unknown_and_legacy_are_never_the_same_bucket():
    """只有这两种时也必须是两项 —— 合并的变异体在这里必然被抓。"""
    out = llmstats.callers([named(None), legacy()])
    assert len(out) == 2
    assert {d["name"] for d in out} == {llmstats.CALLER_UNKNOWN, llmstats.CALLER_LEGACY}
    assert all(d["calls"] == 1 for d in out)


def test_callers_counts_ok_and_failed_per_caller():
    recs = [named("a.py"), named("a.py"), named("a.py", ok=False), named("b.py", ok=False)]
    by = {d["name"]: d for d in llmstats.callers(recs)}
    assert (by["a.py"]["calls"], by["a.py"]["ok"], by["a.py"]["failed"]) == (3, 2, 1)
    assert (by["b.py"]["calls"], by["b.py"]["ok"], by["b.py"]["failed"]) == (1, 0, 1)
    assert all(d["ok"] + d["failed"] == d["calls"] for d in llmstats.callers(recs))


def test_callers_sorted_by_calls_descending():
    recs = [named("few.py")] + [named("many.py") for _ in range(4)] + [named("mid.py")] * 2
    assert [d["name"] for d in llmstats.callers(recs)] == ["many.py", "mid.py", "few.py"]


def test_callers_ties_are_broken_by_name_so_the_dropdown_is_stable():
    """并列时按名字排。一个每次刷新都换序的下拉框,用起来跟坏了没区别。"""
    recs = [named("zeta.py"), named("alpha.py"), named("mid.py")]
    assert [d["name"] for d in llmstats.callers(recs)] == ["alpha.py", "mid.py", "zeta.py"]


def test_callers_does_not_truncate():
    """下拉框来自整个账本。从一页记录里凑出来的清单,会让「今天没出现」
    变成「不存在」,所以这个函数不接受 limit,也不在内部截断。"""
    recs = [named("c%03d.py" % n) for n in range(200)]
    assert len(llmstats.callers(recs)) == 200
    assert llmstats.callers([]) == []


def test_callers_empty_string_and_non_string_count_as_unknown():
    """空串和非字符串都算「推断不出」,但**仍然不是** legacy:键毕竟在。"""
    a, b = named(""), named(123)
    out = llmstats.callers([a, b, legacy()])
    by = {d["name"]: d for d in out}
    assert by[llmstats.CALLER_UNKNOWN]["calls"] == 2
    assert by[llmstats.CALLER_LEGACY]["calls"] == 1


# --------------------------------------------------------------------------
# page 的 caller 筛选
# --------------------------------------------------------------------------

def test_page_caller_is_an_exact_match_not_a_substring():
    """负对照:`em_tick.py` 不许匹配到 `em_tick.py.bak`。

    子串匹配在真实脚本名上会静默地多算 —— 而多算出来的那几条看起来完全正常。
    """
    recs = _seq([named("em_tick.py"), named("em_tick.py.bak"), named("pre_em_tick.py")])
    rows, total = llmstats.page(recs, caller="em_tick.py", known_chain=CHAIN)
    assert total == 1
    assert rows[0]["caller"] == "em_tick.py"


def test_page_caller_total_is_the_filtered_count():
    recs = _seq([named("a.py") for _ in range(3)] + [named("b.py") for _ in range(7)])
    rows, total = llmstats.page(recs, caller="a.py", known_chain=CHAIN)
    assert total == 3 and len(rows) == 3


def test_page_caller_none_means_no_filter():
    """对照面:不传就是不筛,否则这个下拉框一出现就把整张表清空。"""
    recs = _seq([named("a.py"), named(None), legacy()])
    assert llmstats.page(recs, known_chain=CHAIN)[1] == 3
    assert llmstats.page(recs, caller=None, known_chain=CHAIN)[1] == 3


def test_page_caller_can_filter_by_the_two_synthetic_names():
    """下拉框里列得出来的每一项都要点得动,否则那两行就是摆设。"""
    recs = _seq([named("a.py"), named(None), named(None), legacy()])
    assert llmstats.page(recs, caller=llmstats.CALLER_UNKNOWN, known_chain=CHAIN)[1] == 2
    assert llmstats.page(recs, caller=llmstats.CALLER_LEGACY, known_chain=CHAIN)[1] == 1


def test_page_caller_that_matches_nothing_is_really_zero():
    """闸门必须**有可能挡住**:筛一个不存在的调用方要真的得 0,不是退回全量。"""
    recs = _seq([named("a.py") for _ in range(4)])
    assert llmstats.page(recs, caller="nope.py", known_chain=CHAIN)[1] == 0


def test_page_caller_composes_with_ok_and_keeps_absolute_index():
    recs = _seq([named("a.py"), named("a.py", ok=False), named("b.py", ok=False)])
    rows, total = llmstats.page(recs, caller="a.py", ok=False, known_chain=CHAIN)
    assert total == 1 and rows[0]["i"] == 2


# --------------------------------------------------------------------------
# page 每行的 caller 透传:键不在就不要造一个出来
# --------------------------------------------------------------------------

def test_page_row_omits_caller_entirely_for_a_legacy_record():
    """核心负对照:老记录的行里**不许有** caller 这个键。

    页面靠 `"caller" in row` 区分「还没开始记」和「记了但推断不出」。
    给老记录补一个 None,这个区分当场消失,而两者要说的话完全不同。
    """
    row = llmstats.page(_seq([legacy()]), known_chain=CHAIN)[0][0]
    assert "caller" not in row


def test_page_row_keeps_an_explicit_null_caller():
    """对照面:值真的是 null 时,键必须在,值是 None。"""
    row = llmstats.page(_seq([named(None)]), known_chain=CHAIN)[0][0]
    assert "caller" in row
    assert row["caller"] is None


def test_page_row_passes_a_real_caller_through():
    row = llmstats.page(_seq([named("em_tick.py")]), known_chain=CHAIN)[0][0]
    assert row["caller"] == "em_tick.py"


def test_page_rows_distinguish_legacy_from_null_side_by_side():
    """两种记录并排放一次:两行的差别只能靠「键在不在」看出来。

    这正是为什么不能给老记录补 None —— 补了之后这两行就一模一样了。
    """
    rows, _ = llmstats.page(_seq([legacy(), named(None)]), known_chain=CHAIN)
    assert "caller" not in rows[0]
    assert "caller" in rows[1] and rows[1]["caller"] is None
    assert rows[0] != rows[1]
