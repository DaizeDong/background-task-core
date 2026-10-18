"""这台机器上的 powershell.exe 解析成哪一条路径。**只有这一份实现。**

先用钉死的绝对路径,那个文件不在时才退回裸名字 `powershell.exe`。

⚠ 为什么先用绝对路径而不是裸名字:PATH 上可能只有 WindowsApps 的执行别名,
它**能被找到却跑不了任何东西**,在计划任务里表现为静默吊死。
不要为了「简洁」把绝对路径那一档去掉。
"""

from __future__ import annotations

import os

PINNED = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


def powershell() -> str:
    return PINNED if os.path.isfile(PINNED) else "powershell.exe"
