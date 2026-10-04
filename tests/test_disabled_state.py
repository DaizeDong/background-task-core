"""不可用的控件:灰、点不动、说得出自己是谁和为什么不能点。数据全部是合成的。

样式部分直接读 static/*.css;行为部分用 node:vm 台架调 api.js / actions.js / operations.js /
calls.js / profile.js 里的函数,不需要浏览器。真实浏览器里的走查另见交付说明。
"""
import re
from pathlib import Path

from test_operations_ui import run

STATIC = Path(__file__).resolve().parents[1] / "scripts" / "task_console" / "static"
PAGE = STATIC.parent / "console.html"

# 台架里的按钮:够 setDisabled 用的最小 DOM 接口。
BUTTON = """
const fakeButton=(title,attrs={},label=null)=>({title,disabled:false,dataset:{},attrs:{...attrs},
  getAttribute(k){return k in this.attrs?this.attrs[k]:null;},setAttribute(k,v){this.attrs[k]=String(v);},
  removeAttribute(k){delete this.attrs[k];},querySelector(s){return s==='.control-label' && label?{textContent:label}:null;}});
"""
READ_ONLY = "document.querySelector=()=>({content:'true'});"


def css_rules():
    for sheet in sorted(STATIC.glob("*.css")):
        text = re.sub(r"/\*.*?\*/", "", sheet.read_text(encoding="utf-8"), flags=re.S)
        for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text):
            yield sheet.name, [one.strip() for one in selectors.split(",")], body


def button_classes():
    """页面上真正用在 <button> 上的类名,从 console.html 和全部脚本里收集,不手写名单。"""
    source = PAGE.read_text(encoding="utf-8") + "".join(p.read_text(encoding="utf-8") for p in STATIC.rglob("*.js"))
    classes = {"mini", "icon-only"}  # ibtn() 拼出来的那组
    for tag in re.findall(r"<button\b([^>]*)>", source):
        for value in re.findall(r'class="([^"$]*)', tag):
            classes.update(value.split())
    return classes


def test_hover_colours_never_apply_to_a_disabled_button():
    classes = button_classes()
    assert {"repair-chip", "record-link", "primary", "fix"} <= classes
    offenders = []
    for sheet, selectors, body in css_rules():
        if not re.search(r"(^|;)\s*(color|border[\w-]*|background[\w-]*)\s*:", body):
            continue
        for selector in selectors:
            if ":hover" not in selector:
                continue
            hovered = selector.split(":hover")[0].split()
            compound = hovered[-1] if hovered else ""
            targets_button = compound.startswith("button") or bool(set(re.findall(r"\.([\w-]+)", compound)) & classes)
            # 那条统一的不可用规则自己就是 :disabled:hover,它是答案,不是违规。
            is_disabled_rule = re.search(r"(?<!not\():disabled", selector)
            if targets_button and ":not(:disabled)" not in selector and not is_disabled_rule:
                offenders.append(f"{sheet}: {selector}")
    assert offenders == []


def test_one_disabled_rule_wins_over_every_variant():
    rules = [(sheet, selectors, body) for sheet, selectors, body in css_rules()
             if any(re.fullmatch(r"button:disabled(:hover|:focus)?", s) for s in selectors)]
    assert len(rules) == 1, rules
    body = rules[0][2].replace(" ", "")
    opacity = float(re.search(r"opacity:([\d.]+)!important", body).group(1))
    assert opacity < 0.6
    for declaration in ("background:transparent!important", "border-color:var(--line)!important",
                        "color:var(--dim)!important", "cursor:not-allowed!important"):
        assert declaration in body
    # 不可用不用虚线:虚线已经表示建议和未检查。
    assert "dashed" not in body
    # 操作列原来的淡出(.25)和「不可用也全不透明」的覆盖都已删掉,半透明只剩一个意思。
    for sheet, selectors, body in css_rules():
        if any(s.startswith("td.ops .mini") for s in selectors):
            assert "opacity" not in body and "cursor" not in body and "background" not in body, (sheet, selectors)


def test_form_controls_show_disabled_and_readonly():
    rules = {tuple(selectors): body.replace(" ", "") for _, selectors, body in css_rules()}
    disabled = rules[("input:disabled", "select:disabled", "textarea:disabled")]
    assert "opacity:.55" in disabled and "cursor:not-allowed" in disabled and "background:var(--panel2)" in disabled
    readonly = rules[("input[readonly]", "textarea[readonly]")]
    assert "background:var(--panel2)" in readonly and "color:var(--dim)" in readonly and "cursor:default" in readonly


def test_clickable_repair_chip_is_solid_and_advice_stays_dashed():
    rules = {tuple(selectors): body.replace(" ", "") for _, selectors, body in css_rules()}
    assert "border-style:solid" in rules[("button.repair-chip .status-chip.task-repair",)]
    assert "cursor:pointer" in rules[("button.repair-chip",)]
    assert "›" in rules[("button.repair-chip .status-chip.task-repair::after",)]
    assert "border-style:dashed" in rules[(".status-chip.task-verdict",)]


def test_read_only_sync_names_the_action_once_and_keeps_its_label():
    result = run("""(()=>{
      const plain=fakeButton('运行一次'), named=fakeButton('立即运行一次，保留原计划',{'aria-label':'修复'});
      document.querySelectorAll=selector=>selector===ConsoleActions.selector?[plain,named]:[];
      ConsoleActions.sync();ConsoleActions.sync();
      return {plain:[plain.title,plain.disabled,plain.getAttribute('aria-label'),plain.getAttribute('aria-describedby')],
              named:[named.title,named.getAttribute('aria-label')]};
    })()""", BUTTON + READ_ONLY)
    assert result["plain"] == ["运行一次（不可用：只读预览，请用正式控制台）", True, None, "console-mode"]
    assert result["named"] == ["修复（不可用：只读预览，请用正式控制台）", "修复"]


def test_pending_lock_says_what_it_waits_for_and_restores_the_hint():
    result = run("""(()=>{
      const free=fakeButton('立即运行一次，保留原计划',{'aria-label':'运行一次'}), own=fakeButton('运行一次（不可用：请先启用）');
      own.disabled=true;
      document.querySelectorAll=selector=>selector===ConsoleActions.selector?[free,own]:[];
      const op=ConsoleActions.begin('/api/act',{body:JSON.stringify({verb:'run',name:'AcmeSync'})});
      const during=[free.title,free.disabled,own.title];
      ConsoleActions.finish(op,{ok:true,status:'run_requested'});
      return {during,after:[free.title,free.disabled,own.title,own.disabled]};
    })()""", BUTTON)
    assert result["during"] == ["运行一次（不可用：等待「运行一次 · AcmeSync」完成）", True, "运行一次（不可用：请先启用）"]
    assert result["after"] == ["立即运行一次，保留原计划", False, "运行一次（不可用：请先启用）", True]


def test_icon_button_reason_keeps_the_action_as_its_name():
    html = run("ibtn('i-push','提交并推送','','','没有未提交的改动，也没有未推送的提交')")
    assert 'aria-label="提交并推送"' in html and " disabled" in html
    assert 'title="提交并推送（不可用：没有未提交的改动，也没有未推送的提交）"' in html
    enabled = run("ibtn('i-web','在浏览器打开','data-rpact=\"web\"','','','在浏览器里打开 https://example.com/acme')")
    assert "disabled" not in enabled and 'aria-label="在浏览器打开"' in enabled
    assert 'title="在浏览器里打开 https://example.com/acme"' in enabled


def test_task_controls_name_themselves_when_disabled():
    html = run("taskActionButtons({name:'AcmeSync',state:'Unknown'})")
    for label in ("停用", "运行一次"):
        assert f'title="{label}（不可用：任务状态未确认）"' in html
    assert 'title="删除：先预览' in html  # 状态读不出来也能删,提示照旧


def test_git_status_is_an_inspection_not_an_operation():
    result = run("""(async()=>{
      fetch=async()=>({ok:true,status:200,json:async()=>({ok:true,files:[]})});
      const epoch=API_READ_EPOCH;
      await api('/api/maint/act',{method:'POST',inspect:true,body:JSON.stringify({action:'repo.status',name:'AcmeRepo'})});
      const afterInspect=[ConsoleActions.operations.length,API_READ_EPOCH-epoch];
      await api('/api/maint/act',{method:'POST',body:JSON.stringify({action:'repo.fetch',name:'AcmeRepo'})});
      return {afterInspect,afterWrite:ConsoleActions.operations.length};
    })()""")
    assert result == {"afterInspect": [0, 0], "afterWrite": 1}
    # 只读预览照样不发:那边的服务一律拒收 POST。
    refused = run("api('/api/maint/act',{method:'POST',inspect:true}).catch(error=>error.message)", READ_ONLY)
    assert "只读预览" in refused
    source = (STATIC / "panels" / "repositories.js").read_text(encoding="utf-8")
    assert re.search(r'inspect:true,\s*body:JSON\.stringify\(\{action:"repo\.status"', source)


CHAIN = "LLM={chain:{effective:['cc','codex','claude'],source:'file'}};"


def test_chain_draft_is_marked_unsaved_and_read_only_cannot_drag():
    clean = run("renderChain();$('lcnote').innerHTML", CHAIN)
    assert "未保存" not in clean
    dirty = run("LCDRAFT=['codex','cc','claude'];renderChain();[$('lcnote').innerHTML,!!$('lcsave').disabled,$('lcsave').title]", CHAIN)
    assert "未保存" in dirty[0] and dirty[1] is False and dirty[2] == "保存当前排列顺序"
    live = run("renderChain();$('lclist').innerHTML", CHAIN)
    assert 'draggable="true"' in live and 'draggable="false"' not in live
    assert 'title="上移（不可用：已在最前）" disabled' in live and 'title="下移（不可用：已在最后）" disabled' in live
    locked = run("LCDRAFT=['codex','cc','claude'];renderChain();[$('lclist').innerHTML,$('lcsave').disabled,$('lcreset').disabled,$('lcsave').title]",
                 CHAIN + READ_ONLY + "$('lcsave').dataset.label='保存顺序';")
    assert 'draggable="true"' not in locked[0] and locked[0].count('draggable="false"') == 3
    assert locked[1] is True and locked[2] is True and locked[3] == "保存顺序（不可用：只读预览，请用正式控制台）"
    selector = run("ConsoleActions.selector").split(",")
    for entry in ("[data-mv]", "#lcreset", "#cxall", "#cxnone"):
        assert entry in selector, entry
    # 保存顺序和放弃修改是带字的按钮,不再是只有图标。
    page = PAGE.read_text(encoding="utf-8")
    for name in ("lcsave", "lcreset"):
        tag = re.search(rf'<button[^>]*id="{name}"[^>]*>.*?</button>', page).group(0)
        assert "icon-only" not in tag and "control-label" not in tag and 'data-label="' in tag


def test_cleanup_selection_buttons_follow_the_selection():
    setup = BUTTON + """CXL={items:[{rel:'a.jsonl',bytes:1},{rel:'b.jsonl',bytes:2}]};
      const buttons={cxall:fakeButton('全选已列出的文件',{},'全选已列出的文件'),cxnone:fakeButton('清空选择',{},'清空选择'),
        cxdel:fakeButton('永久删除选中的文件，需要确认',{},'删除选中文件')};
      document.getElementById=id=>buttons[id] || {innerHTML:'',dataset:{}};
      const snap=()=>Object.fromEntries(Object.entries(buttons).map(([id,b])=>[id,[b.disabled,b.title]]));"""
    result = run("""(()=>{
      cxSelButtons();const none=snap();
      CXSEL.add('a.jsonl');cxSelButtons();const some=snap();
      CXSEL.add('b.jsonl');cxSelButtons();return {none,some,all:snap()};
    })()""", setup)
    assert result["none"] == {"cxall": [False, "全选已列出的文件"], "cxnone": [True, "清空选择（不可用：没有选中的文件）"],
                              "cxdel": [True, "删除选中文件（不可用：请先选择文件）"]}
    assert result["some"] == {"cxall": [False, "全选已列出的文件"], "cxnone": [False, "清空选择"],
                              "cxdel": [False, "永久删除选中的文件，需要确认"]}
    assert result["all"]["cxall"] == [True, "全选已列出的文件（不可用：已列出的文件都已选中）"]
    # 只读预览里三个都点不了,原因说的是只读,不是选没选。
    locked = run("cxSelButtons();snap()", setup + READ_ONLY)
    assert all(state == [True, f"{name}（不可用：只读预览，请用正式控制台）"]
               for state, name in zip(locked.values(), ("全选已列出的文件", "清空选择", "删除选中文件")))


def test_clear_filter_buttons_count_active_filters_on_every_scope():
    scopes = run("Object.keys(RESET_FILTER_COUNTS).sort()")
    assert scopes == sorted(["tasks", "repos", "catalog", "runtime", "convos", "llm", "diagnostics", "pipelines", "work", "automations"])
    # 每个范围敲一个字进搜索框,按钮就亮起来并说清 1 项;清掉之后又灰回去。
    for scope in scopes:
        result = run(f"""(()=>{{
          const reset=fakeButton('清除筛选');reset.dataset.resetFilters='{scope}';
          document.querySelectorAll=selector=>selector==='[data-reset-filters]'?[reset]:[];
          $('review-filter').value='all';
          const before=(syncResetFilters(),[reset.disabled,reset.title]);
          const box=RESET_FILTER_SEARCH['{scope}'];$(box).value='a';
          if('{scope}'==='catalog') CATALOG_QUERY='a';if('{scope}'==='work') WORK_QUERY='a';
          if('{scope}'==='automations') AUTO_QUERY='a';
          syncResetFilters();const after=[reset.disabled,reset.title];
          return {{before,after}};
        }})()""", BUTTON)
        assert result["before"] == [True, "清除筛选（不可用：当前没有生效的筛选）"], scope
        assert result["after"] == [False, "清除筛选（1 项）"], scope
    page = PAGE.read_text(encoding="utf-8")
    for name, scope in (("work-reset", "work"), ("automation-reset", "automations")):
        tag = re.search(rf'<button[^>]*id="{name}"[^>]*>', page).group(0)
        assert f'data-reset-filters="{scope}"' in tag and " hidden" not in tag
    # 同步与备份只留工具栏上那一个清除筛选。
    pipelines = (STATIC / "panels" / "pipelines.js").read_text(encoding="utf-8")
    assert "data-reset-filters" not in pipelines and page.count('data-reset-filters="pipelines"') == 1
