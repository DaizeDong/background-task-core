"""Previewed maintenance deletion. Plans are short-lived and never accept client paths."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import threading
import time

import maint

_LOCK = threading.RLock()
_PLANS: dict[str, dict] = {}
_TTL = 300


def _stamp(path: Path):
    info = path.lstat()
    tag = getattr(info, "st_reparse_tag", 0)
    linked = stat.S_ISLNK(info.st_mode) or bool(tag)
    if tag and tag not in (stat.IO_REPARSE_TAG_SYMLINK, stat.IO_REPARSE_TAG_MOUNT_POINT):
        raise maint.Refused("不支持此类型的目录重解析点", "unsupported_link")
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, tag, os.readlink(path) if linked else None]


def _skill(name: str, location: str):
    env = {"live": "TASK_CONSOLE_SKILLS", "archive": "TASK_CONSOLE_SKILL_ARCHIVE"}.get(location)
    if not env or not maint._root(env):
        raise maint.Refused("技能位置未配置", "no_config")
    root = maint._root(env).resolve(strict=True)
    path = maint._child(root, name)
    # Windows aliases trailing dots/spaces; reject rather than silently target another name.
    if name.endswith((".", " ")):
        raise maint.Refused("名字不合法", "bad_name")
    records = []
    files = size = links = 0

    def walk(item):
        nonlocal files, size, links
        if len(records) >= 50000:
            raise maint.Refused("目录超过 50000 项，请使用专门的文件管理工具", "too_large")
        stamp = _stamp(item)
        records.append([str(item.relative_to(path)), stamp])
        if stamp[-1] is not None:
            links += 1
        elif stat.S_ISDIR(stamp[2]):
            # A checkout belongs to its repository manager, not the skill registration UI.
            if (item / ".git").exists():
                raise maint.Refused("目标包含 Git 仓库；请移除技能联接或在仓库工具中管理源码", "repository")
            for child in sorted(item.iterdir()):
                walk(child)
        elif stat.S_ISREG(stamp[2]):
            files += 1
            size += stamp[3]
        else:
            raise maint.Refused("目录包含不支持的文件类型", "unsupported_file")

    walk(path)
    if not stat.S_ISDIR(records[0][1][2]) and records[0][1][-1] is None:
        raise maint.Refused("目标不是技能目录", "not_skill")
    fingerprint = hashlib.sha256(json.dumps([str(root), _stamp(root)[:3], records], sort_keys=True).encode()).hexdigest()
    linked = records[0][1][-1] is not None
    return {"path": str(path), "fingerprint": fingerprint, "files": files, "bytes": size,
            "links": links, "linked": linked, "target": records[0][1][-1] if linked else None,
            "message": "仅移除技能联接，保留目标目录及其全部内容" if linked else "永久删除该技能目录及其文件；内部联接只移除联接本身"}


def _plugin(name: str, location: str):
    if location != "user" or not maint.SAFE_NAME.fullmatch(name or ""):
        raise maint.Refused("此入口仅卸载当前用户范围内的已登记插件", "bad_scope")
    if name.endswith('@skills-dir'):
        raise maint.Refused('此条目由技能目录加载，请使用技能删除预览', 'directory_loaded')
    result = maint.read_plugins()
    if not result.get("available"):
        raise maint.Refused(result.get("reason", "插件清单不可用"), "unavailable")
    matches = [row for row in result["plugins"] if row["name"] == name and row.get("scope") == location]
    if len(matches) != 1:
        raise maint.Refused("无法唯一确认此范围的已安装插件", "missing_plugin")
    row = matches[0]
    return {"fingerprint": hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest(),
            "path": row.get("installPath"), "version": row.get("version"),
            "message": "通过插件管理器卸载当前用户的安装，保留插件运行数据，不清理其他插件依赖"}


def _inspect(kind, name, location):
    if kind == "skill":
        return _skill(name, location)
    if kind == "plugin":
        return _plugin(name, location)
    raise maint.Refused("不支持此删除类型", "bad_kind")


def plan(kind: str, name: str, location: str) -> dict:
    with _LOCK:
        now = time.monotonic()
        for token in list(_PLANS):
            if _PLANS[token]["expires"] <= now:
                del _PLANS[token]
        if len(_PLANS) >= 32:
            raise maint.Refused("待确认预览过多，请稍后重新预览", "plan_limit")
        details = _inspect(kind, name, location)
        token = secrets.token_urlsafe(32)
        _PLANS[token] = {"kind": kind, "name": name, "location": location,
                         "details": details, "expires": now + _TTL}
        return {"ok": True, "kind": kind, "name": name, "location": location,
                "token": token, "expiresIn": _TTL, **{k: v for k, v in details.items() if k != "fingerprint"}}


def apply(token: str) -> dict:
    with _LOCK:
        saved = _PLANS.pop(token, None) if isinstance(token, str) else None
        if not saved or saved["expires"] <= time.monotonic():
            raise maint.Refused("预览不存在或已过期，请重新预览", "stale_plan")
        kind, name, location = (saved[key] for key in ("kind", "name", "location"))
        current = _inspect(kind, name, location)
        if current != saved["details"]:
            raise maint.Refused("目标自预览后发生变化，请重新预览", "changed_target")
        if kind == "skill":
            path = Path(current["path"])
            try:
                if current["linked"]:
                    if path.is_symlink():
                        path.unlink()
                    else:
                        path.rmdir()  # Windows junction: never recurse into its target.
                else:
                    shutil.rmtree(path)  # Python 3.11+ does not traverse directory junctions.
            except OSError as error:
                return {"ok": False, "partial": True, "error": str(error),
                        "message": "删除中断，部分内容可能已删除，请刷新核对后重新预览"}
            if os.path.lexists(path):
                return {"ok": False, "error": "删除后目标仍存在，请刷新核对", "partial": True}
            return {"ok": True, "deleted": 1, "message": "技能联接已移除，源码保留" if current["linked"] else "技能目录已删除"}
        exe = maint._claude()
        if not exe:
            raise maint.Refused("找不到插件管理器", "no_claude")
        result = subprocess.run([exe, "plugin", "uninstall", name, "--scope", location, "--keep-data", "--json"],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
                                stdin=subprocess.DEVNULL, creationflags=maint._NO_WINDOW)
        if result.returncode:
            return {"ok": False, "error": f"插件卸载退出 {result.returncode}: {(result.stderr or result.stdout)[:400]}"}
        refreshed = maint.read_plugins()
        if not refreshed.get("available"):
            return {"ok": False, "error": "已执行卸载，但无法读取清单确认结果，请刷新核对"}
        if any(row["name"] == name and row.get("scope") == location for row in refreshed["plugins"]):
            return {"ok": False, "error": "插件管理器返回成功，但此安装仍在清单中"}
        return {"ok": True, "deleted": 1, "message": "当前用户的插件已卸载，运行数据保留；下次会话生效"}
