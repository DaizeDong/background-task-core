// Compose existing owner observations; no execution engine or health policy here.
function loadWork(){
  if(WORK_PENDING) return WORK_PENDING;
  WORK_LOADING=true;
  renderWorkPlatform();
  WORK_PENDING=(async()=>{
    try{
      const result=await api('/api/work');
      if(!result.available) throw new Error(result.reason || '工作记录不可用');
      WORK=result;WORK_ERROR='';
    }
    catch(error){WORK_ERROR=error.message;if(!WORK?.available) WORK={available:false,reason:error.message};}
    finally{WORK_LOADING=false;WORK_PENDING=null;renderWorkPlatform();}
  })();
  return WORK_PENDING;
}
const workTime=value=>value?pipeTime(value):'未记录';
let WORK_DETAIL_ID=null, WORK_DETAIL_ITEM=null, WORK_DETAIL_IDS=[], WORK_ERROR='';
function setWorkFilters({role='work',state=role==='all'?'':'unfinished',source='',query=''}={}){
  WORK_ROLE=role;WORK_STATE=state;WORK_SOURCE=source;WORK_QUERY=query;
  $('work-role').value=role;$('work-state').value=state;$('work-search').value=query;
  renderWorkPlatform();
  $('work-list').scrollTop=0;
}
function selectedWorkRows(){return workGroups(WORK,{role:WORK_ROLE,state:WORK_STATE,query:WORK_QUERY,source:WORK_SOURCE});}
function workRelatedRecords(item){
  const records=item.linked_records || [];
  if(!records.length)return '';
  const pending=records.filter(workUnfinished).length;
  return `<details class="work-related"><summary>同一事项的 ${records.length} 条记录${pending?' · '+pending+' 条仍有提醒':''}</summary>${records.map(row=>`<p><button class="record-link" data-work-id="${esc(row.id)}">${esc(row.title)}</button> <small>${esc(workLabel(row))}${row.due_at?' · '+esc(workTime(row.due_at)):''}</small></p>`).join('')}</details>`;
}
function workEmpty(text){return `<p class="work-empty">${esc(text)}</p>`;}
function workQueueNote(item){
  if(workState(item)!=='queued' || !item.execution?.queue_reason)return '';
  const text=item.execution.queue_reason==='cleanup_unconfirmed'?'前一任务的进程清理尚未确认，执行队列受阻。':'等待前一任务释放执行位置。';
  const links=(item.execution.blocked_by || []).map(id=>`<button class="record-link" data-work-id="${esc(id)}">查看阻塞任务</button>`).join(' ');
  return `<p class="work-queue-note">${esc(text)} ${links}</p>`;
}
function workItemRow(item,compact=false){
  const state=workState(item);
  const tone=({running:'active',doing:'active',queued:'pending',pending:'pending',done:'ok',failed:'bad',blocked:'warn',stalled:'warn',reconcile:'warn',review_unavailable:'idle',cancelled:'muted',stopped:'muted',snoozed:'muted',notified:'muted'})[state] || 'idle';
  if(!compact){
    const controls=workActionButtons(item) || `<div class="work-actions"><button class="mini" data-work-id="${esc(item.id)}">查看详情</button></div>`;
    return `<article class="work-row work-record" data-state="${esc(state)}">
      ${statusBadge(workLabel(item),tone,undefined,'work-state')}
      <div class="work-subject"><button class="record-link" title="${esc(item.title)}" data-work-id="${esc(item.id)}">${esc(item.title)}</button>${workQueueNote(item)}
      ${item.summary?`<p title="${esc(item.summary)}">${esc(item.summary)}</p>`:''}${item.latest_mail_summary?`<p>最新邮件：${esc(item.latest_mail_summary)}</p>`:''}<div class="work-meta"><small title="${esc(item.source)}">${esc(workSourceLabel(item.source))}${item.project?' / '+esc(item.project):''}</small>${(item.linked_work || []).map(work=>`<button class="record-link" data-work-id="${esc(work.id)}">关联执行 · ${esc(workLabel(work))}</button>`).join('')}</div>${workRelatedRecords(item)}</div>
      <div class="work-when"><time>${esc(workTime(item.updated_at))}</time>${item.due_at?`<small>截止 ${esc(workTime(item.due_at))}</small>`:''}</div>${controls}</article>`;
  }
  return `<article class="work-row" data-state="${esc(state)}">
    <div class="work-subject"><div class="work-row-heading">${statusBadge(workLabel(item),tone,undefined,'work-state')}<button class="record-link" title="${esc(item.title)}" data-work-id="${esc(item.id)}">${esc(item.title)}</button></div>${workQueueNote(item)}
    ${item.summary?`<p>${esc(item.summary)}</p>`:''}<div class="work-meta"><small title="${esc(item.source)}">${esc(workSourceLabel(item.source))}${item.project?' / '+esc(item.project):''}</small>
    <div class="work-when"><time>${esc(workTime(item.updated_at))}</time>${item.due_at?`<small>到期 ${esc(workTime(item.due_at))}</small>`:''}</div></div>
    </div>${workActionButtons(item,compact)}</article>`;
}
function renderWorkPlatform(){
  const present=WORK?.available, p=workProjection(WORK), coverage=WORK?.coverage || {};
  const absent=WORK_LOADING?'正在读取工作记录':'暂时读不到工作记录';
  const stale=WORK_ERROR && present?'刷新失败，以下为上次读取的记录。':'';
  $('work-source-state').textContent=present?`${stale}${WORK_LOADING?'正在刷新 · ':''}读取于 ${workTime(WORK.observed_at)}${coverage.omitted || coverage.invalid?' · 部分记录未读到':''}`:absent;
  $('decision-state').innerHTML=workEmpty('这里还未连接审批记录，暂时无法列出需要你确认的事。');
  $('active-work').innerHTML=present?p.active.map(item=>workItemRow(item,true)).join('') || workEmpty('工作单中没有正在执行或排队的任务'):workEmpty(absent);
  $('recent-results').innerHTML=present?p.results.slice(0,6).map(item=>workItemRow(item,true)).join('') || workEmpty('还没有 Agent 完成记录'):workEmpty(absent);
  $('tracked-work').innerHTML=present?p.tracked.slice(0,5).map(item=>workItemRow(item,true)).join('') || workEmpty('没有待办事项'):workEmpty(absent);
  $('active-count').textContent=present?p.active.length:'—';
  $('result-count').textContent=present?`${Math.min(6,p.results.length)} / ${p.results.length}`:'—';
  $('tracked-count').textContent=present?`${Math.min(5,p.tracked.length)} / ${p.tracked.length}`:'—';
  $('work-context').textContent=present?(WORK.queue?.state==='blocked'?'执行队列受阻：旧任务的进程清理尚未确认。':'按工作单记录显示，非实时运行状态'):'';
  $('work-coverage').textContent=present?`${stale}已读取 ${coverage.returned}/${coverage.total} 条${coverage.invalid?' · '+coverage.invalid+' 条读取失败':''}${coverage.omitted?' · '+coverage.omitted+' 条未加载':''}。状态以记录为准，这里尚无独立验证结果。${WORK_ERROR?'读取错误：'+WORK_ERROR:''}`:WORK?.reason || '';
  const rows=selectedWorkRows();
  const columns='<div class="work-list-heading" aria-hidden="true"><span>状态</span><span>工作与摘要</span><span>更新 / 截止时间</span><span>可用操作</span></div>';
  $('work-list').innerHTML=present && rows.length?columns+rows.map(item=>workItemRow(item)).join(''):workEmpty(present?'没有符合筛选条件的记录':absent);
  const grouped=rows.reduce((n,item)=>n+(item.linked_work?.length || 0)+(item.linked_records?.length || 0),0);
  const history=workRows(WORK,{role:WORK_ROLE,source:WORK_SOURCE,query:WORK_QUERY}).filter(item=>!workUnfinished(item)).length;
  $('work-match').textContent=present?`${rows.length} 项${grouped?' · 已归入 '+grouped+' 条关联记录':''}${WORK_STATE==='unfinished' && history?' · '+history+' 条已结束记录可在「全部状态」查看':''}`:'—';
  const sources=[...new Set((WORK?.sources || []).map(row=>row.source))].sort();
  const missingSource=WORK_SOURCE && !sources.includes(WORK_SOURCE);
  const options='<option value="">全部来源</option>'+sources.map(source=>`<option value="${esc(source)}">${esc(workSourceLabel(source))}</option>`).join('')+(missingSource?`<option value="${esc(WORK_SOURCE)}">${esc(workSourceLabel(WORK_SOURCE))}（本次未读到）</option>`:'');
  if($('work-source').innerHTML!==options) $('work-source').innerHTML=options;
  $('work-source').value=WORK_SOURCE;
  $('work-reset').hidden=WORK_ROLE==='work' && WORK_STATE==='unfinished' && !WORK_SOURCE && !WORK_QUERY;
  const counts=new Map();(WORK?.sources || []).forEach(row=>counts.set(row.source,(counts.get(row.source)||0)+row.count));
  $('source-volume').innerHTML=[...counts].sort((a,b)=>b[1]-a[1]).map(([source,count])=>`<button data-work-source="${esc(source)}" title="只看此来源，清除其他筛选" aria-pressed="${source===WORK_SOURCE}">${esc(workSourceLabel(source))} <b>${count}</b></button>`).join('') || workEmpty(present?'没有来源记录':absent);
  const events=WORK?.events || [];
  const knownIds=new Set((WORK?.items || []).map(item=>item.id));
  $('work-activity').innerHTML=events.slice(0,40).map(event=>`<li><time>${esc(workTime(event.ts))}</time><div>${knownIds.has(event.item_id)?`<button class="record-link" data-work-id="${esc(event.item_id)}">${esc(event.title)}</button>`:`<span>${esc(event.title)}</span><small>对应的工作记录未加载</small>`}<small>${esc(workEventLabel(event))}</small></div></li>`).join('') || workEmpty(present?'没有可显示的活动记录':absent);
  $('activity-coverage').textContent=present?coverage.events_available?`最近 ${Math.min(40,events.length)} 条，共 ${coverage.event_total} 条`:'暂时读不到活动记录':'';
  renderPlatformSignals();renderAutomations();
}
function renderPlatformSignals(){
  if(!$('platform-sources')) return;
  const status=(path,available)=>API_READS.get(path)?.pending?statusBadge('读取中','pending'):API_READS.get(path)?.error?statusBadge('读取失败','bad'):available?statusBadge('已连接','ok'):statusBadge('未连接','idle');
  $('platform-sources').innerHTML=`<span>工作记录 ${status('/api/work',WORK?.available)}</span><span>计划任务 ${status('/api/tasks',!!DATA)}</span><span>技能与插件 ${status('/api/components',COMPONENTS?.catalog?.available)}</span><a href="#diagnostics">查看技术问题 →</a>`;
}
function renderAutomations(){
  if(!$('automation-list')) return;
  const rows=automationRows(ROWS,AUTO_QUERY,AUTO_STATE);
  $('automation-count').textContent=DATA?`${rows.length}/${ROWS.length} 项`:'暂时读不到计划任务';
  $('automation-reset').hidden=!AUTO_QUERY && !AUTO_STATE;
  const groups=new Map();rows.forEach(row=>{if(!groups.has(row.cat))groups.set(row.cat,[]);groups.get(row.cat).push(row);});
  const rowHtml=row=>`<article class="automation-row">
    <div class="automation-name"><strong title="${esc(row.desc || row.name)}">${esc((row.desc || row.name).split(/[。]|[：:](?!\d)/)[0])}</strong><small>${esc(row.name)}</small></div>
    ${statusBadge(taskStateLabel(row.state),({Ready:'ok',Running:'active',Queued:'pending',Disabled:'muted'})[row.state] || 'idle',row.state==='Disabled'?'Ⅱ':undefined,'work-state')}
    <div class="automation-schedule" title="${esc(row.triggers)}">${esc(taskSchedule(row))}<small>${row.nextRun?'下次 '+esc(workTime(row.nextRun)):'没有下次运行时间'}</small></div>
    ${taskActionButtons(row)}<button class="record-link" data-task="${esc(row.name)}">查看详情</button></article>`;
  $('automation-list').innerHTML=!DATA?workEmpty('暂时读不到计划任务'):rows.length?[...groups].map(([name,items])=>`<div class="automation-group"><h3>${esc(name)}<span>${items.length} 项计划</span></h3>${items.map(rowHtml).join('')}</div>`).join(''):workEmpty('没有符合筛选条件的计划任务');
}
function openWorkRecord(id,keepOrder=false){
  const item=(WORK?.items || []).find(row=>row.id===id);
  if(!item){toast('该记录未载入，请刷新工作记录','bad');return;}
  if(!keepOrder){
    const rows=selectedWorkRows();
    WORK_DETAIL_IDS=(rows.some(row=>row.id===id)?rows:workRows(WORK,{role:'all'})).map(row=>row.id);
  }
  WORK_DETAIL_ID=id;
  WORK_DETAIL_ITEM=item;
  const events=(WORK.events || []).filter(event=>event.item_id===id);
  $('work-detail-title').textContent=item.title;
  $('work-detail-body').innerHTML=`<dl class="record-fields"><dt>类别</dt><dd>${esc(ROLE_LABELS[item.role])}</dd><dt>来源</dt><dd>${esc(workSourceLabel(item.source))}${workSourceLabel(item.source)!==item.source?' · '+esc(item.source):''}</dd><dt>记录状态</dt><dd>${esc(workLabel(item))}</dd><dt>更新时间</dt><dd>${esc(workTime(item.updated_at))}</dd>${item.due_at?`<dt>截止时间</dt><dd>${esc(workTime(item.due_at))}</dd>`:''}${item.project?`<dt>项目</dt><dd>${esc(item.project)}</dd>`:''}<dt>记录 ID</dt><dd>${esc(item.id)}</dd></dl>
    ${item.group_parent_id?`<p>已归入 <button class="record-link" data-work-id="${esc(item.group_parent_id)}">查看当前事项</button></p>`:''}${workActionButtons(item)}<h3>${workResult(item)?'完成摘要':'摘要'}</h3><p class="record-summary">${esc(item.summary || '没有摘要')}</p>${item.latest_mail_summary?`<p>最新邮件：${esc(item.latest_mail_summary)}</p>`:''}
    ${workQueueNote(item)}${item.execution?'<p class="work-empty">状态来自工作单，不能据此确认程序是否仍在运行。这里尚无独立验证结果。</p>':''}${workSourceContext(item.id)}
    <h3>最近活动</h3><ul class="record-events">${events.map(event=>`<li>${esc(workTime(event.ts))} · ${esc(workEventLabel(event))}${event.actor?' · '+esc(event.actor):''}</li>`).join('') || '<li>本次读取的活动中没有这条记录</li>'}</ul>`;
  const index=WORK_DETAIL_IDS.indexOf(id);
  $('work-detail-prev').disabled=index<=0;$('work-detail-next').disabled=index<0 || index>=WORK_DETAIL_IDS.length-1;
  $('work-detail-position').textContent=`${index+1} / ${WORK_DETAIL_IDS.length}`;
  $('work-detail').scrollTop=0;
  if(!$('work-detail').open) $('work-detail').showModal();
}
function startWorkPlatform(){
  $('work-context-toggle').addEventListener('click',()=>{
    const panels=$('work-context-panels'), toggle=$('work-context-toggle');
    panels.hidden=!panels.hidden;
    toggle.setAttribute('aria-expanded',String(!panels.hidden));
    toggle.textContent=panels.hidden?'显示活动与来源':'收起活动与来源';
  });
  [['work-search','input',value=>WORK_QUERY=value],['work-role','change',value=>WORK_ROLE=value],['work-state','change',value=>WORK_STATE=value],['work-source','change',value=>WORK_SOURCE=value]].forEach(([id,event,set])=>$(id).addEventListener(event,e=>{set(e.target.value);renderWorkPlatform();}));
  $('automation-search').addEventListener('input',e=>{AUTO_QUERY=e.target.value;renderAutomations();});
  $('automation-state').addEventListener('change',e=>{AUTO_STATE=e.target.value;renderAutomations();});
  $('work-detail-close').addEventListener('click',()=>$('work-detail').close());
  $('work-reset').addEventListener('click',()=>{setWorkFilters();$('work-search').focus();});
  $('automation-reset').addEventListener('click',()=>{AUTO_QUERY='';AUTO_STATE='';$('automation-search').value='';$('automation-state').value='';renderAutomations();$('automation-search').focus();});
  for(const [id,delta] of [['work-detail-prev',-1],['work-detail-next',1]]) $(id).addEventListener('click',()=>{
    const next=WORK_DETAIL_IDS[WORK_DETAIL_IDS.indexOf(WORK_DETAIL_ID)+delta];if(next) openWorkRecord(next,true);
  });
  $('work-detail-copy').addEventListener('click',async()=>{
    const item=WORK_DETAIL_ITEM;if(!item) return;
    try{await navigator.clipboard.writeText([item.title,item.summary || '',`记录 ID：${item.id}`,`记录状态：${workLabel(item)}`,item.execution?'这里尚无独立验证结果。':''].filter(Boolean).join('\n\n'));toast('已复制标题、摘要和记录信息');}
    catch(error){toast('复制失败，请选中详情文字手动复制','bad');}
  });
  document.addEventListener('click',event=>{
    const view=event.target.closest('[data-open-view]');if(view){if(event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;event.preventDefault();showView(view.dataset.openView,true);}
    const record=event.target.closest('[data-work-id]');if(record) openWorkRecord(record.dataset.workId);
    const source=event.target.closest('[data-work-source]');if(source){setWorkFilters({role:'all',source:source.dataset.workSource});$('work-search').focus({preventScroll:true});$('work-search').scrollIntoView({block:'center'});}
    const filter=event.target.closest('[data-work-filter]');if(filter){setWorkFilters({role:filter.dataset.workFilter,state:filter.dataset.workState || ''});showView('work',true);}
  });
  renderWorkPlatform();
}
