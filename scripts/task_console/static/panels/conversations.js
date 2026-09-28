// Session metadata is paged by the server; only expanded projects render rows.
let CONVOS=null, CV_HUMAN_ONLY=false, CV_OPEN={}, CV_QUERY="", CV_OPEN_QUERY="", CV_SORT="new";
let CV_VERSION=0, CV_LOADING=false, CV_REQUEST=null, CV_TIMER=null, CV_OBSERVER=null;
const CV_PAGES=new Map(), CV_EPH_KEY="::eph::";
const CV_SESSION_ID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CV_TAG={rename:"自命名","ai-title":"自动标题",summary:"摘要","first-message":"首条消息",slug:"代号",id:"会话号"};
const CV_SRC={rename:"你指定的会话标题","ai-title":"自动生成的窗口标题",summary:"压缩时写的概括",
  "first-message":"第一条消息",slug:"自动代号",id:"只有会话号"};
const cvKey=g=>g.id || g.cwd;
const cvAge=h=>h==null?"时间未记录":h<24?Math.max(0,h).toFixed(0)+" 小时":(h/24).toFixed(0)+" 天";

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
  $('cv-match').textContent=`已加载 ${loaded} / ${matched} 场${CV_QUERY.trim()?'匹配会话':''} · 展开目录后向下滚动继续加载`;
}
function cvRows(g){
  return g.shown.map(r=>{
    const valid=CV_SESSION_ID.test(String(r.id||''));
    const chain=valid && typeof openConvoChain==='function';
    const manage=valid && typeof cvOpenManager==='function';
    const disabled=typeof ConsoleActions!=='undefined' && ConsoleActions.readOnly;
    return `<div class="cv-r${r.humanSeen>=2?' human':''}" data-cvfile="${esc(r.file)}"${chain?` data-cvid="${esc(r.id)}"`:''}>
      <button class="t" data-cvopen="${chain?esc(r.id):''}" title="${esc(r.title)}">${esc(r.title)}</button>
      <span class="src ${esc(r.titleFrom)}" title="${esc(CV_SRC[r.titleFrom]||r.titleFrom)}">${CV_TAG[r.titleFrom]||'?'}</span>
      <span class="m">${r.humanSeen?'👤'+r.humanSeen+(r.partial?'+':''):''}</span>
      <span class="m">${cvAge(r.ageHours)} · ${kb(r.bytes)}${chain?' · <span class="cv-open">对话链 ›</span>':''}</span>
      <span class="cv-row-actions">${manage?`<button class="mini" data-cvrename="${esc(r.id)}"${disabled?' disabled':''}>重命名</button>
        <button class="mini" data-cvmove="${esc(r.id)}"${disabled?' disabled':''}>移动</button>
        <button class="mini cv-handle" data-cvdrag="${esc(r.id)}" draggable="${!disabled}"${disabled?' disabled':''} aria-label="拖动会话到其他目录" title="拖到项目标题上即可迁移文件；也可点击移动">⠿</button>`:''}
        ${ibtn('i-copy','复制会话文件路径',`data-cvcopy="${esc(r.file)}"`)}</span>
      ${r.preview&&r.preview!==r.title?`<span class="pv" title="${esc(r.preview)}">${esc(r.preview)}</span>`:''}
    </div>`;
  }).join('');
}
function cvPage(g){
  const id=cvKey(g), state=CV_PAGES.get(id), more=g.hasMore ?? g.truncated;
  return `<div class="cv-page" data-cvpage="${esc(id)}"><span${state?.error?' class="bad" role="alert"':''}>${state?.error?esc(state.error):`已加载 ${g.shown.length} / ${g.count} 场`}</span>
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
  $('cvhead').innerHTML=`<span>共 <b>${S.files}</b> 场</span><span><b>${S.humanish}</b> 场用户多轮对话</span><span>${S.groups} 个目录 · ${kb(S.bytes)}</span>
    <label>目录排序 <select id="cvsort">${[['new','最近活动'],['size','体积'],['count','场次'],['old','最旧在前']].map(([v,t])=>`<option value="${v}"${CV_SORT===v?' selected':''}>${t}</option>`).join('')}</select></label>
    <label><input type="checkbox" id="cvonly"${CV_HUMAN_ONLY?' checked':''}> 只看用户多轮对话</label>
    ${S.unreadable?`<span class="bad">读取失败 ${S.unreadable} 场</span>`:''}`;
  const groups=[...CONVOS.groups], values=groups.map(g=>g.bytes||0).filter(Boolean);
  const lo=Math.log10(1+Math.min(...values,Infinity)), hi=Math.log10(1+Math.max(...values,1));
  const bar=b=>!b?0:hi<=lo?100:Math.max(3,Math.round(3+97*(Math.log10(1+b)-lo)/(hi-lo)));
  const compare={new:(a,b)=>b.newest-a.newest,old:(a,b)=>a.newest-b.newest,
    size:(a,b)=>b.bytes-a.bytes,count:(a,b)=>b.count-a.count};
  const isEph=g=>/[\\/]Temp[\\/](astab|astra|ccstab|ccagentic)_[a-z0-9]+$/i.test(g.cwd)&&!g.humanish&&g.count<=2;
  const eph=groups.filter(isEph), rest=groups.filter(g=>!isEph(g)).sort(compare[CV_SORT]);
  const groupHtml=g=>{
    const id=cvKey(g), open=CV_OPEN[id] ?? !!query, cwd=String(g.cwd || id);
    const cut=cwd.search(/[\\/][^\\/]*$/), head=cut>0?cwd.slice(0,cut+1):'', tail=cut>0?cwd.slice(cut+1):cwd;
    return `<div class="cv-g${open?' open':''}" data-cv="${esc(id)}" data-cvproject="${esc(id)}">
      <button class="cv-gh" aria-expanded="${open}" title="${esc(g.storagePath || cwd)}">
        <span class="caret">${open?'▼':'▶'}</span><span class="dir"><span class="dp">${esc(head)}</span><span class="dn">${esc(tail)}</span></span>
        <span class="cv-bar${g.bytes?'':' zero'}"><i style="width:${bar(g.bytes)}%"></i></span>
        <span class="cv-hm">${g.humanish?'👤'+g.humanish:''}</span><span class="ag">${g.newest?cvAge((Date.now()/1000-g.newest)/3600):'空目录'}</span>
        <span class="n">${g.count} 场 · ${kb(g.bytes)}</span></button>
      <div class="cv-list">${open?cvLocation(g)+cvRows(g)+cvPage(g):''}</div></div>`;
  };
  const ephOpen=CV_OPEN[CV_EPH_KEY] ?? !!query;
  const ephHtml=eph.length?`<div class="cv-g${ephOpen?' open':''}" data-cv="${CV_EPH_KEY}">
    <button class="cv-gh" aria-expanded="${ephOpen}"><span class="caret">${ephOpen?'▼':'▶'}</span><span class="dir">临时会话目录 ${eph.length} 个</span><span></span><span></span><span></span><span class="n">${eph.reduce((n,g)=>n+g.count,0)} 场</span></button>
    <div class="cv-list">${ephOpen?eph.map(groupHtml).join(''):''}</div></div>`:'';
  const top=window.scrollY;
  $('cvgroups').innerHTML=rest.map(groupHtml).join('')+ephHtml || '<p class="review-empty">没有匹配的会话</p>';
  window.scrollTo({top,behavior:'instant'});cvCount();cvObserve();
}
function cvLocation(g){
  return `<div class="cv-location">${esc(g.storagePath || g.cwd)}${g.locationInferred?' · 工作目录仅由历史记录推断':''}${g.locationWarning?' · '+esc(g.locationWarning):''}</div>`;
}
async function cvCopyPath(path){
  try{await navigator.clipboard.writeText(path);toast('路径已复制');}
  catch(error){toast('无法复制，请手动选择：'+path,'bad');}
}
function cvClick(event){
  const copy=event.target.closest('[data-cvcopy]');
  if(copy){event.stopPropagation();cvCopyPath(copy.dataset.cvcopy);return;}
  const more=event.target.closest('[data-cvmore]');
  if(more){cvLoadMore(more.dataset.cvmore);return;}
  const header=event.target.closest('.cv-gh');
  if(header){
    const id=header.parentElement.dataset.cv;
    CV_OPEN[id]=header.getAttribute('aria-expanded')!=='true';renderConvos();
    const next=[...$('cvgroups').querySelectorAll('.cv-gh')].find(el=>el.parentElement.dataset.cv===id);
    next?.focus({preventScroll:true});return;
  }
  if(event.target.closest('.cv-row-actions')) return;
  const row=event.target.closest('.cv-r[data-cvfile]');
  if(row){
    if(row.dataset.cvid && typeof openConvoChain==='function') openConvoChain(row.dataset.cvid);
    else if(typeof cvCopyPath==='function') cvCopyPath(row.dataset.cvfile);
    else toast(row.dataset.cvfile);
  }
}
function startConvos(){
  $('cvbox').addEventListener('click',cvClick);
  $('cv-search').addEventListener('input',cvSearchChanged);
  $('cvhead').addEventListener('change',event=>{
    if(event.target.id==='cvsort'){CV_SORT=event.target.value;renderConvos();}
    if(event.target.id==='cvonly'){CV_HUMAN_ONLY=event.target.checked;loadConvos();}
  });
}
