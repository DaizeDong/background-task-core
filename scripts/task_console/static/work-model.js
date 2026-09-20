// Pure projections: one record can appear in a list, result panel and activity stream.
let WORK=null,WORK_LOADING=false,WORK_PENDING=null,WORK_QUERY='',WORK_ROLE='work',WORK_STATE='',WORK_SOURCE='',AUTO_QUERY='',AUTO_STATE='';
const workState=item=>item.role==='agent_work'?(item.execution?.state || item.state):item.state;
const WORK_STATES={queued:'排队中',running:'执行中',pending:'待办',doing:'进行中',blocked:'遇到阻碍',stalled:'已停滞',done:'已完成',failed:'执行失败',cancelled:'已取消',stopped:'已停止',review_unavailable:'无法复核',reconcile:'结果待核实',snoozed:'已延后',notified:'已提醒'};
const ROLE_LABELS={agent_work:'Agent 工作',tracked_item:'待办事项',signal:'通知与线索'};
const workLabel=item=>WORK_STATES[workState(item)] || workState(item) || '状态未记录';
const workActive=item=>item.role==='agent_work' && ['queued','running'].includes(workState(item));
const workResult=item=>item.role==='agent_work' && workState(item)==='done';
const workTracked=item=>item.role==='tracked_item' && ['pending','doing','blocked','snoozed'].includes(item.state);
const workSourceLabel=source=>({'agent-center:work':'Agent 工作单','claude-session':'Claude 会话','claude-cc-session':'Claude 会话（cc）','user-request':'用户交办','user':'用户记录','manual-cli':'手动记录（CLI）','manual-claude':'手动记录（Claude）','manual-cc':'手动记录（cc）','manual-session':'会话手动记录'}[source] || source || '来源未记录');
const WORK_EVENTS={status_change:'更改状态',notified:'已发出提醒',created:'创建记录',idempotent_replay:'收到重复请求，沿用原记录',updated:'更新记录',rearmed:'重新安排提醒'};
const workEventLabel=event=>(WORK_EVENTS[event.event_type] || event.event_type || '活动类型未记录')+(event.to_state?' · '+(WORK_STATES[event.to_state] || event.to_state):'');
function workRows(feed,options={}){
  if(!feed?.available) return [];
  const {role='work',state='',query='',source=''}=options, q=query.trim().toLowerCase();
  return feed.items.filter(item=>(role==='all' || role==='work' && item.role!=='signal' || item.role===role) &&
    (!source || item.source===source) && (!state || (state==='active'?workActive(item):state==='tracked_active'?workTracked(item):workState(item)===state)) &&
    (!q || [item.title,item.summary,item.source,workSourceLabel(item.source),item.project,item.id].join(' ').toLowerCase().includes(q)))
    .sort((a,b)=>String(b.updated_at || '').localeCompare(String(a.updated_at || '')) || a.id.localeCompare(b.id));
}
function workProjection(feed){
  const rows=workRows(feed);
  return {active:rows.filter(workActive),results:rows.filter(workResult),
    tracked:rows.filter(workTracked),
    interrupted:rows.filter(item=>item.role==='agent_work' && !workActive(item) && !workResult(item)),
    // No owner decision contract is connected. Never derive approvals from failed work.
    decisions:null};
}
function automationRows(rows,query='',state=''){
  const q=query.trim().toLowerCase();
  return rows.filter(row=>(!q || [row.name,row.desc,row.cat].join(' ').toLowerCase().includes(q)) &&
    (!state || (state==='disabled'?row.state==='Disabled':state==='enabled'?['Ready','Running','Queued'].includes(row.state):row.state==='Running')));
}
