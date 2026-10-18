"""这台机器上的 powershell.exe 解析成哪一条路径。**只有这一份实现。**

先用钉死的绝对路径,那个文件不在时才退回裸名字 `powershell.exe`。

⚠ 为什么先用绝对路径而不是裸名字:PATH 上可能只有 WindowsApps 的执行别名,
它**能被找到却跑不了任何东西**,在计划任务里表现为静默吊死。
不要为了「简洁」把绝对路径那一档去掉。
"""

from __future__ import annotations

import os
import subprocess

PINNED = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


def powershell() -> str:
    return PINNED if os.path.isfile(PINNED) else "powershell.exe"


# pythonw 下每个控制台子进程都要新分配控制台:实测单次 4.5 秒,并发时全部超时。这个标志免掉它。
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_ps(script, args=None, timeout: int = 90) -> tuple[int, str, str]:
    """Run a PowerShell script file and return (rc, stdout, stderr).

    Output is decoded as UTF-8, not the locale: PowerShell 5.1 in a non-interactive session emits
    the ANSI codepage by default and Chinese labels come back as mojibake. collect.ps1 pins its side.
    """
    p = subprocess.run(
        [powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script)] + list(args or []),
        capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    return (p.returncode,
            p.stdout.decode("utf-8", "replace").strip(),
            p.stderr.decode("utf-8", "replace").strip())
