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
  // 自检读不到的来源是「这张清单本身可不可信」那一层,不在清单里,所以不加进数字(加了就对不上行数),
  // 而是写进徽章的说明并把它标红。
  const sckBroken = SCK && !SCK.ok ? ((SCK.broken || []).length || 1) : 0;
  setBadge("diagnostics", todoN, !(REVIEW_TOTALS.bad || sckBroken),
    `技术问题：${todoN} 个对象要处理` + (sckBroken ? `；另有 ${sckBroken} 个数据来源读不到，清单可能不全` : ""));
  if(typeof renderPlatformSignals === "function") { renderPlatformSignals(); renderAutomations(); }

  if(typeof DATA !== "undefined" && DATA && DATA.summary)
    setBadge("tasks", DATA.summary.bad || 0, false, `运行详情：${DATA.summary.bad || 0} 个任务上次运行失败`);

  if(REPOS && REPOS.available && REPOS.summary){
    // 有改动、没推送是要人处理但还没坏,琥珀色;扫不动的仓才是红的。
    const attention = REPOS.summary.attention || 0, broken = (REPOS.summary.counts || {}).error || 0;
    setBadge("repos", attention, !broken, `代码仓库：${attention} 个有未提交、未推送的改动或读不出来`);
  }

  // 存储:磁盘吃紧或记忆索引逼近硬上限。还没坏但快了是琥珀色;判成坏的(比如磁盘已满)才是红的。
  let st = 0, stBad = false;
  if(SYS && SYS.disk && SYS.disk.verdict && SYS.disk.verdict.attention){ st += 1; stBad = stBad || toneOf(SYS.disk.verdict) === "bad"; }
  if(MEM && MEM.available && MEM.verdict && MEM.verdict.attention){ st += 1; stBad = stBad || toneOf(MEM.verdict) === "bad"; }
  setBadge("storage", st, !stBad, `存储清理：${st} 项接近上限（磁盘、记忆索引）`);
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
  const S = (typeof DATA !== "undefined" && DATA && DATA.freshness && DATA.freshness.summary) || {};
  return S.attentionStates || ["down","never","unknown"];
}
function RP_ATT(){
  const S = (REPOS && REPOS.summary) || {};
  return S.attentionStates || ["unpushed","dirty","error"];
}

// 摄入新鲜度的判定同样只从后端读。阈值(24h 注意 / 96h 进丢失窗口)写在
// console_store.ingest_verdict 里,连同它为什么是这两个数 —— 在这里再写一遍就是
// 同一条规则的第二份,而两份一定会漂,漂的时候两处都还在正常渲染。
function INGEST(){
  const h = (typeof DATA !== "undefined" && DATA && DATA.history) || {};
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

// 「这一条能不能一键处理」由下面各行的 fix 字段说(run / commitpush / 没有);按钮由清单那边的
// fixBtn(review.js)画,这里只判有没有通路。
// 要人管的那些行。诊断屏的清单(renderTodo)和工作台顶上的摘要(attentionSummary)读的是这同一份,
// 一处判定、两处显示,两边的数字对得上。key 说明一行来自哪一类,摘要按它分组。
function attentionRows(){
  const rows = [];

  // 任务:sk 是任务模块自己的判定键,bad 这一档就是它判失败的那些。
  // 不在这里另立一套「怎么算失败」 : 两份规则一定会漂,而漂的时候两块内容都还在正常渲染,
  // 看不出哪一份是对的。
  if(typeof DATA !== "undefined" && DATA && Array.isArray(DATA.groups))
    DATA.groups.flatMap(g=>g.rows||[]).filter(r=>r.sk==="bad").forEach(r=>rows.push(
      {key:"tasks", v:"tasks", src:"任务", nm:r.name, task:r.name, description:r.desc, why:r.sl || "上次运行失败", sev:3,
       fix:"run"}));

  if(typeof DATA !== "undefined" && DATA && DATA.freshness && Array.isArray(DATA.freshness.tasks))
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

  if(REPOS && REPOS.available && Array.isArray(REPOS.repos))
    REPOS.repos.filter(r=>RP_ATT().indexOf(r.state) >= 0).forEach(r=>rows.push(
      // fix 是「这一条能不能一键处理」。只有 dirty / unpushed 能:它们的处理方式
      // 逐字相同,而且已经有一条带计划的两步通路。error(扫不动)不给 fix ——
      // 那是要人去看的,给它一个按钮等于假装这里有个办法。
      {key:"repos", v:"repos", src:"仓库", nm:r.name, why:REPO_WHY[r.state] || r.state, sev:2,
       fix:(r.state==="dirty"||r.state==="unpushed") ? "commitpush" : null}));

  // 摄入器停摆。这一条要出现在清单上,而不是只在顶栏当一个灰色时间串:
  // 顶栏那行是给已经在看的人的,清单才是「今天有什么要管」的那份答案,
  // 而摄入器停了的后果比清单上任何一条都大 —— 页面上每一个运行与健康数字都停在那一刻,
  // 且滚动日志过了窗口就永久没了。
  {
    const V = INGEST(), sev = INGEST_SEV(V.state);
    if(sev) rows.push({key:"ingest", v:"tasks", src:"摄入", nm:"运行日志摄入",
                       why:V.why || ("摄入状态 " + V.state), sev:sev});
  }

  if(SYS && SYS.disk && SYS.disk.verdict && SYS.disk.verdict.attention)
    rows.push({key:"disk", v:"storage", src:"存储", nm:"系统盘",
               why:"已用 "+SYS.disk.usedPct+"%", sev:3});

  // available 只说「目录读到了」,不说 MEMORY.md 读到了。MEMORY.md 被改名或删掉时
  // linePct/bytePct 是 null,而 `||0` 把它折成 0,于是板子显示绿色的「0%」,
  // 副标题字面印出「null/200 行」。两条硬上限此刻完全没在量任何东西,
  // 而屏幕上说它离上限还有 100%。
  if(MEM && MEM.available && (MEM.linePct != null || MEM.bytePct != null)){
    const p = Math.max(MEM.linePct||0, MEM.bytePct||0);
    if(MEM.verdict && MEM.verdict.attention) rows.push({key:"memory", v:"resources", src:"存储", nm:"MEMORY.md",
      why:"索引 "+p+"%,逼近硬上限", sev:toneOf(MEM.verdict)==="bad"?3:2});
  }

  return rows;
}

// 清单是三种样子之一:任务还在读(pending)、任务读不到(failed)、读到了(loaded)。
// 以前只问「DATA 有没有」,而第一次读取没回来之前 DATA 也是空的:正常的慢读取被画成了
// 「清单不完整 / 任务读取失败」,同时上面的说明写着「正在读取检查结论」,两句话互相打架。
// 仓库、磁盘、记忆索引这几路晚到或者读不到时,清单照样画,但要说出缺了哪几路,
// 否则一张还缺两路的清单看起来就是完整的,数字过一会儿又自己变了。
const REVIEW_SOURCE_NAMES = {repos:"仓库", disk:"磁盘", memory:"记忆索引"};
function renderTodo(){
  const box = $("todod"); if(!box) return;
  const tasks = readState(ATTENTION_SOURCES.tasks);
  if(tasks.state !== "ok") return renderReviewQueue([], tasks);
  const pending = [], failed = [];
  for(const [key, name] of Object.entries(REVIEW_SOURCE_NAMES)){
    const read = readState(ATTENTION_SOURCES[key]);
    if(read.state === "pending") pending.push(name);
    if(read.state === "failed") failed.push(name);
  }
  return renderReviewQueue(attentionRows(), {state:"loaded", pending, failed});
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
  repos:{path:"/api/repos",loaded:()=>!!REPOS,
    unchecked:()=>REPOS.available?null:(REPOS.reason || "仓库没在查")},
  disk:{path:"/api/sys",loaded:()=>!!(SYS && !SYS.error),
    unchecked:()=>SYS.disk && SYS.disk.usedPct != null?null:((SYS.disk && SYS.disk.reason) || "磁盘没量到")},
  memory:{path:"/api/mem",loaded:()=>!!(MEM && !MEM.error),
    unchecked:()=>!MEM.available?(MEM.reason || "记忆池没在查")
      :(MEM.linePct == null && MEM.bytePct == null)?"索引大小没读到":null}
};
// 一路来源此刻是哪种状态。瓷砖、清单和工作台摘要都从这里读,三处的「读取中 / 读不到 / 没在查」是同一个判断。
// ⚠ 「没在查」要先于「读取失败」判:后端对没配的来源回 available:false,api() 会把它的 reason
// 记成这次读取的 error。只看 error 的话,一个根本没配仓库根目录的机器会被画成「仓库读取失败」,
// 而那里没有任何东西坏了,只是没在查。
function readState(source){
  const read = API_READS.get(source.path), loaded = source.loaded();
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
    if(read.state === "pending"){ out[key] = {state:"pending", count:0}; continue; }
    out[key] = {state:"ok", count:rows.filter(row=>row.key===key).length};
    if(read.unchecked) out[key].unchecked = read.unchecked;
  }
  // 磁盘和记忆索引的数是「有没有逼近上限」,摘要里要的是那个百分比本身。
  if(out.disk.state==="ok" && !out.disk.unchecked) out.disk.usedPct = SYS.disk.usedPct;
  if(out.memory.state==="ok" && !out.memory.unchecked) out.memory.pct = Math.max(MEM.linePct||0, MEM.bytePct||0);
  return out;
}

const REPO_WHY = {dirty:"有未提交改动", unpushed:"有未推送提交",
                  detached:"游离 HEAD", error:"读不出来"};

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
    add(read.state === "failed" ? "failed" : read.state === "pending" ? "pending" : "unchecked", waitingTile(filter, key, read));

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

  const repos = readState(ATTENTION_SOURCES.repos);
  if(repos.state === "ok" && !repos.unchecked && REPOS.summary){
    const R = REPOS.summary;
    // unknownUpstream 在「这个根目录下没有 git 仓」那条分支上不存在。一个 undefined 印在屏幕上,
    // 比一个说不出来的空更糟,因为它看起来像一个值。
    const up = (R.unknownUpstream == null) ? "?" : R.unknownUpstream;
    add(R.attention ? "bad" : "ok", tile({filter:"repos", key:"仓库", val:R.attention, cls:R.attention?"bad":"ok",
      sub:`待处理 / ${R.total} 个仓库`, tip:`${R.attention} 个仓库有改动、没推送或读不出来，共 ${R.total} 个，无上游 ${up}`,
      pct:R.total?100*R.attention/R.total:null, pctWhat:R.total?`存在改动或读取问题 ${R.attention}/${R.total}`:null}));
  } else waiting("repos", "仓库", repos);

  const disk = readState(ATTENTION_SOURCES.disk);
  if(disk.state === "ok" && !disk.unchecked){
    const d = SYS.disk, tone = toneOf(d.verdict);
    add(d.verdict && d.verdict.attention ? tone : "ok", tile({filter:"storage", key:"磁盘已用", val:d.usedPct+"%",
      sub:`剩 ${(d.free/1073741824).toFixed(0)}G`, cls:tone, pct:d.usedPct, pctWhat:"磁盘已用空间占比"}));
  } else waiting("storage", "磁盘", disk);

  // ⚠ available 只表示**记忆池目录**读到了。MEMORY.md 不在或读不了时,
  // linePct / bytePct / indexLines / indexBytes 全是 null,而 available 仍然是 true。
  // 这里原来是 `MEM.linePct||0`,于是一个「索引读不到」的机器上印出的是
  // **绿色的 0%** 和字面量「null/200 行」—— 一个查不成的东西被画成了最健康的样子。
  const memory = readState(ATTENTION_SOURCES.memory);
  if(memory.state === "ok" && !memory.unchecked){
    const p = Math.max(MEM.linePct||0, MEM.bytePct||0), tone = toneOf(MEM.verdict);
    // 取大的那个:两条上限哪条先撞都是撞。副标题要说出取的是哪一条,
    // 否则这个百分比对不上配置屏那两条独立的条,看起来像两处在打架。
    const pWhich = (MEM.linePct||0) >= (MEM.bytePct||0) ? "行数" : "字节";
    // 副标题必须跟着 pWhich 走。取字节口径时却印「150/200 行」,读的人按 150/200 心算
    // 是 75%,而大字写着 96.9% : 两个数字算不出彼此。
    const pFrac = (pWhich === "字节" && MEM.indexBytes != null)
      ? `${kb(MEM.indexBytes)}/${kb(MEM.hardBytes)}`
      : `${MEM.indexLines}/${MEM.hardLines} 行`;
    add(MEM.verdict && MEM.verdict.attention ? tone : "ok", tile({filter:"storage", key:"记忆索引", val:p+"%",
      sub:`${pWhich} ${pFrac} · 已归档 ${MEM.cold}`, cls:tone, pct:p, pctWhat:"索引额度使用率，超过 100% 时显示斜纹"}));
  } else if(memory.state === "ok" && MEM.available){
    // 目录读到了、索引没读到 —— 这不是「未检查」,是「查了但查不成」。
    add("warn", tile({filter:"storage", key:"记忆索引", val:"?", sub:MEM.indexReason || "MEMORY.md 读不到", cls:"warn"}));
  } else waiting("storage", "记忆索引", memory);

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

// 十行四列四十个百分比,而这张表的用途是横向比较哪一类拖后腿 —— 纯数字做不到
// 「一眼看出谁最差」。加一条短横杠,数字原样留在旁边。
// ⚠ null 分支**完全不画槽**,只给一个灰 `-` 加 title。空槽(未检查)和 0% 长度的条
// (真的 0)在形状上必须不同:给 null 画一条空槽,就是把「没查」画成了「查了,是零」。
// 绝对时刻 -> 相对。空串不接受:调用处先判空再进来,不靠返回值兜底 ——
// 一个把空串变成「0分前」的函数,会把「从未跑过」画成「刚刚跑过」,方向正好反了。
