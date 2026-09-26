"""对话链面板(static/panels/convchain.js)的交互契约。数据全部是合成的。

后端 convtree.py 负责判定;这里钉的是前端画结论时不许走样的几件事:
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
    chain = chain_case()
    frm, last = chain["turns"][0]["steps"][1]["u"], chain["turns"][3]["steps"][-1]["u"]
    expr = (f"CH_FROM='{frm}';CH_SEL='h:3';CH_LEAF='{uid(7)}';CH_XT=true;"
            "chExport().then(()=>sent)")
    extra = ("let sent=null;api=async(path,options)=>{sent={path,body:JSON.parse(options.body)};"
             "return {filename:'x.md',text:'',nodes:1,turns:1};};"
             "var URL={createObjectURL:()=>'blob:x',revokeObjectURL(){}};var Blob=function(){};"
             "document.createElement=()=>({click(){},remove(){}});document.body={appendChild(){}};")
    sent = run(expr, setup(chain, extra))
    assert sent["path"] == "/api/convo/export"
    assert sent["body"] == {"id": SID, "to": last, "from": frm, "leaf": uid(7),
                            "tools": True, "thinking": False}


def test_subagent_view_cannot_fork_and_fork_controls_are_guarded():
    chain = chain_case()
    html = run(f"CH_SUB='{AGENT}';CH_SEL='s:0:1';chRenderAct();$('chact').innerHTML", setup(chain))
    assert 'data-ctfork="at" disabled' in html and "子代理的转录不能分叉" in html
    html = run("CH_SEL='s:0:1';chRenderAct();$('chact').innerHTML", setup(chain))
    assert 'data-ctfork="at" title' in html
    selector = run("ConsoleActions.selector", STUB)
    assert "[data-ctfork]" in selector and "[data-ctexport]" in selector, \
        "只读预览和进行中的操作必须能禁用这两个 POST 按钮"


def test_deep_link_is_parsed_and_back_to_the_list_closes_the_chain():
    assert run(f"chParseArg('{SID}/{AGENT}/leaf={uid(3)}')", STUB) == {"id": SID, "sub": AGENT, "leaf": uid(3)}
    assert run(f"chParseArg('{SID}/leaf={uid(3)}')", STUB) == {"id": SID, "sub": None, "leaf": uid(3)}
    # 后退到裸 #convos(不是主动导航):关掉链。
    closed = run("$('chbox').hidden=false;var location={hash:'#convos'};convoChainRoute(null,false);[CH_ID,$('chbox').hidden]",
                 setup(chain_case()))
    assert closed == [None, True]
    # Shift+A 收回摊开也会走 showView("convos", false),那时地址还带着会话 id:链必须留着。
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
