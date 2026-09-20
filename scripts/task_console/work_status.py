"""Consume the reminder owner's work-feed contract; no database access or discovery."""
import json
import os
from pathlib import Path
import subprocess
import sys


def read_configured(env=None):
    env = os.environ if env is None else env
    unavailable = {"schemaVersion": 1, "available": False, "items": [], "events": [], "sources": []}
    cli, database = env.get("TASK_CONSOLE_REMINDER_CLI"), env.get("TASK_CONSOLE_REMINDER_DB")
    if not cli or not database:
        return dict(unavailable, reason="work_reader_not_configured")
    if not Path(cli).is_absolute() or not Path(cli).is_file() or not Path(database).is_absolute():
        return dict(unavailable, reason="work_binding_invalid")
    try:
        result = subprocess.run([sys.executable, cli, "--db", database, "work-feed"],
            capture_output=True, text=True, encoding="utf-8", timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode or len(result.stdout) > 16_000_000:
            return dict(unavailable, reason="work_reader_failed")
        payload = json.loads(result.stdout)
        if (not isinstance(payload, dict) or type(payload.get("schemaVersion")) is not int or payload.get("schemaVersion") != 1
                or type(payload.get("available")) is not bool
                or any(not isinstance(payload.get(key), list) for key in ("items", "events", "sources"))
                or payload["available"] and not isinstance(payload.get("coverage"), dict)
                or any(not isinstance(item, dict) or not isinstance(item.get('id'), str)
                       or not isinstance(item.get('title'), str) or item.get('role') not in ('agent_work','tracked_item','signal')
                       or item.get('execution') is not None and not isinstance(item['execution'], dict)
                       for item in payload.get('items', []))):
            return dict(unavailable, reason="work_contract_invalid")
        return payload
    except (OSError, ValueError, subprocess.SubprocessError):
        return dict(unavailable, reason="work_reader_failed")
