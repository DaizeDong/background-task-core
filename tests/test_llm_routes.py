"""调用屏的服务端组装层:窗口怎么切、缓存什么时候失效、索引怎么拒。

这三件事都是「错了也看起来正常」的那一类,所以每一条都配了负对照。

**窗口**。人问「今天用了多少」问的是日历上的今天。用「最近 24 小时」去答,
在早上八点会把昨天下午的量算进来 —— 数字更大、看起来更健康,而且永远对不上
任何人心里那个数。这个差别只有在**跨零点**的时候才现形,所以下面那条用例
刻意把当前时间摆在凌晨,把一条记录摆在昨晚:滑动窗口会算进来,日历窗口不会。

**缓存**。账本是只追加的,缓存键是 (路径, 字节数, mtime)。如果改成「缓存 N 秒」,
一次刚刚发生的调用会在页面上消失几秒 —— 而「跑完了但看不见」正是这台台子要防的形态。

**索引**。明细的 `i` 是全账本的绝对行号,一路会走到正文查找里去碰文件系统。
按形状拒绝必须发生在那之前。
"""
from __future__ import annotations

import io
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "task_console"))

import llmstats  # noqa: E402
import server  # noqa: E402


def _write(path, recs):
    with io.open(path, "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _rec(ts, provider="codexg", ok=True, attempts=1):
    return {"ts": ts, "provider": provider, "chain": ["codexg", "codex", "cc", "claude"],
            "mode": "judge", "web": False, "prompt_chars": 100, "reply_chars": 50,
            "ms": 1200, "attempts": attempts, "ok": ok}


@pytest.fixture(autouse=True)
def _clean_cache():
    server._LEDGER_CACHE.clear()
    yield
    server._LEDGER_CACHE.clear()


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    p = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(llmstats, "ledger_path", lambda: p)
    return p


# ── 窗口 ────────────────────────────────────────────────────────────────────

def _at(y, mo, d, h, mi=0):
    """本地时间 -> epoch 秒。夏令时交给 mktime 的 isdst=-1 去判。"""
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


def test_today_is_the_calendar_day_not_a_rolling_24h(ledger, monkeypatch):
    """凌晨一点看「今天」,昨晚的调用不许被算进来。

    这是整条窗口逻辑唯一会露馅的地方。不跨零点的话,两种切法给出同一个数,
    一个用错了切法的实现可以一直绿到某天有人早上打开页面。
    """
    now = _at(2026, 3, 10, 1, 0)          # 周二凌晨 1 点
    yesterday_evening = _at(2026, 3, 9, 20, 0)   # 昨晚 8 点,距今 5 小时
    this_morning = _at(2026, 3, 10, 0, 30)       # 今天 0 点半
    _write(ledger, [_rec(yesterday_evening), _rec(this_morning)])
    monkeypatch.setattr(server.time, "time", lambda: now)

    ov = server.llm_overview()
    today = ov["windows"][0]
    assert today["label"] == "今天"
    assert today["calls"] == 1, (
        "「今天」把昨晚那条也算进来了 = 用的是滚动 24 小时,不是日历日。"
        f"实际 {today['calls']} 条")

    # 负对照:同一批数据在 7 日窗口里必须**两条都在**。
    # 少了这一条,一个「什么都过滤掉」的实现也会让上面那条断言通过。
    assert ov["windows"][1]["calls"] == 2, "7 日窗口没把两条都收进来,过滤器过头了"


def test_a_ledger_with_no_timestamps_reports_excluded_not_zero(ledger, monkeypatch):
    """全是无时间戳的账本,窗口必须报「0 条 + 排除 N 条」,不是「0 条」。

    这两者在页面上长得不一样是刻意的:一个是「今天没调用」,
    一个是「有十一万条但问不出今天」。把后者显示成前者,就是这台台子要防的东西。
    """
    recs = [_rec(None) for _ in range(5)]
    for r in recs:
        r.pop("ts")
    _write(ledger, recs)
    monkeypatch.setattr(server.time, "time", lambda: _at(2026, 3, 10, 12))

    ov = server.llm_overview()
    today = ov["windows"][0]
    assert today["calls"] == 0
    assert today["unstamped_excluded"] == 5, (
        "无时间戳的记录没有被单独数出来 = 页面无法把「没调用」和「问不出来」分开")
    # 而全部历史那一栏必须看得见它们,否则这五条记录在整个页面上凭空消失了。
    assert ov["windows"][-1]["label"] == "全部历史"
    assert ov["windows"][-1]["calls"] == 5


# ── 缓存 ────────────────────────────────────────────────────────────────────

def test_an_appended_call_is_visible_immediately(ledger, monkeypatch):
    """追加一行之后,下一次读必须看得见它。

    负对照在下一条:缓存必须真的在生效,否则这一条只是证明了「没有缓存」。
    """
    now = _at(2026, 3, 10, 12)
    monkeypatch.setattr(server.time, "time", lambda: now)
    _write(ledger, [_rec(now - 60)])
    assert server.llm_overview()["windows"][-1]["calls"] == 1

    with io.open(ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(_rec(now - 30)) + "\n")
    # mtime 的分辨率在某些文件系统上不够细,但字节数一定变了,而它也在缓存键里。
    assert server.llm_overview()["windows"][-1]["calls"] == 2, (
        "追加的一行没被看见 = 缓存键没有跟着文件走,刚跑完的调用会在页面上消失")


def test_the_cache_actually_caches(ledger, monkeypatch):
    """负对照:文件没变时不许重新解析。

    没有这一条,把缓存整个删掉,上面那条用例照样全绿。
    """
    now = _at(2026, 3, 10, 12)
    monkeypatch.setattr(server.time, "time", lambda: now)
    _write(ledger, [_rec(now - 60)])
    calls = {"n": 0}
    real = llmstats.read

    def counted(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(llmstats, "read", counted)
    server.llm_overview()
    server.llm_overview()
    server.llm_overview()
    assert calls["n"] == 1, f"账本被重新解析了 {calls['n']} 次,缓存没起作用"


# ── 索引形状 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["abc", "-1", "1.5", "", "..", "1 2", "٣"])
def test_non_numeric_indices_are_refused_by_shape(raw):
    """明细索引的形状闸。

    这里测的是判据本身而不是跑一次 HTTP :路由里那一行就是
    `raw.isdigit()`,而它要挡的正是「一个非数字一路走到正文查找里去碰文件系统」。
    `٣` 是阿拉伯数字三 —— `isdigit()` 对它是 True,所以它**不该**出现在这张表里。
    这条用例故意把它留着,好让下面那条负对照有东西可抓。
    """
    if raw == "٣":
        pytest.skip("留给下面那条负对照")
    assert not raw.isdigit()


def test_isdigit_accepts_non_ascii_digits():
    """负对照,也是一条真实的告警:`isdigit()` 认非 ASCII 数字。

    `'٣'.isdigit()` 是 True,而 `int('٣')` 也确实给出 3 —— 所以这条路径是闭合的,
    没有洞。把它钉在这里的原因是:如果哪天有人把 `int()` 换成别的解析,
    这两者就会分家,而那种分家是静默的。
    """
    assert "٣".isdigit()
    assert int("٣") == 3


# ── 每个对外返回都必须能上网线 ────────────────────────────────────────────────
#
# 真实事故 2026-09-15:`bodies_resolution()` 的 `dir` 是一个 `Path`,而 `Path` 不可
# JSON 序列化。**这个 bug 在此之前不可能被发现**,因为伴生仓还没配好,那一档一直返回
# `None`,而 `None` 序列化得好好的。它是在「把伴生仓配对」这个动作之后**才**出现的 ——
# 也就是说,一个把配置修好的操作会让接口开始 500。
#
# 这种「配置好了反而坏了」的形状,靠逐个函数写用例是防不住的:防住的永远是想到的那几个。
# 判据改成「这个模块的对外返回是给 HTTP 层用的,所以它必须能过 json.dumps」,
# 一次覆盖全部,新加的函数自动进这道闸。

def _json_safe(obj, path="返回值"):
    """返回不可序列化的第一处路径,全都能序列化时返回 None。"""
    try:
        json.dumps(obj, ensure_ascii=False)
        return None
    except (TypeError, ValueError):
        pass
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, (str, int, float, bool, type(None))):
                return f"{path} 的键 {k!r} 是 {type(k).__name__}"
            bad = _json_safe(v, f"{path}[{k!r}]")
            if bad:
                return bad
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            bad = _json_safe(v, f"{path}[{i}]")
            if bad:
                return bad
    else:
        return f"{path} 是 {type(obj).__name__}"
    return f"{path} 整体不可序列化"


def test_every_public_return_survives_json_dumps(ledger, monkeypatch, tmp_path):
    """凡是会进 HTTP 响应的返回,都必须能过 json.dumps。

    这里刻意把伴生仓**配置成存在的**再问一次 —— 那正是当初那个 bug 唯一会现形的状态。
    只在「没配」的状态下测,这条用例会永远绿,和它要防的那个 bug 同时存在。
    """
    bodies = tmp_path / "companion" / "data" / "bodies"
    bodies.mkdir(parents=True)
    monkeypatch.setenv("LLMCALL_CONFIG", str(tmp_path / "companion"))
    monkeypatch.delenv("TASK_CONSOLE_LLMCALL_BODIES", raising=False)
    _write(ledger, [_rec(_at(2026, 3, 10, 9)), _rec(_at(2026, 3, 10, 10), ok=False, attempts=4)])

    recs, meta = llmstats.read()
    rows, _total = llmstats.page(recs)
    chain = llmstats.chain_config(recs)
    checks = {
        "read.meta": meta,
        "aggregate": llmstats.aggregate(recs),
        "rungs": llmstats.rungs(recs, chain.get("effective") or []),
        "verify_rungs": llmstats.verify_rungs(recs),
        "runs": llmstats.runs(recs, min_len=1),
        "callers": llmstats.callers(recs),
        "page.rows": rows,
        "chain_config": chain,
        "bodies_resolution": llmstats.bodies_resolution(),
        "body": llmstats.body(recs[0].get(llmstats.INDEX_KEY), recs),
        "llm_overview": server.llm_overview(),
    }
    bad = {name: _json_safe(v, name) for name, v in checks.items() if _json_safe(v, name)}
    assert not bad, "这些对外返回带着 json.dumps 处理不了的东西:\n  " + "\n  ".join(
        f"{k}: {v}" for k, v in bad.items())

    # 负对照:判据本身必须认得出一个不可序列化的东西,否则上面那条是在为所有输入打印绿色。
    from pathlib import Path as _P
    assert _json_safe({"dir": _P("/tmp/x")}, "探针") is not None
    assert _json_safe({"ok": [1, "a", None, {"n": 2}]}, "探针") is None


def test_the_companion_was_really_resolved_in_that_test(ledger, monkeypatch, tmp_path):
    """前置状态的负对照:上面那条用例里,伴生仓必须真的被解析到了。

    如果 `LLMCALL_CONFIG` 那一档没命中,`bodies_resolution()` 的 dir 会是 None,
    而 None 天然可序列化 —— 上面那条会全绿,却完全没有覆盖到它要防的那个状态。
    """
    bodies = tmp_path / "companion" / "data" / "bodies"
    bodies.mkdir(parents=True)
    monkeypatch.setenv("LLMCALL_CONFIG", str(tmp_path / "companion"))
    monkeypatch.delenv("TASK_CONSOLE_LLMCALL_BODIES", raising=False)
    res = llmstats.bodies_resolution()
    assert res["source"] == "LLMCALL_CONFIG", res
    assert res["dir"] is not None and str(bodies) == res["dir"], res
