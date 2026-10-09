// Classic script module; optional panel (app.js OPTIONAL_PANELS -> #chbox).
// 对话链:会话屏的下钻。一场会话在文件里是一棵树(回退重写会分叉、压缩会把前文换成概括),
// 而 claude --resume 只沿其中一条链走。这一块把那条链按「轮」切开给人看,并允许从任意节点
// 导出或分叉。判定(哪条是链、哪里算分叉、压缩边界之前接哪一段)全在 convo-chain 库里,
// 这里只画结论。
// ⚠ 本文件载入时不许碰 DOM:它是可选面板,载入失败只能坏掉 #chbox 自己,
// 而且 test_panel_parity 会在一个只有 querySelector 的假 document 里执行它。
// 所有挂点都在 startConvoChain() 里,由 events.js 用 typeof 守卫调用。
let CH=null, CH_ID=null, CH_SUB=null, CH_LEAF=null, CH_PARENT=null;
// 选中的是一个**元素**而不是一个 uuid:一轮的标题行和它的第一步是同一个节点,
// 按 uuid 记的话方向键会在这两行之间原地打转。键的形状是 h:轮 / s:轮:步 / m:轮。
let CH_SEL=null, CH_FROM=null, CH_TO=null, CH_OPEN={}, CH_FKOPEN=null, CH_FRES=null;
let CH_XT=false, CH_XK=false, CH_XBUSY=false, CH_FBUSY=false;
// 链读失败时留着那个错误,画成带重试的红块;下一次开始读就清掉。
let CH_ERR=null;
// 本会话内搜索:CH_FIND 是搜索词,CH_FIND_HITS 是命中的轮(下标),CH_FIND_CUR 是 Enter 走到第几个。
let CH_FIND="", CH_FIND_HITS=[], CH_FIND_CUR=0;
// 导出的两个开关记在本机:每次导出都要重新勾一遍「含思考」,是在替人记一件他已经说过的事。
// 存储被禁用时读写都会抛错,那时就当没记过。这不是 DOM,载入时读它不违反下面那条边界。
const CH_PREF_KEY="tc.chain.export";
function chLoadPrefs(){
  try{
    const p=JSON.parse(localStorage.getItem(CH_PREF_KEY) || "null");
    if(p && typeof p==="object"){ CH_XT=p.tools===true; CH_XK=p.thinking===true; }
  }catch(e){}
}
chLoadPrefs();
function chSavePrefs(){
  try{ localStorage.setItem(CH_PREF_KEY, JSON.stringify({tools:CH_XT, thinking:CH_XK})); }catch(e){}
}
let CH_POS={}, CH_ORDER=[], CH_TR=[], CH_FKS={}, CH_AGTURN={}, CH_SUBQ="", CH_AT=[];
const CH_NODE=new Map();
let CH_SEQ=0, CH_NSEQ=0, CH_NT=null, CH_ROUTING=false;
let CH_LIST_SCROLL=0, CH_LIST_FOCUS=null, CH_MOREOPEN=false;
// 从列表打开的是哪一条会话。关掉链回到列表时焦点要落回那一行的「打开」按钮;
// 列表在这期间重画过的话,原来那个按钮已经不在了,就按这个 id 找新画出来的那一个。
let CH_LIST_ID=null;
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
// 只画数:转录是外来数据,一个字符串在这里原样放过去就是一段 HTML。
const chTok=n=>typeof n!=="number" || !isFinite(n) ? "?" : n>=1000 ? Math.round(n/1000)+"k" : String(n);
const chIsSession=id=>CH_UUID.test(String(id||""));
// 时间走共用的 timeTag:写日期和时刻(和别的页同一种写法),悬停看到秒;
// 没有时间戳就写「时间未知」,不写一个 00:00:缺失和午夜是两件事。转录是外来数据,timeTag 自己转义。
const chWhen=ts=>timeTag(ts, {relative:false});
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
// showView("convos", false) 只要地址仍带着会话 id,就不是后退回列表,链不能跟着没了。
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
  if(!$("cvbox").hidden){
    CH_LIST_SCROLL=window.scrollY;CH_LIST_FOCUS=document.activeElement;CH_LIST_ID=id;
    $("cvbox").hidden=true;
    if(typeof CV_OBSERVER!=='undefined') CV_OBSERVER?.disconnect();
  }
  const same=CH_ID===id && CH_SUB===sub;
  if(same && CH_LEAF===leaf && CH && !focusU && !opts.force){ $("chbox").hidden=false; return; }
  // 换分支时按 uuid 记下选中和展开的是哪些节点,重新加载后找回来。按下标记的话,
  // 同一个下标在另一条分支上是另一个节点,而范围、导出和分叉会一声不吭地跟着它走。
  let keepSel=null, keepHead=false, keepOpen=[];
  if(same && CH && CH.turns){
    keepSel=chSelU(); keepHead=!!CH_SEL && CH_SEL[0]==="h";
    keepOpen=Object.keys(CH_OPEN).filter(ti=>CH_OPEN[ti]).map(ti=>(CH.turns[+ti]||{}).u).filter(Boolean);
  }
  if(!same){
    CH_SEL=null; CH_FROM=null; CH_TO=null; CH_OPEN={}; CH_FRES=null;
    if(CH_ID!==id){ CH_SUBQ=""; chFindReset(); }
    if(!sub || CH_ID!==id) CH_PARENT=null;
  }
  CH_FKOPEN=null; CH_ID=id; CH_SUB=sub; CH_LEAF=leaf;
  const want=chHashFor(id, sub, leaf);
  if(location.hash.slice(1)!==want){ try{ history.pushState(null, "", "#"+want); }catch(e){} }
  $("chbox").hidden=false;
  const seq=++CH_SEQ;
  CH_ERR=null;
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
    // 读坏了画成带重试的红块,不写进标题旁的灰字:灰字读起来像一句普通的说明。
    CH=null; CH_ERR=e; $("chnote").textContent=""; chRender(); return;
  }
  if(seq!==CH_SEQ) return;
  CH=j; $("chnote").textContent="";
  try{ chAfterLoad(focusU, keepSel, keepHead, keepOpen); }
  catch(e){ $("chnote").textContent="渲染失败："+e.message; }
}
// 页面的「刷新」按钮在会话屏上也重读开着的那条链(转录还在被写,链尾会长)。
// 选中、展开和起止按 uuid 找回来;没开链时什么也不读。
function reloadConvoChain(){
  if(!chOpen()) return Promise.resolve();
  return openConvoChain(CH_ID, {sub:CH_SUB, leaf:CH_LEAF, force:true});
}
function chAfterLoad(focusU, keepSel, keepHead, keepOpen){
  chIndex();
  if(CH_FIND){ chFindCompute(); CH_FIND_CUR=Math.min(CH_FIND_CUR, Math.max(0, CH_FIND_HITS.length-1)); chFindCount(); }
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
  CH=null; CH_ID=null; CH_SUB=null; CH_LEAF=null; CH_PARENT=null; CH_SEL=null; CH_ERR=null; chFindReset();
  CH_FROM=null; CH_TO=null; CH_OPEN={}; CH_FKOPEN=null; CH_MOREOPEN=false; CH_FRES=null; CH_NODE.clear();
  $("chbox").hidden=true;
  $("cvbox").hidden=false;
  requestAnimationFrame(()=>{
    if(CH_ID || CURVIEW!=="convos") return;
    window.scrollTo({top:CH_LIST_SCROLL,behavior:'instant'});
    const opener=CH_LIST_FOCUS?.isConnected && CH_LIST_FOCUS!==document.body ? CH_LIST_FOCUS : chListOpener(CH_LIST_ID);
    if(opener) opener.focus({preventScroll:true});
    if(typeof cvObserve==='function') cvObserve();
  });
  if(!keepHash && location.hash.slice(1).indexOf("convos/")===0){
    try{ history.replaceState(null, "", "#convos"); }catch(e){}
  }
}

function chListOpener(id){
  if(!id || typeof CSS==='undefined') return null;
  return $("cvgroups")?.querySelector?.(`[data-cvopen="${CSS.escape(id)}"]`) || null;
}
// 卡片标题就是会话名,接在面包屑「会话列表 /」后面。链还没读回来时先用列表里的名字,
// 列表里也没有(从地址直接打开)就写会话 id 的前 8 位,不写一个笼统的「会话内容」。
function chTitleText(){
  if(CH && CH.title) return CH.title;
  const row=typeof CONVOS!=='undefined' && CONVOS && Array.isArray(CONVOS.groups)
    ? CONVOS.groups.flatMap(group=>group.shown || []).find(item=>item.id===CH_ID) : null;
  return row?.title || (CH_ID ? "会话 "+String(CH_ID).slice(0,8) : "会话内容");
}

// 链上每个节点的位置。范围、起止、分叉菜单都按它算,所以只算一遍。
function chIndex(){
  CH_ORDER=[]; CH_POS={}; CH_TR=[]; CH_FKS={}; CH_AGTURN={}; CH_AT=[];
  ((CH && CH.turns) || []).forEach((t, ti)=>{
    const a=CH_ORDER.length;
    if(t.type==="marker"){ CH_POS[t.u]=CH_ORDER.length; CH_ORDER.push(t.u); CH_AT.push({ti, ts:t.ts}); }
    else (t.steps || []).forEach(s=>{
      CH_POS[s.u]=CH_ORDER.length; CH_ORDER.push(s.u); CH_AT.push({ti, ts:s.ts});
      if(s.agentId && CH_AGTURN[s.agentId]==null) CH_AGTURN[s.agentId]=t.k;
    });
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
// [起点位置, 终点位置]。终点没设时:设过起点就到链尾(「从这里导出到结尾」),
// 否则跟着选中,选中也没有就是链尾。设了起点还让终点跟着选中的话,终点就是起点自己。
function chRange(){
  const a=CH_FROM!=null && CH_POS[CH_FROM]!=null ? CH_POS[CH_FROM] : 0;
  const tu=CH_TO || (CH_FROM ? null : chSelEnd());
  const b=tu!=null && CH_POS[tu]!=null ? CH_POS[tu] : CH_ORDER.length-1;
  return [a, b];
}

function chRender(){
  $("chbox").classList?.toggle('ch-short',CH_ORDER.length<=12);
  chRenderHead(); chRenderList(); chRenderAct(); chRenderDet();
}

function chRenderHead(){
  const cr=$("chcrumb"), hd=$("chhead"), wn=$("chwarn");
  const title=$("chtitle");
  if(title){
    // 标题点一下就地改名(conversation-actions.js);子代理只读,没载入改名模块或只读预览时就是纯文字。
    // 列表里的标题仍然是「打开会话」,改名在它旁边的铅笔上。
    const editing=!CH_SUB && CH_ID && typeof cvInlineHtml==="function" ? cvInlineHtml("chain", CH_ID) : "";
    const renamable=!CH_SUB && CH_ID && CH && CH.available && typeof cvQuickRename==="function" && !ConsoleActions.readOnly;
    if(editing) title.innerHTML=editing;
    else if(renamable) title.innerHTML=`<button type="button" class="ch-title-btn" data-cvquick-chain="${esc(CH_ID)}" title="点击重命名">${esc(chTitleText())}</button>`;
    else title.textContent=chTitleText();
    title.title=CH_ID || "";
    if(editing && typeof cvInlineAfterPaint==="function") cvInlineAfterPaint();
  }
  const find=$("chfind");
  if(find) find.hidden=!(CH && CH.available);
  // 面包屑这一行只在子代理里出现(返回主会话)。子代理选择器很少用,收进「更多操作与详情」菜单里,
  // 不再占着会话的第一行。
  cr.innerHTML=CH_SUB
    ? `<button class="icon-only mini" data-chact="back" title="‹ 返回主会话"><svg class="ic" aria-hidden="true"><use href="#i-left"/></svg><span class="control-label">‹ 返回主会话</span></button>`
      +`<span>${CH_PARENT && CH_PARENT.title ? esc(CH_PARENT.title)+" › " : ""}子代理 <code>${esc(CH_SUB)}</code> · 只读</span>`
    : "";
  if(!CH){
    hd.innerHTML="";
    wn.innerHTML=CH_ERR ? errorBlock("会话内容", CH_ERR, "data-chretry") : "";
    return;
  }
  if(!CH.available){
    hd.innerHTML=`<span style="color:var(--warn)">${esc(CH.reason || "这份转录读不了")}</span>`;
    wn.innerHTML=""; return;
  }
  const nT=(CH.turns || []).filter(t=>t.type==="turn").length;
  const manage=!CH_SUB && typeof cvOpenManager==='function', off=ConsoleActions.readOnly?' disabled':'';
  const item=(icon,label,attrs,cls='')=>`<button type="button" class="menu-item${cls?' '+cls:''}" ${attrs}><svg class="ic" aria-hidden="true"><use href="#${icon}"/></svg><span>${label}</span></button>`;
  const S=CH.subagents || [];
  // 标题是会话名;这一行只写它属于哪个项目、有几轮。项目取工作目录的最后一段,完整路径在悬停。
  const dir=String(CH.storageCwd || CH.cwd || CH.projectDir || "");
  const proj=dir.split(/[\\/]/).filter(Boolean).pop() || "";
  // 「更多操作与详情」是一个真菜单(popover):点别处、按 Esc 收起,浮在链上面不挤开它。
  // 删除会话收在菜单最底下、和别的项隔开,标题栏上不再常驻一个垃圾桶。
  hd.innerHTML=(proj ? `<span class="ch-proj" title="${esc(dir)}">项目 <b>${esc(proj)}</b></span>` : "")
    +`<span><b>${nT}</b> 轮</span>`
    +(CH.leafIsDefault ? "" : `<span class="alt">正在看一条非默认分支</span><button class="icon-only mini" data-chact="latest" title="回到最新分支"><svg class="ic" aria-hidden="true"><use href="#i-branch"/></svg><span class="control-label">回到最新分支</span></button>`)
    +`<button type="button" class="mini ch-more" popovertarget="ch-more" title="更多操作与详情"><svg class="ic" aria-hidden="true"><use href="#i-more"/></svg>更多操作与详情</button>`
    +`<div class="pop-menu ch-more-menu" id="ch-more" popover aria-label="更多操作与详情">`
    +(manage?item('i-edit','重命名',`data-cvrename="${esc(CH.id)}"${off} title="重命名"`)+item('i-move','移动会话',`data-cvmove="${esc(CH.id)}"${off} title="移动会话"`)+'<div class="menu-sep" role="separator"></div>':'')
    // 一场长会话有上千个子代理:一个裸下拉框翻不动。加一个筛选框,每项前面写上派生它的那一轮。
    +(!CH_SUB && S.length ? `<div class="ch-subs"><div class="ch-subs-h">子代理 (${S.length})</div>`
      +`<input id="chsubq" type="search" placeholder="筛选(描述、类型、轮号)" aria-label="筛选子代理" value="${esc(CH_SUBQ)}">`
      +`<select id="chsubs" aria-label="打开一个子代理的转录">${chSubOptions(CH_SUBQ)}</select></div><div class="menu-sep" role="separator"></div>` : "")
    +`<div class="menu-facts">`
    +`<span>${chN(CH.lines)} 行 · ${chN(CH.chainEntries)} 个链条目 · ${kb(CH.bytes)}</span>`
    +`<span>显示链 <b>${chN(CH.pathLen)}</b> 个节点</span>`
    +`<span${CH.badLines ? ' class="bad"' : ""}>坏行 ${chN(CH.badLines)}</span>`
    +`<span>悬空父节点 ${chN(CH.danglingParents)}</span>`
    +(CH.duplicateUuids ? `<span title="同一个 uuid 被整行重写过,只保留第一份">重复 uuid ${CH.duplicateUuids}</span>` : "")
    +`<span>压缩 ${chN(CH.compactions)} · 分叉 ${chN(CH.forks)}</span>`
    +`<span title="${CH.cached ? "这次走的是缓存的索引,数字是当初建索引的耗时" : "这次重新建了索引"}">索引 ${chN(CH.indexMs)} ms${CH.cached ? "(缓存)" : ""}</span>`
    +`<span class="cwd" title="${esc(CH.file || "")}">保存于 ${esc(CH.locationInferred?CH.projectDir:CH.storageCwd || CH.cwd || CH.projectDir || '工作目录未记录')}</span>`
    +`</div>`
    +(manage?'<div class="menu-sep" role="separator"></div>'+item('i-trash','删除会话',`data-cvdelete="${esc(CH.id)}"${off} title="永久删除这场会话"`,'menu-danger'):'')
    +`</div>`;
  // 读取警告留在菜单外面,永远看得见:坏数据不能藏在一个要点开才看到的地方。
  // 但只占一行:每条压成一个短说法,原句(含那串 uuid)放在悬停里。以前两条整行的黄色长句排在会话前面。
  const w=chWarnParts();
  wn.innerHTML=w.parts.length
    ? `<div class="warn-line" title="${esc(w.full.join("\n"))}">读取提示：${w.parts.map(esc).join(" · ")}</div>` : "";
  if(CH_MOREOPEN) chReshow("ch-more");
}
// 读取警告的短说法。后端的原句认得出的压短;认不出的原样放进这一行,一条都不丢。
// 坏行和悬空父节点以计数字段为准,同义的那两句原句不再重复。
const CH_WARN_SHORT=[
  [/^\d+ 行不是合法 JSON/, ()=>null],
  [/^\d+ 条记录的父节点不在文件里/, ()=>null],
  [/^最后一行还没写完/, ()=>"末行未写完"],
  [/^上溯时遇到环/, ()=>"链上有环"],
  [/^(\d+) 行是已出现过的 uuid 的重写/, m=>"重复 uuid "+m[1]],
  [/^(\d+) 个子代理文件或其 meta 读不动/, m=>"子代理读不动 "+m[1]],
  [/^(\d+) 条助手记录没有 message\.id/, m=>"缺 message.id "+m[1]],
  [/^压缩边界 (.+?) 没有可用的前驱指针/, m=>"压缩边界 "+m[1].split(",").length+" 个无前驱"]
];
function chWarnParts(){
  const parts=[], full=[];
  if(CH.badLines) parts.push("坏行 "+chN(CH.badLines));
  if(CH.danglingParents) parts.push("悬空父节点 "+chN(CH.danglingParents));
  if(CH.badLines || CH.danglingParents) full.push(`坏行 ${chN(CH.badLines)} · 悬空父节点 ${chN(CH.danglingParents)}`);
  (CH.warnings || []).forEach(w=>{
    const text=String(w);
    full.push(text);
    const rule=CH_WARN_SHORT.find(r=>r[0].test(text));
    if(!rule){ parts.push(text); return; }
    const short=rule[1](text.match(rule[0]));
    if(short) parts.push(short);
    // 坏行、悬空父节点的原句在计数字段为空时(老版本后端)也不能丢。
    else if(!CH.badLines && /JSON/.test(text)) parts.push("坏行 "+(text.match(/^\d+/) || ["?"])[0]);
    else if(!CH.danglingParents && /父节点/.test(text)) parts.push("悬空父节点 "+(text.match(/^\d+/) || ["?"])[0]);
  });
  return {parts, full};
}

// 重画会把开着的菜单换掉,浏览器悄悄关上它(不发 toggle 事件);人没关过的菜单要原样再打开。
function chReshow(id){
  const menu=$(id);
  if(!menu || typeof menu.showPopover!=="function") return;
  const opener=typeof CSS!=="undefined" ? $("chbox").querySelector(`[popovertarget="${CSS.escape(id)}"]`) : null;
  try{ if(!menu.matches(":popover-open")) menu.showPopover(opener ? {source:opener} : undefined); }catch(e){}
}

function chSubLabel(s){
  const ti=CH_AGTURN[s.agentId];
  return (ti!=null ? "#"+ti+" · " : "")+(s.agentType ? s.agentType+" · " : "")+(s.description || s.agentId)+(s.gz ? " (gz)" : "");
}
function chSubOptions(q){
  const S=(CH && CH.subagents) || [], n=String(q || "").trim().toLowerCase();
  const hit=n ? S.filter(s=>chSubLabel(s).toLowerCase().indexOf(n)>=0 || String(s.agentId).toLowerCase().indexOf(n)>=0) : S;
  const head=n ? `筛出 ${hit.length} / ${S.length} 个,选一个打开` : `共 ${S.length} 个,选一个打开`;
  return `<option value="">${esc(head)}</option>`+hit.map(s=>`<option value="${esc(s.agentId)}">${esc(chSubLabel(s))}</option>`).join("");
}
// 分支菜单也是 popover:点别处、按 Esc 收起,开第二个时第一个自己关上。CH_FKOPEN 跟着 toggle 事件走,
// 只为了列表重画之后把它再打开。
const chFkId=u=>"chfk-"+encodeURIComponent(String(u));
function chFkHtml(u){
  const f=CH_FKS[u];
  if(!f) return "";
  return `<div class="ch-fk pop-menu" id="${esc(chFkId(u))}" popover data-chfk-for="${esc(u)}" role="menu"><div class="hd">${f.alternatives.length} 条分支</div>`
    +f.alternatives.map(a=>`<button role="menuitem" data-chleaf="${esc(a.leaf)}" data-chat="${esc(f.u)}"${a.active ? " disabled" : ""}>`
      +`<span class="${a.active ? "act" : "alt"}">${a.active ? "当前" : "切换"}</span>`
      +`<span class="pv" title="${esc(a.preview || "")}">${esc(a.preview || "(这条分支里没有用户消息)")}</span>`
      +`<span class="sz">${chN(a.size)} 个节点${a.leafLineIndex!=null ? " · 止于第 "+(a.leafLineIndex+1)+" 行" : ""}</span>`
      // 人常把同一句话重发一遍,几支的第一条用户消息一字不差:再写出每支从哪一刻开始、
      // 第一步和最后一步各是什么,才分得开。
      +`<span class="sub">${chWhen(a.ts)} 起:${esc(chK(a.firstKind)[0])} ${esc(a.firstPreview || "(无文字)")}`
      +` … 止于 ${chWhen(a.leafTs)}:${esc(chK(a.leafKind)[0])} ${esc(a.leafPreview || "(无文字)")}</span></button>`).join("")
    +`</div>`;
}
function chFkBtn(u, n){
  return `<button type="button" class="mini" data-chfk="${esc(u)}" popovertarget="${esc(chFkId(u))}" title="这个节点下面分出了 ${n} 条分支">⑂ ${n} 个分支</button>`;
}

// 这一轮里各类节点各有几个,写成「工具调用 63 · 回复 36」。用户那一句不算:每轮都是一句。
function chKinds(c){
  return Object.keys(CH_KIND).filter(k=>k!=="human" && c[k]).map(k=>`${chK(k)[2]} ${c[k]}`).join(" · ");
}
function chTurnHtml(t, ti){
  const open=!!CH_OPEN[ti], hu=t.human, c=t.counts || {};
  // 收起的一行只写步数和时间。以前这里是一串表情计数(🔧63 ✎36 💭43 📎73),意思要悬停才知道;
  // 现在各类计数写成字放在步数的悬停里,展开后第一行也写着。
  const kinds=chKinds(c), nSteps=(t.steps || []).length;
  const nf=(t.forks || []).length;
  let s=`<div class="ch-t${open ? " open" : ""}" role="option" aria-expanded="${open}" data-chk="h:${ti}" data-chu="${esc(t.u)}" id="chk-h-${ti}">`
    +`<span class="caret">${open ? "▼" : "▶"}</span><span class="no">#${esc(t.k)}</span>`
    +(hu ? `<span class="pv" title="${esc(hu.preview || "")}">${esc(hu.preview || "(空消息)")}</span>`
         : `<span class="pv none">(不是从一条用户消息开始的)</span>`)
    +`<span class="ch-cnt"><span title="${esc("这一轮 "+nSteps+" 步"+(kinds ? "：" + kinds : ""))}">${nSteps} 步</span>`
    +(nf ? `<span class="fk" title="这一轮里有 ${nf} 个分叉点">⑂${nf}</span>` : "")+`</span>`
    +`<span class="ts">${chWhen(hu ? hu.ts : t.ts)}</span>`
    // 收起时这一行也要看得出最后答了什么,不只是问了什么。
    +(t.reply && t.reply.preview ? `<span class="rp" title="${esc(t.reply.preview)}">✎ ${esc(t.reply.preview)}</span>` : "")
    +`</div>`;
  // 步骤只在展开时才拼:收起的轮在 DOM 里只有一行。
  if(open) s+=`<div class="ch-steps">`+(kinds ? `<div class="ch-kinds">${esc(kinds)}</div>` : "")
    +(t.steps || []).map((x, si)=>chStepHtml(x, ti, si)).join("")+`</div>`;
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
    +`<span class="ts">${chWhen(x.ts)}</span>`
    +(extra ? `<span class="x">${extra}</span>` : "")+`</div>`+chFkHtml(x.u);
}
function chMarkerHtml(t, ti){
  const fk=(t.forks || []).length ? " "+chFkBtn(t.u, t.forks[0].alternatives.length) : "";
  if(t.kind==="compact"){
    const open=!!CH_OPEN[ti];
    const trig=t.trigger==="auto" ? "自动压缩" : t.trigger==="manual" ? "手动压缩" : "压缩";
    const tok=(t.preTokens!=null || t.postTokens!=null)
      ? ` · ${esc(chTok(t.preTokens))}→${esc(chTok(t.postTokens))} tokens` : " · tokens 未记录";
    return `<div class="ch-cmp${open ? " open" : ""}" role="option" aria-expanded="${open}" data-chk="m:${ti}" data-chu="${esc(t.u)}" id="chk-m-${ti}"`
      +` title="压缩边界。点一下${open ? "收起" : "展开"}概括">`
      +`<span class="ln"></span><span class="lb">⟂ ${trig}${tok}</span><span class="ts">${chWhen(t.ts)}</span>`
      +`<span class="ln"></span>${fk}</div>`+chFkHtml(t.u);
  }
  return `<div class="ch-sum" role="option" data-chk="m:${ti}" data-chu="${esc(t.u)}" id="chk-m-${ti}"`
    +` title="${esc(t.preview || "")}">≡ 压缩概括:${esc(t.preview || "(空)")}${fk}</div>`+chFkHtml(t.u);
}

function chRenderList(){
  const L=$("chlist");
  if(!CH || !CH.available){ L.innerHTML=!CH && CH_ID && !CH_ERR ? loadingBlock("会话内容") : ""; return; }
  const st=L.scrollTop, T=CH.turns || [];
  const parts=[];
  // 搜索时只画命中的轮;压缩边界和概括不是一轮对话,搜索时不画。
  const hits=CH_FIND ? new Set(CH_FIND_HITS) : null;
  for(let ti=0; ti<T.length; ti++){
    const t=T[ti];
    if(hits && !hits.has(ti)) continue;
    if(t.type==="marker"){
      // 压缩概括挂在它前面那条边界下面,边界收起时一起收起。
      const underCmp=t.kind==="summary" && ti>0 && T[ti-1].type==="marker" && T[ti-1].kind==="compact";
      if(underCmp && !CH_OPEN[ti-1]) continue;
      parts.push(chMarkerHtml(t, ti));
    } else parts.push(chTurnHtml(t, ti));
  }
  L.innerHTML=parts.join("") || (hits ? emptyBlock("没有哪一轮的提问或回复里含「"+CH_FIND+"」")
    : `<div class="ch-empty">这条链上没有节点</div>`);
  L.scrollTop=st;
  chMarks();
  if(CH_FKOPEN) chReshow(chFkId(CH_FKOPEN));
}

function chRenderTurn(ti){
  const turn=CH?.turns?.[ti], header=$('chlist').querySelector(`[data-chk="h:${ti}"]`);
  if(!header || turn?.type!=='turn'){chRenderList();return;}
  // Keep other rows and their measured content-visibility heights intact.
  const steps=header.nextElementSibling;
  if(steps?.classList.contains('ch-steps')) steps.remove();
  header.outerHTML=chTurnHtml(turn,ti);chMarks();
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
  const L=$("chlist"), el=L.querySelector(`[data-chk="${k}"]`);
  if(el){
    if(scroll){
      const r=el.getBoundingClientRect(), lr=L.getBoundingClientRect();
      const top=lr.top+L.clientTop, bottom=top+L.clientHeight;
      if(r.top<top) L.scrollTop+=r.top-top;
      else if(r.bottom>bottom) L.scrollTop+=r.bottom-bottom;
    }
    L.setAttribute("aria-activedescendant", el.id);
  }
  chRenderAct();
  // 按住 j 连走时不为每一行都发请求:停下来 90ms 再取。
  clearTimeout(CH_NT);
  const u=chSelU();
  if(!u){ chRenderDet(); return; }
  const ck=(CH_SUB || "")+"|"+u;
  if(CH_NODE.has(ck)){ chRenderDet(CH_NODE.get(ck)); return; }
  $("chdet").innerHTML=`<div class="ch-hint">读取中</div>`;
  CH_NT=setTimeout(()=>{ chLoadNode(u, ck).catch(e=>{ $("chdet").innerHTML=errorBlock("这条消息", e, "data-chnode-retry"); }); }, 90);
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
    D.innerHTML=`<div class="ch-hint">选择消息</div>`;
    return;
  }
  if(n.error){ D.innerHTML=errorBlock("这条消息", n.error, "data-chnode-retry"); return; }
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
  // 每段原文右上角一个「复制」:几万字的工具输出靠拖选复制,拖到一半就滚走了。
  // 按钮不带正文(几十万字的属性会让 DOM 翻倍),点的时候读旁边那段 pre 的文字。
  const pre=(t, cls)=>`<div class="ch-prew"><button type="button" class="mini ch-copy" data-chcopy="" title="复制这一段"><svg class="ic" aria-hidden="true"><use href="#i-copy"/></svg>复制</button>`
    +`<pre class="ch-pre${cls ? " "+cls : ""}">${esc(t)}</pre></div>`;
  let h=`<h5>${k[0]} ${esc(k[2])}${n.role ? " · "+esc(n.role) : ""}</h5>`;
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
  if(!body) h+=`<div class="ch-hint">无正文</div>`;
  h+=`<details><summary>消息信息</summary><dl class="ch-kv">${kv.map(x=>`<dt>${esc(x[0])}</dt><dd>${esc(x[1])}</dd>`).join("")}</dl>`
    +(flags ? `<div class="ch-flags">${flags}</div>` : "")+`</details>`;
  h+=n.raw!=null
    ? `<details><summary>原始 JSON 行${n.rawTruncated ? "(已截断)" : ""}</summary>${pre(n.raw)}</details>`
    : `<div class="faint">这一行 ${kb(n.byteLength)},太大,不下发原文。</div>`;
  D.innerHTML=h;
}

// 范围的一端写成「#轮号 时间」,不写 uuid 前八位:人认得出第几轮、几点,认不出 ee22b599。
// 压缩边界和概括不属于哪一轮,直接说它是什么。
function chPosLabel(p){
  const at=CH_AT[p], t=at && (CH.turns || [])[at.ti];
  if(!t) return "—";
  const when=at.ts ? " "+fmtTime(at.ts, {relative:false}) : "";
  if(t.type==="marker") return (t.kind==="compact" ? "压缩边界" : "压缩概括")+when;
  return "#"+t.k+when;
}
function chRenderAct(){
  const A=$("chact");
  if(!CH || !CH.available || !CH_ORDER.length){ A.innerHTML=""; return; }
  const r=chRange(), a=r[0], b=r[1], su=chSelU(), bad=a>b, f=CH_FRES, fe=chSelEnd();
  // 选中的是一轮的标题行时,分叉点是这一轮的最后一步,不是屏上高亮的那句提问:按钮上写明作用范围。
  const fl=fe && CH_SEL && CH_SEL[0]==="h" ? "从本轮末尾新建会话" : "从这里新建会话";
  const pick="先在左侧选一条消息";
  // 每个按钮都写字。以前全是图标(⇤ ⇥、漏斗、下载箭头、分支),要悬停才知道哪个是导出、哪个是新建会话;
  // 不能点的时候悬停说为什么,而不是只重复一遍动作名。
  const btn=(attrs,icon,label,reason,cls)=>`<button type="button" class="mini${cls ? " "+cls : ""}" ${attrs}${reason ? " disabled" : ""}`
    +` title="${esc(reason ? disabledTitle(label, reason) : label)}"><svg class="ic" aria-hidden="true"><use href="#${icon}"/></svg>${label}</button>`;
  const forkWhy=CH_SUB ? "子代理不能新建会话" : !su ? pick : CH_FBUSY ? "正在新建会话" : "";
  A.innerHTML=`<div class="ch-row ch-range"><span class="faint">范围</span>`
    +`<span>${esc(chPosLabel(a))} → ${esc(chPosLabel(b))}</span>`
    +`<span class="faint" title="${CH_TO ? '指定终点' : !CH_FROM && CH_SEL ? '到所选消息' : '到链尾'}">${bad ? "" : "· 共 "+fmtNum(b-a+1)+" 条"}</span>`
    +(bad ? `<span style="color:var(--bad)">起点在终点之后</span>` : "")+`</div>`
    +`<div class="ch-row">`+btn('data-chact="from"', "i-range-start", "从这里开始", su ? "" : pick)
    +btn('data-chact="to"', "i-range-end", "到这里结束", su ? "" : pick)
    +btn('data-chact="clr"', "i-filter-clear", "清除范围", CH_FROM || CH_TO ? "" : "还没有设定范围")+`</div>`
    +`<div class="ch-row"><label><input type="checkbox" id="chtools"${CH_XT ? " checked" : ""}> 含工具调用</label>`
    +`<label><input type="checkbox" id="chthink"${CH_XK ? " checked" : ""}> 含思考</label>`
    +btn('data-chexport="md"', "i-fetch", "导出 Markdown", bad ? "起点在终点之后" : CH_XBUSY ? "正在导出" : "", "primary")+`</div>`
    +`<div class="ch-row"><button type="button" class="mini" data-ctfork="at"${forkWhy ? " disabled" : ""}`
    +` title="${esc(forkWhy ? disabledTitle(fl, forkWhy) : fl+"：包含到消息 "+chU8(fe)+" 为止的历史")}"><svg class="ic" aria-hidden="true"><use href="#i-branch"/></svg>${esc(fl)}</button>`
    +(CH_SUB ? `<span class="faint">子代理不能新建会话</span>` : su ? "" : `<span class="faint">${pick}</span>`)+`</div>`
    +(!f ? "" : f.error
      ? `<div class="ch-fres bad">新建会话失败：${esc(f.error)}</div>`
      : `<div class="ch-fres"><div>${f.reused?'已创建的会话':'新会话'} ${esc(f.title || '')} <code>${esc(f.newId)}</code></div>`
        +`<div class="cv-location">保存位置：${esc(f.storagePath || f.file || '')}</div>`
        +`<div class="faint">${chN(f.emitted)} 条记录 · 共 ${chN(f.lines)} 行 · 约 ${esc(chTok(f.approxTokens))} tokens · `
        +`${f.fromBoundary ? "从压缩边界 "+esc(chU8(f.fromBoundary))+" 起" : "完整历史,没有压缩边界"}</div>`
        +(f.warnings || []).map(w=>`<div class="warn-line">${esc(w)}</div>`).join("")
        +`<pre class="ch-pre">${esc(f.command || "")}</pre>`
        +`<div class="ch-row"><button class="icon-only mini" data-chcopy="${esc(f.command || "")}" title="复制命令"><svg class="ic" aria-hidden="true"><use href="#i-copy"/></svg><span class="control-label">复制命令</span></button>`
        +(typeof cvOpenManager==='function'?`<button class="icon-only mini" data-cvopen-new="${esc(f.newId)}" title="打开新会话"><svg class="ic" aria-hidden="true"><use href="#i-eye"/></svg><span class="control-label">打开新会话</span></button><button class="icon-only mini" data-cvrename="${esc(f.newId)}" title="重命名"><svg class="ic" aria-hidden="true"><use href="#i-edit"/></svg><span class="control-label">重命名</span></button><button class="icon-only mini" data-cvmove="${esc(f.newId)}" title="移动"><svg class="ic" aria-hidden="true"><use href="#i-move"/></svg><span class="control-label">移动</span></button>`:'')
        +`</div></div>`);
}

// ── 本会话内搜索 ──
// 收起的轮不在 DOM 里,浏览器的查找搜不到它们。这里按每轮的用户提问和最后一条回复在数据里筛,
// 筛出来的轮照常能点开;Enter 走到下一个命中,计数写成「第几个 / 共几个」。
function chFindMatches(t, q){
  if(t.type!=="turn") return false;
  const text=((t.human && t.human.preview) || "")+"\n"+((t.reply && t.reply.preview) || "");
  return text.toLowerCase().indexOf(q)>=0;
}
function chFindCompute(){
  const q=CH_FIND.toLowerCase();
  CH_FIND_HITS=q ? ((CH && CH.turns) || []).map((t, ti)=>chFindMatches(t, q) ? ti : -1).filter(ti=>ti>=0) : [];
}
function chFindApply(value){
  CH_FIND=String(value || "").trim();
  chFindCompute(); CH_FIND_CUR=0;
  chRenderList(); chFindCount();
}
function chFindCount(){
  const n=$("chfindn");
  if(!n) return;
  n.textContent=!CH_FIND ? "" : CH_FIND_HITS.length ? `${CH_FIND_CUR+1} / ${CH_FIND_HITS.length} 轮` : "没有匹配的轮";
}
function chFindNext(){
  if(!CH_FIND_HITS.length) return;
  // 第一次 Enter 先选中当前这个命中;已经选中了再按才走到下一个。
  if(CH_SEL==="h:"+CH_FIND_HITS[CH_FIND_CUR]) CH_FIND_CUR=(CH_FIND_CUR+1)%CH_FIND_HITS.length;
  chSelect("h:"+CH_FIND_HITS[CH_FIND_CUR], true);
  chFindCount();
}
function chFindReset(){
  CH_FIND=""; CH_FIND_HITS=[]; CH_FIND_CUR=0;
  const q=$("chfindq");
  if(q) q.value="";
  chFindCount();
}
// 搜索框是页面上原来没有的一块,在挂点里插到读取提示下面、链表上面。
function chMountFind(){
  if($("chfind") || !$("chwarn")) return;
  $("chwarn").insertAdjacentHTML("afterend", `<div class="ch-find" id="chfind" hidden>`
    +`<input type="search" id="chfindq" placeholder="在本会话中搜索提问或回复" aria-label="在本会话中搜索提问或回复" autocomplete="off">`
    +`<span class="faint" id="chfindn" role="status" aria-live="polite"></span></div>`);
}

function chToggle(){
  if(!CH_SEL) return;
  const p=CH_SEL.split(":"), ti=+p[1];
  if(p[0]==="s"){ CH_OPEN[ti]=false; CH_SEL="h:"+ti; }
  else CH_OPEN[ti]=!CH_OPEN[ti];
  chRenderTurn(ti); chSelect(CH_SEL, true);
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
  // 导出是读:GET,只读预览里也能导,不记成一次「操作」,也不让别的读缓存失效。
  const q=[["id", CH_ID], ["to", to], ["from", CH_FROM], ["leaf", CH_LEAF], ["sub", CH_SUB],
           ["tools", CH_XT ? "1" : "0"], ["thinking", CH_XK ? "1" : "0"]]
    .filter(x=>x[1]).map(x=>x[0]+"="+encodeURIComponent(x[1])).join("&");
  $("chnote").textContent="导出中";
  try{
    const j=await api("/api/convo/export?"+q);
    if(typeof j.text!=="string") throw new Error(j.error || "响应里没有正文");
    const url=URL.createObjectURL(new Blob([j.text], {type:"text/markdown;charset=utf-8"}));
    const el=document.createElement("a");
    el.href=url; el.download=j.filename || "conversation.md";
    document.body.appendChild(el); el.click(); el.remove();
    setTimeout(()=>URL.revokeObjectURL(url), 5000);
    $("chnote").textContent="";
    toast(`已导出 ${j.filename}:${chN(j.turns)} 轮用户消息 · ${chN(j.nodes)} 个节点`
          +(j.truncated ? "(超过上限,已截断,文件里写明了截在哪)" : ""), j.truncated ? "bad" : "ok");
  }catch(e){
    $("chnote").textContent="导出失败:"+e.message;
    toast("导出失败:"+e.message, "bad");
  }finally{ CH_XBUSY=false; chRenderAct(); chFocusList(true); }
}

const CH_FREQUESTS=new Map();
function chForkRequest(body){
  const key='tc.fork.'+JSON.stringify([body.id,body.at,body.leaf || null]);
  let request=CH_FREQUESTS.get(key);
  try{request ||= sessionStorage.getItem(key);}catch(error){}
  request ||= crypto.randomUUID();CH_FREQUESTS.set(key,request);
  try{sessionStorage.setItem(key,request);}catch(error){}
  return {key,request};
}
// A lost response reuses the request identity, including after a page refresh.
async function chFork(){
  const at=chSelEnd();
  if(!at || CH_SUB || !CH || CH_FBUSY) return;
  const ok=await askConfirm({title:"从选中位置新建会话",
    body:"新会话会保存在当前项目，保留到所选消息为止的上下文。如果历史已压缩，会从最近一次压缩后的内容开始。原会话会保留。创建后可复制启动命令，在终端里接着聊。",
    confirmLabel:"新建会话"});
  // 确认框开着的这段时间里,人可能换了选中的消息或关掉了链:那时这次确认说的已不是眼前这一处。
  if(!ok || at!==chSelEnd() || CH_SUB || !CH || CH_FBUSY) return;
  const body={id:CH_ID, at};
  if(CH_LEAF) body.leaf=CH_LEAF;
  const request=chForkRequest(body);body.requestId=request.request;
  const stillSelected=()=>CH_ID===body.id && chSelEnd()===body.at && (CH_LEAF || null)===(body.leaf || null);
  CH_FBUSY=true; chRenderAct();
  $("chnote").textContent="正在新建会话";
  try{
    const j=await api("/api/convo/fork", {method:"POST", body:JSON.stringify(body)});
    if(!j.newId) throw new Error(j.error || "响应里没有新会话 id");
    CH_FREQUESTS.delete(request.key);
    try{sessionStorage.removeItem(request.key);}catch(error){}
    if(stillSelected()){CH_FRES=j;$("chnote").textContent="";}
    toast("新会话已创建 "+chU8(j.newId), "ok");
    // 新文件已经落在会话根下了;不重扫的话列表里看不到它,像是没分叉成。
    if(typeof loadConvos==="function") loadConvos().catch(()=>{});
  }catch(e){
    if(stillSelected()){CH_FRES={error:e.message};$("chnote").textContent="新建会话失败:"+e.message;}
    toast("新建会话失败:"+e.message, "bad");
  }finally{ CH_FBUSY=false; chRenderAct(); chFocusList(true); }
}

// Explicitly opening details may reveal the side panel; row expansion never does.
function chRevealSide(){
  if(typeof window==="undefined" || !window.matchMedia || !window.matchMedia("(max-width:760px)").matches) return;
  const A=$("chact");
  if(!A || !A.getBoundingClientRect) return;
  if(A.getBoundingClientRect().top > window.innerHeight*0.6){
    try{ A.scrollIntoView({block:"start", behavior:"smooth"}); }catch(err){}
  }
}
async function chCopy(text){
  try{ await navigator.clipboard.writeText(text); toast("已复制","ok"); }
  // 原文可能有几万字,不能整段塞进提示里;短的(比如启动命令)照旧附上,方便手动复制。
  catch(e){ toast("无法访问剪贴板，请手动选择文字复制"+(String(text).length<=300 ? "："+text : ""), "bad"); }
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
  // 选择器在「更多操作与详情」菜单里:打开子代理就把菜单收起,别让它浮在新内容上面。
  CH_MOREOPEN=false;
  try{ const m=$("ch-more"); if(m && m.hidePopover && m.matches(":popover-open")) m.hidePopover(); }catch(err){}
  CH_PARENT={title:CH && CH.title, leaf:CH_LEAF, u:chSelU()};
  openConvoChain(CH_ID, {sub:agentId});
}
// 子代理筛选框里按 Enter:打开筛出来的第一个,和在下拉框里选它是同一件事。
function chSubFirst(q){
  const S=(CH && CH.subagents) || [], n=String(q || "").trim().toLowerCase();
  const hit=n ? S.find(s=>chSubLabel(s).toLowerCase().indexOf(n)>=0 || String(s.agentId).toLowerCase().indexOf(n)>=0) : S[0];
  return hit ? hit.agentId : null;
}
// 页面级 Esc 退到这一屏时调这里(navigation.js 的 ESC_STEPS),一次只退一层:
// 先收分支菜单,再从子代理回主会话,最后关掉对话链回到列表。焦点在哪里都一样。
function convoChainEscape(){
  if(!chOpen()) return false;
  if(CH_FKOPEN){ CH_FKOPEN=null; chRenderList(); chFocusList(true); return true; }
  if(CH_SUB){ chAct("back"); return true; }
  chClose();
  return true;
}

// 链表上的按键。它是 role=listbox:方向键、Home/End 移动,Enter/空格展开,这是列表框本来的约定,
// 不是快捷键。只在焦点就在链表上时才接,返回 true 表示吃掉了。
function chKey(e){
  const k=e.key;
  if(!CH || !CH.available) return false;
  if(k==="ArrowDown"){ chMove(1); return true; }
  if(k==="ArrowUp"){ chMove(-1); return true; }
  if(k==="Home"){ chMove("home"); return true; }
  if(k==="End"){ chMove("end"); return true; }
  if(k==="Enter" || k===" "){ chToggle(); return true; }
  return false;
}

// 卡片里的按钮会被整块重画,焦点随之掉回 <body>,方向键就不再落在链表上。
// 所以按钮处理完就把焦点还给链。onlyIfLost 用于异步回来的时候:
// 人可能已经去点了别处,那时不抢。
function chFocusList(onlyIfLost){
  const L=$("chlist");
  if(!L || !chOpen()) return;
  const ae=document.activeElement;
  if(onlyIfLost && ae && ae!==document.body && document.contains && document.contains(ae)) return;
  try{ L.focus({preventScroll:true}); }catch(err){}
}
function chClick(e){
  const t=e.target;
  if(t.closest("#chclose")){ chClose(); return; }
  const ex=t.closest("[data-chexport]");
  if(ex){ if(!ex.disabled) chExport().catch(err=>toast("导出失败:"+err.message, "bad")); return; }
  const fo=t.closest("[data-ctfork]");
  if(fo){ if(!fo.disabled) chFork().catch(err=>toast("新建会话失败:"+err.message, "bad")); return; }
  const b=t.closest("[data-chact]");
  if(b){ if(!b.disabled){ chAct(b.dataset.chact); chFocusList(); } return; }
  const cp=t.closest("[data-chcopy]");
  if(cp){
    // 空值的是详情里每段原文旁的「复制」:复制的是旁边那段 pre 的文字。
    const box=cp.dataset.chcopy==="" && cp.closest(".ch-prew"), pre=box && box.querySelector("pre");
    chCopy(pre ? pre.textContent : cp.dataset.chcopy); return;
  }
  if(t.closest("[data-chretry]")){ openConvoChain(CH_ID, {sub:CH_SUB, leaf:CH_LEAF, force:true}); return; }
  if(t.closest("[data-chnode-retry]")){ if(CH_SEL) chSelect(CH_SEL, false); return; }
  const lf=t.closest("[data-chleaf]");
  if(lf){ if(!lf.disabled){ CH_FKOPEN=null; try{ lf.closest("[popover]")?.hidePopover(); }catch(err){} openConvoChain(CH_ID, {sub:CH_SUB, leaf:lf.dataset.chleaf, focus:lf.dataset.chat}); chFocusList(); } return; }
  // ⑂ 按钮由浏览器开合菜单(popovertarget),这里不动焦点:人要用 Tab 走进菜单里选分支。
  if(t.closest("[data-chfk]") || t.closest("[popover]")) return;
  const sb=t.closest("[data-chsub]");
  if(sb){ chOpenSub(sb.dataset.chsub); return; }
  const n=t.closest("[data-chk]");
  if(n){
    const k=n.dataset.chk, ti=+k.split(":")[1];
    // 标题行和压缩边界:没展开就展开,已经选中再点一次就收起。
    if(k[0]==="h" || n.classList.contains("ch-cmp")){
      const L=$("chlist"), y=n.getBoundingClientRect().top-L.getBoundingClientRect().top;
      if(!CH_OPEN[ti]) CH_OPEN[ti]=true;
      else if(CH_SEL===k) CH_OPEN[ti]=false;
      // 上一次分叉的结果属于上一个节点(chSelect 同理;这里先改了 CH_SEL,它就看不出换了节点)。
      if(CH_SEL!==k) CH_FRES=null;
      CH_SEL=k;
      if(k[0]==='h') chRenderTurn(ti);else chRenderList();
      const row=L.querySelector(`[data-chk="${k}"]`);
      if(row) L.scrollTop+=row.getBoundingClientRect().top-L.getBoundingClientRect().top-y;
    }
    chSelect(k, false);
    try{ $("chlist").focus({preventScroll:true}); }catch(err){}
  }
}

// 挂点全挂在卡片自己身上:卡片里的点击和按键先到这里,不和两个 document 级监听抢。
// Esc 不在这里接:它走页面级的那一条(navigation.js 的 handleEscape),焦点在卡片外也能退出。
function startConvoChain(){
  const box=$("chbox");
  if(!box) return;
  chMountFind();
  box.addEventListener("click", e=>{
    try{ chClick(e); }catch(err){ $("chnote").textContent="操作失败:"+err.message; }
  });
  // 菜单的 toggle 不冒泡,在捕获阶段接。开第二个菜单时第一个的「关」可能晚到,只清自己那一个。
  box.addEventListener("toggle", e=>{
    const t=e.target, open=e.newState==="open";
    if(t.id==="ch-more") CH_MOREOPEN=open;
    const u=t.dataset && t.dataset.chfkFor;
    if(u!=null){ if(open) CH_FKOPEN=u; else if(CH_FKOPEN===u) CH_FKOPEN=null; }
  }, true);
  box.addEventListener("input", e=>{
    if(e.target.id==="chfindq"){ chFindApply(e.target.value); return; }
    if(e.target.id!=="chsubq") return;
    CH_SUBQ=e.target.value;
    const sel=$("chsubs");
    if(sel) sel.innerHTML=chSubOptions(CH_SUBQ);
  });
  box.addEventListener("change", e=>{
    if(e.target.id==="chtools"){ CH_XT=e.target.checked; chSavePrefs(); return; }
    if(e.target.id==="chthink"){ CH_XK=e.target.checked; chSavePrefs(); return; }
    if(e.target.id==="chsubs" && e.target.value) chOpenSub(e.target.value);
  });
  box.addEventListener("keydown", e=>{
    if(e.ctrlKey || e.metaKey || e.altKey) return;
    const t=e.target;
    if(t.id==="chfindq" && e.key==="Enter" && !e.isComposing && !e.repeat){
      e.preventDefault(); chFindNext(); return;
    }
    if(t.id==="chsubq" && e.key==="Enter" && !e.isComposing && !e.repeat){
      const id=chSubFirst(t.value);
      if(id){ e.preventDefault(); chOpenSub(id); }
      return;
    }
    // 方向键和 Enter / 空格只在焦点就在链表上时归链表;落在卡片里的按钮、输入框上时归它们自己。
    if(t!==$("chlist")) return;
    if(chKey(e)){ e.preventDefault(); e.stopPropagation(); }
  });
}
