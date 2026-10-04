"""跨屏的跳转:正文里换屏的链接算下钻(目的屏给「← 返回」、Esc 回得去),下钻顺手设的筛选只管这一趟。

数据全部是合成的(AcmeCorp 之类)。行为用 node:vm 台架直接调 navigation.js / workbench.js / review.js /
integrations.js 里的函数。链接的属性从真的渲染结果(renderAttentionStrip、renderPlatformSignals、console.html)
里解析出来,再拿 navigation.js 里的 DRILL_TARGETS 去比:改了标记或改了登记表,两边对不上就红。
"""
import html
import json
import re
from pathlib import Path

from test_keyboard_conventions import tasks_setup
from test_operations_ui import run
from test_shell import SHOW
from test_ux_diagnostics import DOM, loaded, tasks_data

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "scripts" / "task_console" / "console.html"

# 假元素:closest 只看自己,按属性选择器比。认得 [a]、[a="v"]、[a^="v"] 和 :not(...),
# 认不得的选择器直接抛错,免得一条没比的选择器被当成「不匹配」悄悄放过。
FAKE = r"""
function attrMatch(attrs,part){
  const nots=[];
  const rest=part.replace(/:not\((\[[^\]]*\])\)/g,(m,inner)=>{nots.push(inner);return '';});
  const conds=[...rest.matchAll(/\[([\w-]+)(?:(\^?=)"([^"]*)")?\]/g)];
  if(!conds.length || conds.map(c=>c[0]).join('')!==rest) throw new Error('unsupported selector '+part);
  const ok=([_,name,op,value])=>Object.hasOwn(attrs,name) && (!op || (op==='='?attrs[name]===value:String(attrs[name]).startsWith(value)));
  return conds.every(ok) && !nots.some(inner=>attrMatch(attrs,inner));
}
function el(attrs){
  const node={attrs,dataset:{},disabled:false};
  for(const [key,value] of Object.entries(attrs)) if(key.startsWith('data-'))
    node.dataset[key.slice(5).replace(/-([a-z])/g,(m,c)=>c.toUpperCase())]=value;
  node.closest=selector=>selector.split(',').some(part=>attrMatch(attrs,part.trim()))?node:null;
  return node;
}
const click=target=>({target,preventDefault(){},ctrlKey:false,metaKey:false,shiftKey:false,altKey:false});
"""


def anchors(markup):
    """把一段 HTML 里每个 <a> 的属性解析成 dict(值已反转义)。"""
    out = []
    for tag in re.findall(r"<a\b([^>]*)>", markup):
        attrs = {name: html.unescape(value or "") for name, value in re.findall(r'([\w-]+)(?:="([^"]*)")?', tag)}
        out.append(attrs)
    return out


STRIP = "attentionSummary=()=>({tasks:{state:'ok',count:14},disk:{state:'ok',count:1,usedPct:96}});"


def test_every_view_changing_link_in_the_content_is_a_drill_and_the_tabs_are_not():
    rendered = run("""(()=>{renderAttentionStrip();const strip=$('attention-strip').innerHTML;
      // 页底那一个「查看技术问题 →」只在顶上的摘要藏起来时出现:摘要全是零时才画它。
      attentionSummary=()=>({tasks:{state:'ok',count:0},disk:{state:'ok',count:0}});renderPlatformSignals();
      showView('tasks',false);
      return {strip,signals:$('platform-sources').innerHTML,tabs:$('view-tabs').innerHTML};})()""",
                   tasks_setup(SHOW + STRIP))
    strip = anchors(rendered["strip"])
    signals = [a for a in anchors(rendered["signals"]) if a.get("href", "").startswith("#")]
    progress = [a for a in anchors(PAGE.read_text(encoding="utf-8")) if a.get("href") == "#pipelines"]
    tabs = anchors(rendered["tabs"])
    # 芯片两枚 + 「全部技术问题 →」;页底那一个「查看技术问题 →」;诊断页的「查看进度」。
    assert len(strip) == 3 and len(signals) == 1 and len(progress) == 1 and len(tabs) >= 2
    links = strip + signals + progress
    matched = run(f"(()=>{{const links={json.dumps(links)},tabs={json.dumps(tabs)};"
                  "return {links:links.map(a=>!!el(a).closest(DRILL_TARGETS)),tabs:tabs.map(a=>!!el(a).closest(DRILL_TARGETS))};})()",
                  FAKE)
    assert matched["links"] == [True] * len(links)
    assert matched["tabs"] == [False] * len(tabs)


def test_an_attention_chip_and_the_all_problems_link_give_diagnostics_a_way_back():
    result = run("""(()=>{
      renderAttentionStrip();
      const parse=html=>[...html.matchAll(/<a\\b([^>]*)>/g)].map(m=>Object.fromEntries([...m[1].matchAll(/([\\w-]+)(?:="([^"]*)")?/g)].map(x=>[x[1],x[2] ?? ''])));
      const [chip,,all]=parse($('attention-strip').innerHTML);
      const out={};
      for(const [name,attrs] of [['chip',chip],['all',all]]){
        showView('overview',false,{reset:true});
        const target=el(attrs);markDrill(target);routeWorkClick(click(target));
        const origin=JSON.parse(JSON.stringify(VIEW_ORIGIN));
        const chipHtml=$('view-tabs').innerHTML, hidden=$('view-tabs').hidden;
        const event=key('Escape');handleEscape(event);
        out[name]={origin,back:chipHtml.includes('← 返回 工作台'),hidden,afterEsc:CURVIEW,prevented:event.prevented};
      }
      return out;
    })()""", tasks_setup(SHOW + STRIP + FAKE))
    for name in ("chip", "all"):
        assert result[name]["origin"] == {"view": "overview", "to": "diagnostics"}, name
        assert result[name]["back"] is True and result[name]["hidden"] is False, name
        assert result[name]["afterEsc"] == "overview" and result[name]["prevented"] is True, name


STORE = """const STORE={};localStorage.getItem=k=>Object.hasOwn(STORE,k)?STORE[k]:null;localStorage.setItem=(k,v)=>{STORE[k]=String(v);};"""


def test_a_chip_scopes_diagnostics_for_one_visit_without_overwriting_the_saved_scope():
    result = run("""(()=>{
      STORE['tc.review-filter']='tasks';
      const listeners={};$('review-filter').addEventListener=(type,fn)=>{listeners[type]=fn;};
      startDiagnostics();
      const chip=el({href:'#diagnostics','data-attention-filter':'storage'}), out={};
      const drill=()=>{showView('overview',false,{reset:true});markDrill(chip);routeWorkClick(click(chip));};
      drill();
      out.drilled=[CURVIEW,REVIEW_FILTER,$('review-filter').value,STORE['tc.review-filter']];
      // 从侧栏离开再回来:回到人自己存的那个范围。
      showView('overview',true,{reset:true});showView('diagnostics',true,{reset:true});
      out.sidebar=[REVIEW_FILTER,$('review-filter').value,STORE['tc.review-filter']];
      // 从诊断再往下钻、Esc 回来:这一趟还没完,范围不变。
      drill();
      markDrill(el({'data-task':'AcmeSync'}));showView('tasks',true);
      out.deeper=REVIEW_FILTER;
      handleEscape(key('Escape'));
      out.back=[CURVIEW,REVIEW_FILTER];
      // 来源只用一次,这之后从侧栏离开:这一趟结束。
      showView('overview',true,{reset:true});
      out.home=[CURVIEW,REVIEW_FILTER,STORE['tc.review-filter']];
      // 人自己在下拉框里选的才记。
      drill();
      listeners.change({target:{value:'repos'}});
      showView('overview',true,{reset:true});
      out.chosen=[REVIEW_FILTER,STORE['tc.review-filter']];
      return out;
    })()""", tasks_setup(SHOW + DOM + FAKE))
    assert result["drilled"] == ["diagnostics", "storage", "storage", "tasks"]
    assert result["sidebar"] == ["tasks", "tasks", "tasks"]
    assert result["deeper"] == "storage"
    assert result["back"] == ["diagnostics", "storage"]
    assert result["home"] == ["overview", "tasks", "tasks"]
    assert result["chosen"] == ["repos", "repos"]



def test_each_attention_chip_opens_a_list_with_exactly_as_many_rows_as_its_number():
    # 合成数据专挑会让两边对不上的形状:一个任务既失败又有两项输出过期,另一个任务三项输出过期,
    # 摄入停摆,外加一个要处理的仓库。以前三枚芯片都落在「任务与产物」上,清单是 4 行,芯片写 2、5。
    rows = [{"name": "AcmeSync", "sk": "bad", "sl": "失败 0x1", "issues": []},
            {"name": "AcmeOther", "sk": "bad", "sl": "失败 0x1", "issues": []},
            {"name": "AcmeDaily", "sk": "ok", "sl": "正常", "issues": []}]
    fresh = [{"name": "AcmeSync", "check": "report", "state": "down", "reasons": ["产物过期 30h"]},
             {"name": "AcmeSync", "check": "digest", "state": "never", "reasons": []},
             {"name": "AcmeDaily", "check": "a", "state": "down", "reasons": []},
             {"name": "AcmeDaily", "check": "b", "state": "down", "reasons": []},
             {"name": "AcmeDaily", "check": "c", "state": "unknown", "reasons": []}]
    data = tasks_data(rows, freshness={"tasks": fresh, "summary": {"total": 5, "attention": 5}},
                      history={"ingest": {"state": "stale", "why": "synthetic stale ingest"}})
    result = run(r"""(()=>{
      API_READS.set('/api/tasks',{sequence:1,pending:false,error:null});
      API_READS.set('/api/repos',{sequence:2,pending:false,error:null});
      API_READS.set('/api/sys',{sequence:3,pending:true});API_READS.set('/api/mem',{sequence:4,pending:true});
      REPOS={available:true,repos:[{name:'acme-repo',state:'dirty'}],summary:{}};
      startDiagnostics();renderAttentionStrip();
      const chips=[...$('attention-strip').innerHTML.matchAll(/data-attention-filter="([^"]+)"[^>]*>(.*?)<\/a>/g)]
        .map(m=>({filter:m[1],text:m[2].replace(/<[^>]*>/g,'')}));
      return chips.map(chip=>{
        openDiagnosticsFiltered(chip.filter);
        const rows=[...$('todod').innerHTML.matchAll(/<article class="review-row">[\s\S]*?<h3>([^<]*)<\/h3>/g)].map(m=>m[1]);
        return {...chip,rows,caption:$('review-count').innerHTML};
      });
    })()""", tasks_setup(SHOW + loaded(data)))
    by = {chip["filter"]: chip for chip in result}
    assert set(by) == {"failed", "outputs", "ingest", "repos"}, result
    for chip in result:
        number = re.match(r"\D*(\d+)", chip["text"])
        if number:
            assert int(number.group(1)) == len(chip["rows"]), chip
    assert "2 个任务失败" in by["failed"]["text"] and len(by["failed"]["rows"]) == 2
    assert "2 个任务输出过期" in by["outputs"]["text"] and len(by["outputs"]["rows"]) == 2
    assert by["ingest"]["rows"] == ["运行日志摄入"]
    assert "仅显示：任务失败" in by["failed"]["caption"] and "显示 2 / 共 5 个对象" in by["failed"]["caption"]
    # 下拉框里有这几个范围,下钻设的范围才看得见、也才能被人自己换回去。
    page = PAGE.read_text(encoding="utf-8")
    for scope in ("failed", "outputs", "ingest"):
        assert f'<option value="{scope}">' in page, scope

def test_overview_arrows_and_integration_links_filter_work_for_one_visit():
    work = {"available": True, "items": [], "events": [], "sources": [], "coverage": {"total": 0, "returned": 0},
            "observed_at": "2030-01-02T00:00:00Z"}
    result = run("""(()=>{
      STORE['tc.work.filters']=JSON.stringify({role:'tracked_item',state:'',source:''});
      restoreWorkFilters();
      const clicks=[];const add=document.addEventListener;document.addEventListener=(type,fn)=>{if(type==='click') clicks.push(fn);};
      startIntegrations();document.addEventListener=add;
      const out={};
      showView('overview',false,{reset:true});
      const arrow=el({'data-work-filter':'agent_work','data-work-state':'failed'});markDrill(arrow);routeWorkClick(click(arrow));
      out.arrow=[CURVIEW,WORK_ROLE,WORK_STATE,STORE['tc.work.filters']];
      showView('overview',true,{reset:true});
      out.left=[WORK_ROLE,WORK_STATE,WORK_SOURCE,$('work-role').value];
      showView('integrations',false,{reset:true});
      const open=el({'data-integration-open':'work','data-integration-source':'acme-source'});markDrill(open);
      for(const fn of clicks) fn(click(open));
      out.integration=[CURVIEW,WORK_ROLE,WORK_SOURCE,STORE['tc.work.filters']];
      handleEscape(key('Escape'));
      out.back=[CURVIEW,WORK_ROLE,WORK_SOURCE];
      // 人自己在工作页上选的照旧记下。
      setWorkFilters({role:'all',source:'acme-source'});
      showView('overview',true,{reset:true});
      out.chosen=[WORK_ROLE,WORK_SOURCE,JSON.parse(STORE['tc.work.filters']).source];
      return out;
    })()""", tasks_setup(SHOW + STORE + FAKE + "WORK=" + json.dumps(work) + ";"))
    saved = json.dumps({"role": "tracked_item", "state": "", "source": ""}, separators=(",", ":"))
    assert result["arrow"] == ["work", "agent_work", "failed", saved]
    assert result["left"] == ["tracked_item", "", "", "tracked_item"]
    assert result["integration"] == ["work", "all", "acme-source", saved]
    assert result["back"] == ["integrations", "tracked_item", ""]
    assert result["chosen"] == ["all", "acme-source", "acme-source"]
