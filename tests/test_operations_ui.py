"""Interaction contracts for the operational shell, using generated data only."""
import json
import re
from pathlib import Path

from test_panel_parity import node, module_source
from tools.make_fixtures import operations_case, catalog_snapshot, review_pipeline_case
import component_status


def run(expression, setup=""):
    names = json.loads(re.search(r"const CONSOLE_MODULES = (\[.*?\]);", module_source("app.js"), re.S).group(1))
    program = """
const vm=require('node:vm'), elements={};
const document={querySelector:()=>({content:'synthetic'}),createElement:()=>({}),
getElementById:id=>elements[id] ||= {innerHTML:'',textContent:'',value:'',dataset:{},addEventListener:()=>{},appendChild:()=>{}}};
const context=vm.createContext({document,localStorage:{getItem:()=>null},setTimeout,clearTimeout});
"""
    for name in names[:-1]:
        program += f"vm.runInContext({json.dumps(module_source(name))},context);\n"
    program += f"vm.runInContext({json.dumps(setup)},context);\n"
    program += "Promise.resolve(vm.runInContext(" + json.dumps(expression) + ",context)).then(result=>console.log(JSON.stringify(result)));"
    return node(program)


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


def test_catalog_filters_do_not_confuse_unknown_auth_with_healthy():
    catalog = component_status.catalog_view(catalog_snapshot())
    setup = "COMPONENTS=" + json.dumps({"catalog": catalog}) + ";"
    assert run("CATALOG_STATE='authenticated:unknown';COMPONENTS.catalog.records.filter(catalogMatches).map(row=>row.kind)", setup) == ["mcp_binding"]
    assert run("CATALOG_STATE='authenticated:no';COMPONENTS.catalog.records.filter(catalogMatches).length", setup) == 0
    assert run("CATALOG_CLIENT='shared';COMPONENTS.catalog.records.filter(catalogMatches).length", setup) == 1


def test_conversation_search_is_scoped_and_groups_start_open():
    setup = "CONVOS=" + json.dumps(operations_case()["conversations"]) + ";"
    result = run("renderConvos();[$('cvgroups').innerHTML,$('cv-match').textContent]", setup)
    assert result[0].count('class="cv-g open"') == 2
    assert "另有 4 条未载入" in result[1]
    result = run("CV_QUERY='acme';renderConvos();[$('cvgroups').innerHTML,$('cv-match').textContent]", setup)
    assert "Acme project planning" in result[0] and "Sample project planning" not in result[0]
    assert "匹配 1/2" in result[1]
    assert "未参与筛选" in result[0]


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
    assert not re.findall(r'<details(?![^>]*\bopen\b)[^>]*>', html)
    assert 'id="theme-select"' in html and 'id="page-export"' in html
