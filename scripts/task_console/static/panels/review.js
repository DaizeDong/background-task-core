// Group owner verdicts by affected object; filters never recalculate health.
let REVIEW_FILTER="all", REVIEW_QUERY="";
// 清单的总数和其中判为异常(红)的对象数,侧栏徽章按它定颜色。筛选不改它:徽章说的是全部。
let REVIEW_TOTALS={objects:0,bad:0};
function renderReviewQueue(rows,dataBroken){
  const box=$("todod");
  if(dataBroken){
    $("todon").textContent="清单不完整";
    box.innerHTML='<p class="review-notice">任务读取失败，请刷新或查看计划任务。</p>';
    return 1;
  }
  const groups=new Map();
  rows.forEach(row=>{
    const key=JSON.stringify([row.v,row.task || row.nm]);
    if(!groups.has(key)) groups.set(key,{...row,reasons:[],sev:row.sev});
    const group=groups.get(key);group.sev=Math.max(group.sev,row.sev);
    if(row.fix && !group.fix) group.fix=row.fix;
    const reason=(row.src==="产物" && row.nm!==row.task ? row.nm.slice(row.task.length+3)+"：" : "")+row.why;
    if(!group.reasons.includes(reason)) group.reasons.push(reason);
  });
  const all=[...groups.values()].sort((a,b)=>b.sev-a.sev || a.nm.localeCompare(b.nm));
  const query=REVIEW_QUERY.trim().toLowerCase();
  const selected=all.filter(row=>(REVIEW_FILTER==="all" || row.v===REVIEW_FILTER || REVIEW_FILTER==='storage' && row.v==='resources') &&
    (!query || JSON.stringify([row.nm,row.description,row.reasons]).toLowerCase().includes(query)));
  REVIEW_TOTALS={objects:all.length,bad:all.filter(row=>row.sev>=3).length};
  $("todon").textContent=`${all.length} 个对象`;
  $("review-count").textContent=`${selected.length}/${all.length} 个对象 · ${rows.length} 条检查`;
  box.innerHTML=selected.map(row=>`<article class="review-row">
    ${statusBadge(row.sev>=3?'异常':'提示',row.sev>=3?'bad':'warn',undefined,'review-status')}
    <div class="review-object"><h3>${esc(row.task || row.nm)}</h3>
    <ul>${row.reasons.map(reason=>`<li>${esc(reason)}</li>`).join("")}</ul></div>
    <div class="review-actions">
    <button class="icon-only" ${row.task?`data-task="${esc(row.task)}"`:row.v==="repos"?`data-review-repo="${esc(row.nm)}"`:`data-goto="${esc(row.v)}"`} title="查看详情"><svg class="ic" aria-hidden="true"><use href="#i-eye"/></svg><span class="control-label">查看详情</span></button></div>
    </article>`).join("") || '<p class="review-empty">没有符合筛选条件的技术问题</p>';
  return all.length;
}
function reviewClick(event){
  const repo=event.target.closest("[data-review-repo]");
  if(repo){showView("repos",true);RP_SEL=repo.dataset.reviewRepo;renderRepoList();}
}
// 问题搜索里按 Enter:只剩一个对象时等于点它的「查看详情」,去同一个地方。
function openOnlyReview(){
  const rows=[...document.querySelectorAll("#todod .review-row")];
  if(rows.length!==1) return;
  const open=rows[0].querySelector(".review-actions button");
  if(open) open.click();
}

// ── 技术问题与数据来源这一屏的挂点和渲染 ──(从 events.js、profile.js、tasks.js 搬来:
// 数据来源自检、产物新鲜度灯板和数据那一行都只画在诊断这一屏上,放在这里,诊断的包只改这一个文件。)
function startDiagnostics(){
  $('review-search').addEventListener('input',event=>{REVIEW_QUERY=event.target.value;renderTodo();});
  $("review-filter").addEventListener("change",event=>{REVIEW_FILTER=event.target.value;renderTodo();});
  $("scktog").addEventListener("click",()=>{const open=$("sckd").classList.toggle("on");setIconControl($("scktog"),open?'i-up':'i-down',open?'收起检查':'展开检查');});
}
let SCK=null;
async function loadSelfcheck(){
  try{ SCK=await api("/api/selfcheck"); }
  catch(e){ $("scksum").innerHTML=`<span class="bad">数据来源检查失败：${esc(e.message)}</span>`; return; }
  renderSelfcheck();
  updateBadges();
}
function renderSelfcheck(){
  if(!SCK) return;
  $("sckdots").innerHTML=SCK.rows.map(r=>
    `<i class="d ${r.state}" title="${esc(r.title)}: ${esc(r.state)}${r.why?" ("+esc(r.why)+")":""}"></i>`
  ).join("");
  const c=SCK.counts||{};
  const parts=[`读到 ${SCK.probed}/${SCK.total}`];
  if(c.unset) parts.push(`未配 ${c.unset}`);
  if(c.stale) parts.push(`未更新 ${c.stale}`);
  $("scksum").innerHTML = SCK.ok
    ? `<span>${parts.join(" · ")}</span>`
    : `<span class="bad">${SCK.broken.length?("读不到 "+SCK.broken.join(", ")):"必需来源不可用"}</span>`
      +` <span>· ${parts.join(" · ")}</span>`;
  $("sckd").innerHTML=SCK.rows.map(r=>`<div class="r">
    <i class="d ${r.state}"></i>
    <span>${esc(r.title)}</span>
    <span class="p" title="${esc(r.path||r.why||"")}">${esc(r.path||r.why||"")}</span>
    <span class="w">${r.state==="ok"||r.state==="stale"
      ? (r.ageHours!=null? r.ageHours.toFixed(1)+"h":"")+(r.entries!=null?" · "+r.entries+" 项":"")
      : esc(r.state)}</span></div>`).join("");
}

// 诊断屏底部「数据」那一行和计划任务的读取警告。数据取自 /api/tasks,所以由 tasks.js 的 render() 调用。
function renderDataLine(){
  const S=DATA.summary;
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
    ? `<div class="fr-off">${bad.length} 项未通过检查，原因见上方「技术问题」</div>`
    : `<div class="fr-none">所有已检查的输出文件均按时更新</div>`;
}
