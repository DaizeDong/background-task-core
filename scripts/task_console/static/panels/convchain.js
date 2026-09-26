// Classic script module; optional panel (app.js OPTIONAL_PANELS -> #chbox).
// 对话链:会话屏的下钻。一场会话在文件里是一棵树(回退重写会分叉、压缩会把前文换成概括),
// 而 claude --resume 只沿其中一条链走。这一块把那条链按「轮」切开给人看,并允许从任意节点
// 导出或分叉。判定(哪条是链、哪里算分叉、压缩边界之前接哪一段)全在后端 convtree.py,
// 这里只画结论。
// ⚠ 本文件载入时不许碰 DOM:它是可选面板,载入失败只能坏掉 #chbox 自己,
// 而且 test_panel_parity 会在一个只有 querySelector 的假 document 里执行它。
// 所有挂点都在 startConvoChain() 里,由 events.js 用 typeof 守卫调用。
let CH=null, CH_ID=null, CH_SUB=null, CH_LEAF=null, CH_PARENT=null;
// 选中的是一个**元素**而不是一个 uuid:一轮的标题行和它的第一步是同一个节点,
// 按 uuid 记的话 j/k 会在这两行之间原地打转。键的形状是 h:轮 / s:轮:步 / m:轮。
let CH_SEL=null, CH_FROM=null, CH_TO=null, CH_OPEN={}, CH_FKOPEN=null, CH_FRES=null;
let CH_XT=false, CH_XK=false, CH_XBUSY=false, CH_FBUSY=false;
let CH_POS={}, CH_ORDER=[], CH_TR=[], CH_FKS={};
const CH_NODE=new Map();
let CH_SEQ=0, CH_NSEQ=0, CH_NT=null, CH_ROUTING=false;
const CH_UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CH_AGENT=/^[A-Za-z0-9_-]{1,80}$/;
// 字形、类名、可读名。类名逐字写在这里(不拼接),死 CSS 闸才找得到它们。
const CH_KIND={human:["👤","ch-hu","用户"], text:["✎","ch-tx","回复"], thinking:["💭","ch-th","思考"],
  tool:["🔧","ch-tl","工具调用"], result:["↩","ch-rs","工具结果"], usermeta:["·","ch-um","注入的用户行"],
  summary:["≡","ch-sm","压缩概括"], compact:["⟂","ch-cp","压缩边界"], system:["⚙","ch-sy","系统行"],
  attachment:["📎","ch-at","附件"]};
const chK=k=>CH_KIND[k] || ["?","ch-um",k||"未知类型"];
const chU8=u=>u ? String(u).slice(0,8) : "-";
// 缺失不是零:后端没给的数一律写「未记录」,不写 0。
const chN=v=>v==null ? "未记录" : v;
const chTok=n=>n==null ? "?" : n>=1000 ? Math.round(n/1000)+"k" : String(n);
const chIsSession=id=>CH_UUID.test(String(id||""));
// 时间。没有时间戳就写「无时间」,不写一个 00:00:缺失和午夜是两件事。
function chTs(ts, short){
  if(!ts) return "无时间";
  const d=new Date(ts);
  if(isNaN(d)) return String(ts).slice(0,16);
  const p=n=>String(n).padStart(2,"0");
  return short ? `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
               : `${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
// 详情里的完整时间按本机时区,带上偏移:原文的 UTC 在原始 JSON 里照样看得到。
function chTsFull(ts){
  const d=new Date(ts);
  if(isNaN(d)) return String(ts);
  const p=n=>String(n).padStart(2,"0"), o=-d.getTimezoneOffset();
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
    +` ${o>=0?"+":"-"}${p(Math.floor(Math.abs(o)/60))}${p(Math.abs(o)%60)}`;
}
const chOpen=()=>!!CH_ID && !!$("chbox") && !$("chbox").hidden;

// 地址里带上叶子:切到另一条分支是一次导航,后退应该回到上一条分支,而不是关掉对话链。
// 叶子段写成 leaf=<uuid>:子代理 id 的字符集里没有「=」,两种段不会混。
function chHashFor(id, sub, leaf){
  return "convos/"+id+(sub ? "/"+sub : "")+(leaf ? "/leaf="+leaf : "");
}
function chParseArg(arg){
  const parts=String(arg||"").split("/");
  let sub=null, leaf=null;
  parts.slice(1).forEach(p=>{ if(p.indexOf("leaf=")===0) leaf=p.slice(5); else if(p) sub=p; });
  return {id:parts[0], sub, leaf};
}
// navigation.showView 在会话屏上问这里:地址该写成什么。
// arg 是 #convos/ 后面那一段。只有地址本身已经是裸 #convos(后退回列表)才关掉链:
// showView("convos", false) 还有别的来路(Shift+A 收回摊开),那时地址仍带着会话 id,链不能跟着没了。
function convoChainRoute(arg, push){
  // 由 openConvoChain 自己切到会话屏时,链的状态正在建立,这一次不许当成「后退回列表」。
  if(CH_ROUTING) return CH_ID ? chHashFor(CH_ID, CH_SUB, CH_LEAF) : "convos";
  if(arg){ const c=chParseArg(arg); openConvoChain(c.id,{sub:c.sub,leaf:c.leaf}); return "convos/"+arg; }
  if(!push && CH_ID && location.hash.slice(1)==="convos"){ chClose(true); return "convos"; }
  return chOpen() ? chHashFor(CH_ID, CH_SUB, CH_LEAF) : "convos";
}
// 页面导出(operations.pageSnapshot)只带地址和计数:整条链可达十几兆,不塞进 JSON 快照。
function convoChainSnapshot(){
  if(!CH_ID) return null;
  return {id:CH_ID, sub:CH_SUB, leaf:CH_LEAF, available:CH?CH.available:null,
          pathLen:CH&&CH.pathLen, turns:CH&&CH.turns?CH.turns.length:null, from:CH_FROM, to:CH_TO};
}

// 打开(或切换分支、进出子代理)。同一个 id/sub/leaf 已经在屏上就只露出来,不重读:
// hashchange 会把刚 pushState 过的同一个地址再送进来一次。
async function openConvoChain(id, opts){
  opts=opts || {};
  let sub=opts.sub || null, leaf=opts.leaf || null;
  const focusU=opts.focus || null;
  if(!chIsSession(id)){ toast("会话 id 不是 UUID 形状:"+id, "bad"); return; }
  if(sub && !CH_AGENT.test(sub)){ toast("子代理 id 形状不对:"+sub, "bad"); return; }
  if(leaf && !CH_UUID.test(leaf)){ toast("分支的叶子不是 UUID 形状:"+leaf, "bad"); leaf=null; }
  if(CURVIEW!=="convos"){
    CH_ROUTING=true;
    try{ showView("convos", false); }finally{ CH_ROUTING=false; }
  }
  const same=CH_ID===id && CH_SUB===sub;
  if(same && CH_LEAF===leaf && CH && !focusU){ $("chbox").hidden=false; return; }
  // 换分支时按 uuid 记下选中和展开的是哪些节点,重新加载后找回来。按下标记的话,
  // 同一个下标在另一条分支上是另一个节点,而范围、导出和分叉会一声不吭地跟着它走。
  let keepSel=null, keepHead=false, keepOpen=[];
  if(same && CH && CH.turns){
    keepSel=chSelU(); keepHead=!!CH_SEL && CH_SEL[0]==="h";
    keepOpen=Object.keys(CH_OPEN).filter(ti=>CH_OPEN[ti]).map(ti=>(CH.turns[+ti]||{}).u).filter(Boolean);
  }
  if(!same){
    CH_SEL=null; CH_FROM=null; CH_TO=null; CH_OPEN={}; CH_FRES=null;
    if(!sub || CH_ID!==id) CH_PARENT=null;
  }
  CH_FKOPEN=null; CH_ID=id; CH_SUB=sub; CH_LEAF=leaf;
  const want=chHashFor(id, sub, leaf);
  if(location.hash.slice(1)!==want){ try{ history.pushState(null, "", "#"+want); }catch(e){} }
  $("chbox").hidden=false;
  const seq=++CH_SEQ;
  $("chnote").textContent="读取中";
  // 滚动推到下一拍:从深链进来时 showView 在这之后还会把页面滚回顶部。
  if(!same){ CH=null; chRender(); setTimeout(()=>{ try{ $("chbox").scrollIntoView({block:"start"}); }catch(e){} }, 0); }
  const q="/api/convo/chain?id="+encodeURIComponent(id)
        +(sub ? "&sub="+encodeURIComponent(sub) : "")
        +(leaf ? "&leaf="+encodeURIComponent(leaf) : "");
  let j;
  try{ j=await api(q); }
  catch(e){
    if(seq!==CH_SEQ) return;
    CH=null; $("chnote").textContent="读取失败:"+e.message; chRender(); return;
  }
  if(seq!==CH_SEQ) return;
  CH=j; $("chnote").textContent="";
  try{ chAfterLoad(focusU, keepSel, keepHead, keepOpen); }
  catch(e){ $("chnote").textContent="渲染失败:"+e.message; }
}
function chAfterLoad(focusU, keepSel, keepHead, keepOpen){
  chIndex();
  // 切了分支之后,原来的起止点可能已经不在这条链上。留着它们等于让导出静默失败。
  if(CH_FROM && CH_POS[CH_FROM]==null) CH_FROM=null;
  if(CH_TO && CH_POS[CH_TO]==null) CH_TO=null;
  const T=(CH && CH.turns) || [];
  CH_OPEN={};
  keepOpen.forEach(u=>{ const ti=T.findIndex(t=>t.u===u); if(ti>=0) CH_OPEN[ti]=true; });
  const target=focusU || keepSel;
  CH_SEL=null;
  if(target){
    let k=null;
    if(!focusU && keepHead){
      const ti=T.findIndex(t=>t.type==="turn" && t.u===target);
      if(ti>=0) k="h:"+ti;
    }
    if(!k) k=chKeyOf(target);
    if(k){ const ti=+k.split(":")[1]; if(k[0]==="s") CH_OPEN[ti]=true; CH_SEL=k; }
    else toast("原来选中的节点 "+chU8(target)+" 不在这条分支上,已取消选中");
  }
  chRender();
  if(CH_SEL) chSelect(CH_SEL, true);
}

function chClose(keepHash){
  CH_SEQ++; clearTimeout(CH_NT);
  CH=null; CH_ID=null; CH_SUB=null; CH_LEAF=null; CH_PARENT=null; CH_SEL=null;
  CH_FROM=null; CH_TO=null; CH_OPEN={}; CH_FKOPEN=null; CH_FRES=null; CH_NODE.clear();
  $("chbox").hidden=true;
  if(!keepHash && location.hash.slice(1).indexOf("convos/")===0){
    try{ history.replaceState(null, "", "#convos"); }catch(e){}
  }
}

// 链上每个节点的位置。范围、起止、分叉菜单都按它算,所以只算一遍。
function chIndex(){
  CH_ORDER=[]; CH_POS={}; CH_TR=[]; CH_FKS={};
  ((CH && CH.turns) || []).forEach((t, ti)=>{
    const a=CH_ORDER.length;
    if(t.type==="marker"){ CH_POS[t.u]=CH_ORDER.length; CH_ORDER.push(t.u); }
    else (t.steps || []).forEach(s=>{ CH_POS[s.u]=CH_ORDER.length; CH_ORDER.push(s.u); });
    CH_TR[ti]=[a, CH_ORDER.length-1];
    (t.forks || []).forEach(f=>{ CH_FKS[f.u]=f; });
  });
}
function chKeyOf(u){
  const T=(CH && CH.turns) || [];
  for(let ti=0; ti<T.length; ti++){
    const t=T[ti];
    if(t.type==="marker"){ if(t.u===u) return "m:"+ti; continue; }
    const si=(t.steps || []).findIndex(s=>s.u===u);
    if(si>=0) return "s:"+ti+":"+si;
  }
  return null;
}
function chSelU(){
  if(!CH || !CH_SEL) return null;
  const p=CH_SEL.split(":"), t=(CH.turns || [])[+p[1]];
  if(!t) return null;
  if(p[0]==="h" || p[0]==="m") return t.u;
  const s=(t.steps || [])[+p[2]];
  return s ? s.u : null;
}
// 选中的是一轮的标题行时,「到这里为止」指这一轮的最后一步(连同回复),不是那句提问:
// 标题行代表整轮。只取提问的话,导出和分叉都停在一个没人回答的问题上。
function chSelEnd(){
  if(CH && CH_SEL && CH_SEL[0]==="h"){
    const tr=CH_TR[+CH_SEL.split(":")[1]];
    if(tr && tr[1]>=tr[0]) return CH_ORDER[tr[1]];
  }
  return chSelU();
}
// [起点位置, 终点位置]。终点没设就跟着选中,选中也没有就是链尾。
function chRange(){
  const a=CH_FROM!=null && CH_POS[CH_FROM]!=null ? CH_POS[CH_FROM] : 0;
  const tu=CH_TO || chSelEnd();
  const b=tu!=null && CH_POS[tu]!=null ? CH_POS[tu] : CH_ORDER.length-1;
  return [a, b];
}

function chRender(){ chRenderHead(); chRenderList(); chRenderAct(); chRenderDet(); }

function chRenderHead(){
  const cr=$("chcrumb"), hd=$("chhead"), wn=$("chwarn");
  if(CH_SUB){
    cr.innerHTML=`<button class="mini" data-chact="back">‹ 返回主会话</button>`
      +`<span>${CH_PARENT && CH_PARENT.title ? esc(CH_PARENT.title)+" › " : ""}子代理 <code>${esc(CH_SUB)}</code> · 只读</span>`;
  } else if(CH && CH.available && (CH.subagents || []).length){
    const S=CH.subagents;
    cr.innerHTML=`<label>子代理 <select id="chsubs" aria-label="打开一个子代理的转录">
      <option value="">共 ${S.length} 个,选一个打开</option>${S.map(s=>`<option value="${esc(s.agentId)}">${
        esc((s.agentType ? s.agentType+" · " : "")+(s.description || s.agentId))}${s.gz ? " (gz)" : ""}</option>`).join("")}
      </select></label>`;
  } else cr.innerHTML="";
  if(!CH){ hd.innerHTML=""; wn.innerHTML=""; return; }
  if(!CH.available){
    hd.innerHTML=`<span style="color:var(--warn)">${esc(CH.reason || "这份转录读不了")}</span>`;
    wn.innerHTML=""; return;
  }
  const nT=(CH.turns || []).filter(t=>t.type==="turn").length;
  hd.innerHTML=`<span class="ttl">${esc(CH.title || CH.id)}</span>`
    +`<span>${chN(CH.lines)} 行 · ${chN(CH.chainEntries)} 个链条目 · ${kb(CH.bytes)}</span>`
    +`<span>显示链 <b>${chN(CH.pathLen)}</b> 个节点 · <b>${nT}</b> 轮</span>`
    +`<span${CH.badLines ? ' class="bad"' : ""}>坏行 ${chN(CH.badLines)}</span>`
    +`<span>悬空父节点 ${chN(CH.danglingParents)}</span>`
    +(CH.duplicateUuids ? `<span title="同一个 uuid 被整行重写过,只保留第一份">重复 uuid ${CH.duplicateUuids}</span>` : "")
    +`<span>压缩 ${chN(CH.compactions)} · 分叉 ${chN(CH.forks)}</span>`
    +`<span title="${CH.cached ? "这次走的是缓存的索引,数字是当初建索引的耗时" : "这次重新建了索引"}">索引 ${chN(CH.indexMs)} ms${CH.cached ? "(缓存)" : ""}</span>`
    +(CH.leafIsDefault ? "" : `<span class="alt">正在看一条非默认分支</span><button class="mini" data-chact="latest">回到最新分支</button>`)
    +`<span class="cwd" title="${esc(CH.file || "")}">${CH.cwd ? esc(CH.cwd) : "转录里没有记录目录"}</span>`;
  wn.innerHTML=(CH.warnings || []).map(w=>`<div class="warn-line">${esc(w)}</div>`).join("");
}

function chFkHtml(u){
  const f=CH_FKS[u];
  if(!f || CH_FKOPEN!==u) return "";
  return `<div class="ch-fk" role="menu"><div class="hd">⑂ 这里分出 ${f.alternatives.length} 条分支。选一条,就沿它一直看到它最后写下的那个节点。</div>`
    +f.alternatives.map(a=>`<button role="menuitem" data-chleaf="${esc(a.leaf)}" data-chat="${esc(f.u)}"${a.active ? " disabled" : ""}>`
      +`<span class="${a.active ? "act" : "alt"}">${a.active ? "当前" : "切换"}</span>`
      +`<span class="pv" title="${esc(a.preview || "")}">${esc(a.preview || "(这条分支里没有用户消息)")}</span>`
      +`<span class="sz">${chN(a.size)} 个节点${a.leafLineIndex!=null ? " · 止于第 "+(a.leafLineIndex+1)+" 行" : ""}</span></button>`).join("")
    +`</div>`;
}
function chFkBtn(u, n){
  return `<button class="mini" data-chfk="${esc(u)}" aria-expanded="${CH_FKOPEN===u}" title="这个节点下面分出了 ${n} 条分支">⑂ ${n} 个分支</button>`;
}

function chTurnHtml(t, ti){
  const open=!!CH_OPEN[ti], hu=t.human, c=t.counts || {};
  const badges=[["tool","🔧"],["text","✎"],["thinking","💭"],["attachment","📎"]]
    .filter(x=>c[x[0]]).map(x=>`<span class="ch-bd" title="${esc(chK(x[0])[2])}">${x[1]}${c[x[0]]}</span>`).join("");
  const nf=(t.forks || []).length;
  let s=`<div class="ch-t${open ? " open" : ""}" role="option" aria-expanded="${open}" data-chk="h:${ti}" data-chu="${esc(t.u)}" id="chk-h-${ti}">`
    +`<span class="caret">${open ? "▼" : "▶"}</span><span class="no">#${esc(t.k)}</span>`
    +(hu ? `<span class="pv" title="${esc(hu.preview || "")}">${esc(hu.preview || "(空消息)")}</span>`
         : `<span class="pv none">(不是从一条用户消息开始的)</span>`)
    +`<span class="ch-cnt">${badges}<span title="这一轮的节点数">${(t.steps || []).length} 步</span>`
    +(nf ? `<span class="fk" title="这一轮里有 ${nf} 个分叉点">⑂${nf}</span>` : "")+`</span>`
    +`<span class="ts">${chTs(hu ? hu.ts : t.ts)}</span></div>`;
  // 步骤只在展开时才拼:收起的轮在 DOM 里只有一行。
  if(open) s+=`<div class="ch-steps">`+(t.steps || []).map((x, si)=>chStepHtml(x, ti, si)).join("")+`</div>`;
  return s;
}
function chStepHtml(x, ti, si){
  const k=chK(x.kind);
  let extra="";
  if(x.fork) extra+=chFkBtn(x.u, x.fork);
  if(x.agentId) extra+=x.agentFile
    ? `<button class="mini" data-chsub="${esc(x.agentId)}" title="打开这个子代理的转录(只读)">子代理 ${esc(chU8(x.agentId))} ›</button>`
    : `<span class="ch-miss" title="转录里提到了子代理 ${esc(x.agentId)},但它的文件不在">子代理转录不在</span>`;
  return `<div class="ch-s ${k[1]}" role="option" data-chk="s:${ti}:${si}" data-chu="${esc(x.u)}" id="chk-s-${ti}-${si}">`
    +`<span class="g" title="${esc(k[2])}">${k[0]}</span><span class="nm">${x.name ? esc(x.name) : ""}</span>`
    +(x.preview ? `<span class="pv" title="${esc(x.preview)}">${esc(x.preview)}</span>`
                : `<span class="pv faint">(${esc(k[2])},没有可预览的文字)</span>`)
    +`<span class="ts">${chTs(x.ts, 1)}</span>`
    +(extra ? `<span class="x">${extra}</span>` : "")+`</div>`+chFkHtml(x.u);
}
function chMarkerHtml(t, ti){
  const fk=(t.forks || []).length ? " "+chFkBtn(t.u, t.forks[0].alternatives.length) : "";
  if(t.kind==="compact"){
    const open=!!CH_OPEN[ti];
    const trig=t.trigger==="auto" ? "自动压缩" : t.trigger==="manual" ? "手动压缩" : "压缩";
    const tok=(t.preTokens!=null || t.postTokens!=null)
      ? ` · ${chTok(t.preTokens)}→${chTok(t.postTokens)} tokens` : " · tokens 未记录";
    return `<div class="ch-cmp${open ? " open" : ""}" role="option" aria-expanded="${open}" data-chk="m:${ti}" data-chu="${esc(t.u)}" id="chk-m-${ti}"`
      +` title="压缩边界。点一下${open ? "收起" : "展开"}概括">`
      +`<span class="ln"></span><span class="lb">⟂ ${trig}${tok}</span><span class="ts">${chTs(t.ts)}</span>`
      +`<span class="ln"></span>${fk}</div>`+chFkHtml(t.u);
  }
  return `<div class="ch-sum" role="option" data-chk="m:${ti}" data-chu="${esc(t.u)}" id="chk-m-${ti}"`
    +` title="${esc(t.preview || "")}">≡ 压缩概括:${esc(t.preview || "(空)")}${fk}</div>`+chFkHtml(t.u);
}

function chRenderList(){
  const L=$("chlist");
  if(!CH || !CH.available){ L.innerHTML=""; return; }
  const st=L.scrollTop, T=CH.turns || [];
  const parts=[];
  for(let ti=0; ti<T.length; ti++){
    const t=T[ti];
    if(t.type==="marker"){
      // 压缩概括挂在它前面那条边界下面,边界收起时一起收起。
      const underCmp=t.kind==="summary" && ti>0 && T[ti-1].type==="marker" && T[ti-1].kind==="compact";
      if(underCmp && !CH_OPEN[ti-1]) continue;
      parts.push(chMarkerHtml(t, ti));
    } else parts.push(chTurnHtml(t, ti));
  }
  L.innerHTML=parts.join("") || `<div class="ch-empty">这条链上没有节点</div>`;
  L.scrollTop=st;
  chMarks();
}

// 选中、范围、起止这三样只改类名,不重画。展开一轮几千步之后,每按一次 j 重画一遍会卡。
function chMarks(){
  if(!CH || !CH.available) return;
  const r=chRange(), a=r[0], b=r[1], tint=!!(CH_FROM || CH_TO);
  const au=CH_ORDER[a], bu=CH_ORDER[b];
  $("chlist").querySelectorAll("[data-chk]").forEach(el=>{
    const k=el.dataset.chk, u=el.dataset.chu;
    let lo, hi;
    if(k[0]==="h"){ const tr=CH_TR[+k.split(":")[1]] || [-1,-1]; lo=tr[0]; hi=tr[1]; }
    else { lo=hi=CH_POS[u]; }
    const on=k===CH_SEL;
    el.classList.toggle("ch-on", on);
    el.setAttribute("aria-selected", on ? "true" : "false");
    el.classList.toggle("ch-rng", tint && hi>=a && lo<=b);
    el.classList.toggle("ch-a", tint && u===au);
    el.classList.toggle("ch-b", tint && u===bu);
  });
}

function chSelect(k, scroll){
  // 上一次分叉的结果属于上一个节点。选了别的节点还挂着它,看起来像是在说新选的这个。
  if(k!==CH_SEL) CH_FRES=null;
  CH_SEL=k; chMarks();
  const el=$("chlist").querySelector(`[data-chk="${k}"]`);
  if(el){
    if(scroll) el.scrollIntoView({block:"nearest"});
    $("chlist").setAttribute("aria-activedescendant", el.id);
  }
  chRenderAct();
  // 按住 j 连走时不为每一行都发请求:停下来 90ms 再取。
  clearTimeout(CH_NT);
  const u=chSelU();
  if(!u){ chRenderDet(); return; }
  const ck=(CH_SUB || "")+"|"+u;
  if(CH_NODE.has(ck)){ chRenderDet(CH_NODE.get(ck)); return; }
  $("chdet").innerHTML=`<div class="ch-hint">读取中</div>`;
  CH_NT=setTimeout(()=>{ chLoadNode(u, ck).catch(e=>{ $("chdet").innerHTML=`<div class="mt-note">${esc(e.message)}</div>`; }); }, 90);
}
async function chLoadNode(u, ck){
  const seq=++CH_NSEQ, id=CH_ID, sub=CH_SUB;
  let n;
  try{
    n=await api("/api/convo/node?id="+encodeURIComponent(id)+"&u="+encodeURIComponent(u)
                +(sub ? "&sub="+encodeURIComponent(sub) : ""));
  }catch(e){ n={error:e.message}; }
  if(seq!==CH_NSEQ || id!==CH_ID || sub!==CH_SUB) return;
  // 读失败的不进缓存:下一次选中它应该重试,而不是永远记着一次失败。
  if(!n.error){
    CH_NODE.set(ck, n);
    if(CH_NODE.size>300) CH_NODE.delete(CH_NODE.keys().next().value);
  }
  if(chSelU()===u) chRenderDet(n);
}

function chRenderDet(n){
  const D=$("chdet");
  if(!CH || !CH.available){ D.innerHTML=""; return; }
  if(!n){
    D.innerHTML=`<div class="ch-hint">点一个节点看全文。键盘(焦点在链上时):j / k 上下,Enter 展开或收起一轮,`
      +`[ 设起点,] 设终点,Esc 关闭。</div>`;
    return;
  }
  if(n.error){ D.innerHTML=`<div class="mt-note">读不到这个节点:${esc(n.error)}</div>`; return; }
  if(n.available===false){ D.innerHTML=`<div class="mt-note">${esc(n.reason || "不可用")}</div>`; return; }
  const k=chK(n.kind);
  const kv=[["uuid", n.u],
    ["位置", n.lineIndex!=null ? `第 ${n.lineIndex+1} 行 · 字节 ${n.byteOffset} 起 ${kb(n.byteLength)}` : null],
    ["时间", n.ts ? chTsFull(n.ts) : "未记录"], ["类型", (n.type || "未记录")+(n.subtype ? " / "+n.subtype : "")],
    ["模型", n.model], ["父节点", n.parentUuid || "无(根)"], ["逻辑父节点", n.logicalParentUuid],
    ["message.id", n.messageId], ["子代理", n.agentId], ["目录", n.cwd]]
    .filter(x=>x[1]!=null && x[1]!=="");
  const flags=[[n.isMeta,"isMeta"],[n.isSidechain,"isSidechain"],[n.isCompactSummary,"压缩概括"],
    [n.promptSource,"来源 "+n.promptSource]].filter(x=>x[0]).map(x=>`<span>${esc(x[1])}</span>`).join("");
  const pre=(t, cls)=>`<pre class="ch-pre${cls ? " "+cls : ""}">${esc(t)}</pre>`;
  let h=`<h5>${k[0]} ${esc(k[2])}${n.role ? " · "+esc(n.role) : ""}</h5>`
    +`<dl class="ch-kv">${kv.map(x=>`<dt>${esc(x[0])}</dt><dd>${esc(x[1])}</dd>`).join("")}</dl>`
    +(flags ? `<div class="ch-flags">${flags}</div>` : "");
  if(n.truncated){
    const tf=n.truncatedFields || [], cut=tf.filter(x=>x!=="omitted");
    if(cut.length) h+=`<div class="warn-line">这些字段超过 20 万字符,只显示了开头:${esc(cut.join("、"))}</div>`;
    if(tf.indexOf("omitted")>=0) h+=`<div class="warn-line">这一行的工具输入和结果加起来超过单次下发的上限,后面的没有显示</div>`;
  }
  let body=0;
  if(n.text){ body++; h+=`<h5>正文</h5>`+pre(n.text); }
  (n.tools || []).forEach(t=>{ body++;
    h+=`<h5>🔧 ${esc(t.name || "?")} <code class="faint">${esc(t.id || "")}</code></h5>`+pre(t.input || ""); });
  (n.results || []).forEach(r=>{ body++;
    h+=`<h5>↩ 结果 <code class="faint">${esc(r.tool_use_id || "")}</code>${r.isError ? ' <span style="color:var(--bad)">出错</span>' : ""}</h5>`
      +pre(r.text || "(空)", r.isError ? "bad" : ""); });
  if(n.thinking){ body++; h+=`<details><summary>💭 思考(${n.thinking.length} 字)</summary>${pre(n.thinking)}</details>`; }
  if(n.systemContent){ body++; h+=`<h5>系统内容</h5>`+pre(n.systemContent); }
  if(n.attachment){ body++; h+=`<h5>📎 附件 ${esc(n.attachment.type || "")}</h5>`+pre(n.attachment.summary || ""); }
  if(n.compactMetadata){ h+=`<h5>压缩元数据</h5>`+pre(JSON.stringify(n.compactMetadata, null, 2)); }
  if(!body) h+=`<div class="ch-hint">这一行没有可读的正文。原始 JSON 在下面。</div>`;
  h+=n.raw!=null
    ? `<details><summary>原始 JSON 行${n.rawTruncated ? "(已截断)" : ""}</summary>${pre(n.raw)}</details>`
    : `<div class="faint">这一行 ${kb(n.byteLength)},太大,不下发原文。</div>`;
  D.innerHTML=h;
}

function chRenderAct(){
  const A=$("chact");
  if(!CH || !CH.available || !CH_ORDER.length){ A.innerHTML=""; return; }
  const r=chRange(), a=r[0], b=r[1], su=chSelU(), bad=a>b, f=CH_FRES;
  A.innerHTML=`<div class="ch-row"><span class="faint">范围</span>`
    +`<code>${CH_FROM ? esc(chU8(CH_FROM)) : "开头"}</code> → <code>${esc(chU8(CH_ORDER[b]))}</code>`
    +`<span class="faint">${CH_TO ? "" : CH_SEL && CH_SEL[0]==="h" ? "(终点是选中这一轮的最后一步)" : CH_SEL ? "(终点跟着选中)" : "(没选节点时到链尾)"}${bad ? "" : " · "+(b-a+1)+" 个节点"}</span>`
    +(bad ? `<span style="color:var(--bad)">起点在终点之后</span>` : "")+`</div>`
    +`<div class="ch-row"><button class="mini" data-chact="from"${su ? "" : " disabled"}>设为起点 [</button>`
    +`<button class="mini" data-chact="to"${su ? "" : " disabled"}>设为终点 ]</button>`
    +`<button class="mini" data-chact="clr"${CH_FROM || CH_TO ? "" : " disabled"}>清除范围</button></div>`
    +`<div class="ch-row"><label><input type="checkbox" id="chtools"${CH_XT ? " checked" : ""}> 含工具调用</label>`
    +`<label><input type="checkbox" id="chthink"${CH_XK ? " checked" : ""}> 含思考</label>`
    +`<button class="mini" data-ctexport="md"${bad || CH_XBUSY ? " disabled" : ""}><svg class="ic" aria-hidden="true"><use href="#i-fetch"/></svg>导出 Markdown</button></div>`
    +`<div class="ch-row"><button class="mini" data-ctfork="at"${CH_SUB || !su || CH_FBUSY ? " disabled" : ""}`
    +` title="在选中的节点处新建一个可以 --resume 的会话,原文件不动">⑂ 从这里分叉成新会话</button>`
    +(CH_SUB ? `<span class="faint">子代理的转录不能分叉</span>` : su ? "" : `<span class="faint">先选一个节点</span>`)+`</div>`
    +(!f ? "" : f.error
      ? `<div class="ch-fres bad">分叉失败:${esc(f.error)}</div>`
      : `<div class="ch-fres"><div>新会话 <code>${esc(f.newId)}</code></div>`
        +`<div class="faint">${chN(f.emitted)} 条记录 · 共 ${chN(f.lines)} 行 · 约 ${chTok(f.approxTokens)} tokens · `
        +`${f.fromBoundary ? "从压缩边界 "+esc(chU8(f.fromBoundary))+" 起" : "完整历史,没有压缩边界"}</div>`
        +(f.warnings || []).map(w=>`<div class="warn-line">${esc(w)}</div>`).join("")
        +`<pre class="ch-pre">${esc(f.command || "")}</pre>`
        +`<div class="ch-row"><button class="mini" data-chcopy="${esc(f.command || "")}">复制命令</button>`
        +`<span class="faint">在终端里粘贴运行,就从这个节点接着聊</span></div></div>`);
}

function chToggle(){
  if(!CH_SEL) return;
  const p=CH_SEL.split(":"), ti=+p[1];
  if(p[0]==="s"){ CH_OPEN[ti]=false; CH_SEL="h:"+ti; }
  else CH_OPEN[ti]=!CH_OPEN[ti];
  chRenderList(); chSelect(CH_SEL, true);
}
function chMove(d){
  const els=[...$("chlist").querySelectorAll("[data-chk]")];
  if(!els.length) return;
  let i=els.findIndex(x=>x.dataset.chk===CH_SEL);
  if(d==="home") i=0;
  else if(d==="end") i=els.length-1;
  else i=i<0 ? 0 : Math.max(0, Math.min(els.length-1, i+d));
  chSelect(els[i].dataset.chk, true);
}
function chSetEnd(which){
  const u=which==="from" ? chSelU() : chSelEnd();
  if(!u) return;
  if(which==="from") CH_FROM=u; else CH_TO=u;
  chMarks(); chRenderAct();
}

// 导出。后端把 Markdown 装在 JSON 里回来,页面自己存成文件:
// 令牌只走请求头,一个裸 <a href> 带不上它,会拿到 403。
async function chExport(){
  const r=chRange(), to=CH_ORDER[r[1]];
  // 整条链导出在服务端要一两秒;这期间再点一次会得到两份下载。
  if(!to || r[0]>r[1] || CH_XBUSY) return;
  CH_XBUSY=true; chRenderAct();
  const body={id:CH_ID, to, tools:CH_XT, thinking:CH_XK};
  if(CH_FROM) body.from=CH_FROM;
  if(CH_LEAF) body.leaf=CH_LEAF;
  if(CH_SUB) body.sub=CH_SUB;
  $("chnote").textContent="导出中";
  try{
    const j=await api("/api/convo/export", {method:"POST", body:JSON.stringify(body)});
    if(typeof j.text!=="string") throw new Error(j.error || "响应里没有正文");
    const url=URL.createObjectURL(new Blob([j.text], {type:"text/markdown;charset=utf-8"}));
    const el=document.createElement("a");
    el.href=url; el.download=j.filename || "conversation.md";
    document.body.appendChild(el); el.click(); el.remove();
    setTimeout(()=>URL.revokeObjectURL(url), 5000);
    $("chnote").textContent="";
    toast(`已导出 ${j.filename}:${chN(j.turns)} 轮用户消息 · ${chN(j.nodes)} 个节点`, "ok");
  }catch(e){
    $("chnote").textContent="导出失败:"+e.message;
    toast("导出失败:"+e.message, "bad");
  }finally{ CH_XBUSY=false; chRenderAct(); }
}

// 分叉。唯一会写文件的动作:在源转录所在的项目目录里独占新建一份,源文件只读。
async function chFork(){
  const at=chSelEnd();
  if(!at || CH_SUB || !CH || CH_FBUSY) return;
  if(!confirm(`从节点 ${chU8(at)} 分叉成一个新会话?\n\n`
    +`会在原会话所在的项目目录里新建一份转录文件,带一个新的会话 id,`
    +`内容是 Claude 走到这个节点时实际拥有的上下文(隔着压缩时从最近的压缩边界开始)。\n\n`
    +`原会话文件一个字节都不会改。之后用 claude --resume <新 id> 接着聊。`)) return;
  const body={id:CH_ID, at};
  if(CH_LEAF) body.leaf=CH_LEAF;
  CH_FBUSY=true; chRenderAct();
  $("chnote").textContent="分叉中";
  try{
    const j=await api("/api/convo/fork", {method:"POST", body:JSON.stringify(body)});
    if(!j.newId) throw new Error(j.error || "响应里没有新会话 id");
    CH_FRES=j; $("chnote").textContent="";
    toast("已分叉出新会话 "+chU8(j.newId), "ok");
    // 新文件已经落在会话根下了;不重扫的话列表里看不到它,像是没分叉成。
    if(typeof loadConvos==="function") loadConvos().catch(()=>{});
  }catch(e){
    CH_FRES={error:e.message}; $("chnote").textContent="分叉失败:"+e.message;
    toast("分叉失败:"+e.message, "bad");
  }finally{ CH_FBUSY=false; chRenderAct(); }
}

async function chCopy(text){
  try{ await navigator.clipboard.writeText(text); toast("已复制"); }
  catch(e){ toast("无法访问剪贴板,请手动复制:"+text, "bad"); }
}

function chAct(a){
  if(a==="from" || a==="to"){ chSetEnd(a); return; }
  if(a==="clr"){ CH_FROM=null; CH_TO=null; chMarks(); chRenderAct(); return; }
  if(a==="latest"){ openConvoChain(CH_ID, {sub:CH_SUB}); return; }
  if(a==="back"){
    const p=CH_PARENT;
    CH_PARENT=null;
    openConvoChain(CH_ID, {leaf:p ? p.leaf : null, focus:p ? p.u : null});
  }
}
function chOpenSub(agentId){
  CH_PARENT={title:CH && CH.title, leaf:CH_LEAF, u:chSelU()};
  openConvoChain(CH_ID, {sub:agentId});
}

// 链上的按键。返回 true 表示吃掉了。
function chKey(e){
  const k=e.key;
  if(k==="Escape"){
    if(CH_FKOPEN){ CH_FKOPEN=null; chRenderList(); }
    else chClose();
    return true;
  }
  if(!CH || !CH.available) return false;
  if(k==="j" || k==="ArrowDown"){ chMove(1); return true; }
  if(k==="k" || k==="ArrowUp"){ chMove(-1); return true; }
  if(k==="Home"){ chMove("home"); return true; }
  if(k==="End"){ chMove("end"); return true; }
  if(k==="["){ chSetEnd("from"); return true; }
  if(k==="]"){ chSetEnd("to"); return true; }
  if(k==="Enter" || k===" "){ chToggle(); return true; }
  return false;
}

function chClick(e){
  const t=e.target;
  if(t.closest("#chclose")){ chClose(); return; }
  const ex=t.closest("[data-ctexport]");
  if(ex){ if(!ex.disabled) chExport().catch(err=>toast("导出失败:"+err.message, "bad")); return; }
  const fo=t.closest("[data-ctfork]");
  if(fo){ if(!fo.disabled) chFork().catch(err=>toast("分叉失败:"+err.message, "bad")); return; }
  const b=t.closest("[data-chact]");
  if(b){ if(!b.disabled) chAct(b.dataset.chact); return; }
  const cp=t.closest("[data-chcopy]");
  if(cp){ chCopy(cp.dataset.chcopy); return; }
  const lf=t.closest("[data-chleaf]");
  if(lf){ if(!lf.disabled){ CH_FKOPEN=null; openConvoChain(CH_ID, {sub:CH_SUB, leaf:lf.dataset.chleaf, focus:lf.dataset.chat}); } return; }
  const fk=t.closest("[data-chfk]");
  if(fk){ CH_FKOPEN=CH_FKOPEN===fk.dataset.chfk ? null : fk.dataset.chfk; chRenderList(); return; }
  const sb=t.closest("[data-chsub]");
  if(sb){ chOpenSub(sb.dataset.chsub); return; }
  const n=t.closest("[data-chk]");
  if(n){
    const k=n.dataset.chk, ti=+k.split(":")[1];
    // 标题行和压缩边界:没展开就展开,已经选中再点一次就收起。
    if(k[0]==="h" || n.classList.contains("ch-cmp")){
      if(!CH_OPEN[ti]) CH_OPEN[ti]=true;
      else if(CH_SEL===k) CH_OPEN[ti]=false;
      CH_SEL=k; chRenderList();
    }
    chSelect(k, false);
    try{ $("chlist").focus({preventScroll:true}); }catch(err){}
  }
}

// 挂点全挂在卡片自己身上:卡片里的点击和按键先到这里,不和两个 document 级监听抢。
// 按键 stopPropagation:全局那层把 j/k/Enter 当成任务表的键,Esc 会清空任务选择。
function startConvoChain(){
  const box=$("chbox");
  if(!box) return;
  box.addEventListener("click", e=>{
    try{ chClick(e); }catch(err){ $("chnote").textContent="操作失败:"+err.message; }
  });
  box.addEventListener("change", e=>{
    if(e.target.id==="chtools"){ CH_XT=e.target.checked; return; }
    if(e.target.id==="chthink"){ CH_XK=e.target.checked; return; }
    if(e.target.id==="chsubs" && e.target.value) chOpenSub(e.target.value);
  });
  box.addEventListener("keydown", e=>{
    if(e.ctrlKey || e.metaKey || e.altKey) return;
    const t=e.target;
    if(t.closest && t.closest("input,select,textarea")){
      if(e.key==="Escape"){ t.blur(); e.stopPropagation(); }
      return;
    }
    // 焦点在卡片里的按钮上时,Enter / 空格归按钮自己。
    if((e.key==="Enter" || e.key===" ") && t!==$("chlist")) return;
    if(chKey(e)){ e.preventDefault(); e.stopPropagation(); return; }
    // 任务表的那几个键在这里不该有任何效果,更不该把人弹去任务屏。
    if("jkgGxaresd".indexOf(e.key)>=0) e.stopPropagation();
  });
}
