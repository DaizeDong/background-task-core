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
  // 修复进度每次进入自动化的任一标签都重读,不只第一次:工单在别处(工作记录、Discord)推进,这里没有别的消息来源。
  if(group==='automations') loadRepairs();
  // 对话链开着时地址写成带 id 的那个:写成裸 #convos 的话,紧跟着的 hashchange
  // 会把它读成「后退回列表」而关掉对话链。面板没载入就照旧只写分区名。
  const want=key==='convos' && typeof convoChainRoute==='function' ? convoChainRoute(arg,push) : key;
  if(push && location.hash.slice(1)!==want) location.hash=want;
  // 批量操作条只属于运行详情:换到别的分区就收起来,选中的任务留着,回来还在。
  renderBulk();
  window.scrollTo(0,0);
}

// ── 键盘约定 ──
// 这里没有快捷键,只有两条通行的约定:Esc 退一层,Enter 提交搜索框。逻辑放在这里而不是 events.js,
// 是为了让 node:vm 台架能直接测它;events.js 只负责把 keydown 接过来。
//
// Esc 一次只退一层,而且只退**看得见**的那一层。按分区登记:每一项返回 true 表示这次按键退掉了一层。
// 不在表里的分区什么都不做 : 以前任何分区按 Esc 都会清空运行详情里的勾选,
// 人在仓库页按一下,另一屏里那份看不见的选择就没了。
const ESC_STEPS={
  tasks:()=>{
    if(OPEN_DETAIL){ collapseTaskDetail(); return true; }
    if(sel.size){ sel.clear(); if(DATA) render(); else renderBulk(); return true; }
    return false;
  },
  llm:()=>closeCall(),
  // 对话链是可选面板,没载入时这一屏没有可退的层。
  convos:()=>typeof convoChainEscape==='function' && convoChainEscape()
};
// 能打字的控件。勾选框、单选、下拉和按钮不算:焦点停在勾选框上按 Esc,该退的是页面上那一层,
// 以前把所有 INPUT 都当成文本框,于是刚勾完一个任务按 Esc 只是让勾选框失焦,要按第二次才清掉选择。
const TEXT_ENTRY_TYPES=['text','search','number','url','email','tel','password'];
function isTextEntry(el){
  if(!el || !el.tagName) return false;
  if(el.isContentEditable) return true;
  const tag=String(el.tagName).toUpperCase();
  if(tag==='TEXTAREA') return true;
  return tag==='INPUT' && TEXT_ENTRY_TYPES.includes(String(el.type || 'text').toLowerCase());
}
// 对话框、弹出层和下拉菜单自己会处理 Esc。它们开着时这里一律不动,否则一次按键会同时关掉两层。
function overlayOpen(){
  if(document.querySelector('dialog[open]') || document.querySelector('.dropdown-menu.show')) return true;
  // 不认识 :popover-open 的浏览器会在这里抛错,那时它也不可能有打开的弹出层。
  try{ return !!document.querySelector(':popover-open'); }catch(error){ return false; }
}
function handleEscape(event){
  if(event.key!=='Escape' || event.isComposing || event.defaultPrevented) return false;
  if(event.ctrlKey || event.metaKey || event.altKey) return false;
  if(overlayOpen()) return false;
  const el=document.activeElement;
  if(isTextEntry(el)){
    // 搜索框里有字:先清空,再按一次才离开。清空后自己发一次 input 事件,
    // 各分区原有的筛选监听照常重画。浏览器自带的清空要拦掉:它先失焦就不发 input,框空了表还按旧词筛着。
    // 多行文本和数字框只失焦,不清:那里的内容是人写的正文,不是一个可以随手重来的筛选词。
    event.preventDefault();
    const clearable=String(el.tagName).toUpperCase()==='INPUT' && ['text','search'].includes(String(el.type || 'text').toLowerCase());
    if(clearable && el.value){
      el.value='';
      el.dispatchEvent(new Event('input',{bubbles:true}));
    }else el.blur();
    return true;
  }
  const step=ESC_STEPS[CURVIEW];
  if(step && step()){ event.preventDefault(); return true; }
  return false;
}

// Enter 提交搜索。搜索都是边打边筛的,所以 Enter 只做「筛完之后下一步」:
// 要发请求的框立刻发,不再等防抖;只剩一个结果的列表直接打开它。零个或好几个结果时什么都不做,
// 免得 Enter 替人在一堆结果里挑一个他没看过的。
function openOnlyWorkRecord(){
  if(!WORK || !WORK.available) return;
  const rows=selectedWorkRows();
  if(rows.length===1) openWorkRecord(rows[0].id);
}
// 仓库列表里伴生仓会把宿主一起带出来,所以「第一个」取第一个**自己匹配**的仓,不取第一行。
function selectFirstRepo(){
  if(!REPOS || !REPOS.available) return;
  const q=($('rpq').value || '').trim().toLowerCase();
  const acc=$('rpacc').value, kind=$('rpkind').value, vis=$('rpvis').value;
  const listed=[...$('rplist').querySelectorAll('[data-rp]')].map(row=>row.dataset.rp);
  const first=listed.map(name=>REPOS.repos.find(repo=>repo.name===name))
    .find(repo=>repo && rpMatches(repo,q,acc,kind,vis));
  if(!first) return;
  RP_SEL=first.name;renderRepoList();
}
const SEARCH_ENTER={
  lmq:()=>commitCallSearch(),
  'cv-search':()=>loadConvos(),
  q:()=>openOnlyTask(),
  'review-search':()=>openOnlyReview(),
  'work-search':()=>openOnlyWorkRecord(),
  rpq:()=>selectFirstRepo()
};
function handleSearchEnter(event){
  // 输入法组字时的 Enter 是在确认候选字,不是提交。按住不放的连发也不算。
  if(event.key!=='Enter' || event.isComposing || event.repeat) return false;
  if(event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return false;
  const run=event.target && SEARCH_ENTER[event.target.id];
  if(!run) return false;
  event.preventDefault();run();
  return true;
}
