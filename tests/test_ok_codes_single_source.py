#!/usr/bin/env python3
"""「哪些退出码算成功」只能有一份表。

清单里这个声明有两个键名 —— `ok_codes` 和 `ok_exit_codes` —— 两个都在真实清单里出现过。
`freshness.py` 一直两个都认;`server.py` 只认第一个。于是声明成 `ok_exit_codes` 的任务
在渲染那条通路上被静默丢掉:同一个退出码,任务表标红、summary.bad +1、实成功率被拉低,
而新鲜度面板判绿。**同一屏两个自称权威的结论,没有任何一处对账。**

这不是假想:2026-09-09 在一台真实机器的清单上,一个任务声明的正是
`ok_exit_codes` 这一种拼写(它的 `_note` 写明某几个非零码是工具在报告而不是任务坏了),
于是它在两块面板上一红一绿。**具体是哪个任务不写进这里**:
这是公开仓,而操作者真实的自动化清单不是这里该出现的东西。

所以这里钉两件事:
  1. 键名表只有一份(`freshness.OK_CODE_KEYS`),没有第二处硬编码;
  2. 两条通路对同一份声明给出同一个答案,两种拼写都是。
"""
import io
import os
import re
import sys

import pytest

_SCR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "scripts", "task_console")
sys.path.insert(0, _SCR)

import freshness as F  # noqa: E402
import server as S  # noqa: E402


@pytest.mark.parametrize("decl,want", [
    ({"ok_codes": [2, 3]}, [2, 3]),
    ({"ok_exit_codes": [0, 3, 5]}, [0, 3, 5]),
    # 两个都写时合并去重,并保持声明顺序 —— 一个把它们当互斥的读取器会丢掉一半。
    ({"ok_codes": [2, 3], "ok_exit_codes": [3, 5]}, [2, 3, 5]),
    ({}, []),
    # 脏数据不许让整条声明消失:能转成整数的留下,不能的跳过。
    ({"ok_codes": [2, "3", None, "x"]}, [2, 3]),
])
def test_declared_ok_codes_reads_both_spellings(decl, want):
    assert F.declared_ok_codes(decl) == want


@pytest.mark.parametrize("key", ["ok_codes", "ok_exit_codes"])
# ⚠ 声明里**刻意不含 0**,而 rc=0 那一行刻意期望 True。
# 第一版参数是 [0, 3],于是「0 永远算成功」这条规则在每个输入上都不起作用 ——
# 把 server 侧那句 `freshness._ok_codes(...)` 换成裸 `set(codes)`,12 条用例照样全绿。
# 一条规则要被测到,输入必须是「只有这条规则能给出正确答案」的那种。
@pytest.mark.parametrize("rc,ok", [(0, True), (3, True), (4, False)])
def test_both_readers_agree_on_the_same_declaration(key, rc, ok):
    """渲染通路和新鲜度通路必须对同一份声明给出同一个答案。

    ⚠ 这条用例的第一版**根本没跑到 server**:它自己用 `",".join(...)` 再解回来
    模拟了一遍渲染侧的逻辑,两边其实都在调 freshness。
    也就是说它宣称在对账「两个独立的消费者」,而被对账的那一侧是测试自己写的。
    server.py 那两份手写解析(各自还 hand-add 一次 {0})从来没有被这条用例覆盖过。

    现在渲染侧真的走 `server._okset`。而顺带一提:server 已经改成借用
    `freshness._ok_codes`,所以这条用例现在证明的是「那条借用还在」,
    而不是「两份手写的实现碰巧相等」—— 后者才需要天天对账,前者一眼看得出来。
    """
    import server as S
    decl = {key: [3]}          # 不含 0:让「0 永远算成功」那条规则成为唯一解释
    fresh_ok = rc in F._ok_codes(decl)
    # 渲染侧:server 把声明拼成字符串挂在行上,再由 _okset 解回集合。
    row = {"okCodes": ",".join(str(x) for x in F.declared_ok_codes(decl))}
    render_ok = rc in S._okset(row)
    assert fresh_ok is ok, f"新鲜度侧对 {key}={decl[key]} rc={rc} 判错"
    assert render_ok is ok, f"渲染侧对 {key}={decl[key]} rc={rc} 判错"
    assert fresh_ok == render_ok


def test_server_has_exactly_one_place_that_parses_the_ok_codes_string():
    """解析点只许有一处。

    原来有两处,各自 hand-add {0} —— 一条规则三个副本(freshness 一份、server 两份),
    而它决定的是一个任务算不算失败。合并成一处之后,这条用例防的是它再分裂。
    判据是「有几处在把那个逗号串拆开」,不是「有没有写 {0}」:
    后者在注释里也会出现,而注释不参与执行。
    """
    src = io.open(os.path.join(_SCR, "server.py"), encoding="utf-8").read()
    splits = [ln.strip() for ln in src.splitlines()
              if 'okCodes' in ln and '.split(' in ln and not ln.lstrip().startswith("#")]
    assert len(splits) == 1, "把 okCodes 串拆开的地方不止一处: " + " | ".join(splits)


def test_no_second_hardcoded_copy_of_the_key_names():
    """除了那张表,别处不许再硬编码这两个键名。

    这一条防的是「下次又有人写一个 e.get('ok_codes')」。一份表被抄成两份,
    就又回到了两个自称权威的答案 —— 而那种缺陷不在抄的那天暴露。
    """
    bad = []
    for fn in ("server.py", "history.py", "console_ingest.py", "maint.py", "retire.py"):
        p = os.path.join(_SCR, fn)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if line.lstrip().startswith("#"):
                    continue          # 讲这条规则的注释不算违规
                if re.search(r'''\.get\(\s*["']ok_(exit_)?codes["']''', line):
                    bad.append(f"{fn}:{i}: {line.strip()[:90]}")
    assert not bad, ("这些地方绕过 freshness.declared_ok_codes 直接读键名:\n  "
                     + "\n  ".join(bad))
    # 表本身必须还在,否则这条检查什么都没在保护。
    assert set(F.OK_CODE_KEYS) == {"ok_codes", "ok_exit_codes"}


def test_a_manifest_using_both_spellings_is_read_whole(tmp_path, monkeypatch):
    """一份两种拼写混用的清单,必须被完整读出来。

    ⚠ 这条用例的第一版去读**这台机器上真实的**健康清单,并把它的路径写进了公开仓 ——
    pii_guard 当场挡下了提交,挡得对。正确修法是改设计而不是加白名单:
    清单路径本来就该由环境变量注入,测试自己造一份合成的喂进去。
    (顺带,原来那条只断言「至少有一种拼写存在」,在只有一种拼写的机器上恒真 ——
     一个跟着别人机器状态走的断言,既不可移植也不一定能失败。)
    """
    import json

    manifest = {"tasks": [
        {"name": "AcmeSyncJob", "ok_codes": [2, 3]},
        {"name": "AcmeReportJob", "ok_exit_codes": [0, 3, 5]},
        {"name": "AcmeMixedJob", "ok_codes": [1], "ok_exit_codes": [7]},
        {"name": "AcmePlainJob"},
    ]}
    p = tmp_path / "task-health.json"
    p.write_text(json.dumps(manifest, ensure_ascii=True), encoding="ascii")
    monkeypatch.setenv("TASK_CONSOLE_HEALTH", str(p))

    health, warn = S.load_health()
    assert not warn, warn
    got = {n: F.declared_ok_codes(e) for n, e in health.items()}
    assert got["AcmeSyncJob"] == [2, 3]
    assert got["AcmeReportJob"] == [0, 3, 5]      # 只认 ok_codes 时这里会是 []
    assert got["AcmeMixedJob"] == [1, 7]          # 两种拼写合并
    assert got["AcmePlainJob"] == []              # 负对照:没声明就是空,不是默认放行
