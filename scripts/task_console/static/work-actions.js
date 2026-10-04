// Offers and targets belong to the reminder owner. This module only presents them.
const WORK_ACTION_PENDING=new Map(), WORK_ACTION_INTENTS=new Map();
const WORK_SOURCE_CONTEXT=new Map();
const WORK_ACTION_STATES={preparing:['准备中','pending'],queued:['排队中','pending'],running:['正在处理','active'],
  done:['已完成','ok'],failed:['未完成','bad'],stalled:['已暂停','warn'],review_unavailable:['待复核','warn'],
  stopped:['已请求停止','muted'],reconcile:['结果待核实','warn'],dispatching:['正在提交','pending'],task_requested:['已提交任务','pending']};
// 行上的每个动作都写出字来:以前一排图标里,▷ 是交给 Agent、圆圈勾是标记完成、气泡是原对话,
// 而同一只眼睛图标在四个地方各指一件事。图标留着帮人扫,意思靠字说。
const workIcon=icon=>icon?`<svg class="ic" aria-hidden="true"><use href="#${icon}"/></svg>`:'';
function workActionButtons(item,compact=false){
  const actions=item.actions;if(!actions) return item.origin_item_id?`<div class="work-actions"><button type="button" class="mini work-link" data-work-id="${esc(item.origin_item_id)}" title="打开这项工作由之而来的那条待办">查看原待办</button></div>`:'';
  if(!actions.available) return actions.reason?`<div class="work-actions"><small>${esc(actions.reason)}</small></div>`:'';
  const current=actions.current, pending=WORK_ACTION_PENDING.has(item.id), readOnly=ConsoleActions.readOnly;
  const controls=(actions.offers || []).slice(0,3).map((offer,index)=>{
    const disabled=readOnly || pending || !offer.enabled;
    const reason=readOnly?ConsoleActions.reason:pending?'正在提交':offer.reason || offer.description || '';
    const complete=offer.kind==='complete';
    const icon={complete:'i-on',agent:'i-play',task:'i-play'}[offer.kind];
    const title=disabled?disabledTitle(offer.label,reason || '暂不可用'):offer.label+(reason?'：'+reason:'');
    return `<button type="button" class="mini work-action${complete?' complete':index===0?' primary':''}" data-work-item="${esc(item.id)}" data-work-action="${esc(offer.id)}" ${disabled?'disabled':''} title="${esc(title)}" aria-label="${esc(offer.label)}">${workIcon(icon)}<span>${esc(offer.label)}</span></button>`;
  });
  if(current){
    const [label,tone]=WORK_ACTION_STATES[current.state] || ['状态待确认','idle'];
    controls.unshift(statusBadge(label,tone));
    // 「查看结果」和「查看进度」打开的是执行这件事的那条工作记录,不是这一行自己,所以字面上说清看的是什么。
    if(current.work_item_id) controls.push(`<button type="button" class="mini work-link" data-work-id="${esc(current.work_item_id)}" title="打开执行这件事的工作记录">${current.state==='done'?'查看结果':'查看进度'}</button>`);
    if(current.kind==='agent' && ['preparing','queued','running','reconcile'].includes(current.state)) controls.push(`<button type="button" class="mini work-action stop" data-work-item="${esc(item.id)}" data-work-stop="${esc(current.id)}" ${readOnly || pending?'disabled':''} title="${esc(readOnly?disabledTitle('停止',ConsoleActions.reason):pending?disabledTitle('停止','正在提交'):'停止这次处理，保留待办')}" aria-label="停止">${workIcon('i-stop')}<span>停止</span></button>`);
  }
  for(const link of actions.links || []){
    if(link.kind==='session') controls.push(`<button type="button" class="mini work-link" data-work-context="${esc(item.id)}" title="在详情里读这件事的原对话摘录">${workIcon('i-chat')}<span>原对话</span></button>`);
    if(link.kind==='task'){
      const task=COMPONENTS?.tasks?.find(row=>row.task_id===link.id);
      if(task?.name) controls.push(`<button type="button" class="mini work-link" data-task="${esc(task.name)}" title="${esc('打开关联的计划任务 '+task.name)}">${workIcon('i-link')}<span>关联任务</span></button>`);
    }
  }
  const note=current?.summary || (current?.state==='task_requested'?'运行请求已提交，完成结果尚未确认':'');
  return controls.length || note?`<div class="work-actions${compact?' compact':''}"><div class="work-action-controls">${controls.join('')}</div>${note?`<small class="work-action-note" title="${esc(note)}">${esc(note)}</small>`:''}</div>`:'';
}
function workActionIntent(item,actionId,verb){
  const key=JSON.stringify([item.id,actionId,verb]);
  let intent=WORK_ACTION_INTENTS.get(key);
  if(!intent){
    // Retain the exact payload across uncertain replies, including a page reload.
    try{intent=JSON.parse(sessionStorage.getItem('tc.action.'+key) || 'null');}catch(error){}
    if(!intent){
      intent={item_id:item.id,action_id:actionId,revision:item.actions.revision,request_id:crypto.randomUUID()};
      sessionStorage.setItem('tc.action.'+key,JSON.stringify(intent));
      if(sessionStorage.getItem('tc.action.'+key)!==JSON.stringify(intent)) throw new Error('request storage unavailable');
    }
    WORK_ACTION_INTENTS.set(key,intent);
  }
  return {key,intent};
}
async function sendWorkAction(itemId,actionId,verb){
  if(!ConsoleActions.allowWrite() || WORK_ACTION_PENDING.has(itemId)) return;
  const item=(WORK?.items || []).find(row=>row.id===itemId);
  if(!item?.actions?.revision){toast('请先刷新待办','bad');return;}
  let selected;
  try{selected=workActionIntent(item,actionId,verb);}
  catch(error){toast('浏览器未能保存请求，请允许本站使用会话存储后再试','bad');return;}
  const {key,intent}=selected;
  WORK_ACTION_PENDING.set(itemId,true);renderWorkPlatform();
  const forget=()=>{WORK_ACTION_INTENTS.delete(key);try{sessionStorage.removeItem('tc.action.'+key);}catch(error){}};
  try{
    const reply=await api('/api/work/'+verb,{method:'POST',body:JSON.stringify(intent)});
    if(reply.ok){forget();toast(verb==='stop'?'已请求停止':actionId==='complete' && reply.status==='done'?'待办已标记完成':reply.status==='queued'?'已加入队列':reply.status==='task_requested'?'已提交任务，结果待确认':'已找到这次处理记录',actionId==='complete' && reply.status==='done'?'ok':'info');}
    else{if(reply.uncertain===false) forget();toast(reply.message || '未能提交，请刷新后重试','bad');}
    if(reply.wakeup===false && reply.status==='queued') toast('已排队，等待执行服务接手','bad');
  }catch(error){
    if(error.requestRejected || error.payload?.uncertain===false){forget();toast(error.message,'bad');}
    else toast(`${error.message}。未收到确认。再次点击会核对原请求，不会重复创建工作。`,'bad');
  }
  finally{WORK_ACTION_PENDING.delete(itemId);await loadWork();if($('work-detail').open && WORK_DETAIL_ID===itemId) openWorkRecord(itemId,true);}
}
const submitWorkAction=(itemId,actionId)=>sendWorkAction(itemId,actionId,'action');
const stopWorkAction=(itemId,actionId)=>sendWorkAction(itemId,actionId,'stop');
function startWorkActions(){
  document.addEventListener('click',event=>{
    const start=event.target.closest('[data-work-action]'), stop=event.target.closest('[data-work-stop]');
    if(start && !start.disabled) submitWorkAction(start.dataset.workItem,start.dataset.workAction);
    if(stop && !stop.disabled) stopWorkAction(stop.dataset.workItem,stop.dataset.workStop);
    const source=event.target.closest('[data-work-context]');
    if(source) openWorkContext(source.dataset.workContext);
  });
  let timer;
  const refresh=async()=>{
    const visible=['overview','work'].includes(CURVIEW) || $('work-detail').open;
    if(!document.hidden && visible && (WORK?.items || []).some(item=>['preparing','queued','running','dispatching','reconcile','task_requested'].includes(item.actions?.current?.state))){
      await loadWork();if($('work-detail').open && WORK_DETAIL_ID) openWorkRecord(WORK_DETAIL_ID,true);
    }
    timer=setTimeout(refresh,8000);
  };
  timer=setTimeout(refresh,8000);
  window.addEventListener('pagehide',()=>clearTimeout(timer),{once:true});
}
async function openWorkContext(itemId){
  openWorkRecord(itemId);
  try{
    const result=await api('/api/work/context?item_id='+encodeURIComponent(itemId));
    if(WORK_DETAIL_ID!==itemId || !$('work-detail').open) return;
    if(!result.available){toast(result.reason || '原对话暂时不可用','bad');return;}
    WORK_SOURCE_CONTEXT.set(itemId,result.text);
    openWorkRecord(itemId,true);
    $('work-source-context').scrollIntoView({block:'nearest'});
  }catch(error){toast('读取原对话失败，请稍后重试','bad');}
}
function workSourceContext(itemId){
  const text=WORK_SOURCE_CONTEXT.get(itemId);
  return text?'<section id="work-source-context"><h3>原对话摘录</h3><pre class="record-summary">'+esc(text)+'</pre></section>':'';
}
