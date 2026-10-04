"""删除计划任务:先给一份绑定的预览,确认之后才动手,删完一项一项读回来报。

「删除」在这台控制台上的意思是**彻底拿掉**:计划程序里没有它,控制台的分类配置里没有它,
健康监控清单、备份和迁移计划里也没有它。只从计划程序里删掉而别处照旧,会留下一份
「换机还原时把它装回来」的备份和一条「它死了没人知道」的监控声明,而页面照常显示正常。

分工是固定的,每一处只由它自己的主人改:

  - 受控制器管理的任务(registration 的声明里有它)只走控制器的 retire 事务:
    停用、COM DeleteTask、改写控制器自己的登记投影、墓碑、日志、回执、凭据库里的加密 XML 原件。
    这个模块不碰那些文件,也不绕过控制器。
  - 不受管理的任务由这里导出 XML 到私有存档(TASK_CONSOLE_DELETED_ARCHIVE),再按名字从根路径删除。
    存档没配就拒绝:一个连原件都没留下的删除,事后没人说得清删掉的是什么。
  - 控制台自己的分类配置(TASK_CONSOLE_CATEGORIES)由这里改,改之前先证明原样重写能逐字节复现。
  - 健康监控清单、备份仓、迁移计划这些不在这个公开仓的视野里,交给机主私有的后续钩子
    (TASK_CONSOLE_DELETE_FOLLOWUP)。没配钩子时照样能删,但预览和结果都要单独说出
    「这几处没有清理」,不许和「清理完了」长得一样。

预览和执行之间用一次性令牌绑定,做法照搬 deletion.py:300 秒有效,只能用一次,
执行前把预览依赖的每一样(任务定义、是否受管理、控制器计划版本、分类配置字节、钩子计划)
重算一遍指纹,对不上就整个拒绝。不可逆的那一步之前还要再问一次钩子,钩子此刻说不行就不动手。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time

import controller as task_control
import maint
from winps import powershell

# 同 retire.py:pythonw 下每个控制台子程序都要新分配控制台,不加这个标志会慢到超时。
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

HOOK_ENV = "TASK_CONSOLE_DELETE_FOLLOWUP"
ARCHIVE_ENV = "TASK_CONSOLE_DELETED_ARCHIVE"
# 控制器没配置时默认拒绝删除。只有显式设成 1,才把所有任务当作不受控制器管理来删。
WITHOUT_AUTHORITY_ENV = "TASK_CONSOLE_DELETE_WITHOUT_AUTHORITY"
HOOK_PLAN_TIMEOUT = 60
HOOK_APPLY_TIMEOUT = 600
HOOK_OUTPUT_MAX = 1 << 20
HOOK_STEP_STATUSES = frozenset(("planned", "ok", "failed", "skipped", "blocked"))
PIPELINES_JS = Path(__file__).resolve().parent / "static" / "panels" / "pipelines.js"
BOM = b"\xef\xbb\xbf"

_LOCK = threading.RLock()
# 执行可能要十几分钟(控制器事务加钩子),不能拿着预览表的锁跑:那会让所有预览一起卡住。
# 同一时刻只允许一个删除在跑,第二个直接拒绝而不是排队,排队的那个看到的会是过期的世界。
_APPLY = threading.Lock()
_PLANS: dict[str, dict] = {}
_TTL = 300

# 输入错误回 400,其余拒绝(状态冲突、预览过期、钩子拦下)回 409。
INPUT_CODES = frozenset(("bad_name", "no_reason", "bad_request", "name_mismatch", "not_live"))

NOT_CONFIGURED = ("没有配置删除后续钩子（TASK_CONSOLE_DELETE_FOLLOWUP）：健康监控清单、备份和迁移计划"
                  "不会被清理，需要手动处理。")
WITHOUT_AUTHORITY = ("任务控制器没有配置，已按 TASK_CONSOLE_DELETE_WITHOUT_AUTHORITY=1 把这个任务当作不受控制器管理："
                     "如果控制器其实声明了它，控制器的登记会继续指向一个已删除的任务。")

# 控制器拒绝码 -> 给人看的话。没列出来的照原码报,不猜。
_AUTHORITY_MESSAGES = {
    "ownership_required": "控制器声明里有这个任务，但没有它的归属证明，不能绕过控制器删除。",
    "scheduler_ownership_changed": "任务定义和控制器记录的归属证明对不上，请先在控制器里核对。",
    "recovery_required": "控制器有未完成的事务，请先恢复再删除。",
    "busy": "任务正在运行，请先停止本次运行再删除。",
    "incomplete_runtime_configuration": "任务控制器的四项配置不全，拒绝删除。",
    "absolute_runtime_paths_required": "任务控制器的配置路径必须是绝对路径。",
    "authority_not_configured": ("这个控制台进程没有任务控制器的配置（TASK_CONSOLE_RUNTIME_CONFIG 等四项），"
                                 "分不清哪些任务归控制器管，拒绝删除。"),
}


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _authority_refusal(error) -> maint.Refused:
    code = getattr(error, "code", "runtime_failed")
    return maint.Refused(_AUTHORITY_MESSAGES.get(code, f"任务控制器拒绝了这次删除（{code}）"), code)


def _validate_name(name) -> str:
    if not isinstance(name, str):
        raise maint.Refused("任务名不合法", "bad_name")
    try:
        # 和控制器同一道名字闸。刻意不用 maint.SAFE_NAME:任务名可以带空格,
        # 那道闸会把「AcmeSync Daily」这种正当的名字整个挡在门外。
        task_control.task_name(name)
    except task_control.ContractError:
        raise maint.Refused(f"任务名不合法: {name!r}", "bad_name") from None
    return name


# --------------------------------------------------------------------------- scheduler bridge
# 三条 PowerShell 都只认根路径,名字只经环境变量 TC_NAME 进去,从不拼进命令字符串。
# 枚举根路径再按名字筛,而不是 `Get-ScheduledTask -TaskName X -ErrorAction SilentlyContinue`:
# 后者在任务不存在时 rc=1(见 tests/test_retire.py 里那条实测),「确认不存在」和「查不到」
# 会变成同一个结果,而删除恰恰要靠前者来证明自己做完了。
_HASH_PS = ("$sha = [Security.Cryptography.SHA256]::Create();"
            "function Get-XmlHash($text) { ([BitConverter]::ToString($sha.ComputeHash("
            "[Text.Encoding]::UTF8.GetBytes($text)))).Replace('-', '').ToLowerInvariant() };")

_DESCRIBE_PS = (
    "$ErrorActionPreference = 'Stop';"
    "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false;" + _HASH_PS +
    "$all = @(Get-ScheduledTask -TaskPath '\\');"
    "$t = @($all | Where-Object { $_.TaskName -eq $env:TC_NAME });"
    "if ($t.Count -ne 1) { [ordered]@{present = $false; matches = $t.Count} | ConvertTo-Json -Compress; exit 0 };"
    "$t = $t[0];"
    "$x = Export-ScheduledTask -TaskPath '\\' -TaskName $t.TaskName;"
    "[ordered]@{present = $true; name = $t.TaskName; state = \"$($t.State)\"; xml = $x;"
    " sha256 = (Get-XmlHash $x);"
    " actions = @($t.Actions | ForEach-Object { [ordered]@{execute = $_.Execute; arguments = $_.Arguments;"
    " workingDirectory = $_.WorkingDirectory} })} | ConvertTo-Json -Compress -Depth 5")

_UNREGISTER_PS = (
    "$ErrorActionPreference = 'Stop';" + _HASH_PS +
    "$t = @(Get-ScheduledTask -TaskPath '\\' | Where-Object { $_.TaskName -eq $env:TC_NAME });"
    "if ($t.Count -ne 1) { throw 'task_missing' };"
    "if (\"$($t[0].State)\" -eq 'Running') { throw 'task_running' };"
    # 删除前在同一个进程里重新导出一次,和预览时的定义比对。对不上就不删:
    # 一个在预览之后被改过的任务,存档里留的就不是将要删掉的那份。
    "$x = Export-ScheduledTask -TaskPath '\\' -TaskName $t[0].TaskName;"
    "if ((Get-XmlHash $x) -ne $env:TC_SHA256) { throw 'definition_changed' };"
    "Unregister-ScheduledTask -TaskPath '\\' -TaskName $t[0].TaskName -Confirm:$false;"
    "Write-Output 'unregistered'")

_PRESENCE_PS = (
    "$ErrorActionPreference = 'Stop';"
    "$all = @(Get-ScheduledTask -TaskPath '\\');"
    # 同 collect.ps1 的 I3:根路径一个任务都枚举不到是读坏了,不是机器上真没有任务。
    "if ($all.Count -eq 0) { throw 'enumerated_zero' };"
    "if (@($all | Where-Object { $_.TaskName -eq $env:TC_NAME }).Count) { 'present' } else { 'absent' }")


def _ps(script: str, env_extra: dict, timeout: int):
    return subprocess.run([powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, env=dict(os.environ, **env_extra), timeout=timeout,
                          stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)


def _decode(data) -> str:
    return data.decode("utf-8", "replace") if isinstance(data, bytes) else (data or "")


def _describe(name: str) -> dict | None:
    """读任务的状态、动作和完整定义。None 只表示一件事:确认根路径下没有这个名字。"""
    try:
        r = _ps(_DESCRIBE_PS, {"TC_NAME": name}, 90)
    except (OSError, subprocess.SubprocessError) as error:
        raise maint.Refused(f"读不到任务定义: {type(error).__name__}", "state_unreadable") from None
    if r.returncode != 0:
        raise maint.Refused("读不到任务定义: " + (_decode(r.stderr).strip()[:200] or f"rc={r.returncode}"),
                            "state_unreadable")
    try:
        data = json.loads(_decode(r.stdout))
    except ValueError:
        raise maint.Refused("任务定义的输出无法解析", "state_unreadable") from None
    if not isinstance(data, dict) or type(data.get("present")) is not bool:
        raise maint.Refused("任务定义的输出无法解析", "state_unreadable")
    if not data["present"]:
        return None
    xml, digest = data.get("xml"), data.get("sha256")
    # 两侧各算一次哈希。PowerShell 那份是删除前比对用的,这边这份证明传过来的 XML 一个字没丢:
    # 输出编码一旦错位,存档里留的就是一份乱码,而它看起来和正常的存档一模一样。
    if (not isinstance(xml, str) or not isinstance(digest, str)
            or hashlib.sha256(xml.encode("utf-8")).hexdigest() != digest):
        raise maint.Refused("任务定义在传输中不完整，拒绝继续", "state_unreadable")
    actions = data.get("actions") or []
    if isinstance(actions, dict):
        actions = [actions]
    clean = [{key: (a.get(key) if isinstance(a.get(key), str) else None)
              for key in ("execute", "arguments", "workingDirectory")}
             for a in actions if isinstance(a, dict)]
    return {"name": data.get("name"), "state": data.get("state"), "xml": xml,
            "sha256": digest, "actions": clean}


def _unregister(name: str, sha256: str) -> tuple[bool, str]:
    try:
        r = _ps(_UNREGISTER_PS, {"TC_NAME": name, "TC_SHA256": sha256}, 120)
    except (OSError, subprocess.SubprocessError) as error:
        return False, type(error).__name__
    out = _decode(r.stdout).strip().splitlines()
    if r.returncode == 0 and out and out[-1].strip() == "unregistered":
        return True, ""
    return False, (_decode(r.stderr).strip() or _decode(r.stdout).strip())[:200] or f"rc={r.returncode}"


def _presence(name: str) -> str:
    """'present' 或 'absent';读不到就抛,不返回一个猜出来的答案。"""
    try:
        r = _ps(_PRESENCE_PS, {"TC_NAME": name}, 90)
    except (OSError, subprocess.SubprocessError) as error:
        raise maint.Refused(f"无法确认任务是否还在: {type(error).__name__}", "state_unreadable") from None
    out = _decode(r.stdout).strip()
    if r.returncode != 0 or out not in ("present", "absent"):
        raise maint.Refused("无法确认任务是否还在: " + (_decode(r.stderr).strip()[:200] or f"rc={r.returncode}"),
                            "state_unreadable")
    return out


# --------------------------------------------------------------------------- authority
def _authority(env):
    """None 只表示「控制器四项配置一项都没设,而且机主显式允许没有控制器时删除」。

    没配控制器不能默认等于「没有任务归控制器管」:一个少了这四项环境变量的控制台进程
    (第二个启动器、开发用的工作树)会把控制器声明并拥有的任务直接注销,
    控制器的声明、归属证明和投影就此指向一个已删除的任务,既没有墓碑也没有日志。
    retire.apply 在同样的情况下拒绝,这里不能比它松。配了但坏了同样是拒绝,不是降级。
    """
    try:
        return task_control.load_runtime(environ=env)
    except task_control.ContractError as error:
        if error.code == "runtime_not_configured":
            if env.get(WITHOUT_AUTHORITY_ENV) == "1":
                return None
            raise maint.Refused(_AUTHORITY_MESSAGES["authority_not_configured"], "authority_not_configured") from None
        raise _authority_refusal(error) from None


def _managed(runtime, name: str) -> bool:
    if runtime is None:
        return False
    try:
        specs = task_control.registration._compile(runtime.load())["task_specs"]
    except task_control.ContractError as error:
        raise _authority_refusal(error) from None
    # 和 registration.dispatch 同一个认法(不分大小写)。声明里有它就归控制器管,
    # 归属证明不全时由 retire_plan 拒绝,这里不会退回「不受管理」那条路去绕开它。
    return any(spec["name"].casefold() == name.casefold() for spec in specs)


def _private_root(runtime):
    config = getattr(getattr(runtime, "authority", None), "config", None)
    return getattr(config, "private_root", None)


def _state_files(runtime, tx) -> list[str] | None:
    """这次事务在状态根下的三份记录,写成相对私有根的路径(状态根在私有根外面时以 ../ 开头)。

    它们不是 journal 里的 files 步骤,但和投影一起才是一次完整的事务:只把投影交给钩子,
    钩子就只提交投影,current.json、journal、receipt 留在工作树里没提交,
    下一次删除在钩子预检时就会因为「上一个事务没提交」被拦下。receipt 以事务编号命名,
    因为 runtime.finish 把生成编号设成了事务编号。算不出来就是 None,不猜。
    """
    config = getattr(getattr(runtime, "authority", None), "config", None)
    state, root = getattr(config, "state_root", None), getattr(config, "private_root", None)
    if state is None or root is None or not isinstance(tx, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", tx):
        return None
    try:
        return [Path(os.path.relpath(Path(state) / sub, Path(root))).as_posix()
                for sub in ("current.json", f"journals/{tx}.json", f"receipts/{tx}.json")]
    except (TypeError, ValueError):  # 不在同一个盘上,没有相对路径
        return None


def _relative(paths, root) -> tuple[list[str], list[str]]:
    inside, outside = [], []
    for path in paths:
        try:
            inside.append(Path(path).relative_to(root).as_posix())
        except (TypeError, ValueError):
            outside.append(str(path))
    return inside, outside


# --------------------------------------------------------------------------- category map
def _serialise(document, fmt) -> bytes:
    text = json.dumps(document, ensure_ascii=fmt["ascii"], indent=fmt["indent"], separators=fmt["separators"])
    text = text.replace("\n", fmt["newline"]) + fmt["tail"]
    return (BOM if fmt["bom"] else b"") + text.encode("utf-8")


def _format(data: bytes, document):
    """找一种写法,让原文档逐字节重写回同样的字节。找不到就是 None,自动改写随之取消。

    这一步是改写的前提,不是装饰:一份手写的 JSON 只要有一处空白、一个转义、一个重复键
    和 json.dumps 的习惯不同,「删掉一个任务」就会顺手把整份文件重排一遍,
    而那份 diff 里真正的那一行改动会被淹没。
    """
    bom = data.startswith(BOM)
    for ascii_ in (False, True):
        for indent in (2, 4, None, 1, 3, "\t"):
            for separators in ((None,) if indent is not None else (None, (",", ":"))):
                for newline in ("\n", "\r\n"):
                    for tail in ("", newline):
                        fmt = {"bom": bom, "ascii": ascii_, "indent": indent,
                               "separators": separators, "newline": newline, "tail": tail}
                        if _serialise(document, fmt) == data:
                            return fmt
    return None


def _categories_of(document):
    cats = document.get("categories") if isinstance(document, dict) else document
    return [c for c in cats if isinstance(c, dict)] if isinstance(cats, list) else None


def _same_task(value, name) -> bool:
    # 不分大小写:计划程序本身不分,控制器(_managed)和后续钩子也都这样认人。
    # 这里若按原样比,分类配置里写成 acmesync 的条目会被当成「不在」,钩子却找得到它,
    # 一次删除就会稳定地以钩子那一步失败收场。
    return isinstance(value, str) and value.casefold() == name.casefold()


def _without(document, name):
    """去掉这个名字在每个大类里的 tasks、taskDesc、taskInfo 三处。返回 (新文档, 去掉了几处)。"""
    document = deepcopy(document)
    removed = 0
    for cat in _categories_of(document) or []:
        if isinstance(cat.get("tasks"), list):
            kept = [n for n in cat["tasks"] if not _same_task(n, name)]
            removed += len(cat["tasks"]) - len(kept)
            cat["tasks"] = kept
        for key in ("taskDesc", "taskInfo"):
            if isinstance(cat.get(key), dict):
                for hit in [k for k in cat[key] if _same_task(k, name)]:
                    del cat[key][hit]
                    removed += 1
    return document, removed


def _inspect_categories(path, name) -> dict:
    out = {"path": str(path) if path else None, "digest": None, "present": False,
           "editable": False, "reason": None, "verdict": None}
    # missing=True 只表示「根本没有这份文件」;它和下面几种读坏了的情况一样都是没检查,
    # 只是不值得把那一步标成拦下。
    if not path:
        out["reason"] = "没有分类配置路径"
        out["missing"] = True
        return out
    path = Path(path)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        out["reason"] = "分类配置文件不存在"
        out["digest"] = "missing"
        out["missing"] = True
        return out
    except OSError as error:
        out["reason"] = f"分类配置读不了（{type(error).__name__}），不会自动修改"
        out["digest"] = "unreadable"
        return out
    out["digest"] = hashlib.sha256(data).hexdigest()
    try:
        document = json.loads(data[len(BOM):] if data.startswith(BOM) else data)
    except (UnicodeDecodeError, ValueError):
        out["reason"] = "分类配置不是合法的 UTF-8 JSON，不会自动修改"
        return out
    cats = _categories_of(document)
    if cats is None:
        out["reason"] = "分类配置里没有 categories 数组，不会自动修改"
        return out
    # 读到了才谈得上「有没有写建议」。读不到时 verdict 留 None,但 parsed 是 False,
    # 预警那边据此说「没检查」,不说「没写」。
    out["parsed"] = True
    for cat in cats:
        info = cat.get("taskInfo")
        for key, entry in (info.items() if isinstance(info, dict) else ()):
            if _same_task(key, name) and isinstance(entry, dict):
                verdict = entry.get("verdict")
                if isinstance(verdict, str) and verdict.strip():
                    out["verdict"] = verdict
    _edited, removed = _without(document, name)
    out["present"] = removed > 0
    out["removals"] = removed
    if not removed:
        out["reason"] = "分类配置里没有这个任务，不需要修改"
        return out
    if _format(data, document) is None:
        out["reason"] = "分类配置的写法无法原样重写，拒绝自动修改；请手动删除这个任务的条目"
        return out
    out["editable"] = True
    return out


def _category_unedited(categories, phase) -> tuple[dict, str | None]:
    """分类配置不自动改的那一步,和要不要给人留一句话。预览和结果共用,两边不许说两样话。

    只有一种情况算「无需修改」:文件读到了、解析了,而且确实没有这个名字。没路径、没文件、
    读不了、不是 JSON、没有 categories 数组,都只是「没检查」:把它们记成成功,
    一份带尾逗号却仍然列着这个任务的配置,就会让整次删除报出「全部清理完了」。
    """
    step = {"id": "categories.edit", "title": "从分类配置中移除", "target": categories["path"] or "未设置"}
    if categories["present"]:
        return dict(step, status="blocked", detail=categories["reason"]), categories["reason"]
    if categories.get("parsed"):
        return dict(step, status="skipped" if phase == "plan" else "ok", detail=categories["reason"]), None
    note = categories["reason"] + "；没有核对分类配置里是否还列着这个任务，请手动检查。"
    return dict(step, status="skipped" if categories.get("missing") else "blocked", detail=categories["reason"]), note


def _edit_categories(path, name, planned_digest) -> dict:
    """真改分类配置。调用方保证计划程序那一步已经成功。"""
    step = {"id": "categories.edit", "title": "从分类配置中移除", "target": str(path)}
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        return dict(step, status="failed", detail=f"分类配置读不了（{type(error).__name__}），条目仍在")
    if hashlib.sha256(data).hexdigest() != planned_digest:
        return dict(step, status="failed", detail="分类配置在预览之后被改过，没有自动修改；请手动删除这个任务的条目")
    try:
        document = json.loads(data[len(BOM):] if data.startswith(BOM) else data)
    except (UnicodeDecodeError, ValueError):
        return dict(step, status="failed", detail="分类配置不是合法的 UTF-8 JSON，条目仍在")
    fmt = _format(data, document)
    if fmt is None:
        return dict(step, status="blocked", detail="分类配置的写法无法原样重写，没有自动修改；请手动删除这个任务的条目")
    edited, removed = _without(document, name)
    if not removed:
        return dict(step, status="ok", detail="分类配置里没有这个任务，无需修改")
    new = _serialise(edited, fmt)
    from fleet_guards import filesystem as fs
    try:
        fs.atomic_replace(Path(path), new)
        written = Path(path).read_bytes()
    except (OSError, ValueError) as error:
        return dict(step, status="failed", detail=f"分类配置写入失败（{type(error).__name__}），条目可能仍在，请核对")
    if written != new:
        return dict(step, status="failed", detail="分类配置写入后读回的内容不一致，请核对")
    return dict(step, status="ok", detail=f"已移除 {removed} 处（tasks、taskDesc、taskInfo），其余内容逐字节保留")


# --------------------------------------------------------------------------- archive
def _inside_git(path: Path) -> bool:
    return any((p / ".git").exists() for p in (path, *path.parents))


def _archive_dir(env):
    """(目录, 不能用的原因)。存档必须是一个已经存在、不在任何 git 工作树里的私有目录。"""
    raw = env.get(ARCHIVE_ENV)
    if not raw:
        return None, "没有设置删除存档目录（TASK_CONSOLE_DELETED_ARCHIVE），不受控制器管理的任务不能删除"
    path = Path(raw)
    if not path.is_absolute() or not path.is_dir():
        return None, "删除存档目录不是一个已存在的绝对路径，不受控制器管理的任务不能删除"
    if _inside_git(path):
        # 公开仓、备份仓都是 git 工作树。存档里是任务原件,原件进了任何一个仓都是事故。
        return None, "删除存档目录位于 git 工作树内，拒绝把任务原件写进去"
    return path, None


def _archive(directory: Path, name, reason, described) -> tuple[bool, str, dict]:
    from fleet_guards import filesystem as fs
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:80] or "task"
    xml_path = directory / f"{safe}-{stamp}.xml"
    receipt_path = directory / f"{safe}-{stamp}.json"
    # 导出的 XML 自己声明的是 UTF-16。照它的声明写,存档才能原样喂回 Register-ScheduledTask。
    xml_bytes = described["xml"].encode("utf-16")
    receipt = {"schema": 1, "name": name, "reason": reason, "exportedAt": stamp,
               "xml": xml_path.name, "xmlSha256": described["sha256"], "actions": described["actions"]}
    receipt_bytes = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    files = {"xml": str(xml_path), "receipt": str(receipt_path)}
    try:
        for path, data in ((xml_path, xml_bytes), (receipt_path, receipt_bytes)):
            if not fs.create_no_replace(path, data):
                return False, f"存档文件已存在，没有覆盖: {path.name}", files
            if path.read_bytes() != data:
                return False, f"存档写入后读回的内容不一致: {path.name}", files
    except (OSError, ValueError) as error:
        return False, f"存档写入失败（{type(error).__name__}）", files
    return True, "", files


# --------------------------------------------------------------------------- follow-up hook
def _hook_result(state, message, *, steps=(), blocking=(), notes=(), exit_code=None):
    return {"state": state, "message": message, "steps": list(steps), "blocking": list(blocking),
            "notes": list(notes), "exitCode": exit_code}


def _valid_reply(reply) -> bool:
    if not isinstance(reply, dict) or reply.get("schema") != 1 or type(reply.get("ok")) is not bool:
        return False
    for key in ("steps", "blocking", "notes"):
        if not isinstance(reply.get(key), list):
            return False
    if any(not isinstance(text, str) for key in ("blocking", "notes") for text in reply[key]):
        return False
    return all(isinstance(step, dict)
               and all(isinstance(step.get(k), str) for k in ("id", "title", "target", "status", "detail"))
               and step["status"] in HOOK_STEP_STATUSES for step in reply["steps"])


def _run_hook(mode: str, request: dict, env) -> dict:
    """按协议跑一次钩子。只有五种结果,彼此不能长得一样:

    not_configured(没配)、ok、blocked(预检拦下,什么都没改)、failed(做了但没做成或只做了一部分)、
    unreadable(没读到一份合乎协议的回答)。读不出来的回答一律不当成功:
    一个崩溃后打印半截 JSON 的钩子,和一个干完活的钩子,在退出码之外可能毫无区别。
    """
    raw = env.get(HOOK_ENV)
    if not raw:
        return _hook_result("not_configured", NOT_CONFIGURED)
    script = Path(raw)
    if not script.is_absolute() or not script.is_file():
        return _hook_result("failed", "删除后续钩子的路径不是一个存在的绝对路径文件，钩子没有运行")
    try:
        proc = subprocess.run([sys.executable, "-B", "-I", str(script)],
                              input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
                              capture_output=True, cwd=str(script.parent),
                              env=dict(env, PYTHONDONTWRITEBYTECODE="1"),
                              timeout=HOOK_PLAN_TIMEOUT if mode == "plan" else HOOK_APPLY_TIMEOUT,
                              creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return _hook_result("unreadable", "后续钩子超时，结果读不出来；它可能已经改了一部分，请人工核对")
    except OSError as error:
        return _hook_result("failed", f"后续钩子启动失败（{type(error).__name__}）")
    if len(proc.stdout) > HOOK_OUTPUT_MAX:
        return _hook_result("unreadable", "后续钩子的输出超过 1 MB，结果读不出来", exit_code=proc.returncode)
    if proc.returncode not in (0, 1, 2):
        return _hook_result("unreadable", f"后续钩子以意外的退出码 {proc.returncode} 结束，结果读不出来",
                            exit_code=proc.returncode)
    try:
        reply = json.loads(proc.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _hook_result("unreadable", "后续钩子的输出不是一份 UTF-8 JSON，结果读不出来", exit_code=proc.returncode)
    if not _valid_reply(reply):
        return _hook_result("unreadable", "后续钩子的回答不符合协议（schema 1），结果读不出来", exit_code=proc.returncode)
    # 退出码和回答必须说同一件事。两边打架时信哪一边都是在猜。
    consistent = {0: reply["ok"] and not reply["blocking"],
                  1: not reply["ok"],
                  2: not reply["ok"] and bool(reply["blocking"])}[proc.returncode]
    if not consistent:
        return _hook_result("unreadable", "后续钩子的退出码与它的回答不一致，结果读不出来", exit_code=proc.returncode)
    state = {0: "ok", 1: "failed", 2: "blocked"}[proc.returncode]
    # apply 模式里的「拦下」发生在计划程序那一步之后:任务已经删了,钩子只是一处都没清理。
    # 这时再说「拦下了这次删除，什么都没改」,人会以为任务还在,去找一个早已不存在的删除按钮。
    message = {"ok": "后续钩子的预览已就绪" if mode == "plan" else "后续清理已完成",
               "failed": "后续钩子报告失败或只完成了一部分",
               "blocked": "后续钩子的预检拦下了这次删除，什么都没改" if mode == "plan" else
                          "任务已经删除，但后续钩子的预检没有放行，监控清单、备份和迁移计划一处都没有清理"}[state]
    return _hook_result(state, message, steps=reply["steps"], blocking=reply["blocking"],
                        notes=reply["notes"], exit_code=proc.returncode)


def _hook_digest(hook: dict) -> str:
    return _hash({key: hook.get(key) for key in ("state", "steps", "blocking", "notes")})


# --------------------------------------------------------------------------- warnings
def _pipeline_names():
    """流水线页面列出的任务名。读的就是页面那张表本身,不在这里另抄一份。"""
    try:
        text = PIPELINES_JS.read_text(encoding="utf-8")
    except OSError:
        return None
    block = re.search(r"PIPELINE_DEFS\s*=\s*\{(.*?)\n\};", text, re.S)
    if not block:
        return None
    return set(re.findall(r'\bname\s*:\s*"([^"]+)"', block.group(1)))


# 流水线页面上的任务(同步与备份)是备份本身的骨架:删掉它们,删除的后续清理(包括把被删任务移出备份)
# 也就没了着落。页面上三个标签页都不给它们删除按钮,这里再拒一次,按钮之外的请求同样拿不到令牌。
PIPELINE_BLOCK = ("这个任务是「同步与备份」页面上的流水线任务，是备份本身的骨架，控制台不删除它；"
                  "确实要删，请在任务控制器或计划程序里手动处理。")


def _warnings(categories, authority, pipelines):
    out = []
    if pipelines is None:
        out.append({"code": "pipeline_unchecked",
                    "message": "读不到流水线页面的任务列表，没有检查这个任务是否属于某条流水线。"})
    verdict = categories.get("verdict")
    if not categories.get("parsed"):
        out.append({"code": "verdict_unchecked",
                    "message": "没有读到分类配置，未检查机主对这个任务的建议。"})
    elif verdict != "remove":
        out.append({"code": "verdict_not_remove", "verdict": verdict,
                    "message": ("分类配置里没有给这个任务写建议，" if verdict is None
                                else "分类配置里对这个任务的建议不是「可删」，") + "删除前请确认。"})
    # 关联待办同样分「没有」和「没查」。只有控制器读到了一张表,空表才是「没有关联待办」;
    # 读不到、读失败、任务不归控制器管(根本没地方查),都要单独说一句,不能和零条长得一样。
    linked = (authority or {}).get("linkedWorkItems")
    status = (authority or {}).get("linkedWorkItemsStatus")
    if authority is None:
        out.append({"code": "linked_work_items_unchecked",
                    "message": "这个任务不归任务控制器管，没有检查是否有待办关联着它；删除不会关闭任何待办。"})
    elif status not in ("available", "provided") or not isinstance(linked, list):
        out.append({"code": "linked_work_items_unchecked", "status": status,
                    "message": f"没有读到关联待办（{status or '未提供'}），不知道有没有待办关联着这个任务；删除不会关闭它们。"})
    elif linked:
        out.append({"code": "linked_work_items", "count": len(linked),
                    "message": f"有 {len(linked)} 条待办关联着这个任务，删除不会关闭它们。"})
    return out


# --------------------------------------------------------------------------- plan
def _inspect(name, reason, *, live, categories_path, env) -> tuple[dict, str, dict]:
    """算出整份预览,不写任何东西。返回 (给页面的预览, 指纹, 执行要用的上下文)。"""
    name = _validate_name(name)
    if not isinstance(reason, str) or not reason.strip():
        # 原因会写进墓碑和存档回执。没有原因的删除,半年后没人说得清为什么。
        raise maint.Refused("删除必须写明原因", "no_reason")
    reason = reason.strip()
    # 名字从 HTTP 来,在这个进程亲眼在根路径的在册任务里看到它之前没有任何地位。
    # live() 是 collect.ps1 的枚举:只有根路径,厂商任务已经剔掉。
    if sum(1 for row in live() if isinstance(row, dict) and row.get("name") == name) != 1:
        raise maint.Refused(f"不在本机可管理的任务列表里: {name}", "not_live")
    described = _describe(name)
    if described is None:
        raise maint.Refused(f"不在本机可管理的任务列表里: {name}", "not_live")
    if described["state"] == "Running":
        raise maint.Refused("任务正在运行，请先停止本次运行再删除。", "task_running")

    runtime = _authority(env)
    managed = _managed(runtime, name)
    authority = None
    if managed:
        try:
            proposal = task_control.retire_plan(name, reason, runtime=runtime)
        except task_control.ContractError as error:
            raise _authority_refusal(error) from None
        registration = proposal.get("registration_plan") or {}
        changes = [row for row in registration.get("changes", {}).get("files", []) if isinstance(row, dict)]
        relative, _outside = _relative([row.get("path") for row in changes], _private_root(runtime))
        authority = {"planRevision": registration.get("plan_revision"),
                     "changes": [{"path": path, "operation": row.get("operation")}
                                 for path, row in zip(relative, changes)],
                     "linkedWorkItems": proposal.get("linked_work_items"),
                     "linkedWorkItemsStatus": proposal.get("linked_work_items_status")}

    categories = _inspect_categories(categories_path, name)
    archive, archive_reason = (None, None) if managed else _archive_dir(env)
    blocking, notes = [], []
    if runtime is None:
        notes.append(WITHOUT_AUTHORITY)
    pipelines = _pipeline_names()
    if pipelines is not None and any(_same_task(p, name) for p in pipelines):
        blocking.append(PIPELINE_BLOCK)
    steps = []
    if managed:
        steps.append({"id": "authority.retire", "title": "通过任务控制器退役并删除",
                      "target": "计划程序根路径与控制器登记", "status": "planned",
                      "detail": "停用、从计划程序删除、改写控制器自己的登记投影、写入墓碑与回执，"
                                "加密的 XML 原件留在凭据库。"})
    else:
        steps.append({"id": "archive.export", "title": "导出任务 XML 存档",
                      "target": str(archive) if archive else "未设置",
                      "status": "planned" if archive else "blocked",
                      "detail": "导出完整的任务定义和一份记有原因、原始动作的回执，写入后读回核对。"
                      if archive else archive_reason})
        steps.append({"id": "scheduler.unregister", "title": "从计划程序删除",
                      "target": "计划程序根路径", "status": "planned",
                      "detail": "删除前在同一次调用里重新导出并比对任务定义，和预览时不一致就不删。"})
        if archive_reason:
            blocking.append(archive_reason)
    if categories["editable"]:
        steps.append({"id": "categories.edit", "title": "从分类配置中移除", "target": categories["path"],
                      "status": "planned",
                      "detail": f"移除 {categories['removals']} 处（tasks、taskDesc、taskInfo），其余内容逐字节保留。"})
    else:
        cat_step, cat_note = _category_unedited(categories, "plan")
        steps.append(cat_step)
        if categories["present"]:
            # 列着这个任务却改不了,就不发令牌。放行的话,计划程序那一步(不可逆)先做完,
            # 后续钩子在 apply 时看到分类配置里还列着它,整次清理一处都不做;而任务已经不在册,
            # 之后的预览一律被拒,没有任何一条路能回到这里把它补完。所以必须在动手之前就让人先改掉它。
            blocking.append(cat_note + "。任务删除之后就不能再从这里预览它，请先手动删掉它在分类配置里的条目，再重新预览。")
        elif cat_note:
            notes.append(cat_note)

    request = {"schema": 1, "mode": "plan",
               "task": {"name": name, "managed": managed, "actions": described["actions"]},
               "reason": reason,
               "categories": {"path": categories["path"], "edited": categories["editable"]},
               "authority": None}
    hook = _run_hook("plan", request, env)
    if hook["state"] == "not_configured":
        notes.append(NOT_CONFIGURED)
    elif hook["state"] == "blocked":
        blocking.extend(hook["blocking"])
    elif hook["state"] in ("failed", "unreadable"):
        blocking.append("后续钩子的预览没有成功，无法确认删除后的清理：" + hook["message"])
    notes.extend(hook["notes"])
    steps.append({"id": "followup.apply", "title": "清理监控清单、备份与迁移计划",
                  "target": env.get(HOOK_ENV) or "未配置",
                  "status": {"ok": "planned", "not_configured": "skipped"}.get(hook["state"], "blocked"),
                  "detail": hook["message"]})

    preview = {"ok": True, "name": name, "reason": reason, "managed": managed,
               "task": {"state": described["state"], "actions": described["actions"],
                        "definitionSha256": described["sha256"]},
               "steps": steps, "authority": authority, "followup": hook,
               "categories": {"path": categories["path"], "present": categories["present"],
                              "editable": categories["editable"], "verdict": categories["verdict"]},
               "blocking": blocking, "notes": notes,
               "warnings": _warnings(categories, authority, pipelines)}
    fingerprint = _hash({"name": name, "definition": described["sha256"], "managed": managed,
                         "authority": (authority or {}).get("planRevision"),
                         "categories": categories["digest"], "hook": _hook_digest(hook),
                         "archive": str(archive) if archive else None})
    context = {"described": described, "runtime": runtime, "archive": archive,
               "categories": categories, "hook": hook}
    return preview, fingerprint, context


def plan(name, reason, *, live, categories_path, env=None) -> dict:
    env = os.environ if env is None else env
    preview, fingerprint, _context = _inspect(name, reason, live=live, categories_path=categories_path, env=env)
    with _LOCK:
        now = time.monotonic()
        for token in list(_PLANS):
            if _PLANS[token]["expires"] <= now:
                del _PLANS[token]
        if preview["blocking"]:
            # 有拦阻原因就不发令牌:一份「此刻不能执行」的预览,不该附带一把能执行的钥匙。
            return dict(preview, applicable=False, token=None, expiresIn=None)
        if len(_PLANS) >= 32:
            raise maint.Refused("待确认预览过多，请稍后重新预览", "plan_limit")
        token = secrets.token_urlsafe(32)
        _PLANS[token] = {"name": preview["name"], "reason": preview["reason"],
                         "fingerprint": fingerprint, "expires": now + _TTL}
    return dict(preview, applicable=True, token=token, expiresIn=_TTL)


# --------------------------------------------------------------------------- apply
def _readback(name) -> tuple[str, str]:
    """删除那一步之后唯一的裁判:('absent' | 'present' | 'unknown', 读不到时的原因)。"""
    try:
        return _presence(name), ""
    except maint.Refused as error:
        return "unknown", str(error)


def _scheduler_managed(name, reason, runtime) -> tuple[list[dict], dict, str]:
    """返回 (步骤, 控制器事务摘要, 读回的计划程序状态 absent / present / unknown)。

    任务还在不在,不管控制器怎么说,只信计划程序的读回。控制器报失败时任务照样可能已经没了:
    事务提交后清理暂存失败(ok=false 而 status=committed)、停在 committing、
    300 秒执行期限在 COM 删除之后才到。这时候报「没有删除」,分类配置和后续钩子就永远不会再跑,
    而任务已不在册,之后的预览一律被拒,机器就停在半删除的状态里,页面却说什么都没删。
    """
    step = {"id": "authority.retire", "title": "通过任务控制器退役并删除", "target": "计划程序根路径与控制器登记"}
    authority = {"transactionId": None, "status": None, "files": []}
    result = None
    try:
        result = task_control.action(name, "retire", reason=reason, runtime=runtime)
    except Exception as error:  # noqa: BLE001  控制器的任何异常原文都不外泄,只留它的码
        claim = f"控制器拒绝或执行失败（{getattr(error, 'code', type(error).__name__)}）"
    else:
        authority.update(transactionId=result.get("transaction_id"), status=result.get("status"))
        claim = (None if result.get("ok") is True and result.get("status") == "committed" else
                 f"控制器事务没有提交（{(result.get('failure') or {}).get('code') or result.get('status') or 'unknown'}）")
    tx = authority["transactionId"]
    committed = result is not None and result.get("status") == "committed"
    # 读回来自证。计划程序里没有它,才算删除这一步做完了;只看控制器的返回值,
    # 在「事务提交了而 COM 那一步其实没生效」时和成功长得一模一样。
    presence, why = _readback(name)
    if presence == "present":
        detail = (f"{claim}，读回确认计划程序里仍有这个任务，没有删除" if claim and not committed else
                  "控制器报告已提交，但计划程序里仍有这个任务，请核对")
        return [dict(step, status="failed", detail=detail)], authority, "present"
    if presence == "unknown":
        return [dict(step, status="failed",
                     detail=f"{claim or '控制器报告已提交'}，而且无法确认任务是否还在计划程序里：{why}")], authority, "unknown"
    recover = (f"；请用事务编号 {tx} 运行控制器的 recover 核对并完成这次事务" if tx else
               "；请在控制器里核对有没有未完成的事务")
    if claim is None:
        steps = [dict(step, status="ok", detail="控制器事务已提交，读回确认计划程序里已没有这个任务")]
    elif committed:
        # 事务本身提交了,只是暂存文件没清干净:删除这一步是做完的,欠的是另一件事,单独记一步。
        steps = [dict(step, status="ok", detail="控制器事务已提交，读回确认计划程序里已没有这个任务"),
                 {"id": "authority.cleanup", "title": "清理控制器事务的暂存文件", "target": tx or "无事务编号",
                  "status": "failed",
                  "detail": f"事务已提交，但暂存文件没有清理干净（{result.get('cleanup_error') or 'cleanup_required'}）" + recover}]
    else:
        steps = [dict(step, status="failed",
                      detail=f"{claim}，但读回确认计划程序里已没有这个任务，控制器的事务状态需要核对" + recover)]
    # 事务日志另起一步:它读不回来不改变「任务已经删了」这件事,但后续钩子拿到的文件清单
    # 就不是读回来的,这一点必须单独说出来,而不是混进上面那一步的「成功」里。
    readback = {"id": "authority.readback", "title": "读回控制器事务日志", "target": tx or "无事务编号"}
    if not tx:
        steps.append(dict(readback, status="failed", detail="控制器没有返回事务编号，读不回事务日志，后续钩子拿到的改动文件清单是空的"))
        return steps, authority, "absent"
    state = _state_files(runtime, tx)
    try:
        journal = runtime.journal.load(tx)
        files = [s.get("key") for s in journal.get("steps", []) if s.get("kind") == "files"]
        relative, outside = _relative(files, _private_root(runtime))
        authority["files"] = relative + (state or [])
        if journal.get("status") != "committed":
            steps.append(dict(readback, status="failed", detail=f"事务日志状态是 {journal.get('status')}，不是已提交"))
        elif outside:
            steps.append(dict(readback, status="failed",
                              detail=f"有 {len(outside)} 个改动文件不在控制器私有根目录下，没有交给后续钩子"))
        elif state is None:
            steps.append(dict(readback, status="failed",
                              detail="算不出控制器状态根下的 current.json、事务日志和回执的路径，没有交给后续钩子"))
        else:
            steps.append(dict(readback, status="ok",
                              detail=f"事务已提交，改动了 {len(relative)} 个登记文件，连同 current.json、事务日志和回执交给后续钩子"))
    except Exception as error:  # noqa: BLE001
        authority["files"] = state or []
        steps.append(dict(readback, status="failed",
                          detail=f"读不回事务日志（{getattr(error, 'code', type(error).__name__)}），"
                                 "后续钩子拿到的改动文件清单里没有登记投影"))
    return steps, authority, "absent"


def _scheduler_unmanaged(name, reason, archive, described) -> tuple[list[dict], str]:
    """返回 (步骤, 读回的计划程序状态)。删除命令报错或超时时同样先读回,理由同 _scheduler_managed。"""
    export = {"id": "archive.export", "title": "导出任务 XML 存档", "target": str(archive)}
    remove = {"id": "scheduler.unregister", "title": "从计划程序删除", "target": "计划程序根路径"}
    ok, why, files = _archive(archive, name, reason, described)
    if not ok:
        # 存档没写成就根本没发出删除命令,这里不用读回。
        return [dict(export, status="failed", detail=why),
                dict(remove, status="skipped", detail="存档没有写成，没有删除任务")], "present"
    export = dict(export, status="ok", detail="已写入并读回核对：" + Path(files["xml"]).name, files=files)
    done, why = _unregister(name, described["sha256"])
    presence, unreadable = _readback(name)
    if presence == "unknown":
        claim = "删除命令返回成功" if done else f"删除命令没有正常返回（{why}）"
        return [export, dict(remove, status="failed", detail=f"{claim}，而且无法确认任务是否还在：{unreadable}")], "unknown"
    if presence == "present":
        detail = "删除命令返回成功，但计划程序里仍有这个任务" if done else f"删除失败，读回确认任务仍在：{why}"
        return [export, dict(remove, status="failed", detail=detail)], "present"
    if not done:
        # 命令超时或报错,但任务确实没了。删除照样算数,命令为什么报错要有人看一眼。
        return [export, dict(remove, status="failed",
                             detail=f"删除命令没有正常返回（{why}），但读回确认计划程序里已没有这个任务；请核对这次报错")], "absent"
    return [export, dict(remove, status="ok", detail="已删除，读回确认计划程序里已没有它")], "absent"


def _python_for_console() -> str:
    # 控制台常驻时跑在 pythonw.exe 下;把它写进给人在终端里跑的命令,钩子的回答会无声地消失。
    exe = Path(sys.executable)
    return str(exe.with_name("python.exe")) if exe.name.casefold() == "pythonw.exe" else str(exe)


def _followup_retry(request, env) -> dict | None:
    """钩子在 apply 时拦下(退出 2,按协议一处没改)之后,单独重跑后续清理的办法。只在这一种情况下给。

    任务这时已经不在计划程序里,删除对话框的预览会以 not_live 拒绝它,页面上没有第二次机会;
    钩子自己的 apply 预检要求任务已经不在,所以把同一份 apply 请求原样再喂给它,正是它设计里的重跑。
    钩子报失败(退出 1)或回答读不出来时不给:那时它可能已经改了一半,原样重跑会撞上自己留下的改动,
    要先有人核对。
    """
    script = env.get(HOOK_ENV)
    if not script:
        return None
    command = f'"{_python_for_console()}" -B -I "{script}" < "<请求文件>"'
    return {"hook": script, "request": request, "command": command,
            "howto": ("单独重跑后续清理：任务已经不在计划程序里，删除对话框不能再预览它，所以不要「重新删除一次」。"
                      "先处理上面的原因（分类配置里还列着它的话，先手动删掉它的条目），再把结果里的后续清理请求原样存成"
                      "一个 UTF-8 文件，在 cmd 或 Git Bash 里运行：" + command +
                      "。钩子会把预检全部重做一遍，不过就退出 2，一处都不改。")}


def _remaining(steps, hook, retry=None) -> list[str]:
    """计划程序那一步之后还剩什么没做。逐条说清楚,而不是一句「部分完成」。"""
    out = [f"{s['title']}：{s['detail']}" for s in steps
           if s["status"] != "ok" and s["id"] != "followup.apply"]
    if hook["state"] == "not_configured":
        out.append("健康监控清单、备份和迁移计划没有清理（未配置删除后续钩子），需要手动处理")
    elif hook["state"] == "unreadable":
        out.append("后续清理的结果读不出来，请人工核对健康监控清单、备份和迁移计划：" + hook["message"])
    elif hook["state"] == "blocked":
        # 拦下时钩子的每一步都会被记成 skipped,步骤名一条都不说明问题;拦下的原因才是要处理的事。
        out.extend("后续清理没有运行：" + text for text in hook["blocking"] or [hook["message"]])
        if retry:
            out.append(retry["howto"])
    elif hook["state"] != "ok":
        # skipped 是钩子判定这一步不适用(不在 FORBID_TASKS 里、不归登记权威管),钩子自己也把它算作通过;
        # 列进「还没完成」,人就会去找一处根本不存在的改动。只列真没做成的,每条带上钩子给的原因。
        unfinished = [f"{s['title']}：{s['detail']}" for s in hook["steps"]
                      if s["status"] in ("failed", "blocked", "planned")]
        out.extend("后续清理没有完成：" + text for text in unfinished or [hook["message"]])
    return out


def apply(token, name, *, live, categories_path, env=None) -> dict:
    env = os.environ if env is None else env
    if env.get("TASK_CONSOLE_READ_ONLY") == "1":
        raise maint.Refused("只读预览，无法执行操作", "read_only")
    with _LOCK:
        saved = _PLANS.pop(token, None) if isinstance(token, str) else None
    if not saved or saved["expires"] <= time.monotonic():
        raise maint.Refused("预览不存在或已过期，请重新预览", "stale_plan")
    if name != saved["name"]:
        # 令牌已经被弹出作废:拿着别人的令牌来删另一个任务,两边都要重新预览。
        raise maint.Refused("确认的任务名与预览不一致，请重新预览", "name_mismatch")
    if not _APPLY.acquire(blocking=False):
        raise maint.Refused("已有一个删除正在进行，请稍后再试", "busy")
    try:
        # 不可逆的一步之前,把预览依赖的每一样重新算一遍,包括再问一次钩子。
        preview, fingerprint, context = _inspect(name, saved["reason"], live=live,
                                                 categories_path=categories_path, env=env)
        if context["hook"]["state"] == "blocked":
            raise maint.Refused("后续钩子此刻拦下了这次删除，什么都没改：" + "；".join(context["hook"]["blocking"]),
                                "followup_blocked")
        if preview["blocking"]:
            raise maint.Refused("此刻不能执行：" + "；".join(preview["blocking"]), "blocked")
        if fingerprint != saved["fingerprint"]:
            raise maint.Refused("任务、控制器计划、分类配置或后续钩子在预览之后变了，请重新预览", "plan_changed")
        return _run(name, saved["reason"], preview, context, categories_path, env)
    finally:
        _APPLY.release()


def _run(name, reason, preview, context, categories_path, env) -> dict:
    managed, described = preview["managed"], context["described"]
    authority = None
    if managed:
        steps, authority, presence = _scheduler_managed(name, reason, context["runtime"])
    else:
        steps, presence = _scheduler_unmanaged(name, reason, context["archive"], described)
    if presence != "absent":
        # 计划程序里还有它(或者读不出来有没有),后面一步都不跑:不能把一个可能还在的任务从别处的登记里摘掉。
        # 「读不出来」不是「没有删除」:那时任务可能已经没了,只能请人去核对,不能替计划程序回答。
        unknown = presence == "unknown"
        why = "无法确认任务是否已删除" if unknown else "任务没有删除"
        steps += [{"id": "categories.edit", "title": "从分类配置中移除", "target": categories_path and str(categories_path),
                   "status": "skipped", "detail": why + "，没有修改"},
                  {"id": "followup.apply", "title": "清理监控清单、备份与迁移计划",
                   "target": env.get(HOOK_ENV) or "未配置", "status": "skipped", "detail": why + "，没有运行"}]
        first = next(s["detail"] for s in steps if s["status"] == "failed")
        remaining = [f"{s['title']}：{s['detail']}" for s in steps if s["status"] == "failed"]
        if unknown:
            remaining.append("刷新页面核对计划程序里还有没有这个任务；如果已经没有了，分类配置、监控清单、备份和迁移计划"
                             "需要手动清理（预览会因为任务不在册而拒绝）")
        return {"ok": False, "status": "unknown" if unknown else "failed", "name": name, "managed": managed,
                "steps": steps, "authority": authority, "followup": None, "followupRetry": None,
                "notes": [], "remaining": remaining,
                "message": ("无法确认是否已删除：" if unknown else "没有删除：") + first}

    categories = context["categories"]
    cat_note = None
    if categories["editable"]:
        cat_step = _edit_categories(categories_path, name, categories["digest"])
    else:
        cat_step, cat_note = _category_unedited(categories, "result")
    steps.append(cat_step)
    edited = cat_step["status"] == "ok" and categories["editable"]

    request = {"schema": 1, "mode": "apply",
               "task": {"name": name, "managed": managed, "actions": described["actions"]},
               "reason": reason,
               "categories": {"path": categories["path"], "edited": edited},
               "authority": {"transaction_id": (authority or {}).get("transactionId"),
                             "files": (authority or {}).get("files", [])}}
    hook = _run_hook("apply", request, env)
    steps.append({"id": "followup.apply", "title": "清理监控清单、备份与迁移计划",
                  "target": env.get(HOOK_ENV) or "未配置",
                  "status": {"ok": "ok", "not_configured": "skipped", "blocked": "blocked"}.get(hook["state"], "failed"),
                  "detail": hook["message"]})
    retry = _followup_retry(request, env) if hook["state"] == "blocked" else None
    remaining = _remaining(steps, hook, retry)
    # ok 只有一个来源:每一步都是 ok,并且钩子读回来的也是 ok。「钩子没配」是 partial,不是 ok,
    # 否则一次只删了计划程序的删除,会和一次全部清理干净的删除报出同一个绿色。
    ok = all(s["status"] == "ok" for s in steps) and hook["state"] == "ok"
    return {"ok": ok, "status": "ok" if ok else "partial", "name": name, "managed": managed,
            "steps": steps, "authority": authority, "followup": hook, "remaining": remaining,
            "followupRetry": retry,
            "notes": ([cat_note] if cat_note else []) + list(hook["notes"]),
            "message": ("任务已删除，分类配置、监控清单、备份和迁移计划都已清理。" if ok else
                        "任务已从计划程序删除，但还有没完成的清理：" + "；".join(remaining))}
