"""维护面板:skill 清单、记忆池用量、插件开关。读是读,写走一张闭合的动作表。

这个文件的危险在于它**移动目录**。所以每一个动作参数都过同一道闸:名字必须落在
一个窄字符集里,并且解析出的真实路径必须是配置根目录的**直接子项**。两条都过不了
就拒绝整个动作,而不是「清洗一下再执行」: 清洗过的参数看起来安全,但没人知道
清洗掉了什么。

所有路径都从环境变量来,没有默认值。没配就是「未检查」,页面照实说,不画一块空的绿板:
一个没被指向任何东西的面板打印的绿色,和一个真的什么问题都没有的面板一模一样。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

# pythonw 下每个控制台子进程都要新分配控制台,又慢又会在并发时超时;这个标志免掉它。
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# 名字闸。skill 目录名和插件名都过这一道。刻意不含路径分隔符、点号开头、空格。
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,79}$")

# 动作表是闭合的。加一个动作必须改这里,而不是拼一个字符串就能多出一个动词。
ACTIONS = ("skill.archive", "skill.restore", "plugin.enable", "plugin.disable",
           "repo.fetch", "repo.reveal", "repo.status", "repo.commitpush",
           "clean.tempgit",
           "memory.archive", "memory.restore")


class Refused(Exception):
    """动作被闸门拒绝。code 说明是哪一道闸拒绝的。"""

    def __init__(self, msg: str, code: str = "refused"):
        super().__init__(msg)
        self.code = code


def _root(env: str) -> Path | None:
    v = os.environ.get(env)
    return Path(os.path.expanduser(v)) if v else None


def _child(root: Path, name: str) -> Path:
    """把一个名字解析成 root 的直接子项,解析不出来就拒绝。

    比对的是 abspath 之后的父目录,不是字符串前缀,所以挡得住 '..'。
    刻意不解析最后一段:skill 常以 junction 部署,判的是「名字挂在 root 下」,
    不是「它指向哪里」。所以这里不挡最后一段本身是链接的情况。
    """
    if not SAFE_NAME.match(name or ""):
        raise Refused(f"名字不合法: {name!r}", "bad_name")
    p = (root / name)
    try:
        parent = os.path.dirname(os.path.abspath(p))
        root_abs = os.path.abspath(root)
    except OSError as e:
        raise Refused(f"解析不了路径: {e}", "unresolvable") from e
    if os.path.normcase(parent) != os.path.normcase(root_abs):
        raise Refused("目标不是配置根目录的直接子项", "not_child")
    return p


def _is_link(p: Path) -> bool:
    """junction 在 Windows 上不是 symlink,islink() 认不出来。真值是「realpath 变没变」。"""
    try:
        return os.path.normcase(os.path.realpath(p)) != os.path.normcase(os.path.abspath(p))
    except OSError:
        return False


def _desc_len(skill_dir: Path) -> int | None:
    f = skill_dir / "SKILL.md"
    try:
        txt = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.match(r"^---\r?\n(.*?)\r?\n---", txt, re.S)
    if not m:
        return None
    d = re.search(r"^description:\s*(.*)", m.group(1), re.M)
    return len(" ".join(d.group(1).split()).strip('"')) if d else 0


def read_skills() -> dict:
    root, arch = _root("BACKGROUND_TASK_CORE_SKILLS"), _root("BACKGROUND_TASK_CORE_SKILL_ARCHIVE")
    # 「没设」和「设了但路径不在」分开报,别把坏了的检查说成没检查。
    if not root:
        return {"available": False, "unchecked": True,
                "reason": "没有设 BACKGROUND_TASK_CORE_SKILLS,skill 这一栏是「未检查」。"}
    if not root.is_dir():
        return {"available": False,
                "reason": f"skill 目录不存在或读不了: {root}"}
    live, budget, unreadable = [], 0, 0
    for name in sorted(os.listdir(root)):
        d = root / name
        if not (d / "SKILL.md").is_file():
            continue
        # None = 描述读不出来,0 = 真没写描述;读不出的单独计数,预算判定因此不出结论。
        raw = _desc_len(d)
        n = raw or 0
        if raw is None:
            unreadable += 1
        budget += n + len(name)
        live.append({"name": name, "chars": n + len(name),
                     "descUnreadable": raw is None,
                     "linked": _is_link(d), "archived": False})
    archived = []
    if arch and arch.is_dir():
        for name in sorted(os.listdir(arch)):
            if (arch / name / "SKILL.md").is_file():
                archived.append({"name": name, "chars": 0, "linked": _is_link(arch / name), "archived": True})
    from .runs import resource_verdict
    return {"available": True, "root": str(root), "archiveSet": bool(arch),
            "skills": live + archived, "liveCount": len(live),
            "archivedCount": len(archived), "budgetChars": budget, "budgetLimit": 18600,
            "budgetPct": budget / 18600 * 100,
            "verdict": resource_verdict(None if unreadable else budget, warning=15000, critical=18600),
            # 单独报,不并进 0:预算条旁边要能看出「这个数字是不完整的」。
            "descUnreadable": unreadable}


def _claude() -> str | None:
    v = os.environ.get("BACKGROUND_TASK_CORE_CLAUDE")
    if v and os.path.isfile(os.path.expanduser(v)):
        return os.path.expanduser(v)
    return shutil.which("claude")


def read_plugins(timeout: int = 40) -> dict:
    """Read the CLI's structured inventory; an empty JSON list is a verified zero."""
    exe = _claude()
    if not exe:
        return {"available": False, "unchecked": True, "reason": "找不到 claude 可执行文件，插件未检查"}
    try:
        result = subprocess.run([exe, "plugin", "list", "--json"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout,
                                stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        if result.returncode:
            return {"available": False, "reason": f"插件清单退出 {result.returncode}"}
        rows = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return {"available": False, "reason": f"插件清单格式或读取失败: {type(error).__name__}"}
    if not isinstance(rows, list):
        return {"available": False, "reason": "插件清单格式不是数组"}
    plugins = []
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not isinstance(row.get("enabled"), bool)
                or row.get("scope") not in ("user", "project", "local", "managed")):
            return {"available": False, "reason": "插件清单缺少名称、范围或启用状态"}
        removal, removal_reason = None, "请在所属项目管理"
        if row['scope'] == 'user':
            if row['id'].endswith('@skills-dir'):
                skill_name = row['id'].removesuffix('@skills-dir')
                root = _root('BACKGROUND_TASK_CORE_SKILLS')
                if (root and SAFE_NAME.fullmatch(skill_name) and row.get('installPath')
                        and Path(os.path.abspath(root / skill_name)) == Path(os.path.abspath(row['installPath']))):
                    removal = {'kind': 'skill', 'name': skill_name, 'location': 'live'}
                removal_reason = '由技能目录加载；请在所属技能目录删除'
            else:
                removal = {'kind': 'plugin', 'name': row['id'], 'location': 'user'}
                removal_reason = ''
        plugins.append({"name": row["id"], "enabled": row["enabled"], "scope": row["scope"],
                        "removal": removal, "removalReason": removal_reason,
                        "issues": [str(error) for error in row.get('errors', [])] if isinstance(row.get('errors'), list) else [],
                        **{key: row.get(key) for key in ("version", "installPath", "installedAt", "lastUpdated")}})
    return {"available": True, "plugins": plugins}


def read_all() -> dict:
    from . import runs as component_status
    from .integrations import read_many
    return read_many({'skills': read_skills,
                      'plugins': read_plugins,
                      'components': component_status.read_configured})


def act(action: str, name: str, arg: str | None = None) -> dict:
    if action not in ACTIONS:
        raise Refused(f"不在动作表里: {action!r}", "bad_action")
    if action.startswith("memory."):
        # 归档不在这里实现:它是一个三步的生命周期迁移,而那份逻辑已经存在于一个
        # 专门的脚本里。再写一份的结果一定是两份实现慢慢分叉,然后其中一份在没人
        # 注意的时候开始写出格式不对的索引。这里只负责把按钮转成对它的一次调用。
        from . import memops
        return memops.act(action.split(".", 1)[1], name)
    if action == "clean.tempgit":
        # 唯一一个删除动作。它不接受名字参数:删哪些由 sysinfo 自己按名字形状 + 年龄
        # 判定,而不是由调用方指定路径。一个接受路径的删除接口迟早会被喂进一条别的路径。
        from . import sysinfo
        return sysinfo.clean_temp_git()
    if action == "repo.reveal":
        # 只是把资源管理器开到那个目录。不改任何东西,所以点错了没有代价,
        # 这正是它该在这张表里的理由:面板的价值一半是「把我带过去」。
        from . import repos
        return repos.reveal(name)
    if action == "repo.status":
        from . import repos
        return repos.status(name)
    if action == "repo.fetch":
        # 只读的网络动作。
        from . import repos
        return repos.fetch(name)
    if action == "repo.commitpush":
        # 唯一一个把东西送出这台机器的动作,所以是两步:先出只读计划
        # (repo.commitpush.plan),执行时必须带着计划里那份完整快照回来,对不上就整个拒绝。
        # arg 是 JSON: {"message": ..., "expect": <计划返回的完整快照>}。
        from . import repos
        try:
            payload = json.loads(arg or "{}")
        except ValueError:
            raise Refused("参数不是合法 JSON", "bad_args")
        if not isinstance(payload, dict):
            raise Refused("参数必须是对象", "bad_args")
        return repos.commit_push(name, payload.get("message") or "", payload.get("expect"))
    if action.startswith("skill."):
        root, arch = _root("BACKGROUND_TASK_CORE_SKILLS"), _root("BACKGROUND_TASK_CORE_SKILL_ARCHIVE")
        if not root or not arch:
            raise Refused("没有配 BACKGROUND_TASK_CORE_SKILLS / BACKGROUND_TASK_CORE_SKILL_ARCHIVE,拒绝移动任何目录", "no_config")
        arch.mkdir(parents=True, exist_ok=True)
        src_root, dst_root = (root, arch) if action == "skill.archive" else (arch, root)
        src, dst = _child(src_root, name), _child(dst_root, name)
        if not src.is_dir():
            raise Refused(f"源目录不存在: {name}", "missing_src")
        if dst.exists():
            raise Refused(f"目标已存在,不覆盖: {name}", "dst_exists")
        os.rename(src, dst)
        return {"ok": True, "moved": f"{src} -> {dst}"}

    exe = _claude()
    if not exe:
        raise Refused("找不到 claude 可执行文件", "no_claude")
    if not SAFE_NAME.fullmatch(name or ""):
        raise Refused(f"插件名不合法: {name!r}", "bad_name")
    verb = "enable" if action == "plugin.enable" else "disable"
    inventory = read_plugins()
    if not inventory.get('available'):
        raise Refused(inventory.get('reason') or '插件清单不可用', 'unavailable')
    matches = [row for row in inventory['plugins'] if row['name'] == name and row['scope'] == 'user']
    if len(matches) != 1:
        raise Refused('无法唯一确认当前用户范围的插件，请刷新清单', 'missing_plugin')
    enabled = verb == 'enable'
    if matches[0]['enabled'] == enabled:
        return {'ok': True, 'message': '插件已处于所选状态，无需重复修改'}
    r = subprocess.run([exe, "plugin", verb, name, "--scope", "user"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=90, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise Refused(f"claude plugin {verb} 退出 {r.returncode}: {(r.stderr or r.stdout or '').strip()[:200]}", "plugin_failed")
    refreshed = read_plugins()
    verified = [row for row in refreshed.get('plugins', []) if row['name'] == name and row['scope'] == 'user']
    if not refreshed.get('available') or len(verified) != 1 or verified[0]['enabled'] != enabled:
        return {'ok': False, 'error': '命令已执行，但无法确认插件状态已更新；请刷新核对', 'uncertain': True}
    return {"ok": True, "out": (r.stdout or "").strip()[:400],
            "message": ('插件已启用' if enabled else '插件已禁用') + '；现有会话需重新加载插件或开启新会话'}

