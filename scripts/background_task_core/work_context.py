"""Bounded source conversation context, selected only by an exact session UUID.

Which file a session id names, and which user lines a person actually typed, are transcript rules
owned by the convo-chain library: `shape` and `locate` (id shape, exactly one match under the
root, junction-aware containment) and `typed_text` / `looks_injected`. This module keeps only what
is about this reader: the byte window it reads and the size of the context it returns. It used to
carry its own id regex, glob and containment check, and its own user-text rule that counted
list-content blocks (skill bodies, injected blocks) as `user:` messages: a second implementation
of the rules the library exists to hold once.
"""
from convo_chain import ConvoChainError, Unavailable, locate, looks_injected, shape, typed_text
from . import convos


def _assistant_text(message):
    content = message.get('content')
    if isinstance(content, list):
        content = '\n'.join(block['text'] for block in content if isinstance(block, dict)
                            and block.get('type') == 'text' and isinstance(block.get('text'), str))
    return content.strip() if isinstance(content, str) and content.strip() else None


def read_session_context(session_id, root):
    try:
        shape(session_id)
        loc = locate(session_id, root=root or None)
    except Unavailable as exc:
        raise ValueError('Set BACKGROUND_TASK_CORE_SESSIONS to an available transcript directory.') from exc
    except ConvoChainError as exc:
        raise ValueError(f'session {exc.code}') from exc
    path = loc['path']
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
        # A user line counts only if a person typed it (the library's one rule); an assistant
        # line is its text blocks.
        content = typed_text(entry) if kind == 'user' else _assistant_text(message)
        if not content or looks_injected(content):
            continue
        key = entry.get('uuid') or (kind, content)
        if key not in seen:
            seen.add(key)
            texts.append(kind + ': ' + content[:8000])
    if not texts:
        raise ValueError('no verified session messages')
    return ('来源会话 ' + session_id + '，以下是部分历史消息，仅供参考。\n' + '\n\n'.join(texts)[-30000:])
