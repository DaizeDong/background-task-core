"""资源区四页(控制台接入、技能与记忆、代码仓库、存储清理)的交互约定。只用合成数据,在 node:vm 台架里跑真实模块。"""
import json
import re
from pathlib import Path

from test_operations_ui import run
from tools.make_fixtures import catalog_snapshot
import component_status


def visible(html):
    """去掉 title 属性之后剩下的,就是不悬停也看得见的字。"""
    return re.sub(r'title="[^"]*"', "", html)


# ---------- 技能、插件、记忆 ----------

PLUGINS = {"plugins": {"available": True, "plugins": [
    {"name": "acme-plugin@acme-market", "enabled": True, "scope": "user", "removal": None, "removalReason": ""},
    {"name": "sample-plugin@acme-market", "enabled": False, "scope": "user", "removal": None, "removalReason": ""},
    {"name": "acme-project@acme-market", "enabled": True, "scope": "project", "removal": None, "removalReason": "请在所属项目管理"},
    {"name": "acme-unknown@acme-market", "enabled": None, "scope": "user", "removal": None, "removalReason": ""}]}}


def plugin_rows():
    html = run("renderPlugins();$('mt-plugins').innerHTML", "MAINT=" + json.dumps(PLUGINS) + ";")
    return html, {name: row for row in html.split('<div class="mt-r plugin-row')[1:]
                  for name in [re.search(r'class="n" title="([^"]+)"', row).group(1)]}


def test_plugin_state_is_a_switch_and_unknown_is_not_drawn_as_off():
    html, rows = plugin_rows()
    assert 'role="switch" aria-checked="true"' in rows["acme-plugin@acme-market"]
    assert 'data-mt="plugin.disable"' in rows["acme-plugin@acme-market"]
    assert 'role="switch" aria-checked="false"' in rows["sample-plugin@acme-market"]
    assert 'data-mt="plugin.enable"' in rows["sample-plugin@acme-market"]
    unknown = rows["acme-unknown@acme-market"]
    assert 'role="switch"' not in unknown and 'class="plugin-unknown"' in unknown and ">?<" in unknown
    # 项目范围的插件:开关照画当前状态,但灰着并说明原因;范围只在不是 user 时写出来。
    managed = rows["acme-project@acme-market"]
    assert 'aria-checked="true"' in managed and " disabled" in managed and "请在所属项目管理" in managed
    assert '<span class="sub">project</span>' in managed
    assert ">user<" not in html
    assert "当前匹配" not in html and "显示 4 / 共 4 个" in html


SKILLS = {"skills": {"available": True, "budgetChars": 12345, "budgetLimit": 18600, "budgetPct": 66.4,
                     "verdict": {"tone": "ok"}, "descUnreadable": 0, "skills": [
                         {"name": "acme-skill", "chars": 1250, "linked": True, "archived": False},
                         {"name": "sample-skill", "chars": 0, "linked": False, "archived": True}]}}


def test_skill_rows_use_units_words_and_keep_archive_apart_from_delete():
    html = run("renderSkills();$('mt-skills').innerHTML", "MAINT=" + json.dumps(SKILLS) + ";")
    assert "1,250 字" in html and "已归档" in html
    assert "↗" not in html and ">联接<" in html
    assert 'role="meter"' in html and "描述预算" in html and "12,345 / 18,600 字" in html
    archive = re.search(r'<button[^>]*data-mt="skill.archive"', html).group(0)
    assert "danger" not in archive
    assert re.search(r'<button class="icon-only mini danger" data-delete="skill"', html)
    assert html.count('class="mt-acts"') == 2


MEMORY = {"available": True, "live": 40, "cold": 6, "linePct": 50, "indexLines": 100, "hardLines": 200,
          "lineVerdict": {"tone": "ok"}, "bytePct": 40, "indexBytes": 10000, "hardBytes": 25000,
          "byteVerdict": {"tone": "ok"}, "orphanLinks": [], "danglingIndex": [],
          "unreachable": [f"acme_note_{i:02d}" for i in range(15)], "biggest": [], "archiverConfigured": False}


def test_memory_groups_show_twelve_copyable_chips_then_expand():
    setup = "MEM=" + json.dumps(MEMORY) + ";"
    html = run("renderMem();$('mt-memory').innerHTML", setup)
    assert html.count('data-mem-copy="acme_note_') == 12
    assert "展开全部 15" in html and 'aria-expanded="false"' in html
    assert 'data-mem-copy-all="unreachable"' in html and "复制全部名单" in html
    opened = run("MEM_OPEN.add('unreachable');renderMem();$('mt-memory').innerHTML", setup)
    assert opened.count('data-mem-copy="acme_note_') == 15 and "收起" in opened
    # 点一个名字复制它;复制不了时把名字放进提示条,不静默失败。
    copied = run("""(()=>{const said=[];toast=(text,tone)=>said.push([text,tone]);let handler;
      $('mt-memory').addEventListener=(type,fn)=>handler=fn;$('mt-memory').querySelector=()=>null;startMemory();
      handler({target:{closest:sel=>sel==='[data-mem-copy]'?{dataset:{memCopy:'acme_note_03'}}:null}});return said;})()""", setup)
    assert copied == [["无法访问剪贴板，内容是：acme_note_03", "warn"]]


def test_anchor_bar_shows_four_section_counts_in_their_own_states():
    setup = """const anchors={};
document.querySelector=sel=>{const m=/data-goto="([^"]+)"/.exec(sel);return m?(anchors[m[1]] ||= {innerHTML:''}):{content:'synthetic'};};
"""
    loading = run("renderResourceAnchors();Object.fromEntries(Object.entries(anchors).map(([k,v])=>[k,v.innerHTML]))", setup)
    assert set(loading) == {"#mt-skills", "#mt-plugins", "#membox", "#catalog-box", "#catalog-health"}
    assert all("count-loading" in html for html in loading.values())
    # 每个画了数的锚点在页面上都真有一个按钮:少了按钮,数照算,人却点不到。
    page = (Path(__file__).resolve().parents[1] / "scripts" / "task_console" / "console.html").read_text(encoding="utf-8")
    bar = re.search(r'<nav class="resources-anchors".*?</nav>', page, re.S).group(0)
    assert set(re.findall(r'data-goto="([^"]+)"', bar)) == set(loading)
    catalog = component_status.catalog_view(catalog_snapshot())
    ready = run("renderResourceAnchors();Object.fromEntries(Object.entries(anchors).map(([k,v])=>[k,v.innerHTML]))",
                setup + "MAINT=" + json.dumps({**SKILLS, **PLUGINS}) + ";MEM=" + json.dumps(MEMORY)
                + ";COMPONENTS=" + json.dumps({"available": True, "catalog": catalog, "coverage": {"checked": 41, "expected": 49},
                                               "tasks": [{"verdict": "unhealthy"}, {"verdict": "healthy"}, {"verdict": "unknown"}]}) + ";")
    assert ready["#mt-skills"].startswith("技能 ") and '<span class="count-cell">2</span>' in ready["#mt-skills"]
    assert '<span class="count-cell">4</span>' in ready["#mt-plugins"]
    assert '<span class="count-cell">46</span>' in ready["#membox"]
    assert '<span class="count-cell">3</span>' in ready["#catalog-box"]
    # 检查结果排在三百多行的目录下面,页顶要有一个锚点直接跳过去,数字和那一块的标题一样是「已查/应查」。
    assert ready["#catalog-health"].startswith("检查结果 ") and ">41/49</span>" in ready["#catalog-health"]
    assert "异常 1 · 未检查 1" in ready["#catalog-health"]
    broken = run("renderResourceAnchors();anchors['#mt-skills'].innerHTML", setup + "MAINT_ERROR='HTTP 500';")
    assert "count-broken" in broken


def test_a_failed_components_read_marks_catalog_and_checks_broken_not_unchecked():
    setup = """const anchors={};
document.querySelector=sel=>{const m=/data-goto="([^"]+)"/.exec(sel);return m?(anchors[m[1]] ||= {innerHTML:''}):{content:'synthetic'};};
"""
    # 走真实的 loadComponents:接口抛错时,目录和检查结果两个锚点都要画「!」,不能是「没配置」的占位符。
    failed = run("""(async()=>{api=async()=>{throw new Error('HTTP 500');};renderPipelines=()=>{};
      await loadComponents();renderResourceAnchors();return [anchors['#catalog-box'].innerHTML,anchors['#catalog-health'].innerHTML];})()""", setup)
    assert all("count-broken" in html and "count-unchecked" not in html for html in failed)
    # 读到了、只是没有配置目录:这才是「未检查」。
    unset = run("renderResourceAnchors();[anchors['#catalog-box'].innerHTML,anchors['#catalog-health'].innerHTML]",
                setup + "COMPONENTS={available:false,reason:'not configured',tasks:[]};")
    assert all("count-unchecked" in html and "count-broken" not in html for html in unset)


# ---------- 资源目录与检查结果 ----------

def catalog_case():
    catalog = component_status.catalog_view(catalog_snapshot())
    dims = component_status.DIMENSIONS
    catalog["records"][0]["status"].update({key: "yes" for key in dims})
    catalog["records"][1]["status"].update({key: "yes" for key in dims}, installed="no")
    catalog["records"][2]["status"].update({key: "yes" for key in dims}, compatible="unknown")
    return catalog


def test_catalog_rows_summarise_seven_checks_and_never_call_unknown_normal():
    catalog = catalog_case()
    setup = "COMPONENTS=" + json.dumps({"available": True, "tasks": [], "catalog": catalog}) + ";"
    summary = run("COMPONENTS.catalog.records.map(row=>catalogSummary(row.status).text)", setup)
    assert summary == ["正常", "有问题：未安装", "未全查 6/7"]
    assert run("catalogSummary({}).text", setup) == "未全查 0/7"
    html = run("renderCatalog();$('catalog-results').innerHTML", setup)
    # 有问题的在前,没查全的其次,正常的最后。
    order = re.findall(r'data-catalog-toggle="([^"]+)"', html)
    assert order == ["synthetic:acme-plugin@acme-market", "synthetic:acme-connector", "synthetic:acme-skill"]
    # 收着的时候没有 ↳ 入口行,也没有七项检查格;展开那一行才有。
    assert "↳" not in html and "catalog-dims" not in html and 'aria-expanded="false"' in html
    opened = run("CATALOG_OPEN.add('synthetic:acme-skill');renderCatalog();$('catalog-results').innerHTML", setup)
    assert opened.count("↳") == 1 and opened.count('class="catalog-dim"') == 7 + 1
    assert 'aria-expanded="true"' in opened
    assert run("renderCatalog();$('catalog-count').textContent", setup) == "显示 3 / 共 3 项"
    # 表头可点排序:按名称排。
    by_name = run("CATALOG_SORT={key:'name',asc:true};renderCatalogResults();$('catalog-results').innerHTML", setup)
    assert re.findall(r'data-catalog-toggle="([^"]+)"', by_name)[0] == "synthetic:acme-connector"
    assert 'aria-sort="ascending"' in by_name


def test_long_catalog_lists_its_first_rows_and_expands_and_the_checks_have_an_anchor_target():
    catalog = catalog_case()
    base = catalog["records"][0]
    catalog["records"] = [dict(base, source_id=f"synthetic:acme-extra-{i:02d}", registry_key=f"acme-extra-{i:02d}") for i in range(30)]
    setup = "COMPONENTS=" + json.dumps({"available": True, "tasks": [], "catalog": catalog}) + ";"
    html = run("renderCatalog();$('catalog-results').innerHTML", setup)
    assert html.count("data-catalog-toggle=") == 25
    assert re.search(r'data-catalog-more aria-expanded="false">展开全部 30<', html)
    assert run("renderCatalog();$('catalog-count').textContent", setup) == "显示 25 / 共 30 项"
    opened = run("CATALOG_ALL=true;renderCatalogResults();$('catalog-results').innerHTML", setup)
    assert opened.count("data-catalog-toggle=") == 30 and 'aria-expanded="true">收起' in opened
    # 点「展开全部」走 #mt-catalog 上的那个监听。
    clicked = run("""(()=>{let handler;$('mt-catalog').addEventListener=(type,fn)=>{if(type==='click') handler=fn;};
      $('catalog-results').querySelector=()=>null;startResources();
      handler({target:{closest:sel=>sel==='[data-catalog-more]'?{}:null}});return [CATALOG_ALL,$('catalog-results').innerHTML.split('data-catalog-toggle=').length-1];})()""", setup)
    assert clicked == [True, 30]
    # 页顶「检查结果」锚点跳到的那个标题。
    assert 'class="catalog-heading health-heading" id="catalog-health"' in run("renderCatalog();$('mt-catalog').innerHTML", setup)


def test_catalog_groups_system_internals_last():
    catalog = catalog_case()
    catalog["records"][0]["registry_key"] = ".system/acme-internal"
    setup = "COMPONENTS=" + json.dumps({"available": True, "tasks": [], "catalog": catalog}) + ";"
    html = run("renderCatalog();$('catalog-results').innerHTML", setup)
    assert '<tbody class="catalog-system">' in html
    assert html.index(".system/acme-internal") > html.index('<tbody class="catalog-system">')


def test_health_reason_codes_are_translated_and_raw_codes_stay_in_the_title():
    tasks = [{"name": "AcmeSync", "task_id": "synthetic/acme", "verdict": "unknown", "run_id": None,
              "reason_codes": ["missing_workload_observer", "stale", "synthetic_reader_failed"],
              "execution": {"state": "unknown"}, "checks": []}]
    setup = "COMPONENTS=" + json.dumps({"available": True, "tasks": tasks, "catalog": catalog_case()}) + ";"
    html = run("renderCatalog();$('health-results').innerHTML", setup)
    shown = visible(html)
    for code in ("missing_workload_observer", "synthetic_reader_failed", ">stale<"):
        assert code not in shown
    assert "缺少观测器" in shown and "记录过期" in shown and "某一步失败" in shown
    assert "原始代号：missing_workload_observer" in html
    assert "没有运行编号" in shown and "运行 未提供" not in shown
    # 来源覆盖压成一枚芯片。
    tools = run("renderCatalog();$('mt-catalog').innerHTML", setup)
    assert re.search(r'class="coverage-chip"[^>]*>.*来源覆盖：部分检查 3/4', tools)


# ---------- 代码仓库 ----------

def repos_case():
    def repo(name, state, owner="AcmeCorp", **extra):
        row = {"name": name, "state": state, "owner": owner, "kind": "other", "path": "C:/Acme/" + name,
               "remote": "git@example.com:AcmeCorp/" + name + ".git", "visibility": "PRIVATE",
               "dirty": 0, "ahead": 0, "unpushedKnown": True, "behindKnown": True, "identity": {"state": "ok"}}
        row.update(extra)
        return row
    return {"available": True, "summary": {"total": 3, "counts": {"unpushed": 2, "clean": 1}, "kindOrder": ["other"]},
            "repos": [repo("acme-tools", "unpushed", ahead=2), repo("sample-notes", "unpushed", ahead=1, dirty=3),
                      repo("acme-quiet", "clean", unpushedKnown=False, path="C:/Acme/vault/acme-quiet")]}


REPO_SETUP = "REPOS=" + json.dumps(repos_case()) + ";rpLoadStatus=async()=>{};"


def test_unpushed_filter_offers_one_review_for_every_listed_repo():
    html = run("RP_STATE='unpushed';renderRepos();$('rplist').innerHTML", REPO_SETUP)
    button = re.search(r'<button[^>]*data-fixall="commitpush"[^>]*>([^<]*)</button>', html)
    assert button and "审阅并推送这 2 个仓库" in button.group(1)
    names = re.search(r'data-args="([^"]*)"', button.group(0)).group(1).split("\u0001")
    assert names == ["acme-tools", "sample-notes"]
    assert "data-fixall" not in run("RP_STATE='';renderRepos();$('rplist').innerHTML", REPO_SETUP)


def test_state_filter_shows_a_removable_chip_and_segments_are_pressed_buttons():
    result = run("RP_STATE='unpushed';renderRepos();[$('rpstate-chip').innerHTML,$('rphead').innerHTML,$('rphit').textContent]", REPO_SETUP)
    chip, head, hit = result
    assert 'data-rpstate="unpushed"' in chip and "状态：未推送" in chip and "×" in chip
    assert re.search(r'<button type="button" class="d-unpushed sel" data-rpstate="unpushed"\s+aria-pressed="true"', head)
    assert re.search(r'<button type="button" class="d-clean" data-rpstate="clean"\s+aria-pressed="false"', head)
    assert "<span>未推送</span> <b>2</b>" in head and 'role="img"' not in head
    assert "状态：未推送" in hit
    assert run("RP_STATE='';renderRepos();$('rpstate-chip').innerHTML", REPO_SETUP) == ""


def test_repo_rows_hide_zero_counts_and_name_unknown_ones():
    html = run("renderRepos();$('rplist').innerHTML", REPO_SETUP)
    rows = dict(re.findall(r'data-rp="([^"]+)".*?<span class="rp-changes">([^<]*)</span>', html, re.S))
    assert rows == {"acme-tools": "2 个未推送", "sample-notes": "3 个改动 · 1 个未推送", "acme-quiet": "未推送数未检查"}
    assert "0 个" not in html
    # 全是同一个账号时不印缩写;出现第二个账号才印。
    assert ">AC<" not in html
    mixed = run("REPOS.repos[2].owner='SampleOrg';renderRepos();$('rplist').innerHTML", REPO_SETUP)
    assert ">AC<" in mixed and ">SO<" in mixed


def test_repo_search_matches_path_and_remote():
    case = repos_case()["repos"]
    setup = "const rows=" + json.dumps(case) + ";"
    assert run("rows.filter(r=>rpMatches(r,'vault','','','')).map(r=>r.name)", setup) == ["acme-quiet"]
    assert run("rows.filter(r=>rpMatches(r,'example.com:acmecorp/sample','','','')).map(r=>r.name)", setup) == ["sample-notes"]


def test_repo_actions_carry_words_and_push_is_the_primary_with_its_count():
    html = run("RP_SEL='sample-notes';renderRepos();$('rpdetail').innerHTML", REPO_SETUP)
    acts = html.split('<div class="rp-acts">', 1)[1]
    labels = re.findall(r'<span class="rp-act-t">([^<]*)</span>', acts)
    assert labels == ["打开目录", "网页", "查看改动", "获取更新", "提交并推送 4"]
    push = re.search(r'<button type="button" class="mini rp-act primary go" data-rpact="commitpush"', acts)
    assert push
    assert 'id="rpback"' in html and "← 返回列表" in html


def test_selection_is_written_into_the_address_and_read_back_from_it():
    setup = REPO_SETUP + """const calls=[];globalThis.location={hash:'#repos'};
globalThis.history={pushState:(a,b,url)=>{calls.push(['push',url]);location.hash=url;},replaceState:(a,b,url)=>{calls.push(['replace',url]);location.hash=url;}};
CURVIEW='repos';"""
    result = run("""(()=>{renderRepos();const auto=RP_SEL;
      RP_SEL='sample-notes';renderRepoList();
      const routed=repoRoute('acme-tools');
      return {auto,calls,routed,sel:RP_SEL,bare:repoRoute(null)};})()""", setup)
    # 自动选中只替换当前这一条历史;人点的选中加一条,后退就回得去。地址里带的仓名直接选中,不再另加历史。
    assert result["auto"] == "acme-tools"
    assert result["calls"] == [["replace", "#repos/acme-tools"], ["push", "#repos/sample-notes"]]
    assert result["routed"] == "repos/acme-tools" and result["sel"] == "acme-tools"
    assert result["bare"] == "repos/acme-tools"


def test_filters_and_selection_are_remembered_but_the_search_is_not():
    setup = REPO_SETUP + """const stored={};localStorage.getItem=k=>stored[k] ?? null;localStorage.setItem=(k,v)=>stored[k]=v;
$('rpissue').options=[{value:''},{value:'upstream'}];"""
    saved = run("""(()=>{$('rpq').value='acme';$('rpvis').value='priv';RP_ISSUE='upstream';RP_STATE='unpushed';RP_SEL='acme-tools';
      renderRepos();return JSON.parse(stored['tc.repos.v1']);})()""", setup)
    assert saved["vis"] == "priv" and saved["issue"] == "upstream" and saved["state"] == "unpushed" and saved["sel"] == "acme-tools"
    assert "acme" not in [saved.get("q"), saved.get("query")] and "q" not in saved
    restored = run("""(()=>{stored['tc.repos.v1']=JSON.stringify({vis:'pub',issue:'upstream',state:'clean',sel:'sample-notes',acc:'AcmeCorp',kind:'other'});
      startRepositories();renderRepos();return [$('rpvis').value,RP_ISSUE,RP_STATE,RP_SEL,$('rpacc').value,$('rpkind').value];})()""", setup)
    assert restored == ["pub", "upstream", "clean", "sample-notes", "AcmeCorp", "other"]


def test_redrawing_the_detail_keeps_focus_on_the_control_that_had_it():
    # 手机上点开一个仓,焦点在「← 返回列表」;改动列表读回来把详情整块重画,焦点要回到新画的那个按钮上。
    setup = REPO_SETUP + """const focused=[];const box=$('rpdetail');
box.querySelector=sel=>({focus:()=>focused.push(sel)});
box.contains=el=>el===document.activeElement && el.inside;"""
    back = run("RP_SEL='sample-notes';document.activeElement={id:'rpback',dataset:{},inside:true};renderRepoDetail();focused", setup)
    assert back == ["#rpback"]
    act = run("RP_SEL='sample-notes';document.activeElement={id:'',dataset:{rpact:'status'},inside:true};renderRepoDetail();focused", setup)
    assert act == ['[data-rpact="status"]']
    # 焦点不在详情里(在列表或搜索框上)时,重画不许把焦点抢过来。
    outside = run("RP_SEL='sample-notes';document.activeElement={id:'rpq',dataset:{},inside:false};renderRepoDetail();focused", setup)
    assert outside == []


def test_escape_returns_from_the_phone_detail_to_the_list_only_when_narrow():
    setup = REPO_SETUP + "let narrow=false;getComputedStyle=()=>({display:narrow?'inline-flex':'none'});CURVIEW='repos';"
    result = run("""(()=>{renderRepos();RP_SEL='acme-tools';RP_SHEET=true;
      const wide=ESC_STEPS.repos();narrow=true;const phone=ESC_STEPS.repos();
      return [wide,phone,RP_SHEET,ESC_STEPS.repos()];})()""", setup)
    assert result == [False, True, False, False]


# ---------- 存储清理 ----------

def cx_items(n=8):
    return [{"rel": f"2026/10/0{i % 9 + 1}/rollout-2026-10-0{i % 9 + 1}T0{i}-15-30-0199abc{i}-0000-7000-8000-00000000000{i}.jsonl",
             "name": f"rollout-2026-10-0{i % 9 + 1}T0{i}-15-30-0199abc{i}-0000-7000-8000-00000000000{i}.jsonl",
             "bytes": 1000 * (i + 1), "mtime": 1_700_000_000 + i} for i in range(n)]


def test_shift_click_selects_the_whole_visible_range():
    setup = "CXL=" + json.dumps({"available": True, "exists": True, "items": cx_items()}) + ";"
    result = run("""(()=>{const rels=CXL.items.map(i=>i.rel);
      cxToggle(rels[0],true,false);cxToggle(rels[4],true,true);const range=[...CXSEL];
      cxToggle(rels[2],false,true);
      return {range,after:CXSEL.size};})()""", setup)
    assert len(result["range"]) == 5
    assert result["after"] == 2


def test_cleanup_rows_are_labels_with_a_date_and_short_id_and_a_tabbable_box():
    setup = "CXL=" + json.dumps({"available": True, "exists": True, "count": 8, "bytes": 1, "items": cx_items()}) + ";$('cxsort').value='size';"
    html = run("renderCxList();$('cxbody').innerHTML", setup)
    assert html.count('<label class="cx-l') == 8
    assert 'tabindex="-1"' not in html
    assert '<span class="cx-id">0199abc3</span>' in html and "10-04 03:15" in html
    assert 'data-cx-older="30"' in html and 'data-cx-older="180"' in html and 'id="cxq"' in html
    filtered = run("CX_QUERY='0199abc3';renderCxList();$('cxbody').innerHTML", setup)
    assert len(re.findall(r'<label class="cx-l[^"]*" data-cxrel="[^"]+">', filtered)) == 1


def test_quick_select_takes_only_files_older_than_the_chosen_age():
    items = cx_items(4)
    now = 2_000_000_000
    for item, days in zip(items, (5, 40, 100, 200)):
        item["mtime"] = now - days * 86400
    setup = "CXL=" + json.dumps({"available": True, "exists": True, "items": items}) + f";Date.now=()=>{now * 1000};"
    assert run("[30,90,180].map(days=>cxOlderThan(days).length)", setup) == [3, 2, 1]


def test_system_storage_is_never_blank_and_a_full_disk_raises_a_banner():
    assert "正在读取" in run("SYS=null;renderSys();$('mt-sys').innerHTML")
    bad = {"disk": {"usedPct": 95.5, "free": 43 * 1073741824, "verdict": {"tone": "bad"}},
           "pluginCache": {"available": False, "reason": "synthetic unchecked"}, "sessions": {}}
    banner = run("SYS=CASE;renderSys();[$('storage-alert').hidden,$('storage-alert').innerHTML]", "const CASE=" + json.dumps(bad) + ";")
    assert banner[0] is False and "磁盘已用 95.5%，剩 43G" in banner[1] and 'data-goto="#cxbox"' in banner[1]
    fine = dict(bad, disk={"usedPct": 40, "free": 400 * 1073741824, "verdict": {"tone": "ok"}})
    assert run("SYS=CASE;renderSys();$('storage-alert').hidden", "const CASE=" + json.dumps(fine) + ";") is True


# ---------- 控制台接入 ----------

def integrations_case():
    def row(label, layer, state, **extra):
        data = {"label": label, "layer": layer, "provides": "synthetic", "actions": "查看", "depends_on": [],
                "view": "overview", "source": "", "endpoint": "/api/" + label, "connection": {"state": state}}
        data.update(extra)
        return data
    return {"coverage": {"work_sources": "unavailable"}, "items": [
        row("acme-ready", "adapter", "ready", connection={"state": "ready", "last_success": 1_700_000_000}),
        row("acme-unchecked", "adapter", "unchecked"),
        row("acme-broken", "adapter", "unavailable", connection={"state": "unavailable", "reason": "work_reader_failed"}),
        row("acme-core", "core", "ready")]}


def test_integrations_are_one_table_broken_first_with_translated_reasons():
    html = run("renderIntegrations();$('integration-list').innerHTML", "INTEGRATIONS=" + json.dumps(integrations_case()) + ";")
    assert html.count("<table") == 1 and html.count('class="integration-layer"') == 6
    adapters = re.findall(r'<th scope="row" data-label="接入 / 依赖">([\w-]+)', html)
    assert adapters[1:4] == ["acme-broken", "acme-unchecked", "acme-ready"]
    shown = visible(html)
    assert "work_reader_failed" not in shown and "工作服务读取失败" in shown
    assert "原始代号：work_reader_failed" in html
    assert not re.search(r"\d+/\d+/\d{4}, \d", shown)
    unchecked = html.split('data-integration-row="/api/acme-unchecked"', 1)[1].split("</tr>", 1)[0]
    assert "上次成功" not in unchecked
    # 读不到工作服务是故障,用告警样式,不和「真的没有记录」一样灰。
    business = html.split("通知与线索来源", 1)[1].split('class="integration-layer"', 1)[0]
    assert 'class="review-notice"' in business and "state-empty" not in business and "review-empty" not in business


def recheck(answer):
    """点一行的「重读」,接口按 answer 回话;返回这一行闪出的结果和重画后的那一行。"""
    setup = "INTEGRATIONS=" + json.dumps(integrations_case()) + ";"
    return run("""(async()=>{let handler;document.addEventListener=(type,fn)=>{if(type==='click') handler=fn;};
      globalThis.CSS={escape:s=>s};globalThis.setTimeout=()=>0;toast=()=>{};startIntegrations();
      api=async path=>{if(path==='/api/integrations') return INTEGRATIONS;""" + answer + """};
      const check={dataset:{integrationCheck:'/api/acme-broken',label:'重读'},disabled:false,title:'',classList:{add(){},remove(){}},isConnected:false};
      await handler({target:{closest:sel=>sel==='[data-integration-check]'?check:null}});
      const row=$('integration-list').innerHTML.split('data-integration-row="/api/acme-broken"')[1].split('</tr>')[0];
      return [INTEGRATION_FLASH.get('/api/acme-broken'),row];})()""", setup)


def test_recheck_says_success_only_when_the_endpoint_returned_data():
    # 200 回来但内容是 available:false:那一行写着「读取失败」,下面不许再闪一句绿色的「刚刚读取成功」。
    flash, row = recheck("return {available:false,reason:'work_reader_failed'};")
    assert flash == {"ok": False, "text": "仍不可用：工作服务读取失败"}
    assert "刚刚读取成功" not in row and 'integration-flash bad' in row
    flash, row = recheck("return {available:true,items:[]};")
    assert flash == {"ok": True, "text": "刚刚读取成功"} and 'integration-flash ok' in row
    flash, row = recheck("throw new Error('HTTP 502');")
    assert flash == {"ok": False, "text": "读取失败：HTTP 502"}
