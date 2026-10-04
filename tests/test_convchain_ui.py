"""对话链面板(static/panels/convchain.js)的交互契约。数据全部是合成的。

后端判定在 convo-chain 库里(server.py 的四条路由只转接);这里钉的是前端画结论时不许走样的几件事:
收起的轮不渲染步骤、缺失和零分开显示、选中一轮的标题行时范围终点落在这一轮的最后一步、
换分支后不在新链上的起止点被丢掉、导出和分叉请求的正文形状、深链的解析与回退、
以及面板没载入时会话列表退回原来的「复制路径」。
"""
import json

from test_operations_ui import run
from test_panel_parity import module_source

SID = "0000000a-0000-4000-8000-000000000001"
AGENT = "a0synthetic"


def uid(n):
    return f"{n:08x}-0000-4000-8000-00000000abcd"


def chain_case(turns=3, steps=4, **extra):
    """合成的一条显示链:turns 轮,每轮 steps 步,第一轮之后有一对压缩边界 + 概括。"""
    out, n = [], 1
    for k in range(turns):
        st = []
        for s in range(steps):
            kind = "human" if s == 0 else ("tool" if s % 2 else "text")
            step = {"u": uid(n), "kind": kind, "preview": f"synthetic step {k}.{s}",
                    "ts": "2030-01-02T03:04:05Z", "lineIndex": n}
            if kind == "tool":
                step["name"] = "Read"
            st.append(step)
            n += 1
        out.append({"type": "turn", "k": k, "u": st[0]["u"], "ts": st[0]["ts"],
                    "human": {"u": st[0]["u"], "ts": st[0]["ts"], "preview": f"synthetic question {k}"},
                    "steps": st, "counts": {"human": 1, "tool": steps // 2, "text": (steps - 1) // 2},
                    "forks": []})
        if k == 0:
            out.append({"type": "marker", "kind": "compact", "u": uid(900), "lineIndex": 900,
                        "ts": None, "preview": "", "forks": [], "trigger": "auto",
                        "preTokens": 150000, "postTokens": 9000})
            out.append({"type": "marker", "kind": "summary", "u": uid(901), "lineIndex": 901,
                        "ts": None, "preview": "synthetic summary", "forks": []})
    base = {"available": True, "id": SID, "sub": None, "file": "/synthetic/p/x.jsonl",
            "projectDir": "p", "title": "Synthetic chain", "bytes": 4096, "lines": 40,
            "badLines": 0, "incompleteTail": False, "chainEntries": n, "danglingParents": None,
            "duplicateUuids": 0, "indexMs": 3, "cached": False, "cwd": "/synthetic/work",
            "leaf": uid(n - 1), "leafIsDefault": True, "pathLen": n, "turns": out,
            "compactions": 1, "forks": 0, "subagents": [], "subagentMetaUnreadable": 0, "warnings": []}
    base.update(extra)
    return base


# 假 DOM 里没有 querySelectorAll / scrollIntoView;toast 在 node 里会留下一个会炸的定时器。
STUB = """
toast=()=>{};
Object.assign($('chlist'),{querySelectorAll:()=>[],querySelector:()=>null,setAttribute(){},scrollTop:0});
Object.assign($('chbox'),{hidden:true});
"""


def setup(chain=None, extra=""):
    s = STUB
    if chain is not None:
        s += "CH=" + json.dumps(chain) + ";CH_ID=CH.id;chIndex();"
    return s + extra


def test_turns_start_collapsed_and_steps_render_only_on_expand():
    chain = chain_case(turns=200, steps=50)
    # 在 node 里数,不把整段 HTML 搬回来:投毒后它有几兆,搬运本身会拖垮这条用例。
    count = ("(h=>({turns:h.split('class=\"ch-t').length-1,steps:h.split('class=\"ch-s ').length-1,"
             "cmp:h.split('class=\"ch-cmp').length-1,sum:h.split('class=\"ch-sum').length-1}))($('chlist').innerHTML)")
    got = run("chRenderList();" + count, setup(chain))
    assert got["turns"] == 200
    assert got["steps"] == 0, "收起的轮不许渲染步骤,长会话会因此卡住"
    # 压缩边界收起时,它下面的概括也不画。
    assert got["cmp"] == 1 and got["sum"] == 0
    assert run("CH_OPEN[3]=true;chRenderList();" + count, setup(chain))["steps"] == 50


def test_compaction_marker_shows_tokens_and_summary_on_expand():
    html = run("CH_OPEN[1]=true;chRenderList();$('chlist').innerHTML", setup(chain_case()))
    assert "⟂ 自动压缩 · 150k→9k tokens" in html
    assert "压缩概括:synthetic summary" in html
    # 没有 token 记录时说没有记录,不画一个 0→0。
    missing = chain_case()
    missing["turns"][1].update(preTokens=None, postTokens=None)
    assert "tokens 未记录" in run("chRenderList();$('chlist').innerHTML", setup(missing))


def test_missing_counts_are_not_drawn_as_zero():
    head = run("chRenderHead();$('chhead').innerHTML", setup(chain_case()))
    assert "悬空父节点 未记录" in head, "后端没给的计数必须写成未记录"
    assert "坏行 0" in head
    unavailable = run("chRenderHead();$('chhead').innerHTML",
                      setup({"available": False, "reason": "synthetic reason"}))
    assert "synthetic reason" in unavailable and "var(--warn)" in unavailable


def test_a_selected_turn_header_ends_the_range_at_its_last_step():
    chain = chain_case()
    last = chain["turns"][0]["steps"][-1]["u"]
    assert run("CH_SEL='h:0';chSelEnd()", setup(chain)) == last
    assert run("CH_SEL='s:0:1';chSelEnd()", setup(chain)) == chain["turns"][0]["steps"][1]["u"]
    # 没选节点也没设终点:范围到链尾(3 轮 x 4 步 + 压缩边界 + 概括 = 14 个节点)。
    assert run("CH_SEL=null;chRange()", setup(chain)) == [0, 13]


def test_range_endpoints_off_the_new_branch_are_dropped():
    chain = chain_case()
    kept, gone = chain["turns"][0]["steps"][1]["u"], uid(4242)
    result = run(f"CH_FROM='{kept}';CH_TO='{gone}';chAfterLoad(null,null,false,[]);[CH_FROM,CH_TO]", setup(chain))
    assert result == [kept, None]


def test_export_request_carries_range_leaf_and_boolean_switches():
    """导出是一次 GET:只读预览里也能导,也不记成一次「操作」。设了起点没设终点时导到链尾。"""
    chain = chain_case()
    frm, last = chain["turns"][0]["steps"][1]["u"], chain["turns"][-1]["steps"][-1]["u"]
    expr = (f"CH_FROM='{frm}';CH_SEL='h:3';CH_LEAF='{uid(7)}';CH_XT=true;"
            "chExport().then(()=>sent)")
    extra = ("let sent=null;api=async(path,options)=>{const [p,q]=path.split('?');"
             "sent={path:p,method:(options||{}).method||'GET',"
             "query:Object.fromEntries(q.split('&').map(x=>x.split('=').map(decodeURIComponent)))};"
             "return {filename:'x.md',text:'',nodes:1,turns:1};};"
             "var URL={createObjectURL:()=>'blob:x',revokeObjectURL(){}};var Blob=function(){};"
             "document.createElement=()=>({click(){},remove(){}});document.body={appendChild(){}};")
    sent = run(expr, setup(chain, extra))
    assert sent["path"] == "/api/convo/export" and sent["method"] == "GET"
    assert sent["query"] == {"id": SID, "to": last, "from": frm, "leaf": uid(7),
                             "tools": "1", "thinking": "0"}


def test_subagent_view_cannot_fork_and_fork_controls_are_guarded():
    chain = chain_case()
    html = run(f"CH_SUB='{AGENT}';CH_SEL='s:0:1';chRenderAct();$('chact').innerHTML", setup(chain))
    assert 'data-ctfork="at" disabled' in html and "子代理的转录不能分叉" in html
    html = run("CH_SEL='s:0:1';chRenderAct();$('chact').innerHTML", setup(chain))
    assert 'data-ctfork="at" title' in html
    selector = run("ConsoleActions.selector", STUB)
    assert "[data-ctfork]" in selector, "只读预览和进行中的操作必须能禁用分叉这个 POST 按钮"
    # 导出是读:不进写操作的选择器,只读预览里照样能点。
    assert "export" not in selector
    html = run("CH_SEL='s:0:1';chRenderAct();$('chact').innerHTML", setup(chain))
    assert 'data-chexport="md"' in html and "data-ctexport" not in html


def test_deep_link_is_parsed_and_back_to_the_list_closes_the_chain():
    assert run(f"chParseArg('{SID}/{AGENT}/leaf={uid(3)}')", STUB) == {"id": SID, "sub": AGENT, "leaf": uid(3)}
    assert run(f"chParseArg('{SID}/leaf={uid(3)}')", STUB) == {"id": SID, "sub": None, "leaf": uid(3)}
    # 后退到裸 #convos(不是主动导航):关掉链。
    closed = run("$('chbox').hidden=false;var location={hash:'#convos'};convoChainRoute(null,false);[CH_ID,$('chbox').hidden]",
                 setup(chain_case()))
    assert closed == [None, True]
    # 不是主动导航的 showView("convos", false),地址还带着会话 id:不是后退回列表,链必须留着。
    kept_open = run(f"$('chbox').hidden=false;var location={{hash:'#convos/{SID}'}};convoChainRoute(null,false);[CH_ID,$('chbox').hidden]",
                    setup(chain_case()))
    assert kept_open == [SID, False]
    # 从侧栏回到会话屏(主动导航)且链开着:地址保留链的那一段。
    kept = run(f"$('chbox').hidden=false;CH_LEAF='{uid(5)}';convoChainRoute(null,true)", setup(chain_case()))
    assert kept == f"convos/{SID}/leaf={uid(5)}"
    # 别的分区名后面跟了东西就当没跟。
    assert run("showView('repos/whatever',false);CURVIEW", STUB + "loadPageOnce=()=>{};$('view').classList={remove(){}};"
               "document.querySelectorAll=()=>[];var window={scrollTo(){}};") == "repos"


def test_rows_open_the_chain_only_when_the_panel_loaded_and_the_id_is_a_session():
    rows = [{"id": SID, "title": "Synthetic A", "titleFrom": "rename", "file": "/synthetic/a.jsonl",
             "humanSeen": 2, "partial": False, "ageHours": 1, "bytes": 10, "preview": ""},
            {"id": "not-a-session", "title": "Synthetic B", "titleFrom": "rename", "file": "/synthetic/b.jsonl",
             "humanSeen": 2, "partial": False, "ageHours": 1, "bytes": 10, "preview": ""}]
    convos = {"available": True, "summary": {"files": 2, "humanish": 1, "bytes": 20, "groups": 1},
              "groups": [{"cwd": "/synthetic/p", "count": 2, "humanish": 1, "bytes": 20,
                          "newest": 1, "truncated": False, "shown": rows}]}
    base = STUB + "CONVOS=" + json.dumps(convos) + ";CV_OPEN['/synthetic/p']=true;"
    html = run("renderConvos();$('cvgroups').innerHTML", base)
    assert html.count("data-cvid=") == 1 and f'data-cvid="{SID}"' in html
    assert 'data-cvcopy="/synthetic/a.jsonl"' in html
    # 面板没载入:整行照旧是复制路径,也不画打开对话链的提示。
    html = run("renderConvos();$('cvgroups').innerHTML", base + "openConvoChain=undefined;")
    assert "data-cvid=" not in html and "点击打开对话链" not in html


def test_the_panel_touches_no_dom_at_load_time():
    """可选面板的边界:载入时只定义全局,不读不写 DOM。"""
    program = """
const vm=require('node:vm');let touched=0;
const document={querySelector:()=>{touched++;return null;},getElementById:()=>{touched++;return null;}};
const context=vm.createContext({document});
""" + "vm.runInContext(" + json.dumps(module_source("panels/convchain.js")) + ",context);" + \
        "console.log(JSON.stringify([touched,vm.runInContext('typeof startConvoChain',context)]));"
    from test_panel_parity import node
    assert node(program) == [0, "function"]


def test_page_refresh_rereads_an_open_chain_and_keeps_the_selection():
    """会话屏的「刷新」也重读开着的链;没开链时一个请求都不发。"""
    chain = chain_case()
    keep = chain["turns"][3]["steps"][2]["u"]
    extra = ("let calls=[];api=async(path)=>{calls.push(path);return JSON.parse(JSON.stringify(CH));};"
             "CURVIEW='convos';var location={hash:''};var history={pushState(){}};"
             "$('chlist').querySelector=()=>({scrollIntoView(){},id:'x'});")
    closed = run("reloadConvoChain().then(()=>calls.length)", setup(chain, extra))
    assert closed == 0, "没开链时刷新不许去读转录"
    got = run(f"$('chbox').hidden=false;CH_LEAF='{uid(2)}';CH_OPEN[3]=true;CH_SEL='s:3:2';"
              "reloadConvoChain().then(()=>[calls,chSelU(),!!CH_OPEN[3]])", setup(chain, extra))
    assert got[0] == [f"/api/convo/chain?id={SID}&leaf={uid(2)}"]
    assert got[1] == keep and got[2] is True
    assert "reloadConvoChain" in module_source("operations.js"), "刷新按钮要经过 PAGE_READS 才会重读链"



# ---------- 审查修复 ----------

def test_token_counts_and_times_from_the_transcript_are_escaped():
    """转录是外来数据。后端把 token 数压成整数之外,前端自己也不把非数字当数画。"""
    chain = chain_case()
    chain["turns"][1].update(preTokens="<img src=x onerror=alert(1)>", postTokens=9000)
    chain["turns"][0]["ts"] = "<svg onload='x"
    chain["turns"][0]["human"]["ts"] = "<svg onload='x"
    chain["turns"][0]["steps"][1]["ts"] = "<b>x"
    html = run("CH_OPEN[0]=true;chRenderList();$('chlist').innerHTML", setup(chain))
    assert "<img" not in html and "<svg" not in html and "<b>" not in html
    assert "⟂ 自动压缩 · ?→9k tokens" in html
    assert run("esc(\"a'b\")", STUB) == "a&#39;b"


def test_a_start_without_an_end_exports_to_the_end_of_the_chain():
    chain = chain_case()
    start = chain["turns"][3]["steps"][0]["u"]
    got = run(f"CH_SEL='s:3:0';CH_FROM='{start}';[chRange(),CH_ORDER.length-1]", setup(chain))
    assert got[0][1] == got[1] and got[0][0] < got[0][1]
    html = run(f"CH_SEL='s:3:0';CH_FROM='{start}';chRenderAct();$('chact').innerHTML", setup(chain))
    assert "到链尾" in html
    # 设了终点就用终点。
    end = chain["turns"][3]["steps"][2]["u"]
    got = run(f"CH_FROM='{start}';CH_TO='{end}';chRange()", setup(chain))
    assert got[1] - got[0] == 2


def test_the_fork_button_names_the_node_it_forks_at():
    chain = chain_case()
    last0 = chain["turns"][0]["steps"][-1]["u"]
    html = run("CH_SEL='h:0';chRenderAct();$('chact').innerHTML", setup(chain))
    assert f"从本轮末尾新建会话：包含到消息 {last0[:8]} 为止的历史" in html
    one = chain["turns"][0]["steps"][1]["u"]
    html = run("CH_SEL='s:0:1';chRenderAct();$('chact').innerHTML", setup(chain))
    assert f"从这里新建会话：包含到消息 {one[:8]} 为止的历史" in html


def test_chain_read_failures_stay_visible_outside_file_details():
    header, warning = run("chRenderHead();[$('chhead').innerHTML,$('chwarn').innerHTML]",
                          setup(chain_case(badLines=2, danglingParents=1)))
    # 文件详情收进「更多操作与详情」菜单(popover),读取警告留在菜单外面,一直看得见。
    assert 'popovertarget="ch-more"' in header and 'id="ch-more" popover' in header
    assert '坏行 2' in warning and '悬空父节点 1' in warning
    assert '<details' not in warning and 'popover' not in warning


def test_branch_alternatives_show_where_each_one_starts_and_ends():
    chain = chain_case()
    u = chain["turns"][0]["steps"][1]["u"]
    alts = [{"u": uid(500 + i), "size": 3, "leaf": uid(600 + i), "leafLineIndex": 10 + i,
             "preview": "same question", "active": i == 0, "ts": "2030-01-02T03:04:05Z",
             "firstKind": "human", "firstPreview": "same question", "leafTs": "2030-01-02T03:04:05Z",
             "leafKind": "text", "leafPreview": f"distinct ending {i}"} for i in range(2)]
    chain["turns"][0]["forks"] = [{"u": u, "lineIndex": 2, "alternatives": alts}]
    chain["turns"][0]["steps"][1]["fork"] = 2
    html = run(f"CH_OPEN[0]=true;CH_FKOPEN='{u}';chRenderList();$('chlist').innerHTML", setup(chain))
    assert "distinct ending 0" in html and "distinct ending 1" in html


def test_a_collapsed_turn_shows_its_last_reply():
    chain = chain_case()
    chain["turns"][0]["reply"] = {"u": uid(3), "ts": None, "preview": "synthetic final reply"}
    html = run("chRenderList();$('chlist').innerHTML", setup(chain))
    assert "synthetic final reply" in html and 'class="rp"' in html


def test_buttons_inside_the_card_hand_focus_back_to_the_chain():
    """⑂ 菜单、设为起点等按钮会整块重画,焦点掉回 <body> 后 j/k 被全局吞掉、Esc 抛错。"""
    chain = chain_case()
    u = chain["turns"][0]["steps"][1]["u"]
    chain["turns"][0]["forks"] = [{"u": u, "lineIndex": 2, "alternatives": []}]
    extra = ("let focused=0;$('chlist').focus=()=>{focused++};$('chbox').hidden=false;"
             "const btn=(attr,val)=>({closest:sel=>sel==='['+attr+']'?{dataset:{[attr.slice(5).replace(/-(.)/g,(m,c)=>c.toUpperCase())]:val},disabled:false}:null});")
    # ⑂ 由浏览器开合菜单(popovertarget),chClick 不改状态、不抢焦点:人要用 Tab 走进菜单。
    got = run(f"CH_SEL='s:0:1';chClick({{target:btn('data-chfk','{u}')}});"
              "const after=[focused,CH_FKOPEN];chClick({target:btn('data-chact','from')});[...after,focused,CH_FROM]", setup(chain, extra))
    assert got == [0, None, 1, u]


def test_subagent_picker_filters_and_names_the_spawning_turn():
    subs = [{"agentId": "a1synthetic", "description": "alpha task", "agentType": "general", "gz": False,
             "toolUseId": None, "file": "x"},
            {"agentId": "a2synthetic", "description": "beta task", "agentType": None, "gz": True,
             "toolUseId": None, "file": "y"}]
    chain = chain_case(subagents=subs)
    chain["turns"][3]["steps"][1].update(agentId="a2synthetic", agentFile=True)
    all_ = run("chSubOptions('')", setup(chain))
    assert "共 2 个" in all_ and "#1 · beta task (gz)" in all_
    some = run("chSubOptions('alpha')", setup(chain))
    assert "筛出 1 / 2 个" in some and "a1synthetic" in some and "a2synthetic" not in some
    head = run("chRenderHead();$('chcrumb').innerHTML", setup(chain))
    assert 'id="chsubq"' in head and 'id="chsubs"' in head


def test_rows_say_the_chain_can_be_opened():
    rows = [{"id": SID, "title": "Synthetic A", "titleFrom": "rename", "file": "/synthetic/a.jsonl",
             "humanSeen": 2, "partial": False, "ageHours": 1, "bytes": 10, "preview": ""}]
    convos = {"available": True, "summary": {"files": 1, "humanish": 1, "bytes": 10, "groups": 1},
              "groups": [{"cwd": "/synthetic/p", "count": 1, "humanish": 1, "bytes": 10,
                          "newest": 1, "truncated": False, "shown": rows}]}
    base = STUB + "CONVOS=" + json.dumps(convos) + ";CV_OPEN['/synthetic/p']=true;"
    html = run("renderConvos();$('cvgroups').innerHTML", base)
    # 标题本身就是打开会话的按钮;不再另画一个重复的「查看会话」眼睛,也没有常驻的删除按钮。
    assert f'data-cvopen="{SID}" title="查看会话：Synthetic A"' in html
    assert 'class="control-label">查看会话</span>' not in html
    assert "对话链 ›" not in run("renderConvos();$('cvgroups').innerHTML", base + "openConvoChain=undefined;")


def test_escape_before_the_task_data_loaded_does_not_throw():
    """页面级 Esc 以前无条件 render(),而 DATA 在第一次读回来之前是 null。"""
    from test_operations_ui import run as page_run
    result = page_run("""(()=>{
      document.activeElement={tagName:'BODY'};document.querySelector=()=>null;document.querySelectorAll=()=>[];
      DATA=null;CURVIEW='tasks';sel=new Set(['AcmeSync']);
      const event={key:'Escape',preventDefault(){this.prevented=true;}};
      return [handleEscape(event),sel.size,!!event.prevented,$('bulk').hidden];
    })()""")
    assert result == [True, 0, True, True]


def test_clicking_another_turn_header_drops_the_old_fork_result():
    """点标题行时 chClick 先改了 CH_SEL 再叫 chSelect,上一个节点的分叉结果就一直挂着,
    看起来像是在说新选的这一轮。"""
    chain = chain_case()
    extra = ("$('chbox').hidden=false;"
             "const hdr=k=>({closest:sel=>sel==='[data-chk]'?{dataset:{chk:k},getBoundingClientRect:()=>({top:0}),classList:{contains:()=>false}}:null});")
    got = run("CH_SEL='h:0';CH_FRES={newId:'x'};chClick({target:hdr('h:3')});[CH_SEL,CH_FRES]", setup(chain, extra))
    assert got == ["h:3", None]
