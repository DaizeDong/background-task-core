// Navigation composes projections by user intent, independently of backend modules.
const VIEW_GROUPS={
  overview:{label:'工作台',views:{overview:'工作台'}},
  work:{label:'工作记录',views:{work:'工作与结果',convos:'会话',llm:'模型调用'}},
  automations:{label:'自动化',views:{automations:'任务开关',pipelines:'同步与备份',tasks:'运行详情'}},
  resources:{label:'资源',views:{resources:'技能、插件与记忆',repos:'代码仓库',storage:'存储清理'}},
  diagnostics:{label:'诊断',views:{diagnostics:'技术问题与数据来源'}}
};
const VIEWS=Object.values(VIEW_GROUPS).flatMap(group=>Object.keys(group.views));
let CURVIEW=null;
const viewGroup=key=>Object.keys(VIEW_GROUPS).find(group=>key in VIEW_GROUPS[group].views) || 'overview';
function showView(key,push){
  if(!VIEWS.includes(key)) key='overview';
  CURVIEW=key;
  const group=viewGroup(key), definition=VIEW_GROUPS[group];
  $('page-refresh-state').textContent='';
  document.querySelectorAll('section[data-view]').forEach(section=>section.hidden=section.dataset.view!==key);
  document.querySelectorAll('#side .nv').forEach(link=>{
    const selected=link.dataset.view===group;
    link.classList.toggle('active',selected);link.parentElement.classList.toggle('active',selected);
    if(selected) link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
    link.tabIndex=selected?0:-1;
  });
  const tabs=Object.entries(definition.views);
  $('view-tabs').hidden=tabs.length===1;
  $('view-tabs').innerHTML=tabs.map(([id,label])=>`<a href="#${id}" data-open-view="${id}" ${key===id?'aria-current="page"':''}>${esc(label)}</a>`).join('');
  $('view-title').textContent=definition.label;
  document.title=definition.views[key]+' · 本机工作台';
  if(key==='storage' && !CXL) loadCxList();
  if(['overview','work'].includes(key) && !WORK && !WORK_LOADING) loadWork();
  if(key==='automations') renderAutomations();
  if(push && location.hash.slice(1)!==key) location.hash=key;
  $('view').classList.remove('all');window.scrollTo(0,0);
}
