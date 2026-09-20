"""Broker owner-issued work through the existing reminder CLI and Controller."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import work_status
from task_console import controller


class ActionError(ValueError):
    def __init__(self, code, *, uncertain=False):
        self.code, self.uncertain = code, uncertain
        super().__init__(code)


MESSAGES = {
    'stale_recommendation': '待办已更新，请刷新后再操作',
    'request_conflict': '这次请求与原记录不一致，请刷新后重试',
    'another_action_active': '这条待办已有工作在处理',
    'action_schema_upgrade_required': '执行接口需要升级',
    'action_unavailable': '这项操作当前不可用，请刷新查看',
    'work_binding_invalid': '执行接口尚未连接',
    'queue_not_configured': '执行服务尚未连接',
    'runtime_not_configured': '任务控制器尚未连接',
    'session_context_unavailable': '暂时读不到关联的原对话，请恢复来源后重试',
    'owner_reply_unknown': '未收到提交确认，再次点击会核对原请求',
    'read_only': '只读预览，无法执行操作',
}


def validate(request):
    if (not isinstance(request, dict) or set(request) != {'item_id', 'action_id', 'revision', 'request_id'}
            or any(not isinstance(value, str) or not value or len(value) > 300 for value in request.values())
            or not re.fullmatch(r'[A-Za-z0-9_-]{8,120}', request['request_id'])):
        raise ActionError('invalid_action_request')


def invoke(verb, payload, env):
    cli, database = env.get('TASK_CONSOLE_REMINDER_CLI'), env.get('TASK_CONSOLE_REMINDER_DB')
    if (not cli or not database or not Path(cli).is_absolute() or not Path(cli).is_file()
            or not Path(database).is_absolute() or not Path(database).is_file()):
        raise ActionError('work_binding_invalid')
    try:
        reply = subprocess.run([sys.executable, cli, '--db', database, verb],
            input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True, encoding='utf-8',
            env=work_status.owner_environment(env), timeout=30,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        raw = reply.stderr if reply.returncode else reply.stdout
        if len(raw) > 131072:
            raise ActionError('owner_reply_unknown', uncertain=True)
        result = json.loads(raw)
        if reply.returncode:
            code = result.get('error_code') if isinstance(result, dict) else None
            raise ActionError(code if isinstance(code, str) and re.fullmatch('[a-zA-Z_]+', code) else 'owner_reply_unknown', uncertain=code == 'ERR_INTERNAL')
        if (not isinstance(result, dict) or result.get('schemaVersion') != 1 or result.get('ok') is not True
                or not isinstance(result.get('action'), dict)):
            raise ActionError('owner_reply_unknown', uncertain=True)
        return result
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        if isinstance(exc, ActionError):
            raise
        raise ActionError('owner_reply_unknown', uncertain=True) from exc


def _run_task(task_id, runtime):
    specs = controller.registration._compile(runtime.load())['task_specs']
    selected = next((row for row in specs if row['task_id'] == task_id), None)
    if selected is None:
        raise ActionError('task_not_registered')
    return controller.Controller(runtime).action(selected['name'], 'run')


def _context(item_id, env):
    feed = work_status.read_configured(env)
    if not feed.get('available'):
        raise ActionError('work_binding_invalid')
    item = next((row for row in feed['items'] if row['id'] == item_id), None)
    if not item:
        raise ActionError('item_not_found')
    links = item.get('actions', {}).get('links', [])
    session = next((link['id'] for link in links if link.get('kind') == 'session'), None)
    if not session:
        return ''
    from work_context import read_session_context
    try:
        return read_session_context(session, env.get('TASK_CONSOLE_SESSIONS'))
    except (OSError, ValueError) as exc:
        raise ActionError('session_context_unavailable') from exc


def context_view(item_id, env=None):
    try:
        value = _context(item_id, os.environ if env is None else env)
        return {'available': bool(value), 'text': value, 'reason': '' if value else '没有记录准确的原对话关联'}
    except ActionError as exc:
        return {'available': False, 'reason': MESSAGES.get(exc.code, '原对话暂时不可用')}


def submit(request, *, stop=False, env=None):
    env = os.environ if env is None else env
    try:
        validate(request)
        if env.get('TASK_CONSOLE_READ_ONLY') == '1':
            raise ActionError('read_only')
        if stop:
            return invoke('work-action-stop', request, env)
        # Validate the configured executor before reserving a new action.
        runtime = controller.load_runtime(environ=env)
        if request['action_id'] == 'agent' and not env.get('TASK_CONSOLE_AGENT_TASK_ID'):
            raise ActionError('queue_not_configured')
        context = _context(request['item_id'], env) if request['action_id'] == 'agent' else ''
        reply = invoke('work-action', {'request': request, 'context': context}, env)
        dispatch = reply.pop('dispatch', None)
        if dispatch:
            try:
                result = _run_task(dispatch['task_id'], runtime)
            except Exception:
                # A controller error may happen after scheduler acceptance. Never replay it.
                result = {'ok': False, 'status': 'cleanup_uncertain', 'message': '任务是否启动尚未确认'}
            reply = invoke('work-action-result', {'action_id': dispatch['action_id'], 'result': result}, env)
        elif reply.get('wakeup'):
            try:
                result = _run_task(env['TASK_CONSOLE_AGENT_TASK_ID'], runtime)
                reply['wakeup'] = result.get('ok') is True
            except Exception:
                reply['wakeup'] = False
        return reply
    except (ActionError, controller.ContractError) as exc:
        code = exc.code
        return {'schemaVersion': 1, 'ok': False, 'code': code,
                'message': MESSAGES.get(code, '操作未能完成，请刷新查看记录'),
                'uncertain': getattr(exc, 'uncertain', False)}
    except Exception:
        return {'schemaVersion': 1, 'ok': False, 'code': 'operation_unavailable',
                'message': '操作暂时不可用，原请求已保留，请刷新查看', 'uncertain': True}
