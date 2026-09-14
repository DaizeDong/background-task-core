#!/usr/bin/env python3
"""freshness.evaluate 的分叉测试。

这个模块的全部价值在于**它怎么分叉**,所以每个用例都钉住一种分叉,而不是钉一个总数。
凡是曾经因此误判过的形态,下面都有一条对应的用例,注释里写明那次误判长什么样。

负对照贯穿全篇:unknown 必须与 up 分开,坏退出码默认不能被新鲜产物洗掉,
停用的任务不能因为产物陈旧被判红。任何一条被简化掉,对应用例当场变红。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "scripts", "task_console"))

import freshness as F  # noqa: E402

NOW = 1_800_000_000.0
H = 3600.0


def mk(**kw):
    d = {"name": "T", "label": "T", "max_age_hours": 24}
    d.update(kw)
    return d


def rows(**kw):
    r = {"state": "Ready", "last_rc": 0, "last_run": NOW - 1 * H,
         "next_run": NOW + 23 * H, "missed_runs": 0}
    r.update(kw)
    return {"T": r}


def ev(decl, row=None, mtime=None):
    def fake(_path):
        return (mtime, None) if mtime is not None else (None, "读不到产物: 测试注入")
    return F.evaluate([decl], row if row is not None else rows(), NOW, mtime_of=fake)["tasks"][0]


# ---------- 三档年龄 ----------

def test_fresh_artifact_is_up():
    t = ev(mk(artifact="a", artifact_max_age_hours=24), mtime=NOW - 2 * H)
    assert t["state"] == F.UP


def test_just_past_limit_is_grace_not_down():
    # 宽限期存在的理由:一个每 24h 跑一次的任务晚了十分钟不是故障。
    t = ev(mk(artifact="a", artifact_max_age_hours=24), mtime=NOW - 25 * H)
    assert t["state"] == F.GRACE


def test_far_past_limit_is_down():
    t = ev(mk(artifact="a", artifact_max_age_hours=24), mtime=NOW - 90 * H)
    assert t["state"] == F.DOWN


def test_grace_floor_is_at_least_one_hour():
    # 周期取两成会让一个 30 分钟的任务只有 6 分钟宽限,那只会制造抖动。
    t = ev(mk(artifact="a", artifact_max_age_hours=0.5), mtime=NOW - 1.2 * H)
    assert t["state"] == F.GRACE


# ---------- HRESULT 不是退出码 ----------

def test_running_is_not_a_failure():
    # 按「非零即红」去筛,一批正在运行的任务会被当成失败,而它们好好的。
    t = ev(mk(artifact="a", artifact_max_age_hours=1),
           rows(last_rc=F.RC_RUNNING), mtime=NOW - 500 * H)
    assert t["state"] == F.RUNNING


def test_never_run_is_its_own_state():
    # 一个月度任务在创建当天的触发点已过,于是首跑排在下个月。
    # 它既不是 up 也不是 down,把它归进任何一边都会误导。
    t = ev(mk(artifact="a", artifact_max_age_hours=1),
           rows(last_rc=F.RC_NOT_RUN), mtime=NOW - 500 * H)
    assert t["state"] == F.NEVER


def test_disabled_wins_over_stale_artifact():
    t = ev(mk(artifact="a", artifact_max_age_hours=1),
           rows(state="Disabled"), mtime=NOW - 9000 * H)
    assert t["state"] == F.PAUSED


# ---------- 产物不能替退出码背书 ----------

def test_fresh_artifact_does_not_clear_a_bad_rc():
    # .log 型产物通常在失败路径上照写,
    # 所以「产物新鲜」默认洗不掉坏退出码。这条一旦被简化,
    # 那种连挂五天而监控判绿的形态会立刻回来。
    t = ev(mk(artifact="a", artifact_max_age_hours=24), rows(last_rc=126), mtime=NOW - 1 * H)
    assert t["state"] == F.DOWN


def test_declared_success_only_artifact_does_clear_it():
    t = ev(mk(artifact="a", artifact_max_age_hours=24, artifact_written_only_on_success=True),
           rows(last_rc=126), mtime=NOW - 1 * H)
    assert t["state"] == F.UP


def test_authoritative_exit_code_beats_even_that():
    t = ev(mk(artifact="a", artifact_max_age_hours=24, artifact_written_only_on_success=True,
              exit_code_is_authoritative=True),
           rows(last_rc=126), mtime=NOW - 1 * H)
    assert t["state"] == F.DOWN


def test_cannot_prove_success_artifact_does_not_report_up_on_its_own():
    t = ev(mk(artifact="a", artifact_max_age_hours=24, artifact_cannot_prove_success=True),
           rows(last_rc=0, last_run=NOW - 400 * H), mtime=NOW - 1 * H)
    assert t["state"] in (F.GRACE, F.DOWN)


# ---------- 声明的退出码要被认 ----------

@pytest.mark.parametrize("key", ["ok_codes", "ok_exit_codes"])
def test_both_spellings_of_ok_codes_are_honoured(key):
    # 实际的清单里两个键名都出现过。一个只认其中一个名字的读取器
    # 会把另一批任务的声明静默丢掉,然后天天误报,而它看起来完全正常。
    t = ev(mk(artifact="a", artifact_max_age_hours=24, **{key: [2, 3]}),
           rows(last_rc=2), mtime=NOW - 1 * H)
    assert t["state"] == F.UP


def test_code_outside_the_declared_set_still_fails_loudly():
    # 上一条的负对照:声明了 ok_codes 不等于放行一切。
    t = ev(mk(artifact="a", artifact_max_age_hours=24, ok_codes=[2, 3]),
           rows(last_rc=9), mtime=NOW - 1 * H)
    assert t["state"] == F.DOWN


# ---------- 查不成必须与查过了分开 ----------

def test_unreadable_artifact_is_unknown_not_up():
    t = ev(mk(artifact="a", artifact_max_age_hours=24, max_age_hours=None), mtime=None)
    assert t["state"] == F.UNKNOWN


def test_unknown_is_not_up():
    assert F.UNKNOWN != F.UP
    assert F.worst_of(F.UP, F.UNKNOWN) == F.UNKNOWN


def test_empty_artifact_dir_is_unknown_not_1970():
    # 空目录返回 0 会被算成 1970 年,那是一个看起来非常确定的错误答案。
    listing = []
    m, why = F.newest_mtime("d", stat=lambda p: None,
                            listdir=lambda p: listing, isdir=lambda p: True)
    assert m is None and why


def test_task_absent_from_scheduler_is_unknown():
    out = F.evaluate([mk()], {}, NOW, mtime_of=lambda p: (None, "x"))
    assert out["tasks"][0]["state"] == F.UNKNOWN


# ---------- 漏跑是系统替你算好的信号 ----------

def test_missed_runs_does_not_escalate_a_disabled_task():
    # 一个停用的任务会一直累计漏跑次数,照单升级就会被判成 grace。
    # 停用的任务当然一直漏跑,把它画成告警只会造出一个永远亮着、因而没人再看的红点。
    t = ev(mk(artifact="a", artifact_max_age_hours=1),
           rows(state="Disabled", missed_runs=23), mtime=NOW - 9000 * H)
    assert t["state"] == F.PAUSED


def test_missed_runs_does_not_escalate_a_never_run_task():
    t = ev(mk(), rows(last_rc=F.RC_NOT_RUN, missed_runs=5))
    assert t["state"] == F.NEVER


def test_missed_runs_escalates_even_when_fresh():
    t = ev(mk(artifact="a", artifact_max_age_hours=24),
           rows(missed_runs=3), mtime=NOW - 1 * H)
    assert t["state"] != F.UP
    assert any("漏跑" in r for r in t["reasons"])


# ---------- 覆盖率是判据自己的体检 ----------

def test_coverage_counts_only_judged_tasks():
    decls = [mk(name="A", artifact="a", artifact_max_age_hours=24),
             mk(name="B", artifact="b", artifact_max_age_hours=24)]
    rs = {"A": rows()["T"], "B": rows()["T"]}
    out = F.evaluate(decls, rs, NOW, mtime_of=lambda p: (NOW - 1 * H, None))
    assert out["summary"]["coverage"] == 1.0


def test_nothing_judgeable_reports_zero_coverage_not_a_pass():
    # 一个被喂了空的检查器打印的绿色,和一个真没查出问题的检查器打印的绿色一模一样。
    # 这条就是用来把两者分开的。
    out = F.evaluate([mk(name="A")], {}, NOW, mtime_of=lambda p: (None, "x"))
    assert out["summary"]["coverage"] == 0.0
    assert out["summary"]["counts"].get(F.UNKNOWN) == 1


def test_summary_bad_counts_down_and_never():
    decls = [mk(name="A", artifact="a", artifact_max_age_hours=1),
             mk(name="B", artifact="b", artifact_max_age_hours=1)]
    rs = {"A": rows()["T"], "B": dict(rows()["T"], last_rc=F.RC_NOT_RUN)}
    out = F.evaluate(decls, rs, NOW, mtime_of=lambda p: (NOW - 900 * H, None))
    assert out["summary"]["bad"] == 2


# ---------- 「要人管」只能有一份定义 ----------
# 之前 summary.bad 不含 UNKNOWN,而页面上的清单含,于是同一屏出现三个数字:
# 大字格 0、灯板下面写「1 条明细在上面」、清单里真有 1 行。
# UNKNOWN 恰恰是这个项目最在意的那一类(没查成),它在最显眼的那一格里被静默吃掉。

def _decl(name):
    return {"name": name, "artifact": "~/nope", "artifact_max_age_hours": 1}


def test_attention_counts_unknown_but_bad_does_not():
    """两个字段各自说清自己是什么,而不是让一个名字承担两种含义。"""
    # 声明了一个任务,但调度器里没有它 -> UNKNOWN(查不成)
    out = F.evaluate([_decl("Nope")], {}, NOW, mtime_of=lambda p: (NOW - 1 * H, None))
    st = {t["name"]: t["state"] for t in out["tasks"]}
    assert st.get("Nope") == F.UNKNOWN, st
    assert out["summary"]["bad"] == 0, "bad 不含 unknown,这是它原来的含义"
    assert out["summary"]["attention"] == 1, "attention 必须含 unknown"


def test_attention_states_is_published_for_the_page_to_read():
    """页面三处都读这个列表。它不在 payload 里的话,前端会回落到自己写死的一份,
    而那正是这条缺陷的成因。"""
    out = F.evaluate([], {}, NOW)
    assert out["summary"]["attentionStates"] == [F.DOWN, F.NEVER, F.UNKNOWN]


def test_attention_is_a_superset_of_bad():
    """正对照:写反了(比如漏加一项、或把 bad 也改成含 unknown)这条会红,
    而只断言「含 unknown」的那一条不会。"""
    out = F.evaluate([_decl("X"), _decl("Y")], {}, NOW,
                     mtime_of=lambda p: (NOW - 1 * H, None))
    s2 = out["summary"]
    assert s2["attention"] >= s2["bad"]
    assert s2["attention"] == sum(s2["counts"].get(k, 0) for k in s2["attentionStates"])


# ---------- 产物时间戳在未来 ----------
# 系统时钟被回拨,或产物从别的机器拷回来带着未来的 mtime,都会让 (now - mtime) 变成负数,
# 而负龄在任何「比阈值旧吗」的比较里都判新鲜。于是相关任务在时钟追上之前一律绿着,
# **即使它们已经完全停跑** : 一个会持续数小时到数天的假绿,
# 而唯一线索是理由里一个负数。查不成就说查不成,别拿一个算不出意义的数去判绿。
#
# (第一版这三条我自己另造了一份不完整的 row,结果连「正常新鲜」那条都返回 unknown :
#  用文件里现成的 mk/rows/ev,它们已经把 next_run / missed_runs 这些必需字段配齐了。)

def test_a_future_artifact_timestamp_is_unknown_not_fresh():
    r = ev(mk(artifact="~/a", artifact_max_age_hours=1), mtime=NOW + 6 * H)
    assert r["state"] == F.UNKNOWN, r
    assert any("未来" in x for x in r["reasons"]), r["reasons"]


def test_a_tiny_negative_age_is_still_fresh():
    """正对照:刚写完的产物 mtime 比 now 大几秒是正常的(时间戳精度、写入顺序),
    不能因此判成时钟出问题 : 那样这条提示会在每次刚跑完的任务上亮一次。"""
    r = ev(mk(artifact="~/a", artifact_max_age_hours=1), mtime=NOW + 2)
    assert r["state"] == F.UP, r


def test_a_normal_fresh_artifact_is_still_up():
    """再一条正对照:正常的新鲜产物不能被这次改动波及。"""
    r = ev(mk(artifact="~/a", artifact_max_age_hours=10), mtime=NOW - 1 * H)
    assert r["state"] == F.UP, r


# ---------- 同一个任务名下的多条声明,各判各的 ----------
#
# 一个任务把几件不相干的事折叠进来,共用一个退出码和一个产物判据。
# 于是其中任何一件停了,那一行照样绿:宿主的退出码讲的是宿主的事,
# 而折叠进来的活失败时通常被设计成非致命的 —— 记一行,继续走,退 0。
# 这种停摆可以持续很久没人发现,因为屏幕上没有任何一处在单独看它。
#
# 修法不是把它们拆成各自的计划任务:折叠往往有正当理由(比如其中一件必须排在另一件
# 之后,顺序本身是正确性的一部分),拆开会把那个理由连带丢掉。
# 改的是判定的粒度 —— 每件事各写一条声明、各判各的。
# 下面这三条钉住的就是「各判各的」这件事本身。

def _folded(check, artifact, **kw):
    """一条折叠进宿主任务的杂务声明。

    退出码不权威:宿主任务的退出码说的是宿主自己的事,而杂务失败是非致命的,
    两个数字根本不在讲同一件事。产物只在成功路径上写,所以它有资格洗掉宿主的退出码。
    """
    d = mk(check=check, artifact=artifact, artifact_max_age_hours=26,
           artifact_written_only_on_success=True,
           exit_code_is_authoritative=False)
    d.update(kw)
    return d


def test_each_declaration_gets_its_own_verdict(monkeypatch):
    """三件杂务,只有一件的产物过期,那一件红,另外两件不受影响。"""
    ages = {"a.md": NOW - 2 * H, "b.md": NOW - 200 * H, "c.md": NOW - 3 * H}

    def fake(path):
        return (ages[path], None)

    decls = [_folded("chore-a", "a.md"), _folded("chore-b", "b.md"), _folded("chore-c", "c.md")]
    out = F.evaluate(decls, rows(), NOW, mtime_of=fake)["tasks"]
    by = {t["check"]: t for t in out}
    assert len(out) == 3, "三条声明必须产出三条结论,不是合并成一条"
    assert by["chore-b"]["state"] == F.DOWN
    # 负对照:另外两件必须是好的。少了这两句,一个「整组一起判红」的实现照样过。
    assert by["chore-a"]["state"] == F.UP
    assert by["chore-c"]["state"] == F.UP


def test_check_field_is_carried_out_so_the_page_can_name_the_culprit(monkeypatch):
    """光有三条结论不够 —— 它们都叫同一个任务名。

    没有 check,页面只能说「这个任务有问题」,而那个任务底下装着六件不相干的事。
    这正是折叠带来的盲区,补上 check 才算真的补掉。
    """
    out = F.evaluate([_folded("memory-doctor", "r.md")], rows(), NOW,
                     mtime_of=lambda p: (NOW - 300 * H, None))["tasks"][0]
    assert out["check"] == "memory-doctor"
    assert out["name"] == "T"


def test_host_exit_code_does_not_redden_a_folded_chore_that_ran(monkeypatch):
    """宿主任务红,而杂务自己跑完了 —— 杂务不该跟着红。

    这是这套声明能不能用的关键:宿主的退出码讲的是宿主的事。如果它压着所有杂务,
    每条杂务声明都只会复读宿主的红,一件自己的话都说不出来,那和合并成一条没有区别。
    """
    bad = rows(last_rc=1)
    out = F.evaluate([_folded("chore-a", "a.md")], bad, NOW,
                     mtime_of=lambda p: (NOW - 2 * H, None))["tasks"][0]
    assert out["state"] == F.UP

    # 负对照一:同样是宿主退出码 1,但杂务自己的产物过期了 -> 必须红。
    stale = F.evaluate([_folded("chore-a", "a.md")], bad, NOW,
                       mtime_of=lambda p: (NOW - 300 * H, None))["tasks"][0]
    assert stale["state"] == F.DOWN

    # 负对照二:宿主自己那条声明(没有 written_only_on_success)照样吃退出码,
    # 否则这个改动会顺手把宿主的红也洗掉。
    host = F.evaluate([mk(artifact="h.md", artifact_max_age_hours=26)], bad, NOW,
                      mtime_of=lambda p: (NOW - 2 * H, None))["tasks"][0]
    assert host["state"] == F.DOWN
