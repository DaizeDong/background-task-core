// Classic script module; loaded in app.js dependency order.
let SYS=null;
async function loadSys(){
  // 第一次读还没回来时先把「正在读取」画出来;刷新时保留上一次的数,不闪回空白。
  if(!SYS) renderSys();
  try{ SYS=await api("/api/sys"); }catch(e){ SYS={error:e.message}; }
  renderSys();
  updateBadges();
}
  // 会话转录的体积和文件数已从这里删掉:会话那一屏在报同一个目录,而两处用的是两套算法
  // (这里递归数目录下所有文件、有 60000 上限;那边只数解析成功的转录),数字必然对不上,
  // 而两处都不说自己是哪种口径。**同一个问题有两个都自称权威的答案**,比少显示一次糟。
  // 留下的是会话那边那份,它数的正是那个面板要谈的东西。
function renderSys(){
  const el=$("mt-sys"); if(!el) return;
  renderStorageAlert();
  // 读取中不能是一块空白:空白读起来像坏了或者真的什么都没有,而这一张卡恰恰装着这一页最急的那个数。
  if(!SYS){ el.innerHTML=loadingBlock("磁盘用量"); return; }
  if(SYS.error){ el.innerHTML=errorBlock("磁盘用量",SYS.error); return; }
  const d=SYS.disk||{}, pc=SYS.pluginCache||{}, se=SYS.sessions||{};
  const sz=o=>o&&o.size?kb(o.size.bytes)+(o.size.partial?"+":""):"-";
  let h=`<div class="mt-t">磁盘 <b>${d.usedPct!=null?d.usedPct+"%":"-"}</b>
      <span class="sub">剩 ${d.free!=null?(d.free/1073741824).toFixed(0)+"G":"-"}</span></div>
    ${d.usedPct==null
      ? `<div class="mt-note">${esc(d.reason||"磁盘没量到")}</div>`
      : `<div class="mt-meter"><i style="width:${d.usedPct}%;background:${
          `var(--${toneOf(d.verdict)})`}"></i></div>`}`;
  h+=`<div class="mt-rows">`;
  h+=pc.available
    ? `<div class="mt-r"><span class="n">插件缓存</span><span class="c">${sz(pc)}</span>
       ${pc.deletableCount?`<button class="mini danger" data-mt="clean.tempgit" data-name="-"
          title="删除 ${pc.deletableCount} 个废弃克隆暂存目录，先确认再执行"><svg class="ic" aria-hidden="true"><use
          href="#i-trash"/></svg>清理 ${pc.deletableCount} 个临时目录</button>`:""}</div>`
    : `<div class="mt-r"><span class="n">插件缓存</span><span class="c">${
        esc(pc.reason||"未检查")}</span></div>`;
  // partial 要说出来:一个数了一半却报确定数字的体积比不报还糟。
  // ⚠ 这句提示原来把 partial 一律解释成「数到上限」,而 partial 现在还有第二个来源:
  // 扫描过程中读不动的子树 / stat 不了的文件。两者的后果不同 ——
  // 撞上限是「太大了没数完」,读不动是「有一块我根本没看见」,而后者更该让人去查。
  const errN = (pc.size&&pc.size.errors||0)+(se.size&&se.size.errors||0);
  if(errN)
    h+=`<div class="mt-r"><span class="n" style="color:var(--warn)">有 ${errN} 处读取失败（权限、路径过长或文件已消失），体积和数量未统计完整</span></div>`;
  else if((pc.size&&pc.size.partial)||(se.size&&se.size.partial))
    h+=`<div class="mt-r"><span class="n" style="color:var(--warn)">扫描达到上限，带 + 的体积仍有未统计部分</span></div>`;
  // 残留数自己也可能没数成。「这里很干净」和「我根本没数成」不能都渲染成 0。
  if(pc.leftoverPartial)
    h+=`<div class="mt-r"><span class="n" style="color:var(--warn)">残留目录没扫完:${
        esc((pc.leftoverErrors||[]).join(" · ")||"扫描失败")}</span></div>`;
  h+=`</div>`;
  el.innerHTML=h;
}

// 磁盘告警横幅。磁盘快满是这一页最急的事,原来只是 Codex 存储下面一张小卡里的一根红条,
// 没有标题说出来。判定不是 ok 时在页顶整行写出「磁盘已用 95.5%，剩 43G」,并给一个跳到清理列表的按钮,
// 那份列表默认按体积从大到小排,最大的几份就在最上面。读取中、读不到、判定正常时都不显示。
function renderStorageAlert(){
  const box=$("storage-alert"); if(!box) return;
  const d=SYS && !SYS.error && SYS.disk || {};
  const tone=d.verdict && d.verdict.tone;
  if(d.usedPct==null || !tone || tone==="ok" || tone==="idle"){ box.hidden=true; box.innerHTML=""; return; }
  const free=d.free!=null?(d.free/1073741824).toFixed(0)+"G":"未知";
  box.className="storage-alert "+(tone==="bad"?"bad":"warn");
  box.innerHTML=`<span><b>磁盘已用 ${esc(d.usedPct)}%，剩 ${esc(free)}</b>${tone==="bad"?"，已接近写满":"，余量偏少"}</span>
    <button type="button" class="mini" data-goto="#cxbox" data-cx-largest title="跳到下面的会话存储清理，按体积从大到小排">查看最大的会话文件</button>`;
  box.hidden=false;
}

// 清理插件缓存里的废弃克隆暂存目录是这一页唯一一个一点就删的动作,所以先问一句。
// 删哪些由后端按名称形状和年龄自己选,这里只把它报上来的数目和最老的几个名字摆出来。
// 这些目录各自多大没有统计:框里写的是整个插件缓存的体积,并明说是「合计」,不冒充要删的那部分。
async function confirmTempgitCleanup(){
  const pc=(SYS && SYS.pluginCache) || {};
  const doomed=(pc.leftovers || []).filter(item=>item.deletable);
  const age=h=>h==null?"时间未记录":h<24?Math.round(h)+" 小时前":Math.round(h/24)+" 天前";
  const size=pc.size && pc.size.bytes!=null?kb(pc.size.bytes)+(pc.size.partial?"+":""):"未统计";
  return askConfirm({title:`清理 ${pc.deletableCount ?? doomed.length} 个临时目录`,
    body:`由后端按名称形状和年龄选定，删除后无法恢复。插件缓存合计 ${size}（这些目录各自的大小没有统计）。`,
    items:doomed.slice(0,8).map(item=>({text:item.name,note:"修改于 "+age(item.ageHours)})),
    more:doomed.length>8?`…还有 ${doomed.length-8} 个`:"",confirmLabel:"清理",danger:true});
}

// 记忆池那一栏。两条硬上限是护栏不是建议:超了尾部条目会在下一次会话里静默消失,
// 不报错、不记日志,所以这里画成有刻度的条而不是一个数字。
// 第二套 agent 的体征。单独一次 fetch:它要走一遍目录树,不该拖住别的面板。
let MEM=null;
async function loadMem(){
  try{ MEM=await api("/api/mem"); }catch(e){ MEM={error:e.message}; }
  renderMem();
  updateBadges();
}
function renderMem(){
  const el=$("mt-memory"); if(!el||!MEM) return;
  if(MEM.error){ memNote("读取失败");
    el.innerHTML=`<div class="warn-line">记忆池读取失败:${esc(MEM.error)}</div>`; return; }
  if(!MEM.available){ memNote("未检查");
    el.innerHTML=`<div class="warn-line">${esc(MEM.reason)}</div>`; return; }
  const bar=(pct,cur,max,lab,verdict)=>{
    if(pct==null) return `<div class="mt-r"><span class="n">${lab}</span><span class="c">未读到</span></div>`;
    // ⚠ Math.min 把 100% 和 130% 钳成同一个宽度,于是「刚好满」和「已经爆了」
    // 画出来一模一样。而超硬上限的后果是尾部条目在下一次会话里静默消失、
    // 不报错不记日志 —— 恰恰是最不该只靠一根满条去说的事。
    // 所以超限时条右端长出一截斜纹、整条描红,并把超了多少个直接写出来。
    const over = verdict && verdict.overBy || 0;
    return `<div class="mt-r"><span class="n">${lab}</span><span class="c${over?" over":""}">${
        cur}/${max}${over?` · 超 ${over}`:""}</span></div>
      <div class="mt-meter${over?" over":""}"><i style="width:${Math.min(pct,100).toFixed(1)}%;background:${
        `var(--${toneOf(verdict)})`}"></i></div>`;
  };
  // 三类问题各自摊开成一组可点的名字,而不是三行「标签 : 数字」。
  // ⚠ 空要说成「数出来的空」。后端那三个列表都可能合法地是空的,而空在这里是好消息 ——
  // 但它必须是数出来的空,不是没数。所以零态也各自出一行,不合并成一句「一致」:
  // 一句笼统的绿色结论盖住的是「这三项里哪一项其实没查」。
  // 名字是按钮:点一下复制这条记忆的名字(拿去改索引或打开文件)。一组上百个时先只摊前 12 个,
  // 「展开全部 N」再摊开;整组名单也能一次复制走。
  const grp=(key,lab,names,tip,extra)=>{
    const n=names.length, open=MEM_OPEN.has(key), shown=open?names:names.slice(0,MEM_CHIP_LIMIT);
    return `<div class="mem-grp${n?" hit":""}">
      <div class="mem-gh"><span class="lb" title="${esc(tip)}">${lab}</span>
        <b class="${n?"warn":"ok"}">${n}</b>${extra?`<span class="ex">${extra}</span>`:""}
        ${n?`<button type="button" class="link-button mem-copy-all" data-mem-copy-all="${esc(key)}" title="把这一组 ${n} 个名字按行复制到剪贴板">复制全部名单</button>`:""}</div>
      ${n?`<div class="mem-chips">${shown.map(s=>
          `<button type="button" class="mem-chip" data-mem-copy="${esc(s.name)}" title="${esc((s.tip||s.name)+"。点一下复制名字")}">${esc(s.name)}</button>`).join("")}${
          n>MEM_CHIP_LIMIT?`<button type="button" class="link-button mem-more" data-mem-more="${esc(key)}" aria-expanded="${open}">${open?"收起":`展开全部 ${n}`}</button>`:""}</div>`:""}
    </div>`;
  };
  const orph=MEM.orphanLinks.map(o=>({name:o.name,
    tip:`${o.name} 不在热层也不在冷层。指过去的是: ${(o.from||[]).join("、")}`}));
  const unre=MEM.unreachable.map(s=>({name:s,tip:`${s} 在池子里,但索引里没有任何一行指向它`}));
  const dang=MEM.danglingIndex.map(s=>({name:s,tip:`索引里有一行指向 ${s},而它既不在热层也不在冷层`}));
  MEM_GROUPS={orphan:orph.map(x=>x.name),unreachable:unre.map(x=>x.name),dangling:dang.map(x=>x.name)};

  const maxB=Math.max(1,...MEM.biggest.map(b=>b.bytes||0));
  el.innerHTML=`<div class="mem-top">
      <div class="mem-counts">
        <span class="kv"><b>${MEM.live}</b>常用记忆</span>
        <span class="kv"><b>${MEM.cold}</b>归档记忆</span>
      </div>
      <div class="mem-meters">
        ${bar(MEM.linePct,MEM.indexLines,MEM.hardLines,"索引行数",MEM.lineVerdict)}
        ${bar(MEM.bytePct,MEM.indexBytes,MEM.hardBytes,"索引字节",MEM.byteVerdict)}
      </div>
    </div>
    ${MEM.indexReason?`<div class="warn-line">${esc(MEM.indexReason)}</div>`:""}
    <div class="mem-grps">
      ${grp("orphan","记忆中的失效链接",orph,"某条记忆里的 [[链接]] 指向一个既不在热层也不在冷层的名字。悬停看是从哪几条指过去的")}
      ${grp("unreachable","未加入索引的记忆",unre,"记忆文件存在，但 MEMORY.md 中没有对应链接")}
      ${grp("dangling","索引中的失效链接",dang,"索引里有一行,而它指的文件不在")}
    </div>
    <div class="mem-big"><div class="mem-gh"><span class="lb">较大的记忆文件</span>
      <b>${MEM.biggest.length}</b><span class="ex">个，按大小排列</span></div>`
    + MEM.biggest.slice(0,10).map(b=>`<div class="mt-r bar">
        <span class="n" title="${esc(b.slug)}">${esc(b.slug)}</span><span></span>
        <span class="ub" aria-hidden="true"><i style="width:${
          Math.round((b.bytes||0)/maxB*100)}%"></i></span>
        <span class="c">${kb(b.bytes)}</span>
        ${MEM.archiverConfigured?ibtn("i-archive","归档记忆文件，索引保留链接",
          `data-mt="memory.archive" data-name="${esc(b.slug)}"`):""}</div>`).join("")
    + `</div>`;
  memNote("");
  if(typeof renderResourceAnchors==="function") renderResourceAnchors();
}

// 一组问题名字默认只摊前 12 个;哪几组被人展开过记在这里,重画时保持展开。
const MEM_CHIP_LIMIT=12, MEM_OPEN=new Set();
let MEM_GROUPS={};
// 复制到剪贴板。非安全上下文里没有剪贴板,那时把文字放进提示条里让人自己选:
// 一个静默失败的复制会让人粘出上一次的东西。
function memCopy(text,done){
  const fail=()=>toast("无法访问剪贴板，内容是："+text,"warn");
  if(typeof navigator!=="undefined" && navigator.clipboard && window.isSecureContext)
    navigator.clipboard.writeText(text).then(()=>toast(done,"ok"),fail);
  else fail();
}
function startMemory(){
  const box=$("mt-memory"); if(!box) return;
  box.addEventListener("click",event=>{
    const chip=event.target.closest("[data-mem-copy]");
    if(chip){ memCopy(chip.dataset.memCopy,`已复制 ${chip.dataset.memCopy}`); return; }
    const all=event.target.closest("[data-mem-copy-all]");
    if(all){ const names=MEM_GROUPS[all.dataset.memCopyAll]||[];
      if(names.length) memCopy(names.join("\n"),`已复制 ${names.length} 个名字`); return; }
    const more=event.target.closest("[data-mem-more]");
    if(more){ const key=more.dataset.memMore;
      if(MEM_OPEN.has(key)) MEM_OPEN.delete(key); else MEM_OPEN.add(key);
      renderMem(); box.querySelector(`[data-mem-more="${key}"]`)?.focus(); }
  });
}

// 卡头那行小字。读不到时它必须说出来 —— 卡片里那块空白自己不会解释自己。
function memNote(t){ const n=$("memnote"); if(n) n.textContent=t; }

// 退役。它同时改三处登记,所以流程是:先拉一次只读计划给人看,再问原因,最后才执行。
// 顺序是刻意的 : 一个先问原因再告诉你要改什么的流程,等于让人在不知道后果时下决定。
