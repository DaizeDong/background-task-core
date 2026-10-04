"""删除与修复两个任务操作的页面部分:按钮落在哪几个标签页、确认按钮什么时候按得动、结果怎么说。

全部用合成数据(tools/make_fixtures.py 生成),在 node:vm 的假 DOM 里跑,不碰浏览器也不碰任何后端。
事件路由(events.js 不在这个台架里加载)用源码断言。真实浏览器里的版式另见交付说明。
"""
import json
import re
from pathlib import Path

from test_automation_ui import table_setup
from test_operations_ui import run
from test_panel_parity import module_source
from tools.make_fixtures import (task_delete_plan_case, task_delete_result_case, task_repair_order_case,
                                 task_repair_preview_case)

PAGE = Path(__file__).resolve().parents[1] / "scripts" / "task_console" / "console.html"

# 对话框、会话存储和一个记账的 api。replies 按路径给回复;值是函数时调用它(可以抛错)。
HARNESS = """
for(const id of ['task-delete-dialog','repair-dialog','work-detail']){const d=$(id);d.open=false;d.showModal=()=>{d.open=true};d.close=()=>{d.open=false};}
const storage={};
globalThis.sessionStorage={getItem:k=>Object.hasOwn(storage,k)?storage[k]:null,setItem:(k,v)=>{storage[k]=String(v)},removeItem:k=>{delete storage[k]}};
const calls=[], replies={};
toast=()=>{};load=async()=>{calls.push({path:'load'})};loadWork=async()=>{calls.push({path:'loadWork'})};
api=async(path,options)=>{
  calls.push({path,body:options&&options.body?JSON.parse(options.body):null});
  const reply=replies[path];
  return typeof reply==='function'?reply():reply;
};
"""


def harness(**replies):
    return table_setup() + HARNESS + "".join(
        f"replies[{json.dumps(path)}]={json.dumps(value)};" for path, value in replies.items())


def button(html, attr, name):
    match = re.search(r'<button[^>]*' + attr + r'="' + re.escape(name) + r'"[^>]*>', html)
    assert match, f"没有 {attr}={name} 的按钮"
    return match.group(0)


# ---------------------------------------------------------------- 按钮
def test_rows_offer_repair_everywhere_and_delete_everywhere_but_the_backbone_tasks():
    html = run("taskActionButtons({name:'Acme Backup Daily',state:'Ready'})")
    repair, delete = button(html, "data-task-repair", "Acme Backup Daily"), button(html, "data-task-delete", "Acme Backup Daily")
    assert "disabled" not in repair and "disabled" not in delete
    assert 'title="修复' in repair and 'title="删除' in delete
    assert '#i-repair' in html and '#i-trash' in html
    assert '<span class="control-label">修复</span>' in html and '<span class="control-label">删除</span>' in html
    card = run("taskActionButtons({name:'Acme Backup Daily',state:'Ready'},{deletable:false})")
    assert 'data-task-repair="Acme Backup Daily"' in card and "data-task-delete" not in card
    # 流水线上的任务不靠调用方记得传 deletable:false,默认就没有删除按钮(名字不分大小写)。
    backbone = run("PIPELINE_DEFS.backup.name='AcmeBackup';taskActionButtons({name:'acmebackup',state:'Ready'})")
    assert 'data-task-repair="acmebackup"' in backbone and "data-task-delete" not in backbone
    # data-delete 和 data-fix 已经各有主人(删除技能、概览里的修复按钮),借用就会被那两条路由接走。
    for page in (html, card):
        assert "data-delete=" not in page and "data-fix=" not in page and "data-retire" not in page


def test_a_running_task_can_be_repaired_but_not_deleted():
    html = run("taskActionButtons({name:'AcmeSync',state:'Running'})")
    assert "disabled" not in button(html, "data-task-repair", "AcmeSync")
    delete = button(html, "data-task-delete", "AcmeSync")
    assert "disabled" in delete and "正在运行" in delete


def test_each_automation_tab_gets_the_right_buttons():
    prefix = table_setup() + "PIPELINE_DEFS.sync.name='AcmeSync';COMPONENTS={available:true,tasks:[]};"
    table, rows, cards = run("render();renderAutomations();renderPipelines();"
                             "[$('tbl').innerHTML,$('automation-list').innerHTML,$('pipeline-body').innerHTML]", prefix)
    # 同步与备份流水线上的任务是备份本身的骨架:三个标签页上都能修,哪一个上都不能删。
    for page in (table, rows):
        assert 'data-task-repair="AcmeSync"' in page and 'data-task-delete="AcmeSync"' not in page
        assert 'data-task-delete="AcmeOther"' in page
    assert 'data-task-repair="AcmeSync"' in cards and "data-task-delete" not in cards


def test_read_only_preview_and_the_pending_lock_cover_both_operations():
    selector = run("ConsoleActions.selector").split(",")
    for entry in ("[data-task-repair]", "[data-task-delete]", "#task-delete-preview", "#task-delete-confirm", "#repair-submit"):
        assert entry in selector, entry
    assert "[data-retire]" not in selector
    html = run("taskActionButtons({name:'AcmeSync',state:'Ready'})", "document.querySelector=()=>({content:'true'});")
    for attr in ("data-task-repair", "data-task-delete"):
        tag = button(html, attr, "AcmeSync")
        assert "disabled" in tag and "只读预览" in tag


def test_operation_panel_names_both_operations_and_never_calls_a_repair_done():
    labels = run("""['/api/task/delete/plan','/api/task/delete/apply','/api/task/repair/preview','/api/task/repair']
      .map(path=>operationLabel(path,{name:'AcmeSync',reason:'synthetic',note:'synthetic'}))""")
    assert labels == ["准备删除预览 · AcmeSync", "删除任务 · AcmeSync", "读取任务事实 · AcmeSync", "提交修复工单 · AcmeSync"]
    accepted = run("operationOutcome('/api/task/repair',{},{ok:true,existing:false,receipt:{status:'queued'}})")
    assert accepted["tone"] == "warn" and "已受理" in accepted["message"] and "尚未修复" in accepted["message"]
    uncertain = run("operationOutcome('/api/task/repair',{},{ok:false,uncertain:true,message:'未收到提交确认'})")
    assert uncertain["tone"] == "warn" and "核对原请求" in uncertain["message"]
    refused = run("operationOutcome('/api/task/repair',{},{ok:false,uncertain:false,code:'agent_offer_unavailable',message:'修复工单已建立，但 Agent 处理当前不可用'})")
    assert refused["tone"] == "bad" and "agent_offer_unavailable" in refused["message"]
    blocked = run("operationOutcome('/api/task/delete/plan',{},sample)", "const sample=" + json.dumps(task_delete_plan_case(blocking=True)) + ";")
    assert blocked["tone"] == "warn" and "不能删除" in blocked["message"]
    # 操作记录不留请求正文里的原因、备注和令牌。
    kept = run("""(()=>{const op=ConsoleActions.begin('/api/task/delete/apply',{body:JSON.stringify({token:'synthetic-secret',name:'AcmeSync'})});
      ConsoleActions.finish(op,{ok:false,status:'partial',message:'合成'});return JSON.stringify(ConsoleActions.operations);})()""")
    assert "synthetic-secret" not in kept


def test_delete_outcomes_keep_partial_and_failed_apart_from_done():
    setup = "".join(f"const {status}=" + json.dumps(task_delete_result_case(status)) + ";"
                    for status in ("ok", "partial", "failed", "unknown"))
    ok, partial, failed, unknown = run("""[operationOutcome('/api/task/delete/apply',{},ok),operationOutcome('/api/task/delete/apply',{},partial),
      operationOutcome('/api/task/delete/apply',{},null,Object.assign(new Error('没有删除'),{payload:failed})),
      operationOutcome('/api/task/delete/apply',{},null,Object.assign(new Error('合成'),{payload:unknown}))]""", setup)
    assert ok["tone"] == "ok"
    assert partial["tone"] == "warn" and partial["message"].startswith("部分完成")
    assert failed["tone"] == "bad" and "没有删除" in failed["message"]
    assert unknown["tone"] == "bad" and "无法确认是否已删除" in unknown["message"] and "没有删除" not in unknown["message"]
    # 结果里没带说明时也要按 status 说话,不能退成一句「HTTP 500」。
    bare = run("operationOutcome('/api/task/delete/apply',{},null,Object.assign(new Error('合成'),{payload:{ok:false,status:'unknown'}}))")
    assert bare["message"] == "无法确认是否已删除，请刷新核对"


def test_an_unknown_delete_outcome_is_drawn_as_a_result_not_as_a_bare_error():
    # 读不出删没删时服务器回 500,正文仍是一份完整结果:要照它画,不能只剩一句报错,更不能说成「没有删除」。
    plan, unknown = task_delete_plan_case(), task_delete_result_case("unknown")
    result = run("""(async()=>{
      openTaskDelete('Acme Backup Daily');$('task-delete-reason').value='被 AcmeSync 取代';await previewTaskDelete();
      $('task-delete-name').value='Acme Backup Daily';syncTaskDeleteConfirm();await applyTaskDelete();
      return {html:$('task-delete-body').innerHTML,state:$('task-delete-state').textContent};})()""",
        harness(**{"/api/task/delete/plan": plan}) + "const unknown=" + json.dumps(unknown) + ";"
        "replies['/api/task/delete/apply']=()=>{throw Object.assign(new Error('合成'),{status:500,payload:unknown});};")
    assert result["state"] == "无法确认是否已删除，请刷新核对"
    assert "刷新页面核对" in result["html"] and "没有删除" not in result["state"]


# ---------------------------------------------------------------- 删除对话框
def test_delete_confirm_needs_a_reason_a_clean_preview_and_the_exact_name():
    plan, blocked = task_delete_plan_case(), task_delete_plan_case(blocking=True)
    result = run("""(async()=>{
      const out={};
      openTaskDelete('Acme Backup Daily');out.opened=$('task-delete-dialog').open;out.initial=$('task-delete-confirm').disabled;
      await previewTaskDelete();out.noReason=[calls.length,$('task-delete-state').textContent];
      $('task-delete-reason').value='被 AcmeSync 取代';
      replies['/api/task/delete/plan']=blocked;await previewTaskDelete();
      $('task-delete-name').value='Acme Backup Daily';syncTaskDeleteConfirm();
      out.blocked=$('task-delete-confirm').disabled;out.blockedHtml=$('task-delete-alerts').innerHTML;
      out.blockedTitle=$('task-delete-confirm').title;out.blockedNameLocked=$('task-delete-name').disabled;
      // 回复自相矛盾(带着拦下的原因却又给了令牌)时,也按拦下处理:原因在,就不能确认。
      replies['/api/task/delete/plan']={...blocked,applicable:true,token:'synthetic-delete-token',expiresIn:300};
      await previewTaskDelete();out.blockedWithToken=$('task-delete-confirm').disabled;
      replies['/api/task/delete/plan']=plan;await previewTaskDelete();
      out.planHtml=$('task-delete-alerts').innerHTML+$('task-delete-body').innerHTML;
      out.names=['Acme Backup','acme backup daily','Acme Backup Daily ','Acme Backup Daily'].map(name=>{
        $('task-delete-name').value=name;syncTaskDeleteConfirm();return $('task-delete-confirm').disabled;});
      TASK_DELETE.expires=Date.now()-1;syncTaskDeleteConfirm();out.expired=$('task-delete-confirm').disabled;
      await previewTaskDelete();$('task-delete-reason').value='另一个原因';taskDeleteReasonChanged();
      out.reasonChanged=$('task-delete-confirm').disabled;
      out.calls=calls;return out;})()""", harness() + f"const plan={json.dumps(plan)},blocked={json.dumps(blocked)};")
    assert result["opened"] and result["initial"] is True
    assert result["noReason"][0] == 0 and "原因" in result["noReason"][1]
    # 有拦下删除的原因时,即使名字一字不差,确认也按不动,而原因就摆在最醒目的地方。
    assert result["blocked"] is True and result["blockedWithToken"] is True
    assert 'class="task-op-blocking" role="alert"' in result["blockedHtml"] and "TASK_CONSOLE_DELETED_ARCHIVE" in result["blockedHtml"]
    # 拦下的原因在任务名正下方的提醒区,确认按钮的提示也说出来;名称框在没有可用预览时打不了字。
    assert "预览列出了不能删除的原因" in result["blockedTitle"] and result["blockedNameLocked"] is True
    assert result["names"] == [True, True, True, False]
    assert result["expired"] is True and result["reasonChanged"] is True
    assert {"path": "/api/task/delete/plan", "body": {"name": "Acme Backup Daily", "reason": "被 AcmeSync 取代"}} in result["calls"]
    html = result["planHtml"]
    for text in ("导出任务 XML 存档", "D:\\AcmePrivate\\deleted-tasks", "从计划程序删除", "C:\\Acme\\categories.json",
                 "移出健康监控清单", "acme-health.json", "后续清理：已就绪", "需要手动处理", "当前建议：保留", "C:\\Acme\\sync.exe --daily"):
        assert text in html.replace("&quot;", '"'), text


def test_a_partial_delete_lists_what_remains_and_never_reads_as_deleted():
    plan, partial = task_delete_plan_case(), task_delete_result_case("partial")
    result = run("""(async()=>{
      openTaskDelete('Acme Backup Daily');$('task-delete-reason').value='被 AcmeSync 取代';await previewTaskDelete();
      $('task-delete-name').value='Acme Backup Daily';syncTaskDeleteConfirm();
      await applyTaskDelete();await applyTaskDelete();
      return {html:$('task-delete-body').innerHTML,state:$('task-delete-state').textContent,
        confirm:$('task-delete-confirm').disabled,calls};})()""",
        harness(**{"/api/task/delete/plan": plan, "/api/task/delete/apply": partial}))
    applies = [c for c in result["calls"] if c["path"] == "/api/task/delete/apply"]
    # 令牌只交一次,第二次点击什么都不发;交的就是预览给的那张令牌和同一个名字。
    assert applies == [{"path": "/api/task/delete/apply", "body": {"token": "synthetic-delete-token", "name": "Acme Backup Daily"}}]
    assert result["calls"][-1] == {"path": "load"}
    assert result["state"].startswith("部分完成") and "已删除" not in result["state"]
    assert "还没完成" in result["html"] and "未配置删除后续钩子" in result["html"]
    assert "未配置后续清理：监控清单、备份和迁移计划没有清理" in result["html"]
    assert "status-chip ok" not in result["html"].split("各步骤")[0]
    assert result["confirm"] is True


def test_a_follow_up_blocked_after_the_delete_shows_the_retry_and_never_says_refused():
    # 执行时的拦下发生在任务删掉之后:页面不能说「拒绝了这次删除」,要给出单独重跑后续清理的命令和原样的请求。
    blocked = task_delete_result_case("followup_blocked")
    html = run("taskDeleteResultHtml(sample)", "const sample=" + json.dumps(blocked) + ";")
    assert "拒绝了这次删除" not in html
    assert "后续清理没有运行：任务已删除" in html and "单独重跑后续清理" in html
    text = html.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
    assert blocked["followupRetry"]["command"] in text
    assert '"transaction_id": null' in text and '"mode": "apply"' in text
    # 预览阶段的拦下才是「拒绝了这次删除」。
    assert "后续清理拒绝了这次删除" in run("taskFollowupHtml({state:'blocked',blocking:['合成']},'plan')")


def test_a_scheduler_failure_and_a_refused_token_both_say_nothing_was_deleted():
    plan, failed = task_delete_plan_case(), task_delete_result_case("failed")
    steps = """(async()=>{
      openTaskDelete('Acme Backup Daily');$('task-delete-reason').value='被 AcmeSync 取代';await previewTaskDelete();
      $('task-delete-name').value='Acme Backup Daily';syncTaskDeleteConfirm();await applyTaskDelete();
      return {html:$('task-delete-body').innerHTML,state:$('task-delete-state').textContent,loads:calls.filter(c=>c.path==='load').length};})()"""
    thrown = ("replies['/api/task/delete/apply']=()=>{throw Object.assign(new Error('没有删除：合成拒绝'),{status:500,payload:failed});};"
              "const failed=" + json.dumps(failed) + ";")
    result = run(steps, harness(**{"/api/task/delete/plan": plan}) + thrown)
    assert result["state"] == "没有删除" and "status-chip bad" in result["html"] and "合成拒绝" in result["html"]
    assert result["loads"] == 1
    stale = ("replies['/api/task/delete/apply']=()=>{throw Object.assign(new Error('预览不存在或已过期，请重新预览 (stale_plan)'),"
             "{status:409,payload:{ok:false,error:'预览不存在或已过期，请重新预览',code:'stale_plan'}});};")
    result = run(steps, harness(**{"/api/task/delete/plan": plan}) + stale)
    assert "stale_plan" in result["state"] and "重新预览" in result["state"] and result["loads"] == 1


def test_the_five_follow_up_states_read_differently():
    texts = run("""['ok','not_configured','blocked','failed','unreadable','future'].map(state=>
      taskFollowupHtml({state,message:'',steps:[],blocking:[],notes:[]},'plan'))""")
    assert len(set(texts)) == 6
    assert "不会被清理" in texts[1] and "拒绝" in texts[2] and "失败" in texts[3] and "读不出来" in texts[4]
    assert "无法识别" in texts[5]


# ---------------------------------------------------------------- 修复对话框
def test_repair_dialog_shows_the_facts_advice_and_limits_before_anything_is_filed():
    result = run("""(async()=>{await openTaskRepair('Acme Report Sync');
      return {html:$('repair-body').innerHTML,submit:$('repair-submit').disabled,calls};})()""",
        harness(**{"/api/task/repair/preview": task_repair_preview_case()}))
    html = result["html"]
    for text in ("合成建议：先看返回码", "要修", "失败 0x1", "C:\\Acme\\report.exe --sync", "合成：产物 30 小时没更新",
                 "0x1 × 5", "没有观察记录", "合成：漏火不补跑", "只诊断并提出修复方案", "不会改动这个任务本身"):
        assert text in html, text
    assert result["submit"] is False
    assert [c["path"] for c in result["calls"]] == ["/api/task/repair/preview"]


def test_an_existing_order_is_linked_instead_of_filing_another():
    result = run("""(async()=>{await openTaskRepair('Acme Report Sync');
      await submitTaskRepair();return {html:$('repair-body').innerHTML,submit:$('repair-submit').disabled,calls};})()""",
        harness(**{"/api/task/repair/preview": task_repair_preview_case(existing=task_repair_order_case("running"))}))
    assert result["submit"] is True and "已有修复工单" in result["html"]
    assert 'data-repair-order="acme-repair-1"' in result["html"]
    assert [c["path"] for c in result["calls"]] == ["/api/task/repair/preview"]
    unavailable = task_repair_preview_case()
    unavailable["work"] = {"available": False, "reason": "work_reader_not_configured"}
    # 上一次没交出去的工单(执行服务没连上):不是「已有工单不再新建」,而是可以再提交一次,后端会为它补交。
    undispatched = {"schemaVersion": 1, "ok": True, "existing": True, "item_id": "acme-repair-1",
                    "receipt": {"ok": True, "status": "queued", "wakeup": True}, "message": "已为现有工单提交 Agent 处理"}
    result = run("""(async()=>{await openTaskRepair('Acme Report Sync');
      const before={html:$('repair-body').innerHTML,submit:$('repair-submit').disabled,label:$('repair-submit').textContent};
      await submitTaskRepair();return {...before,state:$('repair-state').innerHTML,calls};})()""",
        harness(**{"/api/task/repair/preview": task_repair_preview_case(existing=task_repair_order_case(None)),
                   "/api/task/repair": undispatched, "/api/task/repairs": {"available": True, "orders": {}}}))
    assert result["submit"] is False and "补交" in result["label"]
    assert "还没有交给 Agent" in result["html"] and "不再新建" not in result["html"]
    assert [c["path"] for c in result["calls"]].count("/api/task/repair") == 1
    assert "已为现有修复工单提交 Agent 处理" in result["state"]
    result = run("(async()=>{await openTaskRepair('Acme Report Sync');return [$('repair-body').innerHTML,$('repair-submit').disabled];})()",
                 harness(**{"/api/task/repair/preview": unavailable}))
    assert "工作记录服务尚未连接" in result[0] and result[1] is True


def test_repair_submits_once_per_request_id_and_reuses_it_only_until_answered():
    accepted = {"schemaVersion": 1, "ok": True, "existing": False, "item_id": "acme-repair-1", "decision": "created",
                "receipt": {"ok": True, "status": "queued", "wakeup": True}, "message": "修复工单已建立并提交 Agent 处理"}
    uncertain = {"schemaVersion": 1, "ok": False, "code": "owner_reply_unknown", "uncertain": True,
                 "message": "未收到提交确认，再次点击会核对原请求"}
    result = run("""(async()=>{
      await openTaskRepair('Acme Report Sync');$('repair-note').value='  先看返回码 ';
      let release;replies['/api/task/repair']=()=>new Promise(resolve=>{release=()=>resolve(uncertain);});
      const first=submitTaskRepair(), second=submitTaskRepair();release();await Promise.all([first,second]);
      const kept=sessionStorage.getItem('tc.repair.Acme Report Sync'), afterUncertain=$('repair-state').innerHTML;
      $('repair-note').value='另一份备注';
      replies['/api/task/repair']=accepted;await submitTaskRepair();
      const text=$('repair-state').innerHTML;await submitTaskRepair();
      return {posts:calls.filter(c=>c.path==='/api/task/repair'),kept,afterUncertain,text,
        left:sessionStorage.getItem('tc.repair.Acme Report Sync'),submit:$('repair-submit').disabled,
        reloaded:calls.filter(c=>c.path==='/api/task/repairs' || c.path==='loadWork').length};})()""",
        harness(**{"/api/task/repair/preview": task_repair_preview_case(), "/api/task/repairs": {"available": True, "orders": {}}})
        + f"const uncertain={json.dumps(uncertain)},accepted={json.dumps(accepted)};")
    posts = result["posts"]
    # 并发的两下只发一次;没收到确认的重试带同一个请求号和同一份备注,不是新改的那份。
    assert len(posts) == 2 and posts[0]["body"] == posts[1]["body"]
    body = posts[0]["body"]
    assert body["name"] == "Acme Report Sync" and body["note"] == "先看返回码"
    assert re.fullmatch(r"[A-Za-z0-9_-]{8,120}", body["request_id"])
    assert json.loads(result["kept"])["request_id"] == body["request_id"]
    assert "未收到确认" in result["afterUncertain"]
    assert result["left"] is None and result["submit"] is True
    text = result["text"]
    assert "已受理" in text and "工作记录" in text and "Discord" in text and "还没有修好" in text
    assert 'data-repair-order="acme-repair-1"' in text
    assert result["reloaded"] >= 2


def test_a_definite_refusal_forgets_the_request_and_points_at_the_created_order():
    refused = {"schemaVersion": 1, "ok": False, "code": "agent_offer_unavailable", "uncertain": False,
               "item_id": "acme-repair-1", "message": "修复工单已建立，但 Agent 处理当前不可用：合成原因"}
    result = run("""(async()=>{await openTaskRepair('Acme Report Sync');await submitTaskRepair();
      return [$('repair-state').innerHTML,sessionStorage.getItem('tc.repair.Acme Report Sync'),$('repair-submit').disabled];})()""",
        harness(**{"/api/task/repair/preview": task_repair_preview_case(), "/api/task/repair": refused,
                   "/api/task/repairs": {"available": True, "orders": {}}}))
    assert "Agent 处理当前不可用" in result[0] and "已受理" not in result[0]
    assert 'data-repair-order="acme-repair-1"' in result[0] and "查看已建立的工单" in result[0]
    assert result[1] is None and result[2] is False


# ---------------------------------------------------------------- 修复进度
def test_each_repair_state_has_its_own_chip():
    cases = {("queued", "pending"): "修复排队中", ("running", "pending"): "修复中", ("reconcile", "pending"): "修复待核实",
             ("done", "pending"): "修复方案已出", ("failed", "pending"): "修复失败", ("stopped", "pending"): "修复已停止",
             ("future-state", "pending"): "修复状态未知", (None, "pending"): "修复未开始", ("done", "done"): "修复已关闭",
             (None, "future-todo"): "修复状态未知"}
    for (action, state), label in cases.items():
        order = task_repair_order_case(action, state)
        chip = run("REPAIRS={available:true,orders:{'Acme Report Sync':order}};taskRepairChip('Acme Report Sync')",
                   "const order=" + json.dumps(order) + ";")
        assert f"<span>{label}</span>" in chip, (action, state, chip)
        assert 'data-repair-order="acme-repair-1"' in chip and "task-repair" in chip
    # 芯片永远不说任务已经修好。
    for label in cases.values():
        assert "已修好" not in label and label != "修复完成"


def test_the_chip_sits_in_the_state_area_of_rows_and_the_table():
    order = task_repair_order_case("running")
    rows, table = run("REPAIRS={available:true,orders:{AcmeSync:order}};render();renderAutomations();"
                      "[$('automation-list').innerHTML,$('tbl').innerHTML]", table_setup() + "const order=" + json.dumps(order) + ";")
    assert re.search(r'<div class="automation-state">.*?修复中.*?</div><div class="automation-actions">', rows, re.S)
    state_cell = re.search(r'<tr data-i="\d+" data-name="AcmeSync".*?</tr>', table, re.S).group(0)
    assert "修复中" in state_cell and state_cell.index("修复中") < state_cell.index('class="ops"')
    assert "修复中" not in re.search(r'<tr data-i="\d+" data-name="AcmeOther".*?</tr>', table, re.S).group(0)


def test_not_read_unreadable_failed_and_empty_are_four_different_displays():
    result = run("""(async()=>{
      const out={};
      out.never=[taskRepairReadState().text,taskRepairChip('AcmeSync')];
      replies['/api/task/repairs']={available:true,orders:{AcmeSync:order}};await loadRepairs();
      out.loaded=[taskRepairReadState().text,taskRepairChip('AcmeSync'),repairsInFlight()];
      replies['/api/task/repairs']=()=>{throw new Error('合成网络错误');};await loadRepairs();
      out.failed=[taskRepairReadState().text,taskRepairChip('AcmeSync'),repairsInFlight()];
      replies['/api/task/repairs']={available:false,reason:'work_reader_not_configured',orders:{}};await loadRepairs();
      out.unavailable=[taskRepairReadState().text,taskRepairChip('AcmeSync')];
      replies['/api/task/repairs']={available:true,orders:{}};await loadRepairs();
      out.empty=[taskRepairReadState().text,taskRepairChip('AcmeSync')];
      return out;})()""", harness() + "const order=" + json.dumps(task_repair_order_case("running")) + ";")
    texts = [result[key][0] for key in ("never", "failed", "unavailable", "empty")]
    assert len(set(texts)) == 4
    assert result["never"][0] == "尚未读取修复工单" and "读取失败" in result["failed"][0] and "合成网络错误" in result["failed"][0]
    assert "work_reader_not_configured" in result["unavailable"][0] and result["empty"][0] == "没有修复工单"
    assert "修复中" in result["loaded"][1] and result["loaded"][2] is True
    # 读失败就不留上一次的芯片:一张停在旧值上的「修复中」看起来和正在修一模一样。
    assert result["failed"][1] == "" and result["failed"][2] is False
    assert result["never"][1] == result["unavailable"][1] == result["empty"][1] == ""


def test_polling_continues_only_while_an_order_is_in_flight():
    states = {"queued": True, "running": True, "reconcile": True, "done": False, "failed": False, None: False}
    for action, expected in states.items():
        order = task_repair_order_case(action)
        assert run("REPAIRS={available:true,orders:{A:order}};repairsInFlight()", "const order=" + json.dumps(order) + ";") is expected
    closed = task_repair_order_case("running", "done")
    assert run("REPAIRS={available:true,orders:{A:order}};repairsInFlight()", "const order=" + json.dumps(closed) + ";") is False
    source = module_source("task-operations.js")
    assert "REPAIR_POLL_MS=15000" in source
    assert "!document.hidden && visible && repairsInFlight()" in source
    assert "if(group==='automations') loadRepairs();" in module_source("navigation.js")


# ---------------------------------------------------------------- 路由、焦点、页面
def test_clicks_are_routed_before_the_run_details_row_toggle():
    source = module_source("events.js")
    first = source[:source.index('document.getElementById("side")')]
    toggle = source.index('const tr=e.target.closest("#tbl tbody tr[data-name]")')
    for attr, handler in (("data-repair-order", "openRepairOrder("), ("data-task-repair", "openTaskRepair("),
                          ("data-task-delete", "openTaskDelete(")):
        line = next(text for text in first.splitlines() if handler in text)
        assert "stopImmediatePropagation()" in line, attr
        assert f"closest('[{attr}]')" in first and first.index(f"closest('[{attr}]')") < toggle
    assert "data-retire" not in source and "retireTask" not in source
    assert "startTaskOperations();" in source


def test_focus_returns_to_the_same_row_control_after_a_rebuild():
    result = run("""(()=>{
      const el=(attrs)=>({attrs,getAttribute:k=>Object.hasOwn(attrs,k)?attrs[k]:null,hasAttribute:k=>Object.hasOwn(attrs,k),
        dataset:attrs['data-name']?{name:attrs['data-name']}:{},focus(){focused=this;}});
      let focused=null;
      const before=[el({'data-task-repair':'AcmeSync'}),el({'data-act':'run','data-name':'AcmeOther'}),el({'data-repair-order':'acme-repair-1','data-name':'AcmeSync'})];
      const after=[el({'data-task-delete':'AcmeSync'}),el({'data-task-repair':'AcmeOther'}),el({'data-task-repair':'AcmeSync'}),
        el({'data-act':'run','data-name':'AcmeSync'}),el({'data-act':'run','data-name':'AcmeOther'}),el({'data-repair-order':'acme-repair-1','data-name':'AcmeSync'})];
      const scope={querySelectorAll:selector=>after.filter(node=>node.hasAttribute(selector.slice(1,-1)))};
      return before.map(node=>{focused=null;restoreTaskControlFocus(scope,taskControlKey(node));return after.indexOf(focused);});
    })()""")
    assert result == [2, 4, 5]
    tasks, workbench, pipelines = module_source("panels/tasks.js"), module_source("workbench.js"), module_source("panels/pipelines.js")
    assert "control: taskControlKey(ae)" in tasks and "findTaskControl(row, keep.control)" in tasks
    assert "restoreTaskControlFocus($('automation-list'),focused)" in workbench
    assert "restoreTaskControlFocus(box,focused)" in pipelines


def test_dialogs_are_real_modals_and_every_icon_has_a_symbol():
    html = PAGE.read_text(encoding="utf-8")
    for dialog in ("task-delete-dialog", "repair-dialog"):
        assert f'<dialog id="{dialog}"' in html
    assert re.search(r'<button type="submit" id="task-delete-confirm" class="danger" disabled>', html)
    assert '<label for="task-delete-name">' in html and '<label for="task-delete-reason">' in html
    assert html.count("data-repair-read") == 3
    symbols = set(re.findall(r'<symbol id="(i-[a-z-]+)"', html))
    scripts = "\n".join(module_source(name) for name in json.loads(
        re.search(r"const CONSOLE_MODULES = (\[.*?\]);", module_source("app.js"), re.S).group(1)))
    used = set(re.findall(r'''(?:#|['"])(i-[a-z][a-z-]*)(?=['"/])''', html + scripts))
    assert {"i-repair", "i-trash"} <= used
    assert used <= symbols, sorted(used - symbols)
    assert "i-retire" not in symbols
