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
const workTime=value=>value?fmtTime(value):'未记录';
// 行里的时间写相对的「2 小时前」,完整时刻放进悬停:以前每行都是带秒的绝对时间,要人自己去减。
const workTimeTag=value=>value?timeTag(value):'<span>未记录</span>';
let WORK_DETAIL_ID=null, WORK_DETAIL_ITEM=null, WORK_DETAIL_IDS=[], WORK_ERROR='';
// 工作记录这份数据此刻是哪一种:读到了、还在读(第一次,或者读坏之后正在重读)、读坏了。
// 三种各有一种样子,不许用同一句「暂时读不到」糊过去。
const workPhase=()=>WORK?.available?'ok':(!WORK || WORK_LOADING)?'loading':'broken';
const WORK_FAILED_REASON='工作记录读取失败，暂时无法筛选或查看';

// ── 记住上次的筛选 ──
// 类别、状态、来源存在本机浏览器里,刷新页面后接着用;搜索词不存:那是这一次在找的东西,下次打开还在会让人以为列表少了。
// 存不进去(隐私窗口、禁用存储)就照旧从默认筛选开始,不影响别的。
const WORK_FILTER_KEY='tc.work.filters';
const WORK_ROLES=['work','agent_work','tracked_item','signal','all'];
const WORK_FILTER_STATES=['unfinished','','active','tracked_active',...Object.keys(WORK_STATES)];
// 只在「从存储里恢复了非默认筛选」到「人自己动了筛选」之间为真,用来提示列表为什么不是默认的样子。
let WORK_FILTERS_RESTORED=false;
// 下钻(工作台的「查看全部 →」、接入页的「查看」)设的筛选只管这一趟,不存:点过一次的跳转不该让
// 这一屏从此一直筛着。这里记着人自己上次选的那一份,离开这一屏时由 endWorkDrillFilters 放回去。
let WORK_FILTERS_SAVED={role:'work',state:'unfinished',source:''}, WORK_FILTERS_DRILL=false;
function saveWorkFilters(){
  WORK_FILTERS_SAVED={role:WORK_ROLE,state:WORK_STATE,source:WORK_SOURCE};WORK_FILTERS_DRILL=false;
  try{localStorage.setItem(WORK_FILTER_KEY,JSON.stringify(WORK_FILTERS_SAVED));}catch(error){}
}
function restoreWorkFilters(){
  let saved=null;
  try{saved=JSON.parse(localStorage.getItem(WORK_FILTER_KEY) || 'null');}catch(error){return false;}
  if(!saved || typeof saved!=='object') return false;
  const {role,state,source}=saved;
  // 存的值来自别的版本或被手改过时一概不用:一个下拉框里没有的值会让列表按看不见的条件在筛。
  if(!WORK_ROLES.includes(role) || !WORK_FILTER_STATES.includes(state) || typeof source!=='string' || source.length>200) return false;
  WORK_ROLE=role;WORK_STATE=state;WORK_SOURCE=source;WORK_FILTERS_SAVED={role,state,source};
  $('work-role').value=role;$('work-state').value=state;
  WORK_FILTERS_RESTORED=typeof activeFilterCount==='function'?activeFilterCount('work')>0:true;
  return WORK_FILTERS_RESTORED;
}
// 「已应用上次的筛选」贴在清除筛选按钮旁边。页面里没有这个位置,第一次用到时插一个。
function workRestoredHint(){
  let hint=document.getElementById('work-filter-restored');
  const reset=$('work-reset');
  if(!hint?.id && reset?.insertAdjacentElement){
    hint=document.createElement('span');hint.id='work-filter-restored';hint.className='work-filter-restored';hint.textContent='已应用上次的筛选';
    reset.insertAdjacentElement('afterend',hint);
  }
  return hint;
}

// remember:false 给从别的屏跳过来的那一下:筛选这一趟生效,不写进本机浏览器。
function setWorkFilters({role='work',state=role==='all'?'':'unfinished',source='',query='',remember=true}={}){
  WORK_ROLE=role;WORK_STATE=state;WORK_SOURCE=source;WORK_QUERY=query;WORK_FILTERS_RESTORED=false;
  $('work-role').value=role;$('work-state').value=state;$('work-search').value=query;
  if(remember) saveWorkFilters();
  else WORK_FILTERS_DRILL=role!==WORK_FILTERS_SAVED.role || state!==WORK_FILTERS_SAVED.state || source!==WORK_FILTERS_SAVED.source;
  renderWorkPlatform();
  $('work-list').scrollTop=0;
}
// 离开工作与结果时(navigation.js 的 showView 调):这一趟是下钻设的筛选,就换回人自己上次选的,搜索词不动。
function endWorkDrillFilters(){
  if(!WORK_FILTERS_DRILL) return;
  setWorkFilters({...WORK_FILTERS_SAVED,query:WORK_QUERY,remember:false});
  WORK_FILTERS_RESTORED=typeof activeFilterCount==='function'?activeFilterCount('work')>0:false;
  renderWorkPlatform();
}
function selectedWorkRows(){return workGroups(WORK,{role:WORK_ROLE,state:WORK_STATE,query:WORK_QUERY,source:WORK_SOURCE});}
function workRelatedRecords(item){
  const records=item.linked_records || [];
  if(!records.length)return '';
  const pending=records.filter(workUnfinished).length;
  return `<details class="work-related"><summary>同一事项的 ${records.length} 条记录${pending?' · '+pending+' 条仍有提醒':''}</summary>${records.map(row=>`<p><button class="record-link" data-work-id="${esc(row.id)}">${esc(row.title)}</button> <small>${esc(workLabel(row))}${row.due_at?' · 截止 '+timeTag(row.due_at):''}</small></p>`).join('')}</details>`;
}
function workEmpty(text){return `<p class="work-empty">${esc(text)}</p>`;}
// 截止时间:还没结束的事项,已过截止或 24 小时内到期的用警告色,一眼分得出哪件要赶。已经结束的不再催。
function workDue(item){
  if(!item.due_at) return '';
  const left=timeValue(item.due_at)-Date.now(), open=workUnfinished(item);
  const overdue=open && left<0, soon=open && left>=0 && left<86400000;
  return `<small class="work-due${overdue || soon?' due-warn':''}">${overdue?'已逾期 · ':''}截止 ${timeTag(item.due_at)}</small>`;
}
// 来源一栏。「Agent 工作单」是 Agent 工作的默认来源,每一行都写一遍等于没写,只有别的来源才值得占地方;
// 原始来源代号留在悬停里。
function workOrigin(item){
  const parts=[item.source==='agent-center:work'?'':workSourceLabel(item.source),item.project].filter(Boolean);
  return parts.length?`<small title="${esc(item.source || '')}">${esc(parts.join(' / '))}</small>`:'';
}
function workQueueNote(item){
  if(workState(item)!=='queued' || !item.execution?.queue_reason)return '';
  const text=item.execution.queue_reason==='cleanup_unconfirmed'?'前一任务的进程清理尚未确认，执行队列受阻。':'等待前一任务释放执行位置。';
  // 以前这里是一只眼睛图标,和「查看详情」「查看进度」「查看原待办」同一个图标四种意思;改成直接写出去哪儿。
  const blockers=item.execution.blocked_by || [];
  const links=blockers.map((id,index)=>`<button type="button" class="link-button" data-work-id="${esc(id)}">查看阻塞的任务${blockers.length>1?' '+(index+1):''}</button>`).join(' ');
  return `<p class="work-queue-note">${esc(text)} ${links}</p>`;
}
function workItemRow(item,compact=false){
  const state=workState(item);
  const tone=({running:'active',doing:'active',queued:'pending',pending:'pending',done:'ok',failed:'bad',blocked:'warn',stalled:'warn',reconcile:'warn',review_unavailable:'idle',cancelled:'muted',stopped:'muted',snoozed:'muted',notified:'muted'})[state] || 'idle';
  if(!compact){
    // 标题本身就打开这条记录,行尾不再另放一只「查看详情」的眼睛:同一件事两个入口,还占掉操作列。
    return `<article class="work-row work-record" data-state="${esc(state)}">
      ${statusBadge(workLabel(item),tone,undefined,'work-state')}
      <div class="work-subject"><button class="record-link" title="${esc(item.title)}" data-work-id="${esc(item.id)}">${esc(item.title)}</button>${workQueueNote(item)}
      ${item.summary?`<p title="${esc(item.summary)}">${esc(item.summary)}</p>`:''}${item.latest_mail_summary?`<p>最新邮件：${esc(item.latest_mail_summary)}</p>`:''}<div class="work-meta">${workOrigin(item)}${(item.linked_work || []).map(work=>`<button class="record-link" data-work-id="${esc(work.id)}">关联执行 · ${esc(workLabel(work))}</button>`).join('')}</div>${workRelatedRecords(item)}</div>
      <div class="work-when">${workTimeTag(item.updated_at)}${workDue(item)}</div>${workActionButtons(item)}</article>`;
  }
  // 紧凑行整行可点(data-work-row),点行里的按钮不算:那些按钮各有自己的事。
  return `<article class="work-row" data-state="${esc(state)}" data-work-row="${esc(item.id)}">
    <div class="work-subject"><div class="work-row-heading">${statusBadge(workLabel(item),tone,undefined,'work-state')}<button class="record-link" title="${esc(item.title)}" data-work-id="${esc(item.id)}">${esc(item.title)}</button></div>${workQueueNote(item)}
    ${item.summary?`<p>${esc(item.summary)}</p>`:''}<div class="work-meta">${workOrigin(item)}
    <div class="work-when">${workTimeTag(item.updated_at)}${workDue(item)}</div></div>
    </div>${workActionButtons(item,compact)}</article>`;
}
// 卡片标题旁的数:就是总数,截短了才补一句「显示前 N」。以前写「3 / 3」,读不出哪个是哪个。
// 读取中和读坏了用计数格子那两种样子,不写一个看起来像结论的「—」。
function setWorkCount(id,phase,shown,total){
  const el=$(id);if(!el) return;
  el.innerHTML=phase!=='ok'?countCell(phase==='loading'?'loading':'broken',null,WORK?.reason)
    :total?`${fmtNum(total)}${shown<total?' · 显示前 '+fmtNum(shown):''}`:countCell('ok',0);
}
// 覆盖说明只在有话要说时出现:刷新失败、有记录没读到。全读到了就不写「25/25」这种要人去猜的分数。
function workCoverageText(coverage,stale){
  const parts=[];
  if(stale) parts.push('刷新失败，以下为上次读取的记录');
  if(Number.isFinite(coverage.total) && Number.isFinite(coverage.returned) && coverage.returned<coverage.total)
    parts.push(`共 ${fmtNum(coverage.total)} 条记录，本次读到 ${fmtNum(coverage.returned)} 条`);
  if(coverage.invalid) parts.push(`${fmtNum(coverage.invalid)} 条读取失败`);
  if(coverage.omitted) parts.push(`${fmtNum(coverage.omitted)} 条未加载`);
  return parts.length?parts.join('，')+'。':'';
}
// 工作台上放「读取中 / 读取失败」那一块的位置。页面里没有现成的格子,第一次用到时插在技术问题摘要后面。
function overviewFeedSlot(){
  let slot=document.getElementById('overview-work-state');
  const anchor=$('attention-strip');
  if(!slot?.id && anchor?.insertAdjacentElement){
    slot=document.createElement('div');slot.id='overview-work-state';anchor.insertAdjacentElement('afterend',slot);
  }
  return slot;
}
function renderWorkPlatform(){
  const phase=workPhase(), present=phase==='ok', p=workProjection(WORK), coverage=WORK?.coverage || {};
  const stale=WORK_ERROR && present;
  // 读不到的时候只说一次:工作台一块、工作记录列表一块,带重试;原始代号只进悬停。
  // 以前同一句「暂时读不到工作记录」在一屏里出现四次,原因却一次也没说。
  const feedState=phase==='loading'?loadingBlock('工作记录'):phase==='broken'?errorBlock('工作记录',WORK?.reason,'data-work-retry'):'';
  $('work-source-state').textContent=present?`${stale?'刷新失败，以下为上次读取的记录。':''}${WORK_LOADING?'正在刷新 · ':''}读取于 ${workTime(WORK.observed_at)}${coverage.omitted || coverage.invalid?' · 部分记录未读到':''}`:'';
  const slot=overviewFeedSlot();
  if(slot){slot.innerHTML=feedState;slot.hidden=present;}
  if($('work')?.dataset) $('work').dataset.feed=phase;
  if($('overview')?.dataset) $('overview').dataset.feed=phase;
  // 「需要你确认」只在接上了审批来源时出现。没接上时整条藏起来,而不是常驻顶上告诉人审批还没接上:
  // 那条横幅占着最显眼的位置,却永远不变,读了也做不了什么。
  const decisions=present && Array.isArray(p.decisions)?p.decisions:null;
  if($('decision-strip')) $('decision-strip').hidden=!decisions;
  if(decisions) $('decision-state').innerHTML=decisions.map(item=>workItemRow(item,true)).join('') || workEmpty('没有要你确认的事');
  // Agent 没做完或出错的工作单独一张卡,红色边条;一条都没有就整张藏起来。
  const interrupted=present?p.interrupted:[];
  if($('interrupted-card')) $('interrupted-card').hidden=!interrupted.length;
  $('interrupted-work').innerHTML=interrupted.slice(0,5).map(item=>workItemRow(item,true)).join('');
  setWorkCount('interrupted-count',phase,Math.min(5,interrupted.length),interrupted.length);
  $('active-work').innerHTML=present?p.active.map(item=>workItemRow(item,true)).join('') || emptyBlock('工作单中没有正在执行或排队的任务'):'';
  $('recent-results').innerHTML=present?p.results.slice(0,6).map(item=>workItemRow(item,true)).join('') || emptyBlock('还没有 Agent 完成记录'):'';
  $('tracked-work').innerHTML=present?p.tracked.slice(0,5).map(item=>workItemRow(item,true)).join('') || emptyBlock('没有待办事项'):'';
  setWorkCount('active-count',phase,p.active.length,p.active.length);
  setWorkCount('result-count',phase,Math.min(6,p.results.length),p.results.length);
  setWorkCount('tracked-count',phase,Math.min(5,p.tracked.length),p.tracked.length);
  $('work-context').textContent=present && WORK.queue?.state==='blocked'?'队列受阻：进程清理待确认':'';
  const coverageText=present?workCoverageText(coverage,stale):'';
  $('work-coverage').textContent=coverageText;
  $('work-coverage').hidden=!coverageText;
  $('work-coverage').title=stale?'原始错误：'+WORK_ERROR:'';
  const rows=selectedWorkRows();
  const columns='<div class="work-list-heading" aria-hidden="true"><span>状态</span><span>工作与摘要</span><span>更新 / 截止时间</span><span>可用操作</span></div>';
  const filtered=typeof activeFilterCount==='function' && activeFilterCount('work')?'work':'';
  $('work-list').innerHTML=!present?feedState:rows.length?columns+rows.map(item=>workItemRow(item)).join(''):emptyBlock('没有符合筛选条件的记录',{filtered});
  const grouped=rows.reduce((n,item)=>n+(item.linked_work?.length || 0)+(item.linked_records?.length || 0),0);
  const history=workRows(WORK,{role:WORK_ROLE,source:WORK_SOURCE,query:WORK_QUERY}).filter(item=>!workUnfinished(item)).length;
  $('work-match').textContent=present?`${fmtNum(rows.length)} 项${grouped?' · 含关联 '+grouped+' 条':''}${WORK_STATE==='unfinished' && history?' · 另有 '+history+' 项已结束':''}`:'';
  $('work-match').title=WORK_STATE==='unfinished' && history?'已结束记录可在「全部状态」查看':'';
  // 读坏了的时候,搜索、三个下拉和工作台上的「查看… →」都做不了事:灰掉并说清为什么,而不是点了进一张空列表。
  const blocked=phase==='broken'?WORK_FAILED_REASON:'';
  ['work-search','work-role','work-state','work-source','work-context-toggle'].forEach(id=>setDisabled($(id),blocked));
  document.querySelectorAll?.('#overview [data-work-filter]').forEach(button=>setDisabled(button,blocked));
  const sources=[...new Set((WORK?.sources || []).map(row=>row.source))].sort();
  const missingSource=WORK_SOURCE && !sources.includes(WORK_SOURCE);
  const options='<option value="">全部来源</option>'+sources.map(source=>`<option value="${esc(source)}">${esc(workSourceLabel(source))}</option>`).join('')+(missingSource?`<option value="${esc(WORK_SOURCE)}">${esc(workSourceLabel(WORK_SOURCE))}（本次未读到）</option>`:'');
  if($('work-source').innerHTML!==options) $('work-source').innerHTML=options;
  $('work-source').value=WORK_SOURCE;
  if(typeof syncResetFilters==='function') syncResetFilters();
  const hint=workRestoredHint();
  if(hint) hint.hidden=!(WORK_FILTERS_RESTORED && typeof activeFilterCount==='function' && activeFilterCount('work')>0);
  const counts=new Map();(WORK?.sources || []).forEach(row=>counts.set(row.source,(counts.get(row.source)||0)+row.count));
  $('source-volume').innerHTML=!present?'':[...counts].sort((a,b)=>b[1]-a[1]).map(([source,count])=>`<button data-work-source="${esc(source)}" title="只看此来源，清除其他筛选" aria-pressed="${source===WORK_SOURCE}">${esc(workSourceLabel(source))} <b>${fmtNum(count)}</b></button>`).join('') || emptyBlock('没有来源记录');
  const events=WORK?.events || [];
  const knownIds=new Set((WORK?.items || []).map(item=>item.id));
  $('work-activity').innerHTML=!present?'':events.slice(0,40).map(event=>`<li>${workTimeTag(event.ts)}<div>${knownIds.has(event.item_id)?`<button class="record-link" data-work-id="${esc(event.item_id)}">${esc(event.title)}</button>`:`<span>${esc(event.title)}</span><small>对应的工作记录未加载</small>`}<small>${esc(workEventLabel(event))}</small></div></li>`).join('') || emptyBlock('没有可显示的活动记录');
  $('activity-coverage').textContent=present?coverage.events_available?`最近 ${Math.min(40,events.length)} 条，共 ${fmtNum(coverage.event_total)} 条`:'暂时读不到活动记录':'';
  renderPlatformSignals();renderAutomations();
}
function renderPlatformSignals(){
  renderAttentionStrip();
  if(!$('platform-sources')) return;
  const status=(path,available)=>API_READS.get(path)?.pending?statusBadge('读取中','pending'):API_READS.get(path)?.error?statusBadge('读取失败','bad'):available?statusBadge('已连接','ok'):statusBadge('未连接','idle');
  // 顶上的摘要出现时它已经带着「全部技术问题 →」,页底这一个就不再重复。
  const link=$('attention-strip')?.hidden===false?'':'<a href="#diagnostics" data-open-view="diagnostics" data-drill>查看技术问题 →</a>';
  $('platform-sources').innerHTML=`<span>工作记录 ${status('/api/work',WORK?.available)}</span><span>计划任务 ${status('/api/tasks',!!DATA)}</span><span>技能与插件 ${status('/api/components',COMPONENTS?.catalog?.available)}</span>${link}`;
}

// ── 工作台顶上的技术问题摘要 ──
// 每一类要人管的事一枚芯片,点了去技术问题页并筛到那一类。数从诊断那份清单来(attentionSummary),
// 这里只负责摆出来,不另算:两处各算一份,数字迟早对不上。
// 一类的四种样子:还在读写「读取中」,读坏了写「读取失败」,没在查写「未检查」,是零就不占位置。
// 整条只在每一类都读到了、而且全是零时才藏起来:还在读或读坏了的那一类,不能被藏成「没问题」。
const ATTENTION_KINDS=[
  {key:'tasks',filter:'tasks',name:'计划任务',tone:'bad',text:s=>`${fmtNum(s.count)} 个任务失败`},
  {key:'outputs',filter:'tasks',name:'输出文件检查',tone:'warn',text:s=>`${fmtNum(s.count)} 个输出过期`},
  {key:'ingest',filter:'tasks',name:'运行日志摄入',tone:'warn',text:()=>'运行日志摄入异常'},
  {key:'repos',filter:'repos',name:'代码仓库',tone:'warn',text:s=>`${fmtNum(s.count)} 个仓库要处理`},
  {key:'disk',filter:'storage',name:'系统盘',tone:'bad',text:s=>s.usedPct!=null?`磁盘 ${s.usedPct}%`:'系统盘空间不足'},
  {key:'memory',filter:'storage',name:'记忆索引',tone:'warn',text:s=>s.pct!=null?`记忆索引 ${s.pct}%`:'记忆索引逼近上限'}
];
let ATTENTION_TIMER=null, ATTENTION_HTML=null;
function attentionChips(summary){
  const chips=[], waiting=[];
  for(const kind of ATTENTION_KINDS){
    const s=summary[kind.key];if(!s) continue;
    if(s.state==='pending'){
      waiting.push(kind.name);
    }else if(s.state==='failed'){
      const why=failureReason(s.reason);
      chips.push(`<span class="attention-chip broken" title="${esc(kind.name+'读取失败'+(why.raw?'：'+why.raw:''))}"><span aria-hidden="true">!</span>${esc(kind.name)} 读取失败</span>`);
    }else if(s.unchecked){
      chips.push(`<span class="attention-chip unchecked" title="${esc(s.unchecked)}"><span aria-hidden="true">—</span>${esc(kind.name)} 未检查</span>`);
    }else if(s.count){
      chips.push(`<a class="attention-chip ${kind.tone}" href="#diagnostics" data-attention-filter="${esc(kind.filter)}" title="在技术问题里只看这一类"><span aria-hidden="true">${kind.tone==='bad'?'×':'!'}</span>${esc(kind.text(s))}</a>`);
    }
  }
  // 还在读的几类并成一枚,写明是哪几类:刚打开页面时六类都在读,六枚一样的「读取中」只是噪音。
  if(waiting.length) chips.push(`<span class="attention-chip pending" title="${esc('正在读取：'+waiting.join('、'))}"><span aria-hidden="true">◷</span>读取中：${esc(waiting.join('、'))}</span>`);
  return {chips,pending:waiting.length>0};
}
function renderAttentionStrip(){
  const strip=$('attention-strip');if(!strip) return;
  let summary=null;
  try{summary=typeof attentionSummary==='function'?attentionSummary():null;}catch(error){summary=null;}
  const {chips,pending}=summary?attentionChips(summary):{chips:[],pending:false};
  const html=chips.length?`<span class="attention-label">要处理</span>${chips.join('')}<a class="attention-all" href="#diagnostics" data-open-view="diagnostics" data-drill>全部技术问题 →</a>`:'';
  // role=status 的区域每写一次读屏就念一次,内容没变就不重写。
  if(html!==ATTENTION_HTML){strip.innerHTML=html;ATTENTION_HTML=html;}
  strip.hidden=!chips.length;
  // 某一类还在读时过一会儿再看一眼:有的来源读失败时不会叫任何面板重画,不看的话这里会一直写「读取中」。
  clearTimeout(ATTENTION_TIMER);ATTENTION_TIMER=null;
  if(pending){ATTENTION_TIMER=setTimeout(renderAttentionStrip,2000);ATTENTION_TIMER?.unref?.();}
}
// 从摘要芯片去技术问题页:筛选设成那一类,搜索词清掉,好让页上的条数和芯片上的数对得上。
// 搜索词用 input 事件交给那一页自己的监听;范围走 review.js 的 applyReviewFilter,不在这里改它的状态变量。
// 范围只管这一趟(remember:false):点过一次芯片,诊断页不该从此一直只显示那一类。
function openDiagnosticsFiltered(filter){
  const search=$('review-search');
  if(search?.value){search.value='';search.dispatchEvent(new Event('input',{bubbles:true}));}
  applyReviewFilter(filter,{remember:false});
  showView('diagnostics',true);
}

// 任务开关每组标题右边的建议计数做成小按钮,点一下就按这个建议筛;急修、要修的数是红的。
const URGENT_VERDICTS=['urgent','fix'];
function automationVerdictChips(rows,selected){
  const counts=taskVerdictCounts(rows);
  return [...Object.entries(TASK_VERDICTS),...Object.entries(TASK_VERDICT_EXTRA)].filter(([key])=>counts[key]).map(([key,value])=>
    `<button type="button" class="verdict-count${URGENT_VERDICTS.includes(key)?' urgent':''}" data-automation-verdict="${esc(key)}" aria-pressed="${selected===key}" title="${esc(selected===key?'再点一次取消这个筛选':'只看建议为「'+value.label+'」的任务')}">${esc(value.label)} <b>${counts[key]}</b></button>`).join('');
}
function renderAutomations(){
  if(!$('automation-list')) return;
  const verdict=$('automation-verdict').value;
  const candidates=automationRows(ROWS,AUTO_QUERY,AUTO_STATE);
  updateTaskVerdictFilter('automation-verdict',candidates,verdict);
  const rows=candidates.filter(row=>taskMatchesVerdict(row,verdict));
  $('automation-count').textContent=DATA?`${rows.length}/${ROWS.length} 项`:'暂时读不到计划任务';
  if(typeof syncResetFilters==='function') syncResetFilters();
  const groups=new Map();rows.forEach(row=>{if(!groups.has(row.cat))groups.set(row.cat,[]);groups.get(row.cat).push(row);});
  // 这张列表会被工作记录刷新、修复进度轮询顺带重画:焦点在某个按钮上时,重画之后要放回同一个按钮。
  const focused=$('automation-list').contains?.(document.activeElement)?taskControlKey(document.activeElement):null;
  $('automation-list').innerHTML=!DATA?workEmpty('暂时读不到计划任务'):rows.length?[...groups].map(([name,items])=>{
    const group=DATA.groups.find(g=>g.cat===name), all=ROWS.filter(row=>row.cat===name);
    return `<section class="card automation-group"><div class="card-header automation-group-header"><div><h3 class="card-title">${esc(name)} <span class="n">${items.length===all.length?all.length:items.length+'/'+all.length} 项计划</span></h3>
      ${group?.desc?`<p>${esc(group.desc)}</p>`:''}</div><span class="automation-verdict-counts">${automationVerdictChips(all,verdict)}</span></div>
      <div class="automation-list-heading" aria-hidden="true"><span>任务与建议</span><span>运行计划</span><span>状态</span><span>操作</span></div>${items.map(row=>taskListRow(row)).join('')}</section>`;
  }).join(''):workEmpty('没有符合筛选条件的计划任务');
  restoreTaskControlFocus($('automation-list'),focused);
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
  // 详情里的时间相对、绝对都写:这里是停下来细看的地方,不用再去悬停。
  const when=value=>value?`${timeTag(value)} <small>${esc(fullTime(value))}</small>`:'未记录';
  // 详情的主字段只放人读得懂的:记录 ID 和来源的原始代码放到最后的「技术信息」里,排查时还找得到。
  $('work-detail-title').textContent=item.title;
  $('work-detail-body').innerHTML=`<dl class="record-fields"><dt>类别</dt><dd>${esc(ROLE_LABELS[item.role])}</dd><dt>来源</dt><dd>${esc(workSourceLabel(item.source))}</dd><dt>记录状态</dt><dd>${esc(workLabel(item))}</dd><dt>更新时间</dt><dd>${when(item.updated_at)}</dd>${item.due_at?`<dt>截止时间</dt><dd>${when(item.due_at)}</dd>`:''}${item.project?`<dt>项目</dt><dd>${esc(item.project)}</dd>`:''}</dl>
    ${item.group_parent_id?`<p>已归入 <button type="button" class="link-button" data-work-id="${esc(item.group_parent_id)}">查看当前事项</button></p>`:''}${workActionButtons(item)}<h3>${workResult(item)?'完成摘要':'摘要'}</h3><p class="record-summary">${esc(item.summary || '没有摘要')}</p>${item.latest_mail_summary?`<p>最新邮件：${esc(item.latest_mail_summary)}</p>`:''}
    ${workQueueNote(item)}${workSourceContext(item.id)}
    <h3>最近活动</h3><ul class="record-events">${events.map(event=>`<li>${workTimeTag(event.ts)} · ${esc(workEventLabel(event))}${event.actor?' · '+esc(event.actor):''}</li>`).join('') || '<li>本次读取的活动中没有这条记录</li>'}</ul>
    <h3>技术信息</h3><dl class="record-fields"><dt>记录 ID</dt><dd><code>${esc(item.id)}</code></dd><dt>来源代码</dt><dd><code>${esc(item.source || '未记录')}</code></dd></dl>`;
  const index=WORK_DETAIL_IDS.indexOf(id);
  // 灰着的上一条 / 下一条说清为什么:到头了,或者这条不在当前列表里。
  setDisabled($('work-detail-prev'),index<0?'这条记录不在当前列表里':index===0?'已是第一条':'');
  setDisabled($('work-detail-next'),index<0?'这条记录不在当前列表里':index>=WORK_DETAIL_IDS.length-1?'已是最后一条':'');
  $('work-detail-position').textContent=`${index+1} / ${WORK_DETAIL_IDS.length}`;
  $('work-detail').scrollTop=0;
  if(!$('work-detail').open) $('work-detail').showModal();
}
// 这一屏的点击都在这里分派。单独成一个函数,node:vm 台架可以拿假事件直接测「点行打开、点行里按钮不打开」。
function routeWorkClick(event){
  const target=event.target;
  if(!target?.closest) return;
  const modified=event.ctrlKey || event.metaKey || event.shiftKey || event.altKey;
  const view=target.closest('[data-open-view]');if(view){if(modified) return;event.preventDefault();showView(view.dataset.openView,true);}
  const attention=target.closest('[data-attention-filter]');if(attention){if(modified) return;event.preventDefault();openDiagnosticsFiltered(attention.dataset.attentionFilter);return;}
  if(target.closest('[data-work-retry]')){loadWork();return;}
  const record=target.closest('[data-work-id]');if(record){if(!record.disabled) openWorkRecord(record.dataset.workId);return;}
  // 紧凑行整行可点:行里的按钮、链接和展开块各有自己的事,不算;选着文字时也不算,好让人把标题复制走。
  const row=target.closest('[data-work-row]');
  if(row && !target.closest('button,a,input,select,textarea,summary,label,details') && !String(globalThis.getSelection?.() || '')) openWorkRecord(row.dataset.workRow);
  const source=target.closest('[data-work-source]');if(source){setWorkFilters({role:'all',source:source.dataset.workSource});$('work-search').focus({preventScroll:true});$('work-search').scrollIntoView({block:'center'});}
  const filter=target.closest('[data-work-filter]');if(filter && !filter.disabled){setWorkFilters({role:filter.dataset.workFilter,state:filter.dataset.workState || '',remember:false});showView('work',true);}
  const verdict=target.closest('[data-automation-verdict]');
  if(verdict){
    const key=verdict.dataset.automationVerdict, next=$('automation-verdict').value===key?'':key;
    $('automation-verdict').value=next;renderAutomations();
    // 按钮随整张列表重画,焦点交回同一个建议的第一枚按钮;筛完它没了(取消筛选后总还在)就交给下拉框。
    const again=$('automation-list').querySelector?.(`[data-automation-verdict="${key}"]`);
    try{(again || $('automation-verdict')).focus({preventScroll:true});}catch(error){}
  }
}
function startWorkPlatform(){
  $('work-context-toggle').addEventListener('click',()=>{
    const panels=$('work-context-panels'), toggle=$('work-context-toggle');
    panels.hidden=!panels.hidden;
    toggle.setAttribute('aria-expanded',String(!panels.hidden));
    // 动作名跟着展开状态变;setDisabled 记下的旧名字作废,下次灰掉时说的才是现在这个动作。
    delete toggle.dataset.label;
    setIconControl(toggle,'i-sidebar',panels.hidden?'显示活动与来源':'收起活动与来源');
  });
  // 人一动筛选,「已应用上次的筛选」这句就不再成立;类别、状态、来源改了就存,搜索词不存。
  [['work-search','input',value=>WORK_QUERY=value],['work-role','change',value=>WORK_ROLE=value],['work-state','change',value=>WORK_STATE=value],['work-source','change',value=>WORK_SOURCE=value]].forEach(([id,event,set])=>$(id).addEventListener(event,e=>{
    set(e.target.value);WORK_FILTERS_RESTORED=false;if(id!=='work-search') saveWorkFilters();renderWorkPlatform();
  }));
  $('automation-search').addEventListener('input',e=>{AUTO_QUERY=e.target.value;renderAutomations();});
  $('automation-state').addEventListener('change',e=>{AUTO_STATE=e.target.value;renderAutomations();});
  $('automation-verdict').addEventListener('change',renderAutomations);
  $('work-detail-close').addEventListener('click',()=>$('work-detail').close());
  for(const [id,delta] of [['work-detail-prev',-1],['work-detail-next',1]]) $(id).addEventListener('click',()=>{
    const next=WORK_DETAIL_IDS[WORK_DETAIL_IDS.indexOf(WORK_DETAIL_ID)+delta];if(next) openWorkRecord(next,true);
  });
  $('work-detail-copy').addEventListener('click',async()=>{
    const item=WORK_DETAIL_ITEM;if(!item) return;
    try{await navigator.clipboard.writeText([item.title,item.summary || '',`记录 ID：${item.id}`,`记录状态：${workLabel(item)}`,item.execution?'这里尚无独立验证结果。':''].filter(Boolean).join('\n\n'));toast('已复制标题、摘要和记录信息','ok');}
    catch(error){toast('复制失败，请选中详情文字手动复制','bad');}
  });
  document.addEventListener('click',routeWorkClick);
  restoreWorkFilters();
  renderWorkPlatform();
}
