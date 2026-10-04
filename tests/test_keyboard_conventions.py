"""键盘约定:没有快捷键,只有 Esc 退一层、Enter 提交搜索。数据全部是合成的。

逻辑在 navigation.js / tasks.js / calls.js / convchain.js 里,events.js 只接 keydown,
所以这里用 node:vm 台架直接调那几个函数,不需要浏览器。真实浏览器里的走查另见交付说明。
"""
import json
import re
from pathlib import Path

from test_operations_ui import run
from test_automation_ui import table_setup
from test_panel_parity import module_source

PAGE = Path(__file__).resolve().parents[1] / "scripts" / "task_console" / "console.html"

# 一个最小的按键事件和几种焦点元素。dispatched 记下发出去的事件,用来确认清空搜索框之后真的发了 input。
KEYS = """
var Event=class{constructor(type,options){this.type=type;this.bubbles=!!(options&&options.bubbles);}};
const key=(k,extra={})=>({key:k,prevented:false,preventDefault(){this.prevented=true;},...extra});
const field=(tagName,type,value)=>({tagName,type,value,blurred:0,dispatched:[],blur(){this.blurred++;},dispatchEvent(e){this.dispatched.push(e.type);}});
const body={tagName:'BODY'};
document.activeElement=body;
document.querySelector=selector=>null;
document.querySelectorAll=selector=>[];
"""


def tasks_setup(extra=""):
    return table_setup() + "ROWS.push({...row,name:'AcmeThird',info:{title:'Mike'}});" + KEYS + extra


def test_escape_closes_the_detail_first_and_clears_the_selection_second():
    result = run("""(()=>{
      CURVIEW='tasks';render();OPEN_DETAIL='AcmeSync';sel=new Set(['AcmeSync','AcmeOther']);
      const first=key('Escape');handleEscape(first);
      const afterFirst=[OPEN_DETAIL,sel.size,first.prevented];
      const second=key('Escape');handleEscape(second);
      const third=key('Escape');const handled=handleEscape(third);
      return {afterFirst,afterSecond:sel.size,secondPrevented:second.prevented,idle:[handled,third.prevented]};
    })()""", tasks_setup())
    assert result == {"afterFirst": [None, 2, True], "afterSecond": 0, "secondPrevented": True, "idle": [False, False]}


def test_escape_never_acts_on_a_view_that_is_not_shown():
    # 以前在仓库页按 Esc 会清空运行详情里那份看不见的选择。
    result = run("""(()=>{
      CURVIEW='repos';OPEN_DETAIL='AcmeSync';sel=new Set(['AcmeSync','AcmeOther']);
      const event=key('Escape');const handled=handleEscape(event);
      return [handled,event.prevented,sel.size,OPEN_DETAIL];
    })()""", tasks_setup())
    assert result == [False, False, 2, "AcmeSync"]


def test_a_focused_checkbox_is_not_a_text_box():
    # 刚勾完一个任务,焦点停在勾选框上:一次 Esc 就该清掉选择,不是先让勾选框失焦。
    result = run("""(()=>{
      CURVIEW='tasks';render();sel=new Set(['AcmeSync','AcmeOther']);
      const box=field('INPUT','checkbox','on');document.activeElement=box;
      handleEscape(key('Escape'));
      return [sel.size,box.blurred,box.value];
    })()""", tasks_setup())
    assert result == [0, 0, "on"]


def test_escape_clears_a_search_box_then_leaves_it():
    result = run("""(()=>{
      CURVIEW='tasks';const box=field('INPUT','search','Acme');document.activeElement=box;
      sel=new Set(['AcmeSync']);
      const first=key('Escape');handleEscape(first);
      const afterFirst=[box.value,box.dispatched.slice(),box.blurred,first.prevented];
      const second=key('Escape');handleEscape(second);
      return {afterFirst,afterSecond:[box.value,box.dispatched.length,box.blurred,second.prevented],sel:sel.size};
    })()""", tasks_setup())
    # 第一下清空并发 input(让原有的筛选监听重画),第二下才离开;两下都不碰页面上那一层。
    assert result == {"afterFirst": ["", ["input"], 0, True], "afterSecond": ["", 1, 1, True], "sel": 1}


def test_escape_only_blurs_written_text_and_ignores_input_method_composition():
    result = run("""(()=>{
      CURVIEW='tasks';const note=field('TEXTAREA','textarea','手写的原因');document.activeElement=note;
      handleEscape(key('Escape'));
      const search=field('INPUT','search','ce');document.activeElement=search;
      const composing=key('Escape',{isComposing:true});const handled=handleEscape(composing);
      return [note.value,note.blurred,search.value,handled,composing.prevented];
    })()""", tasks_setup())
    assert result == ["手写的原因", 1, "ce", False, False]


# 「显示列」那种下拉:Bootstrap 只在按键落在菜单或它的按钮上时才收起。焦点掉回 body(点了分组标题或空白处)时,
# 这里收起它;落在里面时留给 Bootstrap。两种情况都不退页面上的层、不清搜索框。
DROPDOWN = """
const classes=names=>{const set=new Set(names);return {remove:c=>set.delete(c),contains:c=>set.has(c)};};
const toggle={attrs:{},focused:0,classList:classes(['dropdown-toggle','show']),setAttribute(k,v){this.attrs[k]=v;},focus(){this.focused++;}};
const inside={tagName:'DIV'};
const box={contains:el=>el===inside || el===toggle,querySelector:s=>s==='[data-bs-toggle="dropdown"]'?toggle:null};
const menu={classList:classes(['dropdown-menu','show']),closest:s=>s==='.dropdown'?box:null};
document.querySelector=s=>s==='.dropdown-menu.show' && menu.classList.contains('show')?menu:null;
"""


def test_escape_closes_a_column_menu_that_focus_fell_out_of():
    result = run("""(()=>{
      CURVIEW='tasks';render();OPEN_DETAIL='AcmeSync';sel=new Set(['AcmeSync']);
      const own=key('Escape',{target:inside});const ownHandled=handleEscape(own);
      const ownState=[ownHandled,own.prevented,menu.classList.contains('show')];
      const search=field('INPUT','search','Acme');document.activeElement=search;
      const stray=key('Escape',{target:search});const handled=handleEscape(stray);
      const after=[handled,stray.prevented,menu.classList.contains('show'),toggle.classList.contains('show'),
        toggle.attrs['aria-expanded'],toggle.focused,search.value,OPEN_DETAIL,sel.size];
      document.activeElement=body;handleEscape(key('Escape',{target:body}));
      return {ownState,after,next:OPEN_DETAIL};
    })()""", tasks_setup(DROPDOWN))
    assert result["ownState"] == [False, False, True]
    assert result["after"] == [True, True, False, False, "false", 1, "Acme", "AcmeSync", 1]
    # 菜单关上之后,下一次 Esc 才退页面上的那一层。
    assert result["next"] is None


def test_escape_hides_the_column_menu_through_its_dropdown_instance_when_there_is_one():
    result = run("""(()=>{
      CURVIEW='tasks';let hidden=0;
      globalThis.tabler={Dropdown:{getInstance:el=>el===toggle?{hide(){hidden++;}}:null}};
      const event=key('Escape',{target:body});const handled=handleEscape(event);
      return [handled,event.prevented,hidden,menu.classList.contains('show'),toggle.focused];
    })()""", tasks_setup(DROPDOWN))
    # 有 Bootstrap 实例时由它收起(它会同时改好自己的状态),这里不另去摘类名。
    assert result == [True, True, 1, True, 1]


def test_escape_leaves_open_dialogs_and_menus_to_themselves():
    for selector in ("dialog[open]", ":popover-open"):
        result = run("""(()=>{
          CURVIEW='tasks';OPEN_DETAIL='AcmeSync';sel=new Set(['AcmeSync']);
          const box=field('INPUT','search','Acme');document.activeElement=box;
          const event=key('Escape');const handled=handleEscape(event);
          document.activeElement=body;handleEscape(key('Escape'));
          return [handled,event.prevented,box.value,OPEN_DETAIL,sel.size];
        })()""", tasks_setup(f"document.querySelector=s=>s==={json.dumps(selector)}?{{}}:null;"))
        assert result == [False, False, "Acme", "AcmeSync", 1], selector


def test_escape_closes_an_open_model_call():
    result = run("""(()=>{
      CURVIEW='llm';LMOPEN=3;LMROWS=null;
      const first=key('Escape');handleEscape(first);
      const second=key('Escape');return [LMOPEN,first.prevented,handleEscape(second),second.prevented];
    })()""", KEYS)
    assert result == [None, True, False, False]


def test_escape_backs_out_of_a_conversation_chain_one_layer_at_a_time():
    stub = KEYS + """
var location={hash:'#convos/acme'};const calls=[];
chRenderList=()=>calls.push('list');chFocusList=()=>{};openConvoChain=(id,opts)=>calls.push('open:'+id+':'+JSON.stringify(opts));
CURVIEW='convos';CH_ID='acme-session';CH_SUB='acme-agent';CH_FKOPEN='acme-node';$('chbox').hidden=false;
"""
    result = run("""(()=>{
      const steps=[];
      handleEscape(key('Escape'));steps.push([CH_FKOPEN,CH_SUB,CH_ID,calls.length]);
      handleEscape(key('Escape'));steps.push(calls[calls.length-1].startsWith('open:acme-session'));
      CH_SUB=null;handleEscape(key('Escape'));steps.push([CH_ID,$('chbox').hidden]);
      steps.push(handleEscape(key('Escape')));
      return steps;
    })()""", stub)
    assert result == [[None, "acme-agent", "acme-session", 1], True, [None, True], False]


def test_select_all_box_selects_the_list_and_shows_a_partial_pick():
    result = run("""(()=>{
      CURVIEW='tasks';const box={};$('tbl').querySelector=s=>s==='input.selall'?box:null;
      render();const shown=VIEW.length;
      toggleSelectAll();const all=[sel.size,/class="selall"[^>]*checked/.test($('tbl').innerHTML),box.indeterminate];
      toggleSelectAll();const none=sel.size;
      sel.add(VIEW[0].name);render();
      return {shown,all,none,partial:box.indeterminate,label:/<th data-column="selc"[^>]*><input type="checkbox" class="selall" aria-label="全选当前列表"/.test($('tbl').innerHTML)};
    })()""", tasks_setup())
    assert result == {"shown": 3, "all": [3, True, False], "none": 0, "partial": True, "label": True}


def test_task_titles_are_buttons_that_keep_focus_across_a_rebuild():
    result = run("""(()=>{
      CURVIEW='tasks';OPEN_DETAIL='AcmeSync';render();
      const html=$('tbl').innerHTML;
      const el=(attrs)=>({attrs,getAttribute:k=>Object.hasOwn(attrs,k)?attrs[k]:null,hasAttribute:k=>Object.hasOwn(attrs,k),
        dataset:{},focus(){focused=this;}});
      let focused=null;
      const toggle=el({'data-row-toggle':'AcmeOther'});
      const after=[el({'data-row-toggle':'AcmeSync'}),el({'data-row-toggle':'AcmeOther'})];
      restoreTaskControlFocus({querySelectorAll:s=>after.filter(node=>node.hasAttribute(s.slice(1,-1)))},taskControlKey(toggle));
      return {key:taskControlKey(toggle),restored:after.indexOf(focused),
        open:html.includes('class="row-toggle" data-row-toggle="AcmeSync" aria-expanded="true"'),
        closed:html.includes('class="row-toggle" data-row-toggle="AcmeOther" aria-expanded="false"'),
        otherLists:taskListRow(ROWS[0]).includes('row-toggle')};
    })()""", tasks_setup())
    assert result == {"key": {"attr": "data-row-toggle", "value": "AcmeOther", "name": "AcmeOther"},
                      "restored": 1, "open": True, "closed": True, "otherLists": False}


def test_the_bulk_bar_belongs_to_run_details_and_says_what_is_selected():
    result = run("""(()=>{
      sel=new Set(['AcmeSync','AcmeOther']);
      CURVIEW='tasks';renderBulk();const shown=[$('bulk').hidden,$('bulk').innerHTML];
      CURVIEW='repos';renderBulk();
      return {shown,elsewhere:$('bulk').hidden,kept:sel.size};
    })()""", tasks_setup())
    assert result["shown"][0] is False and result["elsewhere"] is True and result["kept"] == 2
    html = re.sub(r"\s+", " ", result["shown"][1])
    assert "已选 <b>2</b> 个任务" in html
    for label in ("运行</button>", "启用</button>", "停用</button>", "取消选择</button>"):
        assert label in html
    assert "#i-close" in html and "#i-filter-clear" not in html
    # 换分区时由 showView 收起它。
    assert "renderBulk();" in module_source("navigation.js")


def test_the_detail_rows_carry_a_collapse_button():
    result = run("""(()=>{CURVIEW='tasks';render();return [detail(VIEW[0]),detailHTML({i:4,chain:[],ok:true})];})()""",
                 tasks_setup("LMBODY={4:null};"))
    assert 'data-detail-close' in result[0] and '#i-up' in result[0] and "收起</button>" in result[0]
    assert 'data-call-close' in run("""(()=>{LMROWS=[{i:4,chain:[],ok:true,attempts:1}];LMOPEN=4;LMTOTAL=1;
      $('lmprov').options=[1,2];$('lmcaller').options=[1,2];renderCalls();return $('lmtab').innerHTML;})()""", KEYS + "LMBODY={4:null};")


def test_enter_commits_a_search_without_waiting():
    result = run("""new Promise(done=>{
      let loads=0;loadCalls=async()=>{loads++;};
      $('lmq').value='timeout';callSearchInput('timeout');
      const event=key('Enter',{target:{id:'lmq'}});const handled=handleSearchEnter(event);
      const now=[LMQ.q,loads,handled,event.prevented];
      setTimeout(()=>done({now,later:loads}),320);
    })""", KEYS)
    # 防抖被取消:Enter 当场查一次,260ms 之后不会再查第二次。
    assert result == {"now": ["timeout", 1, True, True], "later": 1}


def test_enter_ignores_composition_and_boxes_that_are_not_searches():
    result = run("""(()=>{
      let loads=0;loadCalls=async()=>{loads++;};
      const composing=key('Enter',{target:{id:'lmq'},isComposing:true});
      const other=key('Enter',{target:{id:'acme-field'}});
      return [handleSearchEnter(composing),handleSearchEnter(other),loads,composing.prevented,other.prevented];
    })()""", KEYS)
    assert result == [False, False, 0, False, False]


def test_enter_opens_the_only_matching_task_and_nothing_when_several_match():
    setup = tasks_setup("""
const opened=[];const rowFor=i=>({dataset:{i:String(i),name:VIEW[i].name},insertAdjacentHTML:()=>opened.push(VIEW[i].name)});
document.querySelector=s=>{const m=/data-i="(\\d+)"/.exec(s);return m&&VIEW[+m[1]]?rowFor(+m[1]):null;};
""")
    result = run("""(()=>{
      CURVIEW='tasks';$('q').value='acme';render();
      handleSearchEnter(key('Enter',{target:{id:'q'}}));const several=[VIEW.length,opened.length,OPEN_DETAIL];
      $('q').value='acmeother';render();
      handleSearchEnter(key('Enter',{target:{id:'q'}}));
      return {several,one:[VIEW.length,opened.slice(),OPEN_DETAIL,$('q').value]};
    })()""", setup)
    assert result == {"several": [3, 0, None], "one": [1, ["AcmeOther"], "AcmeOther", "acmeother"]}


def test_enter_in_the_other_searches_goes_to_the_one_obvious_place():
    result = run("""(()=>{
      const out={};
      WORK={available:true};selectedWorkRows=()=>[{id:'acme-work-1'}];openWorkRecord=id=>out.work=id;
      handleSearchEnter(key('Enter',{target:{id:'work-search'}}));
      selectedWorkRows=()=>[{id:'acme-work-1'},{id:'acme-work-2'}];out.workSeveral=null;openWorkRecord=id=>out.workSeveral=id;
      handleSearchEnter(key('Enter',{target:{id:'work-search'}}));
      const clicked=[];const reviewRow=name=>({querySelector:()=>({click:()=>clicked.push(name)})});
      document.querySelectorAll=s=>s==='#todod .review-row'?[reviewRow('AcmeSync')]:[];
      handleSearchEnter(key('Enter',{target:{id:'review-search'}}));
      document.querySelectorAll=s=>s==='#todod .review-row'?[reviewRow('AcmeSync'),reviewRow('AcmeOther')]:[];
      handleSearchEnter(key('Enter',{target:{id:'review-search'}}));
      out.review=clicked;
      REPOS={available:true,repos:[{name:'acme-tools'},{name:'acme-tools-config',kind:'companion'},{name:'sample-notes'}]};
      $('rpq').value='config';$('rpacc').value='';$('rpkind').value='';$('rpvis').value='';
      $('rplist').querySelectorAll=()=>[{dataset:{rp:'acme-tools'}},{dataset:{rp:'acme-tools-config'}}];
      renderRepoList=()=>{};rpMatches=(r,q)=>r.name.includes(q);
      handleSearchEnter(key('Enter',{target:{id:'rpq'}}));out.repo=RP_SEL;
      let convos=0;loadConvos=async()=>{convos++;};handleSearchEnter(key('Enter',{target:{id:'cv-search'}}));out.convos=convos;
      return out;
    })()""", KEYS)
    assert result == {"work": "acme-work-1", "workSeveral": None, "review": ["AcmeSync"],
                      "repo": "acme-tools-config", "convos": 1}


def test_chain_list_keeps_listbox_keys_and_drops_letter_shortcuts():
    stub = KEYS + "CH={available:true,subagents:[{agentId:'agent-acme-1',description:'Acme 检查'},{agentId:'agent-sample-2',description:'Sample 汇总'}]};const moved=[];chMove=d=>moved.push(d);chSetEnd=()=>moved.push('range');chToggle=()=>moved.push('toggle');"
    result = run("""(()=>{
      const keys=['j','k','[',']','g','Escape','ArrowDown','ArrowUp','Home','End','Enter'];
      const eaten=keys.map(k=>chKey(key(k)));
      return {eaten,moved,first:chSubFirst('sample'),any:chSubFirst(''),none:chSubFirst('zzz')};
    })()""", stub)
    assert result["eaten"] == [False] * 6 + [True] * 5
    assert result["moved"] == [1, -1, "home", "end", "toggle"]
    assert result["first"] == "agent-sample-2" and result["any"] == "agent-acme-1" and result["none"] is None


def test_the_shortcut_layer_and_its_help_are_gone():
    html = PAGE.read_text(encoding="utf-8")
    for gone in ('id="help"', 'id="page-help"', 'i-keyboard', "j / k", "(Esc)"):
        assert gone not in html, gone
    assert 'type="search" id="lmq"' in html
    events = module_source("events.js")
    for gone in ("jkgGxaresd", 'k==="?"', "TABLE_KEYS", "ACTIVATABLE", "targets()", "toggleDetail"):
        assert gone not in events, gone
    static = Path(PAGE).parent / "static"
    sources = [p for p in list(static.glob("*.js")) + list(static.glob("panels/*.js")) + list(static.glob("*.css"))]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert "contains('all')" not in text and 'contains("all")' not in text and "#view.all" not in text, path.name
        assert "#help" not in text and "page-help" not in text, path.name
    chain = module_source("panels/convchain.js")
    assert "jkgGxaresd" not in chain and 'k==="["' not in chain and 'title="设为起点 ["' not in chain
