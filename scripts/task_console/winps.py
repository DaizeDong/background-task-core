"""这台机器上的 powershell.exe 解析成哪一条路径。**只有这一份实现。**

⚠ 这里原来有三份,而且它们不一样:server 和 retire 认 `TASK_CONSOLE_POWERSHELL`,
摄入器那份不认 —— 而 README 的变量表里写着它生效。于是一台设了这个变量的机器上,
摄入器安安静静地用另一个解释器跑,而页面上没有任何一处会显示这个差别。
三份手写的同一条解析,没有任何东西对账;这一份存在的意义就是让那种差别
**不可能再发生**,而不是让它发生时有人喊一声。

顺序有讲究,三档各自有理由:

1. `TASK_CONSOLE_POWERSHELL` —— 显式覆盖。设了就用它,不做存在性检查:
   人明确指定了一条路径而它不存在时,正确的表现是让调用处带着真实的错误失败,
   不是安静地退回另一个解释器然后用一个**没人选过的**解释器把活干了。
2. 钉死的绝对路径 —— 正常形态。
3. 裸名字 `powershell.exe` —— 只在上一条那个文件不在时兜底。

⚠ 为什么第 2 档是绝对路径而不是一开始就用裸名字:PATH 上可能只有 WindowsApps 的
执行别名,它**能被找到却跑不了任何东西**,在计划任务里表现为静默吊死。
这个坑这套东西已经踩过,不要为了「简洁」把绝对路径那一档去掉。
"""

from __future__ import annotations

import os

PINNED = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
ENV = "TASK_CONSOLE_POWERSHELL"


def powershell() -> str:
    v = os.environ.get(ENV)
    if v:
        return v
    return PINNED if os.path.isfile(PINNED) else "powershell.exe"
