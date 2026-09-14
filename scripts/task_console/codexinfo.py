"""Codex 侧的维护体征:指令文件、配置、会话、日志与缓存。

这个模块只读,一个字节都不写。它存在的理由是维护屏原本只有 Claude Code 那半边,
而这台机器上两套 agent 是并排跑的 —— 只量一半,另一半涨到什么程度没有任何地方会说。

三条约束,每一条都来自这个仓里已经踩过的坑:

**根目录只从环境变量来,没有默认值。** 没配就是「未检查」,照实说出来。写死一个
`~/.codex` 当兜底,等于让这个公开仓里出现一条真实机器路径,同时让「没配」和
「配了但那个目录是空的」打印出同一块绿板。

**每一项各自带 available 与 reason。** 一次 OSError 不能让整屏塌成「没数据」:
会话目录读不了和会话目录是空的,要采取的行动相反。所以每一项自己说自己的话。

**数不完的时候要说自己没数完。** 目录树可能深、可能有权限拒绝的子项。扫不动的
条目计入 `errors`,而体积随之标记为偏小 —— 一个悄悄跳过了一半的总量,
和一个真的只有那么大的总量,在屏幕上长得一模一样。
"""

from __future__ import annotations

import os
from pathlib import Path

# 会话与归档会话的目录名。Codex 把活跃与归档分开放,而「归档的能不能删」
# 和「活跃的能不能删」是两个完全不同的问题,所以这里也分开数,不合并成一个总量。
SESSION_DIRS = ("sessions", "archived_sessions")

# 单独点名的文件。指令文件和配置是维护这套东西时真正会去改的那两个,
# 其余目录只报体积。
NAMED_FILES = ("AGENTS.md", "config.toml")

# 只报体积的目录。
SIZED_DIRS = ("log", "cache")

# 一场会话落成一份转录。真实的库是 sessions/YYYY/MM/DD/*.jsonl,
# 所以场数只能递归地数,不能数顶层子项 —— 数顶层会把 2380 份转录报成「2 场」。
TRANSCRIPT_SUFFIX = ".jsonl"


def _root() -> Path | None:
    v = os.environ.get("TASK_CONSOLE_CODEX")
    return Path(os.path.expanduser(v)) if v else None


def _walk_size(d: Path) -> tuple[int, int, int, int]:
    """返回 (字节, 文件数, 扫不动的条目数, 转录文件数)。

    扫不动的那个数字必须一路传到页面上。这个仓里同一个形状栽过不止一次:
    `except OSError: continue` 让总量安静地变小,而调用方拿到的是一个看起来
    正常的数。

    转录数和文件数分开返回,因为会话目录里除了转录还有别的东西,
    而「有多少场」和「有多少文件」是两个问题。
    """
    total = files = errors = transcripts = 0
    stack = [d]
    while stack:
        cur = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except OSError:
            errors += 1
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    stack.append(Path(e.path))
                elif e.is_file(follow_symlinks=False):
                    total += e.stat().st_size
                    files += 1
                    if e.name.endswith(TRANSCRIPT_SUFFIX):
                        transcripts += 1
            except OSError:
                errors += 1
    return total, files, errors, transcripts


def _file_entry(p: Path) -> dict:
    if not p.exists():
        # 「不在」是一个结论,不是一次失败。指令文件不在是完全正常的状态,
        # 而它和「读不了」要做的事不同,所以分开说。
        return {"available": True, "exists": False}
    try:
        st = p.stat()
    except OSError as e:
        return {"available": False, "reason": f"读不了({e.__class__.__name__})"}
    return {"available": True, "exists": True, "bytes": st.st_size, "mtime": st.st_mtime}


def _dir_entry(p: Path, count_transcripts: bool = False) -> dict:
    if not p.exists():
        return {"available": True, "exists": False}
    if not p.is_dir():
        return {"available": False, "reason": "存在但不是目录"}
    total, files, errors, transcripts = _walk_size(p)
    out = {"available": True, "exists": True, "bytes": total, "files": files,
           "errors": errors}
    if count_transcripts:
        # 场数 = 递归数转录文件,一场一份。
        #
        # ⚠ 这里第一版写的是「顶层子项数」,理由是「一场会话可能落成多个文件」。
        # 那个理由是凭空想的,而真实的库是 sessions/YYYY/MM/DD/*.jsonl ——
        # 顶层子项是两个年份目录,于是 3.1G、2380 份转录会被印成「2 场」。
        # 一个自信的错数字比没有数字更糟,而它之所以能活到这一步,
        # 是因为我给测试造的 fixture 是平铺的,而生产是三层嵌套的:
        # **自己造的输入会让测试全绿,而闸门在生产里失效。**
        out["count"] = transcripts
    return out


def read() -> dict:
    root = _root()
    if not root:
        return {"available": False,
                "reason": "没有设 TASK_CONSOLE_CODEX,Codex 这一侧是「未检查」。"}
    if not root.is_dir():
        return {"available": False,
                "reason": f"Codex 目录不存在或读不了: {root}"}

    items: dict[str, dict] = {}
    for name in NAMED_FILES:
        items[name] = _file_entry(root / name)
    for name in SESSION_DIRS:
        items[name] = _dir_entry(root / name, count_transcripts=True)
    for name in SIZED_DIRS:
        items[name] = _dir_entry(root / name)

    hist = root / "history.jsonl"
    items["history.jsonl"] = _file_entry(hist)

    # 总体积只加**数出来了**的那些。加的时候顺带记住有没有哪一项没数全,
    # 好让页面在总量旁边说出「这个数偏小」,而不是让人以为它是准的。
    total = sum(v.get("bytes", 0) for v in items.values() if v.get("available"))
    incomplete = sorted(k for k, v in items.items() if v.get("errors"))
    unread = sorted(k for k, v in items.items() if not v.get("available"))
    return {"available": True, "root": str(root), "items": items,
            "bytes": total, "incomplete": incomplete, "unread": unread}
