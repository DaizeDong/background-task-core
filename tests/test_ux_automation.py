"""自动化三个标签页(任务开关、同步与备份、运行详情)的可读性:失败的任务一眼看得到,原因和下一步就在眼前。

数据全部是这里手写的合成字面量(AcmeSync 一类),在 node:vm 的假 DOM 里跑,不碰浏览器、不碰后端。
真实浏览器里的版式另见交付说明。
"""
import json
import re

from test_automation_ui import setup, table_setup
from test_operations_ui import run


def visible(html):
    """去掉标签和属性之后人看得见的字。title 里的原码不算。"""
    return re.sub(r"<[^>]*>", " ", html)


HEALTH_SETUP = setup() + """
const okRow={...row,name:'AcmeOk',info:{title:'Bravo'},sk:'ok',sl:'正常',issues:[]};
const warnRow={...row,name:'AcmeWarn',info:{title:'Charlie'},sk:'ok',sl:'正常',issues:[['warn','合成警告']]};
const badRow={...row,name:'AcmeBad',info:{title:'Delta'},sk:'bad',sl:'失败 0x1',rcHex:'0x1',issues:[]};
const offRow={...row,name:'AcmeOff',info:{title:'Alpha'},sk:'disabled',sl:'已停用',state:'Disabled',issues:[]};
renderFresh=()=>{};updateBadges=()=>{};renderTL=()=>{};renderHeat=()=>{};renderScores=()=>{};
"""


# ---------------------------------------------------------------- 健康芯片
def test_every_task_list_shows_health_not_the_scheduler_state():
    html = run("taskListRow({...row,state:'Ready',sk:'bad',sl:'失败 0x1'})", setup())
    chip = re.search(r'<div class="automation-state">(.*?)</div>', html, re.S).group(1)
    assert "×" in chip and "✓" not in chip
    assert "失败 · 退出码 1" in chip and 'title="上次运行结果 0x1：' in chip
    # 计划程序的状态退成小字,不再是一枚绿色的「✓ 已启用」。
    assert "计划程序：已启用" in chip and "status-chip ok" not in chip
    # 运行详情的「状态」列是同一个芯片。
    cell = run("C.find(c=>c[0]==='sl')[2]({...row,state:'Ready',sk:'bad',sl:'失败 0x1'})", setup())
    assert "失败 · 退出码 1" in cell and "×" in cell
    labels = run("[okRow,warnRow,badRow,offRow,{name:'AcmeNew'}].map(r=>taskHealth(r).label)", HEALTH_SETUP)
    assert labels == ["正常", "有警告", "失败 · 退出码 1", "已停用", "未知"]
    assert 'class="automation-row is-failing"' in run("taskListRow(badRow)", HEALTH_SETUP)


def test_exit_codes_read_in_decimal_with_the_raw_code_only_in_the_title():
    codes = run("[{rcHex:'0x1'},{rcHex:'0x8004131F'},{sl:'失败 0x2'},{sl:'失败 ?'}].map(r=>taskExitCode(r))")
    assert codes[0] == {"text": "1", "hex": "0x1", "meaning": "程序以 1 退出，通常是脚本自己报错", "label": "退出码 1"}
    assert codes[1]["text"] == "-2147216609" and codes[1]["meaning"]
    assert codes[2]["text"] == "2" and codes[3]["text"] == "未知"
    # 计划程序自己的状态码不按退出码写:「267009」没人看得懂。
    assert run("[taskExitCode({rcHex:'0x41301'}).label,taskExitCode({rcHex:'0x1'}).label]") == ["正在运行", "退出码 1"]
    assert run("taskHealth({sk:'bad',rcHex:'0x8004131F'}).label") == "失败 · 上一次运行还没结束，这次没有启动"
    html = run("taskListRow(badRow)", HEALTH_SETUP)
    assert not re.search(r"0x[0-9a-f]+", visible(html), re.I)


# ---------------------------------------------------------------- 排序
def test_default_sort_is_by_severity_and_a_stored_name_sort_is_kept():
    order = run("ROWS=[okRow,badRow,offRow,warnRow];render();VIEW.map(r=>r.name)", HEALTH_SETUP)
    assert order == ["AcmeBad", "AcmeWarn", "AcmeOk", "AcmeOff"]
    assert run("ROWS=[okRow,badRow,warnRow];render();[sortKey,$('task-sort-state').textContent]", HEALTH_SETUP) == \
        ["severity", "按严重程度：失败在前"]
    stored = run("""({key:sortKey,asc}=loadTaskSort(()=>({getItem:()=>JSON.stringify({key:'name',asc:true})})));
ROWS=[okRow,badRow,offRow,warnRow];render();VIEW.map(r=>r.name)""", HEALTH_SETUP)
    assert stored == ["AcmeOff", "AcmeOk", "AcmeWarn", "AcmeBad"]
    assert run("loadTaskSort(()=>({getItem:()=>{throw new Error('denied')}}))") == {"key": "severity", "asc": True}


def test_failing_rows_come_first_within_their_group():
    groups = [{"cat": "Synthetic", "rows": [{"name": "AcmeA", "sk": "ok"}, {"name": "AcmeB", "sk": "bad"},
                                            {"name": "AcmeC", "sk": "ok"}, {"name": "AcmeD", "sk": "bad"}]},
              {"cat": "Other", "rows": [{"name": "AcmeE", "sk": "ok"}]}]
    order = run("taskRowsInOrder(groups).map(r=>r.cat+'/'+r.name)", "const groups=" + json.dumps(groups) + ";")
    assert order == ["Synthetic/AcmeB", "Synthetic/AcmeD", "Synthetic/AcmeA", "Synthetic/AcmeC", "Other/AcmeE"]


# ---------------------------------------------------------------- 计数
def test_summary_counts_say_loading_and_broken_instead_of_a_dash():
    broken = run("""(async()=>{api=async()=>{throw new Error('synthetic failure');};document.querySelector=()=>null;
      await load();return [$('s-bad').innerHTML,$('s-bad').title];})()""", "DATA=null;")
    assert "!" in broken[0] and "synthetic failure" in broken[0] and "synthetic failure" in broken[1]
    pending = run("""TASKS_LOAD_ERROR=null;api=()=>new Promise(()=>{});load();[$('s-bad').innerHTML,$('s-total').innerHTML]""", "DATA=null;")
    assert all("…" in cell and "-" not in visible(cell) for cell in pending)


# ---------------------------------------------------------------- 明细
def test_the_detail_opens_with_the_reason_and_the_next_step():
    sample = {"name": "AcmeSync", "sk": "bad", "sl": "失败 0x1", "rcHex": "0x1", "state": "Ready",
              "lastRun": "2026-01-01 08:00", "runs": {"rcs": {"0": 7755, "1": 50}, "starts": 7805},
              "issues": [["warn", "合成警告"], ["bad", "产物过期"]], "exec": "acme.exe", "args": "--sync",
              "info": {"summary": "同步合成文件。"}}
    html = run("detail(sample)", "const sample=" + json.dumps(sample) + ";")
    strip = html.index('class="task-alert bad"')
    assert strip < html.index("用途") < html.index("技术细节") < html.index("命令")
    alert = html[strip:html.index("</div>", strip)]
    assert "退出码 1（程序以 1 退出" in alert and "产物过期" in alert
    assert 'data-task-repair="AcmeSync"' in alert and 'data-act="run"' in alert
    assert "0 ×7755" in html and "0x7755" not in html
    assert not re.search(r"0x[0-9a-f]+", visible(html), re.I)
    # 没有失败也没有问题时,不画原因条。
    assert "task-alert" not in run("detail({...sample,sk:'ok',issues:[]})", "const sample=" + json.dumps(sample) + ";")


def test_focus_task_puts_the_highlight_and_the_detail_on_the_exact_row():
    prefix = setup() + """renderFresh=()=>{};updateBadges=()=>{};renderTL=()=>{};renderHeat=()=>{};renderScores=()=>{};
ROWS=[{...row,name:'AcmeSyncBeta',info:{title:'Alpha'}},{...row,name:'AcmeSync',info:{title:'Zulu'}}];
sortKey='name';asc=true;showView=()=>{};document.querySelector=()=>null;toast=()=>{};
"""
    html = run("render();cur=0;render();focusTask('AcmeSync');$('tbl').innerHTML", prefix)
    current = re.search(r'<tr data-i="\d+" data-name="([^"]+)"[^>]*class="cur[^"]*"', html)
    assert current and current.group(1) == "AcmeSync"
    after = html[current.end():]
    assert re.match(r'[^<]*>.*?</tr><tr class="det">', after, re.S)


# ---------------------------------------------------------------- 任务开关
def test_details_on_the_switch_page_expand_in_place():
    prefix = setup() + "document.querySelector=()=>null;CURVIEW='automations';registerAutomationKeys();"
    result = run("""(()=>{
      const before=$('q').value;
      toggleAutomationDetail('AcmeSync');const opened=$('automation-list').innerHTML;
      handleEscape({key:'Escape',preventDefault(){}});const escaped=$('automation-list').innerHTML;
      $('automation-search').value='';const entered=handleSearchEnter({key:'Enter',target:{id:'automation-search'},preventDefault(){}});
      return {before,after:$('q').value,opened,escaped,entered,reopened:$('automation-list').innerHTML,open:AUTO_DETAIL};
    })()""", prefix)
    assert result["before"] == result["after"]
    assert 'class="automation-detail det"' in result["opened"] and 'aria-expanded="true"' in result["opened"]
    assert result["opened"].index('data-task-row="AcmeSync"') < result["opened"].index("automation-detail")
    assert "automation-detail" not in result["escaped"]
    # 只剩一个任务时,搜索框里按 Enter 就地展开它。
    assert result["entered"] is True and result["open"] == "AcmeSync" and "automation-detail" in result["reopened"]


def test_next_run_is_relative_and_whole_minute_clocks_drop_seconds():
    html = run("taskListRow({...row,nextRun:new Date(Date.now()+2*3600000+30000).toISOString()})", setup())
    line = re.search(r'<small title="下次运行 (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)">下次 ([^<]+)</small>', html)
    assert line and line.group(2) == "2 小时后"
    schedule = run("taskSchedule({triggersRaw:[{kind:'daily',days:1,start:'2026-01-01T22:40:00'},{kind:'daily',days:1,start:'2026-01-01T07:05:30'}]})")
    assert schedule == "每天 22:40；每天 07:05:30"


# ---------------------------------------------------------------- 操作列
def test_rows_carry_two_labelled_actions_and_a_menu_for_the_rest():
    html = run("taskActionButtons({name:'AcmeSync',state:'Ready'})")
    head = html[:html.index("<div class=\"pop-menu")]
    assert re.findall(r'data-act="(\w+)"', head) == ["run", "disable"]
    assert '<span class="task-op-label">运行</span>' in head and '<span class="task-op-label">停用</span>' in head
    assert re.search(r'class="mini task-control danger" data-act="disable"', head)
    assert 'popovertarget="tkm-tbl-AcmeSync"' in head
    menu = html[html.index("<div class=\"pop-menu"):]
    for attr in ("data-launch", "data-task-repair", "data-task-delete"):
        assert attr in menu and attr not in head
    assert menu.index('class="menu-sep"') < menu.index("data-task-delete")
    # 同一个任务在三处列表里各有一份菜单,id 不能撞。
    ids = run("[taskActionButtons({name:'AcmeSync',state:'Ready'}),taskActionButtons({name:'AcmeSync',state:'Ready'},{scope:'auto'})]"
              ".map(h=>h.match(/id=\"([^\"]+)\" popover/)[1])")
    assert len(set(ids)) == 2


# ---------------------------------------------------------------- 时间轴
def test_timeline_zoom_buttons_grey_out_at_their_limits_and_a_click_opens_the_task():
    timeline = {"date": "2026-01-01", "now": "01:00", "note": "", "rows": [
        {"name": "AcmeSync", "points": ["00:30"], "spans": [], "eventDriven": [], "unknownTriggers": [], "actual": []}]}
    prefix = setup() + "DATA.timeline=" + json.dumps(timeline) + ";"
    result = run("""tlReset();renderTL();const full=[!!$('tlout').disabled,!!$('tlreset').disabled,!!$('tlin').disabled,$('tlout').title];
tlFrom=0;tlTo=TL_MIN_SPAN;renderTL();({full,deep:[!!$('tlout').disabled,!!$('tlin').disabled,$('tlin').title]})""", prefix)
    assert result["full"][:3] == [True, True, False] and "已是整天" in result["full"][3]
    assert result["deep"][:2] == [False, True] and "已放到最大" in result["deep"][2]
    picks = run("[tlClickTask({x:10,y:10,task:'AcmeSync'},{x:12,y:11}),tlClickTask({x:10,y:10,task:'AcmeSync'},{x:16,y:10}),tlClickTask(null,{x:0,y:0})]")
    assert picks == ["AcmeSync", None, None]


# ---------------------------------------------------------------- 说人话
def test_internal_codes_stay_out_of_the_visible_text():
    state = run("REPAIRS={available:false,reason:'work_reader_failed',orders:{}};taskRepairReadState()")
    assert state["text"] == "工作记录服务未连接，修复进度暂时看不到" and "work_reader_failed" in state["title"]
    assert run("workReasonText('后端给的中文原因')") == {"text": "后端给的中文原因", "code": ""}
    receipt = {"available": True, "tasks": [{"name": "AcmeSync", "task_id": "synthetic/sync", "verdict": "degraded",
               "last_run_v1": {"version": 1, "mode": "apply", "status": "degraded", "finished_at": "2026-01-01T00:00:00Z",
                               "remaining_changes": 0, "findings": []}}]}
    html = run("renderPipelines();$('pipeline-body').innerHTML", setup() + "PIPELINE_DEFS.sync.name='AcmeSync';COMPONENTS=" + json.dumps(receipt) + ";")
    text = visible(html)
    assert "正式写入" in text and "部分可用" in text
    assert not re.search(r"\bapply\b|degraded|work_reader_failed|0x[0-9a-f]+", text)


# ---------------------------------------------------------------- 同步与备份
def test_pipeline_cards_lead_with_one_verdict_and_issues_group_by_reason():
    findings = [{"area": "skills", "name": f"acme-skill-{i}", "status": "blocked", "reason": "external_installer_required"} for i in range(3)]
    findings += [{"area": "mcp", "name": "acme-mcp", "status": "conflict", "reason": "managed_block_modified_by_user"},
                 {"area": "hooks", "name": "acme-hook", "status": "blocked"}]
    components = {"available": True, "captured_at": "STALE", "tasks": [{"name": "AcmeSync", "task_id": "synthetic/sync", "verdict": "unhealthy",
                  "last_run_v1": {"version": 1, "mode": "apply", "status": "unhealthy", "findings": findings}}]}
    prefix = setup() + "PIPELINE_DEFS.sync.name='AcmeSync';PIPELINE_DEFS.backup.name='AcmeBackup';COMPONENTS=" + json.dumps(components) + \
        ";COMPONENTS.captured_at=Date.now()/1000-9*86400;row.sk='bad';"
    result = run("renderPipelines();[$('pipeline-body').innerHTML,$('pipeline-issues').innerHTML,$('pipeline-issue-count').textContent]", prefix)
    body, issues, count = result
    verdict = re.search(r'<div class="card-header"><h2 class="card-title">[^<]*</h2>(.*?)</div>', body, re.S).group(1)
    assert "status-chip bad" in verdict and "任务上次运行失败" in verdict and "同步异常" in verdict and "记录停在 9 天前" in verdict
    heads = re.findall(r'<tr class="issue-group">(.*?)</tr>', issues, re.S)
    assert len(heads) == 3
    assert "由外部安装程序管理" in heads[0] and "3 项" in heads[0]
    assert "1 项" in heads[1] and "1 项" in heads[2]
    # 每一项都还在,也没有折叠起来。
    assert issues.count("<tr>") == 1 + len(findings) and "<details" not in body
    assert count == "显示 5 / 共 5 项"
