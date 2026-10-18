"""Read side of the task-console database, plus the one place that decides where the file lives.

WHERE THE FILE LIVES, and why this module refuses rather than guesses. The database holds real-run
output: what ran on this machine and when. Under this fleet's data boundary that can never sit in
the public repo, not even gitignored, because .gitignore is advisory and `git add -f` walks straight
through it. Resolution order:

    1. TASK_CONSOLE_DB, if set. An explicit operator override.
    2. <companion>/data/task-console/console.sqlite3, where <companion> comes from the shared
       datadir resolver.
    3. UNINITIALISED. Not a fallback into the repo, not a temp file: a stated condition with
       instructions. A repo-relative fallback is how real data ends up in a public repo, and it is
       the specific defect this boundary exists to prevent.

Resolution is a path decision only: no Git or GitHub calls happen at runtime. The one check kept
is a pure path comparison that refuses a destination inside this payload's own source tree. Keeping
DATA out of public repositories is enforced at commit and push time by the fleet-guards hooks.

READS DEGRADE, WRITES DO NOT. Readers open the file read-only through a URI and, when it is absent
or unreadable, return a state object carrying a code (NO_COMPANION, NO_RESOLVER, STORAGE_REFUSED,
NO_FILE or CORRUPT) and a Chinese sentence explaining it. They never return zeros. "No data" and "zero runs" are different claims and only
one of them is ours to make.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

if __package__:
    from .evtlog import ACTION_RC, EVENT_FIELDS
else:
    from evtlog import ACTION_RC, EVENT_FIELDS

HERE = Path(__file__).resolve().parent
SKILL = "task-console"
DB_NAME = "console.sqlite3"

# A wheel carries the source-owned contract beside the installed package.
_checkout = HERE.parents[1]
SOURCE_ROOT = (_checkout if (_checkout / ".git").exists()
               and (_checkout / "pyproject.toml").is_file() else HERE)
_datadir = None


def _load_datadir():
    global _datadir
    if _datadir is None:
        try:
            from fleet_guards.runtime import companion_resolver
        except ImportError as error:
            raise ValueError('fleet_guards is not installed; install it: '
                             'pip install -e "$(git config --get fleet.guards)"') from error
        _datadir = companion_resolver(source_root=SOURCE_ROOT)
    return _datadir


def companion_root():
    """The companion root from the fleet_guards resolver, or None when uninitialized."""
    root = _load_datadir().resolve_companion_root(SKILL)
    return Path(root).absolute() if root else None


def outside_source(destination):
    """Refuse a DATA destination inside this payload's own source tree. Pure path comparison."""
    path = Path(destination).expanduser().absolute()
    _load_datadir().assert_outside_own_repo(path, SKILL)
    return path


def check_write_path(destination):
    """Path-only check for an external configuration leaf; creates nothing, runs no Git.

    Refuses unsafe lexical forms and links (Guards filesystem rules) and any destination inside
    this payload's own source tree, including its Git metadata.
    """
    from fleet_guards import filesystem

    path = filesystem.validate_path(Path(destination).expanduser().absolute())
    return outside_source(path)


class DbState:
    """Why there is no database, in a form the page can render verbatim."""

    def __init__(self, code: str, message: str, path: str | None = None):
        self.code, self.message, self.path = code, message, path

    def as_dict(self):
        return {"available": False, "code": self.code, "reason": self.message, "path": self.path}


def resolve_db(create_parent: bool = False):
    """Return the database path or a distinct unavailable state; never write on reads."""
    try:
        if os.environ.get("TASK_CONSOLE_DB"):
            p = Path(os.environ["TASK_CONSOLE_DB"]).expanduser().absolute()
        else:
            root = companion_root()
            if root is None:
                return None, DbState("NO_COMPANION",
                    "尚未配置伴生仓。请按 CONFIG.md 设置私有伴生仓。")
            p = root / "data" / "task-console" / DB_NAME
        p = outside_source(p)
        if create_parent:
            p.parent.mkdir(parents=True, exist_ok=True)
        return p, None
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        code = "NO_RESOLVER" if "fleet_guards is not installed" in str(error) else "STORAGE_REFUSED"
        return None, DbState(code, "存储位置无效: " + str(error))


def validate_read_schema(connection):
    """Check the declared schema version and columns without reading or changing runtime rows."""
    expected = sqlite3.connect(":memory:")
    try:
        expected.executescript((HERE / "schema.sql").read_text(encoding="utf-8"))
        if connection.execute("PRAGMA user_version").fetchone()[0] != expected.execute("PRAGMA user_version").fetchone()[0]:
            raise sqlite3.DatabaseError("database schema version does not match this payload")
        tables = expected.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        for (table,) in tables:
            literal = '"' + table.replace('"', '""') + '"'
            required = {row[1] for row in expected.execute("PRAGMA table_info(" + literal + ")")}
            present = {row[1] for row in connection.execute("PRAGMA table_info(" + literal + ")")}
            if not required <= present:
                raise sqlite3.DatabaseError("database lacks required columns in " + table)
    finally:
        expected.close()


def connect_ro():
    """Read-only connection, or (None, DbState). Read-only is not a nicety: the console serves HTTP
    and must never be able to write, so a bug there cannot corrupt the ingester's file."""
    p, st = resolve_db()
    if st:
        return None, st
    if not p.exists():
        return None, DbState("NO_FILE",
                             f"数据库还没建。跑一次 console_ingest.py --backfill 就会从现有日志重建。",
                             str(p))
    con = None
    try:
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        validate_read_schema(con)
        return con, None
    except sqlite3.DatabaseError as e:
        # 调用方拿不到这个连接,显式关掉,不依赖引用计数的时机。
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
        return None, DbState("CORRUPT",
                             f"数据库打不开或结构不对({e})。请保留原文件并核对一致性备份。"
                             "--backfill 只能重读仍可用的源日志，不能完整恢复历史配置和事件。", str(p))


# --------------------------------------------------------------------------- queries
def health_by_day(con, days: int = 45) -> dict:
    rows = con.execute(
        "SELECT task, day, klass, COUNT(*) n FROM health_obs "
        "WHERE day >= date('now','localtime',?) GROUP BY task, day, klass",
        (f"-{days - 1} day",)).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        d = out.setdefault(r["task"], {}).setdefault(r["day"],
                                                     {"ok": 0, "bad": 0, "stale": 0, "neutral": 0, "n": 0})
        d[r["klass"]] = d.get(r["klass"], 0) + r["n"]
        d["n"] += r["n"]
    return out


def health_totals(con, days: int = 45) -> dict:
    rows = con.execute(
        "SELECT task, klass, COUNT(*) n FROM health_obs "
        "WHERE day >= date('now','localtime',?) GROUP BY task, klass",
        (f"-{days - 1} day",)).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["task"], {})[r["klass"]] = r["n"]
    for t, c in out.items():
        n = sum(c.values())
        judged = n - c.get("neutral", 0)
        c["obs"], c["judged"] = n, judged
        c["health"] = round(100.0 * c.get("ok", 0) / judged, 1) if judged else None
    return out


def run_totals(con, days: int = 60) -> dict:
    rows = con.execute(
        "SELECT task, event_id, rc_norm, COUNT(*) n FROM run_event "
        "WHERE day >= date('now','localtime',?) GROUP BY task, event_id, rc_norm",
        (f"-{days - 1} day",)).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        t = out.setdefault(r["task"], {**dict.fromkeys(EVENT_FIELDS.values(), 0), "rcs": {}})
        i, n = r["event_id"], r["n"]
        if i in EVENT_FIELDS:
            t[EVENT_FIELDS[i]] += n
        elif i == ACTION_RC and r["rc_norm"] is not None:
            rc = str(r["rc_norm"])
            t["rcs"][rc] = t["rcs"].get(rc, 0) + n
    return out


def runs_today(con) -> dict:
    rows = con.execute(
        "SELECT task, substr(ts,12,5) hm FROM run_event "
        "WHERE event_id = 100 AND day = date('now','localtime') ORDER BY ts").fetchall()
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r["task"], []).append(r["hm"])
    return {k: sorted(set(v)) for k, v in out.items()}


# 摄入器停摆多久算「要人管」。这两个数不是随手定的档位,它们量的是**丢失风险**:
# Windows 的任务运行日志是滚动缓冲,实测约 5-8 天覆盖一轮,所以没被摄入的运行历史
# 过了那个窗口就是永久没了, 不是「晚点补」,是补不回来。
#   24h  这台机器上任何一条自动流水线的正常间隔都远小于一天。超过一天没摄入,
#        要么排班没了,要么在连续失败。此时还追得回来,所以是「注意」不是「严重」。
#   96h  进入丢失边缘。按实测最短的那个覆盖窗口(5 天)留一天余量,过了这条线
#        就要按「已经开始丢」处理。
INGEST_WARN_HOURS = 24.0
INGEST_LOSS_HOURS = 96.0


def ingest_verdict(last_ingest: dict | None, now=None) -> dict:
    """摄入器还活着吗:一个判定,不是一个时间戳。

    取最旧的那一条流水线判定:任何一条停了,页面上就有一部分数字停在那一刻。
    没有任何摄入记录时是 `never`,不是 `ok`。
    """
    import datetime as _dt

    now = _dt.datetime.now() if now is None else now

    def _parse(s):
        # finished_at 只由 console_ingest.now() 写入,格式固定。
        try:
            return _dt.datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    parsed = [(k, v, _parse(v["at"])) for k, v in (last_ingest or {}).items()
              if isinstance(v, dict) and v.get("at")]
    parsed = [(k, v, t) for k, v, t in parsed if t is not None]
    if not parsed:
        return {"state": "never", "ageHours": None, "source": None, "at": None,
                "failed": [], "why": "摄入器没有留下任何运行记录 —— 库里的数字要么是"
                                     "手动灌的,要么根本没有。跑一次 console_ingest.py。"}
    failed = sorted(k for k, v, _t in parsed if v.get("ok") is False)

    parsed.sort(key=lambda x: x[2])
    src, row, at = parsed[0]
    age = (now - at).total_seconds() / 3600.0

    if failed:
        state = "failed"
        why = ("摄入器最近一次运行是失败的(" + "、".join(failed)
               + ")。这些数字停在上一次成功那一刻,而页面其余部分看不出异常。")
    elif age >= INGEST_LOSS_HOURS:
        state = "loss"
        why = (f"{src} 已经 {age / 24:.1f} 天没摄入。Windows 运行日志是滚动缓冲,"
               f"实测约 5-8 天覆盖一轮,所以这段历史很可能已经永久没了。")
    elif age >= INGEST_WARN_HOURS:
        state = "stale"
        why = (f"{src} 已经 {age:.0f} 小时没摄入,页面上的运行与健康数字都停在那时候。"
               f"再拖到 {INGEST_LOSS_HOURS / 24:.0f} 天就进丢失窗口了。")
    else:
        state = "ok"
        why = None

    return {"state": state, "ageHours": round(age, 2), "source": src,
            "at": row.get("at"), "failed": failed, "why": why}


def coverage(con) -> dict:
    """What the database actually covers, so the page can say it instead of implying it."""
    def one(sql, *a):
        r = con.execute(sql, a).fetchone()
        return dict(r) if r else {}
    h = one("SELECT MIN(day) a, MAX(day) b, COUNT(*) n FROM health_obs")
    r = one("SELECT MIN(day) a, MAX(day) b, COUNT(*) n FROM run_event")
    ing = con.execute("SELECT source, MAX(finished_at) t, ok FROM ingest_run "
                      "WHERE finished_at IS NOT NULL GROUP BY source").fetchall()
    return {
        "health": {"from": h.get("a"), "to": h.get("b"), "rows": h.get("n", 0)},
        "runs": {"from": r.get("a"), "to": r.get("b"), "rows": r.get("n", 0)},
        "lastIngest": {x["source"]: {"at": x["t"], "ok": bool(x["ok"])} for x in ing},
    }

