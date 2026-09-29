"""Interaction contracts for the operational shell, using generated data only."""
import json
import re
from pathlib import Path

from test_panel_parity import node, module_source
from tools.make_fixtures import operations_case, catalog_snapshot, review_pipeline_case
from tools.make_fixtures import launch_case
import component_status


def run(expression, setup=""):
    names = json.loads(re.search(r"const CONSOLE_MODULES = (\[.*?\]);", module_source("app.js"), re.S).group(1))
    program = """
const vm=require('node:vm'), elements={};
const document={querySelector:()=>({content:'synthetic'}),createElement:()=>({}),
getElementById:id=>elements[id] ||= {innerHTML:'',textContent:'',value:'',dataset:{},addEventListener:()=>{},appendChild:()=>{},
querySelectorAll:()=>[],getBoundingClientRect:()=>({top:0,bottom:100}),clientHeight:100,clientTop:0}};
const window={scrollY:0,scrollTo(){}};
const context=vm.createContext({document,window,crypto:require('node:crypto').webcrypto,AbortController,
localStorage:{getItem:()=>null},setTimeout,clearTimeout,requestAnimationFrame:fn=>setTimeout(fn,0)});
"""
    for name in names[:-1]:
        program += f"vm.runInContext({json.dumps(module_source(name))},context);\n"
    program += f"vm.runInContext({json.dumps(setup)},context);\n"
    program += "Promise.resolve(vm.runInContext(" + json.dumps(expression) + ",context)).then(result=>console.log(JSON.stringify(result)));"
    return node(program)


def test_navigation_reads_only_the_opened_page_once():
    result=run("(async()=>{await loadPageOnce('resources');await loadPageOnce('resources');return reads;})()",
               "let reads=[];PAGE_READS.resources=[async()=>reads.push('resources')];PAGE_READS.convos=[async()=>reads.push('convos')];")
    assert result == ['resources']


def test_theme_persists_follows_system_and_survives_denied_storage():
    program = """
const vm=require('node:vm'), listeners={}, stored={'tc.theme':'dark'}, changes=[];
const media={matches:false,addEventListener:(event,fn)=>listeners.media=fn};
const root={dataset:{},style:{}}, control={};
const document={documentElement:root,getElementById:()=>control};
const window={addEventListener:(event,fn)=>listeners[event]=fn};
const localStorage={getItem:key=>stored[key],setItem:(key,value)=>stored[key]=value};
const context=vm.createContext({document,window,localStorage,matchMedia:()=>media});
""" + f"vm.runInContext({json.dumps(module_source('theme.js'))},context);\n" + """
changes.push(root.dataset.theme);
window.ConsoleTheme.set('light');media.matches=true;listeners.media();changes.push(root.dataset.theme);
window.ConsoleTheme.set('system');changes.push(root.dataset.theme);
media.matches=false;listeners.media();changes.push(root.dataset.theme);
listeners.storage({key:'tc.theme',newValue:'dark'});changes.push(root.dataset.theme);
localStorage.setItem=()=>{throw new Error('denied')};window.ConsoleTheme.set('light');changes.push(root.dataset.theme);
console.log(JSON.stringify({changes,stored:stored['tc.theme'],bs:root.dataset.bsTheme,control:control.value}));
"""
    result = node(program)
    assert result["changes"] == ["dark", "light", "dark", "light", "dark", "light"]
    assert result["stored"] == "system"
    assert result["bs"] == result["control"] == "light"


def test_runtime_filters_preserve_inactive_rows_and_sort_by_cost():
    case = operations_case()
    setup = "const sample=" + json.dumps(case["skills"]) + ";"
    assert run("RUNTIME_STATE='inactive';runtimeRows(sample,'skill').map(row=>row.name)", setup) == ["Sample"]
    assert run("RUNTIME_QUERY='acme';runtimeRows(sample,'skill').map(row=>row.name)", setup) == ["Acme"]
    assert run("RUNTIME_SORT='budget';runtimeRows(sample,'skill').map(row=>row.chars)", setup) == [30, 10]


def test_launch_details_preserve_every_action_and_quote_scheduler_name():
    result = run("[taskLaunchHtml(sample),taskStartCommand(sample)]", "const sample=" + json.dumps(launch_case()) + ";")
    assert "C:\\Acme Tools\\python.exe" in result[0]
    assert "--check" in result[0] and "verify.exe" in result[0] and "--full" in result[0]
    assert "AcmeService" in result[0] and "补跑" in result[0] and "电池" in result[0]
    assert result[1] == "Start-ScheduledTask -TaskPath '\\Acme\\' -TaskName 'Acme''s sync'"


def test_operation_result_does_not_turn_accepted_or_partial_into_completed():
    result = run("[operationOutcome('/api/act',{verb:'run'},{ok:true,status:'run_requested'}),operationOutcome('/api/codex/delete',{},sample),operationOutcome('/api/act',{},null,new Error('network lost'))]", "const sample=" + json.dumps({"ok": False, "deleted": 1, "error": "synthetic error"}) + ";")
    assert "尚未确认" in result[0]["message"]
    assert result[0]["tone"] == "warn"
    assert "1" in result[1]["message"] and "synthetic error" in result[1]["message"]
    assert result[1]["tone"] == "bad"
    assert "未确认" in result[2]["message"]


def test_operation_timer_stops_and_history_does_not_retain_request_secrets():
    result = run("""(()=>{
      for(let i=0;i<20;i++){
        const operation=ConsoleActions.begin('/api/maintenance/delete',{body:JSON.stringify({token:'synthetic-secret'})});
        ConsoleActions.finish(operation,{ok:true,deleted:1});
      }
      return {timer:ConsoleActions.timer,count:ConsoleActions.operations.length,serialized:JSON.stringify(ConsoleActions.operations)};
    })()""")
    assert result["timer"] is None and result["count"] == 8
    assert "synthetic-secret" not in result["serialized"]


def test_completion_operation_is_success_while_queued_work_remains_pending():
    result = run("""(()=>{
      const operation=ConsoleActions.begin('/api/work/action',{body:JSON.stringify({action_id:'complete'})});
      ConsoleActions.finish(operation,{ok:true,status:'done',action:{kind:'complete',state:'done'}});
      return {operation,queued:operationOutcome('/api/work/action',{action_id:'agent'},{ok:true,status:'queued'})};
    })()""")
    assert result['operation']['tone'] == 'ok'
    assert '标记完成' in result['operation']['label']
    assert '已标记完成' in result['operation']['message']
    assert result['queued']['tone'] == 'warn'


def test_catalog_filters_do_not_confuse_unknown_auth_with_healthy():
    catalog = component_status.catalog_view(catalog_snapshot())
    setup = "COMPONENTS=" + json.dumps({"catalog": catalog}) + ";"
    assert run("CATALOG_STATE='authenticated:unknown';COMPONENTS.catalog.records.filter(catalogMatches).map(row=>row.kind)", setup) == ["mcp_binding"]
    assert run("CATALOG_STATE='authenticated:no';COMPONENTS.catalog.records.filter(catalogMatches).length", setup) == 0
    assert run("CATALOG_CLIENT='shared';COMPONENTS.catalog.records.filter(catalogMatches).length", setup) == 1


def test_conversation_search_is_scoped_and_groups_start_collapsed():
    setup = "CONVOS=" + json.dumps(operations_case()["conversations"]) + ";"
    result = run("renderConvos();[$('cvgroups').innerHTML,$('cv-match').textContent]", setup)
    assert result[0].count('class="cv-g open"') == 0
    assert "已加载 2 / 6" in result[1]
    setup += "api=async()=>({...CONVOS,groups:[CONVOS.groups[0]],summary:{...CONVOS.summary,matched:1}});"
    result = run("CV_QUERY='acme';loadConvos().then(()=>[$('cvgroups').innerHTML,$('cv-match').textContent])", setup)
    assert "Acme project planning" in result[0] and "Sample project planning" not in result[0]
    assert result[0].count('class="cv-g open"') == 1
    assert "已加载 1 / 1" in result[1]
    assert "加载更多" in result[0]
    assert 'class="cv-g open"' not in run("CV_QUERY='acme';renderConvos();CV_QUERY='';renderConvos();$('cvgroups').innerHTML", setup)


def test_client_filter_includes_explicit_entrypoint_clients():
    source = catalog_snapshot()["records"][1]
    source["origin"] = {"kind": "synthetic"}
    setup = "const sample=" + json.dumps(source) + ";"
    assert run("CATALOG_CLIENT='codex';catalogMatches(sample)", setup)
    assert not run("CATALOG_CLIENT='shared';catalogMatches(sample)", setup)


def test_pipeline_shows_both_and_all_stage_evidence_without_disclosure():
    result = run("renderPipelines();$('pipeline-body').innerHTML", "COMPONENTS=" + json.dumps(review_pipeline_case()) + ";")
    assert result.count('class="card pipeline-run"') == 2
    assert "无法分别确认复制和推送结果" in result
    assert "尚未验证 Codex 能否在会话中调用这些记忆" in result
    assert "<details" not in result


def test_repository_issue_filters_use_reported_fields():
    setup = "const sample=" + json.dumps(operations_case()["repository"]) + ";"
    assert run("RP_ISSUE='identity_mismatch';rpMatches(sample,'','','','')", setup)
    assert not run("RP_ISSUE='identity_unchecked';rpMatches(sample,'','','','')", setup)
    assert run("RP_ISSUE='upstream';rpMatches(sample,'','','','')", setup)


def test_call_export_states_page_scope_and_excludes_token():
    result = run("pageSnapshot('llm')", "LMROWS=[];LMTOTAL=100;LMQ.offset=20;")
    assert result["scope"] == "loaded_snapshot"
    assert result["limitation"] == "calls_current_page"
    assert result["data"]["query"]["offset"] == 20
    assert result["data"]["total"] == 100
    assert "token" not in json.dumps(result).lower()


def test_refresh_failure_is_visible_and_button_recovers():
    result = run("refreshPage().then(()=>({text:$('page-refresh-state').textContent,disabled:$('page-refresh').disabled,busy:PAGE_REFRESHING}))", """
CURVIEW='tasks';toast=()=>{};
PAGE_READS.tasks=[async()=>{API_READS.set('/api/tasks',{sequence:++API_SEQUENCE,error:'synthetic failure'});}];
""")
    assert "失败" in result["text"]
    assert result["disabled"] is False and result["busy"] is False


def test_cleanup_scan_deduplicates_and_discards_stale_library_response():
    result = run("""(async()=>{
  $('cxwhich').value='sessions';const first=loadCxList(), duplicate=loadCxList();
  $('cxwhich').value='archived_sessions';const second=loadCxList();
  resolveArchive({items:[],available:true,exists:true,count:0,bytes:0});await second;
  resolveLive({items:[],available:true,exists:true,count:5,bytes:0});await Promise.all([first,duplicate]);
  return {count:CXL.count,requests};
})()""", """
let requests=0,resolveArchive,resolveLive;
api=path=>{requests++;return new Promise(resolve=>{if(path.includes('archived_sessions')) resolveArchive=resolve;else resolveLive=resolve;});};
renderCxList=()=>{};
""")
    assert result == {"count": 0, "requests": 2}


def test_functional_sections_have_no_closed_disclosure_by_default():
    html = (Path(__file__).resolve().parents[1] / "scripts/task_console/console.html").read_text(encoding="utf-8")
    # Instructions and optional export/fork controls may fold; primary content stays visible.
    help_blocks = re.findall(r'<details class="cv-scan-details">(.*?)</details>', html, re.S)
    assert len(help_blocks) == 1
    assert not re.search(r'<(?:button|input|select)\b', help_blocks[0])
    html = re.sub(r'<details class="cv-scan-details">.*?</details>', '', html, flags=re.S)
    assert '<details class="ch-actions-menu"><summary>导出与新建会话</summary><div class="ch-act" id="chact"></div></details>' in html
    html = re.sub(r'<details class="ch-actions-menu">.*?</details>', '', html, flags=re.S)
    assert not re.findall(r'<details(?![^>]*\bopen\b)[^>]*>', html)
    assert 'id="theme-select"' in html and 'id="page-export"' in html
