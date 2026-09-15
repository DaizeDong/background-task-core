"""llmcall 账本的只读视图:谁真的应答了、谁被跳过了、降级挤在哪一段。

这个模块一个字节都不往账本里写,也不 import llmcall 的任何东西 —— 控制台不能
因为另一个仓没装就整块塌掉。它防的是下面这六种「看起来正常」的沉默失败,
每一条都对应代码里一处刻意的别扭:

**解析器默默丢行。** 一个把半数行吞掉的读取器,和一个真的只有半数数据的账本,
打印出来的总数一模一样。所以坏行进 `malformed` 计数,跟着 meta 一路回到页面上,
`parsed + malformed + blank == lines` 是可以当场验算的恒等式。

**没有时间戳的记录被当成「在窗口内」。** 存量的十几万条老记录一个 `ts` 都没有。
按时间取窗时它们既不能算进来(那是凭空捏造时间),也不能悄悄消失(那会让
「近 24 小时零调用」和「近 24 小时的数据还没开始记」长得一样)。所以 window()
返回的第三个数是 `unstamped_excluded`,它必须被显示出来。

**served / failed / skipped 被揉成一个数。** 一级链路「应答了」「上场了但没成」
「压根没上场」是三件不同的事,合并之后最惨的一种恰好看不见:`codexg` 不在本次
chain 里的那些调用,今天在任何地方都读不出来,而它就是「为什么便宜档没用上」
的全部答案。

**日均值把故障形状抹平。** 落到低档的调用如果均匀分布,那是配额偏紧;如果挤在
一个连续段里,那是一次故障。两者的日均值相同。runs() 存在的唯一理由就是把这个
区别画出来。

**calls 为 0 时占比算成 0。** 「便宜档占比 0%」和「今天根本没有调用」是两回事,
前者要去查,后者不用。所以 cheap_share 在没有样本时是 None。

**写进配置文件的顺序被环境变量压着,而按钮照样显示成功。** chain_config() 因此
如实报告来源三件套,`shadowed_by_env` 为真时那个输入框就是个假按钮,必须说出来。

记录的两种形状都要认:成功记录有 `ms` 没有 `error`,失败记录有 `error` 没有 `ms`
(写侧正在补 `ts` / `ms` / `id`,所以新旧记录会在同一个文件里长期共存)。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections import Counter, deque
from pathlib import Path

# 读取器给每条记录挂上的派生字段,下划线开头表示「不是账本里的」。
# `_i` 是这条记录在账本里的绝对物理行号(1 起)。用行号而不是「第几条解析成功的」
# 是刻意的:中间夹一条坏行时,前者仍然指向文件里同一行,后者会整体错位一格。
INDEX_KEY = "_i"

# 整条链都没成时,「谁应答的」这一格填这个。留空或填 None 会和「字段缺失」混在一起,
# 而那两件事要采取的行动不同。
NONE_SERVED = "NONE"

# 便宜档。cheap_share 问的是「有多大比例的调用没花到贵的那两档上」。
CHEAP = ("codexg", "codex")

# 调用方的两种「没有名字」。**它们绝不能合并成一项。**
# 「记了但推断不出来」说明写侧的推断逻辑该修;「还没开始记」说明这个功能刚上、
# 数据还在积累,什么都不用做。合成一项之后,一个刚上线的字段会看起来像
# 「推断全都失败了」,而那会把人送去修一个没坏的东西。
CALLER_UNKNOWN = "(推断不出)"   # 有 caller 这个键,但值是 null / 空 / 不是字符串
CALLER_LEGACY = "(早于此字段)"   # 根本没有 caller 这个键(账本里的存量记录)

# 最后兜底的内置链。**这是一份会漂的拷贝,不是权威。**
#
# 权威在 llmcall 仓里,而控制台不 import 它(两个仓不该互相依赖)。于是这个常量
# 从写下的第一天起就在漂:实测过一次,llmcall 那边已经改成了另一个顺序,而页面
# 顶着「内置默认」的徽章展示了一个 llmcall 实际不会用的链 —— 那个徽章看起来很权威,
# 而两份常量只在有人同时打开两个仓时才对得上号。
#
# 所以它的角色被降到最后一级:**只要账本里有过一条真实记录,就以记录为准**
# (记录里的 chain 是运行时实际走的那条,是证据不是拷贝)。只有一台从没调用过的
# 全新机器才会看见这个常量,而在那种机器上它错了也没有任何东西受影响。
#
# 对应地,这里**不设**「两个常量必须相等」的对账测试:那种闸本身就是在承认有两份。
DEFAULT_CHAIN = ("codexg", "codex", "cc", "claude")

# 观测链时从账本尾部回看多少行。只保留原始文本,回看时才逐行解析,
# 所以这一步几乎不花解析成本;取 200 是为了让尾部即使连着几十条坏行也仍能命中。
OBSERVE_TAIL = 200

# 链路名字的形状闸。小写起头、长度上限 32,和文件名与环境变量的可用字符对齐。
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

# 环境变量名。账本与链配置各有一个显式覆盖,都是为了让测试不碰真机文件。
ENV_LEDGER = "TASK_CONSOLE_LLMCALL_LEDGER"
ENV_CHAIN_FILE = "TASK_CONSOLE_LLMCALL_CHAIN"
# 这一个不是控制台的,是 llmcall 自己的:它压过配置文件,所以控制台必须认它。
ENV_CHAIN = "LLMCALL_CHAIN"
# 正文目录。控制台的显式覆盖优先,其余几步复刻 llmcall 那边的伴生仓解析顺序。
ENV_BODIES = "TASK_CONSOLE_LLMCALL_BODIES"
ENV_LLMCALL_DATA = "LLMCALL_DATA_DIR"
ENV_LLMCALL_CONFIG = "LLMCALL_CONFIG"

# 一条链最多几级。没有上限时,一个手滑粘进来的几千项列表会被原样写进配置文件。
MAX_CHAIN = 16

# 正文的单侧字符上限。超过就截断,但**必须在返回里说出来**:
# 一段被悄悄砍掉一半的 prompt,和一段本来就那么长的 prompt,在页面上长得一样。
BODY_MAX_CHARS = 20000


class Refused(Exception):
    """动作被闸门拒绝。形状与同目录 maint.Refused 一致,服务端按同一套路由。

    带 code 不只是为了好看:write_chain 上有四道闸互相兜底,「抛了 Refused」
    证明不了是哪一道抛的 —— 放开名字闸之后重复闸仍然挡得住,于是两个只断言
    「抛异常」的测试在各自的投毒下都照样全绿。code 让每条用例钉住自己那道闸。
    """

    def __init__(self, msg: str, code: str = "refused"):
        super().__init__(msg)
        self.code = code


# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------

def ledger_path() -> Path:
    """账本路径。**不检查存在性,也不因为找不到而抛。**

    存在与否是调用方要显示的事实,不是这里要替它决定的。在这里抛异常会让
    「还没装 llmcall」变成一次 500,而正确的画面是一块写着「未初始化」的板子。
    """
    v = os.environ.get(ENV_LEDGER)
    if v:
        return Path(os.path.expanduser(v))
    return Path(os.path.expanduser("~")) / ".llmcall" / "ledger.jsonl"


def chain_file_path() -> Path:
    """链配置文件路径:显式覆盖 > 账本同目录下的 chain.txt。

    默认账本是 `~/.llmcall/ledger.jsonl`,所以默认链文件正好是 `~/.llmcall/chain.txt`。
    跟着账本走而不是各自写死,是为了让一次环境变量重定向把两者一起挪开 ——
    只挪一半会让测试往真机的配置里写东西。
    """
    v = os.environ.get(ENV_CHAIN_FILE)
    if v:
        return Path(os.path.expanduser(v))
    return ledger_path().parent / "chain.txt"


def bodies_resolution() -> dict:
    """解析正文目录,返回 `{dir, source, tried}`。解析不出来时 `dir` 是 None,**不抛**。

    正文落在 llmcall 的**私有伴生仓**里(`<name>-config` 仓,数据在 `<config>/data/` 下),
    不是 `~/.llmcall/` 下的散目录 —— 散目录没有版本历史也没有备份,是这个 fleet
    明确禁止的形态。

    顺序(复刻 llmcall 那边的解析顺序):

    1. `TASK_CONSOLE_LLMCALL_BODIES` 显式覆盖,**设了就用,存不存在都用**
       (设错了要能看见那个路径,悄悄滑到下一级会让人对着一份没配好的环境查保留策略)
    2. `LLMCALL_DATA_DIR` 下的 `bodies/`
    3. `LLMCALL_CONFIG` 下的 `data/bodies/`(它本身以 data 结尾时,就是它下面的 `bodies/`)
    4. `~/.example-tool-config/data/bodies/`
    5. `~/.llmcall-data/bodies/`
    6. 都落空 → `dir` 为 None

    **为什么复刻而不是 import llmcall 的 datadir 解析器:** 控制台是只读的观察方,
    它不该因为另一个仓没装、或者那个仓改了内部结构就整块塌掉。代价是这份顺序会漂,
    所以它写在这里、有用例钉着每一级,而不是散在几处各自记一半。

    **读可降级,不抛。** 写方找不到伴生仓必须硬失败(否则真实观察会被丢掉或退回仓内),
    读方找不到只是「还没初始化」,照实说出来就行 —— 在这里抛会让一台没装 llmcall
    的机器打开控制台就是一次 500。

    `tried` 是走过的每一级和它的结论,给页面打出来用:一个「找不到」不说自己找过哪里,
    人只能去猜。
    """
    tried = []

    v = os.environ.get(ENV_BODIES)
    if v:
        p = Path(os.path.expanduser(v))
        tried.append({"source": ENV_BODIES, "path": str(p), "is_dir": p.is_dir()})
        return {"dir": p, "source": ENV_BODIES, "tried": tried}

    home = Path(os.path.expanduser("~"))
    cands = []

    v = os.environ.get(ENV_LLMCALL_DATA)
    if v:
        cands.append((ENV_LLMCALL_DATA, Path(os.path.expanduser(v)) / "bodies"))

    v = os.environ.get(ENV_LLMCALL_CONFIG)
    if v:
        cfg = Path(os.path.expanduser(v))
        # 已经指到 data 了就不要再套一层 data,否则这一级永远解析不出东西
        base = cfg if cfg.name == "data" else cfg / "data"
        cands.append((ENV_LLMCALL_CONFIG, base / "bodies"))

    cands.append(("~/.example-tool-config", home / ".example-tool-config" / "data" / "bodies"))
    cands.append(("~/.llmcall-data", home / ".llmcall-data" / "bodies"))

    for src, p in cands:
        ok = p.is_dir()
        tried.append({"source": src, "path": str(p), "is_dir": ok})
        if ok:
            return {"dir": p, "source": src, "tried": tried}

    return {"dir": None, "source": None, "tried": tried}


def bodies_dir():
    """正文目录,解析不出来就是 None。细节见 bodies_resolution()。"""
    return bodies_resolution()["dir"]


# --------------------------------------------------------------------------
# 读
# --------------------------------------------------------------------------

def _ts_of(rec: dict):
    """取出 ts,只认能变成 float 的数。认不出就是 None,不猜、不填 0。"""
    v = rec.get("ts")
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def read(limit=None):
    """流式读账本,返回 `(records, meta)`。

    `limit` 只截断**返回的记录**(保留最近的 N 条),不截断统计:meta 里的计数
    永远是整个文件的。两者混在一起就会出现「限流之后总数也跟着变小」这种
    看不出错的错。二十兆的文件因此是逐行读进一个定长 deque,不是整表进内存。

    meta 的键:path / exists / bytes / lines / blank / parsed / malformed /
    stamped / unstamped / first_ts / last_ts / returned / truncated / read_error。

    恒等式 `parsed + malformed + blank == lines` 恒成立,页面可以拿它当场验算。
    """
    p = ledger_path()
    meta = {
        "path": str(p),
        "exists": False,
        "bytes": 0,
        "lines": 0,
        "blank": 0,
        "parsed": 0,
        "malformed": 0,
        "stamped": 0,
        "unstamped": 0,
        "first_ts": None,
        "last_ts": None,
        "returned": 0,
        "truncated": False,
        "read_error": None,
    }

    try:
        st = p.stat()
    except OSError as e:
        # 不存在是「未初始化」,读不了是「有东西挡着」。两者的 read_error 不同,
        # 因为要采取的行动不同:前者去装,后者去查权限。
        meta["read_error"] = None if isinstance(e, FileNotFoundError) else f"{type(e).__name__}: {e}"
        return [], meta
    meta["exists"] = True
    meta["bytes"] = st.st_size

    if limit is not None:
        if not isinstance(limit, int) or limit < 0:
            raise ValueError("limit 必须是非负整数或 None")
        keep = deque(maxlen=limit)
    else:
        keep = []

    lineno = 0
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                lineno += 1
                s = line.strip()
                if not s:
                    meta["blank"] += 1
                    continue
                try:
                    rec = json.loads(s)
                except ValueError:
                    meta["malformed"] += 1
                    continue
                if not isinstance(rec, dict):
                    # 合法 JSON 但不是一条记录(比如一个裸数组)。它同样是坏行,
                    # 不能因为 json.loads 没报错就放它进结果。
                    meta["malformed"] += 1
                    continue
                meta["parsed"] += 1
                rec[INDEX_KEY] = lineno
                ts = _ts_of(rec)
                if ts is None:
                    meta["unstamped"] += 1
                else:
                    meta["stamped"] += 1
                    if meta["first_ts"] is None or ts < meta["first_ts"]:
                        meta["first_ts"] = ts
                    if meta["last_ts"] is None or ts > meta["last_ts"]:
                        meta["last_ts"] = ts
                keep.append(rec)
    except OSError as e:
        # 读到一半挂了:已经读到的那部分照样返回,但必须带着 read_error,
        # 否则一次被截断的读取会被当成「账本就这么长」。
        meta["read_error"] = f"{type(e).__name__}: {e}"

    meta["lines"] = lineno
    out = list(keep)
    meta["returned"] = len(out)
    meta["truncated"] = meta["returned"] < meta["parsed"]
    return out, meta


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------

def _served_name(rec: dict) -> str:
    """这条记录是谁应答的。整链失败记作 NONE_SERVED。"""
    if not rec.get("ok"):
        return NONE_SERVED
    p = rec.get("provider")
    return p if isinstance(p, str) and p else NONE_SERVED


def _num(v):
    """只认真正的数。True 是 int 的子类,必须挡掉,否则 `web: true` 会被当成 1。"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def aggregate(records):
    """一屏总量。

    `cheap_share` 在 calls 为 0 时是 **None**:没有样本时说「便宜档占比 0%」
    是在报告一个从未观测到的结论。字符数与耗时的均值同理,没有样本就是 None。

    `avg_ms` 只对**带 ms 的记录**求均值,并同时给出 `ms_samples`;老失败记录
    没有 ms,拿 calls 当分母会把均值系统性地压低而没有任何地方会说。
    """
    calls = len(records)
    ok = sum(1 for r in records if r.get("ok"))
    served = Counter(_served_name(r) for r in records)
    modes = Counter(str(r.get("mode")) for r in records)

    pc = [x for x in (_num(r.get("prompt_chars")) for r in records) if x is not None]
    rc = [x for x in (_num(r.get("reply_chars")) for r in records) if x is not None]
    ms = [x for x in (_num(r.get("ms")) for r in records) if x is not None]

    def stat(vals):
        if not vals:
            return {"total": 0, "avg": None, "max": None, "samples": 0}
        return {
            "total": int(sum(vals)),
            "avg": sum(vals) / len(vals),
            "max": int(max(vals)),
            "samples": len(vals),
        }

    cheap = sum(served.get(n, 0) for n in CHEAP)
    return {
        "calls": calls,
        "ok": ok,
        "failed": calls - ok,
        "served": dict(served),
        "cheap_share": (cheap / calls) if calls else None,
        "modes": dict(modes),
        "prompt_chars": stat(pc),
        "reply_chars": stat(rc),
        "avg_ms": (sum(ms) / len(ms)) if ms else None,
        "ms_samples": len(ms),
        "web_calls": sum(1 for r in records if r.get("web") is True),
    }


def _caller_of(rec: dict) -> str:
    """这条记录归到哪个调用方名下。三分,不是二分。

    键不在 → `CALLER_LEGACY`;键在但值不是个像样的名字 → `CALLER_UNKNOWN`;
    否则就是那个名字。中间那一档单独存在,是因为「推断失败」和「还没开始记」
    要采取的行动相反。
    """
    if "caller" not in rec:
        return CALLER_LEGACY
    v = rec.get("caller")
    if isinstance(v, str) and v:
        return v
    return CALLER_UNKNOWN


def callers(records):
    """按调用方分组,返回 `[{name, calls, ok, failed}]`,按 calls 降序。

    存在的理由:这台机器上十几个 skill 和计划任务共用同一个 llmcall,而账本至今
    没有任何调用方标识 —— 于是「那段 82 连降级到底拖累了谁」这个问题在页面上
    问不出来。

    **不接受 limit,也绝不在内部截断。** 这张表是页面上那个下拉框的来源,而下拉框
    必须覆盖整个账本:从当前这一页的五十行里凑出来的清单,会让「这个调用方今天
    没出现」显示成「这个调用方不存在」。要少读就在调用方那边少传 records,
    而不是让这个函数假装自己数完了。

    并列时按名字排,让下拉框的顺序稳定 —— 一个每次刷新都换序的下拉框,
    用起来跟坏了没区别。
    """
    agg = {}
    for r in records:
        name = _caller_of(r)
        d = agg.get(name)
        if d is None:
            d = agg[name] = {"name": name, "calls": 0, "ok": 0, "failed": 0}
        d["calls"] += 1
        if r.get("ok"):
            d["ok"] += 1
        else:
            d["failed"] += 1
    return sorted(agg.values(), key=lambda d: (-d["calls"], d["name"]))


# --------------------------------------------------------------------------
# 时间窗
# --------------------------------------------------------------------------

def window(records, hours, now=None):
    """按 ts 取时间窗,返回 `(窗内记录, unstamped_excluded, out_of_window)`。

    **没有 ts 的记录一律排除,并单独计数。** 这是这个文件里最要紧的一条:
    把它们当成「落在窗口内」会让一份十一万条全无时间戳的账本显示成「近一小时
    十一万次调用」;把它们悄悄丢掉又会让同一份账本显示成「近一小时零调用」,
    而真相是「这段时间根本没被记录过」。三个数都返回,调用方没有偷懒的余地。
    """
    if not isinstance(hours, (int, float)) or isinstance(hours, bool) or hours <= 0:
        raise ValueError("hours 必须是正数")
    now = time.time() if now is None else float(now)
    lo = now - hours * 3600.0

    inside, unstamped, outside = [], 0, 0
    for r in records:
        ts = _ts_of(r)
        if ts is None:
            unstamped += 1
        elif lo <= ts <= now:
            inside.append(r)
        else:
            # 未来的时间戳也算窗外。时钟跳变造出来的「刚刚」不该被算成活跃。
            outside += 1
    return inside, unstamped, outside


# --------------------------------------------------------------------------
# 链路重建
# --------------------------------------------------------------------------

def _chain_of(rec: dict):
    c = rec.get("chain")
    if not isinstance(c, list):
        return []
    return [x for x in c if isinstance(x, str) and x]


def _split(rec: dict):
    """把一条记录拆成 (failed_names, served_name_or_None, not_reached_names)。

    判据:`chain` 是本次实际用的链,`attempts` 是试到第几级。成功时
    `chain[:attempts-1]` 全部试过没成、`chain[attempts-1]` 应答;失败时
    `chain[:attempts]` 全部试过没成。`attempts` 之后的那几级没轮到。

    `attempts` 缺失或越界时返回 None,由调用方计入「判不了」—— 猜一个
    attempts 会让这张表看起来完整,而完整正是这里最不该伪造的东西。
    """
    chain = _chain_of(rec)
    a = rec.get("attempts")
    if isinstance(a, bool) or not isinstance(a, int) or a < 1 or a > len(chain):
        return None
    if rec.get("ok"):
        return chain[:a - 1], chain[a - 1], chain[a:]
    return chain[:a], None, chain[a:]


def rungs(records, known_chain):
    """每一级链路的上场情况,返回一个 list(按 known_chain 顺序,末尾接上表外的)。

    每项:`{name, served, failed, skipped, never_in_chain, not_reached, in_known_chain}`。

    - `served` 它应答了几次
    - `failed` 它在本次链里、被试过、没成
    - `skipped` 它**根本不在本次的 chain 里**(那次调用它压根没上场)
    - `not_reached` 它在链里,但前面某一级就成了/预算就用完了,没轮到它
    - `never_in_chain` 整个 records 里它一次都没出现在 chain 里
    - `in_known_chain` 它在不在传进来的 known_chain 里

    `served` / `failed` / `skipped` 是三件事,合成一个数之后 skipped 恰好是
    最看不见的那个,而它才是「便宜档为什么没用上」的答案。

    **chain 里出现了 known_chain 之外的名字时,它照样进这张表**(`in_known_chain`
    为假)。悄悄丢掉一个不认识的 provider,和账本里真的没有它,打印出来一样。
    """
    known = [str(n) for n in known_chain]
    seen = []
    for r in records:
        for n in _chain_of(r):
            if n not in known and n not in seen:
                seen.append(n)
    names = known + seen

    stats = {
        n: {
            "name": n,
            "served": 0,
            "failed": 0,
            "skipped": 0,
            "not_reached": 0,
            "never_in_chain": True,
            "in_known_chain": n in known,
        }
        for n in names
    }

    for r in records:
        chain = _chain_of(r)
        chain_set = set(chain)
        for n in chain:
            stats[n]["never_in_chain"] = False
        # skipped 不依赖 attempts:哪怕这条记录的 attempts 是坏的,
        # 「它不在这条链里」仍然是能确定的事实,照样计数。
        for n in names:
            if n not in chain_set:
                stats[n]["skipped"] += 1
        parts = _split(r)
        if parts is None:
            continue
        failed, servedn, not_reached = parts
        for n in failed:
            stats[n]["failed"] += 1
        if servedn is not None:
            stats[servedn]["served"] += 1
        for n in not_reached:
            stats[n]["not_reached"] += 1

    return [stats[n] for n in names]


def verify_rungs(records):
    """rungs() 所依赖的那条判据,在这批记录上到底成不成立。

    返回 `{checked, contradictions, unusable, samples}`。`contradictions` 是
    「成功记录里 `chain[attempts-1]` 不等于 `provider`」的条数 —— 判据一旦在
    某批数据上不成立,rungs 的每一个数都是错的,而它照样会打印出一张漂亮的表。
    所以这个数必须能被单独读出来,也必须能被投毒测出非零。
    """
    checked = contradictions = unusable = 0
    samples = []
    for r in records:
        chain = _chain_of(r)
        a = r.get("attempts")
        if isinstance(a, bool) or not isinstance(a, int) or a < 1 or a > len(chain):
            unusable += 1
            continue
        if not r.get("ok"):
            checked += 1
            continue
        checked += 1
        if chain[a - 1] != r.get("provider"):
            contradictions += 1
            if len(samples) < 5:
                samples.append({"i": r.get(INDEX_KEY), "chain": chain,
                                "attempts": a, "provider": r.get("provider")})
    return {"checked": checked, "contradictions": contradictions,
            "unusable": unusable, "samples": samples}


# --------------------------------------------------------------------------
# 连续降级段
# --------------------------------------------------------------------------

def _degraded(rec: dict) -> bool:
    """这一次调用有没有降级。判据**只看这条记录自己的链**。

    一条记录降级,当且仅当应答它的不是**它自己那条链的链首**(等价于 attempts > 1)。
    整链失败也算,而且是最狠的一种。

    为什么不能用一个全局的「链首集合」去过滤:实测这份账本里链首不止一个,
    `codexg` / `codex` / `cc` 都当过某条链的第一级 —— 不同调用方用了不同起点。
    拿这三个名字组成的集合去判「是不是链首」,会把真正降级到 cc 的那些调用
    当成正常,于是最要紧的那些段被静默吃掉,而剩下的段看起来完全正常。

    链为空时按降级算:证明不了它是正常应答,就不能当成正常。
    """
    chain = _chain_of(rec)
    if not chain:
        return True
    return _served_name(rec) != chain[0]


def runs(records, min_len=3):
    """连续降级段,返回
    `[{provider, total_failure, start_i, end_i, start_offset, end_offset,
    length, start_ts, end_ts, error}]`。

    为什么需要它:落到低档的调用如果均匀摊在一天里,那是配额偏紧;如果挤在
    一个连续段里,那是一次故障。两者的日均值一模一样,只有连续段能把它们分开。

    一段 = 连续的、**每一条都降级**、且应答方相同的记录。一条正常的链首应答会把
    段打断 —— 它不是降级,不能被算进段长,更不能把两次无关的故障粘成一段。
    整链失败(NONE)同样成段,它是最严重的一种,绝不因为 provider 为空被过滤掉。

    只保留 `length >= min_len`。`error` 是段里最常见的那条 error 文本(截到 120 字符),
    没有就是 None —— 一段「23 连失败」不说原因,人看完还得自己去翻账本。

    `total_failure` 标出整链失败的段。**它存在是为了让页面把两类分开画,不要混排。**
    整链失败是「这次调用没有答案」,降级只是「贵了一点」,两者不同量纲。混在一张
    按长度排的表里,一段 23 连的全失败会被更长、却远没那么致命的 82 连 cc 压到
    第九位 —— 最狠的那种恰好被藏起来,而表面上排序完全正确。

    **两套编号,名字必须说清自己是哪一套:**

    - `start_i` / `end_i` —— **账本里的物理行号**,和 `page()` 每行的 `i`、和
      `body(i)` 收的那个参数是同一套。这是给人看、给人对照的数。
    - `start_offset` / `end_offset` —— **在传进来的 records 列表里的下标**,
      也就是能直接喂给 `page(offset=...)` 的那个数。这是给程序跳转用的。

    两者在一个没有空行和坏行的账本上恰好相等,所以只用干净 fixture 是发现不了
    它们是两套的 —— 真实账本里夹着空行和坏行,差几位。曾经只有一个叫 `index` 的
    字段,页面拿它当行号显示、又拿它当下标跳转,于是跳转稳定地落在几条之后,
    而两个数印在同一张页面上没有任何东西说明它们不是一套。

    `start_offset` 是**相对于你传进来的那个列表**的。先过滤再传进来的话,它只在
    你把同一个列表交给 `page()` 时才对得上;`start_i` 则在任何情况下都指向账本里
    同一条记录。

    `start_ts`/`end_ts` 在那条记录没有 ts 时是 **None**,不用邻居的时间去补。
    段的顺序就是传进来的 records 的顺序:如果调用方先过滤再传进来,段内的记录
    在账本里未必相邻,`start_i`/`end_i` 能看出这一点。
    """
    if not isinstance(min_len, int) or isinstance(min_len, bool) or min_len < 1:
        raise ValueError("min_len 必须是正整数")

    segs = []
    cur = None
    for off, r in enumerate(records):
        if not _degraded(r):
            # 正常的链首应答打断降级段。不打断的话,两次相隔很远的故障会被
            # 中间一片正常调用粘成一段假的长段。
            if cur is not None:
                segs.append(cur)
                cur = None
            continue
        name = _served_name(r)
        if cur is not None and cur["provider"] == name:
            cur["items"].append((off, r))
        else:
            if cur is not None:
                segs.append(cur)
            cur = {"provider": name, "items": [(off, r)]}
    if cur is not None:
        segs.append(cur)

    out = []
    for seg in segs:
        if len(seg["items"]) < min_len:
            continue
        (first_off, first), (last_off, last) = seg["items"][0], seg["items"][-1]
        errs = Counter(e[:120] for e in (r.get("error") for _, r in seg["items"])
                       if isinstance(e, str) and e)
        out.append({
            "provider": seg["provider"],
            "total_failure": seg["provider"] == NONE_SERVED,
            # 物理行号:给人看,和 page() 的 i / body(i) 同一套
            "start_i": first.get(INDEX_KEY),
            "end_i": last.get(INDEX_KEY),
            # 列表下标:给程序跳转,直接喂给 page(offset=...)
            "start_offset": first_off,
            "end_offset": last_off,
            "length": len(seg["items"]),
            "start_ts": _ts_of(first),
            "end_ts": _ts_of(last),
            "error": errs.most_common(1)[0][0] if errs else None,
        })
    return out


# --------------------------------------------------------------------------
# 明细分页
# --------------------------------------------------------------------------

def page(records, offset=0, limit=50, provider=None, ok=None, q=None, caller=None,
         known_chain=None):
    """明细分页,返回 `(rows, total)`。`total` 是**过滤之后、分页之前**的条数。

    `total` 算在分页之前,否则分页器会显示一个翻不到的尾巴。

    每行的 `i` 是这条记录在账本里的绝对行号,不是页内序号:「点开第 N 条」
    换一页、换一个筛选之后必须仍然指向同一条。

    `provider` 筛的是「谁应答的」(整链失败用 NONE_SERVED 筛)。`known_chain`
    不传时取当前生效的链,用来算每行的 `skipped`。

    `q` 非空时按 **error 文本**筛,大小写不敏感。没有 error 的记录(也就是成功的
    那些)一律不匹配 —— 这个框在页面上的标签就是「搜错误文本」,让它顺带匹配到
    成功记录会让结果读不懂。因此 `q` 与 `ok=True` 同时给会得到空结果,这是对的。

    大小写不敏感不是顺手加的:真实账本里的 error 全是英文小写,一个忘了折大小写的
    实现在那份数据上永远看起来正常,只有别人用大写搜的时候才静默地什么都搜不到。

    `caller` **精确匹配**(不是子串):`"em_tick.py"` 不许匹配到 `"em_tick.py.bak"`。
    它认的是 `callers()` 给出的那套名字,所以两个合成名 `CALLER_UNKNOWN` /
    `CALLER_LEGACY` 也筛得动 —— 下拉框里列得出来的每一项都点得动,否则那几行
    就是摆设。
    """
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("offset 必须是非负整数")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ValueError("limit 必须是非负整数")
    # 把 records 传下去:chain_config 的观测那一级本来要自己扫一遍账本,
    # 而这里手上已经有这批记录了,再扫一次纯属白读。
    known = (list(known_chain) if known_chain is not None
             else list(chain_config(records)["effective"]))

    sel = records
    if provider is not None:
        sel = [r for r in sel if _served_name(r) == provider]
    if ok is not None:
        want = bool(ok)
        sel = [r for r in sel if bool(r.get("ok")) is want]
    if caller is not None:
        sel = [r for r in sel if _caller_of(r) == caller]
    if q:
        needle = str(q).lower()
        sel = [r for r in sel
               if isinstance(r.get("error"), str) and needle in r["error"].lower()]
    total = len(sel)

    rows = []
    for r in sel[offset:offset + limit]:
        chain = _chain_of(r)
        chain_set = set(chain)
        row = {
            "i": r.get(INDEX_KEY),
            "ts": _ts_of(r),
            "provider": r.get("provider"),
            "served": _served_name(r),
            "ok": bool(r.get("ok")),
            "mode": r.get("mode"),
            "web": r.get("web"),
            "prompt_chars": r.get("prompt_chars"),
            "reply_chars": r.get("reply_chars"),
            "ms": r.get("ms"),
            "attempts": r.get("attempts"),
            "chain": chain,
            "skipped": [n for n in known if n not in chain_set],
            # 原样透传,缺失就是 None。**不许替换成空数组:**「没有逐级明细」
            # 和「有明细但是空的」是两件事,而顶层的 error 只记最后一级的原因,
            # 那一级往往正是因为预算被前面几级烧光才没真的试过 ——
            # 一整段 chain budget exhausted 里,真正的病因一个字都没留下。
            "attempts_detail": r.get("attempts_detail"),
            "error": r.get("error"),
            "id": r.get("id"),
        }
        # **键不在就不要凭空造一个出来。** 页面靠 `"caller" in row` 区分
        # 「老记录(还没开始记)」和「记了但推断不出来(值是 null)」;
        # 给老记录补一个 None,这个区分当场消失,而两者要说的话完全不同。
        if "caller" in r:
            row["caller"] = r["caller"]
        rows.append(row)
    return rows, total


# --------------------------------------------------------------------------
# 链配置
# --------------------------------------------------------------------------

def _parse_names(raw):
    """把一段文本拆成名字列表。逗号或换行都认,`#` 起头的行是注释。"""
    out = []
    for line in str(raw).splitlines():
        line = line.split("#", 1)[0]
        for part in line.replace(",", " ").split():
            out.append(part.strip())
    return out


def _bad_names(names):
    return [n for n in names if not (isinstance(n, str) and NAME_RE.match(n))]


def _observed_chain(records=None):
    """账本里**最近一次调用实际走的那条链**,返回 `(chain, 绝对行号)`,没有就 `(None, None)`。

    语义要说准:这是「最近一次调用实际走的链」,**不是「默认链」**。调用方可以显式
    传一条链进去 —— 真实样本里链首就有三种,说明确实有人这么做。所以它是一次观测,
    不是一个配置值,页面文案必须照这个意思写。

    传了 `records` 就从里面取(服务端通常已经读过一遍,不必再读文件);没传就自己
    扫一遍账本,只留尾部 OBSERVE_TAIL 行的原始文本,回看时才解析 —— 这样既拿得到
    绝对行号(要让人能点过去看那一条),又不必把两万条记录全部解析一遍。
    """
    if records is not None:
        for r in reversed(records):
            c = _chain_of(r)
            if c:
                return tuple(c), r.get(INDEX_KEY)
        return None, None

    tail = deque(maxlen=OBSERVE_TAIL)
    try:
        with ledger_path().open("r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, start=1):
                t = line.strip()
                if t:
                    tail.append((i, t))
    except OSError:
        # 读不到账本不是错误,只是「没有观测」。这里降级返回 None,
        # 由调用方落到下一级去 —— 在这里抛会让一台没装 llmcall 的机器打不开页面。
        return None, None

    for lineno, t in reversed(tail):
        try:
            rec = json.loads(t)
        except ValueError:
            continue
        if isinstance(rec, dict):
            c = _chain_of(rec)
            if c:
                return tuple(c), lineno
    return None, None


def chain_config(records=None) -> dict:
    """当前生效的链,以及它**到底是从哪来的**。

    优先级:`LLMCALL_CHAIN`(环境变量) > chain 文件 > **账本观测** > 内置常量。

    `source` 四种,各自诚实:

    - `env`       环境变量设的
    - `file`      chain 文件里写的
    - `observed`  **最近一次真实调用实际走的那条链**(`observed_i` 是它的绝对行号,
                  让人能点过去看那一条)。它是证据,不是拷贝 —— 这一级存在的理由
                  是内置常量会漂:它在这个仓里,而权威在 llmcall 仓里,两边只在
                  有人同时打开两个仓时才对得上号。
    - `builtin`   账本里一条记录都没有(全新机器)时才会走到,见 DEFAULT_CHAIN 的注释

    `observed` / `observed_i` **无论哪一级生效都会填**:配置里写着 X 而最近一次真实
    调用走的是 Y,这件事本身就值得摆在页面上,哪怕它不一定是错(调用方可以显式传链)。

    `shadowed_by_env` 为真表示「文件里设了,但环境变量压过它」。控制台要靠这一条
    告诉用户「你刚改的顺序现在不生效」—— 少了它,那个输入框就是个写了也没用、
    还回显成功的假按钮。

    环境变量或文件里的内容过不了名字闸时,它**不会被静默忽略**:effective 退回
    下一级,同时 `env_error` / `file_error` 说清楚退回的理由。一个「配了但没生效」
    和一个「没配」不能打印成同一块板子。
    """
    fp = chain_file_path()
    observed, observed_i = _observed_chain(records)
    out = {
        "effective": tuple(DEFAULT_CHAIN),
        "source": "builtin",
        "observed": observed,
        "observed_i": observed_i,
        "env_name": ENV_CHAIN,
        "env_value": os.environ.get(ENV_CHAIN),
        "env_error": None,
        "file_path": str(fp),
        "file_value": None,
        "file_error": None,
        "shadowed_by_env": False,
        "builtin": tuple(DEFAULT_CHAIN),
    }

    # 文件先读,因为 shadowed_by_env 要知道文件里到底有没有东西。
    file_names = None
    try:
        raw = fp.read_text(encoding="utf-8")
    except FileNotFoundError:
        pass
    except OSError as e:
        out["file_error"] = f"{type(e).__name__}: {e}"
    else:
        names = _parse_names(raw)
        out["file_value"] = tuple(names)
        if not names:
            out["file_error"] = "文件存在但没有任何名字"
        elif _bad_names(names):
            out["file_error"] = "非法名字: " + ", ".join(_bad_names(names))
        else:
            file_names = tuple(names)

    env_raw = out["env_value"]
    env_names = None
    if env_raw is not None:
        names = _parse_names(env_raw)
        if not names:
            out["env_error"] = "环境变量已设,但没有任何名字"
        elif _bad_names(names):
            out["env_error"] = "非法名字: " + ", ".join(_bad_names(names))
        else:
            env_names = tuple(names)

    if env_names:
        out["effective"] = env_names
        out["source"] = "env"
        # 只有文件里真的有一份可用的顺序时,才叫「被压住了」。
        # 文件不存在的时候说「被环境变量压住」会把用户送去改一个不存在的文件。
        out["shadowed_by_env"] = file_names is not None and file_names != env_names
    elif file_names:
        out["effective"] = file_names
        out["source"] = "file"
    elif observed:
        # 有真实记录就以记录为准。内置常量只在一条记录都没有时才会被看见。
        out["effective"] = observed
        out["source"] = "observed"
    return out


def write_chain(names) -> dict:
    """把链顺序写进 chain 文件,写完返回新的 `chain_config()`。

    闸门,全部**整批裁决**,拒绝时抛 `Refused` 并带上 code:

    - `not_a_list` 传进来的是一整个字符串(会被逐字符拆成链,静默得很)
    - `empty` 空列表
    - `too_many` 超过 MAX_CHAIN 级
    - `bad_name` 任何一个名字过不了 NAME_RE
    - `duplicate` 同一级出现两次(会让 rungs 的 attempts 推断变成多义的)

    **拒绝时一个字节都不写。** 部分写入会留下一份既不是旧顺序、也不是用户要的
    新顺序的文件,而那种文件不会让任何东西报错。

    写入是原子的:先写同目录下的临时文件再 `os.replace`。跨目录的 replace 在
    Windows 上不保证原子,所以临时文件必须落在目标目录里。

    返回值带着 `shadowed_by_env`:写成功了但环境变量压着,用户必须当场看见,
    否则他会以为自己改好了。
    """
    if isinstance(names, (str, bytes)):
        raise Refused("names 必须是名字的序列,不是一整个字符串", "not_a_list")
    names = list(names)
    if not names:
        raise Refused("拒绝写入空链", "empty")
    if len(names) > MAX_CHAIN:
        raise Refused(f"链最多 {MAX_CHAIN} 级,给了 {len(names)} 级", "too_many")
    bad = _bad_names(names)
    if bad:
        raise Refused("非法名字(整批拒绝,未写入任何内容): " + ", ".join(map(repr, bad)), "bad_name")
    dup = [n for n, c in Counter(names).items() if c > 1]
    if dup:
        raise Refused("链里有重复的名字(整批拒绝,未写入任何内容): " + ", ".join(dup), "duplicate")

    fp = chain_file_path()
    fp.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(names) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(fp.parent), prefix=".chain-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(fp))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return chain_config()


# --------------------------------------------------------------------------
# 单条正文
# --------------------------------------------------------------------------

def _clip(v):
    """截断一段正文,返回 (文本, 是否截断了, 原始长度)。截断从不静默。"""
    s = v if isinstance(v, str) else ("" if v is None else str(v))
    n = len(s)
    if n <= BODY_MAX_CHARS:
        return s, False, n
    return s[:BODY_MAX_CHARS], True, n


def body(i, records=None) -> dict:
    """读第 `i` 行那次调用的 prompt / reply 正文。

    正文不在账本里:写侧默认不记,开启后落到伴生仓的 `bodies/YYYY-MM-DD.jsonl`,
    靠账本记录上的 `id` 去认领。

    **「没有正文」有六种,每一种给一个自己的 reason,一种都不许合并。** 这是这个
    函数存在的全部理由:

    - `no_record`   账本里根本没有这一行(可能已被轮转掉)
    - `uninitialised` 找不到 llmcall 的私有伴生仓 —— 伴生仓没配
    - `disabled`    伴生仓在,但一个正文文件都没有 —— 功能没开
    - `no_id`       这条调用早于正文记录功能
    - `read_error`  正文文件读不动 —— 找不到不代表没记过
    - `not_found`   记过,但过了保留期

    「功能没开」「伴生仓没配」「过期删了」是三件完全不同的事,而它们长得一样正是
    这个台子要防的东西:合并成一句「没有正文」之后,读的人会对着一个开关没打开的
    台子去查保留策略。

    返回里永远有 `available` 与 `reason_code`,后者是给页面判分支用的,免得它去
    匹配中文散文。
    """
    if isinstance(i, bool) or not isinstance(i, int):
        raise Refused("i 必须是账本里的绝对行号(整数)", "bad_index")

    if records is None:
        records, _ = read()
    rec = next((r for r in records if r.get(INDEX_KEY) == i), None)
    if rec is None:
        return {"available": False, "reason_code": "no_record",
                "reason": f"账本里没有第 {i} 行这条记录(可能已被轮转掉)", "i": i}

    res = bodies_resolution()
    d = res["dir"]
    if d is None or not d.is_dir():
        # 显式覆盖设了却指到一个不存在的目录,也走这里 —— 但要把那个路径说出来,
        # 否则人会以为是伴生仓没装,而其实是那个环境变量填错了。
        where = f":{d}" if d is not None else ""
        return {"available": False, "reason_code": "uninitialised",
                "reason": f"正文记录未初始化(找不到 llmcall 的私有伴生仓{where})",
                "i": i, "dir": str(d) if d else None,
                "source": res["source"], "tried": res["tried"]}

    # 文件名是 YYYY-MM-DD,字典序即时间序;倒着找,近的先命中。
    try:
        files = sorted((p for p in d.iterdir() if p.suffix == ".jsonl"), reverse=True)
    except OSError as e:
        return {"available": False, "reason_code": "read_error",
                "reason": f"正文目录读不动: {type(e).__name__}: {e}",
                "i": i, "dir": str(d), "source": res["source"]}

    if not files:
        return {"available": False, "reason_code": "disabled",
                "reason": "正文记录未开启(伴生仓在,但没有任何正文文件)",
                "i": i, "dir": str(d), "source": res["source"]}

    rid = rec.get("id")
    if not isinstance(rid, str) or not rid:
        return {"available": False, "reason_code": "no_id",
                "reason": "这条调用早于正文记录功能,没有 id", "i": i}

    scanned = 0
    malformed = 0
    errors = []
    for fp in files:
        scanned += 1
        try:
            with fp.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        b = json.loads(line)
                    except ValueError:
                        malformed += 1
                        continue
                    if not isinstance(b, dict) or b.get("id") != rid:
                        continue
                    prompt, p_cut, p_len = _clip(b.get("prompt"))
                    reply, r_cut, r_len = _clip(b.get("reply"))
                    return {
                        "available": True, "i": i, "id": rid,
                        "prompt": prompt, "reply": reply,
                        "truncated": bool(p_cut or r_cut),
                        "prompt_chars": p_len, "reply_chars": r_len,
                        "source": str(fp),
                    }
        except OSError as e:
            # 读不动的文件不能被当成「里面没有」:那会把一次权限问题
            # 说成「正文过期了」,而后者是不用处理的。
            errors.append(f"{fp.name}: {type(e).__name__}")

    if errors:
        return {"available": False, "reason_code": "read_error",
                "reason": "有正文文件读不动,找不到不代表没记过: " + ", ".join(errors[:3]),
                "i": i, "id": rid, "files_scanned": scanned, "malformed": malformed}

    return {"available": False, "reason_code": "not_found",
            "reason": "正文已过保留期或未被记录", "i": i, "id": rid,
            "files_scanned": scanned, "malformed": malformed}
