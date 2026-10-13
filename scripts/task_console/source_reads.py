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
