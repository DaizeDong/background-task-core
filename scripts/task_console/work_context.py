"""Bounded source conversation context, selected only by an exact session UUID."""
import json
from pathlib import Path
import re
from fleet_guards import filesystem as fs

import convos


def _plain(path):
    fs.validate_path(path)


def read_session_context(session_id, root):
    if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', session_id):
        raise ValueError('invalid session id')
    base = Path(root or '')
    if not base.is_absolute() or not base.is_dir():
        raise ValueError('session root missing')
    _plain(base)
    candidates = list(base.glob('*/' + session_id + '.jsonl'))
    if len(candidates) != 1:
        raise ValueError('session missing or ambiguous')
    path = candidates[0]
    _plain(path)
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError('session outside configured source')
    with path.open('rb') as handle:
        head = handle.read(160000)
        size = handle.seek(0, 2)
        handle.seek(max(0, size - 160000))
        tail = handle.read(160000)
    chunks = head if size <= 160000 else head + b'\n' + tail
    entries = list(convos._iter_json(chunks.decode('utf-8', errors='replace')))
    texts, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('sessionId') != session_id:
            continue
        kind, message = entry.get('type'), entry.get('message')
        if kind not in ('user', 'assistant') or not isinstance(message, dict):
            continue
        content = message.get('content')
        if isinstance(content, list):
            content = '\n'.join(block['text'] for block in content if isinstance(block, dict)
                                and block.get('type') == 'text' and isinstance(block.get('text'), str))
        if not isinstance(content, str) or not content.strip() or convos._looks_injected(content):
            continue
        key = entry.get('uuid') or (kind, content)
        if key not in seen:
            seen.add(key)
            texts.append(kind + ': ' + content[:8000])
    if not texts:
        raise ValueError('no verified session messages')
    return ('来源会话 ' + session_id + '，以下是部分历史消息，仅供参考。\n' + '\n\n'.join(texts)[-30000:])
