// Classic script module; loaded in app.js dependency order.
// 仓库面板。整片仓一秒多扫完(并发 + 每仓两次 git 调用),所以可以随手重扫。
let CODEX=null;
async function loadCodex(){
  try{ CODEX=await api("/api/codex"); }catch(e){ CODEX={error:e.message}; }
  renderCodex();
}

// 每一项自己说自己的话。一次 OSError 不能让整栏塌成「没数据」——
// 「会话目录读不了」和「会话目录是空的」要采取的行动相反。
const CX_LAB={"AGENTS.md":"指令文件","config.toml":"配置",
  "sessions":"会话","archived_sessions":"归档会话",
  "history.jsonl":"命令历史","log":"日志","cache":"缓存"};

// 「Codex 存储」只在卡片标题上写一次,总量跟在标题后面(#codex-total)。以前卡片里又印了一遍标题和总量,
// 外加一句和数据一样粗的「扫描完成」:同一个名字读两遍,真正的数反倒不显眼。
// 总量只在读到了才写;读取中、读不到和没扫完各有各的说法,标题后面留空,不写一个 0。
function renderCodex(){
  const el=$("mt-codex"); if(!el) return;
  const total=$("codex-total");
  if(total){ total.textContent=""; total.title=""; }
  if(!CODEX){ el.innerHTML=`<div class="mt-note">读取中</div>`; return; }
  if(CODEX.error){ el.innerHTML=`<div class="warn-line">读取失败:${esc(CODEX.error)}</div>`; return; }
  if(!CODEX.available){ el.innerHTML=`<div class="mt-note">${esc(CODEX.reason)}</div>`; return; }

  const rows=Object.keys(CX_LAB).map(k=>{
    const it=CODEX.items[k]||{};
    if(!it.available)
      return `<div class="cx-r unread"><span class="n" title="${esc(k)}">${CX_LAB[k]}</span>
        <span class="c" title="${esc(it.reason||"")}">读取失败</span><span class="m"></span></div>`;
    if(!it.exists)
      return `<div class="cx-r absent"><span class="n" title="${esc(k)}">${CX_LAB[k]}</span>
        <span class="c" title="未找到对应文件或目录">不存在</span>
        <span class="m"></span></div>`;
    // 条数只有会话那两项有。没有条数的项这一格留空,不填 0 ——
    // 一个 0 会被读成「有这个东西但里面是空的」。
    const n = (it.count==null) ? (it.files!=null?it.files+" 个":"") : it.count+" 场";
    const warn = it.errors ? ` · ${it.errors} 处读取失败，已显示的总量偏小` : "";
    return `<div class="cx-r${it.errors?" warnish":""}">
      <span class="n" title="${esc(k)}">${CX_LAB[k]}</span>
      <span class="c" title="${esc(kb(it.bytes)+warn)}">${kb(it.bytes)}${it.errors?"+":""}</span>
      <span class="m">${esc(n)}</span></div>`;
  }).join("");

  const bad=[];
  if(CODEX.incomplete.length) bad.push(`${CODEX.incomplete.length} 项未扫完，总量偏小`);
  if(CODEX.unread.length) bad.push(`${CODEX.unread.length} 项读不了`);
  const partial=CODEX.incomplete.length||CODEX.unread.length;
  if(total){ total.textContent=kb(CODEX.bytes)+(partial?"+":""); total.title=partial?"有项目没扫完或读不了，实际总量更大":"全部项目已扫完"; }
  el.innerHTML=`${bad.length?`<div class="warn-line">${esc(bad.join(" · "))}</div>`:""}
    <div class="mt-rows">${rows}</div>`;
}

// 逐份转录的清单。默认不加载:这一扫要走几千个文件,而这一屏别的东西不该等它。
let CXL=null, CXSEL=new Set();
let CX_REQUEST=0;
let CX_DELETING=false;
const CX_PENDING=new Map();

async function loadCxList(){
  const which=$("cxwhich").value;
  const request=++CX_REQUEST;
  $("cxnote").textContent="扫描中";
  CXL=null;CXSEL.clear();renderCxList();
  if(!CX_PENDING.has(which)){
    const pending=api("/api/codex/list?which="+encodeURIComponent(which))
      .catch(error=>({error:error.message})).finally(()=>CX_PENDING.delete(which));
    CX_PENDING.set(which,pending);
  }
  const result=await CX_PENDING.get(which);
  if(request!==CX_REQUEST) return;
  CXL=result;
  CXSEL=new Set();
  renderCxList();
}

function renderCxList(){
  const el=$("cxbody"); if(!el) return;
  if(!CXL){ el.innerHTML=`<div class="mt-note">正在扫描会话存储</div>`; return; }
  if(CXL.error){ $("cxnote").textContent="失败";
    el.innerHTML=`<div class="warn-line">${esc(CXL.error)}</div>`; return; }
  if(!CXL.available){ $("cxnote").textContent="未检查";
    el.innerHTML=`<div class="mt-note">${esc(CXL.reason||"")}</div>`; return; }
  if(!CXL.exists){ $("cxnote").textContent="";
    el.innerHTML=`<div class="mt-note">这个目录还不存在。</div>`; return; }

  const items=CXL.items.slice();
  const mode=$("cxsort").value;
  if(mode==="old") items.sort((a,b)=>a.mtime-b.mtime);
  else if(mode==="new") items.sort((a,b)=>b.mtime-a.mtime);
  else items.sort((a,b)=>b.bytes-a.bytes);

  // 截断必须说出来。一个悄悄只给前 N 条的清单,会让人以为剩下的不存在,
  // 然后按一个不完整的总量去做清理决定。
  $("cxnote").textContent = `${CXL.count} 份 · ${kb(CXL.bytes)}`
    + (CXL.truncated ? ` · 仅列出最大的 ${items.length} 份，排序只影响已列出的文件` : "")
    + (CXL.errors ? ` · ${CXL.errors} 处读取失败，已显示的总量偏小` : "");

  // 体积条取对数并把 [最小, 最大] 整段铺开。线性标度下最大的一份 583M 把别的全压成
  // 一两个像素 —— 一列几乎人人等长(或者人人等于零)的条,和没有这一列一样没用。
  // 会话那一屏刚因为同一个原因改过一次,这里用同一套算法。
  const vals=items.map(i=>i.bytes).filter(b=>b>0);
  const maxB=Math.max(1,...vals), minB=vals.length?Math.min(...vals):1;
  const lo=Math.log10(1+minB), span=Math.log10(1+maxB)-lo;
  const barW=b=>b<=0?0:(span<=0?100:Math.max(3,Math.round(3+97*(Math.log10(1+b)-lo)/span)));
  const now=Date.now()/1000;
  const rows=items.map(i=>{
    const on=CXSEL.has(i.rel);
    return `<div class="cx-l${on?" on":""}" data-cxrel="${esc(i.rel)}">
      <input type="checkbox" ${on?"checked":""} tabindex="-1" aria-label="选中 ${esc(i.name)}">
      <span class="p" title="${esc(i.rel)}">${esc(i.rel)}</span>
      <span class="bar"><i style="width:${barW(i.bytes)}%"></i></span>
      <span class="sz">${kb(i.bytes)}</span>
      <span class="ag" title="${esc(new Date(i.mtime*1000).toLocaleString())}">${
        cvAge((now-i.mtime)/3600)}</span></div>`;
  }).join("");

  const selBytes=items.filter(i=>CXSEL.has(i.rel)).reduce((a,i)=>a+i.bytes,0);
  el.innerHTML=`<div class="cx-bar">
      <button class="icon-only mini" id="cxall" title="全选已列出的文件"><svg class="ic" aria-hidden="true"><use href="#i-select-all"/></svg><span class="control-label">全选已列出的文件</span></button>
      <button class="icon-only mini" id="cxnone" title="清空选择"><svg class="ic" aria-hidden="true"><use href="#i-filter-clear"/></svg><span class="control-label">清空选择</span></button>
      <span class="sel">选中 <b>${CXSEL.size}</b> 份 · ${kb(selBytes)}</span>
      <button class="icon-only mini danger" id="cxdel" title="永久删除选中的文件，需要确认"><svg class="ic" aria-hidden="true"><use href="#i-trash"/></svg><span class="control-label">删除选中文件</span></button>
    </div><div class="cx-list">${rows}</div>`;
  cxSelButtons();
}

// 三个按钮各自什么时候能点:没选中就没得清空、没得删,已经全选了就没得再全选。
function cxSelButtons(){
  const items=(CXL&&CXL.items)||[];
  const all=items.length>0 && items.every(i=>CXSEL.has(i.rel));
  ConsoleActions.gate(document.getElementById("cxall"),!items.length?"列表里没有文件":all?"已列出的文件都已选中":"");
  ConsoleActions.gate(document.getElementById("cxnone"),CXSEL.size?"":"没有选中的文件");
  ConsoleActions.gate(document.getElementById("cxdel"),CXSEL.size?"":"请先选择文件");
}

// 只更新那一行统计,不碰列表本身。
function cxSelSummary(){
  const el=document.querySelector(".cx-bar .sel"); if(!el) return;
  const byRel={}; ((CXL&&CXL.items)||[]).forEach(i=>byRel[i.rel]=i);
  let b=0; CXSEL.forEach(r=>{ b += (byRel[r]||{}).bytes||0; });
  el.innerHTML=`选中 <b>${CXSEL.size}</b> 份 · ${kb(b)}`;
  cxSelButtons();
}

// 删除结果写在清理卡片里的一行状态上,不弹浏览器自带的提示框:那种框关掉就没了,
// 而「停在哪一份、已经删了几份」正是删完之后还要对着列表核对的东西。
function cxState(text,tone){
  const el=$("cxstate"); if(!el) return;
  el.textContent=text||"";el.hidden=!text;el.className="cx-state"+(tone?" "+tone:"");
}
async function cxDelete(){
  if(CX_DELETING || !ConsoleActions.allowWrite()) return;
  const rels=[...CXSEL];
  if(!rels.length) return;
  const byRel={}; (CXL.items||[]).forEach(i=>byRel[i.rel]=i);
  const bytes=rels.reduce((a,r)=>a+((byRel[r]||{}).bytes||0),0);
  // 删除不可逆,所以确认里要写清「多少份、多少字节」,并把前几条路径列出来 ——
  // 一个只说「确定删除?」的弹窗,等于让人在不知道删什么的情况下下决定。
  const ok=await askConfirm({title:`永久删除 ${rels.length} 份会话文件`,
    body:`约 ${kb(bytes)}。删除后无法恢复。`,items:rels.slice(0,8),
    more:rels.length>8?`…还有 ${rels.length-8} 份`:"",confirmLabel:"永久删除",danger:true});
  if(!ok || CX_DELETING) return;
  CX_DELETING=true;cxState("");
  try{
    const r=await api("/api/codex/delete",{method:"POST",body:JSON.stringify({rels})});
    if(r.error && !Number.isInteger(r.deleted)){ cxState('未能确认删除结果：'+r.error,"bad");toast('未能确认删除结果：'+r.error,"bad");await loadCxList();return; }
    if(!r.ok){
      // 中途失败要说清停在哪里、已经删了几份 —— 不假装什么都没发生。
      cxState(`删除未全部完成，在 ${r.stoppedAt} 处停止：${r.error}。已删除 ${r.deleted} 个文件，合计 ${kb(r.freed)}。`,"bad");
      toast("删除未全部完成，详情见清理卡片","bad");
    } else {
      toast(`已删除 ${r.deleted} 个文件，合计 ${kb(r.freed)}`,'ok');
    }
    await loadCxList();
    loadCodex(); loadSys();
  }catch(e){ cxState(e.message,"bad");toast(e.message,"bad");await loadCxList(); }
  finally{CX_DELETING=false;}
}

// ── 存储清理这一屏的挂点 ──(从 events.js 搬来,原因见 tasks.js 的 startTasksPage 上方)
function startStorage(){
  // 每张卡片自己的刷新按钮都删了:顶栏的刷新会把这一屏的全部读取再跑一遍,同一屏摆两个一样的图标只会让人猜哪个更全。
  // 只留会话库的「重新扫描」:它只扫所选的那个库,比整页刷新便宜得多。扫描期间灰着,免得被连点。
  $("cxload").addEventListener("click",async()=>{
    const button=$("cxload");setDisabled(button,"正在扫描所选的会话库");
    try{ await loadCxList(); }finally{ setDisabled(button,""); }
  });
  // 换库要重扫,换排序不用:排序是纯前端的事,重扫一遍几千个文件只为了换个顺序,
  // 会让这个下拉用起来像卡住了。
  $("cxwhich").addEventListener("change",loadCxList);
  $("cxsort").addEventListener("change",()=>{ if(CXL) renderCxList(); });
  // 清单里的全选、清空、删除和逐行勾选。清单整块重画,所以挂在不重画的 #cxbody 上。
  $("cxbody").addEventListener("click",e=>{
    const target=e.target.closest('button') || e.target;
    if(target.id==="cxall"){
      (CXL&&CXL.items||[]).forEach(i=>CXSEL.add(i.rel)); renderCxList(); return; }
    if(target.id==="cxnone"){ CXSEL.clear(); renderCxList(); return; }
    if(target.id==="cxdel"){ cxDelete(); return; }
    const cx = e.target.closest("[data-cxrel]");
    if(cx){ const k=cx.dataset.cxrel;
      // ⚠ 只改这一行,不重画整张表。2382 行重画一次要几十毫秒,而且会把滚动位置
      // 弹回顶部 —— 在一张两千行的清单上挑东西时,那等于每勾一个就把人送回开头。
      // (实测还有一个更隐蔽的后果:重画会把已有的行节点全部换掉,
      // 于是任何「先取一批节点再逐个点」的用法只有第一次生效。)
      if(CXSEL.has(k)) CXSEL.delete(k); else CXSEL.add(k);
      cx.classList.toggle("on", CXSEL.has(k));
      const box=cx.querySelector("input"); if(box) box.checked=CXSEL.has(k);
      cxSelSummary(); return; }
  });
}
