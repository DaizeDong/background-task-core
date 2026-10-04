// Group owner verdicts by affected object; filters never recalculate health.
let REVIEW_FILTER="all", REVIEW_QUERY="";
// 清单的总数和其中判为异常(红)的对象数,侧栏徽章按它定颜色。筛选不改它:徽章说的是全部。
let REVIEW_TOTALS={objects:0,bad:0};
const REVIEW_SCOPES={tasks:"任务与产物",repos:"仓库",storage:"存储与记忆"};
// 范围筛选记在本机浏览器里,下次打开还是这个范围;搜索词不记:一个上次留下的搜索词会让清单
// 悄悄少掉几条,而人回来时不会想到去看搜索框。存不进去(隐私窗口、禁用存储)就照旧从「全部」开始。
const REVIEW_FILTER_KEY="tc.review-filter";
let REVIEW_FILTER_SAVED=null;
function rememberReviewFilter(){
  if(REVIEW_FILTER===REVIEW_FILTER_SAVED) return;
  REVIEW_FILTER_SAVED=REVIEW_FILTER;
  try{ localStorage.setItem(REVIEW_FILTER_KEY,REVIEW_FILTER); }catch(e){}
}

// 后端给的原因是写给排查的人看的:退出码的十六进制、带一位小数的小时数、异常类名。
// 这里只换说法,不改结论,原文放进悬停。这些字符串由 health.py 和 server.py 的 status_of 生成,
// 那边还有别的读者,所以在显示这一层改,不去动生成它们的地方。
function hoursText(hours){
  // 和 fmtTime 同一条线:48 小时以内说小时,再往前说天。
  if(hours>=48) return `${Math.floor(hours/24)} 天`;
  if(hours>=1) return `${Math.round(hours)} 小时`;
  return `${Math.max(1,Math.round(hours*60))} 分钟`;
}
function humanReason(raw){
  const text=String(raw ?? "");
  const out=text
    .replace(/^失败 (0x[0-9a-f]+|\?)$/i,(m,code)=>{
      if(code==="?") return "上次运行出错（退出码未知）";
      const n=parseInt(code,16);
      // 小的是程序自己的退出码,大的是 Windows 的错误码,后者写成十进制没人认得,保留原样。
      return n<=0xffff?`上次运行出错（退出码 ${n}）`:`上次运行出错（错误码 ${code}）`;
    })
    .replace(/^退出码 (\S+) 不在允许集 \[([^\]]*)\]$/,(m,code,ok)=>`上次运行出错（退出码 ${code}，正常应为 ${ok.replace(/\s*,\s*/g,"、")}）`)
    .replace(/^读不到产物[:：]\s*FileNotFoundError$/,"输出文件找不到")
    .replace(/^读不到产物[:：]\s*\S+$/,"输出文件读不出来")
    .replace(/(^|[^\w.])(\d+(?:\.\d+)?)h(?![A-Za-z])( 前)?/g,(m,lead,hours,ago)=>lead+hoursText(Number(hours))+(ago?"前":""))
    .replace(/^产物/,"输出文件");
  // 同一次失败常有两条说法:计划任务报「失败 0x1」,健康清单报「退出码 1 不在允许集 [0]」。
  // 换成人话以后两条都是「上次运行出错（退出码 1…）」,按退出码认成一件事,只留先到的那条。
  const code=out.match(/^上次运行出错（退出码 (-?\d+)(?=[，）])/);
  return {text:out,raw:out===text?"":text,key:code?"exit "+code[1]:out};
}

// 「这一条能不能一键处理」。分成两档不是为了好看:
// 一张清单里如果「你去看看」和「点一下就完了」混在一起,人只能逐条重新判断该干嘛,
// 而那正是这张清单本来要替他省掉的事。
// ⚠ 只有真的有通路的才给按钮。给一条其实没办法的行配一个按钮,
// 比不给更糟 —— 它承诺了一个不存在的出口。
const FIX_LAB = {commitpush:"提交并推送", run:"跑一次"};

// 按钮是带字的「跑一次」「提交并推送」,排在「查看详情」前面。点击由 events.js 第一个监听里的 [data-fix] 接走,
// 它排在整行跳转之前,所以点按钮只做这件事、不会顺带把人带去别的页;只读预览里由 ConsoleActions 灰掉并说明原因。
function fixBtn(fix, arg){
  if(!fix) return "";
  return `<button type="button" class="fix" data-fix="${fix}" data-arg="${esc(arg)}"
    title="${esc(FIX_LAB[fix])}: ${esc(arg)}">${FIX_LAB[fix]}</button>`;
}

// 清单的三种样子由 status 决定:pending 还在读任务、failed 任务读不到、loaded 读到了。
// 老的调用方传一个布尔(true 表示读不到),照旧认。
function renderReviewQueue(rows,status){
  const box=$("todod");
  const st=status===true?{state:"failed"}:status && typeof status==="object"?status:{state:"loaded"};
  if(st.state==="pending"){
    $("todon").textContent="";
    $("review-count").textContent="正在读取检查结论";
    box.innerHTML=loadingBlock("任务");
    return 0;
  }
  if(st.state==="failed"){
    $("todon").textContent="清单不完整";
    $("review-count").textContent="任务读不到时，这张清单缺了任务和输出文件两类";
    box.innerHTML=errorBlock("任务",st.reason || "",'data-review-retry');
    return 1;
  }
  rememberReviewFilter();
  const groups=new Map();
  rows.forEach(row=>{
    const key=JSON.stringify([row.v,row.task || row.nm]);
    if(!groups.has(key)) groups.set(key,{...row,reasons:[],sev:row.sev,failed:false});
    const group=groups.get(key);group.sev=Math.max(group.sev,row.sev);
    if(row.fix && !group.fix) group.fix=row.fix;
    if(row.key==="tasks") group.failed=true;
    const why=humanReason(row.why);
    const check=row.src==="产物" && row.nm!==row.task ? row.nm.slice(row.task.length+3)+"：" : "";
    const reason={text:check+why.text,raw:why.raw?check+why.raw:"",key:check+why.key};
    if(!group.reasons.some(seen=>seen.key===reason.key)) group.reasons.push(reason);
  });
  // 标题用主人认得的那个名字:任务表里的中文标题(taskText),机器名退到下面一行小字。
  const known=name=>name && typeof ROWS!=="undefined" ? ROWS.find(row=>row.name===name) : null;
  const all=[...groups.values()].map(row=>{
    const info=known(row.task);
    return {...row,info,title:info?taskText(info).title:(row.task || row.nm)};
  }).sort((a,b)=>b.sev-a.sev || a.title.localeCompare(b.title));
  const query=REVIEW_QUERY.trim().toLowerCase();
  const selected=all.filter(row=>(REVIEW_FILTER==="all" || row.v===REVIEW_FILTER || REVIEW_FILTER==='storage' && row.v==='resources') &&
    (!query || JSON.stringify([row.nm,row.title,row.description,row.reasons]).toLowerCase().includes(query)));
  REVIEW_TOTALS={objects:all.length,bad:all.filter(row=>row.sev>=3).length};
  $("todon").textContent=`${all.length} 个对象`;
  const pending=st.pending || [], failed=st.failed || [];
  const scope=REVIEW_SCOPES[REVIEW_FILTER];
  $("review-count").innerHTML=(scope?`<b>仅显示：${esc(scope)}</b> · `:"")
    +esc(matchCount(selected.length,all.length,"个对象")+` · ${rows.length} 条检查`)
    +(pending.length?` · ${esc(pending.join("/"))}仍在读取`:"")
    +(failed.length?` · <span class="bad">${esc(failed.join("/"))}读取失败</span>`:"");
  box.innerHTML=selected.map(row=>{
    const when=row.info?(row.info.lastRun?`${row.failed?"上次失败":"上次运行"} ${timeTag(row.info.lastRun)}`:"从未运行"):"";
    const open=row.task?`data-task="${esc(row.task)}"`:row.v==="repos"?`data-review-repo="${esc(row.nm)}"`:`data-goto="${esc(row.v)}"`;
    return `<article class="review-row">
    ${statusBadge(row.sev>=3?'异常':'提示',row.sev>=3?'bad':'warn',undefined,'review-status')}
    <div class="review-object"><h3>${esc(row.title)}</h3>${row.task && row.title!==row.task?`<code class="review-id">${esc(row.task)}</code>`:""}
    <ul>${row.reasons.map(reason=>`<li${reason.raw?` title="${esc("原文："+reason.raw)}"`:""}>${esc(reason.text)}</li>`).join("")}</ul>
    ${when?`<p class="review-when">${when}</p>`:""}</div>
    <div class="review-actions">${fixBtn(row.fix,row.task || row.nm)}
    <button class="icon-only" data-review-open ${open} title="查看详情"><svg class="ic" aria-hidden="true"><use href="#i-eye"/></svg><span class="control-label">查看详情</span></button></div>
    </article>`;
  }).join("") || (all.length
    ? emptyBlock("没有符合筛选条件的技术问题",{filtered:"diagnostics"})
    : emptyBlock(pending.length?`已读到的来源里没有技术问题，${pending.join("/")}仍在读取`:"没有要处理的技术问题"));
  return all.length;
}
// 点格子、灯板图例或「技术问题」链接:在这一屏里把清单切到那一类,再把清单带到眼前。
function setReviewFilter(value){
  REVIEW_FILTER=REVIEW_SCOPES[value]?value:"all";
  $("review-filter").value=REVIEW_FILTER;
  renderTodo();
  const card=$("todo");
  card.scrollIntoView?.({block:"start",behavior:"smooth"});
  // 焦点交给清单本身:用键盘点的人接下来按 Tab 就进到第一行,不用从页顶再走一遍。
  if(card.tabIndex!==0) card.tabIndex=-1;
  card.focus?.({preventScroll:true});
}
function reviewClick(event){
  const scope=event.target.closest("[data-review-filter]");
  if(scope){setReviewFilter(scope.dataset.reviewFilter);return;}
  if(event.target.closest("[data-review-retry]")){load();renderTodo();return;}
  const repo=event.target.closest("[data-review-repo]");
  if(repo){showView("repos",true);RP_SEL=repo.dataset.reviewRepo;renderRepoList();return;}
  // 整行都能点,去处和行尾的「查看详情」一样。行里的按钮(跑一次、提交并推送、查看详情)各走各的,
  // 选中了一段文字也不算点击:人可能只是想复制那个任务名。
  const row=event.target.closest("#todod .review-row");
  if(!row || event.target.closest("button,a,input,select,.review-actions")) return;
  if(String(window.getSelection?.() || "")) return;
  row.querySelector("[data-review-open]")?.click();
}
// 问题搜索里按 Enter:只剩一个对象时等于点它的「查看详情」,去同一个地方。
function openOnlyReview(){
  const rows=[...document.querySelectorAll("#todod .review-row")];
  if(rows.length!==1) return;
  const open=rows[0].querySelector("[data-review-open]");
  if(open) open.click();
}

// ── 技术问题与数据来源这一屏的挂点和渲染 ──(从 events.js、profile.js、tasks.js 搬来:
// 数据来源自检、产物新鲜度灯板和数据那一行都只画在诊断这一屏上,放在这里,诊断的包只改这一个文件。)
function startDiagnostics(){
  let stored=null;
  try{ stored=localStorage.getItem(REVIEW_FILTER_KEY); }catch(e){}
  if(REVIEW_SCOPES[stored]){ REVIEW_FILTER=stored; $("review-filter").value=stored; }
  REVIEW_FILTER_SAVED=REVIEW_FILTER;
  $('review-search').addEventListener('input',event=>{REVIEW_QUERY=event.target.value;renderTodo();});
  $("review-filter").addEventListener("change",event=>{REVIEW_FILTER=event.target.value;renderTodo();});
  $("scktog").addEventListener("click",()=>{
    SCK_OPEN=!sckOpen();
    try{ localStorage.setItem("tc.sck",SCK_OPEN?"open":"closed"); }catch(e){}
    syncSckPanel();
  });
  syncSckPanel();
  // 第一份数据回来之前就把「读取中」画出来。不画的话格子和清单是几秒钟的空白卡片,
  // 而空白读起来既像「没有问题」也像「坏了」。
  renderTiles();renderTodo();
}
let SCK=null;
// 数据来源自检默认收起:三十行路径是排查时才看的东西,不该天天占着这一屏。
// 有来源读不到时自动展开,那时它正是要看的东西;人在这一次访问里自己开合过,就听人的。
// 开合记在本机浏览器里(tc.sck),只在一切正常时生效。读不到的来源同时写在上面那行红字里,收起时也看得见。
let SCK_OPEN=null;
function sckTrouble(){ return !!SCK && (!SCK.ok || (SCK.rows||[]).some(r=>r.state==="missing")); }
function sckOpen(){
  if(SCK_OPEN!=null) return SCK_OPEN;
  if(sckTrouble()) return true;
  try{ return localStorage.getItem("tc.sck")==="open"; }catch(e){ return false; }
}
function syncSckPanel(){
  const open=sckOpen();
  $("sckd").className=open?"on":"";
  setIconControl($("scktog"),open?'i-up':'i-down',open?'收起检查':'展开检查');
  $("scktog").setAttribute("aria-expanded",String(open));
}
async function loadSelfcheck(){
  try{ SCK=await api("/api/selfcheck"); }
  catch(e){ $("scksum").innerHTML=`<span class="bad">数据来源检查失败：${esc(e.message)}</span>`; return; }
  renderSelfcheck();
  updateBadges();
}
// 自检行的名字。后端有几行还是英文,和旁边的中文名排在一起像两套东西,这里统一成中文;没列的照用后端的。
const SCK_LABEL={component_status:"组件运行观察",source_catalog:"组件来源目录",allowlist:"备份白名单",
  skills:"技能目录",skill_archive:"技能归档区"};
const SCK_STATE={ok:"正常",stale:"未更新",missing:"读不到",unset:"未配置"};
function renderSelfcheck(){
  if(!SCK) return;
  const label=r=>SCK_LABEL[r.key] || r.title;
  $("sckdots").innerHTML=SCK.rows.map(r=>
    `<i class="d ${r.state}" title="${esc(label(r))}: ${esc(SCK_STATE[r.state] || r.state)}${r.why?" ("+esc(r.why)+")":""}"></i>`
  ).join("");
  const c=SCK.counts||{};
  const parts=[`读到 ${SCK.probed}/${SCK.total}`];
  if(c.unset) parts.push(`未配 ${c.unset}`);
  if(c.stale) parts.push(`未更新 ${c.stale}`);
  const name=key=>{const r=SCK.rows.find(row=>row.key===key);return r?label(r):key;};
  $("scksum").innerHTML = SCK.ok
    ? `<span>${parts.join(" · ")}</span>`
    : `<span class="bad">${SCK.broken.length?("读不到 "+esc(SCK.broken.map(name).join("、"))):"必需来源不可用"}</span>`
      +` <span>· ${parts.join(" · ")}</span>`;
  // 路径只进悬停:三十行从左边截断的完整路径,读的人只能看到一串目录名的尾巴。
  // 不正常的行把原因写出来(为什么读不到),那才是排查时要看的。
  $("sckd").innerHTML=SCK.rows.map(r=>{
    const ok=r.state==="ok"||r.state==="stale";
    const facts=ok
      ? [r.ageHours!=null?hoursText(r.ageHours)+"前更新":"",r.entries!=null?`${fmtNum(r.entries)} 项`:"",
         r.state==="stale"?"未更新":""].filter(Boolean).join(" · ")
      : (SCK_STATE[r.state] || r.state);
    return `<div class="r" title="${esc(r.path || r.why || "")}">
    <i class="d ${r.state}"></i>
    <span>${esc(label(r))}</span>
    <span class="w">${esc(facts)}</span>${!ok && r.why?`<span class="p">${esc(r.why)}</span>`:""}</div>`;
  }).join("");
  syncSckPanel();
}

// 诊断屏底部「数据」那一块和计划任务的读取警告。数据取自 /api/tasks,所以由 tasks.js 的 render() 调用。
// 它原来是顶栏的一行字,搬下来以后样式还挂在 #bar 底下,于是名字和值挤成一串
// 「数据2026-… 观察45d 最后摄入 … 运行日志279921」。现在是一张两列的小表:左边名字,右边值。
function renderDataLine(){
  const S=DATA.summary;
  const facts=[["数据时间",timeTag(S.generated)],
    ["观察天数",(DATA.history&&DATA.history.available)?`${fmtNum(DATA.history.days.length)} 天`:`<span class="faint">无观察</span>`]];
  // 摄入器最后一次跑成没跑成,原来查出来了却在 server 里被丢掉:数据库存在但摄入器已经
  // 连续失败几周时,页面照常显示 available=true、热力图照画、健康% 仍是一个具体数字,
  // 而**没有任何一处告诉人这些数字最后一次更新是什么时候**。
  const ingest=(()=>{
    // lastIngest 是 {来源: {at, ok}} 的字典,不是一个时间串。第一版直接 String() 它,
    // 印出「最后摄入 [object Object]」 : 一个显示出来却读不懂的字段,和没有这个字段差不多。
    const li = DATA.history && DATA.history.lastIngest;
    if(!li || typeof li !== "object") return "";
    const rows = Object.entries(li).filter(([,v])=>v && v.at);
    if(!rows.length) return "";
    // 取最旧的那一条:摄入器是几条流水线,任何一条停了这些数字就有一部分停在那一刻。
    rows.sort((a,b)=>String(a[1].at).localeCompare(String(b[1].at)));
    const oldest = rows[0][1];
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
                   ? ` · ${hoursText(V.ageHours)}没摄入`
               : V.state === "unknown" ? " · 时间读不出来" : "";
    return `<span style="${col?`color:${col}`:""}" title="${esc(tip)}">${esc(fmtTime(oldest.at))}${tail}</span>`;
  })();
  if(ingest) facts.push(["最后摄入",ingest]);
  facts.push(["运行日志条数",(()=>{
    const R=DATA.runlog;
    if(!R||!R.available) return `<span class="faint">无运行日志</span>`;
    // 读不懂的条数要跟着 count 一起显示。它们一直在被数,但以前只留在后端:
    // 事件格式一变、大批事件被丢掉时,页面上只看得到运行次数变少、成功率漂移,
    // 没有任何一处说明有多少条读不懂 —— 一个看起来精确、实则不完整的数字,
    // 而它旁边那个 count 还在替它背书。
    const bad = R.partial || (R.dropped>0);
    const tip = (R.countScope||"")
      + (R.dropped>0?` · 有 ${R.dropped} 条事件解析不了,没有计入`:"")
      + (R.partial?" · 日志读到一半失败,下面的条数是不完整的":"");
    return `<span style="${bad?"color:var(--warn)":""}" title="${esc(tip)}">${fmtNum(R.count)} 条${
      R.countScope?` <span class="faint">${esc(R.countScope)}</span>`:""}${
      R.dropped>0?` · 读不懂 ${fmtNum(R.dropped)}`:""}${R.partial?" · 不完整":""}</span>`;
  })()]);
  $("s-src").innerHTML=`<dl class="data-facts">${facts.map(([k,v])=>`<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
  // 含「历史」的那条警告不在这里重复:热力图不可用时会把它印在图的标题上(renderHeat),
  // 贴在图上比躺在警告堆里有用 : 人是在图空着的时候才想知道为什么空。
  $("warns").innerHTML=(DATA.warnings||[])
    .filter(w=>w.indexOf("历史")<0)
    .map(w=>`<div class="warn-line">${esc(w)}</div>`).join("");
}

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

  // 要人管的那几类(失败、未跑过、查不成)在图例上是按钮:点了把左边的清单切到「任务与产物」,
  // 和点「输出文件」那一格一样。其余几类只是图例,点了也没有对应的清单可看。
  $("frkey").innerHTML=FR_ORDER.filter(k=>counts[k]).map(k=>{
    const item=`<i class="fr-c s-${k}"></i>${FR_LABEL[k]} ${counts[k]}`;
    return FR_ATT().indexOf(k) >= 0
      ? `<button type="button" class="link-button" data-review-filter="tasks" title="在「技术问题」里只看任务与产物">${item}</button>`
      : `<span>${item}</span>`;
  }).join("");

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
    // 指路不写方位:宽屏上清单在左边,窄屏上在上面,写「上方」在宽屏上就是错的。直接做成链接,点了切过去。
    ? `<div class="fr-off">${bad.length} 项未通过检查，原因见<button type="button" class="link-button" data-review-filter="tasks">「技术问题」列表</button></div>`
    : `<div class="fr-none">所有已检查的输出文件均按时更新</div>`;
}
