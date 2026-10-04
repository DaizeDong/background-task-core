"""页面构图的准备工作:每页一份样式表、各页的挂点搬回各自的面板文件、几处版面重排。

数据全部是合成的(AcmeSync 之类)。行为用 node:vm 台架(test_operations_ui.run)直接调面板里的函数,
版面顺序直接读 console.html;真实浏览器里的走查另见交付说明。
"""
import json
import re
from pathlib import Path

from test_automation_ui import setup as automation_setup
from test_operations_ui import run

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "scripts" / "task_console" / "static"
PAGE = STATIC.parent / "console.html"


def section(view):
    html = PAGE.read_text(encoding="utf-8")
    start = html.index(f'<section id="{view}"')
    # 注释不算页面上的字:验收看的是渲染出来的内容。
    return re.sub(r"<!--.*?-->", "", html[start:html.index("</section>", start)], flags=re.S)


def order(text, needles):
    positions = [text.index(needle) for needle in needles]
    return positions == sorted(positions)


# ---------- 每页一份样式表 ----------

def test_each_page_area_has_its_own_linked_stylesheet():
    html = PAGE.read_text(encoding="utf-8")
    links = re.findall(r'href="/static/([^"]+\.css)"', html)
    pages = ["page-automation.css", "page-diagnostics.css", "page-work.css", "page-llm.css", "page-resources.css"]
    # 放在共用表之后:页面表要能用同样的选择器覆盖共用规则。
    assert links[-5:] == pages
    assert links.index("conversations.css") < links.index("page-automation.css")
    for name in pages:
        assert (STATIC / name).is_file()


# ---------- 挂点搬回各自的面板 ----------

def test_page_bound_listeners_live_in_their_panels_not_in_events():
    events = (STATIC / "events.js").read_text(encoding="utf-8")
    for gone in ('$("rpq")', '$("cxwhich")', '$("scktog")', '$("lmq")', '$("tl")', "$('runtime-search')",
                 '$("lclist")', '$("cxload")', "$('review-search')", '$("hygtog")'):
        assert gone not in events, gone
    owners = {"panels/tasks.js": ["startTasksPage", "startTimeline"], "panels/repositories.js": ["startRepositories"],
              "panels/profile.js": ["startStorage"], "panels/review.js": ["startDiagnostics"],
              "panels/calls.js": ["startCalls"], "panels/skills.js": ["startResources"]}
    for path, names in owners.items():
        source = (STATIC / path).read_text(encoding="utf-8")
        for name in names:
            assert f"function {name}(" in source, (path, name)
            assert f"{name}();" in events, name
    # 自检、灯板和「数据」那一行只画在诊断屏上,跟着诊断的挂点一起住在 review.js。
    review = (STATIC / "panels/review.js").read_text(encoding="utf-8")
    for name in ("function loadSelfcheck(", "function renderFresh(", "function renderDataLine("):
        assert name in review


def test_storage_list_clicks_are_handled_on_the_list_itself():
    # 清单的点击从 document 搬到 #cxbody 上:点一行只改这一行的勾选,不重画整张表。
    # 行是包着勾选框的 label,勾选在浏览器转给勾选框的那一下点击里做,按它此刻的勾选状态。
    result = run("""(()=>{
      const handlers={};$('cxbody').addEventListener=(type,fn)=>handlers[type]=fn;
      CXL={available:true,exists:true,items:[{rel:'acme/a.jsonl',bytes:10}]};
      const row={dataset:{cxrel:'acme/a.jsonl'}};
      const box={tagName:'INPUT',checked:true,closest:sel=>sel==='.cx-l'?row:null};
      startStorage();
      handlers.click({target:box});const picked=CXSEL.has('acme/a.jsonl');
      box.checked=false;handlers.click({target:box});
      return [picked,CXSEL.has('acme/a.jsonl')];
    })()""", "cxSelSummary=()=>{};")
    assert result == [True, False]


# ---------- 工作台 ----------

def test_overview_puts_attention_first_then_todo_and_active_then_results():
    overview = section("overview")
    assert order(overview, ['id="attention-strip"', 'id="interrupted-work"', 'id="tracked-work"', 'id="active-work"', 'id="recent-results"'])
    assert "审批未接入" not in re.sub(r"<!--.*?-->", "", PAGE.read_text(encoding="utf-8"), flags=re.S)
    assert "审批未接入" not in (STATIC / "workbench.js").read_text(encoding="utf-8")
    assert "Agent 未完成 / 出错" in overview


def test_the_decision_strip_shows_only_when_decisions_are_connected():
    hidden = run("renderWorkPlatform();[$('decision-strip').hidden,$('decision-state').innerHTML]",
                 "WORK={available:true,items:[],events:[],sources:[],coverage:{}};")
    assert hidden == [True, ""]
    shown = run("renderWorkPlatform();[$('decision-strip').hidden,$('decision-state').innerHTML]",
                "WORK={available:true,items:[],events:[],sources:[],coverage:{}};"
                "const project=workProjection;workProjection=feed=>({...project(feed),decisions:[]});")
    assert shown[0] is False and "没有要你确认的事" in shown[1]


def test_attention_summary_says_pending_until_tasks_arrive_then_counts_failed_tasks():
    pending = run("API_READS.set('/api/tasks',{sequence:1,pending:true});attentionSummary().tasks", "DATA=null;")
    assert pending == {"state": "pending", "count": 0}
    rows = [{"name": "AcmeSync", "sk": "bad", "sl": "失败", "issues": []},
            {"name": "AcmeOther", "sk": "bad", "sl": "失败", "issues": []},
            {"name": "AcmeFine", "sk": "ok", "sl": "正常", "issues": []}]
    data = {"groups": [{"cat": "Synthetic", "rows": rows}], "summary": {}, "history": {"ingest": {"state": "ok"}}}
    summary = run("API_READS.set('/api/tasks',{sequence:2,pending:false,error:null});attentionSummary()",
                  "DATA=" + json.dumps(data) + ";")
    assert summary["tasks"] == {"state": "ok", "count": 2}
    # 没有产物清单:读到了,但这一类没在查,不能当成零。
    assert summary["outputs"]["state"] == "ok" and summary["outputs"]["unchecked"]
    assert summary["repos"]["state"] == "pending"
    failed = run("API_READS.set('/api/sys',{sequence:3,pending:false,error:'synthetic failure'});attentionSummary().disk")
    assert failed == {"state": "failed", "count": 0, "reason": "synthetic failure"}
    disk = run("API_READS.set('/api/sys',{sequence:4,pending:false,error:null});attentionSummary().disk",
               "SYS={disk:{usedPct:96,free:1,verdict:{attention:true,tone:'bad'}}};")
    assert disk == {"state": "ok", "count": 1, "usedPct": 96}
    # 读回来了但没在查(没配仓库根目录、没量到磁盘):是 ok 加原因,不能一直挂着「读取中」,也不能当零。
    unchecked = run("API_READS.set('/api/repos',{sequence:5,pending:false,error:null});"
                    "API_READS.set('/api/sys',{sequence:6,pending:false,error:null});"
                    "const s=attentionSummary();[s.repos,s.disk]",
                    "REPOS={available:false,reason:'synthetic root missing'};SYS={disk:{reason:'synthetic no disk'}};")
    assert unchecked == [{"state": "ok", "count": 0, "unchecked": "synthetic root missing"},
                         {"state": "ok", "count": 0, "unchecked": "synthetic no disk"}]


def test_the_diagnostics_list_and_the_summary_read_the_same_rows():
    rows = [{"name": "AcmeSync", "sk": "bad", "sl": "失败", "issues": []}]
    data = {"groups": [{"cat": "Synthetic", "rows": rows}], "summary": {}, "history": {"ingest": {"state": "ok"}}}
    result = run("[renderTodo(),attentionRows().map(row=>row.key)]", "DATA=" + json.dumps(data) + ";")
    assert result == [1, ["tasks"]]


# ---------- 模型调用 ----------

def test_llm_reads_usage_failures_and_calls_before_the_rarely_used_settings():
    assert order(section("llm"), ['id="luse"', 'id="lruns"', 'id="lcalls"', 'id="lrungs"', 'id="lchain"'])


# ---------- 客户端技能与记忆 ----------

def test_resources_has_one_search_and_text_anchors_in_use_order():
    resources = section("resources")
    assert resources.count('type="search"') == 1 and 'id="resources-search"' in resources
    assert order(resources, ['id="mtbox"', 'id="membox"', 'id="catalog-box"'])
    anchors = re.findall(r'<button type="button" data-goto="(#[\w-]+)">([^<]+)</button>', resources)
    assert anchors == [("#mt-skills", "技能"), ("#mt-plugins", "插件"), ("#membox", "记忆"), ("#catalog-box", "目录")]
    # 目录自己不再画搜索框。
    assert "catalog-search" not in (STATIC / "panels/skills.js").read_text(encoding="utf-8")


def test_one_search_narrows_skills_and_the_catalog_and_the_page_reset_clears_both():
    result = run("""(()=>{
      const handlers={};$('resources-search').addEventListener=(type,fn)=>handlers[type]=fn;
      startResources();
      handlers.input({target:{value:'acme'}});
      const skills=runtimeRows(skills0,'skill').map(row=>row.name);
      const catalog=records.filter(catalogMatches).map(row=>row.registry_key);
      const typed=[RUNTIME_QUERY,CATALOG_QUERY];
      $('runtime-state').value='active';RUNTIME_STATE='active';
      resetFilters('runtime');const afterRuntime=[RUNTIME_QUERY,CATALOG_QUERY,RUNTIME_STATE];
      CATALOG_KIND='skill';$('resources-search').value='acme';
      resetFilters('resources');
      return {skills,catalog,typed,afterRuntime,afterPage:[RUNTIME_QUERY,CATALOG_QUERY,CATALOG_KIND,$('resources-search').value]};
    })()""", """
      const skills0=[{name:'AcmeSkill'},{name:'OtherSkill'}];
      const records=[{source_id:'s:1',registry_key:'acme-plugin',kind:'plugin'},{source_id:'s:2',registry_key:'other',kind:'plugin'}];
      renderSkills=()=>{};renderClientPlugins=()=>{};renderCatalog=()=>{};renderCatalogResults=()=>{};renderCatalogHealth=()=>{};
    """)
    assert result["typed"] == ["acme", "acme"]
    assert result["skills"] == ["AcmeSkill"] and result["catalog"] == ["acme-plugin"]
    # 一块自己的清除只清自己的下拉,不动共用的搜索词。
    assert result["afterRuntime"] == ["acme", "acme", ""]
    assert result["afterPage"] == ["", "", "", ""]


# ---------- 存储清理 ----------

def test_storage_puts_the_disk_first_and_names_codex_storage_once():
    storage = section("storage")
    assert order(storage, ['id="storage-alert"', 'id="mt-sys"', 'id="mt-codex"', 'id="cxbox"'])
    assert storage.count("Codex 存储") == 1
    codex = {"available": True, "bytes": 2048, "incomplete": [], "unread": [],
             "items": {"sessions": {"available": True, "exists": True, "bytes": 2048, "count": 3}}}
    result = run("renderCodex();[$('mt-codex').innerHTML,$('codex-total').textContent]", "CODEX=" + json.dumps(codex) + ";")
    assert "Codex 存储" not in result[0] and "扫描完成" not in result[0]
    assert result[1] == "2K"
    # 没扫完的总量带「+」,读取中不写数。
    partial = dict(codex, incomplete=["log"])
    assert run("renderCodex();$('codex-total').textContent", "CODEX=" + json.dumps(partial) + ";") == "2K+"
    assert run("renderCodex();[$('codex-total').textContent,$('mt-codex').innerHTML]", "CODEX=null;") == ["", '<div class="mt-note">读取中</div>']


def test_repository_filters_have_a_slot_for_the_state_chip():
    repos = section("repos")
    filters = repos[repos.index('class="rp-filters"'):repos.index('class="rp-split"')]
    assert 'id="rpstate-chip"' in filters


# ---------- 运行详情 ----------

def status_setup():
    return automation_setup() + """
renderFresh=()=>{};updateBadges=()=>{};renderTL=()=>{};renderHeat=()=>{};renderScores=()=>{};renderDataLine=()=>{};
ROWS.push({...row,name:'AcmeOther',sk:'bad',issues:[['bad','synthetic failure']]});
ROWS.push({...row,name:'AcmeWarn',issues:[['warn','synthetic warning']]});
ROWS.push({...row,name:'AcmeOff',sk:'disabled',state:'Disabled',issues:[]});
"""


def test_status_chips_filter_the_table_and_a_second_click_shows_everything():
    result = run("""(()=>{
      render();const all=VIEW.length;
      setTaskStatus('bad');const bad=VIEW.map(r=>r.name);
      setTaskStatus('bad');const again=[TASK_STATUS,VIEW.length];
      setTaskStatus('warn');const warn=VIEW.map(r=>r.name).sort();
      $('hideoff').checked=true;setTaskStatus('off');const off=[VIEW.map(r=>r.name),$('hideoff').checked];
      const counts=[$('s-bad').textContent,$('s-iss').textContent,$('s-off').textContent];
      const filters=pageSnapshot('tasks').data.filters;
      resetFilters('tasks');
      return {all,bad,again,warn,off,counts,filters,reset:TASK_STATUS};
    })()""", status_setup())
    assert result["all"] == 4
    assert result["bad"] == ["AcmeOther"]
    assert result["again"] == ["", 4]
    assert result["warn"] == ["AcmeOther", "AcmeWarn"]
    # 选「只看停用」时「隐藏停用」被取消,否则表是空的。
    assert result["off"] == [["AcmeOff"], False]
    # 按钮上的数就是点下去剩下的行数。
    assert result["counts"] == [1, 2, 1]
    assert result["filters"]["status"] == "off" and "onlyProblems" not in result["filters"]
    assert result["reset"] == ""


def test_status_chips_are_buttons_and_the_old_checkbox_and_hygiene_toggle_are_gone():
    tasks = section("tasks")
    assert re.findall(r'data-task-status="(\w*)" aria-pressed', tasks) == ["", "bad", "warn", "off"]
    assert 'id="only"' not in tasks and 'id="hygtog"' not in tasks


def test_status_buttons_press_the_active_filter_and_grey_out_empty_ones():
    result = run("""(()=>{
      const buttons=['','bad','warn','off'].map(key=>({dataset:{taskStatus:key},attrs:{},disabled:false,title:'',
        setAttribute(name,value){this.attrs[name]=value;}}));
      document.querySelectorAll=sel=>sel.includes('data-task-status')?buttons:[];
      TASK_STATUS='bad';syncTaskStatus({'':3,bad:1,warn:0,off:0});
      const pressed=buttons.map(b=>b.attrs['aria-pressed']), disabled=buttons.map(b=>b.disabled), title=buttons[2].title;
      syncTaskStatus(null);
      return {pressed,disabled,title,unread:buttons.map(b=>b.disabled)};
    })()""")
    assert result["pressed"] == ["false", "true", "false", "false"]
    assert result["disabled"] == [False, False, True, True]
    assert "没有带警告的任务" in result["title"]
    assert result["unread"] == [True, True, True, True]


def test_the_hygiene_columns_join_the_column_menu_and_the_old_toggle_migrates_once():
    menu = run("renderTaskColumns();$('task-column-options').innerHTML")
    hygiene = menu[menu.index('aria-label="保障配置"'):]
    for label in ("补跑", "重试", "超时限制", "产物", "备份", "监控"):
        assert label in hygiene
    assert "补跑" not in menu[:menu.index('aria-label="保障配置"')]
    migrated = run("""(()=>{
      const store={'tc.hyg':'1','tc.taskColumns':JSON.stringify(['sl'])};
      const storage={getItem:k=>k in store?store[k]:null,setItem:(k,v)=>store[k]=v,removeItem:k=>delete store[k]};
      const columns=[...loadTaskColumns(()=>storage)];
      return {columns,store};
    })()""")
    assert migrated["columns"] == ["sl", "catchup", "retries", "timeout", "artifact", "inAllow", "inHealth"]
    assert "tc.hyg" not in migrated["store"] and json.loads(migrated["store"]["tc.taskColumns"]) == migrated["columns"]
    closed = run("""(()=>{
      const store={'tc.hyg':'0'};
      const storage={getItem:k=>k in store?store[k]:null,setItem:(k,v)=>store[k]=v,removeItem:k=>delete store[k]};
      return {columns:[...loadTaskColumns(()=>storage)],store};
    })()""")
    assert closed == {"columns": ["sl", "ops", "triggers", "nextRun"], "store": {}}
    assert run("[...loadTaskColumns(()=>{throw new Error('denied')})]") == ["sl", "ops", "triggers", "nextRun"]
