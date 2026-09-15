"""一次读取的条数上限,两条路径必须用同一个数。

运行日志有两条读法:快路直接查事件日志,读不成就回落到 PowerShell 脚本。
上限原来只传给了其中一条 —— 回落那条不传 `-MaxEvents`,于是吃脚本自己的默认值,
比快路小二十五倍。同一次摄入,走哪条路决定了它能看到多少事件,
而没有任何一处会说出走的是哪条、上限是多少。

这不是「忘了传一个参数」。禁止这么做的注释在页面那一侧的同名调用处就写着,
只是没有落到摄入这一侧:**一个只修在一处的规则,和一条没有的规则,
区别只是它看起来已经修过了。**

所以这里不测「上限等于某个数」——那会把一个可调的参数钉死。
测的是**两条路径拿到同一个数**。
"""

from __future__ import annotations

import os
import sys

import pytest

_SCR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "scripts", "task_console")
sys.path.insert(0, _SCR)

import console_ingest as CI  # noqa: E402


def test_both_read_paths_get_the_same_cap(monkeypatch):
    """快路和回落路径必须拿到同一个上限。"""
    seen = {}

    class FakeEvtlog:
        @staticmethod
        def available():
            return False, "测试里刻意让快路不可用"

        @staticmethod
        def read(days, max_events):
            seen["fast"] = max_events
            return {}, "evtlog"

    monkeypatch.setitem(sys.modules, "evtlog", FakeEvtlog)

    def fake_run_ps(script, args=None, timeout=None):
        seen["ps_args"] = list(args or [])
        return 0, '{"enabled": true, "tasks": {}}', ""

    monkeypatch.setattr(CI, "run_ps", fake_run_ps)
    CI._read_runlog(days=30)

    args = seen["ps_args"]
    assert "-MaxEvents" in args, (
        "回落路径没有传上限,于是它吃脚本自己的默认值 —— "
        f"实际参数: {args}")
    cap = int(args[args.index("-MaxEvents") + 1])
    assert cap == CI.MAX_EVENTS

    # 再走一次快路,断言它拿到的是同一个数。
    FakeEvtlog.available = staticmethod(lambda: (True, None))
    CI._read_runlog(days=30)
    assert seen["fast"] == CI.MAX_EVENTS
    assert seen["fast"] == cap, "两条路径的上限不一样"


def test_days_is_also_passed_through(monkeypatch):
    """负对照:天数本来就传着。

    没有这一条,一个「什么参数都不传」的实现只会让上面那条红,
    而读的人会以为问题出在天数上。
    """
    seen = {}

    class NoFast:
        @staticmethod
        def available():
            return False, "no"

    monkeypatch.setitem(sys.modules, "evtlog", NoFast)
    monkeypatch.setattr(CI, "run_ps",
                        lambda script, args=None, timeout=None: (
                            seen.setdefault("args", list(args or [])),
                            (0, '{"enabled": true, "tasks": {}}', ""))[1])
    CI._read_runlog(days=17)
    args = seen["args"]
    assert "-Days" in args and args[args.index("-Days") + 1] == "17"


def test_the_page_cap_and_the_ingest_cap_are_allowed_to_differ():
    """两个数各自有主,不是同一个数写了两遍。

    页面是一次点击要等的东西,摄入是后台补历史的东西,能承受的时间不一样。
    这条用例存在是为了防**反向**的过度合并:有人看见两个数不同就把它们并成一个,
    那会让页面去等一次几十万条的查询。合并重复是对的,但前提是它们真的是同一件事。
    """
    import server as S
    assert isinstance(CI.MAX_EVENTS, int) and isinstance(S.RUNLOG_MAX_EVENTS, int)
    assert CI.MAX_EVENTS >= S.RUNLOG_MAX_EVENTS
