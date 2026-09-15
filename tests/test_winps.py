"""解释器解析:一份实现,而且每个消费者拿到的是同一份。

这一条来自一个真实的不一致:三个模块各写了一份 powershell 解析,其中摄入器那份
**不认 `TASK_CONSOLE_POWERSHELL`**,而 README 的变量表里写着它生效。
于是一台设了这个变量的机器上,摄入器安安静静地用另一个解释器跑,
而页面上没有任何一处会显示这个差别 —— 不报错,不告警,只是换了个程序在跑任务。

所以这里钉两件事:
  1. 解析本身的三档行为(覆盖 / 钉死的绝对路径 / 兜底),各配一条能失败的用例;
  2. **三个消费者拿到的是同一个函数对象**,不是三个碰巧相等的实现。
     第二条才是这组用例真正在防的东西:三份实现可以在今天逐位相等,
     而明天有人只改其中一份。
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "scripts", "task_console"))

import winps  # noqa: E402


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv(winps.ENV, r"D:\somewhere\pwsh.exe")
    assert winps.powershell() == r"D:\somewhere\pwsh.exe"


def test_env_override_is_not_existence_checked(monkeypatch, tmp_path):
    """人明确指定了一条路径而它不存在时,要带着真实的错误失败。

    静默退回另一个解释器的后果是:活被一个**没人选过的**程序干了,而且干成了 ——
    于是没有任何迹象表明那条配置根本没生效。
    """
    ghost = str(tmp_path / "does-not-exist.exe")
    monkeypatch.setenv(winps.ENV, ghost)
    assert winps.powershell() == ghost


def test_falls_back_to_the_pinned_absolute_path(monkeypatch):
    monkeypatch.delenv(winps.ENV, raising=False)
    monkeypatch.setattr(os.path, "isfile", lambda p: p == winps.PINNED)
    assert winps.powershell() == winps.PINNED


def test_bare_name_only_when_the_pinned_file_is_missing(monkeypatch):
    """负对照:上一条的反面。

    ⚠ 绝对路径那一档不是为了好看。PATH 上可能只有 WindowsApps 的执行别名,
    它**能被找到却跑不了任何东西**,在计划任务里表现为静默吊死。
    去掉那一档之后这条用例仍然过,但上一条会红 —— 两条一起才钉得住顺序。
    """
    monkeypatch.delenv(winps.ENV, raising=False)
    monkeypatch.setattr(os.path, "isfile", lambda p: False)
    assert winps.powershell() == "powershell.exe"


def test_every_consumer_shares_the_one_implementation():
    """三个消费者拿到的必须是**同一个函数对象**。

    这是这组用例的核心。断言「行为相等」是不够的:三份手写实现可以在今天逐位相等,
    而缺陷正是在有人只改其中一份的那天出现,那一天它们仍然会各自通过自己的行为测试。
    身份相等则一改全改,不存在「只改了一份」这种状态。
    """
    import console_ingest
    import retire
    import server

    assert console_ingest.powershell is winps.powershell
    assert server.powershell is winps.powershell
    assert retire._powershell is winps.powershell


def test_no_module_hardcodes_its_own_copy_of_the_pinned_path():
    """除了 winps 自己,别处不许再写死那条路径。

    防的是「下次又有人顺手复制一行」。一份实现被抄成两份,就又回到了
    两个自称权威的答案,而那种缺陷不在抄的那天暴露。
    """
    scr = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "scripts", "task_console")
    needle = "WindowsPowerShell"
    bad = []
    for fn in sorted(os.listdir(scr)):
        if not fn.endswith(".py") or fn == "winps.py":
            continue
        with open(os.path.join(scr, fn), encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if needle in line and not line.lstrip().startswith("#"):
                    bad.append(f"{fn}:{i}: {line.strip()[:80]}")
    assert not bad, "这些地方又写死了一份解释器路径:\n  " + "\n  ".join(bad)
    # 这条检查本身要有对象,否则它在 winps 被改名之后会安静地什么都不查。
    with open(os.path.join(scr, "winps.py"), encoding="utf-8") as f:
        assert needle in f.read()
