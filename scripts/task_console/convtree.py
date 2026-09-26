"""对话链:把一份会话转录还原成「真正发生过的那条对话」,并允许从任意节点导出或分叉。

`convos.py` 只读每份转录的头和尾,回答「有哪些对话」。这里回答的是另一个问题:
**一场对话从头到这个节点,模型看到的到底是什么。** 那需要读完整份文件,而且只靠
`parentUuid` 往上走是走不对的,原因有三个,每一个都在真实转录上量到过:

  1. 一次助手回复被拆成每个内容块一行,并行工具调用会让一个节点有两个孩子
     (同一 message.id 的下一块,和第一块的工具结果)。那不是分支,是一次回复。
     只沿 parentUuid 走会跳过另一块和它的结果。
  2. 压缩边界那一行的 parentUuid 是 null,所以一条朴素的上溯到边界就停了,
     前面整段历史不在链上。它的 logicalParentUuid **多数时候指向边界之后写下的行**,
     照着走会绕回边界自己,所以要有环检测和两级退路。
  3. 被保留的消息留在边界**之前**的原位,只由边界的 compactMetadata 把它们接进新上下文。
     分叉时要照 Claude Code 自己加载时的重链方式把它们拼回摘要后面。

这个模块只建**索引**(每行的字节偏移和一条瘦记录),不把正文留在内存里:
一份几百兆的转录,正文要看哪一行就按偏移回去读哪一行。

两套编号并存,名字各自说清:`lineIndex` 是文件里的**物理行号**(从 0 起,空行和坏行
都占号),`byteOffset` / `byteLength` 是那一行在文件里的字节位置。链条目在内部列表里的
下标从不下发:在一份有空行或坏行的转录上它和物理行号对不上,而只用干净 fixture 的测试
永远看不出来。

它读的根目录只有一个:`TASK_CONSOLE_SESSIONS`。没设就是「未检查」,不猜默认位置。
客户端给的只有会话 id 和节点 uuid,两者都先按形状拒绝,才碰文件系统。
写只有一处(`fork`),写的是一份**新**文件,独占创建,源文件只以只读方式打开。
"""

from __future__ import annotations

import collections
import gzip
import json
import os
import re
import sys
import threading
import time
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

from convos import _looks_injected, _typed_text
from maint import Refused

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
AGENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

PREVIEW = 200
FIELD_MAX = 200_000
RAW_MAX = 2 << 20          # 超过这个大小的行永远不整行下发
RESULT_MD_MAX = 2000
BIG_FORK_BYTES = 1_500_000
META_MAX = 64 << 10        # 子代理 meta 是几行 JSON;大过这个的不是 meta
RESPONSE_MAX = 2 << 20     # 一个节点的工具输入和结果加起来最多下发这么多
CACHE_SLOTS = 3


class Unavailable(Exception):
    """根目录没设或不存在。它不是「请求错了」,是「这一栏没检查」,所以和 Refused 分开。"""


# ---------------------------------------------------------------- 定位

def _base(root: str | None) -> Path:
    raw = root if root is not None else os.environ.get("TASK_CONSOLE_SESSIONS")
    if not raw:
        raise Unavailable("没有设 TASK_CONSOLE_SESSIONS,对话链读不了,这一栏是「未检查」。")
    base = Path(os.path.expanduser(raw))
    # 相对路径会按「服务器是在哪个目录下启动的」去解析。从一份仓库的检出里启动,
    # 分叉写出的真实对话就落进那份检出里。所以只认绝对路径,不替人猜它相对于谁。
    if not base.is_absolute():
        raise Unavailable(f"会话根目录必须是绝对路径,拿到的是 {raw!r}")
    if not base.is_dir():
        raise Unavailable(f"目录不存在: {base}")
    return base


def _inside(base: Path, p: Path) -> bool:
    try:
        b = os.path.normcase(os.path.realpath(base))
        f = os.path.normcase(os.path.realpath(p))
        return os.path.commonpath([b, f]) == b and f != b
    except (OSError, ValueError):
        return False


class _Containment:
    """列子代理清单时用的归属检查:结论和 `_inside` 一样,但每个目录只解析一次。

    一个会话底下有上千个子代理文件是常态(实测 1345 个)。逐个文件两次 realpath,
    Windows 上每次都是一趟 GetFinalPathNameByHandle,光这一步就是 3.9 秒,
    比给整份 80 MB 转录建索引还慢一倍。能把文件带出根目录的只有两样:
    它所在的目录(目录联接、符号链接)和它自己是一个符号链接。前者按目录缓存,
    后者逐个 lstat,一旦是链接就退回完整的 `_inside`。
    """

    def __init__(self, base: Path):
        self.base = base
        self.dirs: dict = {}

    def __call__(self, p: Path) -> bool:
        try:
            if p.is_symlink():
                return _inside(self.base, p)
        except OSError:
            return False
        d = p.parent
        ok = self.dirs.get(d)
        if ok is None:
            ok = self.dirs[d] = _inside(self.base, d)
        return ok


def _check_id(sid) -> str:
    if not isinstance(sid, str) or not UUID_RE.match(sid):
        raise Refused(f"会话 id 不是 UUID 形状: {str(sid)[:60]!r}", "bad_id")
    return sid


def _check_uuid(u, what: str = "节点") -> str:
    if not isinstance(u, str) or not UUID_RE.match(u):
        raise Refused(f"{what} uuid 形状不对: {str(u)[:60]!r}", "bad_uuid")
    return u


def _check_sub(sub):
    if sub is None or sub == "":
        return None
    if not isinstance(sub, str) or not AGENT_RE.match(sub):
        raise Refused(f"子代理 id 形状不对: {str(sub)[:60]!r}", "bad_sub")
    return sub


def _check_leaf(leaf):
    if leaf is None or leaf == "":
        return None
    if not isinstance(leaf, str) or not UUID_RE.match(leaf):
        raise Refused(f"leaf 形状不对: {str(leaf)[:60]!r}", "bad_leaf")
    return leaf


def shape(sid, sub=None, leaf=None, required=(), **nodes) -> None:
    """客户端给的每一个 id 的形状闸,在**碰文件系统之前**整体判一次。

    服务端的四条路由和这里的四个 API 用的是同一个函数:形状规则只有这一份。
    路由层先叫它,是为了让一个 `../` 形状的参数连一次 stat 都走不到;
    API 自己入口处也叫它,是因为 API 也会被别的调用方直接用。

    码各有各的:会话 id 是 bad_id,子代理是 bad_sub,叶子是 bad_leaf,其余节点参数
    (u / to / from / at)是 bad_uuid。一道闸兜住另一道时,测试靠码分辨是哪一道拦的。
    nodes 里值为 None 或 "" 的是「没给」;名字在 required 里的没给也是 bad_uuid。
    必填与否也放在这里,而不是各个调用点各写一句 `if not x`:路由层和 API 层对
    「缺终点」的判断必须是同一句话。
    """
    _check_id(sid)
    _check_sub(sub)
    _check_leaf(leaf)
    for name in required:
        if nodes.get(name) is None or nodes.get(name) == "":
            raise Refused(f"缺 {name}", "bad_uuid")
    for name, v in nodes.items():
        if v is None or v == "":
            continue
        _check_uuid(v, name)


def locate(sid: str, sub: str | None = None, root: str | None = None) -> dict:
    """把会话 id(和可选的子代理 id)解析成文件。

    返回 {path, projectDir, base, main, sub, gz}。形状不对抛 Refused(bad_id / bad_sub),
    根目录没配抛 Unavailable。0 个匹配是 not_found,多于 1 个是 ambiguous:
    两个项目目录里有同名文件时挑一个看起来合理的,等于替人做了一个看不见的选择。
    """
    _check_id(sid)
    sub = _check_sub(sub)
    base = _base(root)
    hits = sorted(base.glob(f"*/{sid}.jsonl"))
    if not hits:
        raise Refused(f"没找到会话 {sid}", "not_found")
    if len(hits) > 1:
        raise Refused(f"会话 {sid} 在 {len(hits)} 个项目目录里都有,拒绝猜", "ambiguous")
    main = hits[0]
    if not _inside(base, main):
        raise Refused("解析出的文件不在会话根目录里", "outside_root")
    proj = main.parent
    path, gz = main, False
    if sub:
        sd = proj / sid / "subagents"
        cands = []
        for pat in (f"agent-{sub}.jsonl", f"agent-{sub}.jsonl.gz",
                    f"workflows/*/agent-{sub}.jsonl", f"workflows/*/agent-{sub}.jsonl.gz"):
            cands.extend(sd.glob(pat))
        cands = sorted(set(cands))
        if not cands:
            raise Refused(f"没找到子代理 {sub}", "not_found")
        if len(cands) > 1:
            raise Refused(f"子代理 {sub} 有 {len(cands)} 份文件,拒绝猜", "ambiguous")
        path = cands[0]
        if not _inside(base, path):
            raise Refused("解析出的子代理文件不在会话根目录里", "outside_root")
        gz = path.name.endswith(".gz")
    return {"path": path, "projectDir": proj, "base": base, "main": main, "sub": sub, "gz": gz}


def _open_bin(path: Path, gz: bool):
    return gzip.open(path, "rb") if gz else open(path, "rb")


# ---------------------------------------------------------------- 解析

def _s(v):
    return v if isinstance(v, str) else None


def _clip(s, n=PREVIEW):
    if not isinstance(s, str):
        return ""
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _blocks(msg):
    c = (msg or {}).get("content") if isinstance(msg, dict) else None
    return c if isinstance(c, list) else []


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    out = []
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text" and isinstance(b.get("text"), str):
                    out.append(b["text"])
                elif b.get("type") == "image":
                    out.append("[image]")
                else:
                    out.append(f"[{b.get('type')}]")
    return "\n".join(out)


def _tool_short(inp) -> str:
    """工具输入的一句话:挑最能说明这次调用干了什么的那个键。"""
    if not isinstance(inp, dict):
        return _clip(json.dumps(inp, ensure_ascii=False) if inp is not None else "", 160)
    for k in ("description", "command", "file_path", "pattern", "path", "url", "query",
              "prompt", "skill", "action"):
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            return _clip(v, 160)
    return _clip(json.dumps(inp, ensure_ascii=False), 160)


def _classify(o: dict):
    """返回 (kind, preview, toolName, toolUseIds, resultForIds)。"""
    t = o.get("type")
    msg = o.get("message") if isinstance(o.get("message"), dict) else {}
    if t == "user":
        if o.get("isCompactSummary"):
            return "summary", _clip(_result_text(msg.get("content"))), None, (), ()
        blocks = _blocks(msg)
        rids = tuple(b.get("tool_use_id") for b in blocks
                     if isinstance(b, dict) and b.get("type") == "tool_result"
                     and isinstance(b.get("tool_use_id"), str))
        if rids:
            first = next(b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result")
            return "result", _clip(_result_text(first.get("content"))), None, (), rids
        txt = _typed_text(o)
        if txt and not o.get("isMeta") and not _looks_injected(txt):
            return "human", _clip(txt), None, (), ()
        return "usermeta", _clip(_result_text(msg.get("content"))), None, (), ()
    if t == "assistant":
        blocks = [b for b in _blocks(msg) if isinstance(b, dict)]
        tids = tuple(b.get("id") for b in blocks
                     if b.get("type") in ("tool_use", "server_tool_use") and isinstance(b.get("id"), str))
        pick = next((b for b in blocks if b.get("type") not in ("thinking", "redacted_thinking")),
                    blocks[0] if blocks else None)
        if pick is None:
            return "text", "", None, tids, ()
        bt = pick.get("type")
        if bt in ("thinking", "redacted_thinking"):
            return "thinking", _clip(pick.get("thinking") or ""), None, tids, ()
        if bt in ("tool_use", "server_tool_use"):
            name = _s(pick.get("name")) or "?"
            return "tool", _clip(f"{name}: {_tool_short(pick.get('input'))}"), name, tids, ()
        return "text", _clip(pick.get("text") or ""), None, tids, ()
    if t == "system":
        if o.get("subtype") == "compact_boundary":
            cm = o.get("compactMetadata") or {}
            return ("compact", _clip(f"{cm.get('trigger') or '?'} · {cm.get('preTokens')}→{cm.get('postTokens')}"),
                    None, (), ())
        return "system", _clip(f"{o.get('subtype') or ''} {_s(o.get('content')) or ''}".strip()), None, (), ()
    if t == "attachment":
        a = o.get("attachment") if isinstance(o.get("attachment"), dict) else {}
        return "attachment", _clip(str(a.get("type") or "attachment")), None, (), ()
    return "system", _clip(str(t)), None, (), ()


def _slim_cm(cm):
    if not isinstance(cm, dict):
        return None
    pm = cm.get("preservedMessages") if isinstance(cm.get("preservedMessages"), dict) else {}
    lst = lambda v: [x for x in v if isinstance(x, str)] if isinstance(v, list) else []
    return {"trigger": cm.get("trigger"), "preTokens": cm.get("preTokens"),
            "postTokens": cm.get("postTokens"),
            "uuids": lst(pm.get("uuids")), "allUuids": lst(pm.get("allUuids")),
            "anchorUuid": _s(pm.get("anchorUuid"))}


class Index:
    """一份转录的索引。E 是按文件顺序排的链条目(有 uuid 的行),每条一个瘦字典。"""
    __slots__ = ("path", "gz", "bytes", "lines", "bad", "E", "by", "children", "mids",
                 "results", "roots", "dangling", "pred", "title", "aiTitle", "customTitle",
                 "replacements", "agentOf", "size", "maxk", "firstHuman", "ms", "dupUuids",
                 "predinv", "subs", "tail", "guessed", "noMid")


def _build(path: Path, gz: bool) -> Index:
    t0 = time.perf_counter()
    ix = Index()
    ix.path, ix.gz = path, gz
    E, by = [], {}
    bad = lines = 0
    tail = False
    custom = ai = None
    repl = []
    dup = 0
    off = 0
    intern = sys.intern
    with _open_bin(path, gz) as fh:
        for i, raw in enumerate(fh):
            lines += 1
            n = len(raw)
            s = raw.strip()
            if not s:
                off += n
                continue
            try:
                o = json.loads(s)
                if not isinstance(o, dict):
                    raise ValueError
            except ValueError:
                # 没有换行结尾的最后一行是「正在写」,不是「坏了」:一个还开着的会话每次
                # 读都会碰上它。算进坏行的话,每一个活的会话都挂一条损坏警告。
                if not raw.endswith(b"\n"):
                    tail = True
                else:
                    bad += 1
                off += n
                continue
            u = o.get("uuid")
            if isinstance(u, str) and u and u in by:
                # 同一个 uuid 被整行重写了一遍(大转录里会有成百上千次,父节点全都相同,
                # 隔了一两千行)。两份都进孩子表的话,每一份都长成一个「真分叉」,
                # 报出来的分叉绝大多数会是这种复本。
                # 留**第一份**:它在原来的位置上,按行号排序时不会被挪到后面的对话中间。
                dup += 1
            elif isinstance(u, str) and u:
                kind, pv, tname, tids, rids = _classify(o)
                msg = o.get("message") if isinstance(o.get("message"), dict) else {}
                tur = o.get("toolUseResult")
                cwd = _s(o.get("cwd"))
                e = {"u": u, "p": _s(o.get("parentUuid")), "lp": _s(o.get("logicalParentUuid")),
                     "t": _s(o.get("type")), "st": _s(o.get("subtype")), "i": i, "off": off, "n": n,
                     "ts": _s(o.get("timestamp")), "mid": _s(msg.get("id")),
                     "side": bool(o.get("isSidechain")), "meta": bool(o.get("isMeta")),
                     "cs": bool(o.get("isCompactSummary")), "ps": _s(o.get("promptSource")),
                     "cm": _slim_cm(o.get("compactMetadata")) if kind == "compact" else None,
                     "kind": kind, "pv": pv, "tn": tname, "tids": tids, "rids": rids,
                     "ag": _s(tur.get("agentId")) if isinstance(tur, dict) else None,
                     "cwd": intern(cwd) if cwd else None, "k": len(E)}
                by[u] = len(E)
                E.append(e)
            else:
                t = o.get("type")
                if t == "custom-title" and isinstance(o.get("customTitle"), str):
                    custom = o["customTitle"].strip() or custom
                elif t == "ai-title" and isinstance(o.get("aiTitle"), str):
                    ai = o["aiTitle"].strip() or ai
                elif t == "content-replacement":
                    repl.append((off, n))
            off += n
    ix.bytes = off
    ix.subs = None
    ix.lines, ix.bad, ix.E, ix.by = lines, bad, E, by
    ix.tail = tail
    ix.customTitle, ix.aiTitle, ix.replacements, ix.dupUuids = custom, ai, repl, dup

    children = collections.defaultdict(list)
    mids = collections.defaultdict(list)
    results = collections.defaultdict(list)
    agent_of = {}
    roots, dangling = [], 0
    no_mid = 0
    for e in E:
        p = e["p"]
        if p is None:
            roots.append(e["k"])
        elif p not in by:
            dangling += 1
            roots.append(e["k"])
        else:
            children[by[p]].append(e["k"])
        if e["t"] == "assistant" and e["mid"]:
            mids[e["mid"]].append(e["k"])
        elif e["t"] == "assistant":
            no_mid += 1
        for r in e["rids"]:
            results[r].append(e["k"])
            if e["ag"]:
                agent_of[r] = e["ag"]
    ix.children, ix.mids, ix.results, ix.roots, ix.dangling = children, mids, results, roots, dangling
    ix.agentOf = agent_of
    ix.noMid = no_mid

    # 每个压缩边界的前驱。这是静态的(只看文件,不看叶子),所以建索引时算一次。
    ix.pred, ix.guessed = {}, set()
    for e in E:
        if e["kind"] == "compact":
            ix.pred[e["k"]], how = _predecessor(ix, e["k"])
            if how == "prev":
                ix.guessed.add(e["k"])

    _aggregate(ix)
    ix.title = custom or ai
    ix.ms = round((time.perf_counter() - t0) * 1000)
    return ix


def _is_descendant_raw(ix: Index, k: int, anc: int) -> bool:
    seen = set()
    while k is not None and k not in seen:
        if k == anc:
            return True
        seen.add(k)
        p = ix.E[k]["p"]
        k = ix.by.get(p) if p else None
    return False


def _predecessor(ix: Index, b: int):
    """边界 B 之前那段历史接在哪一行上。三级,按可信度排:

    1. logicalParentUuid,但只在它**在文件里、写在 B 之前、而且不是 B 的后代**时。
       实际转录里多数边界的这个指针指向 B 之后写下的行,照走会绕回 B。
    2. preservedMessages.allUuids(没有就 uuids)里写在 B 之前、行号最大的那个。
    3. 文件顺序上紧挨着 B 的前一条链条目。

    返回 (前驱, 用的是哪一级)。第三级是猜:回退过的会话里,紧挨着的前一行完全可能写在
    被放弃的那一支上。没有哪条规则能在两支之间证明谁对,所以不换一个更聪明的猜法,
    而是让调用方把「这里是猜的」说出来。
    """
    E, B = ix.E, ix.E[b]
    lp = B["lp"]
    if lp and lp in ix.by:
        k = ix.by[lp]
        if E[k]["i"] < B["i"] and not _is_descendant_raw(ix, k, b):
            return k, "logical"
    cm = B["cm"] or {}
    best = None
    for u in (cm.get("allUuids") or cm.get("uuids") or []):
        k = ix.by.get(u)
        if k is not None and E[k]["i"] < B["i"] and (best is None or E[k]["i"] > E[best]["i"]):
            best = k
    if best is not None:
        return best, "preserved"
    return (b - 1, "prev") if b > 0 else (None, "none")


def _aggregate(ix: Index):
    """一次后序,算出每个节点子树的大小、末端(行号最大的后代)、第一条真人消息。

    压缩边界被当成它前驱的逻辑孩子:否则一个压缩之前的分叉,活跃那一侧的子树会在边界处
    断掉,选它得到的叶子是压缩前最后一行,整段压缩后的对话凭空消失。
    """
    E = ix.E
    predinv = collections.defaultdict(list)
    for b, p in ix.pred.items():
        if p is not None:
            predinv[p].append(b)
    n = len(E)
    size = [1] * n
    maxk = list(range(n))
    fh = [k if E[k]["kind"] == "human" else None for k in range(n)]
    # 根:原始根里除去那些有前驱的边界(它们挂在前驱下面)。
    starts = [r for r in ix.roots if not (E[r]["kind"] == "compact" and ix.pred.get(r) is not None)]
    visited = bytearray(n)
    order = []
    stack = list(starts)
    for r in starts:
        visited[r] = 1
    while stack:
        k = stack.pop()
        order.append(k)
        for c in ix.children.get(k, ()):
            if not visited[c]:
                visited[c] = 1
                stack.append(c)
        for c in predinv.get(k, ()):
            if not visited[c]:
                visited[c] = 1
                stack.append(c)
    for k in reversed(order):
        for c in list(ix.children.get(k, ())) + predinv.get(k, []):
            size[k] += size[c]
            if maxk[c] > maxk[k]:
                maxk[k] = maxk[c]
            if fh[c] is not None and (fh[k] is None or fh[c] < fh[k]):
                fh[k] = fh[c]
    ix.size, ix.maxk, ix.firstHuman, ix.predinv = size, maxk, fh, predinv


# ---------------------------------------------------------------- 缓存

_CACHE: "collections.OrderedDict" = collections.OrderedDict()
_LOCK = threading.Lock()


def _index(path: Path, gz: bool) -> tuple[Index, bool]:
    st = path.stat()
    key = (os.path.normcase(str(path)), st.st_mtime_ns, st.st_size)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None:
            _CACHE.move_to_end(key)
            return hit, True
    ix = _build(path, gz)
    with _LOCK:
        _CACHE[key] = ix
        _CACHE.move_to_end(key)
        while len(_CACHE) > CACHE_SLOTS:
            _CACHE.popitem(last=False)
    return ix, False


# ---------------------------------------------------------------- 路径

def _leaf_k(ix: Index, leaf) -> tuple[int, bool]:
    if leaf is None or leaf == "":
        if not ix.E:
            raise Refused("这份转录里没有任何链条目", "empty")
        return len(ix.E) - 1, True
    _check_leaf(leaf)
    k = ix.by.get(leaf)
    if k is None:
        raise Refused("leaf 不在这份转录里", "bad_leaf")
    return k, False


def _path(ix: Index, leafk: int) -> tuple[list, dict, int, set]:
    """从叶子走回根,跨过每一个压缩边界,再补全回复组。
    返回 (显示顺序, 段号表, 环数, 上溯真正走过的节点)。

    最后一项和显示顺序不是一回事:显示顺序里还有补全拉进来的同组块和工具结果。
    「哪一支是当前的」只能按走过的节点判,按显示顺序判的话,补全拉进来的那一块
    永远算当前,分叉菜单里会出现两个「当前」。

    段号在每个压缩边界处加一,边界自己属于新的那一段。显示顺序是「先按段、段内按行号」:
    跨段按行号排会把被保留的消息(它们写在边界之前)和压缩后的内容搅在一起。
    """
    E = ix.E
    seq, seen, cycles = [], set(), 0
    k = leafk
    while k is not None:
        if k in seen:
            cycles += 1
            break
        seen.add(k)
        seq.append(k)
        e = E[k]
        if e["kind"] == "compact":
            k = ix.pred.get(k)
            continue
        p = e["p"]
        k = ix.by.get(p) if p else None
    walked = set(seq)
    seq.reverse()
    segof, seg = {}, 0
    for k in seq:
        if E[k]["kind"] == "compact":
            seg += 1
        segof[k] = seg
    _complete(ix, segof)
    order = sorted(segof, key=lambda k: (segof[k], E[k]["i"]))
    return order, segof, cycles, walked


def _complete(ix: Index, segof: dict) -> None:
    """回复组补全:同一 message.id 的所有块,和被纳入的每个 tool_use 的结果。

    并行工具调用在 parentUuid 上长成一个 1→2 的假分叉,叶子那一侧的上溯会跳过另一块
    和它的结果。不补全的话,一次调了三个工具的回复在链上只剩一个。
    """
    E = ix.E
    for k in list(segof):
        e = E[k]
        if e["t"] == "assistant" and e["mid"]:
            for m in ix.mids.get(e["mid"], ()):
                if not E[m]["side"] or e["side"]:
                    segof.setdefault(m, segof[k])
    for k in list(segof):
        for tid in E[k]["tids"]:
            for r in ix.results.get(tid, ()):
                if not E[r]["side"] or E[k]["side"]:
                    segof.setdefault(r, segof[k])


def _fork_at(ix: Index, k: int, walked: set):
    """k 是不是一个真分叉。并行工具的伪分叉(同组的下一块、本组工具的结果)合并成一支。

    walked 是上溯真正走过的节点,不是显示链:显示链含补全拉进来的块,拿它判「当前」
    会让伪分叉那一组永远亮着「当前」。"""
    E = ix.E
    ch = ix.children.get(k, ())
    if len(ch) < 2:
        return None
    e = E[k]
    is_asst = e["t"] == "assistant" and bool(e["mid"])
    grp = ix.mids.get(e["mid"], [k]) if is_asst else [k]
    tids = {t for g in grp for t in E[g]["tids"]}
    pseudo, real = [], []
    for c in ch:
        ce = E[c]
        if (is_asst and ce["mid"] == e["mid"]) or \
                (ce["kind"] == "result" and any(r in tids for r in ce["rids"])):
            pseudo.append(c)
        else:
            real.append(c)
    # 已经有两支以上真分支时,那组同回复的块如果既不在走过的链上、里面也没有任何
    # 用户消息,它就不是一条能「切过去」的对话,只是被放弃那次回复的残块。
    # 列出来的话,菜单里会多一个点了只能停在一个工具调用上的「分支」。
    if (pseudo and len(real) >= 2 and not any(c in walked for c in pseudo)
            and all(ix.firstHuman[c] is None for c in pseudo)):
        pseudo = []
    branches = [[c] for c in real] + ([pseudo] if pseudo else [])
    if len(branches) < 2:
        return None
    alts = []
    for br in branches:
        leafk = max((ix.maxk[c] for c in br), key=lambda x: E[x]["i"])
        fhs = [ix.firstHuman[c] for c in br if ix.firstHuman[c] is not None]
        fh = min(fhs, key=lambda x: E[x]["i"]) if fhs else None
        alts.append({"u": E[br[0]]["u"], "size": sum(ix.size[c] for c in br),
                     "leaf": E[leafk]["u"], "leafLineIndex": E[leafk]["i"],
                     "preview": E[fh]["pv"] if fh is not None else None,
                     "active": any(c in walked for c in br)})
    return {"u": e["u"], "lineIndex": e["i"], "alternatives": alts}


def _subagents(loc: dict, sid: str) -> tuple[list, int]:
    """会话目录下的子代理文件。meta 读不动要计数,不当成「没有描述」。"""
    sd = loc["projectDir"] / sid / "subagents"
    out, bad = [], 0
    if not sd.is_dir():
        return out, bad
    files = list(sd.glob("agent-*.jsonl")) + list(sd.glob("agent-*.jsonl.gz")) + \
        list(sd.glob("workflows/*/agent-*.jsonl")) + list(sd.glob("workflows/*/agent-*.jsonl.gz"))
    inside = _Containment(loc["base"])
    for f in sorted(set(files)):
        # glob 会跟着目录联接走出根目录。打开子代理时 locate 会拦,列清单时也得拦:
        # 否则根外那份 meta 里的描述会原样出现在清单里。
        if not inside(f):
            bad += 1
            continue
        name = f.name
        stem = name[len("agent-"):]
        stem = stem[:-len(".jsonl.gz")] if stem.endswith(".jsonl.gz") else stem[:-len(".jsonl")]
        if not AGENT_RE.match(stem):
            bad += 1
            continue
        meta = f.with_name(f"agent-{stem}.meta.json")
        rec = {"agentId": stem, "toolUseId": None, "description": None, "agentType": None,
               "file": f.relative_to(loc["projectDir"]).as_posix(), "gz": name.endswith(".gz")}
        if meta.is_file():
            try:
                if not inside(meta):
                    raise ValueError("outside root")
                with open(meta, "rb") as fh:
                    blob = fh.read(META_MAX + 1)
                if len(blob) > META_MAX:
                    raise ValueError("too big")
                m = json.loads(blob.decode("utf-8"))
                if isinstance(m, dict):
                    rec["toolUseId"] = _s(m.get("toolUseId"))
                    rec["description"] = _s(m.get("description"))
                    rec["agentType"] = _s(m.get("agentType"))
            except (OSError, ValueError):
                bad += 1
        out.append(rec)
    return out, bad


def _load(sid, sub, root):
    loc = locate(sid, sub, root)
    ix, cached = _index(loc["path"], loc["gz"])
    return loc, ix, cached


def _fallback_title(ix: Index, sid: str) -> str:
    if ix.title:
        return ix.title
    for e in ix.E:
        if e["kind"] == "human":
            return _clip(e["pv"], 110)
    return sid[:8]


# ---------------------------------------------------------------- API 1: chain

def chain(sid, leaf=None, sub=None, root=None) -> dict:
    """整条显示链,按轮切好。形状见 README 的会话一节;字段名以这里为准。"""
    shape(sid, sub=sub, leaf=leaf)
    try:
        loc, ix, cached = _load(sid, sub, root)
    except Unavailable as e:
        return {"available": False, "reason": str(e)}
    E = ix.E
    base = {"available": True, "id": sid, "sub": loc["sub"], "file": str(loc["path"]),
            "projectDir": loc["projectDir"].name, "title": _fallback_title(ix, sid),
            "bytes": ix.bytes, "lines": ix.lines, "badLines": ix.bad, "incompleteTail": ix.tail,
            "chainEntries": len(E),
            "danglingParents": ix.dangling, "duplicateUuids": ix.dupUuids,
            "indexMs": ix.ms, "cached": cached}
    if not E:
        base.update(cwd=None, leaf=None, leafIsDefault=True, pathLen=0, turns=[],
                    compactions=0, forks=0, subagents=[], subagentMetaUnreadable=0,
                    warnings=["这份转录里没有任何链条目"]
                    + ([f"{ix.bad} 行不是合法 JSON,已跳过并计数"] if ix.bad else [])
                    + (["最后一行还没写完(会话可能正在进行),这次没有算进来"] if ix.tail else []))
        return base
    leafk, is_default = _leaf_k(ix, leaf)
    order, _segof, cycles, walked = _path(ix, leafk)

    # 子代理清单跟着主转录的索引一起缓存:派生一个子代理必然往主转录里追加一行 tool_use,
    # 于是主转录的 (mtime, 大小) 变了,索引和这份清单一起失效。实测每次重扫要半秒多。
    if loc["sub"]:
        subs, subs_bad = [], 0
    else:
        if ix.subs is None:
            ix.subs = _subagents(loc, sid)
        subs, subs_bad = ix.subs
    agent_by_tool = {s["toolUseId"]: s["agentId"] for s in subs if s["toolUseId"]}
    have_agent = {s["agentId"] for s in subs}
    tname = {}
    for k in order:
        e = E[k]
        if e["kind"] == "tool" and e["tn"]:
            for t in e["tids"]:
                tname[t] = e["tn"]

    turns, cur, tno, nforks, ncomp = [], None, 0, 0, 0
    for k in order:
        e = E[k]
        fk = _fork_at(ix, k, walked)
        if fk:
            nforks += 1
        if e["kind"] in ("compact", "summary"):
            cur = None
            m = {"type": "marker", "kind": e["kind"], "u": e["u"], "lineIndex": e["i"], "ts": e["ts"],
                 "preview": e["pv"], "forks": [fk] if fk else []}
            if e["kind"] == "compact":
                ncomp += 1
                cm = e["cm"] or {}
                m.update(trigger=cm.get("trigger"), preTokens=cm.get("preTokens"),
                         postTokens=cm.get("postTokens"))
            turns.append(m)
            continue
        if e["kind"] == "human" or cur is None:
            cur = {"type": "turn", "k": tno, "u": e["u"], "ts": e["ts"], "human": None,
                   "steps": [], "counts": {}, "forks": []}
            tno += 1
            turns.append(cur)
            if e["kind"] == "human":
                cur["human"] = {"u": e["u"], "ts": e["ts"], "preview": e["pv"]}
        st = {"u": e["u"], "kind": e["kind"], "preview": e["pv"], "ts": e["ts"], "lineIndex": e["i"]}
        ag = None
        if e["kind"] == "tool":
            st["name"] = e["tn"]
            for t in e["tids"]:
                ag = ix.agentOf.get(t) or agent_by_tool.get(t) or ag
        elif e["kind"] == "result":
            names = [tname[r] for r in e["rids"] if r in tname]
            if names:
                st["name"] = names[0]
            ag = e["ag"] or next((agent_by_tool[r] for r in e["rids"] if r in agent_by_tool), None)
        if ag:
            st["agentId"] = ag
            # 子代理文件找不到时照实说,不画一个点了必然失败的链接。
            st["agentFile"] = ag in have_agent
        if fk:
            st["fork"] = len(fk["alternatives"])
            cur["forks"].append(fk)
        cur["steps"].append(st)
        cur["counts"][e["kind"]] = cur["counts"].get(e["kind"], 0) + 1

    warnings = []
    if ix.bad:
        warnings.append(f"{ix.bad} 行不是合法 JSON,已跳过并计数")
    if ix.tail:
        warnings.append("最后一行还没写完(会话可能正在进行),这次没有算进来")
    if ix.dangling:
        warnings.append(f"{ix.dangling} 条记录的父节点不在文件里,各自当作孤立的根")
    if cycles:
        warnings.append("上溯时遇到环,已在环处停下")
    if ix.dupUuids:
        warnings.append(f"{ix.dupUuids} 行是已出现过的 uuid 的重写,只保留第一份")
    if subs_bad:
        warnings.append(f"{subs_bad} 个子代理文件或其 meta 读不动")
    if ix.noMid:
        warnings.append(f"{ix.noMid} 条助手记录没有 message.id,它们所在的并行工具调用"
                        "认不出是同一次回复,可能被报成分叉")
    guessed = [E[k]["u"][:8] for k in order if k in ix.guessed]
    if guessed:
        warnings.append(f"压缩边界 {', '.join(guessed)} 没有可用的前驱指针,按文件里紧挨着的"
                        "前一行接上了压缩前的历史;会话回退过的话,接上的可能是被放弃的那一支")
    lk = E[leafk]
    base.update(cwd=lk["cwd"], leaf=lk["u"], leafIsDefault=is_default, pathLen=len(order),
                turns=turns, compactions=ncomp, forks=nforks, subagents=subs,
                subagentMetaUnreadable=subs_bad, warnings=warnings)
    return base


# ---------------------------------------------------------------- 读单行

def _read_lines(ix: Index, ks):
    """按偏移把这几行读回来,按偏移从小到大读,gz 也一样(见下)。"""
    out = {}
    # gz 也按偏移顺序 seek:gzip 的 seek 是边解压边往前走,内存里只留要的那几行。
    # 整份 read() 的话,一个压缩比极高的文件解压出来多大就占多大内存。
    with _open_bin(ix.path, ix.gz) as fh:
        for k in sorted(ks, key=lambda x: ix.E[x]["off"]):
            e = ix.E[k]
            fh.seek(e["off"])
            out[k] = fh.read(e["n"])
    return out


def _obj(raw: bytes, u: str) -> dict:
    try:
        o = json.loads(raw)
    except ValueError:
        o = None
    if not isinstance(o, dict) or o.get("uuid") != u:
        # 偏移处读到的不是索引里那一行:文件在索引之后被改写过(不只是追加)。
        raise Refused("转录在读取期间被改写,请重新加载", "stale_index")
    return o


def _cut(s, flags: list, name: str):
    if isinstance(s, str) and len(s) > FIELD_MAX:
        flags.append(name)
        return s[:FIELD_MAX]
    return s


def _content(o: dict) -> dict:
    """一行的可读内容。每个字段各自截断,截了就在 truncatedFields 里点名。"""
    msg = o.get("message") if isinstance(o.get("message"), dict) else {}
    flags: list = []
    text, thinking, tools, results = [], [], [], []
    # 每个字段各截 20 万字符,但字段个数没有上限:一行里几十个工具结果照样能拼出
    # 几十兆的响应。所以工具输入和结果另有一个总额,超了就不再往里加,并且点名。
    budget = [RESPONSE_MAX]

    def room(n):
        if budget[0] <= 0:
            if "omitted" not in flags:
                flags.append("omitted")
            return False
        budget[0] -= n
        return True
    c = msg.get("content")
    if isinstance(c, str):
        text.append(c)
    elif isinstance(c, list):
        for b in c:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text" and isinstance(b.get("text"), str):
                text.append(b["text"])
            elif bt in ("thinking", "redacted_thinking"):
                thinking.append(b.get("thinking") or "[redacted]")
            elif bt in ("tool_use", "server_tool_use"):
                inp = _cut(json.dumps(b.get("input"), ensure_ascii=False, indent=2),
                           flags, "tool.input")
                if room(len(inp)):
                    tools.append({"name": b.get("name"), "id": b.get("id"), "input": inp,
                                  "short": _tool_short(b.get("input"))})
            elif bt == "tool_result":
                rt = _cut(_result_text(b.get("content")), flags, "result.text")
                if room(len(rt)):
                    results.append({"tool_use_id": b.get("tool_use_id"),
                                    "isError": bool(b.get("is_error")), "text": rt})
            elif bt == "image":
                text.append("[image]")
    out = {"text": _cut("\n\n".join(text), flags, "text") if text else None,
           "thinking": _cut("\n\n".join(thinking), flags, "thinking") if thinking else None,
           "tools": tools, "results": results}
    if o.get("type") == "system":
        out["systemContent"] = _cut(_s(o.get("content")), flags, "systemContent")
    if isinstance(o.get("attachment"), dict):
        a = o["attachment"]
        out["attachment"] = {"type": a.get("type"),
                             "summary": _cut(json.dumps(a, ensure_ascii=False, indent=2),
                                             flags, "attachment")}
    out["truncated"] = bool(flags)
    out["truncatedFields"] = flags
    return out


# ---------------------------------------------------------------- API 2: node

def node(sid, uuid, sub=None, root=None) -> dict:
    """一个节点的完整内容,按索引里的偏移单独读回那一行。"""
    shape(sid, sub=sub, required=("u",), u=uuid)
    try:
        loc, ix, _cached = _load(sid, sub, root)
    except Unavailable as e:
        return {"available": False, "reason": str(e)}
    k = ix.by.get(uuid)
    if k is None:
        raise Refused("这个节点不在这份转录里", "bad_node")
    e = ix.E[k]
    raw = _read_lines(ix, [k])[k]
    o = _obj(raw, uuid)
    msg = o.get("message") if isinstance(o.get("message"), dict) else {}
    res = {"available": True, "id": sid, "sub": loc["sub"], "u": uuid, "lineIndex": e["i"],
           "byteOffset": e["off"], "byteLength": e["n"],
           "type": e["t"], "subtype": e["st"], "kind": e["kind"],
           "role": _s(msg.get("role")), "model": _s(msg.get("model")), "ts": e["ts"],
           "parentUuid": e["p"], "logicalParentUuid": e["lp"], "messageId": e["mid"],
           "isMeta": e["meta"], "isSidechain": e["side"], "isCompactSummary": e["cs"],
           "promptSource": e["ps"], "cwd": e["cwd"], "agentId": e["ag"],
           "compactMetadata": e["cm"]}
    res.update(_content(o))
    # 小行给原文方便对照;大行永远不整行下发。
    if e["n"] <= RAW_MAX:
        r = raw.decode("utf-8", "replace").rstrip("\r\n")
        res["raw"] = r[:FIELD_MAX]
        res["rawTruncated"] = len(r) > FIELD_MAX
    else:
        res["raw"] = None
        res["rawTruncated"] = True
    return res


# ---------------------------------------------------------------- API 3: export_md

def _slug(title: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", title or "").strip("-").lower()
    return s[:40].strip("-")


def _fence(text: str) -> str:
    run = max((len(m) for m in re.findall(r"`+", text)), default=0)
    f = "`" * max(3, run + 1)
    return f"{f}text\n{text}\n{f}"


def _local_ts(ts) -> str:
    """转录里的时间是 UTC。导出的标题用本机时区:会话列表和对话链都按本机时间显示,
    导出文件里要是另一套时间,人拿着文件回页面上对不上是哪一轮。"""
    if not isinstance(ts, str) or not ts:
        return "无时间"
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    if d.tzinfo is None:
        return ts
    return d.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def _range(order, E, to, frm):
    pos = {E[k]["u"]: n for n, k in enumerate(order)}
    _check_uuid(to, "终点")
    if to not in pos:
        raise Refused("终点不在当前显示的链上", "not_on_path")
    if frm:
        _check_uuid(frm, "起点")
        if frm not in pos:
            raise Refused("起点不在当前显示的链上", "not_on_path")
    a = pos[frm] if frm else 0
    b = pos[to]
    if a > b:
        raise Refused("起点在终点之后", "bad_range")
    return a, b


def export_md(sid, to, frm=None, leaf=None, sub=None, include_tools=False,
              include_thinking=False, root=None) -> dict:
    """显示链上 [frm, to] 这一段的 Markdown。只读。"""
    shape(sid, sub=sub, leaf=leaf, required=("to",), to=to, frm=frm)
    try:
        loc, ix, _cached = _load(sid, sub, root)
    except Unavailable as e:
        raise Refused(str(e), "unavailable") from e
    E = ix.E
    leafk, _ = _leaf_k(ix, leaf)
    order, _segof, _c, _w = _path(ix, leafk)
    a, b = _range(order, E, to, frm)
    ks = order[a: b + 1]
    want = [k for k in ks if E[k]["kind"] in ("human", "text", "summary", "compact")
            or (include_tools and E[k]["kind"] in ("tool", "result"))
            or (include_thinking and E[k]["kind"] == "thinking")]
    raws = _read_lines(ix, want)
    title = _fallback_title(ix, sid)
    first, last = E[ks[0]], E[ks[-1]]
    cwd = last["cwd"] or first["cwd"]
    out = [f"# {title}", "",
           f"- 会话: `{sid}`" + (f" (子代理 `{loc['sub']}`)" if loc["sub"] else ""),
           f"- 目录: `{cwd}`" if cwd else "- 目录: 转录里没有记录",
           f"- 范围: `{first['u'][:8]}` → `{last['u'][:8]}` ({len(ks)} 个节点)",
           f"- 生成于: {datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S %z')}",
           "- 时间: 本机时区", ""]
    speaker = None
    turns = 0
    for k in ks:
        e = E[k]
        if e["kind"] == "human":
            turns += 1
        if k not in raws:
            continue
        c = _content(_obj(raws[k], e["u"]))
        ts = _local_ts(e["ts"])
        if e["kind"] == "compact":
            cm = e["cm"] or {}
            out += ["", "---", "",
                    f"**⟂ 上下文在这里被压缩** ({cm.get('trigger') or '?'} · "
                    f"{cm.get('preTokens')}→{cm.get('postTokens')} tokens)", ""]
            speaker = None
        elif e["kind"] == "summary":
            out += ["<details><summary>压缩摘要</summary>", "", c["text"] or "", "",
                    "</details>", "", "---", ""]
            speaker = None
        elif e["kind"] == "human":
            out += [f"### 用户 [{ts}]", "", c["text"] or "", ""]
            speaker = "user"
        else:
            if speaker != "claude":
                out += [f"### Claude [{ts}]", ""]
                speaker = "claude"
            if e["kind"] == "text" and c["text"]:
                out += [c["text"], ""]
            elif e["kind"] == "thinking" and c["thinking"]:
                out += ["<details><summary>思考</summary>", "", c["thinking"], "", "</details>", ""]
            elif e["kind"] == "tool":
                for t in c["tools"]:
                    out += [f"> 🔧 {t['name']}: {t['short']}", ""]
            elif e["kind"] == "result":
                for r in c["results"]:
                    txt = r["text"] or ""
                    if len(txt) > RESULT_MD_MAX:
                        txt = txt[:RESULT_MD_MAX] + f"\n… (截断,原文 {len(r['text'])} 字符)"
                    out += [_fence(txt), ""]
            if "omitted" in c["truncatedFields"]:
                out += ["> (这一行还有更多工具内容,超过单行下发上限,没有导出)", ""]
    name = f"{_slug(title) or sid[:8]}-{first['u'][:8]}-{last['u'][:8]}.md"
    name = re.sub(r"[^A-Za-z0-9._-]", "-", name)
    return {"filename": name, "text": "\n".join(out).rstrip() + "\n", "nodes": len(ks),
            "turns": turns}


# ---------------------------------------------------------------- API 4: fork

def _relinked_parent(ix: Index, b: int):
    """边界 b 的加载期重链(Claude Code 的 Wrr),写成一个「父节点」函数。

    uuids[0] 的父是摘要(anchor),uuids[i] 的父是 uuids[i-1],摘要的其他孩子改挂到
    uuids 的最后一个上;其余节点照原来的 parentUuid。沿这个函数上溯得到的,就是
    Claude Code 读这份文件时会拼出来的那条链,一个节点都不多拼。
    """
    E = ix.E
    cm = E[b]["cm"] or {}
    P, seenp = [], set()
    for u in cm.get("uuids") or []:
        k = ix.by.get(u)
        if k is not None and k not in seenp:
            seenp.add(k)
            P.append(k)
    anc = ix.by.get(cm.get("anchorUuid")) if cm.get("anchorUuid") else None
    pos = {k: n for n, k in enumerate(P)}

    def parent(k):
        n = pos.get(k)
        if n is not None:
            if n > 0:
                return P[n - 1]
            # 没有摘要可挂时直接挂在边界上:被保留的消息总归是接在边界之后的。
            return anc if anc is not None else b
        p = E[k]["p"]
        pk = ix.by.get(p) if p else None
        if P and anc is not None and pk == anc:
            return P[-1]
        return pk
    return parent, P, anc


def _fork_chain(ix: Index, atk: int) -> tuple[list, int | None, list]:
    """节点 at 那一刻模型真正拿到的上下文,按链顺序。返回 (链, 边界, 实际带上的被保留消息)。

    第一步只判「在哪个边界停」:沿原始 parentUuid 上溯,直接走到边界 B,或者从边界之后
    一步跨进 B 的被保留消息(uuids 或 allUuids,它们写在 B 之前)。后一种不拦的话,
    上溯会顺着被保留消息一路走进压缩前的全部历史,分叉出来的是模型当时根本没看到的上下文。

    第二步照 B 的加载期重链从 at 重新上溯一遍,链就是那一遍走过的节点。不能把 B 的
    被保留消息整段拼上去:回退到被保留的第 j 条再往下聊时,Claude Code 看到的只有
    前 j 条,整段拼上等于把人回退掉的那一问一答塞回上下文。B 和摘要之间的附件行
    也是这一遍自然走到的,位置和 Claude Code 看到的一样。

    最后给上溯那一段补回复组(同 message.id 的块、工具结果)。被保留的那一段不补:
    重链只认 uuids,补进去的块 Claude Code 看不到,而且会被按行号排到错的位置。
    """
    E = ix.E
    owner = collections.defaultdict(list)
    for e in E:
        if e["kind"] == "compact" and e["cm"]:
            for u in dict.fromkeys(e["cm"]["uuids"] + e["cm"]["allUuids"]):
                owner[u].append(e["k"])
    seen, endb = set(), None
    k = atk
    while k is not None and k not in seen:
        seen.add(k)
        e = E[k]
        if e["kind"] == "compact":
            endb = k
            break
        nk = ix.by.get(e["p"]) if e["p"] else None
        if nk is not None:
            for b in owner.get(E[nk]["u"], ()):
                if E[nk]["i"] < E[b]["i"] < e["i"]:
                    endb = b
                    break
            if endb is not None:
                break
        k = nk

    if endb is None:
        parent, P = (lambda k: ix.by.get(E[k]["p"]) if E[k]["p"] else None), []
    else:
        parent, P, _anc = _relinked_parent(ix, endb)
    walk, seen = [], set()
    k = atk
    while k is not None and k not in seen:
        seen.add(k)
        walk.append(k)
        if k == endb:
            break
        k = parent(k)
    walk.reverse()
    if endb is not None and (not walk or walk[0] != endb):
        # 重链没走到边界(摘要的父链断了)。边界照样放在最前面:没有它,
        # 这份分叉在 Claude Code 眼里就不是一份压缩过的会话。
        walk.insert(0, endb)

    # 分开「头」和「尾」:头是边界、摘要、被保留消息以及写在边界之前的一切,
    # 尾是边界之后真正聊出来的那一段。只有尾补回复组、按行号排。
    cut = -1
    if endb is not None:
        bi = E[endb]["i"]
        anc_k = ix.by.get((E[endb]["cm"] or {}).get("anchorUuid") or "")
        for n, k in enumerate(walk):
            if k == endb or k == anc_k or E[k]["i"] < bi:
                cut = n
    head, tail = walk[: cut + 1], walk[cut + 1:]
    onwalk = set(walk)
    segof = {k: 0 for k in tail}
    _complete(ix, segof)
    extra = {k for k in segof if k not in onwalk}
    if endb is not None:
        extra = {k for k in extra if E[k]["i"] > E[endb]["i"]}
    extra = {k for k in extra if not E[k]["side"]}
    # 补进来的同组块如果带着 tool_use、而它的结果不在分叉里,就不要它:那一块从来
    # 不是 at 的祖先,Claude Code 的链里没有它;带上它,分叉里就会有一个紧跟着
    # 用户消息、没有结果的 tool_use,API 会拒掉这样的上下文。
    have = onwalk | extra
    extra = {k for k in extra
             if not E[k]["tids"]
             or all(any(r in have for r in ix.results.get(t, ())) for t in E[k]["tids"])}
    rest = sorted(set(tail) | extra, key=lambda k: E[k]["i"])
    return head + rest, endb, [k for k in head if k in set(P)]


def _trim_preserved(o: dict, kept: list) -> None:
    """分叉只带上了被保留消息的前一段时,把边界的元数据改成只列这一段。

    分叉文件的 parentUuid 已经线性化,加载期重链本来是个空操作;但它读的是这里的
    uuids 列表。列表里留着分叉文件中不存在的 uuid,「摘要的其他孩子改挂到最后一个
    被保留消息上」就会挂到一个不存在的节点上。整段都带上了就一个字不改,和 /branch 一样。
    """
    cm = o.get("compactMetadata")
    pm = cm.get("preservedMessages") if isinstance(cm, dict) else None
    if not isinstance(pm, dict) or not isinstance(pm.get("uuids"), list):
        return
    if [u for u in pm["uuids"] if isinstance(u, str)] == kept:
        return
    keep = set(kept)
    pm["uuids"] = list(kept)
    if isinstance(pm.get("allUuids"), list):
        pm["allUuids"] = [u for u in pm["allUuids"] if u in keep]
    seg = cm.get("preservedSegment")
    if isinstance(seg, dict):
        if not kept:
            cm.pop("preservedSegment")
        else:
            seg["tailUuid"] = kept[-1]
            if seg.get("headUuid") not in keep:
                seg["headUuid"] = kept[0]
    o["forkTrimmedPreserved"] = True


def _dump(o) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def fork(sid, at, leaf=None, sub=None, root=None) -> dict:
    """在节点 at 处分叉出一个新会话文件。只写一份新文件,源文件只读。"""
    shape(sid, sub=sub, leaf=leaf, required=("at",), at=at)
    if sub:
        raise Refused("子代理的转录不能分叉成会话", "no_sub_fork")
    try:
        loc, ix, _cached = _load(sid, None, root)
    except Unavailable as e:
        raise Refused(str(e), "unavailable") from e
    E = ix.E
    leafk, _ = _leaf_k(ix, leaf)
    order, _segof, _c, _w = _path(ix, leafk)
    atk = ix.by.get(at)
    if atk is None or atk not in set(order):
        raise Refused("分叉点不在当前显示的链上", "not_on_path")
    ks, endb, pres_used = _fork_chain(ix, atk)
    raws = _read_lines(ix, ks)
    kept_pres = [E[k]["u"] for k in pres_used]

    new = str(_uuid.uuid4())
    lines, prev, emitted_bytes, last_ua = [], None, 0, None
    for k in ks:
        o = _obj(raws[k], E[k]["u"])
        o["sessionId"] = new
        if "session_id" in o:
            o["session_id"] = new
        o["parentUuid"] = prev
        o["isSidechain"] = False
        o.pop("sessionKind", None)
        if o.get("type") == "system" and o.get("subtype") == "model_refusal_fallback":
            o["neutralizedByFork"] = True
        if k == endb:
            _trim_preserved(o, kept_pres)
        o["forkedFrom"] = {"sessionId": sid, "messageUuid": E[k]["u"]}
        s = _dump(o)
        emitted_bytes += len(s.encode("utf-8")) + 1
        lines.append(s)
        prev = E[k]["u"]
        if o.get("type") in ("user", "assistant"):
            last_ua = E[k]["u"]
    if last_ua is None:
        raise Refused("分叉出的链里没有任何用户或助手消息,Claude Code 恢复不了它", "empty_fork")
    warnings = []
    if ix.replacements:
        merged, badr = [], 0
        with _open_bin(ix.path, ix.gz) as fh:
            for off, n in ix.replacements:
                fh.seek(off)
                try:
                    reps = json.loads(fh.read(n)).get("replacements")
                    if not isinstance(reps, list):
                        raise ValueError
                    merged.extend(reps)
                except (ValueError, AttributeError):
                    badr += 1
        if badr:
            warnings.append(f"{badr} 条 content-replacement 记录读不懂,没有并进分叉")
        lines.append(_dump({"type": "content-replacement", "sessionId": new,
                            "replacements": merged}))
    title = _fallback_title(ix, sid)
    lines.append(_dump({"type": "custom-title", "customTitle": f"{title} (fork @ {at[:8]})",
                        "sessionId": new}))
    lines.append(_dump({"type": "last-prompt", "leafUuid": last_ua, "explicit": True,
                        "sessionId": new}))
    if E[atk]["t"] not in ("user", "assistant"):
        warnings.append("分叉点不是用户或助手消息,恢复时会停在它之前最近的那一条")
    if endb is None and emitted_bytes > BIG_FORK_BYTES:
        warnings.append(f"分叉里没有压缩边界而且有 {emitted_bytes // 1024} KB,"
                        "大概率超出模型上下文")
    cwd = E[atk]["cwd"] or next((E[k]["cwd"] for k in reversed(ks) if E[k]["cwd"]), None)
    if not cwd:
        warnings.append("转录里没有记录目录,恢复前要自己 cd 到原来的目录")

    d = loc["main"].parent
    repo = _enclosing_worktree(d)
    if repo is not None:
        raise Refused(f"会话目录在一个 git 工作树里({repo}),分叉写出的是真实对话内容,"
                      "拒绝写进任何仓库", "inside_repo")
    target = d / f"{new}.jsonl"
    # 临时名不以 .jsonl 结尾:会话面板按 *.jsonl 扫,半截文件不该出现在列表里。
    tmp = d / f".fork-{new}.tmp"
    data = ("\n".join(lines) + "\n").encode("utf-8")
    try:
        with open(tmp, "xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            # 硬链接 = 带着完整内容的独占创建:目标已存在就失败,不存在就一步到位。
            os.link(tmp, target)
        except FileExistsError:
            raise Refused("目标会话文件已存在,拒绝覆盖", "exists")
        except OSError:
            # 卷不支持硬链接时退到独占创建:仍然是「已存在就失败」,只是不再原子。
            try:
                with open(target, "xb") as fh:
                    fh.write(data)
            except FileExistsError:
                raise Refused("目标会话文件已存在,拒绝覆盖", "exists")
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            # 删不掉不吞:会话目录里留下一份完整对话的副本,人得知道它在哪。
            # 名字不以 .jsonl 结尾,所以它不会被当成一个会话列出来。
            warnings.append(f"临时文件没删掉({type(e).__name__}),请手动删除: {tmp}")
    return {"newId": new, "file": str(target), "cwd": cwd, "lines": len(lines),
            "emitted": len(ks), "leafUuid": last_ua,
            "fromBoundary": E[endb]["u"] if endb is not None else None,
            "approxTokens": emitted_bytes // 4,
            "command": _resume_command(cwd, new, warnings),
            "warnings": warnings}


def _enclosing_worktree(d: Path):
    """d 或它的任一上级里有 .git(目录或文件,子模块和 worktree 的 .git 是文件)就返回那一级。

    不去问那个仓是公开还是私有:那张表在这台机器的私有配置里,公开仓的代码不该知道它在哪。
    问不出来就按公开处理,和 PII 闸门对未知 remote 的做法一样。会话根目录本来就不在
    任何仓里,所以这道闸在正常使用中永远不开火。"""
    try:
        cur = Path(os.path.realpath(d))
    except OSError:
        return d
    for p in (cur,) + tuple(cur.parents):
        if (p / ".git").exists():
            return p
    return None


# 这些字符在 PowerShell 或 POSIX shell 的单引号外面、或者单引号本身里有意义。
# 目录名里出现任何一个就不拼 cd:给人一条「粘贴运行」的命令,它就必须在两种 shell 里
# 都只是一个 cd,而不是 $( ) 或反引号里的另一条命令。
_UNSAFE_CWD = re.compile(r"['`$\x00-\x1f\x7f]")


def _resume_command(cwd, new: str, warnings: list) -> str:
    if not cwd:
        return f"claude --resume {new}"
    if _UNSAFE_CWD.search(cwd):
        warnings.append("目录名里有引号、$、反引号或控制字符,没有把 cd 拼进命令;"
                        "先自己进到那个目录再运行")
        return f"claude --resume {new}"
    return f"cd '{cwd}'; claude --resume {new}"
