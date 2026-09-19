"""Read and edit literal PowerShell TaskNames arrays without evaluating a script.

The legacy parse_names returns (None, reason) for absent, malformed and empty
arrays. The strict reader distinguishes an empty array from a failed read so
retirement remains idempotent after removing the last name.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


class AllowlistError(ValueError):
    """The script has no unambiguous literal TaskNames assignment."""


@dataclass(frozen=True)
class _Literal:
    value: str
    start: int
    end: int


def _comment_end(text: str, pos: int) -> int:
    if text.startswith("<#", pos):
        depth, pos = 1, pos + 2
        while pos < len(text) and depth:
            if text.startswith("<#", pos):
                depth += 1
                pos += 2
            elif text.startswith("#>", pos):
                depth -= 1
                pos += 2
            else:
                pos += 1
        if depth:
            raise AllowlistError("Unterminated block comment")
        return pos
    end = text.find("\n", pos)
    return len(text) if end < 0 else end


def _string(text: str, pos: int, *, literal: bool) -> _Literal:
    start, quote, value = pos, text[pos], []
    pos += 1
    escapes = {"0": "\0", "a": "\a", "b": "\b", "f": "\f", "n": "\n",
               "r": "\r", "t": "\t", "v": "\v"}
    while pos < len(text):
        char = text[pos]
        if char == quote:
            if pos + 1 < len(text) and text[pos + 1] == quote:
                value.append(quote)
                pos += 2
                continue
            return _Literal("".join(value), start, pos + 1)
        if quote == '"' and char == "`":
            pos += 1
            if pos >= len(text):
                break
            char = escapes.get(text[pos], text[pos])
        elif quote == '"' and char == "$" and literal:
            raise AllowlistError("Interpolation is not a literal task name")
        value.append(char)
        pos += 1
    raise AllowlistError("Unterminated string")


def _trivia(text: str, pos: int) -> int:
    while pos < len(text):
        if text[pos].isspace() or text[pos] == "\ufeff":
            pos += 1
        elif text.startswith("`\r\n", pos):
            pos += 3
        elif text.startswith("`\n", pos):
            pos += 2
        elif text[pos] == "#" or text.startswith("<#", pos):
            pos = _comment_end(text, pos)
        else:
            break
    return pos


_TASK_VARIABLE = re.compile(
    r"\$(?:\{(?:(?:script|global|local|private):)?TaskNames\}|"
    r"(?:(?:script|global|local|private):)?TaskNames\b)", re.I)


def _mutates_task_names(text: str, pos: int) -> bool:
    prefix = text[pos:pos + 2] in ("++", "--")
    start = _trivia(text, pos + 2) if prefix else pos
    variable = _TASK_VARIABLE.match(text, start)
    if variable is None:
        return False
    if prefix:
        return True
    end = _trivia(text, variable.end())
    # Follow index expressions without interpreting them. Strings and comments
    # may themselves contain brackets, so a flat bracket regex is insufficient.
    while end < len(text) and text[end] == "[":
        depth, end = 1, end + 1
        while end < len(text) and depth:
            end = _trivia(text, end)
            if end >= len(text):
                break
            if text[end] in "\"'":
                end = _string(text, end, literal=False).end
                continue
            if text[end] == "[":
                depth += 1
            elif text[end] == "]":
                depth -= 1
            end += 1
        if depth:
            raise AllowlistError("Unterminated TaskNames index")
        end = _trivia(text, end)
    return re.match(r"(?:[+*/%\-]?=|\+\+|--)", text[end:]) is not None


def _parse(text: str):
    assignment = re.compile(r"\$TaskNames\b\s*=\s*@\(", re.I)
    found = None
    pos = 0
    while pos < len(text):
        if text[pos:pos + 2] in ("@'", '@"'):
            quote = text[pos + 1]
            end = re.search(r"(?m)^" + re.escape(quote + "@"), text[pos + 2:])
            if not end:
                raise AllowlistError("Unterminated here-string")
            pos += 2 + end.end()
            continue
        if text[pos] == "#" or text.startswith("<#", pos):
            pos = _comment_end(text, pos)
            continue
        if text[pos] in "\"'":
            pos = _string(text, pos, literal=False).end
            continue
        match = assignment.match(text, pos)
        if not match:
            if _mutates_task_names(text, pos):
                raise AllowlistError("Nonliteral TaskNames assignment or mutation")
            pos += 1
            continue
        if found is not None:
            raise AllowlistError("Multiple TaskNames assignments")
        start, body_start = pos, match.end()
        pos, names, commas = body_start, [], []
        need_value = True
        while True:
            previous = pos
            pos = _trivia(text, pos)
            if pos >= len(text):
                raise AllowlistError("Unterminated TaskNames array")
            if text[pos] == ")":
                if need_value and names:
                    raise AllowlistError("Trailing comma in TaskNames")
                break
            if not need_value:
                if text[pos] == ",":
                    commas.append((len(names) - 1, pos))
                    pos += 1
                    need_value = True
                    continue
                if "\n" not in text[previous:pos]:
                    raise AllowlistError("Expected a comma or newline between names")
            if text[pos] not in "\"'":
                raise AllowlistError("TaskNames accepts quoted literals only")
            name = _string(text, pos, literal=True)
            if not name.value or any(ord(c) < 32 for c in name.value):
                raise AllowlistError("Empty or control-character task name")
            names.append(name)
            pos = name.end
            need_value = False
        end = pos + 1
        tail = _trivia(text, end)
        if tail < len(text) and "\n" not in text[end:tail] and text[tail] != ";":
            raise AllowlistError("Executable expression after TaskNames array")
        found = start, body_start, pos, end, names, commas
        pos = end
    if found is None:
        raise AllowlistError("找不到 $TaskNames = @( ... ) 这个块")
    return found


def literal_names(text: str) -> list[str]:
    """Strict reader: preserve order/duplicates; raise on an unchecked source."""
    return [name.value for name in _parse(text)[4]]


def parse_names(text: str) -> tuple[set[str] | None, str | None]:
    try:
        names = set(literal_names(text))
    except AllowlistError as exc:
        return None, str(exc)
    if not names:
        return None, "$TaskNames 里解析出 0 个名字,判为未检查而不是全部缺失"
    return names, None


def remove_name(text: str, name: str) -> tuple[str, bool]:
    """Remove exact literals and their separators, preserving comments."""
    try:
        _, _, _, _, names, commas = _parse(text)
    except AllowlistError:
        return text, False
    removed = {i for i, token in enumerate(names) if token.value == name}
    if not removed:
        return text, False
    kept = set(range(len(names))) - removed
    spans = [(token.start, token.end) for i, token in enumerate(names) if i in removed]
    spans.extend((pos, pos + 1) for i, pos in commas
                 if i not in kept or not any(j > i for j in kept))
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + text[end:]
    return text, True


def render_names(names: list[str]) -> str:
    """Render data as single-quoted literals; never interpolate PowerShell."""
    text = "$TaskNames = @(\n" + ",\n".join(
        "    '" + name.replace("'", "''") + "'" for name in names) + "\n)\n"
    literal_names(text)
    return text


def find_block(text: str) -> re.Match | None:
    """Compatibility match with groups (assignment, body, closing parenthesis)."""
    try:
        start, body, close, end, _, _ = _parse(text)
    except AllowlistError:
        return None
    return re.compile(r"(.{%d})(.{%d})(.)" % (body - start, close - body), re.S).match(text, start, end)


class _BlockPattern:
    def search(self, text: str) -> re.Match | None:
        return find_block(text)


# Compatibility names for legacy callers. Production readers use literal_names.
BLOCK = INLINE = _BlockPattern()
_NAME = re.compile(r"'([^']+)'")
