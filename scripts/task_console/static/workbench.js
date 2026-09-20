// Compose existing owner observations; no execution engine or health policy here.
function loadWork(){
  if(WORK_PENDING) return WORK_PENDING;
  WORK_LOADING=true;
  renderWorkPlatform();
  WORK_PENDING=(async()=>{
    try{WORK=await api('/api/work');}
    catch(error){WORK={available:false,reason:error.message};}
    finally{WORK_LOADING=false;WORK_PENDING=null;renderWorkPlatform();}
  })();
  return WORK_PENDING;
}
const workTime=value=>value?pipeTime(value):'未记录';
function workEmpty(text){return `<p class="work-empty">${esc(text)}</p>`;}
function workItemRow(item,compact=false){
  const tone=workActive(item)?'active':workResult(item)?'done':'neutral';
  const result=workResult(item) && item.summary, title=result?item.summary:item.title;
  const note=result?item.title:item.summary;
  return `<article class="work-row"><span class="work-state ${tone}">${esc(workLabel(item))}</span>
    <div class="work-subject"><button class="record-link" title="${esc(title)}" data-work-id="${esc(item.id)}">${esc(title)}</button>
    ${note && !(compact && result)?`<p>${esc(note)}</p>`:''}<small>${esc(item.source || '来源未记录')}${item.project?' / '+esc(item.project):''}</small></div>
    <div class="work-when"><time>${esc(workTime(item.updated_at))}</time>${item.due_at?`<small>到期 ${esc(workTime(item.due_at))}</small>`:''}
    ${workResult(item) && !compact?'<small>完成摘要；验证未接入</small>':''}</div></article>`;
}
function renderWorkPlatform(){
  const present=WORK?.available, p=workProjection(WORK), coverage=WORK?.coverage || {};
  const absent=WORK_LOADING?'正在读取工作记录':'工作记录未连接';
  $('work-source-state').textContent=present?`采样 ${workTime(WORK.observed_at)}${coverage.omitted || coverage.invalid?' · 记录不完整':''}`:absent;
  $('decision-state').innerHTML=workEmpty('人工决策入口尚未接入');
  $('active-work').innerHTML=present?p.active.map(item=>workItemRow(item,true)).join('') || workEmpty('工作单中暂无执行中或排队项'):workEmpty(absent);
  $('recent-results').innerHTML=present?p.results.slice(0,6).map(item=>workItemRow(item,true)).join('') || workEmpty('暂无 Agent 交付记录'):workEmpty(absent);
  $('tracked-work').innerHTML=present?p.tracked.slice(0,5).map(item=>workItemRow(item,true)).join('') || workEmpty('暂无跟踪事项'):workEmpty(absent);
  $('active-count').textContent=present?p.active.length:'—';
  $('result-count').textContent=present?p.results.length:'—';
  $('tracked-count').textContent=present?p.tracked.length:'—';
  $('work-context').textContent=present?`Agent Center 工作单 ${coverage.roles?.agent_work || 0} · 跟踪事项 ${coverage.roles?.tracked_item || 0} · 信息输入 ${coverage.roles?.signal || 0}`:absent;
  $('work-coverage').textContent=present?`已载入 ${coverage.returned}/${coverage.total} 条${coverage.invalid?' · '+coverage.invalid+' 条记录无法读取':''}${coverage.omitted?' · '+coverage.omitted+' 条未载入':''}；执行状态来自持久记录。`:WORK?.reason || '';
  const rows=workRows(WORK,{role:WORK_ROLE,state:WORK_STATE,query:WORK_QUERY,source:WORK_SOURCE});
  $('work-list').innerHTML=present?rows.map(item=>workItemRow(item)).join('') || workEmpty('没有符合筛选条件的记录'):workEmpty(absent);
  $('work-match').textContent=present?`${rows.length} 条`:'—';
  const sources=[...new Set((WORK?.sources || []).map(row=>row.source))].sort();
  $('work-source').innerHTML='<option value="">全部来源</option>'+sources.map(source=>`<option value="${esc(source)}">${esc(source)}</option>`).join('');
  $('work-source').value=WORK_SOURCE;
  const counts=new Map();(WORK?.sources || []).forEach(row=>counts.set(row.source,(counts.get(row.source)||0)+row.count));
  $('source-volume').innerHTML=[...counts].sort((a,b)=>b[1]-a[1]).map(([source,count])=>`<button data-work-source="${esc(source)}">${esc(source)} <b>${count}</b></button>`).join('') || workEmpty(absent);
  const events=WORK?.events || [];
  $('work-activity').innerHTML=events.slice(0,40).map(event=>`<li><time>${esc(workTime(event.ts))}</time><div><button class="record-link" data-work-id="${esc(event.item_id)}">${esc(event.title)}</button><small>${esc(event.actor || '操作者未记录')} · ${esc(event.event_type)}${event.to_state?' / '+esc(WORK_STATES[event.to_state] || event.to_state):''}</small></div></li>`).join('') || workEmpty(present?'暂无可用活动记录':absent);
  $('activity-coverage').textContent=present?coverage.events_available?`显示最近 ${Math.min(40,events.length)}/${coverage.event_total} 条工作活动`:'活动来源不可用':'';
  renderPlatformSignals();renderAutomations();
}
function renderPlatformSignals(){
  if(!$('platform-sources')) return;
  const status=(path,available)=>API_READS.get(path)?.pending?'读取中':available?'已连接':API_READS.get(path)?.error?'读取失败':'未连接';
  $('platform-sources').innerHTML=`<span>工作记录 ${status('/api/work',WORK?.available)}</span><span>自动化 ${status('/api/tasks',!!DATA)}</span><span>能力目录 ${status('/api/components',COMPONENTS?.catalog?.available)}</span><a href="#diagnostics">诊断</a>`;
}
function renderAutomations(){
  if(!$('automation-list')) return;
  const rows=automationRows(ROWS,AUTO_QUERY,AUTO_STATE);
  $('automation-count').textContent=DATA?`${rows.length}/${ROWS.length} 项`:'任务来源未连接';
  const groups=new Map();rows.forEach(row=>{if(!groups.has(row.cat))groups.set(row.cat,[]);groups.get(row.cat).push(row);});
  const rowHtml=row=>`<article class="automation-row">
    <div class="automation-name"><strong title="${esc(row.desc || row.name)}">${esc((row.desc || row.name).split(/[。]|[：:](?!\d)/)[0])}</strong><small>${esc(row.name)}</small></div>
    <span class="work-state ${row.state==='Running'?'active':'neutral'}">${esc({Ready:'已启用',Disabled:'已停用',Running:'正在运行',Queued:'已排队'}[row.state] || '状态待确认')}</span>
    <div class="automation-schedule">${esc(row.triggers || '未记录触发计划')}<small>${row.nextRun?'下次 '+esc(row.nextRun):'未记录下次运行'}</small></div>
    ${taskActionButtons(row)}<button class="record-link" data-task="${esc(row.name)}">执行明细</button></article>`;
  $('automation-list').innerHTML=!DATA?workEmpty('自动化来源未连接'):rows.length?[...groups].map(([name,items])=>`<div class="automation-group"><h3>${esc(name)}<span>${items.length} 项计划</span></h3>${items.map(rowHtml).join('')}</div>`).join(''):workEmpty('没有符合筛选条件的自动化');
}
function openWorkRecord(id){
  const item=(WORK?.items || []).find(row=>row.id===id);
  if(!item){toast('该记录未载入，请刷新工作记录','bad');return;}
  const events=(WORK.events || []).filter(event=>event.item_id===id);
  $('work-detail-title').textContent=item.title;
  $('work-detail-body').innerHTML=`<dl class="record-fields"><dt>类别</dt><dd>${esc(ROLE_LABELS[item.role])}</dd><dt>来源</dt><dd>${esc(item.source || '未记录')}</dd><dt>状态</dt><dd>${esc(workLabel(item))}</dd><dt>更新</dt><dd>${esc(workTime(item.updated_at))}</dd><dt>记录 ID</dt><dd>${esc(item.id)}</dd></dl>
    <h3>记录摘要</h3><p class="record-summary">${esc(item.summary || '没有摘要')}</p>
    ${item.execution?'<p class="work-empty">这是持久化执行记录，尚未连接实时进程和验证收据。</p>':''}
    <h3>已载入的活动</h3><ul class="record-events">${events.map(event=>`<li>${esc(workTime(event.ts))} · ${esc(event.actor || '未记录')} · ${esc(event.event_type)} ${esc(event.to_state || '')}</li>`).join('') || '<li>当前活动窗口内没有记录</li>'}</ul>`;
  $('work-detail').showModal();
}
function startWorkPlatform(){
  [['work-search','input',value=>WORK_QUERY=value],['work-role','change',value=>WORK_ROLE=value],['work-state','change',value=>WORK_STATE=value],['work-source','change',value=>WORK_SOURCE=value]].forEach(([id,event,set])=>$(id).addEventListener(event,e=>{set(e.target.value);renderWorkPlatform();}));
  $('automation-search').addEventListener('input',e=>{AUTO_QUERY=e.target.value;renderAutomations();});
  $('automation-state').addEventListener('change',e=>{AUTO_STATE=e.target.value;renderAutomations();});
  $('work-detail-close').addEventListener('click',()=>$('work-detail').close());
  document.addEventListener('click',event=>{
    const view=event.target.closest('[data-open-view]');if(view){event.preventDefault();showView(view.dataset.openView,true);}
    const record=event.target.closest('[data-work-id]');if(record) openWorkRecord(record.dataset.workId);
    const source=event.target.closest('[data-work-source]');if(source){WORK_SOURCE=source.dataset.workSource;WORK_ROLE='all';$('work-role').value='all';renderWorkPlatform();$('work-list').scrollIntoView({block:'start'});}
    const filter=event.target.closest('[data-work-filter]');if(filter){WORK_ROLE=filter.dataset.workFilter;WORK_STATE='';WORK_SOURCE='';WORK_QUERY='';$('work-search').value='';$('work-role').value=WORK_ROLE;$('work-state').value='';showView('work',true);renderWorkPlatform();}
  });
  renderWorkPlatform();
}
