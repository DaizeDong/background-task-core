// Shared page controls use existing loaders and in-memory snapshots only.
let PAGE_REFRESHING=false;
// Each panel owns its filters. This shared control only calls their existing renderers.
function resetFilters(scope){
  const clear=ids=>ids.forEach(id=>$(id).value='');
  if(scope==='tasks'){clear(['q','cat','task-verdict']);$('only').checked=false;$('hideoff').checked=false;if(DATA) render();}
  if(scope==='repos'){clear(['rpq','rpacc','rpkind','rpvis','rpissue']);RP_STATE='';RP_ISSUE='';renderRepos();}
  if(scope==='catalog'){CATALOG_QUERY='';CATALOG_KIND='';CATALOG_CLIENT='';CATALOG_STATE='';HEALTH_STATE='';renderCatalog();}
  if(scope==='runtime'){clear(['runtime-search','runtime-state']);RUNTIME_QUERY='';RUNTIME_STATE='';renderSkills();renderClientPlugins();}
  if(scope==='convos'){clear(['cv-search']);CV_QUERY='';CV_HUMAN_ONLY=false;loadConvos();}
  if(scope==='llm'){clearTimeout(LMQT);clear(['lmq','lmprov','lmcaller','lmok']);Object.assign(LMQ,{q:'',provider:'',caller:'',ok:'',offset:0});LMOPEN=null;loadCalls();}
  if(scope==='diagnostics'){clear(['review-search']);$('review-filter').value='all';REVIEW_QUERY='';REVIEW_FILTER='all';renderTodo();}
  if(scope==='pipelines'){PIPELINE_QUERY='';clear(['pipeline-task-search','pipeline-verdict']);renderPipelines();}
  if(scope==='work') setWorkFilters();
  if(scope==='automations'){AUTO_QUERY='';AUTO_STATE='';clear(['automation-search','automation-state','automation-verdict']);renderAutomations();}
  syncResetFilters();
}
// 每个范围里此刻生效的筛选有几项。能读控件就读控件:搜索框有防抖,状态变量要过一会儿
// 才跟上,而按钮该在敲下第一个字时就亮。没有控件的筛选(仓库状态条、只看人类消息)读状态变量。
const RESET_FILTER_COUNTS={
  tasks:value=>[value('q'),value('cat'),value('task-verdict'),!!$('only')?.checked,!!$('hideoff')?.checked],
  repos:value=>[value('rpq'),value('rpacc'),value('rpkind'),value('rpvis'),value('rpissue'),RP_STATE],
  catalog:()=>[CATALOG_QUERY,CATALOG_KIND,CATALOG_CLIENT,CATALOG_STATE,HEALTH_STATE],
  runtime:value=>[value('runtime-search'),value('runtime-state')],
  convos:value=>[value('cv-search'),CV_HUMAN_ONLY],
  llm:value=>[value('lmq'),value('lmprov'),value('lmcaller'),value('lmok')],
  diagnostics:value=>[value('review-search'),value('review-filter')!=='all' && value('review-filter')],
  pipelines:value=>[PIPELINE_QUERY,value('pipeline-task-search'),value('pipeline-verdict')],
  // 工作记录的默认状态随类别变:「全部记录」默认看全部状态,其余默认只看未结束的。
  work:()=>[WORK_ROLE!=='work',WORK_STATE!==(WORK_ROLE==='all'?'':'unfinished'),WORK_SOURCE,WORK_QUERY],
  automations:value=>[AUTO_QUERY,AUTO_STATE,value('automation-verdict')]
};
// 清除之后按钮随即变灰、焦点跟着丢掉,所以把焦点交给这个范围的搜索框,接着就能输入新的条件。
const RESET_FILTER_SEARCH={tasks:'q',repos:'rpq',catalog:'catalog-search',runtime:'runtime-search',convos:'cv-search',llm:'lmq',
  diagnostics:'review-search',pipelines:'pipeline-task-search',work:'work-search',automations:'automation-search'};
function activeFilterCount(scope){
  const value=id=>String($(id)?.value || '').trim();
  return (RESET_FILTER_COUNTS[scope]?.(value) || []).filter(Boolean).length;
}
// 清除筛选按钮常驻:没有筛选时灰着并说明原因,有筛选时说清会清掉几项。
// 原来有的页藏、有的页一直亮着,同一个按钮两种约定,而一直亮着的那种点了什么也不发生。
function syncResetFilters(){
  document.querySelectorAll?.('[data-reset-filters]').forEach(button=>{
    const count=activeFilterCount(button.dataset.resetFilters);
    button.dataset.label='清除筛选';
    setDisabled(button,count?'':'当前没有生效的筛选');
    if(count) button.title=`清除筛选（${count} 项）`;
  });
}
const PAGE_READS={
  overview:[loadWork,load,loadComponents],
  work:[loadWork],automations:[load,loadRepairs],
  integrations:[()=>typeof loadIntegrations==='function'?loadIntegrations():Promise.reject(new Error('接入面板加载失败'))],
  resources:[loadComponents,loadMaint,loadMem],
  diagnostics:[load,loadComponents,loadSelfcheck,loadRepos,loadSys,loadMem,loadConvos],
  pipelines:[loadComponents,load,loadRepairs],tasks:[load,loadRepairs],repos:[loadRepos],
  storage:[loadComponents,loadMaint,loadMem,loadSys,loadCodex,loadCxList],
  convos:[loadConvos,()=>typeof reloadConvoChain==='function'?reloadConvoChain():undefined],llm:[loadLLM]
};
const PAGE_INITIAL_READS=new Map();
function loadPageOnce(view){
  if(!PAGE_INITIAL_READS.has(view)){
    PAGE_INITIAL_READS.set(view,Promise.allSettled((PAGE_READS[view]||[]).map(load=>load())));
  }
  return PAGE_INITIAL_READS.get(view);
}
async function refreshPage(){
  if(PAGE_REFRESHING) return;
  PAGE_REFRESHING=true;
  const view=CURVIEW, button=$('page-refresh'), note=$('page-refresh-state');
  const before=API_SEQUENCE;
  button.disabled=true; note.textContent='读取中';
  try{
    const results=await Promise.allSettled(PAGE_READS[view].map(load=>load()));
    const failed=[...API_READS.values()].filter(read=>read.sequence>before && read.error);
    const broken=failed.length+results.filter(result=>result.status==='rejected').length;
    const text=broken ? `${broken} 项读取失败或不可用` : '读取完成';
    if(CURVIEW===view) note.textContent=text;
    if(broken) toast(text,'bad');
  }finally{PAGE_REFRESHING=false;button.disabled=false;}
}
function pageSnapshot(view){
  const value=id=>$(id)?.value || '';
  const snapshots={
    overview:()=>({work:WORK,tasks:DATA,components:COMPONENTS}),
    work:()=>({work:WORK,filters:{query:WORK_QUERY,role:WORK_ROLE,state:WORK_STATE,source:WORK_SOURCE}}),
    // 导出的 filters 要和屏幕上实际在用的筛选一一对上,少记一个,导出的列表就解释不了。
    automations:()=>({tasks:DATA,repairs:REPAIRS,filters:{query:AUTO_QUERY,state:AUTO_STATE,verdict:value('automation-verdict')}}),
    integrations:()=>({integrations:typeof INTEGRATIONS==='undefined'?null:INTEGRATIONS}),
    resources:()=>({components:catalogComponents(),maintenance:MAINT,memory:MEM}),
    diagnostics:()=>({tasks:DATA,components:COMPONENTS,selfcheck:SCK,repositories:REPOS,system:SYS,memory:MEM}),
    pipelines:()=>({components:COMPONENTS,tasks:DATA,repairs:REPAIRS,filters:{query:value('pipeline-task-search'),verdict:value('pipeline-verdict'),issueQuery:PIPELINE_QUERY}}),
    tasks:()=>({tasks:DATA,repairs:REPAIRS,visibleTaskNames:VIEW.map(row=>row.name),
      filters:{query:value('q'),category:value('cat'),verdict:value('task-verdict'),onlyProblems:!!$('only')?.checked,hideDisabled:!!$('hideoff')?.checked}}),
    repos:()=>({repositories:REPOS,filters:{query:value('rpq'),account:value('rpacc'),kind:value('rpkind'),visibility:value('rpvis'),state:RP_STATE,issue:RP_ISSUE}}),
    storage:()=>({components:catalogComponents(),maintenance:MAINT,memory:MEM,system:SYS,codex:CODEX,cleanup:CXL,cleanupLibrary:value('cxwhich')}),
    convos:()=>({conversations:CONVOS,filters:{query:CV_QUERY,humanOnly:CV_HUMAN_ONLY,sort:CV_SORT},chain:typeof convoChainSnapshot==='function'?convoChainSnapshot():null}),
    llm:()=>({usage:LLM,calls:LMROWS,total:LMTOTAL,query:{...LMQ}})
  };
  return {schemaVersion:1,view,exportedAt:new Date().toISOString(),scope:'loaded_snapshot',
    limitation:view==='llm'?'calls_current_page':view==='convos'?'server_delivered_records':'loaded_data',
    reads:[...API_READS.values()],data:snapshots[view]()};
}
function exportPage(){
  const blob=new Blob([JSON.stringify(pageSnapshot(CURVIEW),null,2)],{type:'application/json'});
  const url=URL.createObjectURL(blob), link=document.createElement('a');
  link.href=url;link.download=`console-${CURVIEW}-${new Date().toISOString().replace(/[:.]/g,'-')}.json`;
  link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  toast('已导出当前已加载数据','ok');
}
