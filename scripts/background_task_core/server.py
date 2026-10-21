
"""The HTTP layer: routing, auth, the host allowlist, and the write endpoints.

Console configuration and component layout: the root `README.md` and `CONFIG.md`.
Before changing anything here: `docs/changing-this.md`.

Three controls make the write endpoints safe to have at all. Each is load-bearing, none is
decorative, and all three are implemented in this file:

  1. It binds 127.0.0.1. Not 0.0.0.0, not a hostname. Nothing off this machine can reach it.
  2. Every /api/ call must carry a token minted fresh at startup and never written to disk. Without
     it, ANY web page you had open could POST to this port and disable a backup task: the browser
     would attach no credentials and the server would accept. That is the CSRF shape, and being on
     localhost does not protect against it.
  3. Names that arrive over HTTP have no standing. A task name is re-enumerated against the live
     system before any verb runs. The shared controller sends names as JSON data
     to the fixed root-folder COM transport, never as PowerShell source.

Usage:
    python -m background_task_core.server [--port 8787] [--no-browser]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import webbrowser
from collections import Counter, defaultdict
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import integrations
from .component_routes import ComponentRoutes, _CONVO_ACCESS, CONVO_UNLOCKED_POSTS
# The aliases name the part of each module a call belongs to.
from . import registry as allowlist
from . import integrations as source_reads, integrations as work_actions
from . import lifecycle as task_control, lifecycle as task_delete, lifecycle as task_repair
from . import runs as console_store, runs as evtlog, runs as freshness, runs as history, runs as timeline
from .runs import RC_NOT_RUN, RC_RUNNING, declared_ok_codes, ok_codes, norm_rc, run_ps


HERE = Path(__file__).resolve().parent
COLLECT = HERE / "collect.ps1"
PAGE = HERE / "console.html"
ICON = HERE / "icon.svg"
VENDOR = HERE / "vendor"
STATIC = HERE / "static"
# 允许下发的静态文件就是随包发布的那些 .js / .css,启动时列一次目录得出。
STATIC_FILES = frozenset(p.relative_to(STATIC).as_posix() for p in STATIC.rglob("*")
                         if p.suffix in (".js", ".css"))

# 任何被拒绝的请求,_drain 最多替它读掉这么多正文(见 _drain)。
DRAIN_MAX = 1 << 20
VERBS = ("enable", "disable", "run", "stop")


def _env_path(name: str) -> Path | None:
    v = os.environ.get(name)
    return Path(os.path.expanduser(v)) if v else None


# --------------------------------------------------------------------------- run log
# 运行日志的读取窗口与条数上限。
RUNLOG_DAYS = 30
# 上限 10 万。2 万实测只覆盖 3 天,而口径写着「最近 30 天」;10 万在本机等于「有多少读多少」。
RUNLOG_MAX_EVENTS = 100000


def load_runlog() -> dict:
    """Aggregate the Task Scheduler Operational log into per-task run counts.

    This is REAL run history: one row per start, per completion, per action return code. It is a
    different measurement from the health monitor's poll series and is never merged into it, because
    merging them would produce a number that is neither.

    The log only goes back to the day it was enabled. An empty result therefore means NOT RECORDED
    YET, and is reported that way rather than as zero runs.
    """
    # 读不到就明说,不装作成功:一段空的运行日志和一段读不到的运行日志,在界面上长得一模一样。
    try:
        raw = evtlog.read(days=RUNLOG_DAYS, max_events=RUNLOG_MAX_EVENTS)
    except Exception as e:
        return {"available": False, "reason": f"读运行日志失败: {type(e).__name__}: {e}", "tasks": {}}
    if not raw.get("enabled"):
        return {"available": False,
                "reason": (raw.get("reason") or "任务运行历史日志是关闭的") +
                          "。开启命令: wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true(需管理员)",
                "tasks": {}}
    if raw.get("reason"):
        return {"available": False, "reason": raw["reason"], "tasks": {}}

    evs = raw.get("events") or []
    # Return codes are kept as a distribution, NOT collapsed into zero/non-zero here. Some tasks
    # encode a VERDICT in the exit code rather than a failure; those codes are declared per task as
    # ok_codes and the ok-code test is applied later, where the declaration is in scope.
    per: dict[str, dict] = defaultdict(lambda: {**dict.fromkeys(evtlog.EVENT_FIELDS.values(), 0),
                                                "rcs": Counter(), "byDay": defaultdict(int),
                                                "todayRuns": []})
    today_str = date.today().isoformat()
    for e in evs:
        t = per[e["task"]]
        i = e["id"]
        if i in evtlog.EVENT_FIELDS:
            t[evtlog.EVENT_FIELDS[i]] += 1
            if evtlog.EVENT_FIELDS[i] == "starts":
                day = e["t"][:10]
                t["byDay"][day] += 1
                # Today's actual starts, so the timeline can draw them against the planned marks.
                if day == today_str:
                    t["todayRuns"].append(e["t"][11:16])
        elif i == evtlog.ACTION_RC:
            z = norm_rc(e.get("rc"))
            if z is not None:
                t["rcs"][z] += 1

    out_tasks = {}
    for name, t in per.items():
        out_tasks[name] = {
            **{k: t[k] for k in evtlog.EVENT_FIELDS.values()},
            "rcs": {str(k): v for k, v in t["rcs"].items()},
            "byDay": dict(t["byDay"]),
            "todayRuns": sorted(set(t["todayRuns"])),
        }
    # count 必须带着它的窗口一起给。撞上条数上限时,取到的是最近 N 条,不是最近 30 天;
    # 没截断也不等于覆盖满了窗口(这个通道是滚动缓冲),所以把真实回溯到哪天一起说出来。
    truncated = bool(raw.get("truncated"))
    _oldest = raw.get("oldest")
    if truncated:
        scope = (f"最近 {RUNLOG_MAX_EVENTS} 条(撞上条数上限,没有覆盖满 {RUNLOG_DAYS} 天"
                 + (f",最早只到 {str(_oldest)[:10]}" if _oldest else "") + ")")
    else:
        scope = f"最近 {RUNLOG_DAYS} 天" + (
            f"(日志只回溯到 {str(_oldest)[:10]})" if _oldest else "")
    return {"available": True, "reason": None, "since": raw.get("since"),
            "oldest": raw.get("oldest"), "count": raw.get("count", len(evs)),
            "countScope": scope,
            "truncated": truncated,
            # 读到一半失败、以及解析不了的条数都带出去,否则大批事件被丢掉时页面只看到次数变少。
            "partial": bool(raw.get("partial")),
            "dropped": int(raw.get("dropped") or 0),
            "tasks": out_tasks,
            "note": ("这一份是真实运行记录(每次启动、完成、动作返回码),和上面那个轮询观察是两回事。"
                     "它只回溯到日志被启用那天,所以空不等于没跑过,而是「还没记到」。")}


# --------------------------------------------------------------------------- database
def load_from_db():
    """Read history and run data from the console database.

    This is the whole performance story. Reading the Windows Operational event log costs 109 seconds
    end to end and used to sit on the request path, so every page load paid it. The ingester pays
    it out of band instead, and this reads the result in single-digit milliseconds.

    ⚠ 排班这件事这里记错过两次,方向相反,所以现在只写形状、不写数字,
    具体排班归机器侧、不归这个仓:

    第一次写的是「每小时付一次」,而运行事件那一半当时根本没在推进。
    第二次(2026-09-08 核实后)改成「没有任何计划任务在跑 console_ingest.py」,
    **那次核实的方法有盲区**:它按名字去任务计划里找,而每小时那条路径是被另一个任务
    在内部调用的,按名字永远查不到。于是一条「我核实过,没有」的注释,
    比原来那句错得更有底气 —— 而它还经 `/api` 下发到了页面上。

    真实形状是两条路径,拆开是有代价换来的:一条高频、跳过运行日志,
    另一条低频、补上那一半。它们合在一起过,而那会因为内外两层超时限制打架
    把任务连杀几十次(内层预算够不着,外层铡刀每次先落)。

    **要知道此刻有没有在跑,看数据本身的新鲜度,不要读这段注释。**
    摄入新鲜度由 `ingest_verdict` 判定并下发,那才是活的。

    Returns (hist, runs, note) shaped EXACTLY like the file-parsing versions, because console.html
    reads seventeen keys off them and a reshape here is a silently blank page there.
    Returns (None, None, reason) when there is no database, and the caller then falls back to the
    slow path with the reason stated. Falling back silently would hide the fact that the fast path
    is not working.
    """
    con, st = console_store.connect_ro()
    if st:
        return None, None, st.message

    try:
        by_day = console_store.health_by_day(con, days=45)
        totals = console_store.health_totals(con, days=45)
        rtot = console_store.run_totals(con, days=60)
        today_runs = console_store.runs_today(con)
        cov = console_store.db_coverage(con)
    finally:
        con.close()

    all_days = sorted({d for t in by_day.values() for d in t})
    htasks = {}
    for task, c in totals.items():
        htasks[task] = {
            "obs": c.get("obs", 0), "judged": c.get("judged", 0),
            "ok": c.get("ok", 0), "bad": c.get("bad", 0),
            "stale": c.get("stale", 0), "neutral": c.get("neutral", 0),
            # other 是判词表认不出来的那些。它进分母(judged = 全部 - neutral)却不出现在
            # 任何字段里,于是监控器换一种措辞之后,每一行会显示 health 0.0% 而
            # ok / bad / stale 全是 0 : **同一行里两个自称权威的数字互相矛盾,
            # 而没有任何字段说明观察去哪了**。history.py 那条通路 2026-09 就补上了这个桶,
            # 而数据库这条主通路一直没有, 于是那次修复在实际走的通路上完全没生效。
            "other": c.get("other", 0),
            "health": c.get("health"),
            "byDay": by_day.get(task, {}),
        }
    # available 原来硬编码成 True,和库里有没有行无关。文本那条路的同一个判断是相反的
    # (history.load 在一条都没解析出来时返回 available=False 加一句明确的原因)。
    # 于是同一个事实(没有可用的观察序列)在两条代码路径上得到相反的答案,而页面只信 available:
    # 一个只有 schema 没有数据的库,会让热力图走正常分支画一张空表、健康% 全是「-」,
    # 而没有任何一处说「库里还没有观察数据」。
    #
    # 判据用**窗口内实际拿到的天数**,不用全表行数:全表有行但最近 45 天为空,
    # 同样是「这张图没东西可画」,而那时摄入器很可能已经停了。
    if not all_days:
        _rows = cov["health"]["rows"]
        _why = ("数据库里还没有观察数据,跑一次 python -m background_task_core ingest --backfill"
                if not _rows else
                f"最近这个窗口内没有观察(全表 {_rows} 行,最后一条在 "
                f"{cov['health'].get('to') or '未知'}),摄入器可能已经停了")
        hist = {"available": False, "reason": _why, "source": "db",
                "matched": _rows, "skipped": 0, "days": [], "tasks": {},
                "lastIngest": cov.get("lastIngest"),
                "ingest": console_store.ingest_verdict(cov.get("lastIngest"))}
    else:
      hist = {
        "available": True, "reason": None, "source": "db",
        # lastIngest 原来算完就被丢掉,而它正是区分「摄入器挂了」和「本来就没跑过」的唯一信号。
        "lastIngest": cov.get("lastIngest"),
        # 而光有时间戳还不够:没有人会读一眼时间然后在心里减出九天。判定在这里做完,
        # 页面只负责搬结论, 页面自己再判一次就是同一条规则的第二份,两份一定会漂。
        "ingest": console_store.ingest_verdict(cov.get("lastIngest")),
        "matched": cov["health"]["rows"], "skipped": 0, "days": all_days, "tasks": htasks,
        "caveat": ("检查通过率按巡检记录统计，同一任务可被重复检查。"
                   "动作成功率按 Windows 日志中的动作返回码统计。"),
    }
    # 运行数据这一半也要说原因。旁边 hist 那一半为同一情形写了三种具体原因,
    # 而这里 reason 恒为 None, 于是库可用但 run_event 是空表时,页面上只剩一句
    # 没有原因的「无运行日志」,而那句话在「日志通道关着」「摄入器从没跑过」
    # 「摄入器停了」三种完全不同的处境下逐字相同。
    # 更糟的是 warnings 只在**回落路径**上才收 runs["reason"],所以这条路上
    # 连那句没有原因的话都不会进警告区。
    _rruns = cov["runs"]["rows"]
    _rwhy = None
    if not _rruns:
        _rwhy = ("数据库里还没有运行事件。要么运行日志通道是关的,要么摄入器还没跑过 —— "
                 "跑一次 python -m background_task_core ingest 就知道是哪一种。"
                 "⚠ 那个通道是滚动缓冲,实测约 5-8 天就会覆盖,拖着不摄入等于永久丢失。")
    runs = {
        "available": _rruns > 0, "reason": _rwhy,
        "since": cov["runs"]["from"], "oldest": cov["runs"]["from"],
        "count": cov["runs"]["rows"], "tasks": rtot,
        # 数据库那条路的 count 是全表行数,不限日期,和上面回落路径那条不是一个量。
        "countScope": "库里全部",
        # ⚠ 这句里**不要再写排班**。它写错过两次、方向相反,理由见 load_from_db 的
        # docstring;而这一句在 RUNLOG_OUT 的导出白名单里,会真的经 /api 到达页面,
        # 一个关于机器配置的断言从这里下发出去,就成了对外契约的一部分,
        # 而这个仓看不见那台机器的排班。摄入到底新不新鲜由 ingest_verdict 当场判,
        # 那个判据读的是数据自己的时间,不读任何人写下的承诺。
        "note": ("真实运行记录,来自 Windows 任务计划的运行日志,由摄入器写进数据库。"
                 "⚠️ 那个日志是滚动缓冲,实测约 5 天就会覆盖,所以没被摄入的历史是永久丢失的。"),
    }
    for name, r in rtot.items():
        r["todayRuns"] = today_runs.get(name, [])
    return hist, runs, None


# --------------------------------------------------------------------------- merge
def categories_path() -> Path | None:
    """分类配置在哪。页面读它、删除任务时改它,两边必须是同一个文件,所以位置只在这里算一次。"""
    explicit = _env_path("BACKGROUND_TASK_CORE_CATEGORIES")
    if explicit is not None:
        return explicit
    root = console_store.companion_root()
    return root / "categories.json" if root is not None else None


def load_categories() -> tuple[list[dict], str | None]:
    """Return (categories, warning). A missing map is a stated condition, never a silent default."""
    try:
        p = categories_path()
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        return [], f"分类配置目录不可用: {error}"
    if not p or not p.exists():
        return [], (f"没有分类配置({p}),所有任务归入「未分类」。"
                    f"复制仓里的 categories.example.json 过去并按你的实际任务改。")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return [], f"分类配置解析失败({p}): {e}"
    cats = raw.get("categories") if isinstance(raw, dict) else raw
    if not isinstance(cats, list) or not cats:
        return [], f"分类配置里没有 categories 数组({p})"
    return cats, None


def load_health() -> tuple[dict, list, str | None]:
    """Return (merged declaration per task name, raw declarations, warning).

    No default path. Unset means NOT CHECKED, which the page renders as unknown, not as a pass.
    The merged map feeds the task table (one row per task); the raw list feeds artifact freshness,
    which judges every declaration on its own.
    """
    p = _env_path("BACKGROUND_TASK_CORE_HEALTH")
    if not p:
        return {}, [], "没有设 BACKGROUND_TASK_CORE_HEALTH,健康监控覆盖这一列是「未检查」,不是通过。"
    if not p.exists():
        return {}, [], f"健康监控清单不存在: {p}"
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception as e:
        return {}, [], f"健康监控清单解析失败({p}): {e}"
    # ⚠ 这一行以前会**静默吃掉**两种条目:漏写 name 的直接丢,重名的后者覆盖前者,
    # 两种都不计数、不产生任何 warning。而「产物新鲜度覆盖率」这个数
    # (freshness.py 里 coverage = judged/total)算的是**过滤之后**那份列表,
    # 被吃掉的那部分同时从分子和分母里消失, 于是覆盖率对「清单条目被吃掉」
    # 这件事完全免疫,永远掉不下来。
    # 屏幕上:清单里写了 12 个、有一条把 "name" 打成了 "task",页面显示
    # 「0 · 覆盖 100% · 共 11」,绿色;那个任务的产物一个月不更新也永远不会出现在
    # 「要人管的事」里,而 100% 恰恰是本该喊出「我没查全」的那个数字。
    raw_tasks = d.get("tasks", [])
    groups: dict[str, list] = {}
    unnamed = 0
    for t in raw_tasks:
        if not isinstance(t, dict) or not t.get("name"):
            unnamed += 1
            continue
        groups.setdefault(t["name"], []).append(t)

    out = {n: (g[0] if len(g) == 1 else _merge_decls(g)) for n, g in groups.items()}

    warn = None
    multi = {n: len(g) for n, g in groups.items() if len(g) > 1}
    if unnamed or multi:
        bits = []
        if unnamed:
            bits.append(f"{unnamed} 条没有 name,被丢掉了")
        if multi:
            bits.append("同名多条(已按最严的那条合并): "
                        + ", ".join(f"{n}x{k}" for n, k in sorted(multi.items())[:5]))
        warn = (f"健康监控清单声明了 {len(raw_tasks)} 条,归到 {len(out)} 个任务名 —— "
                + ";".join(bits) + "。")
    return out, raw_tasks, warn


# 同一个任务名在清单里可以有多条声明:一个任务把几件事折叠进来之后,
# 每件事各写一条,而监控器是 `foreach ($entry in $cfg.tasks)` **逐条**评估的。
# ⚠ 控制台这边原来是 `{t["name"]: t for ...}`,**只留下最后一条**,
# 于是它对同一个任务显示的阈值和产物,可能比监控器实际执行的那套**更松**:
# 实测本机有一个任务写了三条(26h/26h/48h),控制台留下的正是 48h 那条,
# 盯的还是另一个产物。屏幕上没有任何一处显示「这里还有两条声明」。
# 合并规则一律取**更严**的那一侧:年龄上限取最小、豁免类的布尔取或
# (它们都是「更不容易被判绿」的方向)、产物列全部。宁可界面比监控器严,
# 不可比它松, 松的那一侧会让人以为已经查过了。
_STRICTER_MIN = ("max_age_hours", "artifact_max_age_hours", "grace_hours")
_STRICTER_OR = ("artifact_cannot_prove_success", "exit_code_is_authoritative")


def _merge_decls(decls: list[dict]) -> dict:
    out = dict(decls[0])
    for d in decls[1:]:
        for k, v in d.items():
            if k in _STRICTER_MIN:
                have = out.get(k)
                out[k] = v if have is None else min(have, v)
            elif k in _STRICTER_OR:
                out[k] = bool(out.get(k)) or bool(v)
            elif k not in out or out[k] in (None, ""):
                out[k] = v
    labels = [d.get("label") for d in decls if d.get("label")]
    if labels:
        out["label"] = " / ".join(dict.fromkeys(labels))
    return out


def load_allowlist() -> tuple[set[str] | None, str | None]:
    """None means NOT CHECKED. An empty set would read as 'nothing is backed up', which is a
    different and much louder claim, so the two are never collapsed."""
    p = _env_path("BACKGROUND_TASK_CORE_ALLOWLIST")
    if p is None:
        return None, None
    if not p.exists():
        return None, f"备份 allow-list 指向的文件不存在: {p}"
    try:
        txt = p.read_text(encoding="utf-8-sig", errors="replace")
    except Exception as e:
        return None, f"读不了备份 allow-list: {e}"
    # 解析器只有一份(allowlist.py)。
    names, why = allowlist.parse_names(txt)
    if names is None:
        return None, f"{p}: {why}"
    return names, None


def status_of(t: dict, okset: set[int]) -> tuple[str, str]:
    if t["state"] == "Disabled":
        return "disabled", "已停用"
    # ⚠ 读不到任务信息**不是**「失败」。collect.ps1 以前把 Get-ScheduledTaskInfo 的异常
    # 整个吞掉,于是 rcRaw 是 None,而下面那行 `rc in ok` 为假,直接返回
    # ('bad', '失败 ' + (rcHex or '?')), **把「我没读到」编码成了一个确定的坏结论**。
    # 屏幕上是一个红色的「失败 ?」,人会去查一个其实没失败的任务。
    if t.get("infoError"):
        return "unknown", "信息读不到"
    rc = t.get("rcRaw")
    if rc == RC_RUNNING or t["state"] == "Running":
        return "running", "常驻中"
    if rc == RC_NOT_RUN:
        return "pending", "尚未首跑"
    return ("ok", "正常") if rc in okset else ("bad", "失败 " + (t.get("rcHex") or "?"))


def issues_of(t: dict, allow: set[str] | None) -> list[list[str]]:
    out: list[list[str]] = []
    if allow is not None and not t["inAllow"]:
        out.append(["bad", "不在备份清单,换机会静默丢失"])
    if t["healthKnown"] and not t["inHealth"]:
        out.append(["bad", "不在健康监控里,死了没人知道"])
    if t.get("elsewhere"):
        out.append(["info", f"由 {t['elsewhere']} 代为监视(已声明的例外)"])
    if t.get("cannotProve"):
        out.append(["info", "已核实:这个产物撑不起豁免,监控豁免已对它关闭"])
    if t.get("authoritative"):
        out.append(["info", "退出码是权威,任何产物都不得覆盖它"])
    if t["state"] != "Disabled":
        if not t["catchup"]:
            out.append(["warn", "漏火不补跑:错过的触发直接跳过"])
        if t["timeout"] in ("PT72H", "PT0S"):
            out.append(["warn", f"超时上限 {t['timeout']} 等于无限制"])
        if t["stopOnBattery"]:
            out.append(["warn", "拔电源会被中途杀掉"])
        if t["refuseOnBattery"]:
            out.append(["warn", "用电池时拒绝启动"])
    return out


def _epoch(stamp: str | None) -> float | None:
    """collect.ps1 hands back 'yyyy-MM-dd HH:mm' local time, or null. Null means the scheduler had
    nothing to report, which is NOT the same as 'ran at the epoch', so it stays None all the way
    through rather than becoming a very confident 1970."""
    if not stamp:
        return None
    try:
        return datetime.strptime(stamp, "%Y-%m-%d %H:%M").timestamp()
    except (TypeError, ValueError):
        return None


def build_freshness(tasks: dict, merged: dict, declarations: list,
                    health_reason: str | None = None) -> dict:
    """Artifact freshness for every task the health manifest declares.

    The manifest is the input, not the task list: a task nobody declared an artifact for cannot be
    judged on freshness, and saying so is the point. When the manifest itself is missing, coverage
    is 0 and the page says NOT CHECKED instead of drawing an empty green board.
    """
    if not merged and not declarations:
        # 把 load_health 给出的**具体**原因透传上去,不要在这里换成一句笼统的话。
        # 「清单解析失败(JSON 坏了)」和「压根没设环境变量」是两件事:前者是需要修的故障,
        # 后者是没启用。压成同一句之后,页面上没有任何办法把它们分开。
        return {"tasks": [], "summary": {"total": 0, "counts": {}, "judged": 0,
                                         "coverage": 0.0, "bad": 0},
                "reason": health_reason
                          or "没有健康清单,新鲜度这一栏是「未检查」,不是通过。"}
    rows = {}
    for name, t in tasks.items():
        rows[name] = {
            "state": t.get("state"),
            "last_rc": t.get("rcRaw"),
            "last_run": _epoch(t.get("lastRun")),
            "next_run": _epoch(t.get("nextRun")),
            "missed_runs": t.get("missedRuns") or 0,
            "observations": t.get("observations") or {},
        }
    # 逐条评估,不是逐个任务名:合并那一份是任务表的口径(一行一个任务),而新鲜度问的是
    # 「每一件声明过的事是不是都还在跑」,把折叠进一个任务的几件事合成一条,等于把盲区原样保留。
    return freshness.evaluate(declarations, rows, time.time())


# 导出给页面的字段。新增一个字段时要加进这里,否则它到不了页面。
# tasks 很大且已并进每一行,所以不导出。
HISTORY_OUT = ("available", "reason", "days", "caveat", "matched", "source", "lastIngest",
               "ingest")
RUNLOG_OUT = ("available", "reason", "since", "oldest", "count", "note",
              "partial", "dropped", "truncated", "countScope")

TASK_INFO_FIELDS = frozenset({"title", "summary", "cadence", "status", "advice", "verdict", "asOf"})
TASK_INFO_VERDICTS = frozenset({"keep", "fix", "urgent", "adjust", "decide", "remove", "disabled"})


def build_payload() -> dict:
    rc, out, err = run_ps(COLLECT)
    if rc != 0 or not out:
        return {"error": f"采集失败 (rc={rc}): {err or out or '无输出'}"}
    raw = json.loads(out)

    cats, warn_cat = load_categories()
    health, declarations, warn_health = load_health()
    allow, warn_allow = load_allowlist()
    warnings = [w for w in (warn_cat, warn_health, warn_allow) if w]

    assigned: dict[str, str] = {}
    dup_cat: dict[str, list[str]] = {}
    descs: dict[str, str] = {}
    infos: dict[str, dict[str, str]] = {}
    # taskInfo 的三种写坏法各报各的,而且报出任务名:写坏了的说明和没写说明要做的事不一样,
    # 不能都落成页面上的「未填写」「未评估」。
    bad_info: list[str] = []
    # 任务名 -> 坏在哪里。这句话会原样显示在那一行的明细里:汇总警告在「诊断」分区,
    # 自动化的三个标签页上都看不到它。
    invalid_info: dict[str, str] = {}
    unknown_keys: list[str] = []
    bad_verdicts: list[str] = []
    for c in cats:
        for n in c.get("tasks", []):
            # 分类配置是仓外的手写 JSON,复制粘贴一行就能让一个任务落在两个大类里。
            # 原来这里直接覆盖,于是同一个任务在表里出现两行、在两个大类的评分里各贡献一次分母,
            # 而顶部的「总数」按去重后的任务数算 : 同一屏上「总数 40」和「41/41 行」并存,
            # 所有数字都还在正常渲染,看不出哪一份是对的。
            # 现在只认第一次归属(让所有计数对齐),并把重复归属**说出来**,
            # 悄悄挑一个和悄悄算两遍一样坏,区别只是坏得安静。
            if n in assigned:
                dup_cat.setdefault(n, [assigned[n]]).append(c["name"])
                continue
            assigned[n] = c["name"]
        # Optional per-task Chinese descriptions. The category map is machine config living outside
        # this repo, which is where a description of the operator's real automation belongs.
        for n, d in (c.get("taskDesc") or {}).items():
            if d:
                descs[n] = d
        raw_info = c.get("taskInfo", {})
        if not isinstance(raw_info, dict):
            # 整张表不是对象,受影响的是哪些任务无从知道,只能按大类报。
            bad_info.append(f"{c.get('name') or '未命名大类'} 的整张 taskInfo")
            continue
        for n, entry in raw_info.items():
            if not isinstance(entry, dict):
                bad_info.append(n)
                invalid_info[n] = "整条不是一个对象"
                continue
            not_text = sorted(k for k, v in entry.items() if k in TASK_INFO_FIELDS and not isinstance(v, str))
            if not_text:
                bad_info.append(n)
                invalid_info[n] = f"这些字段不是文字:{'、'.join(not_text)}"
                continue
            cleaned = {k: v for k, v in entry.items() if k in TASK_INFO_FIELDS}
            unknown = sorted(k for k in entry if k not in TASK_INFO_FIELDS)
            if unknown:
                # 不认识的键多半是拼错的已知键(asof、advise),悄悄丢掉等于这一项没写。
                unknown_keys.append(f"{n}({'、'.join(unknown)})")
            verdict = cleaned.get("verdict")
            if verdict is not None and not verdict.strip():
                # 空的建议值就是没写建议:按「未评估」显示,不报「认不出」。
                del cleaned["verdict"]
            elif verdict is not None and verdict not in TASK_INFO_VERDICTS:
                # 认不出的建议值保留原文另放一个字段,前端显示「建议无法识别」而不是「未评估」。
                del cleaned["verdict"]
                cleaned["verdictUnrecognized"] = verdict
                bad_verdicts.append(f"{n}({verdict})")
            infos[n] = cleaned

    def listed(items: list[str]) -> str:
        shown = list(dict.fromkeys(items))
        return "、".join(shown[:6]) + (f" 等 {len(shown)} 项" if len(shown) > 6 else "")
    if bad_info:
        warnings.append(f"分类配置里有 {len(bad_info)} 条 taskInfo 写法不对,已整条忽略:{listed(bad_info)}。")
    if unknown_keys:
        warnings.append(f"分类配置里的 taskInfo 有不认识的字段,这些字段已忽略:{listed(unknown_keys)}。")
    if bad_verdicts:
        warnings.append(f"分类配置里有 {len(bad_verdicts)} 个建议值认不出,页面上显示为「建议无法识别」:"
                        f"{listed(bad_verdicts)}。")

    tasks: dict[str, dict] = {}
    oksets: dict[str, set[int]] = {}
    for t in raw["tasks"]:
        e = health.get(t["name"], {})
        t["inAllow"] = (allow is not None and t["name"] in allow)
        t["inHealth"] = bool(e)
        t["healthKnown"] = bool(health)
        t["label"] = e.get("label")
        # Chinese override first, then the task's own description. Neither is invented: if both are
        # absent the cell stays empty rather than being filled with a plausible guess.
        t["desc"] = descs.get(t["name"]) or t.get("description") or None
        t["info"] = infos.get(t["name"], {})
        if t["name"] in invalid_info and t["name"] not in infos:
            t["infoInvalid"] = invalid_info[t["name"]]
        # 两个键名都要认,而且**只能有一份表**知道它们叫什么。
        # 这里以前是 `e.get("ok_codes", [])`,只认一个名字:声明成 ok_exit_codes 的任务
        # 在这条渲染通路上被静默丢掉,而 freshness 那条认全, 同一个退出码,
        # 任务表判红、新鲜度判绿,同一屏两个自称权威的结论,没有任何一处对账。
        t["okCodes"] = ",".join(str(x) for x in declared_ok_codes(e)) or None
        oksets[t["name"]] = ok_codes(e)
        t["artifact"] = e.get("artifact")
        t["artifactMax"] = e.get("artifact_max_age_hours")
        t["elsewhere"] = e.get("watched_elsewhere")
        t["cannotProve"] = bool(e.get("artifact_cannot_prove_success"))
        t["authoritative"] = bool(e.get("exit_code_is_authoritative"))
        k, lbl = status_of(t, oksets[t["name"]])
        t["sk"], t["sl"] = k, lbl
        t["issues"] = issues_of(t, allow)
        tasks[t["name"]] = t

    # taskInfo 的键要对上计划程序里真有的任务。键拼错一个字母,那条说明哪里都显示不出来,
    # 真正的那个任务安静地落成「未评估」;拼错字段名会报警,拼错任务名也得报。
    orphan_info = sorted(n for n in set(infos) | set(invalid_info) if n not in tasks)
    if orphan_info:
        warnings.append(f"分类配置里有 {len(orphan_info)} 条 taskInfo 对不上计划程序里的任何任务,"
                        f"没有显示:{listed(orphan_info)}。")

    groups = []
    for c in cats:
        rows = [tasks[n] for n in c.get("tasks", [])
                if n in tasks and assigned.get(n) == c["name"]]
        if rows:
            groups.append({"cat": c["name"], "desc": c.get("desc", ""), "rows": rows})
    if dup_cat:
        warnings.append(
            "分类配置里有任务被写进了多个大类,只认第一个:"
            + ";".join(f"{n} -> {'/'.join(cs)}" for n, cs in sorted(dup_cat.items())))

    orphan = [t for n, t in tasks.items() if n not in assigned]
    if orphan:
        # Surfaced as its own group rather than dropped. A task the category map forgot is exactly
        # the one nobody is looking at, so hiding it would defeat the point of the page.
        groups.append({"cat": "未分类",
                       "desc": "分类配置里没有它们。加进 categories.json,否则每次都会落在这里。",
                       "rows": orphan})

    # DATABASE FIRST. The slow path stays as a fallback rather than being deleted, because a
    # machine that has not run the ingester yet must still get a working console; but when it is
    # used, the page says so, so "fast path broken" never looks like "everything is fine".
    hist, runs, db_reason = load_from_db()
    # runs 的原因在**两条路上都要收**。原来只有回落路径收,于是数据库可用但表为空时,
    # 那句原因算出来了却没有任何地方显示它。
    if runs and runs.get("reason") and not runs.get("available"):
        warnings.append(runs["reason"])
    if db_reason:
        warnings.append(f"数据库不可用,回落到直接解析日志(会慢很多):{db_reason}")
        hist = history.load(os.environ.get("BACKGROUND_TASK_CORE_HISTORY"))
        if hist.get("reason"):
            warnings.append(hist["reason"])
        runs = load_runlog()
        if runs.get("reason"):
            warnings.append(runs["reason"])
    for t in tasks.values():
        t["hist"] = hist["tasks"].get(t["name"]) or None
        rr = runs["tasks"].get(t["name"])
        if rr:
            rr = dict(rr)
            # Apply the task's own declared ok_codes, the same set the health monitor honours. A run
            # whose exit code is a declared verdict counts as a run that did its job.
            okset = oksets[t["name"]]
            total = sum(rr["rcs"].values())
            good = sum(v for k, v in rr["rcs"].items()
                       if (lambda z: z is not None and z in okset)(norm_rc(k)))
            # judged 是**动作返回码事件**的条数,不是旁边那一列显示的「实跑」(启动事件数)。
            # 一个多动作任务每次运行写多条 201,分母大于实跑;一个 rc 事件被日志滚动截断的任务,
            # 分母小于实跑。于是同一行里两个数看似同源实则不同分母,而页面没有任何地方能让人
            # 分辨:「实成功 100%」可能只建立在 1 个动作事件上,而旁边写着实跑 40 次。
            # judged 一起送到前端,让那一格能说出自己是拿几个样本算的。
            rr["judged"] = total
            rr["good"] = good
            rr["successRate"] = round(100.0 * good / total, 1) if total else None
            rr["okApplied"] = sorted(okset - {0}) or None
        t["runs"] = rr or None

    # Per-category scores, four dimensions, each already a percentage so they are commensurable.
    # A dimension with nothing to measure against is None and renders as NOT CHECKED, never as 0:
    # a zero would read as "this category scores badly", which is a claim, and we would not have
    # made it. Rendered as a grid rather than a radar because nine categories on one radar is
    # unreadable and nine small radars say less than one grid the eye can scan down a column.
    scores = []
    for g in groups:
        rows = g["rows"]
        n = len(rows)
        hs = [r["hist"]["health"] for r in rows if r.get("hist") and r["hist"]["health"] is not None]
        sched_ok = sum(1 for r in rows
                       if r["state"] == "Disabled" or not any(i[0] == "warn" for i in r["issues"]))
        scores.append({
            "cat": g["cat"],
            "n": n,
            "health": round(sum(hs) / len(hs), 1) if hs else None,
            # 健康% 的分母是「本类里**有观察记录的**任务数」,而同一行另外三列的分母是
            # 「本类任务数」n,页面上原来只给出 n。一个 10 个任务、只有 1 个被轮询到且它
            # 100% 的大类会显示「数 10 · 健康 100.0(绿)」:一个被喂了几乎空输入的检查器
            # 打印出了满分绿色,而它的分母不在屏幕上任何地方。
            "healthN": len(hs),
            "backup": round(100.0 * sum(1 for r in rows if r["inAllow"]) / n, 1) if (allow is not None and n) else None,
            "watched": round(100.0 * sum(1 for r in rows if r["inHealth"] or r.get("elsewhere")) / n, 1) if (health and n) else None,
            "hygiene": round(100.0 * sched_ok / n, 1) if n else None,
        })

    # Today's schedule, expanded from the structured triggers. Actual runs recorded today are
    # attached alongside the planned ones so the two can be compared on the same axis: a planned
    # mark with no actual run beside it is the thing worth seeing.
    tl = timeline.build(tasks, runs.get("tasks") or {})

    n_issue = sum(1 for t in tasks.values() for i in t["issues"] if i[0] in ("bad", "warn"))
    fresh = build_freshness(tasks, health, declarations, warn_health)
    return {
        "groups": groups,
        "freshness": fresh,
        "warnings": warnings,
        # 字段白名单:生产者新增的键要加进 HISTORY_OUT / RUNLOG_OUT 才会到页面。
        "history": {k: hist[k] for k in HISTORY_OUT if k in hist},
        "runlog": {k: runs[k] for k in RUNLOG_OUT if k in runs},
        "scores": scores,
        "timeline": tl,
        "summary": {
            "total": len(tasks),
            "bad": sum(1 for t in tasks.values() if t["sk"] == "bad"),
            "disabled": sum(1 for t in tasks.values() if t["state"] == "Disabled"),
            "issues": n_issue,
            "generated": raw["generated"],
            "allowChecked": allow is not None,
        },
    }


def live_tasks() -> list[dict]:
    """当场重新枚举一次在册任务(根路径、已剔掉厂商任务)。删除和修复都靠它核实 HTTP 来的名字。"""
    rc, out, err = run_ps(COLLECT)
    if rc != 0 or not out:
        raise task_control.Refused(f"重新枚举失败,拒绝执行动作: {err or out or '无输出'}", "enumeration_failed")
    try:
        return json.loads(out)["tasks"]
    except (ValueError, KeyError, TypeError) as e:
        raise task_control.Refused(f"重新枚举的输出无法解析: {type(e).__name__}", "enumeration_failed") from None


def task_facts(name: str) -> dict:
    """修复工单的参考事实。取自任务页同一份载荷,所以工单里的判定和页面上的一字不差。"""
    return task_repair.facts_from_payload(build_payload(), name)


class Handler(ComponentRoutes, BaseHTTPRequestHandler):
    server_version = "background-task-core"
    # fail-closed:空集合什么都不匹配,所以一个没走过 main() 的 Handler 会拒绝每一个请求。
    # 默认成 "*" 会让「忘了设置」和「明确允许一切」变成同一件事,而那正是这份代码
    # 在别处一直拒绝的形状。
    allowed_hosts: frozenset = frozenset()
    # 同样是 fail-closed,而且理由和上面那三行逐字相同。
    # ⚠ 这里以前是 `token = ""`,做的正好是上面那段注释否定的那件事:
    # `_authed` 是 `compare_digest(请求头 or "", self.token)`,token 还是 "" 时,
    # 一个**根本不带这个头**的请求会得到 compare_digest("", "") → True,直接过鉴权。
    # 今天没被利用,只是因为 Host 闸恰好先开火(空集合拒掉一切), 也就是说令牌这道控制
    # 在「没初始化」状态下靠的是另一道控制兜底,而两道控制的默认值方向相反。
    # 任何设好 allowed_hosts 却漏设 token 的用法(复用 Handler、
    # 将来在 main() 之外多一条启动路径)都会让 /api/ 全线免鉴权,而页面表现完全正常。
    # None 是一个不可能匹配的哨兵:「忘了设置」和「明确允许」永远不会是同一件事。
    token: str | None = None

    def log_message(self, fmt, *a):  # keep the console quiet; errors still surface in responses
        pass

    def _send(self, code: int, body: bytes, ctype: str, *, revalidate: bool = False):
        etag = None
        if revalidate:
            etag = '"' + hashlib.sha256(body).hexdigest() + '"'
            candidates = ','.join(self.headers.get_all("If-None-Match", []))
            if any(tag.strip().removeprefix("W/") in ("*", etag)
                   for tag in candidates.split(",")):
                code = 304
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if code != 304:
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache" if revalidate else "no-store")
        if etag is not None:
            self.send_header("ETag", etag)
        self.send_header("X-Content-Type-Options", "nosniff")
        # 不许被别人 iframe 进去。Host 白名单防的是 DNS rebinding:攻击者把域名 rebind 到
        # 127.0.0.1 之后 Host 头对不上,于是被挡。iframe 走的是另一条路 :
        # <iframe src="http://127.0.0.1:8787/"> 发出去的 Host 就是 127.0.0.1:8787,
        # 白名单原样放行,页面正常渲染,而且它自带一枚有效令牌。
        # 攻击者不需要读到任何东西(CORS 挡得住读),只需要骗一次点击落在他知道位置的按钮上,
        # 而这一页上的按钮会真删目录、真跑计划任务。
        # frame-ancestors 是权威那一条,X-Frame-Options 给不认识 CSP 的老客户端兜底。
        self.send_header("Content-Security-Policy",
                         "frame-ancestors 'none'; default-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
                         "form-action 'none'")
        self.send_header("X-Frame-Options", "DENY")
        # 页面里没有任何外链,所以 referrer 一栏也没有存在的理由。
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if code != 304:
            self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _drain(self):
        """把请求体读掉再回复,哪怕这次回复是拒绝。

        不读就直接关连接,客户端还在发 body 的那一侧会收到 ECONNRESET 而不是那个 4xx。
        """
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            n = 0
        # 它在鉴权之前就会跑:一个没有令牌的请求自称多大就读多大,等于让任何人
        # 让服务器陪它读几个 G。超过上限的不读,直接断开。
        if n > DRAIN_MAX:
            self.close_connection = True
            return
        if n > 0:
            try:
                self.rfile.read(n)
            except OSError:
                pass

    def _task_body(self, allowed):
        """删除与修复四条 POST 的读体:8192 字节上限,键必须在白名单里。
        返回 body 或 None(已经回过 4xx)。鉴权留在各自处理器的第一行,路由鉴权通扫按处理器正文找它。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = -1
        if length > 8192 and not self.headers.get("Transfer-Encoding"):
            # 超限但不离谱的先读掉再回(见 _drain):不读就关,客户端拿到的是连接被重置,不是这个 413。
            self._drain()
            self.close_connection = True
            self._json(413, {"ok": False, "error": "请求体超过 8192 字节", "code": "too_large"})
            return None
        if length <= 0 or self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            self._json(400, {"ok": False, "error": "invalid request size", "code": "bad_size"})
            return None
        try:
            body = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError):
            self._json(400, {"ok": False, "error": "invalid JSON request", "code": "bad_json"})
            return None
        if (not isinstance(body, dict) or not set(body) <= allowed
                or any(v is not None and not isinstance(v, str) for v in body.values())):
            self._json(400, {"ok": False, "error": "请求体不是对象,或带了不认识的键、非文字的值",
                             "code": "bad_request"})
            return None
        return body

    def _task_delete(self):
        """删除计划任务:/plan 只读预览并发一次性令牌,/apply 凭令牌执行。实现在 task_delete.py。"""
        if not self._authed():
            self._drain()
            return self._json(403, {"error": "bad token"})
        preview = self.path.split("?", 1)[0].endswith("/plan")
        body = self._task_body(frozenset(("name", "reason")) if preview else frozenset(("token", "name")))
        if body is None:
            return None
        try:
            if preview:
                result = task_delete.delete_plan(body.get("name"), body.get("reason"),
                                          live=live_tasks, categories_path=categories_path())
            else:
                result = task_delete.delete_apply(body.get("token"), body.get("name"),
                                           live=live_tasks, categories_path=categories_path())
            # 计划程序那一步没做成(或读不出来做没做成)是 500(和 /api/act 一样);部分完成照样回 200,正文里 status 说清。
            return self._json(500 if result.get("status") in ("failed", "unknown") else 200, result)
        except task_control.Refused as e:
            status = (400 if e.code in task_delete.INPUT_CODES else
                      500 if e.code in ("enumeration_failed", "state_unreadable") else 409)
            return self._json(status, {"ok": False, "error": str(e), "code": e.code})
        except Exception as e:
            return self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"[:400]})

    def _task_repair(self):
        """修复工单:/preview 只读汇总事实,不带后缀的那条经待办主人开单并提交 Agent。实现在 task_repair.py。"""
        if not self._authed():
            self._drain()
            return self._json(403, {"error": "bad token"})
        preview = self.path.split("?", 1)[0].endswith("/preview")
        body = self._task_body(frozenset(("name",)) if preview else frozenset(("name", "note", "request_id")))
        if body is None:
            return None
        try:
            if preview:
                return self._json(200, task_repair.preview(body.get("name"), facts=task_facts))
            return self._json(200, task_repair.submit(body.get("name"), body.get("note"),
                                                      body.get("request_id"), facts=task_facts))
        except task_control.Refused as e:
            return self._json(500 if e.code == "enumeration_failed" else 400,
                              {"ok": False, "error": str(e), "code": e.code})
        except Exception as e:
            return self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"[:400]})

    def _authed(self) -> bool:
        # token 是 None 表示这个 Handler 没被初始化过, 那不是「令牌是空串」,是「没有令牌」,
        # 而没有令牌时唯一安全的答案是拒绝。不要试图在这里生成一个:
        # 一个自己发明令牌的鉴权函数,会让「服务起来了」和「服务起来了但谁都进不去」都消失。
        if not self.token:
            return False
        return secrets.compare_digest(self.headers.get("X-Console-Token", ""), self.token)

    def _host_ok(self) -> bool:
        """Binding 127.0.0.1 stops the network. It does not stop DNS rebinding, and rebinding is
        the attack that matters here: the token is substituted into the page at '/', so anything
        that can make a SAME-ORIGIN request to '/' simply reads the token out of the HTML and then
        has full access to the action endpoints. A Host allowlist is what closes that, because a
        rebound name never matches one of the loopback literals. A missing Host header is rejected.
        """
        host = self.headers.get("Host")
        return bool(host) and host.lower() in self.allowed_hosts

    # 两个入口都套一层兜底。没有它时,任何一个逃出去的异常由 socketserver 的
    # handle_error 打印 traceback 然后**直接关连接**, 而 log_message 被置空,
    # 本地窗口里几乎什么都看不到,客户端拿到的是一个断掉的连接而不是一个错误。
    # 这几种都真的会发生:run_ps 的 subprocess.TimeoutExpired(枚举 90s / 动作 60s)、
    # json.loads(out)["tasks"] 的 ValueError / KeyError。
    # **一个报错方式是「连接消失」的接口,和一个挂掉的服务器长得一样。**
    def _guard(self, fn, label):
        try:
            return fn()
        except Exception as e:                        # noqa: BLE001
            try:
                return self._json(500, {"error": f"{label} 内部错误: "
                                                 f"{e.__class__.__name__}: {e}"[:400]})
            except Exception:
                # 连回话都失败时(连接已经断了)就算了,但不要再让异常继续往上跑,
                # 否则又回到「traceback 打在一个没人看的地方」那个形态。
                return None

    def do_GET(self):
        if self.path.split("?", 1)[0].startswith("/api/convo"):
            with _CONVO_ACCESS:
                return self._guard(self._do_GET, "GET " + self.path.split("?", 1)[0])
        return self._guard(self._do_GET, "GET " + self.path.split("?", 1)[0])

    def do_POST(self):
        route = self.path.split("?", 1)[0]
        try:
            if route.startswith("/api/convo/") and route not in CONVO_UNLOCKED_POSTS:
                with _CONVO_ACCESS:
                    return self._guard(self._do_POST, "POST " + route)
            return self._guard(self._do_POST, "POST " + route)
        finally:
            # Every authenticated POST, whatever its outcome: a lost response is not proof of no change.
            if self._host_ok() and self._authed():
                source_reads.mutated()

    def _do_GET(self):
        if not self._host_ok():
            return self._json(400, {"error": "bad host"})
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            html = PAGE.read_text(encoding="utf-8").replace("__TOKEN__", self.token)
            if os.environ.get('BACKGROUND_TASK_CORE_READ_ONLY') == '1':
                html = html.replace('name="console-read-only" content="false"', 'name="console-read-only" content="true"')
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if path.startswith("/static/"):
            # Exact lexical membership rejects encoded traversal and UNC before I/O.
            rel = path[len("/static/"):]
            if rel not in STATIC_FILES:
                return self._json(404, {"error": "not found"})
            try:
                target = (STATIC / rel).resolve()
                target.relative_to(STATIC.resolve())
                body = target.read_bytes()
            except (OSError, ValueError):
                return self._json(404, {"error": "not found"})
            ctype = "text/css; charset=utf-8" if rel.endswith(".css") else "text/javascript; charset=utf-8"
            return self._send(200, body, ctype, revalidate=True)
        if path == '/api/integrations':
            if not self._authed():
                return self._json(403, {"error": "bad token"})
            return self._json(200, integrations.read_configured())
        if path in self.component_get_routes or path.startswith("/api/llmcall/call/"):
            return self.component_get(path)
        if path in integrations.ROUTES:
            if not self._authed():
                return self._json(403, {"error": "bad token"})
            try:
                return self._json(200, integrations.ROUTES[path].read(globals()))
            except Exception as error:
                return self._json(500, {'error': f'{type(error).__name__}: {error}'})
        if path == "/api/hours":
            if not self._authed():
                return self._json(403, {"error": "bad token"})
            query = parse_qs(urlparse(self.path).query)
            start, end = (query.get("from") or [""])[0], (query.get("to") or [""])[0]
            con, status = console_store.connect_ro()
            if status:
                return self._json(200, {"available": False, "reason": status.message})
            try:
                tasks = console_store.health_by_hour(con, start, end)
                return self._json(200, {"available": True, "from": start, "to": end, "tasks": tasks})
            except ValueError as error:
                return self._json(400, {"error": str(error)})
            finally:
                con.close()
        if path == "/api/task/repairs":
            if not self._authed():
                return self._json(403, {"error": "bad token"})
            try:
                return self._json(200, task_repair.orders())
            except Exception as e:
                return self._json(500, {"error": f"{type(e).__name__}: {e}"[:400]})
        if path.startswith("/vendor/"):
            # 第三方资产随仓发,不吊 CDN:这台控制台正是出事的时候要打开的,
            # 而出事的时候网络是最不该依赖的东西。
            # 这条路由刻意免令牌(<link> 标签发不了自定义头,里面也没有秘密),
            # 所以「不许爬出 vendor」是它唯一的控制,而那道控制必须在**碰文件系统之前**开火。
            #
            # 第一版只有解析后的归属检查,它确实拦得住 : 文件一个字节都没泄。
            # 但 "//host/share/x" 会让 Path.resolve() 在 Windows 上把它当 UNC,
            # 于是**在归属检查之前**先去连那台主机:实测对一个不可路由地址耗时 21.07 秒,
            # 对一台可达的攻击者主机则是一次自动的 NTLM 协商(凭据外泄),
            # 期间还占着一个 handler 线程。免令牌 + 任意主机 + 每请求 21 秒,
            # 这三样凑一起就是一个不用登录的外联与阻塞原语,而返回码始终是干净的 404。
            #
            # 所以改成先按形状拒绝、再逐段拼接,最后仍然保留归属检查兜底。
            # 形状检查的好处是它不需要知道操作系统怎么解释路径 :
            # 上一版的错误正是「让 resolve() 先替我理解这个字符串」。
            rel = unquote(path[len("/vendor/"):])
            segs = rel.split("/")
            if ("\\" in rel or rel.startswith("/") or not rel
                    or any(s in ("", ".", "..") or ":" in s for s in segs)):
                return self._json(404, {"error": "not found"})
            try:
                target = (VENDOR.joinpath(*segs)).resolve()
                target.relative_to(VENDOR.resolve())
            except (ValueError, OSError):
                return self._json(404, {"error": "not found"})
            ctype = {".css": "text/css; charset=utf-8",
                     ".js": "text/javascript; charset=utf-8"}.get(target.suffix, "text/plain")
            try:
                return self._send(200, target.read_bytes(), ctype, revalidate=True)
            except OSError:
                return self._json(404, {"error": "not found"})
        if path in ("/favicon.svg", "/favicon.ico"):
            # --app= 窗口的任务栏图标取的就是页面 favicon,所以这不只是消掉一个 404:
            # 没有它,这个「桌面应用」在任务栏上是一张白纸。
            try:
                return self._send(200, ICON.read_bytes(), "image/svg+xml")
            except OSError:
                return self._json(404, {"error": "no icon"})
        return self._json(404, {"error": "not found"})

    def _do_POST(self):
        if not self._host_ok():
            self._drain()
            return self._json(400, {"error": "bad host"})
        if os.environ.get('BACKGROUND_TASK_CORE_READ_ONLY') == '1':
            self._drain()
            return self._json(403, {'error': '只读预览，无法执行操作'})
        route = self.path.split("?", 1)[0]
        if route in self.component_post_routes:
            return self.component_post(route)
        if self.path.split('?', 1)[0] in ('/api/work/action', '/api/work/stop'):
            if not self._authed():
                self._drain()
                return self._json(403, {'error': 'bad token'})
            try:
                n = int(self.headers.get('Content-Length') or 0)
                if n < 1 or n > 8192 or self.headers.get('Transfer-Encoding'):
                    self.close_connection = True
                    return self._json(400, {'error': 'invalid request size'})
                request = json.loads(self.rfile.read(n))
            except (ValueError, UnicodeError):
                return self._json(400, {'error': 'invalid JSON request'})
            return self._json(200, work_actions.submit(request, stop=self.path.split('?', 1)[0].endswith('/stop')))
        # 四条分开写:路由通扫的正则对 `in (...)` 只认得第一个字面量,写成一组会让后面几条逃过扫描。
        if self.path.split("?", 1)[0] == "/api/task/delete/plan":
            return self._task_delete()
        if self.path.split("?", 1)[0] == "/api/task/delete/apply":
            return self._task_delete()
        if self.path.split("?", 1)[0] == "/api/task/repair/preview":
            return self._task_repair()
        if self.path.split("?", 1)[0] == "/api/task/repair":
            return self._task_repair()
        if self.path.split("?", 1)[0] != "/api/act":
            self._drain()
            return self._json(404, {"error": "not found"})
        if not self._authed():
            self._drain()
            return self._json(403, {"error": "bad token"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._json(400, {"error": f"bad request: {e}"})

        if not isinstance(req, dict):
            return self._json(400, {"error": "request must be an object"})
        name, verb = str(req.get("name", "")), str(req.get("verb", ""))
        if verb not in VERBS:
            return self._json(400, {"error": f"verb not allowed: {verb}"})
        if not name:
            return self._json(400, {"error": "no task name"})
        try:
            task_control.validate(name, verb)
        except task_control.ContractError as e:
            return self._json(400, task_control.error_result(e, name=name, verb=verb))

        # Re-enumerate and check membership rather than trusting the client's name. The page could
        # be stale, and more to the point a name that arrived over HTTP has no standing until this
        # process has seen it in the live task list itself.
        rc, out, err = run_ps(COLLECT)
        if rc != 0 or not out:
            return self._json(500, {"error": f"重新枚举失败,拒绝执行动作: {err or out}"})
        live = {t["name"] for t in json.loads(out)["tasks"]}
        if name not in live:
            return self._json(400, {"error": f"不在本机可管理的任务列表里: {name}"})

        try:
            res = task_control.action(name, verb)
        except Exception as e:
            res = task_control.error_result(e, name=name, verb=verb)
        return self._json(200 if res.get("ok") else 500, res)


def main() -> int:
    ap = argparse.ArgumentParser(description="local scheduled-task console")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    if os.name != "nt":
        print("background-task-core 只在 Windows 上有意义:它读的是 Windows 任务计划。", file=sys.stderr)
        return 2
    for f in (COLLECT, PAGE):
        if not f.exists():
            print(f"缺文件: {f}", file=sys.stderr)
            return 2

    Handler.token = secrets.token_urlsafe(24)
    # Three loopback spellings a browser can legitimately send for this port, and nothing else.
    Handler.allowed_hosts = frozenset((f"localhost:{a.port}", f"127.0.0.1:{a.port}", f"[::1]:{a.port}"))
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"background-task-core: {url}")
    print("  只监听 127.0.0.1。令牌每次启动重新生成,不落盘。关掉这个窗口即停止。")
    if not a.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
