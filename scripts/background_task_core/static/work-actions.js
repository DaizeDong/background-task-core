// Offers and targets belong to the reminder owner. This module only presents them.
const WORK_ACTION_PENDING=new Map(), WORK_ACTION_INTENTS=new Map();
const WORK_ACTION_STATES={preparing:['准备中','pending'],queued:['排队中','pending'],running:['正在处理','active'],
  done:['已完成','ok'],failed:['未完成','bad'],stalled:['已暂停','warn'],review_unavailable:['待复核','warn'],
  stopped:['已请求停止','muted'],reconcile:['结果待核实','warn'],dispatching:['正在提交','pending'],task_requested:['已提交任务','pending']};
// 行上的每个动作都写出字来:以前一排图标里,▷ 是交给 Agent、圆圈勾是标记完成,
// 而同一只眼睛图标在四个地方各指一件事。图标留着帮人扫,意思靠字说。
const workIcon=icon=>icon?`<svg class="ic" aria-hidden="true"><use href="#${icon}"/></svg>`:'';
function workActionButtons(item,compact=false){
  const actions=item.actions;if(!actions) return item.origin_item_id?`<div class="work-actions"><button type="button" class="mini work-link" data-work-id="${esc(item.origin_item_id)}" title="打开这项工作由之而来的那条待办">查看原待办</button></div>`:'';
  const source=(actions.links || []).some(link=>link.kind==='session')
    ?`<button type="button" class="mini icon-only" data-work-context="${esc(item.id)}" title="查看原对话与片段" aria-label="查看原对话与片段">${workIcon('i-link')}</button>`:'';
  if(!actions.available) return `<div class="work-actions">${source}${actions.reason?`<small>${esc(actions.reason)}</small>`:''}</div>`;
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
    if(link.kind==='task'){
      const task=COMPONENTS?.tasks?.find(row=>row.task_id===link.id);
      if(task?.name) controls.push(`<button type="button" class="mini work-link" data-task="${esc(task.name)}" title="${esc('打开关联的计划任务 '+task.name)}">${workIcon('i-link')}<span>关联任务</span></button>`);
    }
  }
  if(source) controls.push(source);
  const wakeup=current?.wakeup;
  if(wakeup?.state==='failed' && ['preparing','queued'].includes(current.state)) controls.push(`<small class="work-wakeup-warning" role="status">${esc(wakeup.message)}</small>`);
  const note=current?.summary || (current?.state==='task_requested'?'运行请求已提交，完成结果尚未确认':'');
  return controls.length || note?`<div class="work-actions${compact?' compact':''}"><div class="work-action-controls">${controls.join('')}</div>${note?`<small class="work-action-note" title="${esc(note)}">${esc(note)}</small>`:''}</div>`:'';
}
// 页面只在有工作真的在动时自己重读:待办上的处理在准备、排队、运行,或者这条 Agent 工作本身在排队、执行。
// reconcile、task_requested、review_unavailable 等的是人,轮询它们只会一直读下去;
// 别的情况靠操作后的重读和刷新按钮。
// 上限按「这一页上次看到它变化」算,不按回执的 updated_at:回执只在提交时写一次,
// 拿它算会把一个跑得好好的长任务在半小时后停掉轮询。状态、进度和更新时间都 30 分钟没变才停。
const WORK_POLL_MS=8000, WORK_POLL_STATES=['preparing','queued','running'], WORK_POLL_QUIET_MS=30*60*1000;
const WORK_SEEN=new Map();
function workActionMoving(item){
  const current=item.actions?.current;
  if(!(current && WORK_POLL_STATES.includes(current.state)) && !workActive(item)){WORK_SEEN.delete(item.id);return false;}
  const sig=JSON.stringify([workState(item),item.updated_at,item.execution,current?.state,current?.updated_at,current?.summary]);
  const seen=WORK_SEEN.get(item.id), now=Date.now();
  if(!seen || seen.sig!==sig){WORK_SEEN.set(item.id,{sig,at:now});return true;}
  return now-seen.at<WORK_POLL_QUIET_MS;
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
    if(reply.wakeup?.state==='failed') toast(reply.wakeup.message,'bad');
  }catch(error){
    if(error.requestRejected || error.payload?.uncertain===false){forget();toast(error.message,'bad');}
    else toast(`${error.message}。未收到确认。再次点击会核对原请求，不会重复创建工作。`,'bad');
  }
  finally{WORK_ACTION_PENDING.delete(itemId);await loadWork({fresh:true});if($('work-detail').open && WORK_DETAIL_ID===itemId) openWorkRecord(itemId,true);}
}
const submitWorkAction=(itemId,actionId)=>sendWorkAction(itemId,actionId,'action');
const stopWorkAction=(itemId,actionId)=>sendWorkAction(itemId,actionId,'stop');
function startWorkActions(){
  document.addEventListener('click',event=>{
    const start=event.target.closest('[data-work-action]'), stop=event.target.closest('[data-work-stop]');
    if(start && !start.disabled) submitWorkAction(start.dataset.workItem,start.dataset.workAction);
    if(stop && !stop.disabled) stopWorkAction(stop.dataset.workItem,stop.dataset.workStop);
  });
  let timer;
  const refresh=async()=>{
    const visible=['overview','work'].includes(CURVIEW) || $('work-detail').open;
    // workActionMoving 会记下每条的最后变化,所以每条都要过一遍,再看有没有在动的。
    if(!document.hidden && visible && (WORK?.items || []).map(item=>workActionMoving(item)).includes(true)){
      await loadWork();if($('work-detail').open && WORK_DETAIL_ID) openWorkRecord(WORK_DETAIL_ID,true);
    }
    timer=setTimeout(refresh,WORK_POLL_MS);
  };
  timer=setTimeout(refresh,WORK_POLL_MS);
  window.addEventListener('pagehide',()=>clearTimeout(timer),{once:true});
}
