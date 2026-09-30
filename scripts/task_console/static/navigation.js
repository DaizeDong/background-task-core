// Navigation composes projections by user intent, independently of backend modules.
const VIEW_GROUPS={
  overview:{label:'工作台',views:{overview:'工作台'}},
  work:{label:'工作记录',views:{work:'工作与结果',convos:'会话',llm:'模型调用'}},
  automations:{label:'自动化',views:{automations:'任务开关',pipelines:'同步与备份',tasks:'运行详情'}},
  resources:{label:'资源',views:{integrations:'控制台接入',resources:'客户端技能与记忆',repos:'代码仓库',storage:'存储清理'}},
  diagnostics:{label:'诊断',views:{diagnostics:'技术问题与数据来源'}}
};
const VIEWS=Object.values(VIEW_GROUPS).flatMap(group=>Object.keys(group.views));
let CURVIEW=null;
const viewGroup=key=>Object.keys(VIEW_GROUPS).find(group=>key in VIEW_GROUPS[group].views) || 'overview';
function showView(key,push){
  // 会话屏有一级下钻:#convos/<会话 id>[/<子代理 id>][/leaf=<uuid>],由可选面板 convchain 接。
  // 只按第一个 / 切;别的分区后面跟了东西就当没跟,照旧落到那一屏。
  let arg=null;
  const slash=typeof key==='string'?key.indexOf('/'):-1;
  if(slash>0){arg=key.slice(slash+1);key=key.slice(0,slash);}
  if(!VIEWS.includes(key)) key='overview';
  if(key!=='convos') arg=null;
  CURVIEW=key;
  const group=viewGroup(key), definition=VIEW_GROUPS[group];
  $('page-refresh-state').textContent='';
  document.querySelectorAll('section[data-view]').forEach(section=>section.hidden=section.dataset.view!==key);
  document.querySelectorAll('#side .nv').forEach(link=>{
    const selected=link.dataset.view===group;
    link.title=link.querySelector('.nav-link-title').textContent;
    link.classList.toggle('active',selected);link.parentElement.classList.toggle('active',selected);
    if(selected) link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
    link.tabIndex=selected?0:-1;
  });
  const tabs=Object.entries(definition.views);
  $('view-tabs').hidden=tabs.length===1;
  $('view-tabs').innerHTML=tabs.map(([id,label])=>`<a href="#${id}" data-open-view="${id}" ${key===id?'aria-current="page"':''}>${esc(label)}</a>`).join('');
  $('view-title').textContent=definition.label;
  document.title=definition.views[key]+' · 本机工作台';
  loadPageOnce(key);
  if(key==='automations') renderAutomations();
  // 对话链开着时地址写成带 id 的那个:写成裸 #convos 的话,紧跟着的 hashchange
  // 会把它读成「后退回列表」而关掉对话链。面板没载入就照旧只写分区名。
  const want=key==='convos' && typeof convoChainRoute==='function' ? convoChainRoute(arg,push) : key;
  if(push && location.hash.slice(1)!==want) location.hash=want;
  $('view').classList.remove('all');window.scrollTo(0,0);
}
