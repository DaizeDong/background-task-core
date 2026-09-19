"""Legacy freshness response adapter around the shared pure health evaluator."""
from __future__ import annotations
import os
try:
    from . import health
except ImportError:
    import health

RC_NOT_RUN, RC_RUNNING = health.RC_NOT_RUN, health.RC_RUNNING
UP, GRACE, DOWN, NEVER, RUNNING, PAUSED, UNKNOWN = (
    "up", "grace", "down", "never", "running", "paused", "unknown")
OK_CODE_KEYS = health.OK_CODE_KEYS
declared_ok_codes = health.declared_ok_codes
_ok_codes = health.ok_codes
worst_of = health.worst_of

def newest_mtime(path: str, stat=os.stat, listdir=os.listdir, isdir=os.path.isdir):
    """产物可以是一个文件,也可以是一个目录(比如一个按年份分目录的归档)。
    目录取里面最新那个文件的 mtime;空目录返回 None,**不是 0**:0 会被读成 1970 年,
    那是一个看起来很确定的错误答案。

    读不到就返回 None 并附上原因。返回 (mtime, reason)。"""
    p = os.path.expanduser(path)
    try:
        if isdir(p):
            best = None
            for name in listdir(p):
                try:
                    m = stat(os.path.join(p, name)).st_mtime
                except OSError:
                    continue
                if best is None or m > best:
                    best = m
            return (best, None) if best is not None else (None, "产物目录是空的")
        return stat(p).st_mtime, None
    except OSError as e:
        return None, f"读不到产物: {e.__class__.__name__}"


def evaluate(decls, rows, now, mtime_of=None):
    """Read only explicitly declared artifacts; preserve the old page fields."""
    mtime_of = mtime_of or newest_mtime
    out = []
    for index, raw_decl in enumerate(decls):
        decl = raw_decl if isinstance(raw_decl, dict) else {}
        name = decl.get("name")
        row = rows.get(name) or {}
        artifacts = {}
        for check in [decl, *(decl.get("checks") or [])]:
            path = check.get("artifact") or (check.get("legacy") or {}).get("artifact")
            if path:
                mtime, reason = mtime_of(path)
                artifacts[path] = {"mtime": mtime, "reason": reason}
        obs = dict(row.get("observations") or {}, scheduler=row, artifacts=artifacts)
        result = health.evaluate_task(decl, obs, now)
        if not name:
            result.update(state=UNKNOWN, verdict="unknown", reasons=["missing task name"],
                          reason_codes=["invalid_declaration"])
        out.append(dict(result, name=name or f"[invalid declaration {index + 1}]",
                        check=decl.get("check"), label=decl.get("label") or name,
                        last_run=row.get("last_run"), next_run=row.get("next_run"),
                        last_rc=row.get("last_rc"), missed_runs=row.get("missed_runs"),
                        artifact=decl.get("artifact"), artifact_max_age_hours=decl.get("artifact_max_age_hours"),
                        cannot_prove_success=bool(decl.get("artifact_cannot_prove_success"))))
    counts = {}
    for item in out:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
    judged = sum(value for key, value in counts.items() if key != UNKNOWN)
    total = len(out)
    cov = health.coverage(judged, total)
    return {"schemaVersion": health.SCHEMA_VERSION,
            "tasks": sorted(out, key=lambda t: (-health.SEVERITY.get(t["state"], 2), t["name"])),
            "summary": {"total": total, "counts": counts, "judged": judged,
                        "coverage": cov["ratio"], "coverageVerdict": cov,
                        "bad": counts.get(DOWN, 0) + counts.get(NEVER, 0),
                        "attention": sum(counts.get(k, 0) for k in (DOWN, NEVER, UNKNOWN)),
                        "attentionStates": [DOWN, NEVER, UNKNOWN]}}
