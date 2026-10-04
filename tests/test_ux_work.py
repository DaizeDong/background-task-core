"""工作台与工作记录这一区的交互约定,只用合成数据(AcmeCorp、user1@example.com)。"""
import json
import re
from pathlib import Path

from test_operations_ui import run

STATIC = Path(__file__).resolve().parents[1] / "scripts/task_console/static"
HOUR = 3600 * 1000


def item(item_id, role="agent_work", state="running", **extra):
    row = {"id": item_id, "role": role, "title": "AcmeCorp " + item_id, "summary": "Synthetic summary for user1@example.com",
           "state": state, "source": "acme-source", "updated_at": "2030-01-02T00:00:00Z",
           "execution": {"state": state} if role == "agent_work" else None}
    row.update(extra)
    return row


def feed(*items):
    return {"available": True, "items": list(items), "events": [], "sources": [],
            "coverage": {"total": len(items), "returned": len(items)}, "observed_at": "2030-01-02T00:00:00Z"}


def setup_work(data):
    return "WORK=" + json.dumps(data) + ";"


# 一个够用的假元素:有 dataset、matches 和 closest,认得标签名和 [data-x] / [data-x="v"] 两种选择器。
FAKE_DOM = r"""
function el(tag,attrs={},parent=null){
  const node={tagName:tag.toUpperCase(),attrs,parent,dataset:{},disabled:false};
  for(const [key,value] of Object.entries(attrs)) if(key.startsWith('data-'))
    node.dataset[key.slice(5).replace(/-([a-z])/g,(m,c)=>c.toUpperCase())]=value;
  node.matches=selector=>selector.split(',').some(part=>{
    part=part.trim();const attr=part.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
    if(attr) return Object.hasOwn(attrs,attr[1]) && (attr[2]===undefined || attrs[attr[1]]===attr[2]);
    return node.tagName===part.toUpperCase();
  });
  node.closest=selector=>{for(let n=node;n;n=n.parent) if(n.matches(selector)) return n;return null;};
  return node;
}
const click=target=>({target,preventDefault(){},ctrlKey:false,metaKey:false,shiftKey:false,altKey:false});
"""


def test_interrupted_agent_work_gets_its_own_card_and_hides_when_none():
    failed = item("w-failed", state="failed")
    cancelled = item("w-cancelled", state="cancelled")
    shown = run("renderWorkPlatform();[$('interrupted-card').hidden,$('interrupted-work').innerHTML]",
                setup_work(feed(failed, cancelled, item("w-running"))))
    assert shown[0] is False
    assert "AcmeCorp w-failed" in shown[1]
    # 已取消是人自己收的尾,不算出错。
    assert "AcmeCorp w-cancelled" not in shown[1]
    hidden = run("renderWorkPlatform();[$('interrupted-card').hidden,$('interrupted-work').innerHTML]",
                 setup_work(feed(item("w-running"), cancelled)))
    assert hidden == [True, ""]


def test_attention_strip_names_each_source_state_and_hides_only_when_all_clear():
    strip = "renderAttentionStrip();[$('attention-strip').hidden,$('attention-strip').innerHTML]"
    mixed = run(strip, "attentionSummary=()=>({tasks:{state:'ok',count:2},disk:{state:'failed',reason:'HTTP 500'}});")
    assert mixed[0] is False
    assert "2 个任务失败" in mixed[1] and "读取失败" in mixed[1]
    # 失败的任务落在只装失败任务的范围上,不落在混着输出过期和摄入的「任务与产物」上。
    assert 'data-attention-filter="failed"' in mixed[1]
    pending = run(strip, "attentionSummary=()=>({tasks:{state:'pending',count:0},repos:{state:'ok',count:0}});")
    assert pending[0] is False and "读取中" in pending[1]
    clear = run(strip, "attentionSummary=()=>({tasks:{state:'ok',count:0},repos:{state:'ok',count:0},disk:{state:'ok',count:0}});")
    assert clear[0] is True
    disk = run(strip, "attentionSummary=()=>({disk:{state:'ok',count:1,usedPct:95.5},repos:{state:'ok',count:0,unchecked:'仓库没在查'}});")
    assert "磁盘 95.5%" in disk[1] and 'data-attention-filter="storage"' in disk[1]
    # 没在查不是零:它有自己的一枚,不和零一样消失。
    assert "代码仓库 未检查" in disk[1]


def test_unreadable_work_is_stated_once_with_retry_and_disables_filters():
    result = run("""renderWorkPlatform();
      const overview=['overview-work-state','interrupted-work','active-work','tracked-work','recent-results','decision-state'].map(id=>$(id).innerHTML).join('');
      const work=['work-list','work-activity','source-volume'].map(id=>$(id).innerHTML).join('');
      ({overview,work,search:$('work-search').disabled,role:$('work-role').disabled,title:$('work-search').title,
        count:$('active-count').innerHTML,meta:$('work-source-state').textContent,coverage:$('work-coverage').textContent})""",
                 "WORK={available:false,reason:'work_reader_failed'};$('work-search').setAttribute=()=>{};")
    for html in (result["overview"], result["work"]):
        assert html.count("state-error") == 1 and "data-work-retry" in html
        # 原始代号只进悬停(title),不出现在正文里。
        assert "work_reader_failed" not in re.sub(r'title="[^"]*"', "", html)
    assert result["search"] is True and result["role"] is True
    assert "工作记录读取失败，暂时无法筛选或查看" in result["title"]
    assert "count-broken" in result["count"]
    assert "暂时读不到工作记录" not in json.dumps(result, ensure_ascii=False)


def test_empty_feed_is_a_zero_not_a_failure_and_keeps_filters_enabled():
    result = run("renderWorkPlatform();[$('work-list').innerHTML,$('work-search').disabled,$('active-count').innerHTML]",
                 setup_work(feed()))
    assert "没有符合筛选条件的记录" in result[0] and "state-error" not in result[0]
    assert not result[1]
    assert "count-zero" in result[2]


def test_loading_counts_use_the_loading_cell():
    result = run("renderWorkPlatform();[$('tracked-count').innerHTML,$('overview-work-state').innerHTML]",
                 "WORK=null;WORK_LOADING=true;")
    assert "count-loading" in result[0] and "state-loading" in result[1]


def test_rows_use_relative_times_due_warnings_and_drop_the_default_source():
    setup = f"""const now=Date.now();
      const recent={json.dumps(item('w-recent', role='tracked_item', state='pending', source='agent-center:work'))};
      recent.updated_at=new Date(now-2*{HOUR}).toISOString();recent.due_at=new Date(now+3*{HOUR}).toISOString();
      const late={json.dumps(item('w-late', role='tracked_item', state='pending'))};late.due_at=new Date(now-{HOUR}).toISOString();
      const done={json.dumps(item('w-done', role='tracked_item', state='done'))};done.due_at=new Date(now-{HOUR}).toISOString();"""
    result = run("[workItemRow(recent,true),workItemRow(late),workItemRow(done)]", setup)
    recent, late, done = result
    assert "2 小时前" in recent
    assert re.search(r'<time[^>]*title="\d{4}-\d\d-\d\d \d\d:\d\d:\d\d"[^>]*>2 小时前</time>', recent)
    assert "due-warn" in recent and "3 小时后" in recent
    assert "Agent 工作单" not in recent
    assert "due-warn" in late and "已逾期" in late
    # 已经结束的事项不再催。
    assert "due-warn" not in done
    assert "acme-source" in late


def test_header_counts_say_the_total_and_only_mention_truncation_when_it_happens():
    todos = [item(f"t{i}", role="tracked_item", state="pending", updated_at=f"2030-01-0{i + 1}T00:00:00Z") for i in range(7)]
    result = run("renderWorkPlatform();[$('tracked-count').innerHTML,$('active-count').innerHTML]",
                 setup_work(feed(*todos, item("a1"), item("a2"), item("a3"))))
    assert result[0] == "7 · 显示前 5"
    assert result[1] == "3"


def test_coverage_is_written_in_words_and_hidden_when_complete():
    data = feed(item("a1"))
    data["coverage"] = {"total": 25, "returned": 23, "invalid": 2}
    partial = run("renderWorkPlatform();[$('work-coverage').textContent,$('work-coverage').hidden]", setup_work(data))
    assert partial[0] == "共 25 条记录，本次读到 23 条，2 条读取失败。" and partial[1] is False
    complete = run("renderWorkPlatform();[$('work-coverage').textContent,$('work-coverage').hidden]", setup_work(feed(item("a1"))))
    assert complete == ["", True]


def test_compact_row_opens_its_record_but_its_buttons_do_not():
    result = run("""const opened=[];openWorkRecord=id=>opened.push(id);
      const row=el('article',{'data-work-row':'w1'}), subject=el('div',{},row), summary=el('p',{},subject);
      const actions=el('div',{},row), action=el('button',{'data-work-item':'w1','data-work-action':'agent'},actions), label=el('span',{},action);
      routeWorkClick(click(summary));const afterSummary=[...opened];
      routeWorkClick(click(label));routeWorkClick(click(action));
      ({afterSummary,all:opened})""", FAKE_DOM)
    assert result == {"afterSummary": ["w1"], "all": ["w1"]}
    html = run("workItemRow(row,true)", "const row=" + json.dumps(item("w1")) + ";")
    assert 'data-work-row="w1"' in html


def test_row_actions_are_written_out_and_the_detail_eye_is_gone():
    offer = item("t1", role="tracked_item", state="pending", actions={
        "available": True, "revision": "sha256:synthetic", "links": [], "current": None,
        "offers": [{"id": "agent", "kind": "agent", "label": "交给 Agent", "enabled": True}]})
    html = run("workActionButtons(row)", "const row=" + json.dumps(offer) + ";")
    assert re.search(r'<button[^>]*data-work-action="agent"[^>]*>.*?<span>交给 Agent</span></button>', html)
    bare = run("workItemRow(row)", "const row=" + json.dumps(item("t2", role="tracked_item", state="pending")) + ";")
    assert "i-eye" not in bare and "查看详情" not in bare
    progress = run("workActionButtons(row)", "const row=" + json.dumps({**offer, "actions": {**offer["actions"], "offers": [],
                   "current": {"id": "r1", "kind": "agent", "state": "running", "work_item_id": "w9"}}}) + ";")
    assert ">查看进度</button>" in progress and "i-eye" not in progress
    origin = run("workActionButtons(row)", "const row=" + json.dumps(item("w3", origin_item_id="t1")) + ";")
    assert ">查看原待办</button>" in origin and "i-eye" not in origin


def test_work_filters_are_remembered_without_the_search_text():
    store = "const store={};localStorage.getItem=key=>store[key] ?? null;localStorage.setItem=(key,value)=>{store[key]=String(value);};"
    saved = run("setWorkFilters({role:'all',state:'done',query:'AcmeCorp'});JSON.parse(store['tc.work.filters'])",
                store + setup_work(feed(item("a1"))))
    assert saved == {"role": "all", "state": "done", "source": ""}
    restored = run("""store['tc.work.filters']=JSON.stringify({role:'work',state:'done',source:''});
      restoreWorkFilters();renderWorkPlatform();
      [WORK_STATE,$('work-state').value,$('work-search').value,WORK_QUERY,$('work-filter-restored').hidden]""",
                   store + setup_work(feed(item("a1"))))
    assert restored == ["done", "done", "", "", False]
    # 存的值不认识(别的版本留下的)就不用,照旧从默认开始。
    junk = run("store['tc.work.filters']=JSON.stringify({role:'nonsense',state:'done',source:''});[restoreWorkFilters(),WORK_ROLE,WORK_STATE]",
               store + setup_work(feed(item("a1"))))
    assert junk == [False, "work", "unfinished"]


def test_record_pager_says_why_it_is_disabled():
    result = run("""$('work-detail').showModal=()=>{};openWorkRecord('a2');
      const first=[$('work-detail-prev').disabled,$('work-detail-prev').title,$('work-detail-next').disabled];
      openWorkRecord('a1',true);
      [first,[$('work-detail-next').disabled,$('work-detail-next').title,$('work-detail-prev').disabled]]""",
                 setup_work(feed(item("a1", updated_at="2030-01-01T00:00:00Z"), item("a2", updated_at="2030-01-02T00:00:00Z"))))
    first, last = result
    assert first[0] is True and "第一条" in first[1] and not first[2]
    assert last[0] is True and "最后一条" in last[1] and not last[2]


def automation_setup():
    from tools.make_fixtures import automation_info_case, console_page_fixture
    case = automation_info_case()
    page = console_page_fixture()
    page["groups"][0]["rows"][0]["info"] = case["info"]
    return "DATA=" + json.dumps(page) + ";ROWS=DATA.groups.flatMap(g=>g.rows.map(r=>({...r,cat:g.cat})));"


def test_group_verdict_counts_are_buttons_that_filter_and_urgent_counts_are_red():
    html = run("renderAutomations();$('automation-list').innerHTML", automation_setup())
    assert re.search(r'<button[^>]*class="verdict-count urgent"[^>]*data-automation-verdict="fix"[^>]*aria-pressed="false"', html)
    result = run("""routeWorkClick(click(el('button',{'data-automation-verdict':'fix'})));
      const on=[$('automation-verdict').value,$('automation-list').innerHTML];
      routeWorkClick(click(el('button',{'data-automation-verdict':'fix'})));
      [on,$('automation-verdict').value]""", FAKE_DOM + automation_setup())
    (value, listing), cleared = result
    assert value == "fix" and 'data-automation-verdict="fix" aria-pressed="true"' in listing
    assert cleared == ""


def test_phone_toolbar_and_desktop_page_scroll_rules_are_present():
    css = (STATIC / "page-work.css").read_text(encoding="utf-8")
    phone = css[css.index("@media(max-width:767px)"):]
    assert "#work .work-toolbar{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))" in phone
    assert "#work .work-toolbar>input{grid-column:1 / -1" in phone
    desktop = re.search(r"@media\(min-width:768px\)\{\n  #work [^@]*", css).group(0)
    assert "max-height:none;overflow:visible" in desktop
