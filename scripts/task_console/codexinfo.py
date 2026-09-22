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
# 所以场数只能递归地数,不能数顶层子项, 数顶层会把 2380 份转录报成「2 场」。
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
        # 那个理由是凭空想的,而真实的库是 sessions/YYYY/MM/DD/*.jsonl,
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


# ---------------------------------------------------------------------------------------
# 逐份转录的清单,给人自己挑着清。
#
# 刻意**不做自动归档**:归档只是把同一批字节挪到另一个目录,磁盘该满还是满,
# 而且它会替人做出「这份还要不要」的判断, 那个判断没有任何自动化依据。
# 这里只负责把「有哪些、多大、多旧」摆清楚,按哪一维排由看的人当场决定。

# 一次最多交出多少条。2380 条大约 170KB JSON,本机自用扛得住;
# 但上限必须存在,而且**截断了要说出来**, 一个悄悄只给前 N 条的清单,
# 会让人以为剩下的不存在,然后按一个不完整的总量去做清理决定。
LIST_CAP = 3000


def list_transcripts(which: str = "sessions") -> dict:
    """把会话目录下每一份转录摊平成一条记录。只读。

    返回的路径是**相对 root 的**,不是绝对路径。删除接口只收这种相对路径,
    所以这里也只给这种 —— 一个交出绝对路径的列表接口,迟早会被配上一个
    接受绝对路径的删除接口。
    """
    if which not in SESSION_DIRS:
        return {"available": False, "reason": f"只支持 {' / '.join(SESSION_DIRS)}"}
    root = _root()
    if not root:
        return {"available": False,
                "reason": "没有设 TASK_CONSOLE_CODEX,这一栏是「未检查」。"}
    base = root / which
    if not base.is_dir():
        return {"available": True, "exists": False, "items": [], "count": 0, "bytes": 0}

    items: list[dict] = []
    errors = 0
    stack = [base]
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
                elif e.is_file(follow_symlinks=False) and e.name.endswith(TRANSCRIPT_SUFFIX):
                    st = e.stat()
                    items.append({"rel": str(Path(e.path).relative_to(root).as_posix()),
                                  "name": e.name,
                                  "bytes": st.st_size, "mtime": st.st_mtime})
            except OSError:
                errors += 1
    # 默认按体积降序。前端还能按时间重排,但默认给的是「谁在占地方」,
    # 这份清单存在的理由就是那个问题。
    items.sort(key=lambda x: -x["bytes"])
    total = sum(x["bytes"] for x in items)
    cut = items[:LIST_CAP]
    return {"available": True, "exists": True, "which": which,
            "items": cut, "count": len(items), "bytes": total,
            "truncated": len(items) > LIST_CAP,
            "shownBytes": sum(x["bytes"] for x in cut),
            "errors": errors}


def delete_transcripts(rels: list[str]) -> dict:
    """删掉点名的那几份。不可逆。

    只收**相对 root 的路径**,并且逐条重新解析 + 归属检查:
    一个接受路径的删除接口,唯一的控制就是那道归属检查,而它必须在碰文件系统之前开火。
    三条硬规则:
      - 必须落在会话目录里(SESSION_DIRS 之一),别的目录一律拒绝;
      - 必须是转录后缀,不删别的文件;
      - 必须是真实存在的普通文件,不跟符号链接。
    任何一条不过就**拒绝整批**,不做「跳过这条继续删别的」——
    部分成功的删除最难收拾:人不知道到底少了哪些。
    """
    from maint import Refused
    root = _root()
    if not root:
        raise Refused("没有设 TASK_CONSOLE_CODEX", "no_config")
    if not isinstance(rels, list) or not rels:
        raise Refused("没有点名要删哪些", "bad_args")
    if len(rels) > LIST_CAP:
        raise Refused(f"一次最多 {LIST_CAP} 条", "bad_args")

    root_r = root.resolve()
    targets: list[Path] = []
    for rel in rels:
        if not isinstance(rel, str) or not rel or "\\" in rel or rel.startswith("/"):
            raise Refused(f"路径形状不对: {rel!r}", "bad_path")
        segs = rel.split("/")
        if any(s in ("", ".", "..") or ":" in s for s in segs):
            raise Refused(f"路径形状不对: {rel!r}", "bad_path")
        if segs[0] not in SESSION_DIRS:
            raise Refused(f"只允许删会话目录里的东西: {rel!r}", "bad_path")
        if not rel.endswith(TRANSCRIPT_SUFFIX):
            raise Refused(f"只删转录文件: {rel!r}", "bad_path")
        p = (root.joinpath(*segs))
        try:
            rp = p.resolve()
            rp.relative_to(root_r)
        except (ValueError, OSError):
            raise Refused(f"解析出来不在那棵树里: {rel!r}", "bad_path")
        if not p.is_file() or p.is_symlink():
            raise Refused(f"不是一个普通文件: {rel!r}", "bad_path")
        targets.append(p)

    freed = 0
    gone: list[str] = []
    for p, rel in zip(targets, rels):
        try:
            n = p.stat().st_size
        except OSError:
            n = 0
        try:
            p.unlink()
        except OSError as e:
            # 到这一步才失败的,前面已经删掉几条了。说清删了哪些、停在哪里,
            # 不假装什么都没发生。
            return {"ok": False, "deleted": len(gone), "freed": freed,
                    "stoppedAt": rel, "error": f"{e.__class__.__name__}: {e}",
                    "gone": gone[:50]}
        freed += n
        gone.append(rel)
    return {"ok": True, "deleted": len(gone), "freed": freed, "gone": gone[:50]}
