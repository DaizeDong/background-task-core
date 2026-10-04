"""诊断(技术问题与数据来源)这一屏的可用性:读取中不像失败、行用主人认得的名字、一键处理就在问题旁边、
不常看的工程细节收起来。

数据全部是合成的(AcmeDaily、AcmeSync 之类)。行为用 node:vm 台架(test_operations_ui.run)直接调
overview.js / review.js 里的函数;真实浏览器里的走查另见交付说明。
"""
import json
import re
from pathlib import Path

from test_operations_ui import run

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "scripts" / "task_console" / "static"

# 台架里的元素是普通对象。补上这一屏用到的几样 DOM 方法,并把写进 localStorage 的东西记下来。
DOM = """
const STORE={};localStorage.getItem=k=>Object.hasOwn(STORE,k)?STORE[k]:null;localStorage.setItem=(k,v)=>{STORE[k]=String(v);};
const _get=document.getElementById;
document.getElementById=id=>{const el=_get(id);
  if(!el.classList){
    el.classList={add:c=>{if(!el.className.split(' ').includes(c)) el.className=(el.className+' '+c).trim();},
      toggle:c=>{const on=!el.className.split(' ').includes(c);on?el.classList.add(c):el.className=el.className.split(' ').filter(x=>x!==c).join(' ');return on;}};
    el.className=el.className||'';el.attrs={};el.setAttribute=(k,v)=>{el.attrs[k]=v;};
    el.scrollIntoView=()=>{};el.focus=()=>{document.activeElement=el;};el.style=el.style||{};
  }
  return el;};
"""


def tasks_data(rows, **extra):
    data = {"groups": [{"cat": "Synthetic", "rows": rows}],
            "summary": {"total": len(rows), "bad": sum(1 for r in rows if r.get("sk") == "bad"), "disabled": 0,
                        "generated": "2026-10-04 08:00:00"},
            "history": {"ingest": {"state": "ok"}}}
    data.update(extra)
    return data


def loaded(data, reads="", rows=None):
    """DATA 读到了:ROWS 照 tasks.js 的 load() 摊平;reads 里写额外的 API_READS 记录。"""
    flat = rows if rows is not None else [dict(r, cat="Synthetic") for g in data["groups"] for r in g["rows"]]
    return DOM + "DATA=" + json.dumps(data) + ";ROWS=" + json.dumps(flat) + ";" + reads


def visible(html):
    """去掉标签和属性之后人眼看得到的字。"""
    return re.sub(r"<[^>]*>", " ", html)


# ---------- 读取中、读取失败、读到了:三种样子 ----------

def test_a_slow_task_read_is_shown_as_loading_never_as_a_failure():
    result = run("renderTodo();[$('todod').innerHTML,$('review-count').textContent]",
                 DOM + "DATA=null;API_READS.set('/api/tasks',{sequence:1,pending:true});")
    assert "正在读取任务" in result[0] and "state-loading" in result[0]
    assert "读取失败" not in result[0] and "清单不完整" not in result[0]
    failed = run("renderTodo();[$('todod').innerHTML,$('todon').textContent]",
                 DOM + "DATA=null;API_READS.set('/api/tasks',{sequence:2,pending:false,error:'synthetic timeout'});")
    assert "任务读取失败" in failed[0] and "synthetic timeout" in failed[0]
    assert "data-review-retry" in failed[0] and "重试" in failed[0]
    assert failed[1] == "清单不完整"


def test_tiles_say_loading_failed_or_unchecked_and_never_mix_them_up():
    pending = run("renderTiles();$('tiles').innerHTML",
                  DOM + "DATA=null;API_READS.set('/api/tasks',{sequence:1,pending:true});")
    tiles = re.findall(r'<button type="button" class="tile".*?</button>', pending, re.S)
    assert len(tiles) == 5 and all("读取中" in t and "未检查" not in t for t in tiles)
    # 仓库读失败是「读取失败」;仓库根本没配(后端回 available:false,api() 也把 reason 记成 error)是「未检查」。
    failed = run("renderTiles();$('tiles').innerHTML", loaded(tasks_data([]),
                 "API_READS.set('/api/repos',{sequence:3,pending:false,error:'synthetic abort'});"))
    repo = next(t for t in re.findall(r'<button type="button" class="tile".*?</button>', failed, re.S) if "仓库" in t)
    assert "读取失败" in repo and "未检查" not in repo and "synthetic abort" in repo
    unset = run("renderTiles();$('tiles').innerHTML", loaded(tasks_data([]),
                "API_READS.set('/api/repos',{sequence:4,pending:false,error:'synthetic root missing'});"
                "REPOS={available:false,reason:'synthetic root missing'};"))
    repo = next(t for t in re.findall(r'<button type="button" class="tile".*?</button>', unset, re.S) if "仓库" in t)
    assert "未检查" in repo and "读取失败" not in repo


def test_the_caption_names_the_sources_that_are_still_missing():
    caption = run("renderTodo();$('review-count').innerHTML", loaded(tasks_data([]),
                  "API_READS.set('/api/repos',{sequence:1,pending:true});"
                  "API_READS.set('/api/sys',{sequence:2,pending:false,error:'synthetic disk'});"))
    assert "仓库/记忆索引仍在读取" in caption
    assert "磁盘读取失败" in caption


def test_an_unconfigured_source_is_unchecked_in_the_summary_not_failed():
    summary = run("attentionSummary().repos", loaded(tasks_data([]),
                  "API_READS.set('/api/repos',{sequence:1,pending:false,error:'synthetic root missing'});"
                  "REPOS={available:false,reason:'synthetic root missing'};"))
    assert summary == {"state": "ok", "count": 0, "unchecked": "synthetic root missing"}


# ---------- 行用主人认得的名字和说法 ----------

def test_rows_lead_with_the_known_title_and_keep_engineer_text_in_the_tooltip():
    rows = [{"name": "AcmeDaily", "sk": "ok", "info": {"title": "晨报整理"}, "lastRun": "2026-10-04 07:00:00"}]
    html = run("""renderReviewQueue([{key:'outputs',v:'tasks',src:'产物',nm:'AcmeDaily',task:'AcmeDaily',
      why:'产物 426.5h 前更新',sev:2}],{state:'loaded'});$('todod').innerHTML""", loaded(tasks_data(rows)))
    assert re.search(r"<h3>晨报整理</h3>", html)
    assert '<code class="review-id">AcmeDaily</code>' in html
    text = visible(html)
    assert "天" in text and "426.5h" not in text
    titles = re.findall(r'title="([^"]*)"', html)
    assert any("426.5h" in t for t in titles)
    assert "上次运行" in text


def test_engineer_reasons_are_reworded_and_one_exit_is_said_once():
    result = run("""[humanReason('失败 0x1').text,humanReason('读不到产物: FileNotFoundError').text,
      humanReason('产物 5.2h 前更新').text,humanReason('失败 0x80070002').text,
      (()=>{renderReviewQueue([{key:'tasks',v:'tasks',src:'任务',nm:'AcmeSync',task:'AcmeSync',why:'失败 0x1',sev:3,fix:'run'},
        {key:'outputs',v:'tasks',src:'产物',nm:'AcmeSync',task:'AcmeSync',why:'退出码 1 不在允许集 [0]',sev:3}],{state:'loaded'});
        return $('todod').innerHTML;})()]""", loaded(tasks_data([{"name": "AcmeSync", "sk": "bad", "lastRun": "2026-10-04 07:00:00"}])))
    assert result[0] == "上次运行出错（退出码 1）"
    assert result[1] == "输出文件找不到"
    assert result[2] == "输出文件 5 小时前更新"
    assert result[3] == "上次运行出错（错误码 0x80070002）"
    assert visible(result[4]).count("上次运行出错") == 1
    assert "上次失败" in visible(result[4])


# ---------- 一键处理和整行点击 ----------

def test_rows_carry_their_one_click_fix_next_to_the_detail_button():
    html = run("""renderReviewQueue([
      {key:'tasks',v:'tasks',src:'任务',nm:'AcmeSync',task:'AcmeSync',why:'失败 0x1',sev:3,fix:'run'},
      {key:'repos',v:'repos',src:'仓库',nm:'acme-tools',why:'有未推送提交',sev:2,fix:'commitpush'},
      {key:'repos',v:'repos',src:'仓库',nm:'acme-broken',why:'读不出来',sev:2,fix:null}],{state:'loaded'});
      $('todod').innerHTML""", loaded(tasks_data([{"name": "AcmeSync", "sk": "bad"}])))
    rows = re.findall(r'<article class="review-row">.*?</article>', html, re.S)
    run_row = next(r for r in rows if "AcmeSync" in r)
    assert re.search(r'<button type="button" class="fix" data-fix="run" data-arg="AcmeSync"[^>]*>跑一次</button>', run_row)
    # 按钮在「查看详情」前面,同在行尾那一格里。
    actions = run_row.split('class="review-actions"')[1]
    assert actions.index('data-fix="run"') < actions.index("data-review-open")
    repo_row = next(r for r in rows if "acme-tools" in r)
    assert ">提交并推送</button>" in repo_row and 'data-review-repo="acme-tools"' in repo_row
    assert "data-fix" not in next(r for r in rows if "acme-broken" in r)


def test_clicking_the_row_opens_it_but_clicking_its_buttons_does_not():
    result = run("""(()=>{const opened=[];
      const open={click:()=>opened.push('open')};
      const row={querySelector:s=>s==='[data-review-open]'?open:null};
      const target=(inButton)=>({closest:s=>s==='#todod .review-row'?row:
        (s.startsWith('button') && inButton)?{}:null});
      reviewClick({target:target(false)});
      reviewClick({target:target(true)});
      return opened;})()""", DOM)
    assert result == ["open"]


# ---------- 格子在本页筛清单 ----------

def test_tiles_lead_with_the_problem_count_filter_in_place_and_drop_conversations():
    rows = [{"name": "AcmeSync", "sk": "bad"}, {"name": "AcmeFine", "sk": "ok"}]
    html = run("renderTiles();$('tiles').innerHTML", loaded(tasks_data(rows),
               "API_READS.set('/api/sys',{sequence:1,pending:false,error:null});"
               "SYS={disk:{usedPct:40,free:1073741824*50,verdict:{attention:false,tone:'ok'}}};"
               "CONVOS={available:true,summary:{files:9,humanish:3,groups:2}};"))
    tiles = re.findall(r'<button type="button" class="tile".*?</button>', html, re.S)
    assert "对话" not in html and "data-goto" not in html
    task = next(t for t in tiles if "任务" in t)
    assert 'data-review-filter="tasks"' in task
    assert re.search(r'<span class="v bad">1</span>', task) and "失败 / 2 个任务" in task
    # 判坏的排在最前,正常的其次,还在读的、没在查的排在最后。
    kinds = ["bad" if 'class="v bad"' in t else "pending" if "读取中" in t else "unchecked" if "未检查" in t else "ok"
             for t in tiles]
    assert kinds == ["bad", "ok", "pending", "pending", "unchecked"], kinds
    assert "任务" in tiles[0]
    disk = next(t for t in tiles if "磁盘" in t)
    assert 'data-review-filter="storage"' in disk


def test_a_tile_click_filters_the_list_on_this_page():
    result = run("""(()=>{const el={dataset:{reviewFilter:'repos'}};
      reviewClick({target:{closest:s=>s==='[data-review-filter]'?el:null}});
      return [REVIEW_FILTER,$('review-filter').value,document.activeElement===$('todo'),$('review-count').innerHTML];})()""",
                 loaded(tasks_data([])))
    assert result[:3] == ["repos", "repos", True]
    assert "仅显示：仓库" in result[3]


# ---------- 记住范围,不记搜索词 ----------

def test_the_scope_filter_is_remembered_but_the_search_text_is_not():
    result = run("""(()=>{STORE['tc.review-filter']='repos';startDiagnostics();
      REVIEW_QUERY='acme';renderTodo();
      applyReviewFilter('storage');
      // 只是重画不算人选了范围:下钻设的范围要能只管这一趟(见 test_ux_drill_filters.py)。
      REVIEW_FILTER='tasks';renderTodo();
      return [$('review-filter').value,STORE['tc.review-filter'],Object.values(STORE).includes('acme')];})()""",
                 loaded(tasks_data([])))
    assert result == ["storage", "storage", False]
    denied = run("""(()=>{localStorage.getItem=()=>{throw new Error('denied')};localStorage.setItem=()=>{throw new Error('denied')};
      startDiagnostics();applyReviewFilter('repos');return [REVIEW_FILTER,$('review-count').innerHTML];})()""",
                 loaded(tasks_data([])))
    assert denied[0] == "repos" and "仅显示：仓库" in denied[1]


# ---------- 数据来源自检默认收起 ----------

SCK_ROWS = [{"key": "component_status", "title": "Component observations", "state": "ok", "path": "C:/Synthetic/status.json",
             "ageHours": 224.8, "entries": 3, "required": False},
            {"key": "health", "title": "健康声明清单", "state": "ok", "path": "C:/Synthetic/health.json", "ageHours": 2,
             "required": True}]


def test_the_data_source_panel_starts_folded_when_everything_reads_and_opens_on_trouble():
    ok = {"rows": SCK_ROWS, "counts": {"ok": 2}, "broken": [], "ok": True, "probed": 2, "total": 2}
    closed = run("SCK=" + json.dumps(ok) + ";startDiagnostics();renderSelfcheck();[$('sckd').className,$('sckd').innerHTML]", DOM)
    assert "on" not in closed[0].split()
    # 友好名字和天数,路径只在悬停里。
    assert "组件运行观察" in closed[1] and "Component observations" not in closed[1]
    assert "9 天前更新" in visible(closed[1]) and "224.8" not in closed[1]
    assert "C:/Synthetic/status.json" not in visible(closed[1]) and 'title="C:/Synthetic/status.json"' in closed[1]
    bad_rows = SCK_ROWS + [{"key": "repos", "title": "仓库根目录", "state": "missing", "why": "synthetic gone", "required": True}]
    bad = {"rows": bad_rows, "counts": {"ok": 2, "missing": 1}, "broken": ["repos"], "ok": False, "probed": 2, "total": 3}
    opened = run("SCK=" + json.dumps(bad) + ";startDiagnostics();renderSelfcheck();[$('sckd').className,$('scksum').innerHTML]", DOM)
    assert "on" in opened[0].split() and "读不到 仓库根目录" in opened[1]
    # 正常时人点开过一次,下次还是开着。
    remembered = run("SCK=" + json.dumps(ok) + ";STORE['tc.sck']='open';startDiagnostics();renderSelfcheck();$('sckd').className", DOM)
    assert "on" in remembered.split()


def test_the_data_line_is_a_label_value_grid():
    data = tasks_data([], runlog={"available": True, "count": 279921, "countScope": "库里全部"},
                      history={"available": True, "days": ["2026-10-01", "2026-10-02"], "ingest": {"state": "ok"}})
    html = run("renderDataLine();$('s-src').innerHTML", loaded(data))
    assert html.startswith('<dl class="data-facts">')
    labels = re.findall(r"<dt>([^<]+)</dt>", html)
    assert labels == ["数据时间", "观察天数", "运行日志条数"]
    assert "279,921 条" in html and "2 天" in html


# ---------- 灯板与指路 ----------

def fresh_data():
    tasks = [{"name": "AcmeSync", "state": "down", "reasons": ["退出码 1 不在允许集 [0]"]},
             {"name": "AcmeFine", "state": "up", "reasons": []}]
    return tasks_data([], freshness={"tasks": tasks, "summary": {"counts": {"down": 1, "up": 1}, "total": 2, "judged": 2,
                                                                  "coverage": 1, "attention": 1}})


def test_the_freshness_footer_links_to_the_list_instead_of_pointing_up():
    html = run("renderFresh();[$('frlist').innerHTML,$('frkey').innerHTML]", loaded(fresh_data()))
    assert "上方" not in html[0]
    assert re.search(r'<button type="button" class="link-button" data-review-filter="tasks">「技术问题」列表</button>', html[0])
    # 失败那一项图例是按钮(筛清单),正常那一项只是图例。
    assert re.search(r'<button[^>]*data-review-filter="tasks"[^>]*><i class="fr-c s-down"></i>失败 1</button>', html[1])
    assert '<span><i class="fr-c s-up"></i>正常 1</span>' in html[1]


def test_failed_never_run_and_unknown_squares_carry_a_symbol():
    css = (STATIC / "page-diagnostics.css").read_text(encoding="utf-8")
    for state, symbol in (("down", "×"), ("never", "!"), ("unknown", "?")):
        assert re.search(r"\.fr-c\.s-" + state + r'::after\{content:"' + re.escape(symbol) + '"', css), state


def test_no_trapped_scroll_box_around_the_problem_list():
    css = (STATIC / "page-diagnostics.css").read_text(encoding="utf-8")
    assert re.search(r"#diagnostics \.diagnostic-layout #todod\{max-height:none;overflow:visible\}", css)
    assert re.search(r"#diagnostics \.diagnostic-layout>#todo\{position:static\}", css)
    phone = css[css.index("@media(max-width:767px)"):]
    assert "grid-column:3;grid-row:1" in phone


# ---------- 历史检查结果的行名 ----------

def test_heat_rows_show_the_known_title_and_open_the_task():
    rows = [{"name": "AcmeDaily", "info": {"title": "晨报整理"},
             "hist": {"health": 50, "byDay": {"2026-10-01": {"n": 2, "neutral": 0, "ok": 1, "bad": 1, "stale": 0}}}}]
    data = tasks_data([], history={"available": True, "days": ["2026-10-01"], "ingest": {"state": "ok"}})
    html = run("renderHeat();$('heat').innerHTML", loaded(data, rows=rows))
    th = re.search(r'<th class="tn" data-task="AcmeDaily" title="([^"]*)">(.*?)</th>', html)
    assert th, html
    assert "AcmeDaily" in th.group(1) and visible(th.group(2)).strip() == "晨报整理"
    assert "<button" in th.group(2)
