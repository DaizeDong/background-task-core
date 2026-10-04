"""外框:侧栏和标签上的徽章、顶栏的刷新与「⋯」、标题、记住的标签、下钻后的「← 返回」、会话链的面包屑。

数据全部是合成的(AcmeSync 之类)。行为用 node:vm 台架直接调 navigation.js / operations.js / overview.js /
calls.js / convchain.js 里的函数,不需要浏览器;真实浏览器里的走查另见交付说明。
"""
import json
import re
from pathlib import Path

from test_keyboard_conventions import tasks_setup
from test_operations_ui import run
from test_panel_parity import node

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "scripts" / "task_console" / "static"
PAGE = STATIC.parent / "console.html"

# 让真的 showView 能在台架里跑:没有 DOM 列表、没有地址栏,各屏的读取换成空的。
SHOW = """
document.querySelectorAll=()=>[];
var location={hash:''};
renderBulk=()=>{};loadRepairs=()=>{};renderAutomations=()=>{};
for(const key of Object.keys(PAGE_READS)) PAGE_READS[key]=[];
"""


def badge(selector="bg-automations"):
    return f"(e=>({{text:e.textContent,hidden:e.hidden,cls:e.className,title:e.title}}))($('{selector}'))"


def test_a_badge_is_pending_until_its_source_settles_then_counts_then_shows_a_broken_read():
    result = run(f"""(()=>{{
      const out=[];
      setBadge('tasks',3,false,'运行详情：3 个任务上次运行失败');out.push({badge()});
      API_READS.set('/api/tasks',{{sequence:1,pending:false,error:null}});noteRead('/api/tasks',{{pending:false}});out.push({badge()});
      setBadge('tasks',0,false);out.push({badge()});
      API_READS.set('/api/tasks',{{sequence:2,pending:true}});noteRead('/api/tasks',{{pending:true}});setBadge('tasks',2,false);out.push({badge()});
      API_READS.set('/api/tasks',{{sequence:3,pending:false,error:'synthetic failure'}});noteRead('/api/tasks',{{pending:false}});out.push({badge()});
      return out;
    }})()""")
    pending, counted, zero, refreshing, broken = result
    # 第一次读取没回来:「…」,哪怕已经有人先报了一个数。
    assert pending["text"] == "…" and pending["cls"] == "nav-badge pending" and not pending["hidden"]
    assert counted == {"text": "3", "hidden": False, "cls": "nav-badge bad", "title": "运行详情：3 个任务上次运行失败"}
    assert zero["hidden"] is True
    # 刷新途中仍显示上一次读到的数,不闪回「…」。
    assert refreshing["text"] == "2" and not refreshing["hidden"]
    assert broken["text"] == "?" and broken["cls"] == "nav-badge broken" and "synthetic failure" in broken["title"]


def test_a_group_badge_sums_its_tabs_and_takes_the_worst_state():
    result = run(f"""(()=>{{
      const settle=path=>{{API_READS.set(path,{{sequence:1,pending:false,error:null}});noteRead(path,{{pending:false}});}};
      settle('/api/repos');setBadge('repos',4,true);
      const waiting={badge('bg-resources')};
      settle('/api/sys');settle('/api/mem');setBadge('storage',1,false);setBadge('resources',0,true);
      const summed={badge('bg-resources')};
      API_READS.set('/api/mem',{{sequence:2,pending:false,error:'synthetic mem failure'}});noteRead('/api/mem',{{pending:false}});
      return [waiting,summed,{badge('bg-resources')}];
    }})()""")
    waiting, summed, broken = result
    assert waiting["text"] == "…"
    # 琥珀加红:合计取红。
    assert summed["text"] == "5" and summed["cls"] == "nav-badge bad"
    assert broken["text"] == "?" and "synthetic mem failure" in broken["title"]


def test_diagnostics_badge_counts_the_review_rows_and_names_unreadable_sources_without_adding_them():
    result = run(f"""(()=>{{
      for(const path of BADGE_SOURCES.diagnostics){{API_READS.set(path,{{sequence:1,pending:false,error:null}});noteRead(path,{{pending:false}});}}
      renderPlatformSignals=()=>{{}};renderAutomations=()=>{{}};renderTiles=()=>{{}};
      DATA={{summary:{{total:2,bad:1}},history:{{ingest:{{state:'ok'}}}},groups:[{{cat:'Acme',rows:[{{name:'AcmeSync',sk:'bad',sl:'synthetic failure'}},{{name:'AcmeOther',sk:'ok'}}]}}]}};
      SCK={{ok:false,broken:['synthetic source']}};
      updateBadges();
      return [{badge('bg-diagnostics')},{badge('bg-automations')},REVIEW_TOTALS];
    }})()""")
    diagnostics, automations, totals = result
    assert diagnostics["text"] == "1" and totals == {"objects": 1, "bad": 1}
    assert diagnostics["cls"] == "nav-badge bad" and "1 个数据来源读不到" in diagnostics["title"]
    assert automations["text"] == "1"


def test_model_call_badge_shows_the_failure_count_not_a_label():
    result = run(f"""(()=>{{
      API_READS.set('/api/llmcall',{{sequence:1,pending:false,error:null}});noteRead('/api/llmcall',{{pending:false}});
      LLM={{windows:[{{}},{{failed:7}}],chain:{{shadowed_by_env:false}}}};llmBadge();const plain={badge('bg-work')};
      LLM={{windows:[{{}},{{failed:0}}],chain:{{shadowed_by_env:true}}}};llmBadge();
      return [plain,{badge('bg-work')}];
    }})()""")
    plain, shadowed = result
    assert plain["text"] == "7" and "7 次" in plain["title"]
    assert shadowed["text"] == "1" and "环境变量" in shadowed["title"]


def test_a_failed_read_turns_its_badge_into_a_question_mark_without_any_panel_redrawing():
    # loadRepos 读失败时直接返回、不重画任何东西;徽章靠 api() 自己报上来的那一笔变成「?」。
    result = run(f"""(async()=>{{
      fetch=async()=>{{throw new Error('synthetic offline');}};
      await api('/api/repos').catch(()=>{{}});
      return [{badge('bg-resources')},API_READS.get('/api/repos').error];
    }})()""")
    assert result[0]["text"] == "?" and "synthetic offline" in result[0]["title"]
    assert result[1] == "synthetic offline"


def test_badge_sources_are_read_once_after_the_page_and_never_twice():
    result = run("""(async()=>{
      const calls=[];
      load=()=>calls.push('tasks');loadRepos=()=>calls.push('repos');loadSys=()=>calls.push('sys');
      loadMem=()=>calls.push('mem');loadLLM=()=>calls.push('llm');
      API_READS.set('/api/tasks',{sequence:1,pending:false,error:null});
      API_READS.set('/api/mem',{sequence:2,pending:false,error:'synthetic failure'});
      await prefetchBadgeSources();
      return calls;
    })()""")
    assert result == ["repos", "sys", "llm"]


def test_title_names_the_tab_and_tabs_carry_their_own_badge():
    result = run("""(()=>{
      showView('tasks',false);const tasks=[$('view-title').textContent,$('view-tabs').innerHTML,$('view-tabs').hidden];
      showView('diagnostics',false);
      return {tasks,diagnostics:[$('view-title').textContent,$('view-tabs').hidden]};
    })()""", SHOW)
    title, tabs, hidden = result["tasks"]
    assert title == "自动化 · 运行详情" and hidden is False
    assert 'data-badge-for="tasks"' in tabs and 'data-badge-for="pipelines"' not in tabs
    assert result["diagnostics"] == ["诊断", True]


def test_sidebar_returns_to_the_last_tab_of_a_group_and_ignores_unknown_values():
    result = run("""(()=>{
      const stored={};localStorage.getItem=key=>stored[key] ?? null;localStorage.setItem=(key,value)=>{stored[key]=value;};
      const fresh=groupEntry('resources');
      showView('storage',false);
      const back=groupEntry('resources');
      stored['tc.view.automations']='repos';
      const foreign=groupEntry('automations');
      localStorage.getItem=()=>{throw new Error('denied');};
      return [fresh,back,foreign,groupEntry('work')];
    })()""", SHOW)
    assert result == ["resources", "storage", "automations", "work"]


def test_a_drill_down_offers_one_way_back_and_escape_uses_it_once():
    result = run("""(()=>{
      const drill={closest:()=>({})}, plain={closest:()=>null};
      showView('diagnostics',false);
      markDrill(drill);showView('tasks',true);
      const chip=$('view-tabs').innerHTML;
      // 地址跳转回来的那一次 hashchange 是同一屏:来源还在。
      showView('tasks',false);
      const kept=originChip();
      const first=key('Escape');handleEscape(first);
      const afterFirst=[CURVIEW,first.prevented];
      const second=key('Escape');const handled=handleEscape(second);
      return {chip,kept,afterFirst,afterSecond:[CURVIEW,handled,second.prevented]};
    })()""", tasks_setup(SHOW))
    assert '← 返回 技术问题与数据来源' in result["chip"] and 'href="#diagnostics"' in result["chip"]
    assert '← 返回 技术问题与数据来源' in result["kept"]
    assert result["afterFirst"] == ["diagnostics", True]
    # 来源用掉了:再按 Esc 不会接着往回走。
    assert result["afterSecond"] == ["diagnostics", False, False]


def test_views_reached_from_the_sidebar_or_a_tab_have_no_way_back():
    result = run("""(()=>{
      const drill={closest:()=>({})};
      showView('diagnostics',false);markDrill(drill);showView('tasks',true);
      showView('tasks',true,{reset:true});const sidebar=[originChip(),handleEscape(key('Escape')),CURVIEW];
      showView('diagnostics',false);markDrill(drill);showView('tasks',true);
      showView('pipelines',true);const tab=[originChip(),handleEscape(key('Escape')),CURVIEW];
      markDrill({closest:()=>null});showView('tasks',true);
      return {sidebar,tab,plain:[originChip(),CURVIEW]};
    })()""", tasks_setup(SHOW))
    assert result["sidebar"] == ["", False, "tasks"]
    assert result["tab"] == ["", False, "pipelines"]
    assert result["plain"] == ["", "tasks"]


def test_refresh_spins_while_reading_then_says_how_long_ago():
    result = run("""(async()=>{
      const classes=new Set();
      $('page-refresh').classList={add:name=>classes.add(name),remove:name=>classes.delete(name)};
      CURVIEW='repos';toast=()=>{};let finish;
      PAGE_READS.repos=[()=>new Promise(resolve=>{finish=resolve;})];
      const pending=refreshPage();
      const during=[classes.has('spinning'),$('page-refresh').disabled,$('page-refresh-state').textContent,$('page-refresh').title];
      finish();await pending;
      const after=[classes.has('spinning'),$('page-refresh').disabled,$('page-refresh-state').textContent];
      PAGE_READ_AT.set('repos',Date.now()-5*60000);renderRefreshAge();
      return {during,after,later:$('page-refresh-state').textContent};
    })()""")
    spinning, disabled, text, title = result["during"]
    assert spinning is True and disabled is True and text == "读取中" and "不可用" in title
    assert result["after"] == [False, False, "刚刚刷新"]
    assert result["later"] == "5 分钟前刷新"


def test_theme_menu_marks_the_chosen_appearance():
    # theme.js 在 <head> 里跑,不在台架的模块表里;这里单独载入一次,看三个按钮的按下状态跟着偏好走。
    program = """
const vm=require('node:vm');
const choices=['system','light','dark'].map(value=>({dataset:{themeChoice:value},pressed:null,setAttribute(k,v){if(k==='aria-pressed') this.pressed=v;}}));
const control={querySelectorAll:selector=>selector==='[data-theme-choice]'?choices:[]};
const media={matches:false,addEventListener(){}};
const document={documentElement:{dataset:{},style:{}},getElementById:id=>id==='theme-select'?control:null};
const context=vm.createContext({document,window:{addEventListener(){}},localStorage:{getItem:()=>null,setItem(){}},matchMedia:()=>media});
""" + f"vm.runInContext({json.dumps((STATIC / 'theme.js').read_text(encoding='utf-8'))},context);\n" + """
const seen=[choices.map(c=>c.pressed)];
context.window.ConsoleTheme.set('dark');seen.push(choices.map(c=>c.pressed));
console.log(JSON.stringify({seen,value:control.value}));
"""
    result = node(program)
    assert result["seen"] == [["true", "false", "false"], ["false", "false", "true"]]
    assert result["value"] == "dark"


def test_chain_title_is_the_session_name_even_before_the_chain_arrives():
    result = run("""(()=>{
      CONVOS={groups:[{shown:[{id:'00000000-0000-4000-8000-000000000001',title:'AcmeSync planning'}]}]};
      CH=null;CH_ID='00000000-0000-4000-8000-000000000001';const listed=chTitleText();
      CH_ID='00000000-0000-4000-8000-000000000002';const unknown=chTitleText();
      CH={title:'Acme release notes'};const loaded=chTitleText();
      return [listed,unknown,loaded];
    })()""")
    assert result == ["AcmeSync planning", "会话 00000000", "Acme release notes"]


def test_the_shell_markup_has_one_refresh_two_header_controls_and_a_labelled_way_back():
    html = PAGE.read_text(encoding="utf-8")
    # 卡片上那一排和顶栏重复的刷新按钮都删了;会话库保留一个不带图标的「重新扫描」。
    for gone in ("rpreload", "cvreload", "lcreload", "mtreload", "memreload", "pipeline-refresh", 'id="refresh"'):
        assert gone not in html, gone
    assert html.count('href="#i-refresh"') == 1
    assert re.search(r'<button[^>]*id="cxload"[^>]*>重新扫描</button>', html)
    header = re.search(r'<header[^>]*id="bar".*?</header>', html, re.S).group(0)
    outside_menu = re.sub(r'<div class="pop-menu[^"]*" id="page-menu" popover.*?</div>\s*</div>', "", header, flags=re.S)
    assert re.findall(r'<button[^>]*id="([\w-]+)"', outside_menu) == ["page-refresh", "page-more"]
    assert 'popovertarget="page-menu"' in header and 'id="page-export"' in header and 'id="theme-select"' in header
    # 侧栏的五个图标都是 sprite 里各不相同的 svg,徽章有四个分组。
    icons = re.findall(r'<span class="nav-link-icon"><svg class="ic" aria-hidden="true"><use href="#([\w-]+)"/>', html)
    assert len(icons) == 5 and len(set(icons)) == 5
    assert sorted(re.findall(r'id="bg-([\w-]+)"', html)) == ["automations", "diagnostics", "resources", "work"]
    # 会话链的返回在标题左边,带字。
    chain = re.search(r'<div class="card" id="chbox".*?</nav>', html, re.S).group(0)
    assert re.search(r'<nav class="ch-back"[^>]*><button[^>]*id="chclose"[^>]*>.*?会话列表</button>', chain)
    assert 'id="chtitle"' in chain


def test_dead_heading_code_is_gone():
    source = "".join(path.read_text(encoding="utf-8") for path in STATIC.rglob("*.js"))
    styles = "".join(path.read_text(encoding="utf-8") for path in STATIC.glob("*.css"))
    assert "updateViewHeading" not in source and "VIEW_COPY" not in source
    assert "#s-src" not in styles
    # 模型调用的徽章不再自己找元素、把数字改写成「7日」。
    assert 'bg-llm' not in source and 'textContent="7日"' not in source


# 台架里的 fetch:按路径回合成的响应。status 不是 200 的那一路就是「读坏了」。
FAKE_FETCH = """
const REPLIES={};
fetch=async path=>{const key=Object.keys(REPLIES).find(prefix=>path.startsWith(prefix));
  const [status,body]=key?REPLIES[key]:[200,{}];
  return {ok:status<400,status,json:async()=>body};};
"""
UNSET_REPOS = "{available:false,reason:'没有设 TASK_CONSOLE_REPOS,仓库这一栏是「未检查」。'}"
UNSET_MEM = "{available:false,reason:'没有设 TASK_CONSOLE_MEMORY,记忆池诊断这一栏是「未检查」。'}"


def test_an_unconfigured_source_is_a_muted_dash_not_a_broken_question_mark():
    # 后端对没配的来源回 200 加 available:false。工作台的格子把它画成「未检查」,徽章必须说同一句话。
    result = run(f"""(async()=>{{
      REPLIES['/api/repos']=[200,{UNSET_REPOS}];REPLIES['/api/mem']=[200,{UNSET_MEM}];
      REPLIES['/api/sys']=[200,{{disk:{{usedPct:40}}}}];REPLIES['/api/tasks']=[200,{{}}];
      for(const path of ['/api/repos','/api/mem','/api/sys','/api/tasks']) await api(path);
      setBadge('storage',0,true);setBadge('diagnostics',2,true,'技术问题：2 个对象要处理');
      const unchecked=['repos','resources','storage','diagnostics'].map(key=>badgeState(key));
      const group=groupBadge('resources'), diagnostics=groupBadge('diagnostics');
      // 负对照:同一路真的读坏了(500),照旧是「?」。
      REPLIES['/api/repos']=[500,{{error:'synthetic scan failure'}}];await api('/api/repos').catch(()=>{{}});
      return {{unchecked,group,diagnostics,broken:badgeState('repos'),brokenGroup:groupBadge('resources'),
        text:[badgeText(unchecked[0]),badgeText(group)]}};
    }})()""", FAKE_FETCH)
    repos, resources, storage, diagnostics = result["unchecked"]
    assert repos["state"] == "unchecked" and "TASK_CONSOLE_REPOS" in repos["title"] and "读取失败" not in repos["title"]
    assert resources["state"] == "unchecked" and "TASK_CONSOLE_MEMORY" in resources["title"]
    assert storage["state"] == "count" and storage["n"] == 0
    # 诊断有一部分来源没在查:数照旧是查了的那些,悬停里说清哪几路没算进来。
    assert diagnostics["state"] == "count" and diagnostics["n"] == 2
    assert "未检查" in diagnostics["title"] and "TASK_CONSOLE_REPOS" in diagnostics["title"]
    # 资源组:两个标签没在查、存储是零,合计是零(藏起来),不是「?」。
    assert result["group"]["state"] == "count" and result["group"]["n"] == 0
    assert result["text"] == ["—", "0"]
    assert result["diagnostics"]["state"] == "count" and result["diagnostics"]["n"] == 2
    assert result["broken"]["state"] == "broken" and "synthetic scan failure" in result["broken"]["title"]
    assert result["brokenGroup"]["state"] == "broken"


def test_an_unconfigured_tab_badge_paints_as_a_dash_with_its_own_class():
    result = run(f"""(async()=>{{
      REPLIES['/api/repos']=[200,{UNSET_REPOS}];REPLIES['/api/mem']=[200,{UNSET_MEM}];REPLIES['/api/sys']=[200,{{}}];
      for(const path of ['/api/repos','/api/mem','/api/sys']) await api(path);
      const el={{dataset:{{}},setAttribute(){{}}}};paintBadge(el,badgeState('repos'));
      return {{text:el.textContent,cls:el.className,hidden:el.hidden}};
    }})()""", FAKE_FETCH)
    assert result == {"text": "—", "cls": "nav-badge unchecked", "hidden": False}
    styles = (STATIC / "styles.css").read_text(encoding="utf-8")
    assert re.search(r"\.nav-badge\.unchecked\{[^}]*dashed", styles)


def test_the_memory_index_counts_on_its_own_tab_not_on_storage():
    # 记忆索引在「客户端技能与记忆」那一屏;存储清理的徽章只数磁盘,读不到记忆索引也不关它的事。
    result = run("""(()=>{
      for(const path of ['/api/tasks','/api/repos','/api/sys','/api/mem']){API_READS.set(path,{sequence:1,pending:false,error:null});noteRead(path,{pending:false});}
      renderPlatformSignals=()=>{};renderAutomations=()=>{};renderTiles=()=>{};renderTodo=()=>0;
      SYS={disk:{usedPct:40,verdict:{attention:false}}};
      MEM={available:true,linePct:97,verdict:{attention:true,state:'warn'}};
      updateBadges();
      const counted=[badgeState('storage'),badgeState('resources')];
      API_READS.set('/api/mem',{sequence:2,pending:false,error:'synthetic mem failure'});noteRead('/api/mem',{pending:false});
      return {counted,failed:[badgeState('storage'),badgeState('resources')],sources:BADGE_SOURCES};
    })()""")
    storage, resources = result["counted"]
    assert storage["state"] == "count" and storage["n"] == 0
    assert resources["state"] == "count" and resources["n"] == 1 and "记忆索引" in resources["title"]
    assert result["failed"][0]["state"] == "count"
    assert result["failed"][1]["state"] == "broken" and "synthetic mem failure" in result["failed"][1]["title"]
    assert result["sources"]["storage"] == ["/api/sys"] and result["sources"]["resources"] == ["/api/mem"]
    # 标签栏上「客户端技能与记忆」带一枚徽章,「存储清理」那枚只是磁盘的。
    tabs = run("(()=>{showView('resources',false);return $('view-tabs').innerHTML;})()", SHOW)
    assert 'data-badge-for="resources"' in tabs and 'data-badge-for="storage"' in tabs


def test_refresh_is_per_page_and_another_page_can_refresh_while_one_is_reading():
    result = run("""(async()=>{
      const classes=new Set(), toasts=[];
      $('page-refresh').classList={add:name=>classes.add(name),remove:name=>classes.delete(name)};
      $('page-refresh').title='刷新这一页';
      toast=(text,tone)=>toasts.push([text,tone]);
      let finishRepos;
      REPLIES['/api/llmcall']=[200,{windows:[]}];
      PAGE_READS.repos=[()=>api('/api/repos').catch(()=>{})];
      PAGE_READS.llm=[()=>api('/api/llmcall')];
      fetch=async path=>path.startsWith('/api/repos')
        ? new Promise(resolve=>{finishRepos=()=>resolve({ok:false,status:500,json:async()=>({error:'synthetic scan failure'})});})
        : {ok:true,status:200,json:async()=>({windows:[]})};
      CURVIEW='repos';const repos=refreshPage();
      const onRepos=[$('page-refresh').disabled,classes.has('spinning')];
      // 换到模型调用:那一屏没在读,按钮亮着、不转。
      CURVIEW='llm';renderRefreshAge();
      const onLlm=[$('page-refresh').disabled,classes.has('spinning'),$('page-refresh').title];
      await refreshPage();
      const llmDone=[$('page-refresh-state').textContent,$('page-refresh-state').dataset.tone];
      // 回到代码仓库:它还在读,按钮又灰着转着。
      CURVIEW='repos';renderRefreshAge();
      const backOnRepos=[$('page-refresh').disabled,classes.has('spinning'),$('page-refresh-state').textContent];
      CURVIEW='llm';renderRefreshAge();
      finishRepos();await repos;
      // 代码仓库读坏了,但那不是模型调用的失败:模型调用那一屏照旧写刷新时间。
      const afterRepos=[$('page-refresh-state').textContent,$('page-refresh').disabled];
      CURVIEW='repos';renderRefreshAge();
      return {onRepos,onLlm,llmDone,backOnRepos,afterRepos,reposDone:[$('page-refresh-state').textContent,$('page-refresh-state').dataset.tone],
        toasts,busy:PAGE_REFRESHING_VIEWS.size};
    })()""", FAKE_FETCH)
    assert result["onRepos"] == [True, True]
    assert result["onLlm"] == [False, False, "刷新这一页"]
    assert result["llmDone"] == ["刚刚刷新", ""]
    assert result["backOnRepos"] == [True, True, "读取中"]
    assert result["afterRepos"] == ["刚刚刷新", False]
    assert result["reposDone"] == ["1 项读取失败", "bad"]
    # 读完时人在别的屏:提示里带上是哪一屏。
    assert result["toasts"] == [["「代码仓库」1 项读取失败", "bad"]]
    assert result["busy"] == 0


def test_a_page_whose_first_read_failed_does_not_say_it_was_just_refreshed():
    result = run("""(async()=>{
      REPLIES['/api/tasks']=[500,{error:'RuntimeError: simulated failure'}];
      REPLIES['/api/repos']=[200,""" + UNSET_REPOS + """];
      PAGE_READS.tasks=[()=>api('/api/tasks').catch(()=>{})];
      PAGE_READS.repos=[()=>api('/api/repos')];
      CURVIEW='tasks';await loadPageOnce('tasks');
      const tasks=[$('page-refresh-state').textContent,$('page-refresh-state').dataset.tone];
      CURVIEW='repos';await loadPageOnce('repos');
      const repos=[$('page-refresh-state').textContent,$('page-refresh-state').dataset.tone];
      // 负对照:工作记录回 available:false 是它的读取器坏了,不是没在查。
      REPLIES['/api/work']=[200,{available:false,reason:'work_reader_failed'}];
      PAGE_READS.work=[()=>api('/api/work')];
      CURVIEW='work';await loadPageOnce('work');
      return {tasks,repos,work:[$('page-refresh-state').textContent,$('page-refresh-state').dataset.tone]};
    })()""", FAKE_FETCH)
    assert result["tasks"] == ["1 项读取失败", "bad"]
    # 没配仓库根目录不是读坏了:照常写刷新时间,带一句未检查,不标红。
    assert result["repos"] == ["刚刚刷新，1 项未检查", ""]
    assert result["work"] == ["1 项读取失败", "bad"]


def test_the_phone_nav_stays_one_row_whatever_the_badges_say():
    # 窄屏横排的五项不换行:换行的话,徽章数字回来那一刻「诊断」掉到第二行,整页往下跳。
    css = (STATIC / "workbench.css").read_text(encoding="utf-8")
    block = re.search(r"@media\(max-width:767px\)\{\s*/\* 窄屏时刷新.*?\n\}", css, re.S).group(0)
    nav = re.search(r"#side \.navbar-nav\{([^}]*)\}", block).group(1)
    assert "flex-wrap:nowrap" in nav and "overflow-x:auto" in nav
    assert "flex-wrap:wrap" not in block
