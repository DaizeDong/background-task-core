// Classic script module; loaded in app.js dependency order.
let SCK=null;
async function loadSelfcheck(){
  try{ SCK=await api("/api/selfcheck"); }
  catch(e){ $("scksum").innerHTML=`<span class="bad">自检本身失败:${esc(e.message)}</span>`; return; }
  renderSelfcheck();
  updateBadges();
}
function renderSelfcheck(){
  if(!SCK) return;
  $("sckdots").innerHTML=SCK.rows.map(r=>
    `<i class="d ${r.state}" title="${esc(r.title)}: ${esc(r.state)}${r.why?" ("+esc(r.why)+")":""}"></i>`
  ).join("");
  const c=SCK.counts||{};
  const parts=[`读到 ${SCK.probed}/${SCK.total}`];
  if(c.unset) parts.push(`未配 ${c.unset}`);
  if(c.stale) parts.push(`陈旧 ${c.stale}`);
  $("scksum").innerHTML = SCK.ok
    ? `<span>${parts.join(" · ")}</span>`
    : `<span class="bad">${SCK.broken.length?("读不到 "+SCK.broken.join(", ")):"必需来源不可用"}</span>`
      +` <span>· ${parts.join(" · ")}</span>`;
  $("sckd").innerHTML=SCK.rows.map(r=>`<div class="r">
    <i class="d ${r.state}"></i>
    <span>${esc(r.title)}</span>
    <span class="p" title="${esc(r.path||r.why||"")}">${esc(r.path||r.why||"")}</span>
    <span class="w">${r.state==="ok"||r.state==="stale"
      ? (r.ageHours!=null? r.ageHours.toFixed(1)+"h":"")+(r.entries!=null?" · "+r.entries+" 项":"")
      : esc(r.state)}</span></div>`).join("");
}

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

function renderCodex(){
  const el=$("mt-codex"); if(!el) return;
  if(!CODEX){ el.innerHTML=`<div class="mt-t">第二套 agent</div>
    <div class="mt-note">读取中</div>`; return; }
  if(CODEX.error){ el.innerHTML=`<div class="mt-t">第二套 agent</div>
    <div class="warn-line">读取失败:${esc(CODEX.error)}</div>`; return; }
  if(!CODEX.available){ el.innerHTML=`<div class="mt-t">第二套 agent</div>
    <div class="mt-note">${esc(CODEX.reason)}</div>`; return; }

  const rows=Object.keys(CX_LAB).map(k=>{
    const it=CODEX.items[k]||{};
    if(!it.available)
      return `<div class="cx-r unread"><span class="n" title="${esc(k)}">${CX_LAB[k]}</span>
        <span class="c" title="${esc(it.reason||"")}">读不了</span><span class="m"></span></div>`;
    if(!it.exists)
      return `<div class="cx-r absent"><span class="n" title="${esc(k)}">${CX_LAB[k]}</span>
        <span class="c" title="这一项不在。不在是一个结论,不是一次失败">不在</span>
        <span class="m"></span></div>`;
    // 条数只有会话那两项有。没有条数的项这一格留空,不填 0 ——
    // 一个 0 会被读成「有这个东西但里面是空的」。
    const n = (it.count==null) ? (it.files!=null?it.files+" 个":"") : it.count+" 场";
    const warn = it.errors ? ` · ${it.errors} 处扫不动,这个数偏小` : "";
    return `<div class="cx-r${it.errors?" warnish":""}">
      <span class="n" title="${esc(k)}">${CX_LAB[k]}</span>
      <span class="c" title="${esc(kb(it.bytes)+warn)}">${kb(it.bytes)}${it.errors?"+":""}</span>
      <span class="m">${esc(n)}</span></div>`;
  }).join("");

  const bad=[];
  if(CODEX.incomplete.length) bad.push(`${CODEX.incomplete.length} 项没扫全,总量偏小`);
  if(CODEX.unread.length) bad.push(`${CODEX.unread.length} 项读不了`);
  el.innerHTML=`<div class="mt-t">第二套 agent <b>${kb(CODEX.bytes)}</b>
      <span class="sub">${CODEX.incomplete.length||CODEX.unread.length?"":"全部数到"}</span></div>
    ${bad.length?`<div class="warn-line">${esc(bad.join(" · "))}</div>`:""}
    <div class="mt-rows">${rows}</div>`;
}

// 逐份转录的清单。默认不加载:这一扫要走几千个文件,而这一屏别的东西不该等它。
let CXL=null, CXSEL=new Set();

async function loadCxList(){
  const which=$("cxwhich").value;
  $("cxnote").textContent="扫描中";
  try{ CXL=await api("/api/codex/list?which="+encodeURIComponent(which)); }
  catch(e){ CXL={error:e.message}; }
  CXSEL=new Set();
  renderCxList();
}

function renderCxList(){
  const el=$("cxbody"); if(!el) return;
  if(!CXL){ el.innerHTML=`<div class="mt-note">还没加载。</div>`; return; }
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
    + (CXL.truncated ? ` · 只列出最大的 ${items.length} 份` : "")
    + (CXL.errors ? ` · ${CXL.errors} 处扫不动,这个数偏小` : "");

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
      <button class="mini" id="cxall">全选这一屏</button>
      <button class="mini" id="cxnone">清空选择</button>
      <span class="sel">选中 <b>${CXSEL.size}</b> 份 · ${kb(selBytes)}</span>
      <button class="mini danger" id="cxdel"${CXSEL.size?"":" disabled"}
        title="${CXSEL.size?"删掉选中的这些。不可逆":"先选几份"}">删掉选中的</button>
    </div><div class="cx-list">${rows}</div>`;
}

// 只更新那一行统计,不碰列表本身。
function cxSelSummary(){
  const el=document.querySelector(".cx-bar .sel"); if(!el) return;
  const byRel={}; ((CXL&&CXL.items)||[]).forEach(i=>byRel[i.rel]=i);
  let b=0; CXSEL.forEach(r=>{ b += (byRel[r]||{}).bytes||0; });
  el.innerHTML=`选中 <b>${CXSEL.size}</b> 份 · ${kb(b)}`;
  const d=document.getElementById("cxdel");
  if(d){ d.disabled=!CXSEL.size;
         d.title=CXSEL.size?"删掉选中的这些。不可逆":"先选几份"; }
}

async function cxDelete(){
  const rels=[...CXSEL];
  if(!rels.length) return;
  const byRel={}; (CXL.items||[]).forEach(i=>byRel[i.rel]=i);
  const bytes=rels.reduce((a,r)=>a+((byRel[r]||{}).bytes||0),0);
  // 删除不可逆,所以确认里要写清「多少份、多少字节」,并把前几条路径列出来 ——
  // 一个只说「确定删除?」的弹窗,等于让人在不知道删什么的情况下下决定。
  if(!confirm(`删掉 ${rels.length} 份转录,约 ${kb(bytes)}。\n\n`
      +rels.slice(0,8).map(r=>"  "+r).join("\n")
      +(rels.length>8?`\n  …还有 ${rels.length-8} 份`:"")
      +`\n\n不可逆。继续?`)) return;
  try{
    const r=await api("/api/codex/delete",{method:"POST",body:JSON.stringify({rels})});
    if(r.error){ toast(r.error,"bad"); return; }
    if(!r.ok){
      // 中途失败要说清停在哪里、已经删了几份 —— 不假装什么都没发生。
      alert(`停在 ${r.stoppedAt}\n\n${r.error}\n\n已经删掉 ${r.deleted} 份,释放 ${kb(r.freed)}。`);
    } else {
      toast(`删掉 ${r.deleted} 份,释放 ${kb(r.freed)}`);
    }
    await loadCxList();
    loadCodex(); loadSys();
  }catch(e){ toast(e.message,"bad"); }
}
