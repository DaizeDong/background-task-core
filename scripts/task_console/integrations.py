"""Console read adapters and their interfaces. Business producers own their data.

This registry describes shipped adapters, not executable plugins discovered from
records or paths. Writes remain behind the existing owner's guarded endpoints.
"""
from dataclasses import asdict, dataclass
import importlib

import source_reads


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
        return source_reads.read(self.id, self.resolve(local))

    def resolve(self, local=None):
        return local[self.reader] if self.module == 'server' and local is not None else getattr(importlib.import_module(self.module), self.reader)


ADAPTERS = (
    Adapter('work', '共享工作服务', 'work_status', 'read_configured', '/api/work', 'work',
            '待办、工作单、通知与执行回执', '由工作记录提供完成、执行、停止等可用操作', 'shared'),
    Adapter('tasks', '任务控制', 'server', 'build_payload', '/api/tasks', 'automations',
            '计划任务、运行状态与启动说明', '由控制器校验后启用、停用、运行、停止', 'shared'),
    Adapter('components', '组件观测', 'component_status', 'read_configured', '/api/components', 'pipelines',
            '声明的检查结果与来源目录', '查看检查证据', 'shared', ('tasks',)),
    Adapter('skills', '技能目录', 'maint', 'read_skills', '/api/skills', 'resources',
            '技能清单与加载预算', '归档、恢复、预览删除'),
    Adapter('plugins', 'Claude 插件清单', 'maint', 'read_plugins', '/api/plugins', 'resources',
            '客户端插件安装与启用状态', '启用、停用、预览卸载'),
    Adapter('memory', '记忆管理', 'memops', 'read', '/api/mem', 'resources',
            '记忆池状态与诊断', '查看及管理记忆'),
    Adapter('codex', 'Codex 本地资源', 'codexinfo', 'read', '/api/codex', 'storage',
            '本地会话与资源占用', '预览清理'),
    Adapter('llm', '模型调用账本', 'server', 'llm_overview', '/api/llmcall', 'llm',
            '用量、调用结果与当前路由', '查看调用、管理允许的配置'),
    Adapter('system', '系统资源', 'sysinfo', 'read', '/api/sys', 'storage',
            '磁盘与目录占用', '预览清理'),
    Adapter('conversations', '本地会话', 'convos', 'scan', '/api/convos', 'convos',
            '会话记录与来源', '查看记录、复制路径'),
    Adapter('repositories', '代码仓库', 'repos', 'scan', '/api/repos', 'repos',
            '仓库状态与变更', '查看差异及经确认的仓库操作'),
    Adapter('selfcheck', '配置自检', 'selfcheck', 'run', '/api/selfcheck', 'diagnostics',
            '读取路径与配置检查', '查看配置问题'),
)
ROUTES = {adapter.endpoint: adapter for adapter in ADAPTERS}


def read_configured():
    """Refresh shared evidence; other adapters report their last observed read.

    Old producer records prove neither current installation nor liveness. Keep
    these sources separate and explicitly leave their health unchecked.
    """
    by_id = {adapter.id: adapter for adapter in ADAPTERS}
    sections = source_reads.read_many({name: lambda name=name: by_id[name].resolve()()
                                       for name in ('work', 'components')})
    work, components = sections['work'], sections['components']
    observations = source_reads.boundary.snapshot()
    items = [{'id': 'console', 'label': '控制台框架', 'layer': 'core', 'view': 'overview',
              'provides': '页面、鉴权、读取隔离与操作反馈', 'actions': '导航、刷新、导出',
              'depends_on': [], 'connection': {'state': 'ready'}}]
    for adapter in ADAPTERS:
        row = asdict(adapter)
        row.pop('module'); row.pop('reader')
        row['connection'] = observations.get(adapter.id, {'state': 'unchecked'})
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
