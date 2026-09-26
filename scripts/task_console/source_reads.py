"""Bounded request-scoped reads, without TTLs or retained data payloads."""
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
import threading
import time


class ReadBoundary:
    def __init__(self):
        self._condition = threading.Condition()
        self._active = {}
        self._observations = {}
        self._revision = 0

    def invalidate(self):
        """A completed mutation makes every earlier in-flight snapshot obsolete."""
        with self._condition:
            self._revision += 1

    def snapshot(self):
        with self._condition:
            return deepcopy(self._observations)

    def read(self, source, reader, *, timeout=210):
        """Coalesce overlapping calls only; each caller gets its own result object.

        Readers own I/O deadlines. Waiting HTTP callers have a separate deadline
        and admission limit; a stuck reader cannot accumulate workers here.
        """
        for attempt in range(3):
            with self._condition:
                slot = self._active.get(source)
                leader = slot is None
                if leader:
                    if len(self._observations) >= 64 and source not in self._observations:
                        raise RuntimeError('source limit reached')
                    slot = {'future': Future(), 'revision': self._revision, 'callers': 0}
                    self._active[source] = slot
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
                            if slot['revision'] == self._revision:
                                self._observe(source, payload, time.monotonic() - started)
                        slot['future'].set_result(payload)
                    except BaseException as error:
                        with self._condition:
                            if slot['revision'] == self._revision:
                                self._observe(source, {'available': False, 'reason': type(error).__name__}, time.monotonic() - started)
                        slot['future'].set_exception(error)
                    finally:
                        with self._condition:
                            self._active.pop(source, None)
                payload = slot['future'].result(timeout=timeout)
                with self._condition:
                    if slot['revision'] == self._revision:
                        return deepcopy(payload)
            finally:
                with self._condition:
                    slot['callers'] -= 1
        raise RuntimeError('source changed during read; refresh again')

    def _observe(self, source, payload, duration):
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
