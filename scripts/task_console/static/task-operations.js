// Delete and repair for one scheduled task. Every decision is the backend's (task_delete.py,
// task_repair.py); this module lays the preview out, carries the confirmation back and reports
// the result in words that never turn "accepted" or "partly done" into "done".

// ================= 删除 ==============================================================
// 预览和执行是两步:预览只读,发一张一次性令牌;执行凭令牌,并要求手打出完整任务名。
// 令牌一交出去就作废(后端先把它弹掉),所以执行之后无论成败,这里都把预览一起丢掉。
let TASK_DELETE={name:null,plan:null,busy:false,expires:0};
// 控制器退役最多 300 秒,后续钩子最多 600 秒,执行前还要把整份预览重算一遍。
// api() 默认 360 秒就放弃等待,对删除来说太短:那时后端还在干活,页面却报超时。
const TASK_DELETE_WAIT_MS=20*60*1000;
// 预览里的步骤状态和结果里的步骤状态是两套说法:「将执行」不能和「已完成」共用一个词。
const TASK_DELETE_PLAN_STEPS={planned:['将执行','pending'],skipped:['无需修改或不会运行','muted'],blocked:['不能自动执行','warn'],
  ok:['可以执行','ok'],failed:['预览失败','bad']};
const TASK_DELETE_RESULT_STEPS={ok:['已完成','ok'],failed:['失败','bad'],skipped:['未执行','muted'],blocked:['未自动执行（需手动）','warn'],
  planned:['未执行','muted']};
// 后续钩子的五种结果各说各的话。「没配置」「拒绝了」「失败了」「读不出来」要做的事都不一样,
// 任何两种长得一样,人就会照着错的那一种去处理。
const TASK_FOLLOWUP_STATES={
  ok:{plan:['后续清理：已就绪','ok'],result:['后续清理：已完成','ok']},
  not_configured:{plan:['未配置后续清理：监控清单、备份和迁移计划不会被清理','warn'],result:['未配置后续清理：监控清单、备份和迁移计划没有清理','warn']},
  // 执行时的「拦下」发生在任务已经从计划程序删掉之后:那时说「拒绝了这次删除」,人会以为任务还在。
  blocked:{plan:['后续清理拒绝了这次删除','bad'],result:['后续清理没有运行：任务已删除，监控清单、备份和迁移计划都没有清理','bad']},
  failed:{plan:['后续清理预览失败','bad'],result:['后续清理没有全部完成','bad']},
  unreadable:{plan:['后续清理的结果读不出来','bad'],result:['后续清理的结果读不出来，请人工核对','bad']}
};
// unknown 是「删除之后读不出计划程序里还有没有它」:它可能已经没了,所以既不能说删了,也不能说没删。
const TASK_DELETE_OUTCOMES={ok:['已删除','ok'],partial:['部分完成：任务已从计划程序删除，清理没有全部完成','warn'],failed:['没有删除','bad'],
  unknown:['无法确认是否已删除，请刷新核对','bad']};

function taskDeleteStepHtml(step,phase){
  const table=phase==='plan'?TASK_DELETE_PLAN_STEPS:TASK_DELETE_RESULT_STEPS;
  const [label,tone]=table[step.status] || [`状态无法识别（${step.status || '未提供'}）`,'idle'];
  const files=step.files && typeof step.files==='object'?Object.values(step.files).filter(Boolean):[];
  return `<li><div class="task-op-step-head"><strong>${esc(step.title || step.id || '未命名步骤')}</strong>${statusBadge(label,tone)}</div>`+
    `<small>目标：<code>${esc(step.target || '未提供')}</code></small>`+
    (step.detail?`<p>${esc(step.detail)}</p>`:'')+
    (files.length?`<p>${files.map(file=>`<code>${esc(file)}</code>`).join('<br>')}</p>`:'')+'</li>';
}
function taskDeleteList(items){
  return `<ul>${items.map(text=>`<li>${esc(typeof text==='string'?text:JSON.stringify(text))}</li>`).join('')}</ul>`;
}
function taskFollowupHtml(followup,phase){
  if(!followup) return `<h3>后续清理</h3><p>${statusBadge(phase==='plan'?'后续清理没有预览':'后续清理没有运行','muted')}</p>`;
  const known=TASK_FOLLOWUP_STATES[followup.state];
  const [label,tone]=known?known[phase]:[`后续清理状态无法识别（${followup.state || '未提供'}）`,'bad'];
  const message=followup.message && !label.includes(followup.message)?`<p>${esc(followup.message)}</p>`:'';
  return `<h3>后续清理</h3><p>${statusBadge(label,tone)}</p>${message}`+
    (followup.blocking?.length?`<div class="task-op-blocking">${taskDeleteList(followup.blocking)}</div>`:'')+
    (followup.steps?.length?`<ol class="task-op-steps">${followup.steps.map(step=>taskDeleteStepHtml(step,phase)).join('')}</ol>`:'')+
    (followup.notes?.length?taskDeleteList(followup.notes):'');
}
function taskDeleteWarning(warning){
  let text=warning?.message || warning?.code || '未说明的提醒';
  if(warning?.code==='verdict_not_remove' && warning.verdict && Object.hasOwn(TASK_VERDICTS,warning.verdict))
    text+=`（当前建议：${TASK_VERDICTS[warning.verdict].label}）`;
  return `<p class="review-notice">${esc(text)}</p>`;
}
function taskDeletePlanHtml(plan){
  const actions=plan.task?.actions || [];
  const blocking=plan.blocking || [];
  return `<dl class="launch-facts"><dt>归属</dt><dd>${plan.managed?'任务控制器管理：通过控制器退役并删除':'不受任务控制器管理：先导出 XML 存档，再从计划程序删除'}</dd>
    <dt>当前状态</dt><dd>${esc(taskStateLabel(plan.task?.state))}</dd><dt>原因</dt><dd>${esc(plan.reason)}</dd></dl>`+
    // 拦下删除的原因放最前面而且醒目:确认按钮为什么按不动,答案必须在人第一眼看的地方。
    (blocking.length?`<div class="task-op-blocking" role="alert"><strong>现在不能删除：</strong>${taskDeleteList(blocking)}</div>`:'')+
    `<h3>将执行的步骤</h3><ol class="task-op-steps">${(plan.steps || []).map(step=>taskDeleteStepHtml(step,'plan')).join('')}</ol>`+
    (plan.authority?.changes?.length?`<h3>控制器将改写</h3><ul>${plan.authority.changes.map(change=>`<li><code>${esc(change.path)}</code> · ${esc(change.operation)}</li>`).join('')}</ul>`:'')+
    taskFollowupHtml(plan.followup,'plan')+
    `<h3>任务运行的程序（删除前已记录）</h3>`+(actions.length?`<ul>${actions.map(action=>`<li><code>${esc([action.execute,action.arguments].filter(Boolean).join(' ') || '未提供')}</code>${action.workingDirectory?` <small>工作目录 <code>${esc(action.workingDirectory)}</code></small>`:''}</li>`).join('')}</ul>`:'<p>没有读到动作</p>')+
    (plan.notes?.length?`<h3>需要手动处理</h3>${taskDeleteList(plan.notes)}`:'')+
    (plan.warnings || []).map(taskDeleteWarning).join('')+
    (plan.token?`<p>预览 ${Math.round((plan.expiresIn || 300)/60)} 分钟内有效，只能用一次；任务、分类配置或后续清理有变化时需要重新预览。</p>`:'');
}
function taskDeleteResultHtml(result){
  const [label,tone]=TASK_DELETE_OUTCOMES[result.status] || ['结果无法识别，请刷新核对','bad'];
  const authority=result.authority;
  return `<p>${statusBadge(label,tone)}</p>${result.message?`<p>${esc(result.message)}</p>`:''}`+
    (result.remaining?.length?`<div class="task-op-blocking"><strong>还没完成，需要处理：</strong>${taskDeleteList(result.remaining)}</div>`:'')+
    `<h3>各步骤</h3><ol class="task-op-steps">${(result.steps || []).map(step=>taskDeleteStepHtml(step,'result')).join('')}</ol>`+
    (authority?`<p>控制器事务 <code>${esc(authority.transactionId || '未提供')}</code> · ${esc(authority.status || '状态未提供')}</p>`+
      (authority.files?.length?taskDeleteList(authority.files):''):'')+
    taskFollowupHtml(result.followup,'result')+taskFollowupRetryHtml(result.followupRetry)+
    (result.notes?.length?`<h3>需要手动处理</h3>${taskDeleteList(result.notes)}`:'');
}
// 任务已经不在册,删除对话框不能再预览它;单独重跑后续清理只能在终端里把同一份请求原样喂给钩子。
// 请求原文要能整段复制:少一个字段,钩子会按协议读不懂并拦下。
function taskFollowupRetryHtml(retry){
  if(!retry || typeof retry!=='object' || !retry.command) return '';
  return `<h3>单独重跑后续清理</h3>${retry.howto?`<p>${esc(retry.howto)}</p>`:''}<pre><code>${esc(retry.command)}</code></pre>`+
    `<details><summary>后续清理请求（原样存成 UTF-8 文件）</summary><pre><code>${esc(JSON.stringify(retry.request ?? null,null,2))}</code></pre></details>`;
}
function taskDeleteReady(){
  const plan=TASK_DELETE.plan;
  return !TASK_DELETE.busy && !ConsoleActions.readOnly && !!plan && plan.applicable===true && !!plan.token &&
    !(plan.blocking || []).length && Date.now()<TASK_DELETE.expires && $('task-delete-name').value===plan.name;
}
function syncTaskDeleteConfirm(){
  $('task-delete-confirm').disabled=!taskDeleteReady();
  $('task-delete-preview').disabled=TASK_DELETE.busy || ConsoleActions.readOnly;
}
function openTaskDelete(name){
  if(!ConsoleActions.allowWrite() || TASK_DELETE.busy) return;
  const row=ROWS.find(task=>task.name===name);
  TASK_DELETE={name,plan:null,busy:false,expires:0};
  $('task-delete-subject').innerHTML=`<dl class="launch-facts"><dt>任务</dt><dd>${esc(row?taskText(row).title:name)}</dd><dt>任务名</dt><dd><code>${esc(name)}</code></dd></dl>`;
  $('task-delete-body').innerHTML='<p>写明原因后生成预览。预览会列出每一步要改哪里；确认之前什么都不会改。</p>';
  $('task-delete-reason').value='';$('task-delete-name').value='';$('task-delete-state').textContent='';
  $('task-delete-cancel').textContent='取消';
  syncTaskDeleteConfirm();$('task-delete-dialog').showModal();
}
async function previewTaskDelete(){
  const name=TASK_DELETE.name, reason=$('task-delete-reason').value.trim();
  if(!name || TASK_DELETE.busy || !ConsoleActions.allowWrite()) return;
  if(!reason){$('task-delete-state').textContent='请先写明删除原因，它会写进墓碑或存档回执。';return;}
  TASK_DELETE.busy=true;TASK_DELETE.plan=null;syncTaskDeleteConfirm();
  $('task-delete-state').textContent='正在核对任务、分类配置和后续清理…';
  try{
    const plan=await api('/api/task/delete/plan',{method:'POST',body:JSON.stringify({name,reason})});
    if(TASK_DELETE.name!==name || !$('task-delete-dialog').open) return;
    // 等预览的这几秒里原因被改过:回来的这份预览写的是旧原因,不能拿来确认。
    if($('task-delete-reason').value.trim()!==reason){$('task-delete-state').textContent='原因已修改，请重新生成预览。';return;}
    TASK_DELETE.plan=plan;TASK_DELETE.expires=Date.now()+(plan.expiresIn || 0)*1000;
    $('task-delete-body').innerHTML=taskDeletePlanHtml(plan);
    $('task-delete-state').textContent=(plan.blocking || []).length?'预览列出了不能删除的原因，确认按钮保持禁用。':'输入完整任务名后才能确认删除。';
  }catch(error){$('task-delete-state').textContent=error.message;}
  finally{TASK_DELETE.busy=false;syncTaskDeleteConfirm();}
}
async function applyTaskDelete(){
  if(!taskDeleteReady() || !ConsoleActions.allowWrite()) return;
  const plan=TASK_DELETE.plan;
  TASK_DELETE.busy=true;TASK_DELETE.plan=null;syncTaskDeleteConfirm();
  $('task-delete-state').textContent='正在删除，请勿关闭页面或重复提交；控制器和后续清理可能要几分钟…';
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),TASK_DELETE_WAIT_MS);
  try{
    const result=await api('/api/task/delete/apply',{method:'POST',body:JSON.stringify({token:plan.token,name:plan.name}),signal:controller.signal});
    showTaskDeleteResult(result);
  }catch(error){
    // 计划程序那一步失败是 HTTP 500,但正文仍是一份完整的结果:照结果画,不只画一句错误。
    if(Object.hasOwn(TASK_DELETE_OUTCOMES,error.payload?.status || '')) showTaskDeleteResult(error.payload);
    else{
      $('task-delete-state').textContent=error.message+'。这份预览已作废，需要重新预览。';
      toast(`${plan.name}：${error.message}`,'bad');
    }
  }finally{
    clearTimeout(timer);TASK_DELETE.busy=false;syncTaskDeleteConfirm();
    // 无论成败都重读:部分完成和失败同样可能已经改了东西,而那一行要按计划程序的真实状态消失或留下。
    await load();
  }
}
function showTaskDeleteResult(result){
  $('task-delete-body').innerHTML=taskDeleteResultHtml(result);
  const [label,tone]=TASK_DELETE_OUTCOMES[result.status] || ['结果无法识别，请刷新核对','bad'];
  $('task-delete-state').textContent=label;$('task-delete-cancel').textContent='关闭';
  toast(`${result.name || TASK_DELETE.name}：${result.message || label}`,tone==='ok'?'ok':'bad');
}

// ================= 修复 ==============================================================
// 修复只是开一张工单交给已有的 Agent 执行通道,它不改任务本身。页面上任何一处都不能把
// 「已受理」说成「已修好」:工单交出去之后,诊断结论出现在工作记录和 Discord 里。
let TASK_REPAIR={name:null,preview:null,busy:false,done:false};
const TASK_REPAIR_INTENTS=new Map();
// 同一次点击的重试必须带同一个 request_id,哪怕中间刷新了页面:后端靠它认出「这是同一个请求」,
// 不再开第二张单。所以请求号和备注一起存进会话存储,确定有了结论才清掉。
function taskRepairIntent(name,note){
  const key='tc.repair.'+name;
  let intent=TASK_REPAIR_INTENTS.get(key);
  if(!intent){
    try{intent=JSON.parse(sessionStorage.getItem(key) || 'null');}catch(error){}
    if(!intent){
      intent={name,note,request_id:'repair-'+crypto.randomUUID()};
      sessionStorage.setItem(key,JSON.stringify(intent));
      if(sessionStorage.getItem(key)!==JSON.stringify(intent)) throw new Error('request storage unavailable');
    }
    TASK_REPAIR_INTENTS.set(key,intent);
  }
  return {key,intent};
}
function taskRepairPendingIntent(name){
  const key='tc.repair.'+name;
  if(TASK_REPAIR_INTENTS.has(key)) return TASK_REPAIR_INTENTS.get(key);
  try{return JSON.parse(sessionStorage.getItem(key) || 'null');}catch(error){return null;}
}
function forgetTaskRepairIntent(key){
  TASK_REPAIR_INTENTS.delete(key);
  try{sessionStorage.removeItem(key);}catch(error){}
}
function taskRepairFactsHtml(preview){
  const facts=preview.facts || {}, info=facts.info || {};
  const value=(v,missing)=>v!=null && v!==''?esc(v):`<span class="u">${missing}</span>`;
  const verdict=TASK_VERDICTS[info.verdict];
  const failures=facts.recentFailures, polls=facts.polls;
  const rows=[
    ['任务',`${esc(info.title || facts.name || preview.name)} <code>${esc(preview.name)}</code>`],
    ['机主建议',(verdict?statusBadge(verdict.label,verdict.tone,verdict.symbol,'task-verdict')+' ':'')+value(info.advice,'未填写建议')+(info.asOf?`<small class="task-info-date">核对于 ${esc(info.asOf)}</small>`:'')],
    ['状态',`${esc(taskStateLabel(facts.state))}${facts.status?.label?' · '+esc(facts.status.label):''}`],
    ['上次运行',`${value(facts.lastRun,'从未运行')}${facts.lastResult?.meaning?' · '+esc(facts.lastResult.meaning):''}`],
    ['下次运行',value(facts.nextRun,'没有下次运行时间')+(facts.missedRuns?` · 错过 ${esc(facts.missedRuns)} 次`:'')],
    ['计划',value(facts.triggers,'未记录触发计划')],
    ['动作',(facts.actions || []).length?facts.actions.map(action=>`<code>${esc([action.execute,action.arguments].filter(Boolean).join(' '))}</code>`).join('<br>'):'<span class="u">没有读到动作</span>'],
    ['产物',facts.artifact?.path?`<code>${esc(facts.artifact.path)}</code>${facts.artifact.maxAgeHours!=null?` · 最长 ${esc(facts.artifact.maxAgeHours)} 小时`:''}`:'<span class="u">未声明</span>'],
    ['健康检查',(facts.health || []).length?facts.health.map(entry=>`${esc(entry.label || entry.check || '检查')}：${esc(FR_LABEL[entry.state] || entry.state || '未知')}${(entry.reasons || []).length?' · '+esc(entry.reasons.join('；')):''}`).join('<br>'):'<span class="u">没有健康检查记录</span>'],
    ['近期运行',failures?`${esc(failures.window || '')} 启动 ${esc(failures.starts ?? '-')} 次，成功率 ${esc(failures.successRate ?? '-')}%`+((failures.failingCodes || []).length?`；失败码 ${failures.failingCodes.map(code=>esc(code.code)+' × '+esc(code.count)).join('、')}`:''):'<span class="u">运行日志里没有记录</span>'],
    ['轮询观察',polls?`正常 ${esc(polls.ok)} · 失败 ${esc(polls.bad)} · 陈旧 ${esc(polls.stale)} / 共 ${esc(polls.obs)} 条`:'<span class="u">没有观察记录</span>'],
    ['问题',(facts.issues || []).length?facts.issues.map(issue=>esc(issue.text || issue)).join('<br>'):'<span class="u">没有记录问题</span>'],
    ['读取于',value(facts.observedAt,'未记录')]
  ];
  return `<dl class="launch-facts">${rows.map(([label,html])=>`<dt>${label}</dt><dd>${html}</dd>`).join('')}</dl>`+
    `<h3>Agent 必须遵守</h3><ul>${(preview.limits || []).map(line=>`<li>${esc(line)}</li>`).join('')}</ul>`+
    '<p>以上事实会作为参考数据写进工单。Agent 只在副本上诊断并提出修复方案，不会改动这个任务本身。</p>';
}
function taskRepairBlocker(preview){
  if(!preview) return '';
  // 只有已经交给 Agent 的工单才挡住提交。建好了却从没提交成功的(执行服务没连上、上次没收到确认),
  // 再点一次正是后端为它补交的那条路;在这里挡住,那条路就永远走不到,芯片上「再点一次修复会补交」也成了空话。
  if(preview.existing?.action) return 'existing';
  if(preview.work?.available===false) return 'work';
  return '';
}
function syncTaskRepairSubmit(){
  $('repair-submit').disabled=TASK_REPAIR.busy || TASK_REPAIR.done || ConsoleActions.readOnly ||
    !TASK_REPAIR.preview || !!taskRepairBlocker(TASK_REPAIR.preview);
}
function taskRepairOrderLink(itemId,name,label='查看工单'){
  return itemId?`<button class="record-link" data-repair-order="${esc(itemId)}" data-name="${esc(name)}">${esc(label)}</button>`:'';
}
async function openTaskRepair(name){
  if(!ConsoleActions.allowWrite() || TASK_REPAIR.busy) return;
  TASK_REPAIR={name,preview:null,busy:true,done:false};
  const row=ROWS.find(task=>task.name===name);
  $('repair-title').textContent=(row?taskText(row).title:name)+' · 修复工单';
  $('repair-body').innerHTML='<p>正在读取这个任务的事实…</p>';$('repair-state').textContent='';$('repair-submit').textContent='提交修复工单';
  const pending=taskRepairPendingIntent(name);
  // 上次提交没收到确认时,备注锁定成上次那份:同一个请求号配一份不同的备注,待办服务会判成冲突。
  $('repair-note').value=pending?.note || '';$('repair-note').readOnly=!!pending;
  if(pending) $('repair-state').textContent='上次提交没有收到确认。再次提交会核对原请求，不会重复建立工单。';
  syncTaskRepairSubmit();$('repair-dialog').showModal();
  try{
    const preview=await api('/api/task/repair/preview',{method:'POST',body:JSON.stringify({name})});
    if(TASK_REPAIR.name!==name || !$('repair-dialog').open) return;
    TASK_REPAIR.preview=preview;
    const blocker=taskRepairBlocker(preview), resubmit=!blocker && !!preview.existing;
    if(resubmit) $('repair-submit').textContent='为现有工单补交 Agent 处理';
    $('repair-body').innerHTML=(resubmit?`<p class="review-notice">这个任务已有一张修复工单，但还没有交给 Agent（${esc(taskRepairStatus(preview.existing).label)}）。提交会为这张工单补交 Agent 处理，不会新建。${taskRepairOrderLink(preview.existing.item_id,name)}</p>`:
      blocker==='existing'?`<p class="review-notice">这个任务已有修复工单（${esc(taskRepairStatus(preview.existing).label)}），不再新建。${taskRepairOrderLink(preview.existing.item_id,name)}</p>`:
      blocker==='work'?`<p class="review-notice">工作记录服务尚未连接，无法提交修复工单${preview.work.reason?'（'+esc(preview.work.reason)+'）':''}。</p>`:'')+taskRepairFactsHtml(preview);
  }catch(error){$('repair-body').innerHTML='';$('repair-state').textContent=error.message;}
  finally{TASK_REPAIR.busy=false;syncTaskRepairSubmit();}
}
function taskRepairReplyText(reply){
  if(reply.ok){
    const accepted='已受理：结果会出现在工作记录页和 Discord，任务本身没有被改动，也还没有修好。';
    if(reply.existing && !reply.receipt) return {tone:'warn',text:'这个任务已有修复工单，没有重复建立。'};
    const queued=reply.receipt?.status==='queued' && reply.receipt?.wakeup===false?'已排队，等待执行服务接手。':'';
    return {tone:'warn',text:(reply.existing?'已为现有修复工单提交 Agent 处理。':'修复工单已建立并交给 Agent 处理。')+queued+accepted};
  }
  if(reply.uncertain) return {tone:'warn',text:`${reply.message || '未收到确认'}。未收到确认，再次点击会核对原请求，不会重复建立工单。`};
  return {tone:'bad',text:apiError(reply,500)};
}
async function submitTaskRepair(){
  const name=TASK_REPAIR.name;
  if(!name || TASK_REPAIR.busy || TASK_REPAIR.done || !TASK_REPAIR.preview || taskRepairBlocker(TASK_REPAIR.preview) || !ConsoleActions.allowWrite()) return;
  let selected;
  try{selected=taskRepairIntent(name,$('repair-note').value.trim());}
  catch(error){$('repair-state').textContent='浏览器未能保存请求，请允许本站使用会话存储后再试';return;}
  const {key,intent}=selected;
  TASK_REPAIR.busy=true;syncTaskRepairSubmit();$('repair-state').textContent='正在提交…';
  try{
    const reply=await api('/api/task/repair',{method:'POST',body:JSON.stringify({name,note:intent.note || null,request_id:intent.request_id})});
    if(reply.ok || reply.uncertain===false) forgetTaskRepairIntent(key);
    const {tone,text}=taskRepairReplyText(reply);
    TASK_REPAIR.done=reply.ok===true;
    $('repair-state').innerHTML=esc(text)+' '+taskRepairOrderLink(reply.item_id,name,reply.ok?'查看工单':'查看已建立的工单');
    $('repair-note').readOnly=!TASK_REPAIR.done && !!taskRepairPendingIntent(name);
    toast(`${name}：${text}`,tone==='bad'?'bad':'ok');
  }catch(error){
    if(error.requestRejected || error.payload?.uncertain===false){forgetTaskRepairIntent(key);$('repair-state').textContent=error.message;}
    else{$('repair-note').readOnly=true;$('repair-state').textContent=`${error.message}。未收到确认，再次点击会核对原请求，不会重复建立工单。`;}
  }finally{
    TASK_REPAIR.busy=false;syncTaskRepairSubmit();
    // 工单号要能点开:工作记录里没有它的话,「查看工单」只会弹一句未载入。
    await Promise.allSettled([loadRepairs(),loadWork()]);
  }
}
async function openRepairOrder(id){
  if(!id) return;
  if($('repair-dialog').open && !TASK_REPAIR.busy) $('repair-dialog').close();
  if(!(WORK?.items || []).some(row=>row.id===id)) await loadWork();
  openWorkRecord(id);
}

// ================= 修复进度 ==========================================================
// REPAIRS 有三种「没有芯片」,彼此不能混:null 是还没读过,available=false 是读不到,
// available=true 而这一行不在表里才是真没有工单。读不到时整页说一句「未读取」,
// 而不是让每一行看起来都像没有工单。
let REPAIRS=null, REPAIRS_ERROR=null, REPAIRS_PENDING=null, REPAIRS_DRAWN='';
const REPAIR_ACTIVE_TODOS=['pending','doing','blocked','snoozed'];
const REPAIR_IN_FLIGHT=['preparing','queued','dispatching','task_requested','running','reconcile'];
// 芯片说的是工单走到哪了,不是任务好没好:Agent 做完也只是交出了诊断和方案,所以没有「已修好」这个说法。
const REPAIR_ACTION_STATES={
  preparing:['修复排队中','pending','准备执行环境'],queued:['修复排队中','pending','排队等待 Agent'],
  dispatching:['修复排队中','pending','正在提交给执行服务'],task_requested:['修复排队中','pending','已提交，等待执行服务确认'],
  running:['修复中','active','Agent 正在诊断'],reconcile:['修复待核实','warn','正在确认执行进程是否已退出'],
  done:['修复方案已出','ok','Agent 已交出诊断和修复方案（见工单里的 report.md）；任务本身没有被改动'],
  failed:['修复失败','bad','Agent 这次没有完成'],stopped:['修复已停止','muted','这次处理已停止'],stalled:['修复停滞','warn','这次处理停滞了']
};
const REPAIR_POLL_MS=15000;
function taskRepairStatus(order){
  const action=order?.action, active=REPAIR_ACTIVE_TODOS.includes(order?.state);
  if(['done','cancelled'].includes(order?.state)) return {label:'修复已关闭',tone:'muted',title:'上一张修复工单已关闭'};
  if(!action){
    if(active) return {label:'修复未开始',tone:'warn',title:'修复工单已建立，但还没有交给 Agent；再点一次修复会补交'};
    return {label:'修复状态未知',tone:'idle',title:`读到了修复工单，但它的状态认不出（${order?.state || '未提供'}）`};
  }
  const known=REPAIR_ACTION_STATES[action.state];
  if(!known) return {label:'修复状态未知',tone:'idle',title:`Agent 处理状态认不出（${action.state || '未提供'}）`};
  return {label:known[0],tone:known[1],title:known[2]};
}
function taskRepairOrderFor(name){
  return REPAIRS?.available && REPAIRS.orders && Object.hasOwn(REPAIRS.orders,name)?REPAIRS.orders[name]:null;
}
function taskRepairChip(name){
  const order=taskRepairOrderFor(name);
  if(!order) return '';
  const status=taskRepairStatus(order), summary=order.action?.summary;
  const title=status.title+(summary?'：'+summary:'')+(order.item_id?'。点击查看工单':'');
  const badge=statusBadge(status.label,status.tone,undefined,'task-repair');
  return order.item_id?`<button class="repair-chip" data-repair-order="${esc(order.item_id)}" data-name="${esc(name)}" title="${esc(title)}">${badge}</button>`:
    `<span class="repair-chip" title="${esc(title)}">${badge}</span>`;
}
function taskRepairReadState(){
  if(REPAIRS_ERROR) return {text:'修复工单状态读取失败：'+REPAIRS_ERROR,tone:'bad'};
  if(!REPAIRS) return {text:REPAIRS_PENDING?'正在读取修复工单':'尚未读取修复工单',tone:'pending'};
  if(!REPAIRS.available) return {text:'修复工单状态未读取'+(REPAIRS.reason?'（'+REPAIRS.reason+'）':''),tone:'warn'};
  const orders=Object.values(REPAIRS.orders || {});
  if(!orders.length) return {text:'没有修复工单',tone:'muted'};
  const open=orders.filter(order=>REPAIR_ACTIVE_TODOS.includes(order.state)).length;
  return {text:`修复工单 ${orders.length} 张${open?'，未关闭 '+open:''}`,tone:'muted'};
}
function renderRepairReadState(){
  const state=taskRepairReadState();
  document.querySelectorAll?.('[data-repair-read]').forEach(el=>{el.textContent=state.text;el.dataset.tone=state.tone;});
}
function repairsInFlight(){
  return !!REPAIRS?.available && Object.values(REPAIRS.orders || {}).some(order=>
    REPAIR_ACTIVE_TODOS.includes(order.state) && REPAIR_IN_FLIGHT.includes(order.action?.state));
}
function loadRepairs(){
  if(REPAIRS_PENDING) return REPAIRS_PENDING;
  REPAIRS_PENDING=(async()=>{
    renderRepairReadState();
    try{REPAIRS=await api('/api/task/repairs');REPAIRS_ERROR=null;}
    // 读失败就不留旧芯片:一张停在上次的「修复中」看起来和正在修一模一样。
    catch(error){REPAIRS=null;REPAIRS_ERROR=error.message || '读取失败';}
    finally{REPAIRS_PENDING=null;}
    renderRepairReadState();
    // 只有工单真的变了才重画任务列表。整块重画会把正在用的控件换掉,每 15 秒一次的轮询不该打断人。
    const drawn=JSON.stringify([REPAIRS,REPAIRS_ERROR]);
    if(drawn!==REPAIRS_DRAWN){
      REPAIRS_DRAWN=drawn;
      renderAutomations();if(DATA) render();renderPipelines();
    }
  })();
  return REPAIRS_PENDING;
}
// 原因写进了预览;改了原因,旧预览就不再是这次要执行的东西。
function taskDeleteReasonChanged(){
  if(TASK_DELETE.plan){TASK_DELETE.plan=null;$('task-delete-body').innerHTML='<p>原因已修改，请重新生成预览。</p>';}
  syncTaskDeleteConfirm();
}
function startTaskOperations(){
  $('task-delete-reason').addEventListener('input',taskDeleteReasonChanged);
  $('task-delete-name').addEventListener('input',syncTaskDeleteConfirm);
  $('task-delete-preview').addEventListener('click',previewTaskDelete);
  $('task-delete-confirm').addEventListener('click',applyTaskDelete);
  $('task-delete-cancel').addEventListener('click',()=>{if(!TASK_DELETE.busy){TASK_DELETE.plan=null;$('task-delete-dialog').close();}});
  $('task-delete-dialog').addEventListener('cancel',event=>{if(TASK_DELETE.busy) event.preventDefault();else TASK_DELETE.plan=null;});
  $('repair-submit').addEventListener('click',submitTaskRepair);
  $('repair-cancel').addEventListener('click',()=>{if(!TASK_REPAIR.busy) $('repair-dialog').close();});
  $('repair-dialog').addEventListener('cancel',event=>{if(TASK_REPAIR.busy) event.preventDefault();});
  let timer;
  const tick=async()=>{
    const visible=viewGroup(CURVIEW)==='automations';
    if(!document.hidden && visible && repairsInFlight()) await loadRepairs();
    timer=setTimeout(tick,REPAIR_POLL_MS);
  };
  timer=setTimeout(tick,REPAIR_POLL_MS);
  window.addEventListener('pagehide',()=>clearTimeout(timer),{once:true});
  renderRepairReadState();
}
