// Classic script module; loaded in app.js dependency order.
let SYS=null;
async function loadSys(){
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
  if(!SYS){ el.innerHTML=""; return; }
  if(SYS.error){ el.innerHTML=`<div class="mt-note">磁盘读取失败:${esc(SYS.error)}</div>`; return; }
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
          title="删除 ${pc.deletableCount} 个废弃克隆暂存目录"
          aria-label="删除 ${pc.deletableCount} 个废弃克隆暂存目录"><svg class="ic"><use
          href="#i-trash"/></svg>${pc.deletableCount}</button>`:""}</div>`
    : `<div class="mt-r"><span class="n">插件缓存</span><span class="c">${
        esc(pc.reason||"未检查")}</span></div>`;
  // partial 要说出来:一个数了一半却报确定数字的体积比不报还糟。
  // ⚠ 这句提示原来把 partial 一律解释成「数到上限」,而 partial 现在还有第二个来源:
  // 扫描过程中读不动的子树 / stat 不了的文件。两者的后果不同 ——
  // 撞上限是「太大了没数完」,读不动是「有一块我根本没看见」,而后者更该让人去查。
  const errN = (pc.size&&pc.size.errors||0)+(se.size&&se.size.errors||0);
  if(errN)
    h+=`<div class="mt-r"><span class="n" style="color:var(--warn)">有 ${errN} 处扫不动(权限/路径过长/中途消失),体积和残留数都是偏小的</span></div>`;
  else if((pc.size&&pc.size.partial)||(se.size&&se.size.partial))
    h+=`<div class="mt-r"><span class="n" style="color:var(--warn)">体积只数到上限,带 +</span></div>`;
  // 残留数自己也可能没数成。「这里很干净」和「我根本没数成」不能都渲染成 0。
  if(pc.leftoverPartial)
    h+=`<div class="mt-r"><span class="n" style="color:var(--warn)">残留目录没扫完:${
        esc((pc.leftoverErrors||[]).join(" · ")||"扫描失败")}</span></div>`;
  h+=`</div>`;
  el.innerHTML=h;
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
  const grp=(lab,names,tip,extra)=>{
    const n=names.length;
    return `<div class="mem-grp${n?" hit":""}">
      <div class="mem-gh"><span class="lb" title="${esc(tip)}">${lab}</span>
        <b class="${n?"warn":"ok"}">${n}</b>${extra?`<span class="ex">${extra}</span>`:""}</div>
      ${n?`<div class="mem-chips">${names.map(s=>
          `<span class="chip" title="${esc(s.tip||s.name)}">${esc(s.name)}</span>`).join("")}</div>`:""}
    </div>`;
  };
  const orph=MEM.orphanLinks.map(o=>({name:o.name,
    tip:`${o.name} 不在热层也不在冷层。指过去的是: ${(o.from||[]).join("、")}`}));
  const unre=MEM.unreachable.map(s=>({name:s,tip:`${s} 在池子里,但索引里没有任何一行指向它`}));
  const dang=MEM.danglingIndex.map(s=>({name:s,tip:`索引里有一行指向 ${s},而它既不在热层也不在冷层`}));

  const maxB=Math.max(1,...MEM.biggest.map(b=>b.bytes||0));
  el.innerHTML=`<div class="mem-top">
      <div class="mem-counts">
        <span class="kv"><b>${MEM.live}</b>热层</span>
        <span class="kv"><b>${MEM.cold}</b>冷层</span>
      </div>
      <div class="mem-meters">
        ${bar(MEM.linePct,MEM.indexLines,MEM.hardLines,"索引行数",MEM.lineVerdict)}
        ${bar(MEM.bytePct,MEM.indexBytes,MEM.hardBytes,"索引字节",MEM.byteVerdict)}
      </div>
    </div>
    ${MEM.indexReason?`<div class="warn-line">${esc(MEM.indexReason)}</div>`:""}
    <div class="mem-grps">
      ${grp("孤儿链接",orph,"某条记忆里的 [[链接]] 指向一个既不在热层也不在冷层的名字。悬停看是从哪几条指过去的")}
      ${grp("索引够不到",unre,"文件在池子里,但 MEMORY.md 里没有任何一行指向它 —— 下次会话不会加载到")}
      ${grp("索引指向空",dang,"索引里有一行,而它指的文件不在")}
    </div>
    <div class="mem-big"><div class="mem-gh"><span class="lb">最占地方的</span>
      <b>${MEM.biggest.length}</b><span class="ex">条,按字节</span></div>`
    + MEM.biggest.slice(0,10).map(b=>`<div class="mt-r bar">
        <span class="n" title="${esc(b.slug)}">${esc(b.slug)}</span><span></span>
        <span class="ub" aria-hidden="true"><i style="width:${
          Math.round((b.bytes||0)/maxB*100)}%"></i></span>
        <span class="c">${kb(b.bytes)}</span>
        ${MEM.archiverConfigured?ibtn("i-archive","移进冷层,索引里只留一行指路",
          `data-mt="memory.archive" data-name="${esc(b.slug)}"`,"danger"):""}</div>`).join("")
    + `</div>`;
  memNote("");
}

// 卡头那行小字。读不到时它必须说出来 —— 卡片里那块空白自己不会解释自己。
function memNote(t){ const n=$("memnote"); if(n) n.textContent=t; }

// 退役。它同时改三处登记,所以流程是:先拉一次只读计划给人看,再问原因,最后才执行。
// 顺序是刻意的 : 一个先问原因再告诉你要改什么的流程,等于让人在不知道后果时下决定。
