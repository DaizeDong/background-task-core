"""Task correlation only. COM observation lives in the adjacent PowerShell owner."""
import json
import os
from pathlib import Path
import re
import subprocess


def retained_run_id(explicit=None, *, environ=None):
    env = os.environ if environ is None else environ
    values = [v for v in (explicit, env.get('TASK_RUN_ID'), env.get('SCHEDULE_RUN_ID')) if v is not None and v != '']
    if any(not isinstance(v, str) or v != v.strip() or len(v) > 512 or
           any(ord(c) < 32 for c in v) for v in values):
        raise ValueError('task-context: malformed run identity')
    if len(set(values)) > 1:
        raise ValueError('task-context: conflicting run identities')
    return values[0] if values else None


def resolve_run_id(task_name, *, explicit=None, dry_run=False, scheduled=False,
                   environ=None, process=None):
    env = os.environ if environ is None else environ
    run_id = retained_run_id(explicit, environ=env)
    marker = env.get('TASK_SCHEDULER_NAME')
    if marker and marker != task_name:
        raise ValueError('task-context: inherited Scheduler task name mismatch')
    if run_id is None and (scheduled or marker) and not dry_run:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', task_name):
            raise ValueError('task-context: exact root task name required')
        ps = str(Path(env.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
        result = (process or subprocess.run)(
            [ps, '-NoProfile', '-NonInteractive', '-File', str(Path(__file__).with_suffix('.ps1')),
             '-QueryTaskName', task_name], capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), env=dict(env))
        if result.returncode:
            raise ValueError('task-context: Scheduler identity observation failed')
        context = json.loads(result.stdout)
        if context.get('task_name') != task_name or not re.fullmatch(
                r'scheduler:' + re.escape(task_name) + r':\{[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\}',
                context.get('run_id', '')):
            raise ValueError('task-context: invalid Scheduler identity response')
        run_id = context['run_id']
    if run_id is None and not dry_run:
        raise ValueError('task-context: stable run ID required before business work; use --run-id for manual operation')
    if run_id:
        env['TASK_RUN_ID'] = env['SCHEDULE_RUN_ID'] = run_id
    return run_id
