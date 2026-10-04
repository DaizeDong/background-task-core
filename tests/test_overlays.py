"""浮层的通用约定:对话框、页面内确认框和弹出菜单。

点遮罩或 Esc 能关(忙着或者框里有人打的字时例外),Enter 只按得动没灰掉的主按钮,
主按钮灰着时说为什么,浏览器自带的 confirm()/alert() 一个不剩,行菜单是真菜单。
数据全部是合成的;在 node:vm 的假 DOM 里跑,真实浏览器里的版式和原生行为另见交付说明。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_automation_ui import table_setup
from test_convchain_ui import chain_case, setup as chain_setup
from test_operations_ui import run
from test_task_operations_ui import harness
from tools.make_fixtures import task_delete_plan_case, task_repair_preview_case

ROOT = Path(__file__).resolve().parents[1] / "scripts" / "task_console"
STATIC = ROOT / "static"
PAGE = ROOT / "console.html"

# 记下 addEventListener 交来的处理函数,好在假 DOM 里直接调用;对话框给出 showModal/close。
WIRE = """
const handlers={};
const wire=(...ids)=>ids.forEach(id=>{$(id).addEventListener=(type,fn)=>{handlers[id+':'+type]=fn;};});
const modal=id=>{const d=$(id);d.open=false;d.showModal=()=>{d.open=true;d.shown=(d.shown||0)+1;};d.close=()=>{d.open=false;};return d;};
const submitEvent=()=>({preventDefault(){this.prevented=true;}});
toast=()=>{};
"""


# ---------------------------------------------------------------- 删除(技能与插件)
def test_delete_form_submits_only_after_the_plan_and_the_exact_name():
    result = run("""(async()=>{
      wire('delete-name','delete-cancel','delete-dialog','delete-form');modal('delete-dialog');
      const calls=[];let release;loadMaint=async()=>{};
      let focused=0;$('delete-name').focus=()=>focused++;
      api=(path)=>{calls.push(path);return path==='/api/maintenance/plan'?new Promise(r=>release=r):Promise.resolve({ok:true,message:'synthetic done'});};
      startDeletionControls();
      const submit=()=>handlers['delete-form:submit'](submitEvent());
      const pending=previewDeletion({name:'AcmeSkill',delete:'skill',location:'user'});
      const loading={mode:$('delete-dialog').closedBy,title:$('delete-confirm').title};
      $('delete-name').value='AcmeSkill';submit();await new Promise(r=>setTimeout(r,0));
      const early=calls.filter(p=>p==='/api/maintenance/delete').length;
      release({name:'AcmeSkill',kind:'skill',token:'synthetic-token',message:'synthetic plan',location:'user',path:'C:/Acme/skills/AcmeSkill',files:2,links:0,bytes:10});
      await pending;
      $('delete-name').value='AcmeSk';handlers['delete-name:input']();
      const partial={title:$('delete-confirm').title,hint:$('delete-hint').textContent,disabled:$('delete-confirm').disabled,mode:$('delete-dialog').closedBy};
      submit();await new Promise(r=>setTimeout(r,0));
      const afterPartial=calls.filter(p=>p==='/api/maintenance/delete').length;
      $('delete-name').value='AcmeSkill';handlers['delete-name:input']();
      const ready={disabled:$('delete-confirm').disabled,hint:$('delete-hint').textContent,chip:$('delete-expected').innerHTML};
      submit();submit();await new Promise(r=>setTimeout(r,0));
      return {loading,early,partial,afterPartial,ready,focused,deletes:calls.filter(p=>p==='/api/maintenance/delete').length};
    })()""", WIRE)
    # 预览还在路上:谁都关不掉,确认按钮说明在等什么,提交不发删除请求。
    assert result["loading"]["mode"] == "none" and "正在读取删除范围" in result["loading"]["title"]
    assert result["early"] == 0
    # 名称没打完:按钮灰着并说还差几个字,对话框只认 Esc 和取消。
    assert result["partial"]["disabled"] is True and "名称还差 3 个字符" in result["partial"]["title"]
    assert result["partial"]["hint"] and result["partial"]["mode"] == "closerequest"
    assert result["afterPartial"] == 0
    assert result["ready"]["disabled"] is False and "名称一致" in result["ready"]["hint"]
    assert 'class="confirm-name">AcmeSkill</code>' in result["ready"]["chip"] and 'data-copy-text="AcmeSkill"' in result["ready"]["chip"]
    # 预览一到,光标进名称框;连按两次提交只发一次删除。
    assert result["focused"] == 1 and result["deletes"] == 1


def test_name_mismatch_says_how_far_off_the_typed_name_is():
    assert run("[nameMismatchReason('','Acme'),nameMismatchReason('Ac','Acme'),nameMismatchReason('Acne','Acme'),nameMismatchReason('Acme','Acme')]") == \
        ["先输入完整名称", "名称还差 2 个字符", "名称不一致", ""]
    assert run("[dialogDismissMode(true,true),dialogDismissMode(false,true),dialogDismissMode(false,false)]") == \
        ["none", "closerequest", "any"]


# ---------------------------------------------------------------- 删除计划任务
def test_task_delete_buttons_and_hints_say_why_they_wait():
    plan, blocked = task_delete_plan_case(), task_delete_plan_case(blocking=True)
    result = run("""(async()=>{
      const out={};let focused=0;$('task-delete-name').focus=()=>focused++;
      openTaskDelete('Acme Backup Daily');
      out.opened={preview:$('task-delete-preview').disabled,previewTitle:$('task-delete-preview').title,
        reasonHint:$('task-delete-reason-hint').textContent,name:$('task-delete-name').disabled,
        title:$('task-delete-confirm').title,hint:$('task-delete-hint').textContent,mode:$('task-delete-dialog').closedBy};
      $('task-delete-reason').value='被 AcmeSync 取代';taskDeleteReasonChanged();
      out.typed={preview:$('task-delete-preview').disabled,mode:$('task-delete-dialog').closedBy};
      replies['/api/task/delete/plan']=plan;await previewTaskDelete();
      out.ready={name:$('task-delete-name').disabled,focused,chip:$('task-delete-expected').hidden};
      $('task-delete-name').value='Acme Backup';syncTaskDeleteConfirm();
      out.partial={title:$('task-delete-confirm').title,hint:$('task-delete-hint').textContent,disabled:$('task-delete-confirm').disabled};
      $('task-delete-name').value='Acme Backup Daily';syncTaskDeleteConfirm();
      out.match={disabled:$('task-delete-confirm').disabled,hint:$('task-delete-hint').textContent};
      replies['/api/task/delete/plan']=blocked;await previewTaskDelete();
      out.blocked={title:$('task-delete-confirm').title,name:$('task-delete-name').disabled,
        alerts:$('task-delete-alerts').innerHTML,body:$('task-delete-body').innerHTML};
      return out;})()""", harness() + f"const plan={json.dumps(plan)},blocked={json.dumps(blocked)};")
    opened = result["opened"]
    # 没写原因:预览按钮灰着并说先写原因;名称框在有可用预览之前打不了字。
    assert opened["preview"] is True and "先写删除原因" in opened["previewTitle"] and "先写删除原因" in opened["reasonHint"]
    assert opened["name"] is True and "先生成删除预览" in opened["title"] and opened["hint"]
    assert opened["mode"] == "any"
    assert result["typed"] == {"preview": False, "mode": "closerequest"}
    assert result["ready"] == {"name": False, "focused": 1, "chip": False}
    assert result["partial"]["disabled"] is True and "名称" in result["partial"]["title"] and result["partial"]["hint"]
    assert result["match"]["disabled"] is False and "名称一致" in result["match"]["hint"]
    blocked_state = result["blocked"]
    assert "预览列出了不能删除的原因" in blocked_state["title"] and blocked_state["name"] is True
    # 拦下的原因在任务名正下方的提醒区,不在长长的预览末尾。
    assert 'class="task-op-blocking" role="alert"' in blocked_state["alerts"] and "task-op-blocking" not in blocked_state["body"]


# ---------------------------------------------------------------- 修复工单
def test_repair_submit_says_why_it_waits_and_a_locked_note_says_so():
    preview = task_repair_preview_case()
    unavailable = dict(preview, work={"available": False, "reason": "work_reader_not_configured"})
    accepted = {"schemaVersion": 1, "ok": True, "existing": False, "item_id": "acme-repair-1",
                "receipt": {"ok": True, "status": "queued", "wakeup": True}, "message": "synthetic accepted"}
    result = run("""(async()=>{
      const out={};
      const opening=openTaskRepair('Acme Report Sync');
      out.loading=[$('repair-submit').title,$('repair-dialog').closedBy];await opening;
      $('repair-note').value='先看返回码';syncTaskRepairSubmit();out.typed=$('repair-dialog').closedBy;
      await submitTaskRepair();out.done=[$('repair-submit').disabled,$('repair-submit').title,$('repair-dialog').closedBy];
      $('repair-dialog').close();replies['/api/task/repair/preview']=unavailable;
      await openTaskRepair('Acme Report Sync');out.work=[$('repair-submit').disabled,$('repair-submit').title,$('repair-note-hint').textContent];
      $('repair-dialog').close();replies['/api/task/repair/preview']=preview;
      sessionStorage.setItem('tc.repair.Acme Report Sync',JSON.stringify({name:'Acme Report Sync',note:'上次的备注',request_id:'repair-synthetic-1'}));
      await openTaskRepair('Acme Report Sync');out.locked=[$('repair-note').readOnly,$('repair-note-hint').textContent,$('repair-dialog').closedBy];
      return out;})()""",
        harness(**{"/api/task/repair/preview": preview, "/api/task/repair": accepted,
                   "/api/task/repairs": {"available": True, "orders": {}}})
        + f"const preview={json.dumps(preview)},unavailable={json.dumps(unavailable)};")
    assert "正在读取事实" in result["loading"][0] and result["loading"][1] == "none"
    assert result["typed"] == "closerequest"
    assert result["done"][0] is True and "工单已提交给 Agent" in result["done"][1] and result["done"][2] == "any"
    assert result["work"][0] is True and "工作服务不可用" in result["work"][1] and "工作服务不可用" in result["work"][2]
    # 锁住的备注不是人刚打的字,误点遮罩照样能关;但要说清为什么改不了。
    assert result["locked"][0] is True and "备注沿用上次请求，不能修改" in result["locked"][1] and result["locked"][2] == "any"


# ---------------------------------------------------------------- 页面内确认框
def test_ask_confirm_answers_no_on_cancel_escape_and_backdrop_and_yes_on_submit():
    result = run("""(async()=>{
      wire('confirm-form','confirm-cancel','confirm-dialog');const d=modal('confirm-dialog');
      let focused='';$('confirm-cancel').focus=()=>focused='cancel';$('confirm-ok').focus=()=>focused='ok';
      const results=[];
      let p=askConfirm({title:'停用 1 个任务',items:[{text:'Acme 同步',note:'AcmeSync'}],danger:true,confirmLabel:'停用'});
      const danger={focused,items:$('confirm-items').innerHTML,ok:[$('confirm-ok').className,$('confirm-ok').textContent],hidden:$('confirm-items').hidden};
      handlers['confirm-cancel:click']();results.push(await p,d.open);
      p=askConfirm({title:'新建会话'});const plain={focused,ok:$('confirm-ok').className,hidden:$('confirm-items').hidden};
      handlers['confirm-form:submit'](submitEvent());results.push(await p,d.open);
      // Esc 和点遮罩:浏览器先关掉对话框,再发 close。
      p=askConfirm({title:'第三个'});d.open=false;handlers['confirm-dialog:close']();results.push(await p);
      // 上一个问题晚到的 close 不能回答已经换上的新问题。
      const first=askConfirm({title:'甲'}), second=askConfirm({title:'乙'});
      handlers['confirm-dialog:close']();handlers['confirm-form:submit'](submitEvent());
      results.push(await first,await second);
      return {results,danger,plain};})()""", WIRE)
    assert result["results"] == [False, False, True, False, False, False, True]
    assert result["danger"]["focused"] == "cancel" and result["danger"]["ok"] == ["danger", "停用"]
    assert "Acme 同步" in result["danger"]["items"] and 'class="confirm-note">AcmeSync' in result["danger"]["items"]
    assert result["danger"]["hidden"] is False
    assert result["plain"] == {"focused": "ok", "ok": "primary", "hidden": True}


def test_disabling_tasks_asks_in_page_with_titles_and_sends_nothing_on_no():
    result = run("""(async()=>{
      const asked=[], calls=[];askConfirm=async options=>{asked.push(options);return false;};
      api=async path=>{calls.push(path);return {ok:true,status:'applied'};};
      await act(['AcmeSync','AcmeOther'],'disable');
      return {asked,calls,busy};})()""", table_setup())
    options = result["asked"][0]
    assert result["calls"] == [] and result["busy"] is False
    assert options["danger"] is True and "停用 2 个任务" in options["title"]
    assert options["items"] == [{"text": "Zulu", "note": "AcmeSync"}, {"text": "Alpha", "note": "AcmeOther"}]


def test_session_file_cleanup_asks_in_page_and_sends_nothing_on_no():
    rels = [f"2030/01/acme-{i}.jsonl" for i in range(10)]
    result = run("""(async()=>{
      const asked=[], calls=[];askConfirm=async options=>{asked.push(options);return false;};
      api=async path=>{calls.push(path);return {ok:true,deleted:0};};
      CXL={items:RELS.map(rel=>({rel,bytes:1024}))};CXSEL=new Set(RELS);
      await cxDelete();return {asked,calls};})()""", "const RELS=" + json.dumps(rels) + ";")
    options = result["asked"][0]
    assert result["calls"] == []
    assert options["danger"] is True and len(options["items"]) == 8 and "还有 2 份" in options["more"]
    assert "10 份会话文件" in options["title"] and "10K" in options["body"]


def test_plugin_cache_cleanup_asks_before_the_request():
    sys_case = {"pluginCache": {"available": True, "deletableCount": 9, "size": {"bytes": 2048},
                                "leftovers": [{"name": f"temp_git_acme{i}", "ageHours": 48 + i, "deletable": i < 9}
                                              for i in range(10)]}}
    result = run("""(async()=>{
      const asked=[], calls=[];let answer=false;askConfirm=async options=>{asked.push(options);return answer;};
      api=async(path,options)=>{calls.push(JSON.parse(options.body).action);return {ok:true,message:'synthetic cleaned'};};
      loadSys=async()=>{};toast=()=>{};SYS=CASE;
      await maintAct('clean.tempgit','-');const refused=calls.length;
      answer=true;await maintAct('clean.tempgit','-');
      return {refused,calls,asked};})()""", "const CASE=" + json.dumps(sys_case) + ";")
    assert result["refused"] == 0 and result["calls"] == ["clean.tempgit"]
    options = result["asked"][0]
    assert options["danger"] is True and "清理 9 个临时目录" in options["title"]
    assert "由后端按名称形状和年龄选定" in options["body"] and "插件缓存合计 2K" in options["body"]
    assert len(options["items"]) == 8 and "还有 1 个" in options["more"]
    label = run("SYS=CASE;renderSys();$('mt-sys').innerHTML", "const CASE=" + json.dumps(sys_case) + ";")
    assert "清理 9 个临时目录" in label


def test_forking_asks_in_page_and_sends_nothing_on_no():
    chain = chain_case()
    result = run("""(async()=>{
      const calls=[];let answer=false;askConfirm=async()=>answer;
      api=async path=>{calls.push(path);return {newId:'0000000b-0000-4000-8000-000000000002'};};
      globalThis.sessionStorage={getItem:()=>null,setItem(){},removeItem(){}};loadConvos=async()=>{};
      CH_SEL='s:0:1';await chFork();const refused=calls.length;
      answer=true;await chFork();return [refused,calls.filter(p=>p==='/api/convo/fork').length];})()""",
        chain_setup(chain, "chFocusList=()=>{};"))
    assert result == [0, 1]


def test_no_native_confirm_or_alert_remains():
    offenders = []
    for path in STATIC.rglob("*.js"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("//", 1)[0] if not line.lstrip().startswith("//") else ""
            if re.search(r"(?<![\w$])(?:window\.)?(?:confirm|alert)\s*\(", code):
                offenders.append(f"{path.name}:{number}")
    assert offenders == []


# ---------------------------------------------------------------- 会话:改名、移动、拖放
CV = """
for(const id of ['cv-submit','cv-cancel','cv-dialog']) $(id).classList={add(){},remove(){}};
$('cv-submit').disabled=false;
modal('cv-dialog');
CONVOS={groups:[],locations:[{id:'C--Acme-a',cwd:'C:/Acme/a',storagePath:'C:/Acme/store/a'},{id:'C--Acme-b',cwd:'C:/Acme/b',storagePath:'C:/Acme/store/b'}]};
const row={id:'0000000a-0000-4000-8000-000000000001',projectDir:'C--Acme-a',title:'Example conversation'};
const calls=[];api=async path=>{calls.push(path);return {};};
"""


def test_dropping_a_conversation_opens_the_move_dialog_instead_of_moving():
    result = run("""(()=>{
      CV_DRAG=row;cvClearDrag=()=>{CV_DRAG=null;};let prevented=0;
      cvDrop({target:{closest:()=>({dataset:{cvproject:'C--Acme-b'}})},preventDefault(){prevented++;}});
      return {shown:$('cv-dialog').shown,target:$('cv-target').value,kind:CV_EDIT.kind,calls,prevented,
        submit:[$('cv-submit').textContent,$('cv-submit').disabled]};})()""", WIRE + CV)
    assert result == {"shown": 1, "target": "C--Acme-b", "kind": "move", "calls": [], "prevented": 1,
                      "submit": ["移动到此项目", False]}


def test_rename_cannot_submit_an_empty_or_unchanged_name():
    result = run("""(()=>{
      cvOpenManager('rename',row);const out={same:[$('cv-submit').disabled,$('cv-submit').title,$('cv-dialog').closedBy]};
      $('cv-new-title').value='  ';cvSyncSubmit();out.empty=[$('cv-submit').disabled,$('cv-submit').title];
      $('cv-new-title').value='Renamed example';cvSyncSubmit();out.changed=[$('cv-submit').disabled,$('cv-dialog').closedBy];
      return out;})()""", WIRE + CV)
    assert result["same"][0] is True and "名称没有变化" in result["same"][1] and result["same"][2] == "any"
    assert result["empty"][0] is True and "先写会话名称" in result["empty"][1]
    assert result["changed"] == [False, "closerequest"]


def test_the_row_menu_button_does_not_open_the_conversation():
    rows = [{"id": "0000000a-0000-4000-8000-000000000001", "title": "Synthetic A", "titleFrom": "rename",
             "file": "/synthetic/a.jsonl", "humanSeen": 2, "partial": False, "ageHours": 1, "bytes": 10, "preview": ""}]
    group = {"cwd": "/synthetic/p", "count": 1, "humanish": 1, "bytes": 10, "newest": 1, "truncated": False, "shown": rows}
    result = run("""(()=>{
      let opened=0;openConvoChain=()=>opened++;
      const html=cvRows(GROUP);
      // 用渲染出来的那颗按钮的真实属性回答 closest:它带了哪个 data-* 就算命中哪个选择器。
      const tag=html.match(/<button[^>]*data-cvmenu=[^>]*>/)[0];
      const has=sel=>sel.split(',').some(part=>{const m=part.trim().match(/^\\[([\\w-]+)\\]$/);return !!m && new RegExp('\\\\s'+m[1]+'[=\\\\s>]').test(tag);});
      const button={dataset:{cvopen:'0000000a-0000-4000-8000-000000000001'}};
      cvClick({target:{closest:sel=>has(sel)?button:null}});
      return {opened,tag,trash:/data-cvdelete/.test(html.split('popover')[0]),eye:html.includes('i-eye')};})()""",
        "const GROUP=" + json.dumps(group) + ";")
    assert result["opened"] == 0
    assert 'popovertarget="cvm-' in result["tag"]
    # 行上不再常驻垃圾桶和重复的「查看」眼睛:删除在菜单最底下,标题本身就是打开。
    assert result["trash"] is False and result["eye"] is False


# ---------------------------------------------------------------- 静态外壳
def test_every_dialog_uses_the_shared_shell_and_light_dismiss():
    html = PAGE.read_text(encoding="utf-8")
    dialogs = re.findall(r"<dialog\b[^>]*>.*?</dialog>", html, re.S)
    assert len(dialogs) == 7
    for block in dialogs:
        tag = re.match(r"<dialog\b[^>]*>", block).group(0)
        assert 'closedby="any"' in tag and "console-dialog" in tag, tag
        assert 'class="dialog-head"' in block and "dialog-x" in block, tag
        # 表单里没写 type 的按钮默认是提交:排在前面的「取消」会变成 Enter 按到的那一个。
        for button in re.findall(r"<button\b[^>]*>", block):
            assert re.search(r'\btype="(button|submit)"', button), button
    for form_id, primary in (("delete-form", "delete-confirm"), ("task-delete-form", "task-delete-confirm"),
                             ("repair-form", "repair-submit"), ("cv-form", "cv-submit"), ("confirm-form", "confirm-ok")):
        form = re.search(r'<form id="' + form_id + r'" method="dialog">(.*?)</form>', html, re.S)
        assert form and re.search(r'<button[^>]*type="submit"[^>]*id="' + primary + r'"|<button[^>]*id="' + primary + r'"[^>]*type="submit"', form.group(1)), form_id
    # 原因框属于预览那张表单:在里面按 Enter 生成预览,不会按到确认删除。
    assert re.search(r'<input id="task-delete-reason"[^>]*form="task-delete-preview-form"', html)
    assert re.search(r'<button id="task-delete-preview" type="submit" form="task-delete-preview-form"', html)
    source = (STATIC / "panels" / "repositories.js").read_text(encoding="utf-8")
    assert 'setAttribute?.("closedby", "any")' in source and '"rp-review console-dialog"' in source


def test_publish_review_needs_a_deliberate_enter_in_the_message_field():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node required for browser module checks")
    source = (STATIC / "panels" / "repositories.js").read_text(encoding="utf-8")
    program = r"""
const vm=require('node:vm');let opened=null;const focused=[];
function element(tag){
  const e={tag,children:[],textContent:'',value:'',handlers:{},attrs:{},
    appendChild(x){this.children.push(x);return x;},setAttribute(k,v){this.attrs[k]=v;},remove(){},
    addEventListener(k,fn){this.handlers[k]=fn;},focus(){focused.push(this.textContent || this.tag);},
    showModal(){opened=this;}};
  return e;
}
const find=(node,test)=>test(node)?node:node.children.map(c=>find(c,test)).find(Boolean);
const context=vm.createContext({document:{createElement:element,body:element('body')},$:()=>null,toast(){}});
vm.runInContext(SOURCE,context);
(async()=>{
  const review=vm.runInContext('repoReview([{name:"acme",plan:{retry:false,expect:{},files:[],ahead:[],diff:""}}])',context);
  const input=find(opened,c=>c.tag==='input'), form=find(opened,c=>c.tag==='form');
  const key=extra=>Object.assign({key:'Enter',prevented:false,preventDefault(){this.prevented=true;}},extra);
  const beforeFocus=key();input.handlers.keydown(beforeFocus);
  input.handlers.focus();
  const held=key({repeat:true});input.handlers.keydown(held);
  const composing=key({isComposing:true});input.handlers.keydown(composing);
  input.value='synthetic change';input.handlers.input();const dismiss=opened.attrs.closedby;
  const deliberate=key();input.handlers.keydown(deliberate);
  form.handlers.submit({preventDefault(){}});
  console.log(JSON.stringify({first:focused[0],prevented:[beforeFocus.prevented,held.prevented,composing.prevented,deliberate.prevented],
    dismiss,message:await review,shell:[opened.className,find(opened,c=>c.className==='icon-only dialog-x')!=null]}));
})();
""".replace("SOURCE", json.dumps(source))
    out = subprocess.run([node, "-"], input=program, encoding="utf-8", capture_output=True, timeout=20)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    # 打开时焦点在「取消」;没点进框、按住连发、输入法组字时的 Enter 都被拦下,点进框后的一次 Enter 才发布。
    assert result["first"] == "取消"
    assert result["prevented"] == [True, True, True, False]
    assert result["dismiss"] == "closerequest" and result["message"] == "synthetic change"
    assert result["shell"] == ["rp-review console-dialog", True]
