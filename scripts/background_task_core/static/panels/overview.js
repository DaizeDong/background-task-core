// Classic script module; loaded in app.js dependency order.
// 侧栏和标签上的徽章由 navigation.js 的 setBadge() 画;这里只负责算数,
// 每个数都取自那一屏自己的判定,不另立一套。
function updateBadges(){
  renderTiles();
  const todoN = renderTodo();
  // 诊断徽章 = **这一屏上那张清单的条数**,也就是不筛选时「技术问题」里的行数。
  //
  // ⚠ 它以前是自己另算一套(产物新鲜度 attention + 自检 broken),而清单收的是
  // 任务 bad + 产物 attention + 仓库 attention + 磁盘 + 记忆索引 ——
  // 两个集合互不包含:徽章含自检而清单不含,清单含任务/仓库/存储而徽章不含。
  // 于是侧栏写着 3、点进去列着 7,**同一件事的两个数,而没有任何一处对账**。
  // 现在徽章直接用清单算出来的条数:清单是这一屏唯一在回答「有哪些事」的东西,
  // 徽章只是它的一个投影。**同一个事实只留一个来源,比让两个来源互相解释便宜得多。**
  setBadge("diagnostics", todoN, !REVIEW_TOTALS.bad, `技术问题：${todoN} 个对象要处理`);
  renderPlatformSignals(); renderAutomations();

  if(DATA && DATA.summary)
    setBadge("tasks", DATA.summary.bad || 0, false, `运行详情：${DATA.summary.bad || 0} 个任务上次运行失败`);

}

// 一格瓷砖。filter 是点它时「技术问题」清单切到的范围:点格子只在这一屏里筛清单,不再跳去别的页。
// 以前有的格子跳走(任务去运行详情)、有的格子在本屏滚动(输出文件去灯板),同一排格子两种行为,
// 点之前猜不出会去哪。html 给的是已经拼好的大字(读取中、未检查、读取失败用共用的 countCell)。
function tile({filter, key, val, html, sub, cls, pct, pctWhat, tip}){
  let bar = "";
  if(pct != null && isFinite(pct)){
    // 超过 100 时钳到 100,但加 over 类长出一截斜纹尾巴 ——
    // 「刚好满」和「已经爆了」必须是两个形状,不能是同一根满条。
    const over = pct > 100;
    bar = `<span class="tb${over?" over":""}" title="${esc(pctWhat||"")}"
      ><i class="${cls||""}" style="width:${Math.min(100,Math.max(0,pct)).toFixed(1)}%"></i></span>`;
  }
  const title = (tip || sub || "") + (pctWhat ? " · " + pctWhat : "") + " · 点击只看这一类问题";
  return `<button type="button" class="tile" data-review-filter="${esc(filter)}" title="${esc(title)}">
    <span class="k">${esc(key)}</span>
    <span class="v ${cls||""}">${html != null ? html : esc(String(val))}</span>
    ${bar}<span class="s">${esc(sub||"")}</span></button>`;
}

// 还没有数的那三种格子。读取中、未检查、读取失败各有各的字形(countCell),副标题一个词说清是哪种,
// 原因进悬停。以前三种都写「- 未检查」:读取慢的时候和真的没配一模一样,读失败了也还是这一句。
function waitingTile(filter, key, read){
  if(read.state === "failed"){
    const why = failureReason(read.reason);
    return tile({filter, key, html:countCell("broken", null, read.reason), sub:"读取失败",
                 tip:"读取失败：" + (why.text || why.raw || "原因未知")});
  }
  if(read.state === "pending") return tile({filter, key, html:countCell("loading"), sub:"读取中", cls:"idle"});
  return tile({filter, key, html:countCell("unchecked"), sub:"未检查", cls:"idle", tip:read.unchecked || "未检查"});
}

// 要人管的事:把四个分区里判为「要人管」的行合到一处。
// 只搬结论,不重新判定 : 判定各自留在各自的模块里,这里再判一次就等于把同一条规则写两遍,
// 而两份规则一定会漂。
// 这两个函数是「要人管的集合」的唯一读取点。后端不给时才回落到一份写死的默认,
// 而回落本身要看得见 : 一个静默回落的默认值,和后端真给了这个值,在页面上长得一样。
function FR_ATT(){
  const S = (DATA && DATA.freshness && DATA.freshness.summary) || {};
  return S.attentionStates || ["down","never","unknown"];
}

// 摄入新鲜度的判定同样只从后端读。阈值(24h 注意 / 96h 进丢失窗口)写在
// console_store.ingest_verdict 里,连同它为什么是这两个数 —— 在这里再写一遍就是
// 同一条规则的第二份,而两份一定会漂,漂的时候两处都还在正常渲染。
function INGEST(){
  const h = (DATA && DATA.history) || {};
  const v = h.ingest;
  // 后端没给判定 ≠ 摄入器没事。老版本的后端、或者一条忘了带这个键的通路,
  // 都会走到这里;此时说「不知道」,不说「正常」。
  if(!v || typeof v !== "object")
    return {state:"unknown", why:"这份数据没带摄入新鲜度判定,没法判断摄入器是不是还在跑。",
            failed:[], ageHours:null};
  return v;
}
// 判定 -> 严重度。n/a 和 ok 都不算要人管,但它们是两件事,所以分开写在这里而不是
// 靠一个 falsy 检查把两者折成一个。
function INGEST_SEV(state){
  return state === "loss" || state === "failed" ? 3
       : state === "stale" || state === "never" || state === "unknown" ? 2
       : 0;
}

// 「这一条能不能一键处理」由下面各行的 fix 字段说(run / 没有);按钮由清单那边的
// fixBtn(review.js)画,这里只判有没有通路。
// 要人管的那些行。诊断屏的清单(renderTodo)和工作台顶上的摘要(attentionSummary)读的是这同一份,
// 一处判定、两处显示,两边的数字对得上。key 说明一行来自哪一类,摘要按它分组。
function attentionRows(){
  const rows = [];

  // 任务:sk 是任务模块自己的判定键,bad 这一档就是它判失败的那些。
  // 不在这里另立一套「怎么算失败」 : 两份规则一定会漂,而漂的时候两块内容都还在正常渲染,
  // 看不出哪一份是对的。
  if(DATA && Array.isArray(DATA.groups))
    DATA.groups.flatMap(g=>g.rows||[]).filter(r=>r.sk==="bad").forEach(r=>rows.push(
      {key:"tasks", v:"tasks", src:"任务", nm:r.name, task:r.name, description:r.desc, why:r.sl || "上次运行失败", sev:3,
       fix:"run"}));

  if(DATA && DATA.freshness && Array.isArray(DATA.freshness.tasks))
    // 状态集合来自后端 summary.attentionStates,不在这里再写一遍。
    // 两处各写一份的后果是它们会漂,而漂的时候两处都还在正常渲染。
    DATA.freshness.tasks.filter(r=>FR_ATT().indexOf(r.state) >= 0).forEach(r=>rows.push(
      // ⚠ 名字要带上 check。一个任务可以把好几件不相干的事折叠进来,每件各写一条声明,
      // 而它们在这里全叫同一个任务名 —— 光看名字只知道「这个任务有问题」,
      // 说不出是它底下哪一件。折叠带来的盲区就是在这一行被补上的。
      {key:"outputs", v:"tasks", src:"产物", nm:r.check ? `${r.name} · ${r.check}` : r.name, task:r.name,
       why:(r.reasons && r.reasons[0]) ||
           (r.state==="never" ? "从未运行过" : r.state==="unknown" ? "查不成" : "产物过期"),
       sev:r.state==="down" ? 3 : 2}));

  // 摄入器停摆。这一条要出现在清单上,而不是只在顶栏当一个灰色时间串:
  // 顶栏那行是给已经在看的人的,清单才是「今天有什么要管」的那份答案,
  // 而摄入器停了的后果比清单上任何一条都大 —— 页面上每一个运行与健康数字都停在那一刻,
  // 且滚动日志过了窗口就永久没了。
  {
    const V = INGEST(), sev = INGEST_SEV(V.state);
    if(sev) rows.push({key:"ingest", v:"tasks", src:"摄入", nm:"运行日志摄入",
                       why:V.why || ("摄入状态 " + V.state), sev:sev});
  }

  return rows;
}

// 清单是三种样子之一:任务还在读(pending)、任务读不到(failed)、读到了(loaded)。
// 以前只问「DATA 有没有」,而第一次读取没回来之前 DATA 也是空的:正常的慢读取被画成了
// 「清单不完整 / 任务读取失败」,同时上面的说明写着「正在读取检查结论」,两句话互相打架。
function renderTodo(){
  const box = $("todod"); if(!box) return;
  const tasks = readState(ATTENTION_SOURCES.tasks);
  if(tasks.state !== "ok") return renderReviewQueue([], tasks);
  return renderReviewQueue(attentionRows(), {state:"loaded"});
}

// 工作台顶上那条摘要要的数:每一类要人管的事有几条,以及这个数此刻能不能信。
// state 只有三种:pending 还没读到(第一次读取没回来,或者根本还没开始读),failed 读了但读不到,
// ok 读到了。只有 ok 时 count 才是一个结论;另外两种 count 记 0,但调用方必须先看 state,
// 否则「没读到」会被画成「没有问题」。
// 刷新途中旧数据还在就照旧算 ok,不让摘要在每次刷新时闪成「读取中」。
// 读到了却没在查的那一类(没有产物清单、没配仓库根目录、没量到磁盘)仍是 ok、count 为 0,另带一个 unchecked 说明原因,
// 调用方不能把它画成零。它也不能算 pending:那份数据已经回来了,再等也不会变,一直写「读取中」等于撒谎。
const TASKS_LOADED=()=>!!(DATA && Array.isArray(DATA.groups));
const ATTENTION_SOURCES={
  tasks:{path:"/api/tasks",loaded:TASKS_LOADED},
  outputs:{path:"/api/tasks",loaded:TASKS_LOADED,
    unchecked:()=>!DATA.freshness?"这份数据没带产物检查":(DATA.freshness.reason || null)},
  ingest:{path:"/api/tasks",loaded:TASKS_LOADED},
};
// 一路来源此刻是哪种状态。瓷砖、清单和工作台摘要都从这里读,三处的「读取中 / 读不到 / 没在查」是同一个判断。
// ⚠ 「没在查」要先于「读取失败」判:后端对没配的来源回 available:false,api() 会把它的 reason
// 记成这次读取的 error。只看 error 的话,一个根本没配仓库根目录的机器会被画成「仓库读取失败」,
// 而那里没有任何东西坏了,只是没在查。
// unread:这一页从没请求过的按需来源(onDemand)。它不是读取中,也不是失败,和徽章的 unread 一样什么都不画:
// 写成「读取中」的话,工作台摘要会永远挂着一枚等不来的芯片。
function readState(source){
  const read = API_READS.get(source.path), loaded = source.loaded();
  if(!read && !loaded && source.onDemand) return {state:"unread"};
  const unchecked = loaded && source.unchecked ? source.unchecked() : null;
  if(read && !read.pending && read.error && !unchecked) return {state:"failed", reason:read.error};
  if(!loaded) return {state:"pending"};
  return unchecked ? {state:"ok", unchecked} : {state:"ok"};
}
function attentionSummary(){
  const rows = attentionRows(), out = {};
  for(const [key, source] of Object.entries(ATTENTION_SOURCES)){
    const read = readState(source);
    if(read.state === "failed"){ out[key] = {state:"failed", count:0, reason:read.reason}; continue; }
    if(read.state === "pending" || read.state === "unread"){ out[key] = {state:read.state, count:0}; continue; }
    // 数对象不数检查,对象怎么认由清单那边的 attentionObject(review.js)定,芯片上的数才等于点开后的行数。
    out[key] = {state:"ok", count:new Set(rows.filter(row=>row.key===key).map(attentionObject)).size};
    if(read.unchecked) out[key].unchecked = read.unchecked;
  }
  return out;
}

// 瓷砖先说问题:大字是要人管的条数,总数退到副标题里当背景。以前大字是总数(「任务 43」还涂成红色),
// 真正的问题「失败 13」缩在灰色小字里,扫一眼看到的是一个说明不了任何事的数。
// 排列按严重程度:判坏的在前,读不到的其次,再是快到上限的,然后是正常的,最后是还在读和没在查的。
// 「对话」那一格去掉了:它是会话文件的个数,不是一项检查,放在诊断这一排里只会被当成一项指标去读。
const TILE_RANK = {bad:0, failed:1, warn:2, ok:3, pending:4, unchecked:5};
function renderTiles(){
  const el = $("tiles"); if(!el) return;
  const out = [];
  const add = (rank, html) => out.push({rank:rank in TILE_RANK ? rank : "ok", html, at:out.length});
  const waiting = (filter, key, read) =>
    add(read.state === "failed" ? "failed" : read.state === "pending" ? "pending" : "unchecked",
        waitingTile(filter, key, read.state === "unread" ? {state:"unchecked", unchecked:"尚未读取，打开那一屏时读取"} : read));

  const tasks = readState(ATTENTION_SOURCES.tasks);
  if(tasks.state === "ok" && DATA.summary){
    const S = DATA.summary;
    add(S.bad ? "bad" : "ok", tile({filter:"tasks", key:"任务", val:S.bad, cls:S.bad?"bad":"ok",
      sub:`失败 / ${S.total} 个任务`, tip:`${S.bad} 个任务上次运行失败，共 ${S.total} 个，停用 ${S.disabled}`,
      pct:S.total?100*S.bad/S.total:null, pctWhat:S.total?`条里填的是失败占比 ${S.bad}/${S.total}`:null}));
  } else waiting("tasks", "任务", tasks.state === "ok" ? {state:"unchecked", unchecked:"这份数据没带任务汇总"} : tasks);

  // reason 要先判。没有健康清单时后端仍然返回一个 summary(total/bad 全是 0),
  // 所以「有没有 summary」这个条件永远成立,这一格拿到 bad=0 走绿色,
  // 印出「0 / 覆盖 0% · 共 0」 : 读起来是「零个产物过期」,实际上一个任务都没被检查。
  // 这件事由 ATTENTION_SOURCES.outputs 的 unchecked 判,这里只按它画。
  const outputs = readState(ATTENTION_SOURCES.outputs);
  if(outputs.state === "ok" && !outputs.unchecked && DATA.freshness.summary){
    const F = DATA.freshness.summary;
    // 用 attention 不用 bad:attention 含 unknown(查不成),而查不成正是这一格最该喊出来的那一类。
    const fa = (F.attention != null) ? F.attention : F.bad;
    add(fa ? "bad" : "ok", tile({filter:"tasks", key:"输出文件", val:fa, cls:fa?"bad":"ok",
      sub:`异常 / ${F.total} 项`, tip:`${fa} 项输出文件过期或查不成，共 ${F.total} 项，覆盖 ${Math.round((F.coverage||0)*100)}%`,
      pct:F.total?100*fa/F.total:null, pctWhat:F.total?`异常或无法检查 ${fa}/${F.total}`:null}));
  } else waiting("tasks", "输出文件", outputs);

  el.innerHTML = out.sort((a,b)=>TILE_RANK[a.rank]-TILE_RANK[b.rank] || a.at-b.at).map(t=>t.html).join("");
}

function renderHeat(){
  const H=DATA.history||{};
  if(!H.available){
    // 先用 history 自己给的原因。去 warnings 里捞是老写法:那条警告只在回落路径上才有,
    // 数据库路径不产生它,于是这里只剩「无历史」三个字 :
    // 「库里还没有观察数据(跑一次 backfill)」和「摄入器停了两周」和「本来就没配」
    // 在屏幕上会是同一句话,而它们要做的事完全不同。
    $("hmnote").textContent = H.reason
      || (DATA.warnings||[]).find(w=>w.indexOf("历史")>=0) || "无历史";
    $("heat").innerHTML=""; return;
  }
  $("hmnote").textContent=H.caveat||"";
  const days=H.days||[];
  const rows=ROWS.filter(r=>r.hist).sort((a,b)=>((a.hist.health==null?101:a.hist.health)-(b.hist.health==null?101:b.hist.health)));
  const last=days.length-1;
  // 行名用和任务表、时间轴同一个中文标题(taskText),机器名进悬停:同一个任务在一屏上两个名字,
  // 人得先在脑子里对一遍才知道说的是同一件事。行名是一个按钮,点了和任务表的「查看详情」去同一个地方;
  // data-task 挂在 th 上,由 events.js 里已有的 [data-task] 处理接走,不另写一套跳转。
  const body=rows.map(r=>{
    const label=taskText(r).title;
    const cells=days.map((d,i)=>{
      const td=i===last?"today":"";
      const c=r.hist.byDay[d];
      if(!c||!c.n) return `<td class="${td}" title="${esc(label)} ${d} 无观察"></td>`;
      const j=c.n-c.neutral;
      if(j<=0) return `<td class="${td}" title="${esc(label)} ${d} 仅中性观察"></td>`;
      const p=c.ok/j, k=p>=.99?"h2":p>=.9?"h1":p>=.6?"h3":p>=.25?"h4":"h5";
      return `<td class="${k} ${td}" title="${esc(label)} ${d} 正常 ${c.ok}/${j}${c.bad?" 失败 "+c.bad:""}${c.stale?" 陈旧 "+c.stale:""}"></td>`;
    }).join("");
    const tip=`${r.name} · 检查通过率 ${r.hist.health==null?"-":r.hist.health+"%"} · 点击查看任务详情`;
    return `<tr><th class="tn" data-task="${esc(r.name)}" title="${esc(tip)}"><button type="button" class="link-button">${esc(label)}</button></th>${cells}</tr>`;
  }).join("");
  // 横轴。这块图原来**一个列标签都没有**,于是右边那片浅灰到底是「最近没数据」
  // 还是「45 天前没数据」只能靠一格一格 hover 问出来 —— 而这两件事的严重程度天差地别,
  // 且这块图正是判断「摄入器是不是停了」的唯一视图。
  // 标签只在首、末和每月 1 号出现:45 个日期全印会糊成一条灰带。
  const head=days.map((d,i)=>{
    const lab=(i===0||i===last||d.slice(-2)==="01") ? d.slice(5) : "";
    return `<th class="hd${i===last?" today":""}" title="${esc(d)}">${lab}</th>`;
  }).join("");
  $("heat").innerHTML=`<thead><tr><th class="tn"></th>${head}</tr></thead><tbody>${body}</tbody>`;
}
