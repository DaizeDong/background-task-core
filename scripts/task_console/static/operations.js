// Shared page controls use existing loaders and in-memory snapshots only.
let PAGE_REFRESHING=false;
const PAGE_READS={
  overview:[loadWork,load,loadComponents],
  work:[loadWork],automations:[load],
  resources:[loadComponents,loadMaint,loadMem],
  diagnostics:[load,loadComponents,loadSelfcheck,loadRepos,loadSys,loadMem,loadConvos],
  pipelines:[loadComponents,load],tasks:[load],repos:[loadRepos],
  storage:[loadComponents,loadMaint,loadMem,loadSys,loadCodex,loadCxList],
  convos:[loadConvos],llm:[loadLLM]
};
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
    automations:()=>({tasks:DATA,filters:{query:AUTO_QUERY,state:AUTO_STATE}}),
    resources:()=>({components:catalogComponents(),maintenance:MAINT,memory:MEM}),
    diagnostics:()=>({tasks:DATA,components:COMPONENTS,selfcheck:SCK,repositories:REPOS,system:SYS,memory:MEM}),
    pipelines:()=>({components:COMPONENTS,tasks:DATA}),
    tasks:()=>({tasks:DATA,visibleTaskNames:VIEW.map(row=>row.name)}),
    repos:()=>({repositories:REPOS,filters:{query:value('rpq'),account:value('rpacc'),kind:value('rpkind'),visibility:value('rpvis'),state:RP_STATE,issue:RP_ISSUE}}),
    storage:()=>({components:catalogComponents(),maintenance:MAINT,memory:MEM,system:SYS,codex:CODEX,cleanup:CXL,cleanupLibrary:value('cxwhich')}),
    convos:()=>({conversations:CONVOS,filters:{query:CV_QUERY,humanOnly:CV_HUMAN_ONLY,sort:CV_SORT}}),
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
  toast('已导出当前已加载数据');
}
