"""Exercise shared automation presentation with synthetic data, without a browser."""
import json
import re

from test_operations_ui import run
from tools.make_fixtures import automation_info_case, console_page_fixture


def setup():
    case = automation_info_case()
    page = console_page_fixture()
    page["groups"][0]["rows"][0]["info"] = case["info"]
    return "DATA=" + json.dumps(page) + ";ROWS=DATA.groups.flatMap(g=>g.rows.map(r=>({...r,cat:g.cat})));const row=ROWS[0];"


def test_labels_prefer_info_and_keep_legacy_fallback_and_clock_colons():
    assert run("taskText(row)", setup()) == {"title": "同步示例文件", "summary": "检查并同步合成文件。"}
    assert run("taskText({name:'Acme',desc:'每天 08:00：同步文件。保留日志'})") == {"title": "每天 08:00", "summary": "同步文件。保留日志"}
    assert run("taskText({name:'Acme',desc:'标题。说明',info:{summary:'新摘要'}})") == {"title": "标题", "summary": "新摘要"}
    assert run("taskText({name:'Acme'})") == {"title": "Acme", "summary": ""}


def test_search_and_verdict_filters_compose_without_inventing_verdicts():
    for query in ("同步示例", "合成文件", "重试", "acmesync"):
        assert run(f"automationRows(ROWS,{json.dumps(query)},'enabled','fix').length", setup()) == 1
    assert run("automationRows(ROWS,'','disabled','fix').length", setup()) == 0
    assert run("automationRows(ROWS,'','','urgent').length", setup()) == 0
    assert run("taskVerdict({info:{verdict:'future'}})") == ""
    assert "未评估" in run("taskVerdictBadge({state:'Disabled'})")
    # Broken metadata has its own bucket and its own words; it must not read as not assessed.
    assert run("taskVerdictBucket({info:{verdictUnrecognized:'future'}})") == "unrecognized"
    assert run("taskVerdictBucket({infoInvalid:true,info:{}})") == "unrecognized"
    assert run("taskVerdictBucket({info:{}})") == "unassessed"
    unrecognized = run("taskVerdictBadge({info:{verdictUnrecognized:'future'}})")
    assert "建议无法识别" in unrecognized and "未评估" not in unrecognized
    invalid = run("taskVerdictBadge({infoInvalid:true,info:{}})")
    assert "说明写法不对" in invalid and "未评估" not in invalid
    assert "建议未读取" in run("taskVerdictBadge({infoPending:true})")


def test_description_block_is_first_and_escapes_all_metadata():
    html = run("detail(row)", setup())
    for label in ("用途", "频率", "现状", "建议", "核对于 2026-10-01"):
        assert label in html
    assert html.index("用途") < html.index("运行计划") < html.index("命令")
    escaped = run("taskInfoHtml({info:{summary:'<script>',status:'<img>',asOf:'<date>',advice:'<b>'}})")
    assert "&lt;script&gt;" in escaped and "&lt;img&gt;" in escaped and "&lt;date&gt;" in escaped
    assert "未填写" in run("taskInfoHtml({})")
    broken = run("taskInfoHtml({infoInvalid:true,info:{}})")
    assert "写法不对" in broken and "未填写" not in broken
    # What is broken is said on the row itself; the summary warning lives on another page.
    named = run("taskInfoHtml({infoInvalid:'这些字段不是文字:<status>',info:{}})")
    assert "这些字段不是文字:&lt;status&gt;" in named and "顶部" not in named
    assert "认不出" in run("taskInfoHtml({info:{verdictUnrecognized:'<x>'}})")
    assert "&lt;x&gt;" in run("taskInfoHtml({info:{verdictUnrecognized:'<x>'}})")


def test_title_split_skips_clock_drive_and_url_colons_and_never_loses_the_description():
    assert run("taskText({name:'A',desc:'运行 C:\\\\tools\\\\sync.exe'})") == {"title": "运行 C:\\tools\\sync.exe", "summary": ""}
    assert run("taskText({name:'A',desc:'打开 https://example.com 看结果'})") == {"title": "打开 https://example.com 看结果", "summary": ""}
    assert run("taskText({name:'A',desc:'备份:每天跑一次'})") == {"title": "备份", "summary": "每天跑一次"}
    # A title from info must not make an unsplittable description disappear.
    assert run("taskText({name:'A',desc:'只有一句说明',info:{title:'标题'}})") == {"title": "标题", "summary": "只有一句说明"}
    assert run("taskText({name:'A',desc:'标题',info:{title:'标题'}})") == {"title": "标题", "summary": ""}


def test_a_verdict_the_page_does_not_know_reads_as_unrecognized():
    # The server and the page each keep a verdict table; a value added on one side only must not
    # quietly render as not assessed.
    assert run("taskVerdictBucket({info:{verdict:'future'}})") == "unrecognized"
    assert "建议无法识别" in run("taskVerdictBadge({info:{verdict:'future'}})")
    assert "建议值「future」认不出" in run("taskInfoHtml({info:{verdict:'future'}})")
    assert run("taskVerdictBucket({info:{verdict:''}})") == "unassessed"


def test_rows_still_being_read_have_their_own_bucket_and_option():
    assert run("taskVerdictBucket({infoPending:true,info:{verdict:'fix'}})") == "pending"
    assert "建议未读取 (1)" in run("taskVerdictOptions([{infoPending:true}])")
    assert "建议未读取" not in run("taskVerdictOptions([{name:'AcmeOther'}])")
    # A selected option stays listed even at zero, or the control would show all while still filtering.
    assert "建议未读取 (0)" in run("taskVerdictOptions([{name:'AcmeOther'}],'pending')")


def test_rows_show_title_verdict_summary_name_and_category_context():
    html = run("renderAutomations();$('automation-list').innerHTML", setup())
    for text in ("同步示例文件", "要修", "检查并同步合成文件。", "AcmeSync", "Generated test tasks", "1 项"):
        assert text in html
    assert 'data-task="AcmeSync"' in html and 'data-act="run"' in html
    cell = run("C.find(c=>c[0]==='name')[2](row)", setup())
    assert '<strong' in cell and 'title="检查并同步合成文件。"' in cell


def test_verdict_counts_include_unassessed_and_zero_options():
    result = run("taskVerdictOptions([row,{name:'AcmeOther'}])", setup())
    assert "要修 (1)" in result and "急修 (0)" in result and "未评估 (1)" in result and "无法识别 (0)" in result
    assert "建议未读取" not in result


def test_verdict_chip_never_reads_like_the_live_state_chip():
    verdict = run("taskVerdictBadge({info:{verdict:'disabled'}})")
    assert "保持停用" in verdict and "task-verdict" in verdict and "已停用" not in verdict
    keep = run("taskVerdictBadge({info:{verdict:'keep'}})")
    assert "保留" in keep and "✓" not in keep
    html = run("taskListRow({...row,state:'Disabled',info:{verdict:'disabled'}})", setup())
    assert "保持停用" in html and "已停用" in html


def test_an_opened_detail_survives_a_table_rebuild_until_closed():
    # render() rebuilds the whole table; the page re-reads and re-renders right after a details link
    # switches tabs, which used to collapse the detail the link had just opened.
    result = run("""const inserted=[];
const fakeRow={dataset:{i:'0',name:'AcmeSync'},insertAdjacentHTML:where=>inserted.push(where)};
document.querySelectorAll=selector=>selector.startsWith('#tbl tbody tr')?[fakeRow]:[];
render();fakeRow.dataset.i=String(VIEW.findIndex(r=>r.name==='AcmeSync'));
openDetail(fakeRow);const opened=inserted.length;
render();const rebuilt=inserted.length;
closeDetail();render();({opened,rebuilt,afterClose:inserted.length,open:OPEN_DETAIL})""", table_setup())
    assert result == {"opened": 1, "rebuilt": 2, "afterClose": 2, "open": None}


def test_next_run_distinguishes_missing_empty_broken_and_invalid_time():
    result = run("[{}, {nextRun:null}, {infoError:'synthetic error'}, {nextRun:'invalid'}].map(r=>taskNextRun(r))")
    assert result == ["未读取下次运行时间", "没有下次运行时间", "下次运行时间读取失败", "时间无法识别"]
    assert run("taskNextRun({nextRun:'invalid'},true)") == "时间无法识别"
    html = run("taskListRow({...row,infoError:'synthetic error'})", setup())
    assert "下次运行时间读取失败" in html and "没有下次运行时间" not in html
    # The narrow table cell uses short words; they must stay as distinct as the long ones.
    short = run("[{}, {nextRun:null}, {infoError:'x'}, {nextRun:'invalid'}, {state:'Disabled'}].map(r=>taskNextRun(r,true))")
    assert len(set(short)) == len(short)


def test_relative_time_under_half_a_minute_reads_as_now_not_zero_minutes():
    assert run("relTime(new Date(Date.now()+10000).toISOString())") == "即将运行"
    assert run("relTime(new Date(Date.now()-10000).toISOString())") == "刚刚"
    assert run("relTime(new Date(Date.now()+5*60000).toISOString())") == "5分后"


def test_a_disabled_task_never_shows_the_next_run_the_scheduler_still_reports():
    # Windows keeps computing NextRunTime from the triggers of a disabled task; it will not run.
    soon = "nextRun:new Date(Date.now()+60000).toISOString()"
    assert run(f"taskNextRun({{state:'Disabled',{soon}}})") == "已停用，不会运行"
    assert run(f"taskNextRun({{state:'Disabled',{soon}}},true)") == "不会运行"
    html = run(f"taskListRow({{...row,state:'Disabled',{soon}}})", setup())
    assert "已停用，不会运行" in html and "下次 " not in html
    assert run(f"C.find(c=>c[0]==='nextRun')[3]({{state:'Disabled',{soon}}})") == ""


def test_uncategorized_rows_and_unknown_verdict_are_visible_in_all_filter():
    prefix = setup() + "row.cat='未分类';row.info={};"
    html = run("renderAutomations();$('automation-list').innerHTML", prefix)
    assert "AcmeSync" in html and "未分类" in html and "未评估 1" in html
    assert run("automationRows(ROWS,'','','unassessed').length", prefix) == 1


def table_setup():
    return setup() + """
renderFresh=()=>{};updateBadges=()=>{};renderTL=()=>{};renderHeat=()=>{};renderScores=()=>{};
row.info.title='Zulu';
ROWS.push({...row,name:'AcmeOther',info:{title:'Alpha',verdict:'urgent'},sk:'bad',state:'Disabled'});
"""


def test_table_sort_filters_and_counts_compose_and_detail_links_clear_filters():
    result = run("""sortKey='name';render();const sorted=VIEW.map(r=>r.name);
$('only').checked=true;render();const bad=VIEW.map(r=>r.name);
$('hideoff').checked=true;render();const hidden=VIEW.length;
$('only').checked=false;$('hideoff').checked=false;$('task-verdict').value='fix';render();
const filtered=VIEW.map(r=>r.name), counts=$('task-verdict').innerHTML;
showView=()=>{};document.querySelector=()=>null;toast=()=>{};
focusTask('AcmeOther');({sorted,bad,hidden,filtered,counts,focused:VIEW.map(r=>r.name),verdict:$('task-verdict').value})""", table_setup())
    assert result["sorted"] == ["AcmeOther", "AcmeSync"]
    assert result["bad"] == ["AcmeOther"] and result["hidden"] == 0
    assert result["filtered"] == ["AcmeSync"]
    assert "急修 (1)" in result["counts"] and "要修 (1)" in result["counts"]
    assert result["focused"] == ["AcmeOther"] and result["verdict"] == ""


def test_the_cursor_follows_its_task_when_the_order_changes():
    # The open detail follows the task by name; the cursor is an index. After a re-sort the same
    # index points at another task, and r/s would act on a task other than the one being read.
    result = run("""sortKey='name';asc=true;render();cur=VIEW.findIndex(r=>r.name==='AcmeSync');
asc=false;render();VIEW[cur].name""", table_setup())
    assert result == "AcmeSync"


def test_sorting_by_a_column_that_is_then_hidden_falls_back_to_the_name():
    result = run("""sortKey='lastRun';asc=false;render();
({sortKey,asc,order:VIEW.map(r=>r.name)})""", table_setup())
    assert result == {"sortKey": "name", "asc": True, "order": ["AcmeOther", "AcmeSync"]}


def test_timeline_rows_are_labelled_with_task_titles():
    timeline = {"date": "2026-01-01", "now": "01:00", "note": "", "rows": [
        {"name": "AcmeSync", "points": ["00:30"], "spans": [], "eventDriven": [], "unknownTriggers": [], "actual": []},
        {"name": "AcmeUnlisted", "points": [], "spans": [], "eventDriven": ["logon"], "unknownTriggers": [], "actual": []}]}
    html = run("renderTL();$('tl').innerHTML", setup() + "DATA.timeline=" + json.dumps(timeline) + ";")
    assert 'title="同步示例文件 · AcmeSync">同步示例文件</div>' in html
    assert 'title="同步示例文件 计划 00:30"' in html
    # Not in the task table: the machine name stays rather than an invented label.
    assert 'title="AcmeUnlisted">AcmeUnlisted</div>' in html


def test_escape_clears_the_task_search_and_the_table_follows():
    # Chrome and Edge clear a type=search box on Esc and, after a blur, fire no input event: the box
    # would be empty while the table stays filtered. The page clears it itself and fires input, so
    # the existing listener re-filters; only the second Esc leaves the box.
    result = run("""(()=>{
      const box=$('q');box.tagName='INPUT';box.type='search';box.value='AcmeOther';box.blurred=0;box.blur=()=>box.blurred++;
      box.dispatchEvent=e=>{if(e.type==='input') render();};
      document.activeElement=box;document.querySelector=()=>null;
      const key={key:'Escape',preventDefault(){this.prevented=true;}};
      render();const filtered=VIEW.length;
      handleEscape(key);const cleared=[box.value,VIEW.length,box.blurred,key.prevented];
      handleEscape({...key});
      return {filtered,cleared,blurred:box.blurred};
    })()""", table_setup() + "var Event=class{constructor(type){this.type=type;}};")
    assert result == {"filtered": 1, "cleared": ["", 2, 0, True], "blurred": 1}


def test_exported_snapshots_record_every_filter_in_use():
    prefix = table_setup() + """
$('q').value='Zulu';$('cat').value='Generated test tasks';$('task-verdict').value='fix';$('only').checked=true;
$('automation-verdict').value='urgent';$('pipeline-task-search').value='备份';$('pipeline-verdict').value='keep';PIPELINE_QUERY='mcp';
"""
    result = run("""({tasks:pageSnapshot('tasks').data.filters,automations:pageSnapshot('automations').data.filters,
pipelines:pageSnapshot('pipelines').data.filters})""", prefix)
    assert result["tasks"] == {"query": "Zulu", "category": "Generated test tasks", "verdict": "fix",
                               "onlyProblems": True, "hideDisabled": False}
    assert result["automations"]["verdict"] == "urgent"
    assert result["pipelines"] == {"query": "备份", "verdict": "keep", "issueQuery": "mcp"}


def test_default_columns_are_compact_but_all_columns_remain_available():
    defaults = run("shownCols().map(c=>c[0])")
    assert defaults == ["selc", "name", "sl", "triggers", "nextRun", "ops"]
    assert run("TASK_COLUMNS=new Set(C.map(c=>c[0]));HYG_OPEN=true;shownCols().length===C.length")
    assert run("TASK_COLUMNS.clear();HYG_OPEN=false;shownCols().map(c=>c[0])") == ["selc", "name"]


def test_delete_replaces_the_retire_button_on_every_tab_that_lists_tasks():
    # 停用并移出清单只动了控制器自己的登记,留下监控清单、分类配置和备份;删除取代了它。
    # 三处列表都不能再出现那个按钮,否则同一个任务会有两种「移除」,而少做的那一种看起来一样可点。
    prefix = table_setup() + "PIPELINE_DEFS.sync.name='AcmeSync';COMPONENTS={available:true,tasks:[]};"
    html = run("render();renderAutomations();renderPipelines();[$('tbl').innerHTML,$('automation-list').innerHTML,$('pipeline-body').innerHTML]", prefix)
    for page in html:
        assert "data-retire" not in page and "停用并移出清单" not in page and "i-retire" not in page
    # AcmeSync 在这里被设成流水线任务,它在哪一页都没有删除按钮;普通任务在两张列表上都有。
    assert 'data-task-delete="AcmeOther"' in html[0] and 'data-task-delete="AcmeOther"' in html[1]
    assert all('data-task-delete="AcmeSync"' not in page for page in html)
    assert run("typeof retireTask") == "undefined"


def test_pipeline_uses_metadata_search_verdicts_and_shared_stop_controls():
    prefix = setup() + """
PIPELINE_DEFS.sync.name='AcmeSync';PIPELINE_DEFS.backup.name='AcmeBackup';
row.state='Running';COMPONENTS={available:true,tasks:[]};
"""
    html = run("$('pipeline-task-search').value='重试';renderPipelines();$('pipeline-body').innerHTML", prefix)
    assert html.count('class="card pipeline-run"') == 1
    assert "同步示例文件" in html and "要修" in html and 'data-act="stop"' in html
    assert 'data-act="disable"' in html and 'data-task="AcmeSync"' in html
    assert "未找到对应记录" in html
    assert "没有符合筛选条件" in run("$('pipeline-verdict').value='urgent';renderPipelines();$('pipeline-body').innerHTML", prefix)


def test_pipeline_names_why_a_task_is_missing():
    base = setup() + """
PIPELINE_DEFS.sync.name='AcmeSync';PIPELINE_DEFS.backup.name='AcmeBackup';COMPONENTS={available:true,tasks:[]};
"""
    body = "renderPipelines();$('pipeline-body').innerHTML"

    def states(html):
        # The state column, not the schedule line, has to carry the reason.
        return re.findall(r'<div class="automation-state">(.*?)</div>', html, re.S)

    loading = run(body, base + "DATA=null;ROWS=[];")
    assert all("正在读取计划任务" in cell for cell in states(loading)) and states(loading)
    assert "建议未读取" in loading and "未评估" not in loading
    failed = run(body, base + "DATA=null;ROWS=[];TASKS_LOAD_ERROR='synthetic failure';")
    assert all("计划任务读取失败" in cell for cell in states(failed)) and states(failed)
    absent = run(body, base)
    assert any("计划程序里没有这个任务" in cell for cell in states(absent))
    twice = run(body, base + "ROWS.push({...row,name:'AcmeBackup'},{...row,name:'AcmeBackup'});")
    assert any("2 个同名任务" in cell for cell in states(twice))


def test_a_new_read_clears_the_previous_failure_while_it_is_in_flight():
    # While the next read is pending, the pipeline cards should say it is being read, not repeat
    # a failure that is already in the past.
    assert run("TASKS_LOAD_ERROR='old failure';api=()=>new Promise(()=>{});load();TASKS_LOAD_ERROR") is None


def pipeline_setup(extra=""):
    return setup() + """
PIPELINE_DEFS.sync.name='AcmeSync';PIPELINE_DEFS.backup.name='AcmeBackup';
ROWS.push({...row,name:'AcmeBackup',info:{title:'Synthetic backup'},desc:''});COMPONENTS={available:true,tasks:[]};
""" + extra


def test_pipeline_search_finds_a_card_by_its_title():
    html = run("$('pipeline-task-search').value='每日备份';renderPipelines();$('pipeline-body').innerHTML", pipeline_setup())
    assert html.count('class="card pipeline-run"') == 1 and 'id="pipeline-backup"' in html


def test_pipeline_snapshot_age_is_stated_and_an_old_snapshot_is_flagged():
    now = "const NOW=Date.UTC(2026,0,10,12);"
    assert run(now + "pipelineSnapshotAge(null,NOW).text") == "快照时间未记录"
    assert run(now + "pipelineSnapshotAge('not a time',NOW).text") == "快照时间无法识别"
    fresh = run(now + "pipelineSnapshotAge(NOW/1000-600,NOW)")
    assert fresh["stale"] is False and "不到 1 小时前" in fresh["text"]
    old = run(now + "pipelineSnapshotAge(NOW/1000-9*86400,NOW)")
    assert old["stale"] is True and old["age"] == "9 天前"
    body = "renderPipelines();({html:$('pipeline-body').innerHTML,cls:$('pipeline-sample').className})"
    stale = run(body, pipeline_setup("COMPONENTS.captured_at=Date.now()/1000-9*86400;"))
    assert "9 天前采集的快照" in stale["html"] and stale["cls"] == "pipeline-stale"
    current = run(body, pipeline_setup("COMPONENTS.captured_at=Date.now()/1000-600;"))
    assert "采集的快照" not in current["html"] and current["cls"] == "faint"


def test_pipeline_issue_reasons_read_in_chinese_and_stay_searchable_by_code():
    findings = [{"area": "mcp", "name": "synthetic-mcp", "status": "conflict", "reason": "managed_block_modified_by_user"},
                {"area": "skills", "name": "synthetic-skill", "status": "blocked", "reason": "synthetic_unknown_code"}]
    prefix = pipeline_setup("COMPONENTS.tasks=[{name:'AcmeSync',last_run_v1:{status:'degraded',findings:"
                            + json.dumps(findings) + "}}];")
    html = run("renderPipelines();$('pipeline-issues').innerHTML", prefix)
    assert 'title="原因码 managed_block_modified_by_user">同步管理的配置段被手动改过' in html
    # A code with no wording yet is shown as it is, never hidden or guessed.
    assert "<td>synthetic_unknown_code</td>" in html
    for query in ("手动改过", "managed_block"):
        rows = run(f"PIPELINE_QUERY={json.dumps(query)};renderPipelineIssues();$('pipeline-issues').innerHTML", prefix)
        assert rows.count("<tr>") == 2 and "synthetic-mcp" in rows


def test_pipeline_reset_before_first_response_needs_no_issue_search_input():
    result = run("""
const getElement=document.getElementById;
document.getElementById=id=>id==='pipeline-search'?null:getElement(id);
let renders=0;renderPipelines=()=>renders++;
PIPELINE_QUERY='old issue';$('pipeline-task-search').value='old task';$('pipeline-verdict').value='fix';
resetFilters('pipelines');
({query:PIPELINE_QUERY,search:$('pipeline-task-search').value,verdict:$('pipeline-verdict').value,renders})
""")
    assert result == {"query": "", "search": "", "verdict": "", "renders": 1}
