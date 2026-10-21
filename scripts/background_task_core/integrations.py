"""Producer integrations read by the console: the read boundary, linked work items, the shared work
service (schedule-reminder) and its actions, and the adapter registry behind /api/integrations.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path

# ---- source_reads ------------------------------------------------------------------------------
# Bounded request-scoped reads, without TTLs or retained data payloads.


class ReadBoundary:
    def __init__(self):
        self._condition = threading.Condition()
        self._active = {}
        self._observations = {}
        # Bumped after every mutating POST. A read joins an in-flight read only when both
        # began in the same generation, so the reread that follows a mutation never gets the
        # snapshot of a read that started before it.
        self._generation = 0
        self._observed_generation = {}

    def mutated(self):
        with self._condition:
            self._generation += 1

    def snapshot(self):
        with self._condition:
            return deepcopy(self._observations)

    def read(self, source, reader, *, timeout=210):
        """Coalesce overlapping calls only; each caller gets its own result object.

        A caller that arrives while a read of the same source and generation is running joins it.
        Readers own
        I/O deadlines. Waiting HTTP callers have a separate deadline and admission limit; a stuck
        reader cannot accumulate workers here.
        """
        with self._condition:
            key = (source, self._generation)
            slot = self._active.get(key)
            leader = slot is None
            if leader:
                if len(self._observations) >= 64 and source not in self._observations:
                    raise RuntimeError('source limit reached')
                slot = {'future': Future(), 'callers': 0, 'generation': key[1]}
                self._active[key] = slot
            if slot['callers'] >= 8:
                raise RuntimeError('source busy; retry after the current read')
            slot['callers'] += 1
            self._condition.notify_all()
        try:
            if leader:
                started = time.monotonic()
                try:
                    payload = reader()
                    if not isinstance(payload, dict):
                        raise ValueError('reader must return an object')
                    with self._condition:
                        self._observe(source, slot, payload, time.monotonic() - started)
                    slot['future'].set_result(payload)
                except BaseException as error:
                    with self._condition:
                        self._observe(source, slot, {'available': False, 'reason': type(error).__name__},
                                      time.monotonic() - started)
                    slot['future'].set_exception(error)
                finally:
                    with self._condition:
                        self._active.pop(key, None)
            return deepcopy(slot['future'].result(timeout=timeout))
        finally:
            with self._condition:
                slot['callers'] -= 1

    def _observe(self, source, slot, payload, duration):
        # An older read that finishes after a newer one must not overwrite the newer observation.
        if slot['generation'] < self._observed_generation.get(source, 0):
            return
        self._observed_generation[source] = slot['generation']
        previous = self._observations.get(source, {})
        failed = payload.get('available') is False or bool(payload.get('error'))
        reason = payload.get('reason') or payload.get('error')
        state = 'stale' if failed and previous.get('last_success') else 'unavailable' if failed else 'ready'
        if payload.get('reason_code') == 'not_configured' or reason == 'work_reader_not_configured':
            state = 'unconfigured'
        now = time.time()
        self._observations[source] = {'state': state, 'checked_at': now,
            'last_success': previous.get('last_success') if failed else now,
            'duration_ms': round(duration * 1000), 'reason': reason,
            'coverage': deepcopy(payload.get('coverage'))}


boundary = ReadBoundary()


def read(source, reader):
    return boundary.read(source, reader)


def mutated():
    """Call after a mutating request, whatever its outcome; later reads start fresh."""
    boundary.mutated()


def isolated(source, reader):
    """For grouped responses, an unexpected failure is one unavailable section."""
    try:
        return read(source, reader)
    except Exception as error:
        return {'available': False, 'reason_code': 'reader_failed',
                'reason': f'{source}: {type(error).__name__}'}


def read_many(readers):
    """Read independent sources together; release all workers with the request.

    Pass leaf readers only, so nested groups cannot multiply worker pools.
    Each source retains its existing coalescing, deadline and error boundary.
    """
    if not readers:
        return {}
    with ThreadPoolExecutor(max_workers=min(4, len(readers)), thread_name_prefix='console-read') as pool:
        pending = {source: pool.submit(isolated, source, reader) for source, reader in readers.items()}
        return {source: future.result() for source, future in pending.items()}


# ---- linked_items ------------------------------------------------------------------------------
# Fixed optional reminder read seam. No store import, discovery or DB creation.


def report(runtime, task_ids, bundle):
    supplied = bundle.get('linked_work_items')
    if isinstance(supplied, dict) and all(i in supplied for i in task_ids):
        if all(isinstance(supplied[i], list) for i in task_ids):
            return {'status': 'provided', 'items': {i: supplied[i] for i in task_ids}}
        return {'status': 'error', 'items': None, 'code': 'linked_reader_failed'}
    try:
        result = runtime.linked_work_items(task_ids)
        if (not isinstance(result, dict) or result.get('status') not in ('available', 'unavailable', 'error')
                or 'items' not in result or result.get('status') != 'available' and result['items'] is not None
                or result.get('status') == 'available' and (not isinstance(result['items'], dict)
                or set(result['items']) != set(task_ids) or any(not isinstance(v, list) for v in result['items'].values()))):
            raise ValueError('invalid linked result')
        return result
    except Exception:
        return {'status': 'error', 'items': None, 'code': 'linked_reader_failed'}


def read_linked_items(task_ids):
    path = os.environ.get('BACKGROUND_TASK_CORE_REMINDER_DB')
    if not path or not Path(path).is_absolute():
        return {'status': 'unavailable', 'items': None, 'code': 'linked_db_not_configured'}
    try:
        from reminder_linked_items import read_linked_items as read
    except ModuleNotFoundError as exc:
        if exc.name != 'reminder_linked_items':
            raise
        return {'status': 'unavailable', 'items': None, 'code': 'linked_reader_not_installed'}
    return read(task_ids, db_path=path)


# ---- work_status -------------------------------------------------------------------------------
# Consume the reminder owner's work-feed contract; no database access or discovery.


def owner_environment(env):
    child = dict(env)
    child['SCHEDULE_DB_PATH'] = env.get('BACKGROUND_TASK_CORE_REMINDER_DB', '')
    child['SCHEDULE_ACTION_WORKSPACE'] = env.get('BACKGROUND_TASK_CORE_ACTION_WORKSPACE', '')
    return child


def read_work(env=None):
    env = os.environ if env is None else env
    unavailable = {"schemaVersion": 1, "available": False, "items": [], "events": [], "sources": []}
    cli, database = env.get("BACKGROUND_TASK_CORE_REMINDER_CLI"), env.get("BACKGROUND_TASK_CORE_REMINDER_DB")
    if not cli or not database:
        return dict(unavailable, reason="work_reader_not_configured")
    if not Path(cli).is_absolute() or not Path(cli).is_file() or not Path(database).is_absolute():
        return dict(unavailable, reason="work_binding_invalid")
    try:
        result = subprocess.run([sys.executable, cli, "--db", database, "work-feed"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=20,
            env=owner_environment(env),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode or len(result.stdout) > 16_000_000:
            return dict(unavailable, reason="work_reader_failed")
        output = result.stdout.decode('utf-8') if isinstance(result.stdout, bytes) else result.stdout
        payload = json.loads(output)
        if (not isinstance(payload, dict) or type(payload.get("schemaVersion")) is not int or payload.get("schemaVersion") != 1
                or type(payload.get("available")) is not bool
                or any(not isinstance(payload.get(key), list) for key in ("items", "events", "sources"))
                or payload["available"] and not isinstance(payload.get("coverage"), dict)
                or any(not isinstance(row, dict) for key in ('events', 'sources') for row in payload[key])
                or any(not isinstance(item, dict) or not isinstance(item.get('id'), str)
                       or not isinstance(item.get('title'), str) or item.get('role') not in ('agent_work','tracked_item','signal')
                       or item.get('execution') is not None and not isinstance(item['execution'], dict)
                       or not _valid_actions(item.get('actions'))
                       for item in payload.get('items', []))):
            return dict(unavailable, reason="work_contract_invalid")
        runtime_ready = all(env.get(key) for key in ('BACKGROUND_TASK_CORE_RUNTIME_CONFIG', 'BACKGROUND_TASK_CORE_PRIVATE_ROOT',
                                                     'BACKGROUND_TASK_CORE_STATE_ROOT', 'BACKGROUND_TASK_CORE_VAULT_ROOT'))
        for item in payload.get('items', []):
            actions = item.get('actions')
            if not isinstance(actions, dict):
                continue
            for offer in actions.get('offers', []):
                # A linked task runs through the controller; an agent action only needs the queue tick.
                if (offer.get('kind') == 'task' and not runtime_ready
                        or offer.get('kind') == 'agent' and not env.get('BACKGROUND_TASK_CORE_AGENT_TASK_ID')):
                    offer.update(enabled=False, reason='执行服务尚未连接')
            if actions.get('offers') and not actions.get('current') and not any(offer.get('enabled') for offer in actions['offers']):
                actions.update(available=False, reason='执行服务尚未连接', offers=[])
        return payload
    except (OSError, ValueError, subprocess.SubprocessError):
        return dict(unavailable, reason="work_reader_failed")


def _valid_actions(actions):
    if actions is None:
        return True
    if not isinstance(actions, dict):
        return False
    offers = actions.get('offers', [])
    return (isinstance(offers, list) and all(isinstance(offer, dict) for offer in offers)
            and (actions.get('current') is None or isinstance(actions['current'], dict)))


# ---- work_actions ------------------------------------------------------------------------------
# Broker owner-issued work through the existing reminder CLI.
#
# A linked task runs through the Controller. An agent action only asks the scheduler to run the
# agent queue tick sooner; the tick owns execution.


class ActionError(ValueError):
    def __init__(self, code, *, uncertain=False):
        self.code, self.uncertain = code, uncertain
        super().__init__(code)


MESSAGES = {
    'stale_recommendation': '待办已更新，请刷新后再操作',
    'request_conflict': '这次请求与原记录不一致，请刷新后重试',
    'another_action_active': '这条待办已有工作在处理',
    'ERR_DEPENDENCY_UNMET': '前置待办尚未完成，请先处理前置事项',
    'action_schema_upgrade_required': '执行接口需要升级',
    'action_unavailable': '这项操作当前不可用，请刷新查看',
    'work_binding_invalid': '执行接口尚未连接',
    'queue_not_configured': '执行服务尚未连接',
    'runtime_not_configured': '任务控制器尚未连接',
    'owner_reply_unknown': '未收到提交确认，再次点击会核对原请求',
}


def validate(request):
    if (not isinstance(request, dict) or set(request) != {'item_id', 'action_id', 'revision', 'request_id'}
            or any(not isinstance(value, str) or not value or len(value) > 300 for value in request.values())
            or not re.fullmatch(r'[A-Za-z0-9_-]{8,120}', request['request_id'])):
        raise ActionError('invalid_action_request')


def invoke(verb, payload, env):
    cli, database = env.get('BACKGROUND_TASK_CORE_REMINDER_CLI'), env.get('BACKGROUND_TASK_CORE_REMINDER_DB')
    if (not cli or not database or not Path(cli).is_absolute() or not Path(cli).is_file()
            or not Path(database).is_absolute() or not Path(database).is_file()):
        raise ActionError('work_binding_invalid')
    try:
        reply = subprocess.run([sys.executable, cli, '--db', database, verb],
            input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True, encoding='utf-8',
            env=owner_environment(env), timeout=30,
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


def _wake_agent_tick(task_id):
    """Ask the scheduler to run the agent queue tick now, off the request thread.

    task_id is the configured owner/name ID; its name part is the scheduler task name.

    Best effort only: the tick's own trigger picks the queued action up anyway, so a failed or
    slow wakeup costs latency, never the action. Nothing waits on it and nothing reports it.
    """
    def run():
        try:
            subprocess.run(['schtasks', '/run', '/tn', task_id.rsplit('/', 1)[-1]], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=60,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.SubprocessError):
            pass
    threading.Thread(target=run, name='agent-tick-wakeup', daemon=True).start()


def submit(request, *, stop=False, env=None):
    env = os.environ if env is None else env
    try:
        validate(request)
        if stop:
            return invoke('work-action-stop', request, env)
        action = request['action_id']
        if action == 'agent' and not env.get('BACKGROUND_TASK_CORE_AGENT_TASK_ID'):
            raise ActionError('queue_not_configured')
        # A linked task runs through the controller; validate it before reserving the action.
        runtime = None if action in ('complete', 'agent') else controller.load_runtime(environ=env)
        reply = invoke('work-action', {'request': request, 'context': ''}, env)
        if action == 'agent' and reply.pop('wakeup', False):
            _wake_agent_tick(env['BACKGROUND_TASK_CORE_AGENT_TASK_ID'])
        dispatch = reply.pop('dispatch', None) if runtime is not None else None
        if dispatch:
            try:
                result = _run_task(dispatch['task_id'], runtime)
            except Exception:
                # A controller error may happen after scheduler acceptance. Never replay it.
                result = {'ok': False, 'status': 'cleanup_uncertain', 'message': '任务是否启动尚未确认'}
            reply = invoke('work-action-result', {'action_id': dispatch['action_id'], 'result': result}, env)
        return reply
    except (ActionError, controller.ContractError) as exc:
        code = exc.code
        return {'schemaVersion': 1, 'ok': False, 'code': code,
                'message': MESSAGES.get(code, '操作未能完成，请刷新查看记录'),
                'uncertain': getattr(exc, 'uncertain', False)}
    except Exception:
        return {'schemaVersion': 1, 'ok': False, 'code': 'operation_unavailable',
                'message': '操作暂时不可用，原请求已保留，请刷新查看', 'uncertain': True}


# ---- integrations ------------------------------------------------------------------------------
# Console read adapters and their interfaces. Business producers own their data.
#
# This registry describes shipped adapters, not executable plugins discovered from
# records or paths. Writes remain behind the existing owner's guarded endpoints.

@dataclass(frozen=True)
class Adapter:
    id: str
    label: str
    module: str
    reader: str
    endpoint: str
    view: str
    provides: str
    actions: str
    layer: str = 'adapter'
    depends_on: tuple = ()

    def read(self, local=None):
        # Resolve at call time: no process launch or external discovery at import.
        return read(self.id, lambda: self.resolve(local)())

    def resolve(self, local=None):
        if self.module == 'server' and local is not None:
            return local[self.reader]
        return getattr(importlib.import_module('.' + self.module, __package__), self.reader)


ADAPTERS = (
    Adapter('work', '共享工作服务', 'integrations', 'read_work', '/api/work', 'work',
            '待办、工作单、通知与执行回执', '由工作记录提供完成、执行、停止等可用操作', 'shared'),
    Adapter('tasks', '任务控制', 'server', 'build_payload', '/api/tasks', 'automations',
            '计划任务、运行状态与启动说明', '由控制器校验后启用、停用、运行、停止', 'shared'),
    Adapter('components', '组件观测', 'runs', 'read_configured', '/api/components', 'pipelines',
            '声明的检查结果与来源目录', '查看检查证据', 'shared', ('tasks',)),
    Adapter('skills', '技能目录', 'maint', 'read_skills', '/api/skills', 'resources',
            '技能清单与加载预算', '归档、恢复、预览删除'),
    Adapter('plugins', 'Claude 插件清单', 'maint', 'read_plugins', '/api/plugins', 'resources',
            '客户端插件安装与启用状态', '启用、停用、预览卸载'),
    Adapter('memory', '记忆管理', 'memops', 'read', '/api/mem', 'resources',
            '记忆池状态与诊断', '查看及管理记忆'),
    Adapter('codex', 'Codex 本地资源', 'codexinfo', 'read', '/api/codex', 'storage',
            '本地会话与资源占用', '预览清理'),
    Adapter('llm', '模型调用账本', 'component_routes', 'llm_overview', '/api/llmcall', 'llm',
            '用量、调用结果与当前路由', '查看调用、管理允许的配置'),
    Adapter('system', '系统资源', 'sysinfo', 'read', '/api/sys', 'storage',
            '磁盘与目录占用', '预览清理'),
    Adapter('conversations', '本地会话', 'convos', 'scan', '/api/convos', 'convos',
            '会话记录与存储位置', '搜索、连续加载、重命名与迁移'),
    Adapter('repositories', '代码仓库', 'repos', 'scan', '/api/repos', 'repos',
            '仓库状态与变更', '查看差异及经确认的仓库操作'),
    Adapter('selfcheck', '配置自检', 'selfcheck', 'run', '/api/selfcheck', 'diagnostics',
            '读取路径与配置检查', '查看配置问题'),
    Adapter('convo-chain', '对话链库 convo-chain', 'convo_chain', '__version__', '', 'convos',
            '对话链、导出、分叉与会话文件管理', '在会话页打开对话链；修改经受保护的 POST',
            'library', ('conversations',)),
)
ROUTES = {adapter.endpoint: adapter for adapter in ADAPTERS if adapter.endpoint}


def read_configured():
    """Refresh shared evidence; other adapters report their last observed read.

    Old producer records prove neither current installation nor liveness. Keep
    these sources separate and explicitly leave their health unchecked.
    """
    by_id = {adapter.id: adapter for adapter in ADAPTERS}
    sections = read_many({name: lambda name=name: by_id[name].resolve()()
                                       for name in ('work', 'components')})
    work, components = sections['work'], sections['components']
    observations = boundary.snapshot()
    items = [{'id': 'console', 'label': '控制台框架', 'layer': 'core', 'view': 'overview',
              'provides': '页面、鉴权、读取隔离与操作反馈', 'actions': '导航、刷新、导出',
              'depends_on': [], 'connection': {'state': 'ready'}}]
    for adapter in ADAPTERS:
        row = asdict(adapter)
        row.pop('module'); row.pop('reader')
        row['connection'] = observations.get(adapter.id, {'state': 'unchecked'})
        if adapter.layer == 'library':
            try:
                version = getattr(importlib.import_module(adapter.module), adapter.reader)
                row['connection'] = {'state': 'ready', 'reason': f'Installed {version}'}
            except ImportError as error:
                row['connection'] = {'state': 'unavailable', 'reason': str(error)}
        if adapter.id == 'components':
            row['coverage'] = components.get('coverage')
            row['catalog'] = {key: components.get('catalog', {}).get(key) for key in ('available', 'reason', 'coverage')}
        items.append(row)
    sources = work.get('sources', []) if work.get('available') else []
    origins = {}
    for source in sources:
        name = source.get('source')
        if not isinstance(name, str) or not name:
            continue
        group = origins.setdefault(name, {'count': 0, 'signal': False})
        count = source.get('count')
        if type(count) is int and count >= 0 and group['count'] is not None:
            group['count'] += count
        else:
            group['count'] = None
        group['signal'] = group['signal'] or source.get('role') == 'signal'
    for name, group in sorted(origins.items()):
        signal = group['signal']
        items.append({'id': 'producer:' + name, 'label': name, 'layer': 'business' if signal else 'origin',
            'provides': '通知与线索来源' if signal else '待办或工作单的来源标签', 'record_count': group['count'],
            'actions': '查看该来源的工作记录', 'view': 'work', 'source': name, 'depends_on': ['work'],
            'connection': {'state': 'unchecked', 'reason': '记录可读不代表插件正在运行；未接入独立健康证据' if signal else '记录的来源标签，不代表一个已安装插件'}})
    return {'schemaVersion': 1, 'available': True, 'items': items,
            'coverage': {'work_sources': 'readable' if work.get('available') else 'unavailable',
                         'producer_health': 'unchecked'},
            'work_reason': work.get('reason'), 'observation_scope': 'last_read_in_this_server'}


# Imported last: these modules import this one while it loads.
from . import lifecycle as controller
