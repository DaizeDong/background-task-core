// Group owner verdicts by affected object; filters never recalculate health.
let REVIEW_FILTER="all", REVIEW_QUERY="";
const VIEW_COPY={overview:"审查概览",pipelines:"流水线",tasks:"计划任务",repos:"仓库",
  storage:"配置与存储",convos:"会话记录",llm:"模型调用"};
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
  $("todon").textContent=`${all.length} 个对象`;
  $("review-count").textContent=`${selected.length}/${all.length} 个对象 · ${rows.length} 条检查`;
  box.innerHTML=selected.map(row=>`<article class="review-row">
    ${statusBadge(row.sev>=3?'异常':'提示',row.sev>=3?'bad':'warn',undefined,'review-status')}
    <div class="review-object"><h3>${esc(row.task || row.nm)}</h3>
    <ul>${row.reasons.map(reason=>`<li>${esc(reason)}</li>`).join("")}</ul></div>
    <div class="review-actions">
    <button ${row.task?`data-task="${esc(row.task)}"`:row.v==="repos"?`data-review-repo="${esc(row.nm)}"`:`data-goto="${esc(row.v)}"`}>查看详情</button></div>
    </article>`).join("") || '<p class="review-empty">没有符合筛选条件的技术问题</p>';
  return all.length;
}
function updateViewHeading(key){
  const title=VIEW_COPY[key] || VIEW_COPY.overview;
  $("view-title").textContent=title;document.title=title+" · 基建维护台";
}
function reviewClick(event){
  const repo=event.target.closest("[data-review-repo]");
  if(repo){showView("repos",true);RP_SEL=repo.dataset.reviewRepo;renderRepoList();}
  if(event.target.closest("#page-help")) $("help").hidden=false;
}
