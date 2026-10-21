"""Optional console component endpoints; task control stays in server and lifecycle.

Providers are imported when requested, so an unavailable component does not stop
startup or prevent the task pages from loading.
"""
from __future__ import annotations

import json
import os
import threading
import time
from urllib.parse import parse_qs, urlparse

CONVO_BODY_MAX = 8192

CONVO_DRAIN_MAX = CONVO_BODY_MAX * 16

CONVO_CHAIN_KEYS = frozenset(("id", "leaf", "sub"))

CONVO_NODE_KEYS = frozenset(("id", "u", "sub"))

CONVO_EXPORT_KEYS = frozenset(("id", "to", "from", "leaf", "sub", "tools", "thinking"))

CONVO_FORK_KEYS = frozenset(("id", "at", "leaf", "sub", "requestId"))

CONVO_LIST_KEYS = frozenset(("group", "cursor", "q", "human_only", "limit"))

CONVO_RENAME_KEYS = frozenset(("id", "title", "expectedProject"))

CONVO_MOVE_KEYS = frozenset(("id", "targetProject", "expectedProject"))

CONVO_DELETE_PLAN_KEYS = frozenset(("id", "expectedProject"))

CONVO_DELETE_KEYS = CONVO_DELETE_PLAN_KEYS | frozenset(("fingerprint", "requestId", "confirmed"))

CONVO_SUGGEST_KEYS = frozenset(("id", "expectedProject"))

CONVO_UNLOCKED_POSTS = frozenset(("/api/convo/suggest-title",))

_CONVO_ACCESS = threading.RLock()

CONVO_ROOT_UNSET = "没有设 BACKGROUND_TASK_CORE_SESSIONS,对话链读不了,这一栏是「未检查」。"

_LEDGER_CACHE: dict = {}

_LEDGER_LOCK = threading.Lock()


def _convo_root():
    return os.environ.get("BACKGROUND_TASK_CORE_SESSIONS") or None


def _convo_read(res, root):
    """读侧:库对没给根目录答 200 + available:false(未检查),这里只把原因换成点名变量的那句。"""
    if root is None and isinstance(res, dict) and res.get("available") is False:
        return {"available": False, "reason": CONVO_ROOT_UNSET}
    return res


def _convo_refused(e, root):
    """库拒绝的请求 → 400 正文。码原样透传;没设根目录的 unavailable 换成点名变量的措辞。"""
    msg = CONVO_ROOT_UNSET if e.code == "unavailable" and root is None else str(e)
    return {"error": msg, "code": e.code}


def llm_records():
    """读账本,带失效缓存。返回 (records, meta)。"""
    from . import llmstats
    with _LEDGER_LOCK:
        p = llmstats.ledger_path()
        if p is None:
            _LEDGER_CACHE.clear()
            return llmstats.read()
        try:
            st = os.stat(p)
            key = (str(p), st.st_size, st.st_mtime_ns)
        except OSError:
            key = (str(p), None, None)
        if _LEDGER_CACHE.get("key") == key:
            return _LEDGER_CACHE["recs"], _LEDGER_CACHE["meta"]
        recs, meta = llmstats.read()
        # Failed reads remain retryable even if the file's size and mtime do not change.
        if not meta.get("read_error"):
            _LEDGER_CACHE.update(key=key, recs=recs, meta=meta)
        else:
            _LEDGER_CACHE.clear()
        return recs, meta


def llm_overview() -> dict:
    """调用屏的总览。

    窗口的选取是有讲究的。「今天」按**本地零点**算而不是「最近 24 小时」:
    人问「今天用了多少」的时候问的是日历上的今天,而一个滑动 24 小时窗口
    在早上八点看会把昨天下午的量算进来,那个数字对不上任何人的直觉。
    """
    from . import llmstats
    recs, meta = llm_records()
    now = time.time()
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    wins = []
    for label, hours in (("今天", (now - midnight) / 3600.0), ("近 7 日", 168.0)):
        inside, unstamped, _out = llmstats.window(recs, hours, now=now)
        agg = llmstats.aggregate(inside)
        agg["label"] = label
        agg["hours"] = round(hours, 3)
        agg["unstamped_excluded"] = unstamped
        wins.append(agg)
    life = llmstats.aggregate(recs)
    life["label"] = "全部历史"
    life["hours"] = None
    life["unstamped_excluded"] = 0
    wins.append(life)

    # 手上已经有解析好的 recs 就传下去。不传的话 chain_config 会自己再去扫一遍账本
    # 尾部, 结果一样,但那是同一份文件在同一个请求里被读了两次,
    # 而两次读之间文件可能已经变了,于是页面上的「最近一次调用」和明细表的最后一行
    # 会指向不同的记录,且没有任何东西说得出这一点。
    chain = llmstats.chain_config(recs)
    # verify 不是装饰。rungs 那张表的每一个数都建在「chain[attempts-1] 就是应答那一级」
    # 这条判据上,而判据垮掉的时候那张表照样画得很漂亮, 一张建在坏判据上的漂亮的表,
    # 和一张对的表,在页面上长得一模一样。所以反例数要跟着表一起送上去。
    return {"ledger": meta, "windows": wins, "chain": chain,
            "rungs": llmstats.rungs(recs, chain.get("effective") or []),
            "verify": llmstats.verify_rungs(recs),
            "callers": llmstats.callers(recs),
            "runs": llmstats.runs(recs)}


class ComponentRoutes:
    component_get_routes = frozenset((
        '/api/convos',
        '/api/work/context',
        '/api/codex/list',
        '/api/llmcall/calls',
        '/api/convo/chain',
        '/api/convo/export',
        '/api/convo/node',
        '/api/maint',
    ))
    component_post_routes = {
        '/api/repo/plan': '_repo_plan',
        '/api/codex/delete': '_codex_delete',
        '/api/maint/act': '_maint_act',
        '/api/maintenance/plan': '_maintenance_delete',
        '/api/maintenance/delete': '_maintenance_delete',
        '/api/llmcall/chain': '_llm_chain',
        '/api/convo/fork': '_convo_fork',
        '/api/convo/rename': '_convo_edit',
        '/api/convo/move': '_convo_edit',
        '/api/convo/delete-plan': '_convo_delete',
        '/api/convo/delete': '_convo_delete',
        '/api/convo/suggest-title': '_convo_suggest_title',
    }

    def component_get(self, path):
        if path == '/api/work/context':
            if not self._authed():
                return self._json(403, {'error': 'bad token'})
            query = parse_qs(urlparse(self.path).query)
            item_id = (query.get('item_id') or [''])[0]
            if not item_id or len(item_id) > 300:
                return self._json(400, {'error': 'item_id is required'})
            from . import integrations
            return self._json(200, integrations.context_view(item_id))

        if path == '/api/convos':
            return self._convo_list()

        if path == "/api/codex/list":
            from . import codexinfo
            if not self._authed():
                return self._json(403, {"error": "bad token"})
            q = parse_qs(urlparse(self.path).query)
            which = (q.get("which") or ["sessions"])[0]
            try:
                return self._json(200, codexinfo.list_transcripts(which))
            except Exception as e:
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})

        if path == "/api/llmcall/calls":
            from . import llmstats
            if not self._authed():
                return self._json(403, {"error": "bad token"})
            q = parse_qs(urlparse(self.path).query)
            def _int(k, d, lo, hi):
                try:
                    return max(lo, min(hi, int((q.get(k) or [str(d)])[0])))
                except ValueError:
                    return d
            prov = (q.get("provider") or [""])[0]
            okq = (q.get("ok") or [""])[0]
            ok = None if okq not in ("0", "1") else (okq == "1")
            try:
                recs, _ = llm_records()
                # 搜索词只在错误文本里找,而且截断:一个十一万行的账本上,
                # 一个超长的查询串除了让每一行都做一次白费的比较之外没有别的作用。
                needle = (q.get("q") or [""])[0][:200]
                caller = (q.get("caller") or [""])[0][:200]
                # 不认识的顺序按旧行为走正序,而不是报错:旧页面不带这个参数,也得照常翻页。
                order = "desc" if (q.get("order") or [""])[0] == "desc" else "asc"
                rows, total = llmstats.page(recs, offset=_int("offset", 0, 0, 10 ** 9),
                                            limit=_int("limit", 50, 1, 200),
                                            provider=(prov or None), ok=ok,
                                            q=(needle or None), caller=(caller or None),
                                            order=order)
                return self._json(200, {"rows": rows, "total": total})
            except Exception as e:
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})

        if path.startswith("/api/llmcall/call/"):
            from . import llmstats
            if not self._authed():
                return self._json(403, {"error": "bad token"})
            raw = path[len("/api/llmcall/call/"):]
            # 索引是**全账本的绝对行号**,所以它必须是纯数字。不先按形状拒绝的话,
            # 这个值会一路走到正文查找里去,而那边要拿它去碰文件系统。
            if not raw.isdigit():
                return self._json(400, {"error": "bad index"})
            try:
                recs, _ = llm_records()
                return self._json(200, llmstats.body(int(raw), recs))
            except Exception as e:
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})

        if path == "/api/convo/chain":
            import convo_chain
            if not self._authed():
                return self._json(403, {"error": "bad token"})
            q = self._convo_query(CONVO_CHAIN_KEYS)
            if q is None:
                return None
            root = _convo_root()
            try:
                convo_chain.shape(q.get("id"), sub=q.get("sub"), leaf=q.get("leaf"))
                return self._json(200, _convo_read(convo_chain.chain(
                    q.get("id"), leaf=q.get("leaf"), sub=q.get("sub"), root=root), root))
            except convo_chain.ConvoChainError as e:
                return self._json(400, _convo_refused(e, root))
            except Exception as e:
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})

        if path == "/api/convo/export":
            import convo_chain
            # 导出是读:不写任何文件,Markdown 装在 JSON 里由页面自己存。做成 POST 的话,
            # 只读预览里导不了,每次导出还会记一条「操作」、让所有读缓存失效。
            if not self._authed():
                return self._json(403, {"error": "bad token"})
            q = self._convo_query(CONVO_EXPORT_KEYS)
            if q is None:
                return None
            flags = {}
            for k in ("tools", "thinking"):
                v = q.get(k)
                if v not in (None, "0", "1"):
                    return self._json(400, {"error": f"{k} 只能是 0 或 1", "code": "bad_query"})
                flags[k] = v == "1"
            root = _convo_root()
            try:
                # 形状闸在碰文件系统之前:规则只有 convo_chain.shape 这一份。
                convo_chain.shape(q.get("id"), sub=q.get("sub"), leaf=q.get("leaf"),
                                  required=("to",), to=q.get("to"), frm=q.get("from"))
                return self._json(200, convo_chain.export_md(
                    q.get("id"), q.get("to"), frm=q.get("from"), leaf=q.get("leaf"),
                    sub=q.get("sub"), include_tools=flags["tools"],
                    include_thinking=flags["thinking"], root=root))
            except convo_chain.ConvoChainError as e:
                return self._json(400, _convo_refused(e, root))
            except Exception as e:
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})

        if path == "/api/convo/node":
            import convo_chain
            if not self._authed():
                return self._json(403, {"error": "bad token"})
            q = self._convo_query(CONVO_NODE_KEYS)
            if q is None:
                return None
            root = _convo_root()
            try:
                convo_chain.shape(q.get("id"), sub=q.get("sub"), required=("u",), u=q.get("u"))
                return self._json(200, _convo_read(convo_chain.node(
                    q.get("id"), q.get("u"), sub=q.get("sub"), root=root), root))
            except convo_chain.ConvoChainError as e:
                return self._json(400, _convo_refused(e, root))
            except Exception as e:
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})

        if path == "/api/maint":
            from . import maint
            if not self._authed():
                return self._json(403, {"error": "bad token"})
            try:
                return self._json(200, maint.read_all())
            except Exception as e:
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})

        return self._json(404, {"error": "not found"})

    def component_post(self, route):
        return getattr(self, self.component_post_routes[route])()

    def _repo_plan(self):
        """只读预览:这次提交推送会送出去什么。写之前先让人看见要写什么。

        它是唯一会把东西送出这台机器的动作,所以计划里除了文件清单,
        还要说清推到哪个 ref、那个 remote 是公开还是私有。
        """
        from . import maint
        from . import repos as repos_mod
        if not self._authed():
            self._drain()
            return self._json(403, {"error": "bad token"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._json(400, {"error": f"bad request: {e}"})
        try:
            return self._json(200, repos_mod.commit_push_plan(str(body.get("name") or "")))
        except maint.Refused as e:
            return self._json(400, {"error": str(e), "code": e.code})
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def _codex_delete(self):
        """点名删掉几份转录。不可逆。

        不走 /api/maint/act 那张动作表是刻意的:那张表里每个动作只收一个名字,
        而这个收的是一批路径。把一个收路径列表的动作塞进那张表,
        等于让表里其余每一个动作也多出一条「其实可以传路径」的可能。
        """
        from . import codexinfo
        from . import maint
        if not self._authed():
            self._drain()
            return self._json(403, {"error": "bad token"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._json(400, {"error": f"bad request: {e}"})
        try:
            return self._json(200, codexinfo.delete_transcripts(body.get("rels") or []))
        except maint.Refused as e:
            return self._json(400, {"error": str(e), "code": e.code})
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def _convo_query(self, allowed):
        """对话链 GET 的查询串:键在白名单里,每个键只给一次。返回 dict 或 None(已回 400)。

        重复的键不取第一个:`id=a&id=b` 被默默读成 a,而日志和人看到的是两个值。"""
        q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        if not set(q) <= allowed or any(len(v) != 1 for v in q.values()):
            self._json(400, {"error": "查询参数不对", "code": "bad_query"})
            return None
        return {k: (v[0] or None) for k, v in q.items()}

    def _convo_body(self, allowed):
        """对话链 POST(分叉)的读体。返回 body 或 None(已经回过 4xx)。
        鉴权留在各自的处理器里第一行:路由鉴权通扫是按处理器正文找 `_authed()` 的。"""
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            self._json(400, {"error": "invalid request size", "code": "bad_size"})
            return None
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            n = -1
        if n > CONVO_BODY_MAX:
            if n <= CONVO_DRAIN_MAX:
                self._drain()
            else:
                self.close_connection = True
            self._json(413, {"error": f"请求体超过 {CONVO_BODY_MAX} 字节", "code": "too_large"})
            return None
        if n <= 0:
            self.close_connection = True
            self._json(400, {"error": "invalid request size", "code": "bad_size"})
            return None
        try:
            body = json.loads(self.rfile.read(n))
        except (ValueError, UnicodeError):
            self._json(400, {"error": "invalid JSON request", "code": "bad_json"})
            return None
        if not isinstance(body, dict) or not set(body) <= allowed:
            self._json(400, {"error": "请求体不是对象,或带了不认识的键", "code": "bad_body"})
            return None
        return body

    def _convo_fork(self):
        """在一个节点处分叉出新会话文件。写的是一份新文件,独占创建,源转录只读。

        不进 /api/maint/act 那张动作表:那张表的每个动作收一个名字,这个收的是
        (会话, 节点, 叶子) 三个 id,塞进去等于让表里每个动作都多一种参数形状。
        """
        import convo_chain
        if not self._authed():
            self._drain()
            return self._json(403, {"error": "bad token"})
        body = self._convo_body(CONVO_FORK_KEYS)
        if body is None:
            return None
        root = _convo_root()
        try:
            convo_chain.shape(body.get("id"), sub=body.get("sub"), leaf=body.get("leaf"),
                              required=("at",), at=body.get("at"))
            return self._json(200, convo_chain.fork(body.get("id"), body.get("at"),
                                                    leaf=body.get("leaf") or None,
                                                    sub=body.get("sub") or None, root=root,
                                                    request_id=body.get("requestId")))
        except convo_chain.ConvoChainError as e:
            # 写侧没有根目录是硬失败(400 unavailable),不是「未检查」:库自己这样判。
            return self._json(400, _convo_refused(e, root))
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def _convo_edit(self):
        import convo_chain
        from convo_chain import session_ops
        if not self._authed():
            self._drain()
            return self._json(403, {"error": "bad token"})
        rename = self.path.split("?", 1)[0].endswith("/rename")
        body = self._convo_body(CONVO_RENAME_KEYS if rename else CONVO_MOVE_KEYS)
        if body is None:
            return None
        root = _convo_root()
        try:
            convo_chain.shape(body.get("id"))
            expected = body.get("expectedProject")
            if expected is not None and (not isinstance(expected, str) or len(expected) > 255):
                raise convo_chain.ConvoChainError("来源项目标识不正确", "bad_project")
            operation = session_ops.rename if rename else session_ops.move
            result = operation(body.get("id"), body.get("title" if rename else "targetProject"),
                               root=root, expected_project=expected)
            return self._json(200, result)
        except convo_chain.Unavailable as error:
            return self._json(400, {"error": CONVO_ROOT_UNSET if root is None else str(error), "code": "unavailable"})
        except convo_chain.ConvoChainError as error:
            status = 409 if error.code in ("busy", "exists", "conflict", "recovery_conflict", "ambiguous") else 400
            return self._json(status, _convo_refused(error, root))

    def _convo_delete(self):
        import convo_chain
        if not self._authed():
            self._drain()
            return self._json(403, {"error": "bad token"})
        preview = self.path.split("?", 1)[0].endswith("/delete-plan")
        body = self._convo_body(CONVO_DELETE_PLAN_KEYS if preview else CONVO_DELETE_KEYS)
        if body is None:
            return None
        root = _convo_root()
        try:
            if preview:
                result = convo_chain.delete_plan(body.get("id"), root=root,
                                                expected_project=body.get("expectedProject"))
            else:
                if body.get("confirmed") is not True:
                    raise convo_chain.ConvoChainError("请先查看删除范围并确认永久删除", "confirmation_required")
                result = convo_chain.delete(body.get("id"), root=root,
                    expected_project=body.get("expectedProject"), fingerprint=body.get("fingerprint"),
                    request_id=body.get("requestId"))
            return self._json(200, result)
        except convo_chain.Unavailable as error:
            return self._json(400, {"error": CONVO_ROOT_UNSET if root is None else str(error), "code": "unavailable"})
        except convo_chain.ConvoChainError as error:
            status = 409 if error.code in ("busy", "conflict", "recovery_conflict", "cleanup_pending", "ambiguous") else 400
            return self._json(status, _convo_refused(error, root))

    def _convo_suggest_title(self):
        """给一场会话起名的建议。只读不写:返回 {title, provider},保存仍走 /api/convo/rename。

        读转录在对话锁里(和改名、迁移、删除互斥,读到的不会是写了一半的文件);
        等模型在锁外,一次几十秒的调用不该挡住别的会话操作。
        """
        from . import title_suggest
        import convo_chain
        if not self._authed():
            self._drain()
            return self._json(403, {"error": "bad token"})
        body = self._convo_body(CONVO_SUGGEST_KEYS)
        if body is None:
            return None
        root = _convo_root()
        try:
            with _CONVO_ACCESS:
                convo_chain.shape(body.get("id"))
                expected = body.get("expectedProject")
                if expected is not None and (not isinstance(expected, str) or len(expected) > 255):
                    raise convo_chain.ConvoChainError("来源项目标识不正确", "bad_project")
                loc = convo_chain.locate(body.get("id"), root=root)
                if expected is not None and loc["projectDir"].name != expected:
                    raise convo_chain.ConvoChainError("会话位置已改变，请刷新后再操作", "conflict")
                digest = title_suggest.collect_digest(loc["main"])
            return self._json(200, title_suggest.suggest(digest, digest["title"]))
        except title_suggest.SuggestError as error:
            return self._json(error.status, {"error": str(error), "code": error.code})
        except convo_chain.Unavailable as error:
            return self._json(400, {"error": CONVO_ROOT_UNSET if root is None else str(error), "code": "unavailable"})
        except convo_chain.ConvoChainError as error:
            status = 409 if error.code in ("conflict", "ambiguous") else 400
            return self._json(status, _convo_refused(error, root))
        except OSError as error:
            return self._json(409, {"error": f"会话文件暂时读不了：{type(error).__name__}", "code": "busy"})

    def _convo_list(self):
        from . import convos
        if not self._authed():
            return self._json(403, {"error": "bad token"})
        query = self._convo_query(CONVO_LIST_KEYS)
        if query is None:
            return None
        try:
            if query.get("human_only") not in (None, "0", "1"):
                raise ValueError("human_only must be 0 or 1")
            result = convos.scan(group=query.get("group"), cursor=query.get("cursor"),
                query=query.get("q") or "", human_only=query.get("human_only") == "1",
                limit_per_group=int(query.get("limit") or "40"))
            return self._json(200, result)
        except ValueError as error:
            return self._json(400, {"error": str(error), "code": "bad_query"})

    def _llm_chain(self):
        """改降级链的顺序。

        这是这台控制台上少数几个「改的东西不在这台控制台里」的动作之一 :
        它写的是 llmcall 自己的配置文件,而 llmcall 是全 fleet 的判断基元。
        所以名字闸门在 llmstats.write_chain 里,整批拒绝,不做部分写入 ——
        一个写进去一半的链,比一个没写进去的链危险得多。
        """
        from . import llmstats
        from . import maint
        if not self._authed():
            self._drain()
            return self._json(403, {"error": "bad token"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._json(400, {"error": f"bad request: {e}"})
        try:
            return self._json(200, llmstats.write_chain(body.get("chain") or []))
        except maint.Refused as e:
            return self._json(400, {"error": str(e), "code": e.code})
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def _maintenance_delete(self):
        from . import maint
        from . import deletion
        if not self._authed():
            self._drain()
            return self._json(403, {"error": "bad token"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= 8192 or self.headers.get("Transfer-Encoding"):
                self.close_connection = True
                return self._json(400, {"error": "invalid request size"})
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                return self._json(400, {"error": "request must be an object"})
            if self.path.split("?", 1)[0].endswith("/plan"):
                if any(not isinstance(body.get(key), str) for key in ("kind", "name", "location")):
                    return self._json(400, {"error": "kind, name and location must be strings"})
                result = deletion.plan(body["kind"], body["name"], body["location"])
            else:
                result = deletion.apply(body.get("token"))
            return self._json(200, result)
        except maint.Refused as error:
            return self._json(409, {"error": str(error), "code": error.code})
        except (ValueError, UnicodeError):
            return self._json(400, {"error": "invalid JSON request"})
        except Exception as error:
            return self._json(500, {"error": f"{type(error).__name__}: {error}"})

    def _maint_act(self):
        """维护动作。和 /api/act 分开是刻意的:两张动作表混在一起,加一个 skill 动作
        就等于同时扩大了任务动作的表面,而没有人会在评审时注意到这一点。"""
        from . import maint
        from . import lifecycle as task_control
        if not self._authed():
            self._drain()
            return self._json(403, {"error": "bad token"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._json(400, {"error": f"bad request: {e}"})
        try:
            if not isinstance(body, dict):
                return self._json(400, {"error": "request must be an object"})
            return self._json(200, maint.act(str(body.get("action") or ""),
                                             str(body.get("name") or ""), body.get("arg")))
        except task_control.ContractError as e:
            return self._json(400, task_control.error_result(e))
        except maint.Refused as e:
            return self._json(400, {"error": str(e), "code": e.code})
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

