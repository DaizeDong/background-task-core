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

// ── 侧栏和标签上的徽章 ──
// 徽章是「合并」这件事的安全带:分区把东西收进了标签里,徽章负责让要人管的东西不用点进去也看得见。
// 少了它,合并就是纯粹的藏。徽章按视图记数(运行详情、代码仓库、存储清理、模型调用、技术问题),
// 侧栏那一枚是它所在分组的合计,标签上那一枚是它自己的数。
//
// 一枚徽章有四种样子,对应这一页「读取中、未检查、零、读取失败」不许长得一样的规矩:
// 第一次读取还没回来是「…」,读到零就藏起来,读不到是虚线框里的「?」,其余是数字。
// 数字之外的三种不靠颜色区分,靠字形,所以色弱和黑白截图里也分得开。
const BADGE_SOURCES={tasks:['/api/tasks'],repos:['/api/repos'],storage:['/api/sys','/api/mem'],llm:['/api/llmcall'],
  diagnostics:['/api/tasks','/api/repos','/api/sys','/api/mem']};
const BADGE_NAMES={tasks:'计划任务',repos:'代码仓库',storage:'磁盘和记忆索引',llm:'模型调用账本',diagnostics:'技术问题清单'};
const BADGE_COUNTS={};
// 至少读完过一次的来源(成功和失败都算)。API_READS 只留最新一条,刷新时那一条变回「读取中」,
// 拿它判断「第一次还没回来」的话,每点一次刷新所有徽章都会闪回「…」。
const BADGE_SETTLED=new Set();
// failures 是红、warnings 是琥珀。title 说清这个数是什么,一个只有数字的色块说不出自己是什么。
function setBadge(key,n,warn,title){
  const count=Math.max(0,Number(n) || 0);
  BADGE_COUNTS[key]={n:count,tone:warn?'warn':'bad',title:title || `${BADGE_NAMES[key] || key}：${count} 项要处理`};
  renderBadges();
}
function badgeState(key){
  const paths=BADGE_SOURCES[key] || [], name=BADGE_NAMES[key] || key;
  const failed=paths.map(path=>API_READS.get(path)).filter(read=>read && !read.pending && read.error);
  if(failed.length) return {state:'broken',title:`${name}读取失败：${failed.map(read=>read.error).join('；')}`};
  const count=BADGE_COUNTS[key];
  if(!count || paths.some(path=>!BADGE_SETTLED.has(path))) return {state:'pending',title:`正在读取${name}`};
  return {state:'count',n:count.n,tone:count.tone,title:count.title};
}
// 分组合计:一个来源读不到,整组就是「?」(合计里少了一块,写个数字等于把缺口说成没事);
// 一个还在读就是「…」。都读到了才相加,颜色按最严重的那一个。
function groupBadge(group){
  const states=Object.keys(VIEW_GROUPS[group]?.views || {}).filter(view=>BADGE_SOURCES[view]).map(view=>badgeState(view));
  if(!states.length) return null;
  const broken=states.filter(state=>state.state==='broken');
  if(broken.length) return {state:'broken',title:broken.map(state=>state.title).join('\n')};
  if(states.some(state=>state.state==='pending')) return {state:'pending',title:states.filter(state=>state.state==='pending').map(state=>state.title).join('\n')};
  const live=states.filter(state=>state.n);
  return {state:'count',n:live.reduce((sum,state)=>sum+state.n,0),tone:live.some(state=>state.tone==='bad')?'bad':'warn',
    title:live.map(state=>state.title).join('\n') || `${VIEW_GROUPS[group].label}：没有要处理的项`};
}
function badgeText(badge){
  return badge.state==='pending'?'…':badge.state==='broken'?'?':badge.n>99?'99+':String(badge.n);
}
function paintBadge(el,badge){
  if(!el) return;
  if(!badge){ el.hidden=true; return; }
  // 用 hidden 而不是一个「零」类来藏:侧栏收起时徽章绝对定位到图标角上,
  // 一个宽高为零但仍在文档流里的徽章会在那里留下一个看不见的偏移。
  el.hidden=badge.state==='count' && !badge.n;
  el.textContent=badgeText(badge);
  el.className='nav-badge '+(badge.state==='count'?badge.tone:badge.state);
  el.title=badge.title;
  el.setAttribute?.('aria-label',badge.title);
}
function renderBadges(){
  Object.keys(VIEW_GROUPS).forEach(group=>paintBadge($('bg-'+group),groupBadge(group)));
  document.querySelectorAll?.('#view-tabs [data-badge-for]').forEach(el=>paintBadge(el,badgeState(el.dataset.badgeFor)));
}
// api() 每读完一次就告诉这里,读失败的来源不用等哪个面板重画,徽章自己变成「?」。
function noteRead(path,state){
  if(!Object.values(BADGE_SOURCES).some(paths=>paths.includes(path))) return;
  if(!state.pending) BADGE_SETTLED.add(path);
  renderBadges();
}
// 徽章要在人点进去之前就有数,所以当前这一屏读完以后,把各徽章还没读过的来源补读一遍。
// 等当前屏读完再读,是为了不跟它抢:扫一遍仓库要十几秒。读过的(哪怕失败了)不再重复读,刷新归刷新按钮。
const BADGE_LOADERS={'/api/tasks':()=>load(),'/api/repos':()=>loadRepos(),'/api/sys':()=>loadSys(),
  '/api/mem':()=>loadMem(),'/api/llmcall':()=>loadLLM()};
function prefetchBadgeSources(){
  return Promise.allSettled(Object.entries(BADGE_LOADERS).filter(([path])=>!API_READS.has(path)).map(([,loader])=>loader()));
}

// ── 每个分组记住上次停在哪个标签 ──
// 从侧栏点进一个分组,回到上次在那里看的标签,而不是每次都落回第一个。
// 只是本机浏览器里的一点便利:存不进去(隐私窗口、禁用存储)就照旧落到分组的默认标签。
function groupEntry(group){
  const views=VIEW_GROUPS[group]?.views || {};
  let saved=null;
  try{ saved=localStorage.getItem('tc.view.'+group); }catch(error){}
  if(saved && Object.hasOwn(views,saved)) return saved;
  return Object.hasOwn(views,group) ? group : Object.keys(views)[0] || 'overview';
}

// ── 从哪儿来,回哪儿去 ──
// 下钻(从一行的「查看详情」、一个指标格、一条问题跳到别的屏)会把人带离原处。这里记下来源,
// 目的屏的标签栏里给一个「← 返回 来源」,Esc 退到最后一层时也回去一次。回去之后来源就用掉了,
// 再按 Esc 不会接着往回走。从侧栏、标签或者直接打开地址到达的屏没有来源:
// 不加区分地「Esc 就后退」的话,关掉对话框之后紧跟的那一下 Esc 会把人带离这一页。
// 正文里换屏的链接(工作台「要处理」的芯片、「全部技术问题 →」、诊断的「查看进度」)也是下钻:
// 芯片认 data-attention-filter,别的链接带 data-drill。标签栏的 [data-open-view] 不带它,点标签不算下钻。
const DRILL_TARGETS='[data-task],[data-fr],[data-review-repo],[data-work-filter],[data-integration-open],[data-goto]:not([data-goto^="#"]),[data-fix="commitpush"],[data-attention-filter],[data-drill]';
let DRILL_FROM=null, VIEW_ORIGIN=null;
// 点击的捕获阶段调这里:这一下要是下钻,就记下点之前在哪一屏,由紧接着的那次 showView 取走。
function markDrill(target){
  DRILL_FROM=target?.closest?.(DRILL_TARGETS) ? CURVIEW : null;
  return DRILL_FROM;
}
function noteOrigin(key,reset){
  const from=DRILL_FROM;DRILL_FROM=null;
  if(reset) VIEW_ORIGIN=null;
  else if(from && from!==key) VIEW_ORIGIN={view:from,to:key};
  // 同一屏再进一次(地址跳转回来的 hashchange、同一屏里的下钻)保留来源;去了别的屏就作废。
  else if(VIEW_ORIGIN && VIEW_ORIGIN.to!==key) VIEW_ORIGIN=null;
}
const viewLabel=key=>VIEW_GROUPS[viewGroup(key)].views[key] || key;
function originChip(){
  if(!VIEW_ORIGIN || VIEW_ORIGIN.to!==CURVIEW) return '';
  return `<a class="view-origin" href="#${esc(VIEW_ORIGIN.view)}" data-origin-back title="回到刚才所在的「${esc(viewLabel(VIEW_ORIGIN.view))}」">← 返回 ${esc(viewLabel(VIEW_ORIGIN.view))}</a>`;
}
// Esc 的最后一层:这一屏是下钻来的,就回来源一次,并把来源用掉。
function returnToOrigin(){
  if(!VIEW_ORIGIN || VIEW_ORIGIN.to!==CURVIEW) return false;
  const origin=VIEW_ORIGIN.view;VIEW_ORIGIN=null;
  showView(origin,true,{reset:true});
  return true;
}

function showView(key,push,options){
  // 会话屏有一级下钻:#convos/<会话 id>[/<子代理 id>][/leaf=<uuid>],由可选面板 convchain 接。
  // 仓库屏带选中的仓:#repos/<仓名>,由 repositories.js 的 repoRoute 接,浏览器后退就回到上一个选中的仓。
  // 只按第一个 / 切;别的分区后面跟了东西就当没跟,照旧落到那一屏。
  let arg=null;
  const slash=typeof key==='string'?key.indexOf('/'):-1;
  if(slash>0){arg=key.slice(slash+1);key=key.slice(0,slash);}
  if(!VIEWS.includes(key)) key='overview';
  if(key!=='convos' && key!=='repos') arg=null;
  // 同一屏里换一个仓(后退、前进)不是换页,不把人送回页顶。
  const sameRepos=key==='repos' && CURVIEW==='repos';
  // 下钻顺手设在目的屏上的筛选只管这一趟(review.js / workbench.js 不把它存进本机浏览器)。
  // 人离开那一屏(侧栏、标签、浏览器后退、Esc 回来源)时放回人自己上次选的;
  // 离开本身又是一次下钻时不放回:从那里 Esc 回来,这一屏还该是刚才的样子。
  if(CURVIEW && CURVIEW!==key && !DRILL_FROM){
    if(CURVIEW==='diagnostics' && typeof endReviewDrillScope==='function') endReviewDrillScope();
    if(CURVIEW==='work' && typeof endWorkDrillFilters==='function') endWorkDrillFilters();
  }
  noteOrigin(key,options?.reset);
  CURVIEW=key;
  const group=viewGroup(key), definition=VIEW_GROUPS[group];
  try{ localStorage.setItem('tc.view.'+group,key); }catch(error){}
  document.querySelectorAll('section[data-view]').forEach(section=>section.hidden=section.dataset.view!==key);
  document.querySelectorAll('#side .nv').forEach(link=>{
    const selected=link.dataset.view===group;
    link.title=link.querySelector('.nav-link-title').textContent;
    link.classList.toggle('active',selected);link.parentElement.classList.toggle('active',selected);
    if(selected) link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
    link.tabIndex=selected?0:-1;
  });
  const tabs=Object.entries(definition.views), back=originChip();
  // 只有一个视图的分组不显示标签,但下钻到那里时标签栏照样出来,好放「← 返回」。
  $('view-tabs').hidden=tabs.length===1 && !back;
  $('view-tabs').innerHTML=(tabs.length>1?tabs.map(([id,label])=>`<a href="#${id}" data-open-view="${id}" ${key===id?'aria-current="page"':''}><span>${esc(label)}</span>${BADGE_SOURCES[id]?`<span class="nav-badge pending" data-badge-for="${id}">…</span>`:''}</a>`).join(''):'')+back;
  renderBadges();
  // 标题说清在哪一个标签上:「自动化 · 运行详情」。只写分组名的话,侧栏亮着的是同一个词,标题等于没说。
  $('view-title').textContent=tabs.length>1?`${definition.label} · ${definition.views[key]}`:definition.label;
  document.title=definition.views[key]+' · 本机工作台';
  loadPageOnce(key);
  renderRefreshAge();
  if(key==='automations') renderAutomations();
  // 修复进度每次进入自动化的任一标签都重读,不只第一次:工单在别处(工作记录、Discord)推进,这里没有别的消息来源。
  if(group==='automations') loadRepairs();
  // 对话链开着时地址写成带 id 的那个:写成裸 #convos 的话,紧跟着的 hashchange
  // 会把它读成「后退回列表」而关掉对话链。面板没载入就照旧只写分区名。
  const want=key==='convos' && typeof convoChainRoute==='function' ? convoChainRoute(arg,push)
    : key==='repos' && typeof repoRoute==='function' ? repoRoute(arg) : key;
  if(push && location.hash.slice(1)!==want) location.hash=want;
  // 批量操作条只属于运行详情:换到别的分区就收起来,选中的任务留着,回来还在。
  renderBulk();
  if(!sameRepos) window.scrollTo(0,0);
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
  // 手机上仓库详情盖着列表时,Esc 回到列表;宽屏上没有这一层。
  repos:()=>typeof repoEscape==='function' && repoEscape(),
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
  // 这一屏已经没有可退的层了:是下钻来的就回来源一次。
  if(returnToOrigin()){ event.preventDefault(); return true; }
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
