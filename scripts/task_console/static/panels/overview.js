// Classic script module; loaded in app.js dependency order.
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
  el.title = `${n} 项技术问题，点击查看`;
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
  setBadge("diagnostics", ov, false);
  if(typeof renderPlatformSignals === "function") { renderPlatformSignals(); renderAutomations(); }

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
      {v:"tasks", src:"任务", nm:r.name, task:r.name, description:r.desc, why:r.sl || "上次运行失败", sev:3,
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
    if(MEM.verdict && MEM.verdict.attention) rows.push({v:"resources", src:"存储", nm:"MEMORY.md",
      why:"索引 "+p+"%,逼近硬上限", sev:toneOf(MEM.verdict)==="bad"?3:2});
  }

  const dataBroken = !DATA || !Array.isArray(DATA.groups);
  return renderReviewQueue(rows, dataBroken);
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
    out.push(tile("#frbox","输出文件检查","-", DATA.freshness.reason, "idle"));
  } else if(typeof DATA !== "undefined" && DATA && DATA.freshness && DATA.freshness.summary){
    const F = DATA.freshness.summary;
    // 这一格原来 data-goto="overview",而它自己就渲染在 overview 里:
    // 点了 showView 什么都不会变,唯一效果是滚回页顶。改成滚到灯板那一块。
    // 用 attention 不用 bad:attention 含 unknown(查不成),而查不成正是这块板子
    // 最该喊出来的那一类。bad 留给别处表示「判成坏的」。
    const fa = (F.attention != null) ? F.attention : F.bad;
    out.push(tile("#frbox","输出文件异常",fa,
      `覆盖 ${Math.round((F.coverage||0)*100)}% · 共 ${F.total}`, fa?"bad":"ok",
      F.total?100*fa/F.total:null, F.total?`异常或无法检查 ${fa}/${F.total}`:null));
  } else out.push(tile("#frbox","输出文件检查","-","未检查","idle"));

  if(REPOS && REPOS.available && REPOS.summary){
    const R = REPOS.summary;
    // unknownUpstream 在「这个根目录下没有 git 仓」那条分支上不存在,
    // 而这里原来无条件拼进去 —— 副标题印出「无上游 undefined」。
    // 后端已经补齐了那条分支,这里再兜一层:一个 undefined 印在屏幕上,
    // 比一个说不出来的空更糟,因为它看起来像一个值。
    const up = (R.unknownUpstream == null) ? "?" : R.unknownUpstream;
    out.push(tile("repos","仓库待检查",R.attention,
      `共 ${R.total} · 无上游 ${up}`, R.attention?"bad":"ok",
      R.total?100*R.attention/R.total:null,
      R.total?`存在改动或读取问题 ${R.attention}/${R.total}`:null));
  } else out.push(tile("repos","仓库","-","未检查","idle"));

  if(SYS && SYS.disk && SYS.disk.usedPct != null){
    const d = SYS.disk;
    out.push(tile("storage","磁盘已用",d.usedPct+"%",
      `剩 ${(d.free/1073741824).toFixed(0)}G`, toneOf(d.verdict),
      d.usedPct, "磁盘已用空间占比"));
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
    out.push(tile("resources","记忆索引",p+"%",
      `${pWhich} ${pFrac} · 已归档 ${MEM.cold}`,
      toneOf(MEM.verdict),
      p, "索引额度使用率，超过 100% 时显示斜纹"));
  } else if(MEM && MEM.available){
    // 目录读到了、索引没读到 —— 这不是「未检查」,是「查了但查不成」。
    out.push(tile("resources","记忆索引","?",
      esc(MEM.indexReason || "MEMORY.md 读不到"),"warn"));
  } else out.push(tile("resources","记忆索引","-","未检查","idle"));

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
    return `<tr><th class="tn" title="检查通过率 ${r.hist.health==null?"-":r.hist.health+"%"}">${esc(r.name)}</th>${cells}</tr>`;
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
