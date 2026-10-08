"""Read-only native environment doctor; no server, ingestion, model or Scheduler action."""
import argparse
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/task_console"))
import console_store


def doctor():
    path, state = console_store.resolve_db()
    problems = []
    checks = {}
    if state:
        problems.append(state.message)
    else:
        checks["private_versioned_database_destination"] = True
        if not path.is_file():
            problems.append("TASK_CONSOLE_DB database is uninitialized; configure ingestion separately")
        elif any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-journal")):
            problems.append("database has active transaction sidecars; retry after the writer closes")
        else:
            try:
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
                    console_store.validate_read_schema(connection)
                checks["database_schema"] = True
            except sqlite3.Error:
                problems.append("database cannot be read with the expected schema")
    checks["windows"] = os.name == "nt"
    if not checks["windows"]:
        problems.append("server requires Windows")
    for name in ("yaml", "win32evtlog", "convo_chain", "llmcall"):
        checks[name] = importlib.util.find_spec(name) is not None
        if not checks[name]:
            problems.append("missing runtime import: " + name)
    selected_root = path.parents[2] if path else None
    return {"ready": not problems, "resolved_root": str(selected_root) if selected_root else None,
            "scope": "local-server-and-history", "checks": checks, "problems": problems,
            "work_records": "configured" if all(os.environ.get(name) for name in
                ("TASK_CONSOLE_REMINDER_CLI", "TASK_CONSOLE_REMINDER_DB")) else "not_configured",
            "live_scheduler_verified": False, "ingestion_verified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = doctor()
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        print("RESOLVED: " + str(result["resolved_root"]))
        print("READY" if result["ready"] else "NOT READY")
        for problem in result["problems"]:
            print("- " + problem)
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
