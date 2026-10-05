"""会话与模型调用两屏的易用性契约。数据全部是合成的(AcmeSync、合成 uuid),字面量写在本文件里。

钉的是:调用记录最新在前且能直接翻到首末页、每行写明成没成、同一件事只说一次;
对话链的标题、读取提示压成一行、导出和新建会话的按钮写字并说明为什么不能点、
会话内搜索和逐段复制;会话列表不再写「展开 / 收起」;读取失败画成带重试的红块;
筛选和开关记在本机但不记搜索词。
"""
import json

from test_operations_ui import run
from test_convchain_ui import STUB, chain_case, setup as chain_setup, uid


# ── 模型调用 ─────────────────────────────────────────────────────────────────

CALLS_STUB = "$('lmprov').options=[1,2];$('lmcaller').options=[1,2];toast=()=>{};"


def call_row(i, ok=True, ts=1900000000, provider="codexg"):
    return {"i": i, "ts": ts, "provider": provider if ok else None, "served": provider if ok else "NONE",
            "ok": ok, "mode": "judge", "web": False, "chain": ["codexg", "cc"], "skipped": [],
            "prompt_chars": 120, "reply_chars": 40 if ok else 0, "ms": 900, "attempts": 1 if ok else 2,
            "caller": "acmesync.py", "error": None if ok else "synthetic upstream timeout"}


def test_each_call_row_says_whether_it_failed():
    html = run("renderCalls();$('lmtab').innerHTML",
               CALLS_STUB + "LMROWS=" + json.dumps([call_row(7), call_row(8, ok=False)]) + ";LMTOTAL=2;")
    head, rows = html.split("</tr>", 1)
    assert "<th>结果</th>" in head
    failed = rows.split('data-i="8"')[0].rsplit("<tr", 1)[1] + rows.split('data-i="8"')[1].split("</tr>")[0]
    # 看「结果」那一格本身:应答服务一格的「整链失败」不算数。
    cell = lambda row: row.split('<td class="res">')[1].split("</td>")[0]
    assert "失败" in cell(failed) and "成功" not in cell(failed) and "lrow failed" in failed
    ok_row = rows.split('data-i="7"')[1].split("</tr>")[0]
    assert "成功" in cell(ok_row) and "失败" not in ok_row
    # 行首有三角,行上有 aria-expanded:看得出点了能展开。
    assert 'aria-expanded="false"' in rows and "▶" in rows


def test_pager_has_first_last_and_a_page_box_with_reasons():
    html = run("LMQ.offset=0;LMQ.limit=20;renderCalls();$('lmpage').innerHTML",
               CALLS_STUB + "LMROWS=" + json.dumps([call_row(1)]) + ";LMTOTAL=95;")
    first = html.split('id="lmfirst"')[1].split(">")[0]
    assert "disabled" in first and "已是第一页" in first
    assert 'id="lmpgno"' in html and 'max="5"' in html and "/ 5 页" in html
    last = run("LMQ.offset=80;LMQ.limit=20;renderCalls();$('lmpage').innerHTML",
               CALLS_STUB + "LMROWS=" + json.dumps([call_row(1)]) + ";LMTOTAL=95;").split('id="lmlast"')[1].split(">")[0]
    assert "disabled" in last and "已是最后一页" in last
    # 页码超出范围按末页算;算下来就是当前页时不发请求。
    got = run("let loads=0;loadCalls=()=>{loads++;};LMQ.offset=0;LMQ.limit=20;LMTOTAL=95;"
              "const a=lmGoPage(99),off=LMQ.offset,b=lmGoPage(5);[a,off,b,loads]", CALLS_STUB)
    assert got == [True, 80, False, 1]


def test_calls_ask_newest_first_and_remember_filters_but_not_the_search():
    got = run("""(async()=>{
      let paths=[],saved={};localStorage={getItem:k=>saved[k]||null,setItem:(k,v)=>{saved[k]=v;}};
      api=async p=>{paths.push(p);return {rows:[],total:0};};
      Object.assign(LMQ,{provider:'codexg',ok:'0',caller:'acmesync.py',q:'timeout'});
      await loadCalls();
      const stored=JSON.parse(saved['tc.llm.calls']);
      Object.assign(LMQ,{provider:'',ok:'',caller:'',order:'asc'});lmLoadPrefs();
      return {path:paths[0],stored,restored:[LMQ.provider,LMQ.ok,LMQ.caller,LMQ.order]};
    })()""", CALLS_STUB)
    assert "order=desc" in got["path"]
    assert got["stored"] == {"provider": "codexg", "ok": "0", "caller": "acmesync.py", "order": "desc"}
    assert "timeout" not in json.dumps(got["stored"]), "搜索词不许记下来"
    assert got["restored"] == ["codexg", "0", "acmesync.py", "desc"]
    # 记下的服务已经不在账本里:丢掉,别按一个下拉框里看不见的条件筛。
    stale = run("LLM={rungs:[{name:'codexg'}],callers:[]};LMQ.provider='retired';LMQ.caller='gone.py';"
                "lmDropStalePrefs();[LMQ.provider,LMQ.caller]", CALLS_STUB)
    assert stale == ["", ""]


def test_jump_in_newest_first_order_lands_on_the_segment_start():
    got = run("""(async()=>{
      let paths=[];api=async p=>{paths.push(p);return {rows:[],total:100};};
      document.querySelector=()=>null;$('lcalls').scrollIntoView=()=>{};
      LLM={runs:[{start_offset:10,start_i:11,length:3}]};LMQ.order='desc';LMQ.limit=20;
      await jumpToCalls(10);return {offset:LMQ.offset,jump:LMJUMP,paths};
    })()""", CALLS_STUB)
    # 倒序里段首在第 100-1-10=89 位,放在那一页的最后一行:offset 70,本页是 70..89。
    assert got["offset"] == 70 and 70 <= 89 < 70 + 20
    assert got["jump"] == 11
    assert "order=desc" in got["paths"][-1]


def test_unstamped_count_is_said_once_and_dead_runs_show_their_error():
    llm = {"windows": [
        {"label": "今天", "calls": 3, "ok": 3, "failed": 0, "cheap_share": 0.5, "avg_ms": 800, "unstamped_excluded": 4242},
        {"label": "近 7 日", "calls": 9, "ok": 8, "failed": 1, "cheap_share": 0.4, "avg_ms": 900, "unstamped_excluded": 4242}],
        "ledger": {"exists": True, "bytes": 2048, "parsed": 4251, "malformed": 0, "blank": 0, "lines": 4251,
                   "stamped": 9, "unstamped": 4242},
        "chain": {"effective": ["codexg", "cc"], "source": "builtin"}, "rungs": [], "verify": {},
        "runs": [{"total_failure": True, "length": 5, "start_offset": 3, "start_i": 4, "start_ts": None,
                  "error": "synthetic chain budget exhausted", "provider": "NONE"},
                 {"total_failure": False, "length": 7, "start_offset": 20, "start_i": 21, "start_ts": 1900000000,
                  "error": None, "provider": "cc"}]}
    got = run("renderLLM();[$('lwins').innerHTML+$('lledger').innerHTML,$('lstab').innerHTML]",
              "busy=false;setBadge=()=>{};LLM=" + json.dumps(llm) + ";")
    usage, runs = got
    assert usage.count("4,242") == 1, "无时间戳的条数只说一次"
    dead = runs.split("备用服务应答")[0]
    assert "synthetic chain budget exhausted" in dead
    assert "整链失败" not in dead
    # 没有时间戳的段写成淡色的占位符,原因在悬停里。
    assert 'title="无时间戳">—<' in dead


def test_call_order_arrows_say_why_they_stop_at_the_ends():
    html = run("renderChain();$('lclist').innerHTML",
               "busy=false;LLM={chain:{effective:['codexg','codex','cc'],source:'builtin'}};LCDRAFT=null;")
    ups = html.split('data-mv="up"')
    downs = html.split('data-mv="dn"')
    assert "最前" in ups[1].split(">")[0]
    assert "最后" in downs[-1].split(">")[0]


def test_call_read_failure_is_a_red_block_with_retry():
    got = run("""(async()=>{api=async()=>{throw new Error('synthetic reader down');};
      await loadCalls();return [$('lmtab').innerHTML,$('lmnote').textContent,$('lmpage').innerHTML];})()""", CALLS_STUB)
    assert "state-error" in got[0] and "重试" in got[0] and "data-lm-retry" in got[0]
    assert "synthetic reader down" not in got[1]
    assert got[2] == "", "读坏了不画一个「0–0 / 共 0 条」的分页器"


# ── 对话链 ───────────────────────────────────────────────────────────────────

def test_chain_head_compacts_read_warnings_into_one_line():
    chain = chain_case(badLines=2, danglingParents=1, cwd="/synthetic/work/AcmeSync", warnings=[
        "2 行不是合法 JSON,已跳过并计数",
        "1 条记录的父节点不在文件里,各自当作孤立的根",
        "压缩边界 aaaa1111, bbbb2222, cccc3333 没有可用的前驱指针,按文件里紧挨着的前一行接上了压缩前的历史"])
    crumb, head, warn = run("chRenderHead();[$('chcrumb').innerHTML,$('chhead').innerHTML,$('chwarn').innerHTML]",
                            chain_setup(chain))
    assert warn.count('class="warn-line"') == 1
    line = warn.split(">", 1)[1]
    assert "读取提示：坏行 2 · 悬空父节点 1 · 压缩边界 3 个无前驱" in line
    # uuid 只在悬停里,不在看得见的那一行。
    visible = line.split("</div>")[0]
    assert "aaaa1111" not in visible and "aaaa1111" in warn
    assert "<select" not in crumb
    assert "项目 <b>AcmeSync</b>" in head and "<b>3</b> 轮" in head


def test_range_controls_are_words_and_say_why_they_are_unavailable():
    chain = chain_case()
    html = run("CH_SEL=null;chRenderAct();$('chact').innerHTML", chain_setup(chain))
    for act, label in (("from", "从这里开始"), ("to", "到这里结束")):
        button = html.split(f'data-chact="{act}"')[1].split("</button>")[0]
        assert "disabled" in button and "先在左侧选一条消息" in button and label in button.split(">", 1)[1]
    clear = html.split('data-chact="clr"')[1].split("</button>")[0]
    assert "还没有设定范围" in clear
    export = html.split('data-chexport="md"')[0].rsplit("<button", 1)[1]
    assert "primary" in export and "导出 Markdown" in html
    start = chain["turns"][3]["steps"][0]["u"]
    ranged = run(f"CH_SEL='s:3:0';CH_FROM='{start}';chRenderAct();$('chact').innerHTML", chain_setup(chain))
    desc = ranged.split('class="ch-row ch-range"')[1].split("</div>")[0]
    assert "#" in desc and "共" in desc and start[:8] not in desc


def test_turn_rows_keep_step_count_and_time_only():
    chain = chain_case()
    html = run("chRenderList();$('chlist').innerHTML", chain_setup(chain))
    row = html.split('data-chk="h:0"')[1].split("</div>")[0]
    assert "🔧" not in row and "✎2" not in row
    assert "4 步" in row and "工具调用 2" in row  # 计数写成字,在步数的悬停里
    opened = run("CH_OPEN[0]=true;chRenderList();$('chlist').innerHTML", chain_setup(chain))
    assert 'class="ch-kinds">回复 1 · 工具调用 2<' in opened


def test_in_chain_search_filters_turns_and_counts_matches():
    chain = chain_case()
    chain["turns"][3]["human"]["preview"] = "deploy AcmeSync tonight"
    count = "(h=>h.split('class=\"ch-t').length-1)($('chlist').innerHTML)"
    got = run(f"chFindApply('acmesync');[{count},$('chfindn').textContent,CH_FIND_HITS]", chain_setup(chain))
    assert got[0] == 1 and got[1].startswith("1 /") and got[2] == [3]
    # Enter:先选中这个命中,再按才走到下一个(只有一个就留在它上面)。
    got = run("chFindApply('synthetic question');const a=$('chfindn').textContent;chFindNext();const b=CH_SEL;"
              "chFindNext();[a,b,CH_SEL,$('chfindn').textContent]",
              chain_setup(chain, "$('chlist').querySelector=()=>null;"))
    # 命中的是第 0 项和第 4 项(第 1 轮之后隔着一对压缩标记)。
    assert got[0] == "1 / 2 轮" and got[1] == "h:0" and got[2] == "h:4" and got[3] == "2 / 2 轮"
    empty = run("chFindApply('nothing-like-this');[$('chlist').innerHTML,$('chfindn').textContent]", chain_setup(chain))
    assert "state-empty" in empty[0] and empty[1] == "没有匹配的轮"


def test_every_block_of_text_in_the_detail_has_a_copy_button():
    node = {"u": uid(5), "kind": "text", "type": "assistant", "text": "synthetic reply",
            "tools": [{"name": "Read", "id": "toolu_1", "input": "{}"}],
            "results": [{"tool_use_id": "toolu_1", "text": "synthetic result"}],
            "thinking": "synthetic thought", "raw": "{\"x\":1}", "lineIndex": 4, "byteOffset": 0, "byteLength": 10}
    html = run("chRenderDet(" + json.dumps(node) + ");$('chdet').innerHTML", chain_setup(chain_case()))
    assert html.count("<pre") == 5
    assert html.count("data-chcopy") == html.count("<pre")


def test_chain_read_failure_is_a_red_block_with_retry_and_export_switches_persist():
    got = run(f"""(async()=>{{api=async()=>{{throw new Error('synthetic transcript locked');}};
      CURVIEW='convos';$('cvbox').hidden=true;$('chbox').scrollIntoView=()=>{{}};
      await openConvoChain('{chain_case()["id"]}',{{force:true}});
      return [$('chwarn').innerHTML,$('chnote').textContent];}})()""", STUB + "var location={hash:''};var history={pushState(){}};")
    assert "state-error" in got[0] and "重试" in got[0] and "data-chretry" in got[0]
    assert "synthetic transcript locked" not in got[1]
    saved = run("""(()=>{let saved={};localStorage={getItem:k=>saved[k]||null,setItem:(k,v)=>{saved[k]=v;}};
      CH_XT=false;CH_XK=true;chSavePrefs();CH_XK=false;chLoadPrefs();return [saved['tc.chain.export'],CH_XK];})()""", STUB)
    assert json.loads(saved[0]) == {"tools": False, "thinking": True} and saved[1] is True


# ── 会话列表 ─────────────────────────────────────────────────────────────────

def convos_case():
    rows = [{"id": uid(1), "title": "AcmeSync planning", "titleFrom": "rename", "file": "/synthetic/a.jsonl",
             "humanSeen": 2, "partial": False, "ageHours": 3, "bytes": 10, "preview": ""}]
    return {"available": True, "summary": {"files": 1, "matched": 1, "humanish": 1, "bytes": 10, "groups": 1},
            "groups": [{"cwd": "/synthetic/AcmeSync", "count": 1, "humanish": 1, "bytes": 10,
                        "newest": 1900000000, "truncated": False, "shown": rows}]}


def test_group_headers_have_a_caret_but_no_expand_word():
    for open_ in ("false", "true"):
        html = run("renderConvos();$('cvgroups').innerHTML",
                   STUB + "CONVOS=" + json.dumps(convos_case()) + f";CV_OPEN['/synthetic/AcmeSync']={open_};")
        header = html.split('class="cv-gh"')[1].split("</button>")[0]
        assert "展开" not in header and "收起" not in header
        assert f'aria-expanded="{open_}"' in header


def test_convo_read_failure_is_a_red_block_with_retry():
    got = run("""(async()=>{api=async()=>{throw new Error('synthetic index unreadable');};
      await loadConvos();return [$('cvgroups').innerHTML,$('cvnote').textContent];})()""", STUB)
    assert "state-error" in got[0] and "重试" in got[0] and "data-cvretry" in got[0]
    assert "synthetic index unreadable" not in got[1]


def test_convo_sort_filter_and_open_groups_persist_but_search_does_not():
    got = run("""(async()=>{let saved={};localStorage={getItem:k=>saved[k]||null,setItem:(k,v)=>{saved[k]=v;}};
      api=async()=>CONVOS;CV_SORT='size';CV_HUMAN_ONLY=true;CV_QUERY='acme secret';
      CV_BASE_OPEN={'/synthetic/AcmeSync':true};await loadConvos();
      const stored=JSON.parse(saved['tc.convos']);
      CV_SORT='new';CV_HUMAN_ONLY=false;CV_BASE_OPEN={};cvLoadPrefs();
      return {stored,back:[CV_SORT,CV_HUMAN_ONLY,CV_BASE_OPEN]};})()""",
              STUB + "CONVOS=" + json.dumps(convos_case()) + ";")
    assert got["stored"] == {"sort": "size", "humanOnly": True, "open": {"/synthetic/AcmeSync": True}}
    assert "acme secret" not in json.dumps(got["stored"])
    assert got["back"] == ["size", True, {"/synthetic/AcmeSync": True}]
    # 清空搜索后回到记下的展开,而不是全部收起。
    html = run("CV_BASE_OPEN={'/synthetic/AcmeSync':true};CV_QUERY='zzz';renderConvos();CV_QUERY='';renderConvos();"
               "$('cvgroups').innerHTML", STUB + "CONVOS=" + json.dumps(convos_case()) + ";")
    assert 'class="cv-g open"' in html


def test_copy_path_in_the_row_menu_closes_the_menu_but_the_location_copy_does_not_touch_menus():
    """行菜单里的「复制文件路径」和同一菜单的其他项一样,选中就收起菜单;项目位置里的复制按钮不在菜单里。"""
    got = run("""(async()=>{let copied=[],hidden=0,toasts=[];
      navigator={clipboard:{writeText:async p=>{copied.push(p);}}};toast=(m,t)=>toasts.push(t);
      const menu={id:'cvm-synthetic',matches:s=>s===':popover-open',hidePopover(){hidden++;}};
      const inMenu={dataset:{cvcopy:'/synthetic/a.jsonl'}};
      inMenu.closest=s=>s==='[data-cvcopy]'?inMenu:s==='[popover]'?menu:null;
      const loose={dataset:{cvcopy:'/synthetic/AcmeSync'}};loose.closest=s=>s==='[data-cvcopy]'?loose:null;
      let stopped=0;const ev=target=>({target,stopPropagation(){stopped++;}});
      cvClick(ev(inMenu));await null;const afterMenu=hidden;
      cvClick(ev(loose));await null;
      return {afterMenu,hidden,copied,stopped,toasts};})()""")
    assert got["afterMenu"] == 1 and got["hidden"] == 1
    assert got["copied"] == ["/synthetic/a.jsonl", "/synthetic/AcmeSync"]
    assert got["stopped"] == 2 and got["toasts"] == ["ok", "ok"]
    # 上面的假菜单对应真实标记:复制文件路径那一项确实画在行菜单的 popover 里。
    html = run("cvRows(CONVOS.groups[0])", STUB + "CONVOS=" + json.dumps(convos_case()) + ";")
    menu = html.split(" popover data-cvmenu-for=", 1)[1].split("</div>", 1)[0]
    assert "data-cvcopy=" in menu and "复制文件路径" in menu
