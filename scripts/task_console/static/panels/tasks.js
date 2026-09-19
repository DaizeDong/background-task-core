// Classic script module; loaded in app.js dependency order.

let DATA=null, ROWS=[], VIEW=[], cur=0, sel=new Set(), sortKey="cat", asc=true, busy=false;
// 调度卫生那六列几乎不变,平铺在每天都动的列旁边只会稀释后者。默认收起,一键展开。
// 收起不是隐藏:按钮上一直印着它藏了几列,而且状态记在 localStorage 里,
// 一个记不住的开关每次都要重按,等于没有。
const HYGIENE = ["catchup","retries","timeout","artifact","inAllow","inHealth"];
let HYG_OPEN = false;
try{ HYG_OPEN = localStorage.getItem("tc.hyg") === "1"; }catch(e){}
const shownCols = () => HYG_OPEN ? C : C.filter(c => HYGIENE.indexOf(c[0]) < 0);
// 时间轴可视窗口,单位分钟。整天是 [0,1440];缩放和拖动只改这两个数,所有位置都由它们算出来。
let tlFrom=0, tlTo=1440;
const TL_MIN_SPAN=5;    // 最小窗口 5 分钟。实测深度缩放时一帧 1.1ms,成本由 25 行固定的
                        // 行名和轨道 div 主导而不是标记数,所以收窄下限不额外花钱。
const sev = r => r.issues.some(i=>i[0]==="bad")?"bad":r.issues.some(i=>i[0]==="warn")?"warn":"";
const mins = t => { const p=String(t).split(":"); return (+p[0])*60+(+p[1]); };

async function load(){
  try{
    DATA=await api("/api/tasks");
    if(DATA.error) throw new Error(DATA.error);
    ROWS=[]; for(const g of DATA.groups) for(const r of g.rows) ROWS.push(Object.assign({cat:g.cat},r));
    const s=$("cat"), keep=s.value;
    s.innerHTML='<option value="">全部大类</option>'+DATA.groups.map(g=>`<option>${esc(g.cat)}</option>`).join("");
    s.value=keep;
    render();
  }catch(e){ $("tbl").innerHTML=`<tbody><tr><td style="color:var(--bad);padding:10px">读取失败:${esc(e.message)}</td></tr></tbody>`; }
}

// 把渲染合并到一帧里。之前滚轮和拖动都是每个事件同步渲染一次,而浏览器一次拖动可以
// 派发出远多于一帧的 pointermove,于是同一帧内白渲染好几遍。
let tlPending=false;
function scheduleTL(){
  if(tlPending) return;
  tlPending=true;
  requestAnimationFrame(()=>{ tlPending=false; if(DATA) renderTL(); });
}
function tlPos(m){ return (m - tlFrom) / (tlTo - tlFrom) * 100; }
function tlTicks(){
  // 刻度密度跟着窗口走。整天时每 2 小时一格,放大到半小时窗口时每 5 分钟一格,
  // 否则要么挤成一片黑要么整条轴上一个标都没有。
  const span = tlTo - tlFrom;
  const step = span > 720 ? 120 : span > 240 ? 60 : span > 120 ? 30 : span > 40 ? 10 : 5;
  const out = [];
  for (let m = Math.ceil(tlFrom/step)*step; m <= tlTo; m += step){
    const h = String(Math.floor(m/60)%24).padStart(2,"0"), mi = String(m%60).padStart(2,"0");
    out.push(`<i style="left:${tlPos(m)}%">${step>=60?h:h+":"+mi}</i>`);
  }
  return out.join("");
}
function renderTL(){
  const T=DATA.timeline;
  if(!T||!T.rows||!T.rows.length){ $("tlbox").hidden=true; return; }
  $("tlbox").hidden=false;
  const rlDown = !(DATA.runlog && DATA.runlog.available);
  // ⚠ 这里原来把三样东西拼成一个字符串:日期与行数(体征)、T.note(通用说明,
  // 读一次就够)、以及「运行日志不可用」(**真实状态,必须有人看见**)。
  // 一起折叠就等于把那条告警藏进灰字堆;所以先拆开,状态单独升成警告条。
  $("tlnote").textContent=`${T.date} 现在 ${T.now} · ${T.rows.length} 行`;
  const help=$("tlhelp");
  if(help){ help.textContent="说明"; help.dataset.full=T.note||""; help.title=T.note||""; }
  const w=$("tlwarn");
  if(w){ w.hidden=!rlDown;
    w.textContent="运行日志不可用,本图只有计划、没有实际:每行都没有实际运行标记不代表它们没跑。"; }
  const fmt=m=>`${String(Math.floor(m/60)).padStart(2,"0")}:${String(Math.round(m)%60).padStart(2,"0")}`;
  $("tlrange").textContent=`${fmt(tlFrom)}-${fmt(Math.min(tlTo,1439))}`;
  const nowM=mins(T.now);
  // 视窗外的标记直接不渲染。让它们留在 DOM 里靠 overflow 裁掉,在放大到几十分钟时
  // 等于每行仍要摆几百个绝对定位元素,滚动会明显掉帧。
  const vis=m=>m>=tlFrom-1&&m<=tlTo+1;
  const rows=T.rows.map(r=>{
    const marks=r.points.filter(p=>vis(mins(p))).map(p=>
      `<i class="tlpt${mins(p)<=nowM?" done":""}" style="left:${tlPos(mins(p))}%" title="${esc(r.name)} 计划 ${p}"></i>`).join("");
    const spans=r.spans.map(sp=>{
      const a=Math.max(mins(sp.from),tlFrom), b=Math.min(mins(sp.to),tlTo);
      if(b<=a) return "";
      // truncated 后端算了、传了、也测了,而这里从来没读过 ——
      // 于是一个十秒级的任务会在 tooltip 里声称「00:00-13:53 共 5000 次」,
      // 而真实是全天 8640 次。**一个数了一半却报出确定数字的结果,比不报还糟。**
      const cnt = sp.truncated ? `至少 ${sp.count} 次(展开撞上上限,没数完)` : `共 ${sp.count} 次`;
      return `<i class="tlspan${sp.truncated?" trunc":""}" style="left:${tlPos(a)}%;width:${Math.max(tlPos(b)-tlPos(a),0.4)}%" title="${esc(r.name)} ${sp.from}-${sp.to} 每 ${esc(sp.every)} ${cnt}"></i>`;
    }).join("");
    const acts=(r.actual||[]).filter(a=>vis(mins(a))).map(a=>
      `<i class="tlact" style="left:${tlPos(mins(a))}%" title="${esc(r.name)} 实际运行 ${a}"></i>`).join("");
    const evt=(r.eventDriven.length&&!r.points.length&&!r.spans.length)
      ? `<span class="tlevt" title="${esc(r.eventDriven.join("/"))} 触发,无固定时刻"
          ><b class="tlbadge">${esc(r.eventDriven.join("/"))}</b></span>`:"";
    // 认不出的触发器类型。后端一直在收集它,而这一行以前**整个不渲染**,
    // 连带那些只有认不出的触发器的任务在今日时间轴上一行都没有 ——
    // 屏幕表现与「这个任务今天本来就不该跑」逐像素相同。
    // 说「我不认识」比装作没有强:前者能被人去查,后者不能。
    const unk=(r.unknownTriggers&&r.unknownTriggers.length&&!r.points.length&&!r.spans.length)
      ? `<span class="tlevt warnish" title="触发器类型 ${esc(r.unknownTriggers.join("/"))} 认不出,今日时刻算不出来"
          ><b class="tlbadge">? ${esc(r.unknownTriggers.join("/"))}</b></span>`:"";
    const nowBar=vis(nowM)?`<i class="tlnow" style="left:${tlPos(nowM)}%"></i>`:"";
    // ⚠ 这里原来还拼了 `r.desc`,而 timeline.build 从不产出这个字段(它拼的 row 只有
    // name/cat/points/spans/eventDriven/unknownTriggers/actual/sk)。于是这个三元
    // 永远走 false 分支 —— 一段看起来在显示说明、实际什么都不显示的代码。
    // 说明确实存在,但它在任务表那条通路上(DATA.groups 的行里),不在这里。
    // 要么去取过来,要么别装作有:现在是后者,并写明它在哪。
    return `<div class="tlrow" data-task="${esc(r.name)}"><div class="tlname" title="${esc(r.name)}">${esc(r.name)}</div>
      <div class="tltrack">${spans}${marks}${acts}${evt}${unk}${nowBar}</div></div>`;
  }).join("");
  $("tl").innerHTML=`<div class="hours">${tlTicks()}</div>${rows}`;
}
function tlZoom(factor, anchorPct){
  const span=tlTo-tlFrom;
  let ns=Math.min(1440, Math.max(TL_MIN_SPAN, span*factor));
  const anchorM=tlFrom+span*anchorPct;         // 以指针所在时刻为锚,缩放后它仍在指针下
  let nf=anchorM-ns*anchorPct;
  if(nf<0) nf=0;
  if(nf+ns>1440) nf=1440-ns;
  tlFrom=nf; tlTo=nf+ns;
  scheduleTL();
}
function tlPan(dxPct){
  const span=tlTo-tlFrom;
  let nf=tlFrom-span*dxPct;
  nf=Math.max(0,Math.min(1440-span,nf));
  tlFrom=nf; tlTo=nf+span;
  scheduleTL();
}
function tlReset(){ tlFrom=0; tlTo=1440; scheduleTL(); }

// 新鲜度灯板。状态由后端现算(freshness.py),这里只负责把它画出来,不在前端重新判定 :
// 两处各判一次必然漂移,而漂移的那一方看起来同样理直气壮。
const FR_LABEL={up:"正常",running:"运行中",grace:"宽限",down:"失败",never:"未跑过",
                paused:"停用",unknown:"未查成"};
const FR_ORDER=["down","never","grace","unknown","paused","running","up"];
const FR_COLOR={up:"var(--ok)",running:"var(--cyan)",grace:"var(--warn)",down:"var(--bad)",
                paused:"var(--idle)",never:"var(--amber)",unknown:"var(--faint)"};

function renderFresh(){
  const F=DATA.freshness;
  if(!F){ $("frbox").style.display="none"; return; }
  $("frbox").style.display="";
  const S=F.summary||{}, ts=F.tasks||[];
  // 没有清单就说没有清单。画一块空的绿板等于用「没检查」冒充「没问题」。
  if(F.reason){
    $("frstack").innerHTML="";
    // 这里原来写 "0%"。控制台是轮询刷新的,而这个分支只设 textContent 不碰颜色和 tooltip,
    // 所以上一轮覆盖率 >=80% 染成绿色之后清单被删,这一轮会渲染出一个**绿色的 0%**
    // 外加一句陈旧的「12/12 个任务拿到了判据」。三样都要一起重置。
    $("frcov").textContent="-";
    $("frcov").style.color=""; $("frcov").title="";
    $("frkey").innerHTML=""; $("frboard").innerHTML="";
    $("frlist").innerHTML=`<div class="fr-off">${esc(F.reason)}</div>`;
    return;
  }
  const counts=S.counts||{}, total=S.total||0;
  $("frstack").innerHTML=FR_ORDER.filter(k=>counts[k]).map(k=>
    `<i style="width:${(counts[k]/total*100).toFixed(2)}%;background:${FR_COLOR[k]}" `
    +`title="${FR_LABEL[k]} ${counts[k]}"></i>`).join("");

  const cov=Math.round((S.coverage||0)*100);
  const covEl=$("frcov");
  covEl.textContent=cov+"%";
  // 覆盖率本身就是判据的体检:一个被喂了空的检查器,打印的绿色和真没查出问题的一模一样。
  covEl.style.color = `var(--${toneOf(S.coverageVerdict)})`;
  covEl.title=`${S.judged||0}/${total} 个任务拿到了判据`;

  $("frkey").innerHTML=FR_ORDER.filter(k=>counts[k]).map(k=>
    `<span><i class="fr-c s-${k}" style="width:9px;height:9px"></i>${FR_LABEL[k]} ${counts[k]}</span>`
  ).join("");

  // 按 FR_ORDER 排:坏的聚到左上角。乱序的灯板等于把「有 5 个红的」这个事实
  // 摊平成「你自己去 40 格里数」,而这块板子存在的理由就是一眼看出坏了几个。
  // 排序只改显示顺序,data-fr 仍是任务名,点击与 title 的路径一个字没动。
  const tsSorted=ts.slice().sort((a,b)=>
    FR_ORDER.indexOf(a.state)-FR_ORDER.indexOf(b.state) || a.name.localeCompare(b.name));
  $("frboard").innerHTML=tsSorted.map(t=>{
    const why=(t.reasons||[]).join(" · ");
    // 加 tabindex 和 role:这一格是概览屏两个核心导航之一,原来只能用鼠标。
    // 折叠进同一个任务的几件事在灯板上是各自一格。它们的 data-fr 都指向那个任务
    // (点了跳过去是对的),但 title 必须说清是哪一件,否则同名的几格看起来像重复渲染。
    const who = t.check ? `${t.name} · ${t.check}` : t.name;
    return `<i class="fr-c s-${t.state}" data-fr="${esc(t.name)}" tabindex="0" role="button" `
      +`title="${esc(who)} : ${FR_LABEL[t.state]}${why?" : "+esc(why):""}"></i>`;
  }).join("");

  // 明细搬到了上面的「要人管的事」:那张清单列的就是这几条,一字不差。
  // 同一段文字在一屏里出现两次,读的人得先分辨这是两件事还是一件事。
  // 这块保留聚合视图(条形图、覆盖率、热力图),它回答的是「整体什么样」,清单回答「是哪几条」。
  // 但不能就这么让明细消失 : 留一行指路,否则下次有人会以为它坏了。
  // 这里的口径必须和「要人管的事」清单**逐字**一致,否则这句话会指着一张
  // 不包含那几条的清单报数。清单收的是 down / never / unknown 三种:
  // grace 是「还在宽限期内」,不算要人管;unknown 是「查不成」,算。
  const bad=ts.filter(t=>FR_ATT().indexOf(t.state) >= 0);
  $("frlist").innerHTML = bad.length
    ? `<div class="fr-off">${bad.length} 条明细在上面的「要人管的事」</div>`
    : `<div class="fr-none">全部产物新鲜</div>`;
}

// 维护面板。单独一次 fetch:它要跑 `claude plugin list`,几秒起步,不该拖住主表。
function relTime(s){
  const t=new Date(String(s).replace(" ","T"));
  if(isNaN(t)) return String(s).slice(5);   // 解析不了就照实回显原文,不编一个数出来
  const d=(t-Date.now())/36e5, a=Math.abs(d);
  const u=a<1?`${Math.round(a*60)}分`:a<48?`${a.toFixed(a<10?1:0)}小时`:`${(a/24).toFixed(0)}天`;
  return d<0?u+"前":u+"后";
}

function pc(v){ if(v==null) return `<td class="num u" title="未检查">-</td>`;
  const c=v>=95?"var(--ok)":v>=80?"var(--warn)":"var(--bad)";
  return `<td class="num"><span class="pcb"><i style="width:${
    Math.min(100,Math.max(0,v))}%;background:${c}"></i></span><span
    style="color:${c}">${v.toFixed(1)}</span></td>`; }
function renderScores(){
  $("scores").innerHTML=`<thead><tr><th>大类</th><th class="num">数</th><th class="num">健康%</th>
    <th class="num">备份%</th><th class="num">监控%</th><th class="num" title="本类里「没有任何 warn 项」的任务占比。注意它和上面那个按钮说的
「卫生六列」不是一回事:那六列包含备份与监控,而这个百分比不看这两项,却多算了两条电池规则。
一个大类可能六列全是 N 而这里是 100.0">无警告项%</th></tr></thead><tbody>`
    // 每一行挂上它自己的大类。之前这张表的格子有 cursor:pointer 却没有任何处理器,
    // 那是最糟的一种:它主动告诉你可以点,然后什么也不做。
    // 健康% 那一格单独出:它的分母和同一行的「数」不是一回事,而四列长得一模一样。
    // 分母不足时用未检查色阶,和 pc() 的 null 分支一致 : 一个 1 个样本的 100%
    // 和一个 40 个样本的 100% 不该长得一样。
    +(DATA.scores||[]).map(s=>{
      const hn = (s.healthN != null) ? s.healthN : s.n;
      const thin = hn < s.n;
      const hcell = (s.health == null)
        ? `<td class="num u" title="没有任何任务有观察记录">-</td>`
        : `<td class="num" title="${hn}/${s.n} 个任务有观察记录"${
            thin ? ' style="color:var(--faint)"' : ""}>${s.health}</td>`;
      return `<tr data-scat="${esc(s.cat)}" title="只看这一类"><td>${esc(s.cat)}</td>`
        + `<td class="num">${s.n}</td>${hcell}${pc(s.backup)}${pc(s.watched)}${pc(s.hygiene)}</tr>`;
    }).join("")
    +`</tbody>`;
}

const C=[
 // 原来这里是一个 14px 宽、显示 * 的格子:它让人以为点它能选中,点下去展开的却是明细,
 // 而 x / a 这两个选择键没有任何鼠标等价物 : 只用鼠标的人永远看不到那条批量操作条,
 // 也就用不到「全部运行 / 全部启用 / 全部停用」。这是本页唯一一处两种输入方式能力不对等的功能。
 ["selc","",r=>`<td style="width:20px"><input type="checkbox" class="selbox"
   data-selname="${esc(r.name)}"${sel.has(r.name)?" checked":""}
   aria-label="选中 ${esc(r.name)}"></td>`,()=>0],
 ["sl","状态",r=>`<td><span class="st ${r.sk}">${esc(r.sl)}</span></td>`,r=>r.sl],
 ["ops","操作",r=>{
   // 动作长在每一行上,而不是藏在展开态和键盘快捷键里。一个要先发现才能用的动作,
   // 对一个每天只瞥一眼的维护台来说等于不存在。
   const on=r.state!=="Disabled", run=r.state==="Running";
   // 五个动作五个图形。停用和启用刻意不是「同一个图形换个颜色」:
   // 它们是相反的操作,只靠颜色区分等于在最该分清的地方只留一条通道。
   const nm=`data-name="${esc(r.name)}"`;
   return `<td class="ops">`
     +ibtn("i-play","立即运行",`data-act="run" ${nm}`)
     +(run?ibtn("i-stop","停止",`data-act="stop" ${nm}`,"danger"):"")
     +ibtn("i-retire","退役:停用 + 退出备份名单 + 退出健康清单",
           `data-retire="${esc(r.name)}"`,"danger")
     +(on?ibtn("i-pause","停用",`data-act="disable" ${nm}`,"danger")
         :ibtn("i-on","启用",`data-act="enable" ${nm}`))
     +`</td>`;
 },r=>r.state],
 // 说明折进任务名的第二行。它们本来就是一体的「这是什么」,而分成两列的代价是
 // 说明只剩 280px、每行都被截成半句话(「每日 22:00 的配置备份总…」)。
 // 合并之后说明可用宽度涨到 340px,而且不再和任务名争抢。
 ["name","任务",r=>`<td class="nm" title="${esc(r.name)}">${esc(r.name)}`
   +(r.desc?`<span class="ds2" title="${esc(r.desc)}">${esc(r.desc)}</span>`:"")
   +`</td>`,r=>r.name],
 ["cat","大类",r=>`<td class="dim">${esc(r.cat)}</td>`,r=>r.cat],
 // 「说明」不再单独占一列 —— 它现在是任务名那一格的第二行。
 // 按说明排序这个能力一并没了,而那个能力没有实际用途;搜索仍然能搜到说明
 // (过滤框的 placeholder 写的就是「任务名 / 大类 / 说明」,那条路径没变)。
 // 健康% 旁边要能看出它是拿什么算出来的。判词表认不出来的那些进 other 桶,
 // 它只进分母不出现在任何地方:监控器换一种措辞之后,每一行会显示 0.0% 而
 // ok/bad/stale 全是 0,同一行里两个数字互相矛盾而没有任何字段说明观察去哪了。
 // 一格顶原来的四格。原来「健康% / 实跑 / 失败 / 陈旧」各占一列,42 行乘 4 列
 // 是 168 个数字,其中绝大多数是 100 / 0 / -,而真正要回答的问题只有一个:
 // 这一行有没有事。于是健康率画成一条按 ok/失败/陈旧 分段的微型条,数字留在旁边;
 // 失败、陈旧、实跑三列的精确值搬进展开行(见 detail 里的「观察」一条)。
 //
 // ⚠ 三种「不能长得一样」的状态在条上各有各的形状,不是各有各的颜色:
 //   没有观察记录 -> 虚线空框(不是一条 0 宽的条,那会读成「全坏」)
 //   样本不足     -> 整条降饱和 + 虚线外框
 //   判词认不出   -> 分母里留一段**背景色缺口**,把原来只在 tooltip 里的那个缺陷画出来
 ["health","体征",r=>{
   const h=r.hist||{}, v=h.health;
   const oth=h.other||0;
   const tip = v==null ? "没有观察记录"
     : `${h.ok}/${h.judged} 条观察判为正常`
       + (h.bad?` · 失败 ${h.bad}`:"") + (h.stale?` · 陈旧 ${h.stale}`:"")
       + (oth ? ` · ${oth} 条判词认不出来,它们只进了分母` : "");
   // 小样本降色。紧挨着的两处都做了这件事(实成功% 在 j<5 时降 faint,大类评分表在
   // healthN<n 时降色),唯独这一列没有:**一个 1 条观察的 100% 和一个 4000 条观察的
   // 100% 在屏幕上长得完全一样**,而分母只在 hover 的 tooltip 里。
   // 一个刚被监控器纳入、只轮询到一次的任务因此显示满格绿色。
   const thin = h.judged != null && h.judged < 5;
   const col = v==null?"var(--faint)"
     :thin?"var(--faint)"
     :oth&&oth>=h.judged*0.5?"var(--faint)"
     :v>=95?"var(--ok)":v>=80?"var(--warn)":"var(--bad)";
   // 样本太少时把分母印出来。降色只说「别太当真」,印出 n 才说清为什么。
   const suffix = oth ? "*" : (thin && v != null ? `<span class="m"> n=${h.judged}</span>` : "");
   if(v==null)
     return `<td class="num" title="${esc(tip)}"><span class="vit unk"></span><span
       class="u">-</span></td>`;
   // 分母用 judged,不是 ok+bad+stale:差出来的那一段就是「判词认不出」的那些,
   // 它们留成背景色的缺口 —— 那个缺陷原来只活在 tooltip 里。
   const tot=Math.max(h.judged||1,1), w=n=>(100*(n||0)/tot).toFixed(1)+"%";
   return `<td class="num" title="${esc(tip)}"><span class="vit${thin?" thin":""}"
       ><i class="k-ok" style="width:${w(h.ok)}"></i
       ><i class="k-bad" style="width:${w(h.bad)}"></i
       ><i class="k-st" style="width:${w(h.stale)}"></i></span><span
       style="color:${col}">${v}${suffix}</span></td>`;
 },r=>(r.hist&&r.hist.health!=null)?r.hist.health:-1],
 // 分母要说出来。judged 是动作返回码事件数,不是旁边那列的「实跑」(启动事件数):
 // 多动作任务每次运行写多条 201,分母大于实跑;rc 事件被日志滚动截断的任务分母小于实跑。
 // 一个 1 个样本的 100% 和一个 4000 个样本的 100% 不该长得一样。
 ["realOk","实成功%",r=>{
   const R2=r.runs||{}, v=R2.successRate, j=R2.judged, st=R2.starts;
   const thin = (j != null && j < 5) || (st != null && j != null && j < st * 0.5);
   const tip = v==null ? "运行日志里还没有这个任务的动作返回码"
     : `${R2.good}/${j} 个动作返回码判为成功` + (st!=null?` · 启动 ${st} 次`:"");
   const col = v==null||thin ? "var(--faint)" : v>=95?"var(--ok)":"var(--bad)";
   return `<td class="num" title="${esc(tip)}" style="color:${col}">${v==null?"-":v}</td>`;
 },r=>(r.runs&&r.runs.successRate!=null)?r.runs.successRate:-1],
 // 实跑 / 失败 / 陈旧 三列已经并进「体征」那一格的分段条,精确值在展开行的「观察」一条。
 // 它们留在表上时是三列几乎全 0 的数字,而三个标签每次都一样。
 ["triggers","触发",r=>`<td class="dim" title="${esc(r.triggers)}">${esc((r.triggers||"").slice(0,20))}</td>`,r=>r.triggers||""],
 // 84 个等宽时间戳(42 行两列),而读它们的目的基本只有「多久没跑了」「还有多久跑」。
 // 相对时间直接回答那个问题,绝对时刻进 title,一个都没丢。
 // ⚠ 「从未」和「-」保持两个不同的词:前者是确定没跑过,后者是没有下次计划。
 // 排序键仍取原始字符串,排序结果与改之前逐行一致。
 ["lastRun","上次",r=>`<td class="dim num" data-col="lastRun" title="${esc(r.lastRun||"从未")}">${
    r.lastRun?esc(relTime(r.lastRun)):'<span class="u">从未</span>'}</td>`,r=>r.lastRun||""],
 ["nextRun","下次",r=>`<td class="dim num" data-col="nextRun" title="${esc(r.nextRun||"没有下次计划")}">${
    r.nextRun?esc(relTime(r.nextRun)):'<span class="u">-</span>'}</td>`,r=>r.nextRun||""],
 ["catchup","补跑",r=>`<td class="${r.catchup?"y":"n"}">${r.catchup?"Y":"N"}</td>`,r=>r.catchup?1:0],
 ["retries","重试",r=>`<td class="num">${r.retries}</td>`,r=>r.retries],
 ["timeout","超时",r=>{const i=(r.timeout==="PT72H"||r.timeout==="PT0S");return `<td data-col="timeout" style="color:${i?"var(--warn)":"var(--dim)"}">${esc(r.timeout)}</td>`},r=>r.timeout||""],
 ["artifact","产物",r=>`<td class="${r.artifact?(r.cannotProve?"u":"y"):"u"}" title="${esc(r.artifact||"未声明")}">${r.artifact?(r.cannotProve?"弱":"Y"):"-"}</td>`,r=>r.artifact?(r.cannotProve?1:2):0],
 ["inAllow","备份",r=>{const u=!DATA.summary.allowChecked;return `<td class="${u?"u":r.inAllow?"y":"n"}">${u?"?":r.inAllow?"Y":"N"}</td>`},r=>r.inAllow?1:0],
 ["inHealth","监控",r=>`<td class="${r.inHealth?"y":r.elsewhere?"u":"n"}" title="${esc(r.elsewhere||"")}">${r.inHealth?"Y":r.elsewhere?"代":"N"}</td>`,r=>r.inHealth?1:0],
];

function detail(r){
  const tail=s=>esc(String(s==null?"":s).split("\\").pop().replace(/"$/,""));
  const rcs=r.runs?Object.keys(r.runs.rcs||{}).map(k=>k+"x"+r.runs.rcs[k]).join(", "):"";
  const dl=[
   ["说明",r.desc?esc(r.desc):'<span class="u">没有说明。补在分类配置的 taskDesc 里(路径见 README)</span>'],
   ["命令",`${tail(r.exec)} ${tail(r.args)}`],
   ["身份",`${esc(r.userId)} · ${esc(r.runLevel)} · ${esc(r.multi)}`],
   ["退出码",`${esc(r.rcHex)||"-"}${r.okCodes?` <span class="faint">声明 ${esc(r.okCodes)} 也算正常</span>`:""}`],
   ["产物",r.artifact?`${esc(r.artifact)} · ${esc(r.artifactMax)}h`:"未声明"],
   ["电池",`${r.refuseOnBattery?"用电池时拒绝启动":"电池可启动"} · ${r.stopOnBattery?"拔电源会被杀":"拔电源不杀"}`],
   // 「体征」那一格只画比例,精确值在这里。少了这一条,失败与陈旧的具体条数
   // 就只剩 tooltip 一个出口 —— 而 tooltip 是发现不了的。
   ["观察",r.hist?`正常 ${r.hist.ok} · 失败 ${r.hist.bad} · 陈旧 ${r.hist.stale}`
     +(r.hist.other?` · 判词认不出 ${r.hist.other}`:"")
     +` / 共 ${r.hist.judged} 条`:'<span class="u">没有观察记录</span>'],
   ["真实运行",r.runs?`启动 ${r.runs.starts} · 完成 ${r.runs.done} · 被杀 ${r.runs.killed} · 超时 ${r.runs.timedOut} · 启动失败 ${r.runs.failStart} · 返回码 ${rcs||"无"}${r.runs.okApplied?` (声明 ${r.runs.okApplied.join(",")} 也算成功)`:""}`:'<span class="u">运行日志里还没有记录</span>'],
  ].map(x=>`<dt>${x[0]}</dt><dd>${x[1]}</dd>`).join("");
  const iss=r.issues.length?`<ul class="iss">${r.issues.map(i=>`<li class="${i[0]}">${esc(i[1])}</li>`).join("")}</ul>`:"";
  // 展开态原来还有一组 启用/停用/立即运行/停止 按钮,是行内操作列的真子集(少一个退役)。
  // 而这个文件里早就写下过决定:「动作长在每一行上,而不是藏在展开态和键盘快捷键里」。
  // 动作提到行上之后,展开态那份没删,于是同一个按钮在同一屏出现两次,
  // 而其中一份还比另一份少一个动作 : 两份不完全一样的重复,比完全一样的更糟,
  // 因为人会以为差别是有意义的。
  return `<tr class="det"><td colspan="${shownCols().length}"><div class="det"><dl>${dl}</dl>${iss}</div></td></tr>`;
}

function render(){
  const S=DATA.summary;
  // 零态改色而不是隐藏。「0 个失败」和「这一项没采到」必须保持可分辨 ——
  // 藏起来之后它们都表现为「顶栏上没有这一段」。
  $("s-total").textContent=S.total;
  $("s-bad").textContent=S.bad; $("s-bad").className=S.bad?"bad":"zero";
  $("s-iss").textContent=S.issues; $("s-iss").className=S.issues?"warn":"zero";
  $("s-off").textContent=S.disabled; $("s-off").className=S.disabled?"":"zero";
  $("s-src").innerHTML=`<span class="k">数据</span>${esc(S.generated)}`
    +((DATA.history&&DATA.history.available)?` <span class="faint">观察${DATA.history.days.length}d</span>`:` <span class="faint">无观察</span>`)
    // 摄入器最后一次跑成没跑成,原来查出来了却在 server 里被丢掉:数据库存在但摄入器已经
    // 连续失败几周时,页面照常显示 available=true、热力图照画、健康% 仍是一个具体数字,
    // 而**没有任何一处告诉人这些数字最后一次更新是什么时候**。
    +(()=>{
       // lastIngest 是 {来源: {at, ok}} 的字典,不是一个时间串。第一版直接 String() 它,
       // 顶栏印出「最后摄入 [object Object]」 : 一个显示出来却读不懂的字段,
       // 和没有这个字段差不多。
       const li = DATA.history && DATA.history.lastIngest;
       if(!li || typeof li !== "object") return "";
       const rows = Object.entries(li).filter(([,v])=>v && v.at);
       if(!rows.length) return "";
       // 取最旧的那一条:摄入器是几条流水线,任何一条停了这些数字就有一部分停在那一刻。
       rows.sort((a,b)=>String(a[1].at).localeCompare(String(b[1].at)));
       const [oldName, oldest] = rows[0];
       // 判定由后端给。以前这里只按 ok 标红,而**时间有多旧完全没人看** ——
       // 一个九天前成功的摄入,在屏幕上和刚跑完的摄入长得一模一样:灰色、一个时间串,
       // 而热力图和健康% 全部停在九天前照常显示。没有人会读一眼时间再在心里减出九天。
       const V = INGEST();
       const tip = rows.map(([k,v])=>`${k}: ${v.at}${v.ok===false?" (失败)":""}`).join(" · ")
         + (V.why ? " — " + V.why
                  : " — 摄入器停了的话,上面这些数字会一直停在那一刻,而页面看起来毫无异常");
       const sev = INGEST_SEV(V.state);          // 3 红 / 2 黄 / 0 不着色
       const col = sev >= 3 ? "var(--bad)" : sev >= 2 ? "var(--warn)" : "";
       const tail = V.state === "failed" ? ` · ${esc((V.failed||[]).join("/"))} 失败`
                  : (V.state === "stale" || V.state === "loss")
                      ? ` · ${(V.ageHours/24).toFixed(1)}d 没摄入`
                  : V.state === "unknown" ? " · 时间读不出来" : "";
       return ` <span class="${col?"":"faint"}" style="${col?`color:${col}`:""}"
         title="${esc(tip)}">最后摄入 ${esc(String(oldest.at).slice(0,16))}${tail}</span>`;
     })()
    +(()=>{
       const R=DATA.runlog;
       if(!R||!R.available) return ` <span class="faint">无运行日志</span>`;
       // 读不懂的条数要跟着 count 一起显示。它们一直在被数,但以前只留在后端:
       // 事件格式一变、大批事件被丢掉时,页面上只看得到运行次数变少、成功率漂移,
       // 没有任何一处说明有多少条读不懂 —— 一个看起来精确、实则不完整的数字,
       // 而它旁边那个 count 还在替它背书。
       const bad = R.partial || (R.dropped>0);
       const tip = (R.countScope||"")
         + (R.dropped>0?` · 有 ${R.dropped} 条事件解析不了,没有计入`:"")
         + (R.partial?" · 日志读到一半失败,下面的条数是不完整的":"");
       return ` <span class="${bad?"":"faint"}" style="${bad?"color:var(--warn)":""}"
         title="${esc(tip)}">运行日志${R.count}${R.countScope?" · "+esc(R.countScope):""}${
         R.dropped>0?` · 读不懂 ${R.dropped}`:""}${R.partial?" · 不完整":""}</span>`;
     })();
  // 含「历史」的那条警告不在这里重复:热力图不可用时会把它印在图的标题上(renderHeat),
  // 贴在图上比躺在警告堆里有用 : 人是在图空着的时候才想知道为什么空。
  $("warns").innerHTML=(DATA.warnings||[])
    .filter(w=>w.indexOf("历史")<0)
    .map(w=>`<div class="warn-line">${esc(w)}</div>`).join("");
  renderFresh();
  updateBadges();
  renderTL(); renderHeat(); renderScores();

  const q=$("q").value.trim().toLowerCase(), cat=$("cat").value;
  const onlyBad=$("only").checked, hideOff=$("hideoff").checked;
  VIEW=ROWS.filter(r=>(!cat||r.cat===cat)&&!(hideOff&&r.state==="Disabled")
    &&!(onlyBad&&r.sk!=="bad"&&!sev(r))
    &&(!q||r.name.toLowerCase().indexOf(q)>=0||r.cat.toLowerCase().indexOf(q)>=0
       ||(r.desc||"").toLowerCase().indexOf(q)>=0));
  const col=C.find(c=>c[0]===sortKey)||C[3];
  VIEW.sort((a,b)=>{const x=col[3](a),y=col[3](b);
    const c=(typeof x==="number"&&typeof y==="number")?x-y:String(x).localeCompare(String(y),"zh");
    return asc?c:-c;});
  if(cur>=VIEW.length) cur=Math.max(0,VIEW.length-1);
  $("cnt").textContent=`${VIEW.length}/${ROWS.length} 行`+(sel.size?` · 已选 ${sel.size}`:"");
  // 记下焦点落在哪一行的哪个控件上,重建之后放回去。
  const ae = document.activeElement;
  const aeRow = ae && ae.closest ? ae.closest("#tbl tbody tr[data-name]") : null;
  const keep = aeRow ? {name: aeRow.dataset.name,
                        act: ae.dataset ? ae.dataset.act : null,
                        box: ae.classList && ae.classList.contains("selbox")} : null;
  const CC=shownCols();
  // 排序是这张表最主要的整理手段,原来只有 click:纯键盘用户完全用不了,
  // 读屏用户既按不动也听不出当前按哪一列排(方向只存在于 ::after,没有 aria-sort 兜底)。
  // selc 那一列的排序键是常量,点它会重排一次却看不出任何变化 :
  // 一个会响应但没有效果的可点区域,比不可点更让人怀疑自己看错了,所以它不给 data-k。
  $("tbl").innerHTML=`<thead><tr>${CC.map(c=>{
      const sortable = c[0] !== "selc";
      const cur = sortKey===c[0];
      const aria = !sortable ? "" : ` aria-sort="${cur ? (asc?"ascending":"descending") : "none"}"`;
      const tab = sortable ? ' tabindex="0" role="columnheader"' : "";
      return `<th${sortable?` data-k="${c[0]}"`:""}${tab}${aria} class="${cur?"s"+(asc?" a":""):""}">${c[1]}</th>`;
    }).join("")}</tr></thead>`
    // 光标行和选中行原来只有 CSS 类:读屏用户按 j/k 时焦点始终在 body,
    // 屏幕上那条光标在无障碍树里不存在,他不知道自己停在哪一行。
    // tabindex=-1 让 focusCur() 能真的把焦点放上去,aria-selected 让选中态可播报。
    +`<tbody>${VIEW.map((r,i)=>`<tr data-i="${i}" data-name="${esc(r.name)}" tabindex="-1" aria-selected="${sel.has(r.name)}" class="${i===cur?"cur":""}${sel.has(r.name)?" sel":""}">${CC.map(c=>c[2](r)).join("")}</tr>`).join("")}</tbody>`;
  // render() 用 innerHTML 整表重建,焦点持有者被移出文档,activeElement 掉回 body,
  // 下一次 Tab 从页面开头重来。x 选中和 j/k 也都调 render(),
  // 所以任何一次选择都会打断 Tab 序列,而同一操作用鼠标毫无代价。
  if(keep && keep.name){
    const row = document.querySelector(`#tbl tbody tr[data-name="${CSS.escape(keep.name)}"]`);
    if(row){
      const target = keep.act
        ? row.querySelector(`[data-act="${keep.act}"]`) || row
        : (keep.box ? row.querySelector("input.selbox") : row);
      try{ (target||row).focus({preventScroll:true}); }catch(e){}
    }
  }
  renderBulk();
}

function renderBulk(){
  const b=$("bulk");
  if(!sel.size){ b.hidden=true; return; }
  b.hidden=false;
  b.innerHTML=`<span>已选 <b>${sel.size}</b></span>
    <button data-bulk="run">全部运行</button>
    <button data-bulk="enable">全部启用</button>
    <button class="danger" data-bulk="disable">全部停用</button>
    <button data-bulk="clear">清空</button>`;
}

async function act(names, verb){
  if(busy||!names.length) return;
  if(verb==="disable"&&!confirm(`停用 ${names.length} 个任务?\n\n${names.join("\n")}\n\n它们将不再按计划运行,直到重新启用。`)) return;
  busy=true;
  let ok=0, fail=0;
  for(const n of names){
    try{
      const r=await api("/api/act",{method:"POST",body:JSON.stringify({name:n,verb:verb})});
      if(r.ok){ ok++; if(names.length===1) toast(`${n}:${r.message} (${r.before} -> ${r.after})`,"ok"); }
      else { fail++; toast(`${n}:${r.message}`,"bad"); }
    }catch(e){ fail++; toast(`${n}:${e.message}`,"bad"); }
  }
  if(names.length>1) toast(`${verb}:成功 ${ok},失败 ${fail}`, fail?"bad":"ok");
  busy=false;
  await load();
}

// ================= 跨区跳转 ========================================================
// 分区改造引入了一整类静默失效的动作:「点这边、改那边」。灯板上一格的处理器一直都在,
// 它把任务表过滤到那一格 : 可任务表在 tasks 分区里,而你点格子的时候人在 overview,
// 那张表是 display:none 的。过滤真的跑了,只是发生在一块看不见的地方,不报错、不提示。
//
// 所以凡是跨区的动作都必须先把目标分区切出来。下面两个函数是唯一的入口,
// 所有「显示了任务名的地方」都走它们,而不是各自去改一次过滤框。
function focusTask(name){
  showView("tasks", true);
  const q = $("q");
  // 再点同一个就取消过滤:一个只能进不能退的过滤,用一次就得手动清一次。
  q.value = (q.value === name) ? "" : name;
  $("cat").value = "";        // 大类过滤会和任务名过滤互相缩小,同时开着等于两道条件
  if (DATA) render();
  const tr = document.querySelector("#tbl tbody tr");
  if (tr) tr.scrollIntoView({block: "nearest"});
}

function focusCategory(cat){
  showView("tasks", true);
  $("q").value = "";
  const sel2 = $("cat");
  sel2.value = (sel2.value === cat) ? "" : cat;
  if (DATA) render();
}

const targets = () => sel.size ? Array.from(sel) : (VIEW[cur] ? [VIEW[cur].name] : []);

function openDetail(tr){
  document.querySelectorAll("tr.det").forEach(x=>x.remove());
  const r=VIEW[+tr.dataset.i];
  if(r) tr.insertAdjacentHTML("afterend", detail(r));
}
function toggleDetail(){
  const tr=document.querySelector(`#tbl tbody tr[data-i="${cur}"]`);
  if(!tr) return;
  const d=tr.nextElementSibling;
  if(d&&d.classList.contains("det")) d.remove(); else openDetail(tr);
}
function focusCur(){
  const tr=document.querySelector(`#tbl tbody tr[data-i="${cur}"]`);
  if(!tr) return;
  tr.scrollIntoView({block:"nearest"});
  // 真的把焦点放上去,不只是滚过去。否则读屏那边始终停在 body,
  // 「我在第几行」这件事只存在于一条视觉上的高亮里。
  try{ tr.focus({preventScroll:true}); }catch(e){ tr.focus(); }
}


async function retireTask(name){
  let p;
  try{ p=await api("/api/retire/plan",{method:"POST",body:JSON.stringify({name})}); }
  catch(e){ toast(`${name}: ${e.message}`,"bad"); return; }
  if(p.error){ toast(`${name}: ${p.error}`,"bad"); return; }
  if(p.blocked&&p.blocked.length){
    toast(`${name}: 这几处没配置,不能只做一半 (${p.blocked.join(", ")})`,"bad"); return;
  }
  const lines=p.steps.map(s=>`  ${s.step}: ${s.state}`).join("\n");
  if(!confirm(`退役 ${name}\n\n${lines}\n\n共 ${p.changes} 处会被改动。继续?`)) return;
  const reason=prompt(`退役原因(必填,会写进任务的 Description):`,"");
  if(!reason||!reason.trim()){ toast("没有原因就不退役",'bad'); return; }
  try{
    const r=await api("/api/maint/act",{method:"POST",
      body:JSON.stringify({action:"task.retire",name,arg:reason})});
    if(r.error){ toast(`${name}: ${r.error}`,"bad"); return; }
    toast(`${name} 已退役(${(r.done||[]).join("+")||"无需改动"})`);
    await load();
  }catch(e){ toast(`${name}: ${e.message}`,"bad"); }
}

// 提交并推送。两步:先拿一份只读计划摊开给人看,确认之后才带着那份清单去执行。
//
// 这是这个台子上唯一一个把东西送出这台机器的动作,所以它刻意**不是一键**:
// 一次误击只会打开一份计划。但确认之后的那一串(add 逐条路径、commit、push、
// 等钩子跑完、把钩子输出原样摊出来)全部由这里完成 —— 要省掉的是那串机械动作,
// 不是那个决定。
