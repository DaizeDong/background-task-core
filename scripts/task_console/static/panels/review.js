// Review layout: reuse owner verdicts and group evidence by the exact affected object.
let REVIEW_FILTER="all", REVIEW_LIMIT=6;
const VIEW_COPY={
  overview:["审查概览","先处理异常，再沿流水线核对证据。"],
  pipelines:["流水线","查看每个环节做什么、有什么证据、还缺什么。"],
  tasks:["计划任务","查看运行结果和下次触发时间，展开任务后处理。"],
  repos:["仓库","检查未提交改动、未推送提交和仓库状态。"],
  storage:["配置与存储","查找组件来源，管理技能、插件、记忆与磁盘。"],
  convos:["会话记录","按项目查找历史对话。"],
  llm:["模型调用","查看调用结果与用量，按需调整 llmcall 路由。"]
};

function renderReviewQueue(rows, dataBroken){
  const box=$("todod");
  if(dataBroken){
    $("todon").textContent="清单不完整";
    box.innerHTML='<p class="review-notice">任务数据读取失败，暂时无法列出全部问题。请到「计划任务」查看原因。</p>';
    return 1;
  }
  const groups=new Map();
  rows.forEach(row=>{
    const key=JSON.stringify([row.v,row.task || row.nm]);
    if(!groups.has(key)) groups.set(key,{...row,reasons:[],sev:row.sev});
    const group=groups.get(key); group.sev=Math.max(group.sev,row.sev);
    const reason=(row.src==="产物" && row.nm!==row.task ? row.nm.slice(row.task.length+3)+"：" : "")+row.why;
    if(!group.reasons.includes(reason)) group.reasons.push(reason);
  });
  const all=[...groups.values()].sort((a,b)=>b.sev-a.sev || a.nm.localeCompare(b.nm));
  const selected=all.filter(row=>REVIEW_FILTER==="all" || row.v===REVIEW_FILTER);
  $("todon").textContent=`${all.length} 个对象`;
  const meta=$("review-count"); if(meta) meta.textContent=`${rows.length} 条检查结论，按对象合并；显示 ${Math.min(REVIEW_LIMIT,selected.length)}/${selected.length}`;
  box.innerHTML=selected.slice(0,REVIEW_LIMIT).map(row=>{
    const title=(row.description || row.task || row.nm).split(/[，,。；;\n]|:(?!\d)/)[0];
    const sync=typeof COMPONENTS!=="undefined" && pipelineTask("sync",COMPONENTS);
    const receipt=sync && sync.name===row.task && sync.last_run_v1;
    const reason=receipt && receipt.status==="degraded" ? "同步收据：部分可用。文件差异与能力限制需分别核对。" : row.reasons[0];
    return `<article class="review-row">
    <span class="review-status ${row.sev>=3?"bad":"warn"}">${row.sev>=3?"需处理":"需查看"}</span>
    <div class="review-object"><h3 title="${esc(row.task || row.nm)}">${esc(title.length>48?title.slice(0,46)+"…":title)}</h3><p>${esc(reason)}</p>
      ${row.reasons.length>1?`<details><summary>另有 ${row.reasons.length-1} 条证据</summary><ul>${row.reasons.slice(1).map(reason=>`<li>${esc(reason)}</li>`).join("")}</ul></details>`:""}</div>
    <button ${row.task?`data-task="${esc(row.task)}"`:row.v==="repos"?`data-review-repo="${esc(row.nm)}"`:`data-goto="${esc(row.v)}"`}>${row.task?"查看任务":row.v==="repos"?"查看仓库":"查看详情"}</button>
    </article>`;}).join("") || '<p class="review-empty">当前范围内没有待处理项。</p>';
  if(selected.length>REVIEW_LIMIT) box.innerHTML+=`<button id="review-more" class="review-more">再显示 ${Math.min(8,selected.length-REVIEW_LIMIT)} 项</button>`;
  return all.length;
}

function updateViewHeading(key){
  const copy=VIEW_COPY[key] || VIEW_COPY.overview;
  $("view-title").textContent=copy[0]; $("view-purpose").textContent=copy[1];
  document.title=copy[0]+" · 基建维护台";
}

function reviewClick(event){
  if(event.target.closest("#review-more")){REVIEW_LIMIT+=8;renderTodo();}
  const repo=event.target.closest("[data-review-repo]");
  if(repo){showView("repos",true);RP_SEL=repo.dataset.reviewRepo;renderRepoList();}
  if(event.target.closest("#page-help")) $("help").hidden=false;
}
