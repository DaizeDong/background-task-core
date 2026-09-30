// Session metadata is paged by the server; only expanded projects render rows.
let CONVOS=null, CV_HUMAN_ONLY=false, CV_OPEN={}, CV_QUERY="", CV_OPEN_QUERY="", CV_SORT="new";
let CV_VERSION=0, CV_LOADING=false, CV_REQUEST=null, CV_TIMER=null, CV_OBSERVER=null;
const CV_PAGES=new Map(), CV_EPH_KEY="::eph::";
const CV_DETAILS=new Set();
const CV_SESSION_ID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CV_SRC={rename:"你指定的会话标题","ai-title":"自动生成的窗口标题",summary:"压缩时写的概括",
  "first-message":"第一条消息",slug:"自动代号",id:"只有会话号"};
const cvKey=g=>g.id || g.cwd;
const cvAge=h=>h==null?"时间未记录":h<1?"刚刚":h<24?Math.floor(h)+" 小时前":Math.floor(h/24)+" 天前";

function cvInvalidate(){
  CV_VERSION++;CV_REQUEST?.abort();CV_REQUEST=null;CV_OBSERVER?.disconnect();
  for(const page of CV_PAGES.values()) page.controller?.abort();
  CV_PAGES.clear();
}
function cvQuery(extra={}){
  const q={q:CV_QUERY.trim(),human_only:CV_HUMAN_ONLY?'1':'0',limit:'40',...extra};
  return '/api/convos?'+Object.entries(q).filter(([,v])=>v!=null && v!=='')
    .map(([k,v])=>k+'='+encodeURIComponent(v)).join('&');
}
async function loadConvos(){
  clearTimeout(CV_TIMER);cvInvalidate();
  const version=CV_VERSION, controller=new AbortController();CV_REQUEST=controller;
  const timer=setTimeout(()=>controller.abort(),45000);
  CV_LOADING=true;$('cvnote').textContent='读取中';
  try{
    const result=await api(cvQuery(),{signal:controller.signal});
    if(version!==CV_VERSION) return;
    CONVOS=result;$('cvnote').textContent='';renderConvos();
  }catch(error){
    if(version===CV_VERSION) $('cvnote').textContent='读取失败：'+error.message;
  }finally{
    clearTimeout(timer);
    if(version===CV_VERSION){CV_LOADING=false;CV_REQUEST=null;cvObserve();}
  }
}
function cvSearchChanged(){
  CV_QUERY=$('cv-search').value;clearTimeout(CV_TIMER);cvInvalidate();
  CV_LOADING=true;$('cvnote').textContent='等待搜索';
  CV_TIMER=setTimeout(loadConvos,280);
}
function cvCount(){
  if(!CONVOS?.available) return;
  const loaded=CONVOS.groups.reduce((n,g)=>n+g.shown.length,0);
  const matched=CONVOS.summary.matched ?? CONVOS.summary.files;
  $('cv-match').textContent=`已加载 ${loaded} / ${matched}`;
}
function cvRows(g){
  return g.shown.map(r=>{
    const valid=CV_SESSION_ID.test(String(r.id||''));
    const chain=valid && typeof openConvoChain==='function';
    const manage=valid && typeof cvOpenManager==='function';
    const disabled=typeof ConsoleActions!=='undefined' && ConsoleActions.readOnly;
    return `<div class="cv-r${r.humanSeen>=2?' human':''}" data-cvfile="${esc(r.file)}"${chain?` data-cvid="${esc(r.id)}"`:''}>
      <div class="cv-main">${chain?`<button class="t" data-cvopen="${esc(r.id)}" title="查看会话：${esc(r.title)}">${esc(r.title)}</button>`:`<span class="t">${esc(r.title)}</span>`}
        ${r.preview&&r.preview!==r.title?`<span class="pv">${esc(r.preview)}</span>`:''}</div>
      <span class="cv-updated">${cvAge(r.ageHours)}</span>
      <span class="cv-row-actions">${chain?`<button class="icon-only mini cv-open" data-cvopen="${esc(r.id)}" title="查看会话"><svg class="ic" aria-hidden="true"><use href="#i-eye"/></svg><span class="control-label">查看会话</span></button>`:''}
        ${manage?`<button class="icon-only mini cv-danger" data-cvdelete="${esc(r.id)}"${disabled?' disabled':''} title="删除"><svg class="ic" aria-hidden="true"><use href="#i-trash"/></svg><span class="control-label">删除</span></button>`:''}</span>
      <details class="cv-details" data-cvdetail="${esc(r.id)}"${CV_DETAILS.has(r.id)?' open':''}><summary title="更多操作" aria-label="更多操作"><svg class="ic" aria-hidden="true"><use href="#i-more"/></svg></summary><div class="cv-detail-body">
        ${manage?`<span class="cv-row-actions"><button class="icon-only mini" data-cvrename="${esc(r.id)}"${disabled?' disabled':''} title="重命名"><svg class="ic" aria-hidden="true"><use href="#i-edit"/></svg><span class="control-label">重命名</span></button>
        <button class="icon-only mini cv-handle" data-cvmove="${esc(r.id)}" data-cvdrag="${esc(r.id)}" draggable="${!disabled}"${disabled?' disabled':''} title="点击选择目标项目，也可拖到其他项目上"><svg class="ic" aria-hidden="true"><use href="#i-move"/></svg><span class="control-label">移动</span></button></span>`:''}
        <span>标题来源：${esc(CV_SRC[r.titleFrom]||r.titleFrom||'未记录')}</span><span>文件大小：${kb(r.bytes)}</span>
        <span>已识别的用户提问：${r.humanSeen ?? '未记录'}${r.partial?'（只读取了部分内容）':''}</span>
        <span class="cv-location">会话编号：${esc(r.id)}</span><span class="cv-location">文件位置：${esc(r.file)}</span>
        ${!valid?'<span class="warn">会话编号无法识别，可通过文件位置检查原始记录。</span>':''}
        <button class="icon-only mini" data-cvcopy="${esc(r.file)}" title="复制文件路径"><svg class="ic" aria-hidden="true"><use href="#i-copy"/></svg><span class="control-label">复制文件路径</span></button></div></details>
    </div>`;
  }).join('');
}
function cvPage(g){
  const id=cvKey(g), state=CV_PAGES.get(id), more=g.hasMore ?? g.truncated;
  return `<div class="cv-page" data-cvpage="${esc(id)}"><span${state?.error?' class="bad" role="alert"':''}>${state?.error?esc(state.error):`已加载 ${g.shown.length} / ${g.count} 个会话`}</span>
    ${more?`<button data-cvmore="${esc(id)}"${state?.busy?' disabled':''}>${state?.busy?'加载中…':state?.error?'重试加载':'加载更多'}</button>`:'<span>已全部显示</span>'}</div>`;
}
function cvGroupElement(id){
  return [...$('cvgroups').querySelectorAll('[data-cvproject]')].find(el=>el.dataset.cvproject===id);
}
function cvPaintGroup(g){
  const el=cvGroupElement(cvKey(g));
  if(!el) return;
  const top=window.scrollY;
  el.querySelector('.cv-list').innerHTML=el.classList.contains('open')?cvLocation(g)+cvRows(g)+cvPage(g):'';
  window.scrollTo({top,behavior:'instant'});cvCount();cvObserve();
}
async function cvLoadMore(id){
  const g=CONVOS?.groups.find(x=>cvKey(x)===id);
  if(!g || !g.nextCursor || CV_LOADING || CV_PAGES.get(id)?.busy) return;
  const version=CV_VERSION, cursor=g.nextCursor, controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),45000);
  const state={busy:true,error:null,controller};CV_PAGES.set(id,state);cvPaintGroup(g);
  try{
    const result=await api(cvQuery({group:id,cursor}),{signal:controller.signal});
    if(version!==CV_VERSION || g.nextCursor!==cursor) return;
    if(!result.available) throw new Error(result.reason || '会话目录暂时无法读取');
    const page=result.groups.find(x=>cvKey(x)===id);
    if(!page) throw new Error('项目位置已改变，请刷新列表');
    const known=new Set(g.shown.map(x=>x.id));
    const added=page.shown.filter(x=>!known.has(x.id));
    Object.assign(g,page,{shown:[...g.shown,...added]});state.error=null;
  }catch(error){if(version===CV_VERSION) state.error='加载失败：'+error.message;}
  finally{
    clearTimeout(timer);
    if(version===CV_VERSION){state.busy=false;state.controller=null;cvPaintGroup(g);}
  }
}
function cvObserve(){
  CV_OBSERVER?.disconnect();
  if(CV_LOADING || typeof IntersectionObserver!=='function') return;
  CV_OBSERVER ??= new IntersectionObserver(entries=>{
    for(const entry of entries){
      const id=entry.target.dataset.cvmore;
      if(entry.isIntersecting && !CV_PAGES.get(id)?.error) cvLoadMore(id);
    }
  },{rootMargin:'0px 0px 180px 0px'});
  $('cvgroups').querySelectorAll('.cv-g.open > .cv-list [data-cvmore]').forEach(button=>{
    if(button.getClientRects().length && !button.disabled) CV_OBSERVER.observe(button);
  });
}

function renderConvos(){
  if(!CONVOS) return;
  if(!CONVOS.available){
    $('cvhead').innerHTML=`<span class="warn">${esc(CONVOS.reason)}</span>`;
    $('cvgroups').innerHTML='';$('cv-match').textContent='';return;
  }
  const S=CONVOS.summary, query=CV_QUERY.trim().toLowerCase();
  if(query!==CV_OPEN_QUERY){CV_OPEN={};CV_OPEN_QUERY=query;}
  $('cvhead').innerHTML=`<span class="cv-total"><b>${S.matched ?? S.files}</b> 个会话，分布在 <b>${S.groups}</b> 个项目</span>
    <label>排序 <select id="cvsort">${[['new','最近更新'],['count','会话最多'],['old','最早更新'],['size','占用空间']].map(([v,t])=>`<option value="${v}"${CV_SORT===v?' selected':''}>${t}</option>`).join('')}</select></label>
    <label title="根据已读取的内容识别，长会话可能只统计了一部分"><input type="checkbox" id="cvonly"${CV_HUMAN_ONLY?' checked':''}> 至少两次用户提问</label>
    ${S.unreadable?`<span class="bad" role="alert">${S.unreadable} 个文件或目录读取失败，列表可能不完整。请检查访问权限后刷新。</span>`:''}`;
  const groups=[...CONVOS.groups];
  const compare={new:(a,b)=>b.newest-a.newest,old:(a,b)=>a.newest-b.newest,
    size:(a,b)=>b.bytes-a.bytes,count:(a,b)=>b.count-a.count};
  const isEph=g=>/[\\/]Temp[\\/](astab|astra|ccstab|ccagentic)_[a-z0-9]+$/i.test(g.cwd)&&!g.humanish&&g.count<=2;
  const eph=groups.filter(isEph), rest=groups.filter(g=>!isEph(g)).sort(compare[CV_SORT]);
  const groupHtml=g=>{
    const id=cvKey(g), open=CV_OPEN[id] ?? !!query, cwd=String(g.cwd || id);
    const cut=cwd.search(/[\\/][^\\/]*$/), head=cut>0?cwd.slice(0,cut+1):'', tail=cut>0?cwd.slice(cut+1):cwd;
    return `<div class="cv-g${open?' open':''}" data-cv="${esc(id)}" data-cvproject="${esc(id)}">
      <button class="cv-gh" aria-expanded="${open}" title="${esc(g.storagePath || cwd)}">
        <span class="caret" aria-hidden="true">${open?'▼':'▶'}</span><span class="dir"><span class="dn">${esc(tail)}</span><span class="dp">${esc(head)}</span></span>
        <span class="n">${g.count} 个会话</span><span class="ag">${g.newest?cvAge((Date.now()/1000-g.newest)/3600):'时间未记录'}</span>
        <span class="cv-expand">${open?'收起':'展开'}</span></button>
      <div class="cv-list">${open?cvLocation(g)+cvRows(g)+cvPage(g):''}</div></div>`;
  };
  const ephOpen=CV_OPEN[CV_EPH_KEY] ?? !!query;
  const ephHtml=eph.length?`<div class="cv-g${ephOpen?' open':''}" data-cv="${CV_EPH_KEY}">
    <button class="cv-gh" aria-expanded="${ephOpen}"><span class="caret" aria-hidden="true">${ephOpen?'▼':'▶'}</span><span class="dir">临时运行记录（${eph.length} 个项目）</span><span class="n">${eph.reduce((n,g)=>n+g.count,0)} 个会话</span><span class="ag"></span><span class="cv-expand">${ephOpen?'收起':'展开'}</span></button>
    <div class="cv-list">${ephOpen?eph.map(groupHtml).join(''):''}</div></div>`:'';
  const top=window.scrollY;
  $('cvgroups').innerHTML=rest.map(groupHtml).join('')+ephHtml || '<p class="review-empty">没有匹配的会话</p>';
  window.scrollTo({top,behavior:'instant'});cvCount();cvObserve();
}
function cvLocation(g){
  return `${g.locationWarning?`<p class="cv-notice error" role="alert">项目位置需要核对：${esc(g.locationWarning)}</p>`:''}
    <details class="cv-project-details" data-cvdetail="project:${esc(cvKey(g))}"${CV_DETAILS.has('project:'+cvKey(g))?' open':''}><summary>项目位置</summary><div class="cv-detail-body">
      <span class="cv-location">工作目录：${esc(g.cwd)}${g.locationInferred?'（根据历史记录推断）':''}</span>
      <span class="cv-location">会话存放位置：${esc(g.storagePath || g.cwd)}</span><span>文件合计：${kb(g.bytes)}</span>
      <button class="icon-only mini" data-cvcopy="${esc(g.storagePath || g.cwd)}" title="复制存放路径"><svg class="ic" aria-hidden="true"><use href="#i-copy"/></svg><span class="control-label">复制存放路径</span></button></div></details>`;
}
async function cvCopyPath(path){
  try{await navigator.clipboard.writeText(path);toast('路径已复制');}
  catch(error){toast('无法复制，请手动选择：'+path,'bad');}
}
function cvClick(event){
  const copy=event.target.closest('[data-cvcopy]');
  if(copy){event.stopPropagation();cvCopyPath(copy.dataset.cvcopy);return;}
  const open=event.target.closest('[data-cvopen]');
  if(open){openConvoChain(open.dataset.cvopen);return;}
  if(event.target.closest('details')) return;
  const more=event.target.closest('[data-cvmore]');
  if(more){cvLoadMore(more.dataset.cvmore);return;}
  const header=event.target.closest('.cv-gh');
  if(header){
    const id=header.parentElement.dataset.cv;
    CV_OPEN[id]=header.getAttribute('aria-expanded')!=='true';renderConvos();
    const next=[...$('cvgroups').querySelectorAll('.cv-gh')].find(el=>el.parentElement.dataset.cv===id);
    next?.focus({preventScroll:true});return;
  }
}
function startConvos(){
  $('cvbox').addEventListener('click',cvClick);
  $('cvbox').addEventListener('toggle',event=>{
    const key=event.target.dataset.cvdetail;
    if(key) event.target.open?CV_DETAILS.add(key):CV_DETAILS.delete(key);
  },true);
  $('cv-search').addEventListener('input',cvSearchChanged);
  $('cvhead').addEventListener('change',event=>{
    if(event.target.id==='cvsort'){CV_SORT=event.target.value;renderConvos();}
    if(event.target.id==='cvonly'){CV_HUMAN_ONLY=event.target.checked;loadConvos();}
  });
}
