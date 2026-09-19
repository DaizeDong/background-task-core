"""Fixed optional reminder read seam. No store import, discovery or DB creation."""
import os
from pathlib import Path


def report(runtime, task_ids, bundle):
    supplied = bundle.get('linked_work_items')
    if isinstance(supplied, dict) and all(i in supplied for i in task_ids):
        if all(isinstance(supplied[i], list) for i in task_ids):
            return {'status': 'provided', 'items': {i: supplied[i] for i in task_ids}}
        return {'status': 'error', 'items': None, 'code': 'linked_reader_failed'}
    reader = getattr(runtime, 'linked_work_items', None)
    if reader is None:
        return {'status': 'unavailable', 'items': None, 'code': 'linked_reader_not_configured'}
    try:
        result = reader(task_ids)
        if (not isinstance(result, dict) or result.get('status') not in ('available', 'unavailable', 'error')
                or 'items' not in result or result.get('status') != 'available' and result['items'] is not None
                or result.get('status') == 'available' and (not isinstance(result['items'], dict)
                or set(result['items']) != set(task_ids) or any(not isinstance(v, list) for v in result['items'].values()))):
            raise ValueError('invalid linked result')
        return result
    except Exception:
        return {'status': 'error', 'items': None, 'code': 'linked_reader_failed'}


def read_linked_items(task_ids):
    path = os.environ.get('TASK_CONSOLE_REMINDER_DB')
    if not path or not Path(path).is_absolute():
        return {'status': 'unavailable', 'items': None, 'code': 'linked_db_not_configured'}
    try:
        from reminder_linked_items import read_linked_items as read
    except ModuleNotFoundError as exc:
        if exc.name != 'reminder_linked_items':
            raise
        return {'status': 'unavailable', 'items': None, 'code': 'linked_reader_not_installed'}
    return read(task_ids, db_path=path)
