// Classic script module; loaded in app.js dependency order.
const VIEWS = ["overview", "tasks", "repos", "storage", "convos", "llm"];
let CURVIEW = null;

function showView(key, push){
  if(VIEWS.indexOf(key) < 0) key = VIEWS[0];
  CURVIEW = key;
  document.querySelectorAll("section[data-view]").forEach(sec=>{
    sec.hidden = sec.dataset.view !== key;
  });
  // 导航语境里 aria-current 的值是 "page",不是 "true";非当前项直接摘掉属性,
  // 留一个 aria-current="false" 虽然合法,但读屏会把它念出来。
  // 同时做 roving tabindex:整条侧栏在 Tab 序列里只占一格。这不是无障碍装饰 :
  // 这个台子的表格已经是 j/k 驱动的,侧栏是全页唯一还要连按六下 Tab 才能越过的地方。
  document.querySelectorAll(".nv").forEach(b=>{
    const cur = b.dataset.view === key;
    if(cur) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    b.classList.toggle("active", cur);          // Tabler 画选中态看的是这个类
    b.parentElement.classList.toggle("active", cur);
    b.tabIndex = cur ? 0 : -1;
  });
  if(push && location.hash.slice(1) !== key) location.hash = key;
  // 摊开态下所有分区都可见(#view.all 用 !important 压过 hidden),
  // 于是「切分区」这个动作没有任何可见效果,只剩滚动归零。
  // 这时正确的行为是滚到那一段,而不是滚到页顶 : 否则每一次跳转看起来都像坏了。
  // 摊开态下「切分区」本来没有可见效果:所有分区都显示着,写 hidden 被 !important 压掉,
  // 于是点侧栏只剩滚动归零,看起来像坏了。
  // 试过三种滚到那一段的做法,没有一种稳:scrollIntoView 差 538px;自己算绝对位置
  // 当时五个分区里只有两个落点对;交回浏览器原生锚点又和 .nv 上的 preventDefault 打架。
  // 所以不滚了,直接收回摊开 : 点侧栏的本意就是「我要看这一类」,而摊开是「我暂时全都要看」。
  // Ctrl+F 需要摊开时随时再按 Shift+A,那条路径没有被拿走。
  $("view").classList.remove("all");
  // 切分区时把滚动位置归零。不归零的话从一屏很长的分区切到一屏很短的,
  // 看到的是一片空白,而那看起来像「这一类什么都没有」。
  // (原来这里还有一行 $("view").scrollTop = 0。#view 从来不是滚动容器,那一行永远无效,
  //  而它会让下一个人以为 #view 有独立滚动,任何基于这个假设的改动都会踩空。)
  window.scrollTo(0, 0);
}

// 侧栏徽章。这是「合并」这件事的安全带:分区把东西收了起来,徽章负责让要人管的东西
// 不用点进去也看得见。少了它,合并就是纯粹的藏。
function setBadge(key, n, warn){
  const el = $("bg-" + key);
  if(!el) return;
  el.textContent = n > 99 ? "99+" : String(n);
  // 用 hidden 而不是一个 .zero 类来藏:侧栏收起时有一条规则把徽章绝对定位到图标角上,
  // 一个「宽高为零但仍在文档流里」的徽章会在那里留下一个看不见的偏移。
  el.hidden = !n;
  el.className = "badge ms-auto " + (warn ? "bg-warning" : "bg-danger");
  // 一个只有数字的红块说不出自己是什么。加可读名字之后它才是「这一区有 N 项要人管」,
  // 而不是「这里有个红色的东西」。
  el.title = `${n} 项要人管,点进去看`;
  el.setAttribute("aria-label", el.title);
}

function updateBadges(){
  renderTiles();
  const todoN = renderTodo();
  // 概览徽章 = **这一屏上那张清单的条数** + 自检读不到的来源。
  //
  // ⚠ 它以前是自己另算一套(产物新鲜度 attention + 自检 broken),而清单收的是
  // 任务 bad + 产物 attention + 仓库 attention + 磁盘 + 记忆索引 ——
  // 两个集合互不包含:徽章含自检而清单不含,清单含任务/仓库/存储而徽章不含。
  // 于是侧栏写着 3、点进去列着 7,**同一件事的两个数,而没有任何一处对账**。
  // 现在徽章直接用清单算出来的条数:清单是这一屏唯一在回答「有哪些事」的东西,
  // 徽章只是它的一个投影。**同一个事实只留一个来源,比让两个来源互相解释便宜得多。**
  // 自检单独加,是因为它是「这张清单本身可不可信」那一层,不在清单里。
  let ov = todoN;
  if(SCK && !SCK.ok) ov += (SCK.broken || []).length || 1;
  setBadge("overview", ov, false);

  if(typeof DATA !== "undefined" && DATA && DATA.summary)
    setBadge("tasks", DATA.summary.bad || 0, false);

  if(REPOS && REPOS.available && REPOS.summary)
    setBadge("repos", REPOS.summary.attention || 0, false);

  // 存储:磁盘吃紧或记忆索引逼近硬上限。两者都是「还没坏但快了」,所以用警告色而不是红色。
  let st = 0, stWarn = true;
  if(SYS && SYS.disk && SYS.disk.verdict && SYS.disk.verdict.attention) st += 1;
  if(MEM && MEM.available && MEM.verdict && MEM.verdict.attention) st += 1;
  setBadge("storage", st, stWarn);
}

function tile(view, key, val, sub, cls, pct, pctWhat){
  let bar = "";
  if(pct != null && isFinite(pct)){
    // 超过 100 时钳到 100,但加 over 类长出一截斜纹尾巴 ——
    // 「刚好满」和「已经爆了」必须是两个形状,不能是同一根满条。
    const over = pct > 100;
    bar = `<span class="tb${over?" over":""}" title="${esc(pctWhat||"")}"
      ><i class="${cls||""}" style="width:${Math.min(100,Math.max(0,pct)).toFixed(1)}%"></i></span>`;
  }
  const tip = (sub||"") + (pctWhat ? " · " + pctWhat : "");
  return `<button class="tile" data-goto="${view}" title="${esc(tip)}">
    <span class="k">${esc(key)}</span>
    <span class="v ${cls||""}">${esc(String(val))}</span>
    ${bar}<span class="s">${esc(sub||"")}</span></button>`;
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

// 「这一条能不能一键处理」。分成两档不是为了好看:
// 一张清单里如果「你去看看」和「点一下就完了」混在一起,人只能逐条重新判断该干嘛,
// 而那正是这张清单本来要替他省掉的事。
// ⚠ 只有真的有通路的才给按钮。给一条其实没办法的行配一个按钮,
// 比不给更糟 —— 它承诺了一个不存在的出口。
const FIX_LAB = {commitpush:"提交并推送", run:"跑一次"};

function fixBtn(fix, arg){
  if(!fix) return "";
  return `<button class="fix" data-fix="${fix}" data-arg="${esc(arg)}"
    title="${esc(FIX_LAB[fix])}: ${esc(arg)}">${FIX_LAB[fix]}</button>`;
}

function renderTodo(){
  const box = $("todod"); if(!box) return;
  const rows = [];

  // 任务:sk 是任务模块自己的判定键,bad 这一档就是它判失败的那些。
  // 不在这里另立一套「怎么算失败」 : 两份规则一定会漂,而漂的时候两块内容都还在正常渲染,
  // 看不出哪一份是对的。
  if(typeof DATA !== "undefined" && DATA && Array.isArray(DATA.groups))
    DATA.groups.flatMap(g=>g.rows||[]).filter(r=>r.sk==="bad").forEach(r=>rows.push(
      {v:"tasks", src:"任务", nm:r.name, task:r.name, why:r.sl || "上次运行失败", sev:3,
       fix:"run"}));

  if(typeof DATA !== "undefined" && DATA && DATA.freshness && Array.isArray(DATA.freshness.tasks))
    // 状态集合来自后端 summary.attentionStates,不在这里再写一遍。
    // 两处各写一份的后果是它们会漂,而漂的时候两处都还在正常渲染。
    DATA.freshness.tasks.filter(r=>FR_ATT().indexOf(r.state) >= 0).forEach(r=>rows.push(
      // ⚠ 名字要带上 check。一个任务可以把好几件不相干的事折叠进来,每件各写一条声明,
      // 而它们在这里全叫同一个任务名 —— 光看名字只知道「这个任务有问题」,
      // 说不出是它底下哪一件。折叠带来的盲区就是在这一行被补上的。
      {v:"tasks", src:"产物", nm:r.check ? `${r.name} · ${r.check}` : r.name, task:r.name,
       why:(r.reasons && r.reasons[0]) ||
           (r.state==="never" ? "从未运行过" : r.state==="unknown" ? "查不成" : "产物过期"),
       sev:r.state==="down" ? 3 : 2}));

  if(REPOS && REPOS.available && Array.isArray(REPOS.repos))
    REPOS.repos.filter(r=>RP_ATT().indexOf(r.state) >= 0).forEach(r=>rows.push(
      // fix 是「这一条能不能一键处理」。只有 dirty / unpushed 能:它们的处理方式
      // 逐字相同,而且已经有一条带计划的两步通路。error(扫不动)不给 fix ——
      // 那是要人去看的,给它一个按钮等于假装这里有个办法。
      {v:"repos", src:"仓库", nm:r.name, why:REPO_WHY[r.state] || r.state, sev:2,
       fix:(r.state==="dirty"||r.state==="unpushed") ? "commitpush" : null}));

  // 摄入器停摆。这一条要出现在清单上,而不是只在顶栏当一个灰色时间串:
  // 顶栏那行是给已经在看的人的,清单才是「今天有什么要管」的那份答案,
  // 而摄入器停了的后果比清单上任何一条都大 —— 页面上每一个运行与健康数字都停在那一刻,
  // 且滚动日志过了窗口就永久没了。
  {
    const V = INGEST(), sev = INGEST_SEV(V.state);
    if(sev) rows.push({v:"tasks", src:"摄入", nm:"运行日志摄入",
                       why:V.why || ("摄入状态 " + V.state), sev:sev});
  }

  if(SYS && SYS.disk && SYS.disk.verdict && SYS.disk.verdict.attention)
    rows.push({v:"storage", src:"存储", nm:"系统盘",
               why:"已用 "+SYS.disk.usedPct+"%", sev:3});

  // available 只说「目录读到了」,不说 MEMORY.md 读到了。MEMORY.md 被改名或删掉时
  // linePct/bytePct 是 null,而 `||0` 把它折成 0,于是板子显示绿色的「0%」,
  // 副标题字面印出「null/200 行」。两条硬上限此刻完全没在量任何东西,
  // 而屏幕上说它离上限还有 100%。
  if(MEM && MEM.available && (MEM.linePct != null || MEM.bytePct != null)){
    const p = Math.max(MEM.linePct||0, MEM.bytePct||0);
    if(MEM.verdict && MEM.verdict.attention) rows.push({v:"storage", src:"存储", nm:"MEMORY.md",
      why:"索引 "+p+"%,逼近硬上限", sev:toneOf(MEM.verdict)==="bad"?3:2});
  }

  rows.sort((a,b)=> b.sev-a.sev || a.src.localeCompare(b.src));
  $("todon").textContent = rows.length ? rows.length + " 条" : "";
  // 主表读不到时不能说「没有要人管的事」。collect.ps1 挂掉(任务计划服务坏了、
  // 找不到 PowerShell、采集抛异常)时,默认打开的就是这一屏:六个指标格显示「未检查」,
  // 而这块明确写着一句肯定的绿色结论,来自一次根本没发生的检查。
  // 错误正文躺在另一个 hidden 的分区里,人要点进去才看得到。
  const dataBroken = (typeof DATA === "undefined") || !DATA || !Array.isArray(DATA.groups);
  if(dataBroken){
    box.innerHTML = '<div class="rw" style="cursor:default">'
      + '<span class="dot" style="background:var(--bad)"></span>'
      + '<span class="src">任务</span>'
      + '<span class="nm">读取失败</span>'
      + '<span class="why">任务数据没读到,这张清单不完整。明细在「任务」分区</span></div>';
    $("todon").textContent = "不完整";
    // 读不到时返回 1:徽章上有个数比没有数好。一个空徽章在「没事」和「没查成」
    // 之间不作区分,而这两件事恰好需要相反的反应。
    return 1;
  }
  if(!rows.length){
    box.innerHTML = '<div class="none">没有要人管的事</div>';
    return 0;
  }
  // 任务与产物两类行都指向某个具体任务,所以带上任务名,点了直接过滤到它。
  // 之前它们统一走 data-goto,而产物行的目标就是它自己所在的 overview,
  // 于是点了 hash 不变、什么也不发生 : 一个指向自己的跳转,和一个坏掉的跳转,
  // 在用的人那里是同一件事。
  // 逐字相同的原因合并成一行。只按 (src, why, sev) 三元组逐字比,不做任何归类判断:
  // 一旦开始判断「这两条算不算同一类」,就是在清单里第二次判定,而判定只能有一处
  // —— 这块代码上面每一段注释讲的都是同一件事。
  // 分隔符不能是空串：那样 ("A","1B") 和 ("A1","B") 会撞成同一个桶。
  const SEP = String.fromCharCode(1);
  const bucket = new Map();
  // ⚠ fix 也要进分桶键。少了它,一组里可能混进一条没有一键通路的行,
  // 而整组那个按钮是按第一条的 fix 画的 —— 于是按钮会替一条它处理不了的行做出承诺。
  rows.forEach(r=>{ const k = r.src+SEP+r.why+SEP+r.sev+SEP+(r.fix||"");
    if(!bucket.has(k)) bucket.set(k, []);
    bucket.get(k).push(r); });
  const out = [];
  bucket.forEach(g=>{ if(g.length>=3) out.push({grp:g}); else g.forEach(r=>out.push(r)); });
  out.sort((a,b)=>{ const x=a.grp?a.grp[0]:a, y=b.grp?b.grp[0]:b;
    return y.sev-x.sev || x.src.localeCompare(y.src); });

  box.innerHTML = out.map(o=>{
    if(o.grp){
      const g = o.grp, h = g[0];
      return `<div class="rw grp">
        <span class="dot" style="background:var(--${h.sev>=3?"bad":"warn"})"></span>
        <span class="src">${esc(h.src)}</span>
        <span class="why">${esc(h.why)} <b>${g.length}</b></span>
        <span class="chips">${g.map(m=>`<button class="chip" ${m.task
            ? `data-task="${esc(m.task)}"` : `data-goto="${m.v}"`
          }>${esc(m.nm)}</button>`).join("")}${
          // 整组一个按钮。逐个点十六次和「这一组我看过了,做吧」是两件事,
          // 而后者才是这张清单该提供的。它仍然要过一次确认,并逐仓各自出计划。
          h.fix ? `<button class="fix all" data-fixall="${h.fix}" data-args="${
            esc(g.map(m=>m.task||m.nm).join(""))}"
            title="${esc(FIX_LAB[h.fix])} 这 ${g.length} 个">${esc(FIX_LAB[h.fix])} ×${g.length}</button>` : ""
        }</span></div>`;
    }
    const r = o;
    return `<div class="rw" ${r.task
      ? `data-task="${esc(r.task)}"` : `data-goto="${r.v}"`}>
    <span class="dot" style="background:var(--${r.sev>=3?"bad":"warn"})"></span>
    <span class="src">${esc(r.src)}</span>
    <span class="nm" title="${esc(r.nm)}">${esc(r.nm)}</span>
    <span class="why">${esc(r.why)}${fixBtn(r.fix, r.task||r.nm)}</span></div>`;
  }).join("");
  // 条数返回给徽章。清单是这一屏唯一在回答「有哪些事」的东西,徽章只是它的投影 ——
  // 让徽章自己再数一遍,就是给同一个事实造第二个来源。
  return rows.length;
}
const REPO_WHY = {dirty:"有未提交改动", unpushed:"有未推送提交",
                  detached:"游离 HEAD", error:"读不出来"};

function renderTiles(){
  const el = $("tiles"); if(!el) return;
  const out = [];

  if(typeof DATA !== "undefined" && DATA && DATA.summary){
    const S = DATA.summary;
    out.push(tile("tasks","任务",S.total,`失败 ${S.bad} · 停用 ${S.disabled}`,S.bad?"bad":"ok",
      S.total?100*S.bad/S.total:null, S.total?`条里填的是失败占比 ${S.bad}/${S.total}`:null));
  } else out.push(tile("tasks","任务","-","未检查","idle"));

  // reason 要先判。没有健康清单时后端仍然返回一个 summary(total/bad 全是 0),
  // 所以「有没有 summary」这个条件永远成立,这一格拿到 bad=0 走绿色,
  // 印出「0 / 覆盖 0% · 共 0」 : 读起来是「零个产物过期」,实际上一个任务都没被检查。
  // 数值和副标题必须一起换掉:只把大字换成 "-" 而留着「共 0」,那个零照样像一个结论。
  if(typeof DATA !== "undefined" && DATA && DATA.freshness && DATA.freshness.reason){
    out.push(tile("#frbox","产物新鲜度","-", DATA.freshness.reason, "idle"));
  } else if(typeof DATA !== "undefined" && DATA && DATA.freshness && DATA.freshness.summary){
    const F = DATA.freshness.summary;
    // 这一格原来 data-goto="overview",而它自己就渲染在 overview 里:
    // 点了 showView 什么都不会变,唯一效果是滚回页顶。改成滚到灯板那一块。
    // 用 attention 不用 bad:attention 含 unknown(查不成),而查不成正是这块板子
    // 最该喊出来的那一类。bad 留给别处表示「判成坏的」。
    const fa = (F.attention != null) ? F.attention : F.bad;
    out.push(tile("#frbox","产物新鲜度",fa,
      `覆盖 ${Math.round((F.coverage||0)*100)}% · 共 ${F.total}`, fa?"bad":"ok",
      F.total?100*fa/F.total:null, F.total?`条里填的是要人管的占比 ${fa}/${F.total}`:null));
  } else out.push(tile("#frbox","产物新鲜度","-","未检查","idle"));

  if(REPOS && REPOS.available && REPOS.summary){
    const R = REPOS.summary;
    // unknownUpstream 在「这个根目录下没有 git 仓」那条分支上不存在,
    // 而这里原来无条件拼进去 —— 副标题印出「无上游 undefined」。
    // 后端已经补齐了那条分支,这里再兜一层:一个 undefined 印在屏幕上,
    // 比一个说不出来的空更糟,因为它看起来像一个值。
    const up = (R.unknownUpstream == null) ? "?" : R.unknownUpstream;
    out.push(tile("repos","仓库要人管",R.attention,
      `共 ${R.total} · 无上游 ${up}`, R.attention?"bad":"ok",
      R.total?100*R.attention/R.total:null,
      R.total?`条里填的是要人管的占比 ${R.attention}/${R.total}`:null));
  } else out.push(tile("repos","仓库","-","未检查","idle"));

  if(SYS && SYS.disk && SYS.disk.usedPct != null){
    const d = SYS.disk;
    out.push(tile("storage","磁盘已用",d.usedPct+"%",
      `剩 ${(d.free/1073741824).toFixed(0)}G`, toneOf(d.verdict),
      d.usedPct, "条里填的就是大字那个百分比"));
  } else out.push(tile("storage","磁盘","-","未检查","idle"));

  // ⚠ available 只表示**记忆池目录**读到了。MEMORY.md 不在或读不了时,
  // linePct / bytePct / indexLines / indexBytes 全是 null,而 available 仍然是 true。
  // 这里原来是 `MEM.linePct||0`,于是一个「索引读不到」的机器上印出的是
  // **绿色的 0%** 和字面量「null/200 行」—— 一个查不成的东西被画成了最健康的样子。
  // 同一个文件里 renderTodo 早就为此加了判空并写了一段注释,而这一格没跟着改:
  // **同一个坑修在了两处中的一处**,而两处的输入是同一个对象。
  if(MEM && MEM.available && (MEM.linePct != null || MEM.bytePct != null)){
    const p = Math.max(MEM.linePct||0, MEM.bytePct||0);
    // 取大的那个:两条上限哪条先撞都是撞。副标题要说出取的是哪一条,
    // 否则这个百分比对不上配置屏那两条独立的条,看起来像两处在打架。
    const pWhich = (MEM.linePct||0) >= (MEM.bytePct||0) ? "行数" : "字节";
    // 副标题必须跟着 pWhich 走。取字节口径时却印「150/200 行」,读的人按 150/200 心算
    // 是 75%,而大字写着 96.9% : 两个数字算不出彼此,而它们之间没有任何东西说明
    // 自己分属两条上限。
    const pFrac = (pWhich === "字节" && MEM.indexBytes != null)
      ? `${kb(MEM.indexBytes)}/${kb(MEM.hardBytes)}`
      : `${MEM.indexLines}/${MEM.hardLines} 行`;
    out.push(tile("storage","记忆索引",p+"%",
      `${pWhich} ${pFrac} · 冷 ${MEM.cold}`,
      toneOf(MEM.verdict),
      p, "条里填的就是大字那个百分比,超过 100 时右端长出斜纹"));
  } else if(MEM && MEM.available){
    // 目录读到了、索引没读到 —— 这不是「未检查」,是「查了但查不成」。
    out.push(tile("storage","记忆索引","?",
      esc(MEM.indexReason || "MEMORY.md 读不到"),"warn"));
  } else out.push(tile("storage","记忆索引","-","未检查","idle"));

  if(CONVOS && CONVOS.available && CONVOS.summary){
    const C2 = CONVOS.summary;
    out.push(tile("convos","对话",C2.files,`真人 ${C2.humanish} · ${C2.groups} 个目录`,""));
  } else out.push(tile("convos","对话","-","未检查","idle"));

  el.innerHTML = out.join("");
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
  const body=rows.map(r=>{
    const cells=days.map((d,i)=>{
      const td=i===last?"today":"";
      const c=r.hist.byDay[d];
      if(!c||!c.n) return `<td class="${td}" title="${esc(r.name)} ${d} 无观察"></td>`;
      const j=c.n-c.neutral;
      if(j<=0) return `<td class="${td}" title="${esc(r.name)} ${d} 仅中性观察"></td>`;
      const p=c.ok/j, k=p>=.99?"h2":p>=.9?"h1":p>=.6?"h3":p>=.25?"h4":"h5";
      return `<td class="${k} ${td}" title="${esc(r.name)} ${d} 正常 ${c.ok}/${j}${c.bad?" 失败 "+c.bad:""}${c.stale?" 陈旧 "+c.stale:""}"></td>`;
    }).join("");
    return `<tr><th class="tn" title="健康率 ${r.hist.health==null?"-":r.hist.health+"%"}">${esc(r.name)}</th>${cells}</tr>`;
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
