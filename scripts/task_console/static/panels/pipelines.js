// Read-only projections of existing component observations. No execution or state store.
let COMPONENTS = null, PIPELINE_KEY = "sync", PIPELINE_STEP = 0;
const PIPELINE_DEFS = {
  sync: {title:"Claude → Codex 配置同步", name:"SyncClaudeToCodex",
    purpose:"查看配置是否写入，以及哪些能力仍需处理。"},
  backup: {title:"每日备份与整理", name:"SyncClaudeConfig",
    purpose:"查看备份、记忆整理、日志归档和维护检查的证据。"}
};
const pipeTime = value => {
  if(value == null || value === "") return "未记录";
  const date = new Date(typeof value === "number" ? value*1000 : value);
  return Number.isNaN(date.getTime()) ? "时间无法识别" : date.toLocaleString("zh-CN", {hour12:false});
};
const pipeTone = state => ({healthy:"ok",unhealthy:"bad",degraded:"warn",failed:"bad",success:"ok",ok:"ok"}[state] || "idle");
const pipeState = state => ({healthy:"正常",unhealthy:"异常",degraded:"部分可用",failed:"失败",success:"完成",ok:"完成",running:"运行中",unknown:"待验证"}[state] || "待验证");

function pipelineTask(key, components){
  const def = PIPELINE_DEFS[key];
  // Resolve the canonical adapter task name only when unique. Never guess its repository.
  const found = (components && components.tasks || []).filter(task=>task.name===def.name);
  return found.length===1 ? found[0] : null;
}

function pipelineSteps(key, components){
  const task = pipelineTask(key, components);
  const receipt = task && task.last_run_v1;
  const unknown = (title, purpose, detail) => ({title,purpose,label:"待验证",tone:"idle",detail,evidence:[]});
  const evidence = task ? [["任务",task.name],["任务标识",task.task_id],["运行标识",task.run_id || "未提供"],
    ["开始时间",pipeTime(task.execution && task.execution.started_at)]] : [];
  if(key === "sync"){
    const catalog = components && components.catalog;
    const source = unknown("读取来源", "汇集配置、技能和插件", "来源目录是独立采样，尚未与这次同步关联。");
    if(catalog && catalog.available){
      source.label="已读取"; source.evidence=[["来源数",(catalog.records||[]).length],["采样时间",pipeTime(catalog.observed_at)],
        ["检查范围",catalogCoverageText(catalog.coverage)]];
    }
    const files = unknown("写入配置", "应用差异并复查", "尚无可用的同步收据。");
    // A preview, unfinished or failed apply cannot prove alignment, even at zero changes.
    const applied = receipt && receipt.version===1 && receipt.mode==="apply" && receipt.finished_at &&
      ["ok","success","healthy","degraded"].includes(receipt.status);
    if(receipt){
      files.evidence=[...evidence,["收据状态",receipt.status],["运行模式",receipt.mode],
        ["完成时间",pipeTime(receipt.finished_at)],["本次改动",receipt.change_count ?? "未记录"],
        ["剩余差异",receipt.remaining_changes ?? "未记录"]];
      if(applied && receipt.remaining_changes===0){
        files.label="文件已对齐"; files.tone="ok";
        files.detail="这次应用后的剩余差异为 0。能力兼容性请继续看下一环节。";
      }else{
        files.label=receipt.status==="failed"?"写入失败":"待核对";
        files.tone=receipt.status==="failed"?"bad":"warn";
        files.detail="这份收据尚不能证明应用后差异已归零。";
      }
    }
    const findings = receipt && Array.isArray(receipt.findings) ? receipt.findings : null;
    const capability = unknown("核对能力", "检查技能、连接和钩子", "没有独立的能力验收结论。");
    const memory = unknown("导入记忆", "归档并投递授权增量", "导入和原生记忆召回是不同环节，召回效果仍需独立验证。");
    if(findings){
      const rest=findings.filter(f=>f.area!=="memory"), mem=findings.filter(f=>f.area==="memory");
      capability.findings=rest; memory.findings=mem;
      if(rest.length){capability.label=`${rest.length} 项待处理`;capability.tone="warn";capability.detail="同步报告仍有能力限制，展开问题查看具体对象。";}
      if(mem.length){memory.label="部分完成";memory.tone="warn";}
      capability.evidence=memory.evidence=evidence;
    }
    return [source,files,capability,memory];
  }
  const checks = task && task.checks || [];
  const checkStep = (title,purpose,ids,detail) => {
    const step=unknown(title,purpose,detail), selected=checks.filter(c=>ids.includes(c.check_id));
    step.checks=selected; step.evidence=evidence;
    if(selected.length===ids.length){
      const states=selected.map(c=>c.state);
      step.tone=states.includes("unhealthy")?"bad":states.includes("degraded")?"warn":states.every(s=>s==="healthy")?"ok":"idle";
      step.label=step.tone==="ok"?"产物检查正常":step.tone==="idle"?"待验证":"检查需处理";
    }
    return step;
  };
  return [
    {...unknown("备份配置", "复制、检查差异并推送", "任务整体退出码不证明每个步骤成功；复制和推送尚无分阶段收据。"),evidence},
    checkStep("整理记忆", "通过 llmcall 整理变更", ["changelog"], "这里检查整理产物的新鲜度。模型调用及降级结果尚未按运行标识关联。"),
    checkStep("归档日志", "保存每日工作记录", ["journal"], "这里检查日志产物。尚未证明它由上面同一次任务运行生成。"),
    checkStep("维护检查", "检查组件、记忆和变更", ["fleet-check","memory-doctor","diff-review"], "逐项展示现有检查结论；会话清理尚无独立收据。")
  ];
}

async function loadComponents(){
  const button=$("pipeline-refresh"); if(button) button.disabled=true;
  try{COMPONENTS=await api("/api/components");}
  catch(error){COMPONENTS={available:false,reason:error.message,tasks:[]};}
  finally{if(button) button.disabled=false;}
  renderPipelines(); renderCatalog();
}

function renderPipelines(){
  const box=$("pipeline-body"); if(!box) return;
  const def=PIPELINE_DEFS[PIPELINE_KEY], task=pipelineTask(PIPELINE_KEY,COMPONENTS);
  const steps=pipelineSteps(PIPELINE_KEY,COMPONENTS);
  PIPELINE_STEP=Math.min(PIPELINE_STEP,steps.length-1);
  const chosen=steps[PIPELINE_STEP], receipt=task && task.last_run_v1;
  $("pipeline-tabs").innerHTML=Object.entries(PIPELINE_DEFS).map(([key,value])=>
    `<button data-pipeline="${key}" aria-pressed="${key===PIPELINE_KEY}">${esc(value.title)}</button>`).join("");
  const unavailable=!COMPONENTS || !COMPONENTS.available;
  const issue=unavailable?"组件证据不可用":!task?"任务尚未关联":pipeState(receipt && receipt.status || task.verdict);
  const tone=unavailable || !task?"warn":pipeTone(receipt && receipt.status || task.verdict);
  box.innerHTML=`<div class="pipeline-summary"><div><h2>${esc(def.title)}</h2><p>${esc(def.purpose)}</p></div>
    <span class="review-status ${tone}">${esc(issue)}</span></div>
    ${unavailable?`<p class="review-notice">${esc(COMPONENTS && COMPONENTS.reason || "正在读取组件证据")}</p>`:""}
    <p class="pipeline-time">组件采样 ${esc(pipeTime(COMPONENTS && COMPONENTS.captured_at))} <span>最近运行 ${esc(pipeTime(task && task.execution && task.execution.started_at))}</span></p>
    <div class="pipeline-layout"><ol class="pipeline-steps">${steps.map((step,i)=>`<li><button data-pipe-step="${i}" aria-pressed="${i===PIPELINE_STEP}">
      <span class="step-number">${i+1}</span><span class="step-copy"><strong>${esc(step.title)}</strong><small>${esc(step.purpose)}</small></span>
      <span class="review-status ${step.tone}">${esc(step.label)}</span></button></li>`).join("")}</ol>
    <article class="pipeline-detail" aria-live="polite"><h3>${esc(chosen.title)}</h3><p>${esc(chosen.detail)}</p>
      ${chosen.checks && chosen.checks.length?`<ul class="check-list">${chosen.checks.map(check=>`<li><span>${esc(({changelog:"记忆整理产物",journal:"日志归档","fleet-check":"组件合规","memory-doctor":"记忆检查","diff-review":"变更复核"})[check.check_id] || check.check_id)}</span><b class="${pipeTone(check.state)}">${esc(pipeState(check.state))}</b></li>`).join("")}</ul>`:""}
      ${chosen.findings && chosen.findings.length?`<details class="pipeline-findings"><summary>待处理问题（${chosen.findings.length}）</summary><ul>${chosen.findings.map(f=>`<li><strong>${esc(({skills:"技能",agents:"Agent",hooks:"钩子",memory:"记忆",mcp:"MCP"})[f.area] || f.area)}</strong> ${esc(f.name || "")}<small>${esc(f.reason || catalogLabel(f.status))}</small></li>`).join("")}</ul></details>`:""}
      <details class="evidence"><summary>查看证据</summary><dl>${(chosen.evidence||[]).map(([label,value])=>`<dt>${esc(label)}</dt><dd>${esc(value ?? "未记录")}</dd>`).join("") || "暂无证据"}</dl></details>
      <div class="pipeline-actions">${task?`<button data-task="${esc(task.name)}">查看任务</button>`:""}<button data-goto="storage">查看来源目录</button></div>
    </article></div>
    <p class="pipeline-footnote">顺序表示流程职责。只有显式运行标识才能关联同一次执行；独立产物检查不表示整条流水线完成。</p>`;
  const links=$("overview-pipelines");
  if(links) links.innerHTML=Object.entries(PIPELINE_DEFS).map(([key,value])=>{
    const t=pipelineTask(key,COMPONENTS), status=t && (t.last_run_v1 && t.last_run_v1.status || t.verdict);
    return `<button data-open-pipeline="${key}"><span><strong>${esc(value.title)}</strong><small>${esc(value.purpose)}</small></span><span class="review-status ${t?pipeTone(status):"idle"}">${t?esc(pipeState(status)):"待验证"}</span></button>`;
  }).join("");
}

function pipelineClick(event){
  const tab=event.target.closest("[data-pipeline],[data-open-pipeline]");
  if(tab){PIPELINE_KEY=tab.dataset.pipeline || tab.dataset.openPipeline; PIPELINE_STEP=0;
    if(tab.dataset.openPipeline) showView("pipelines",true); renderPipelines();}
  const step=event.target.closest("[data-pipe-step]");
  if(step){PIPELINE_STEP=Number(step.dataset.pipeStep);renderPipelines();}
}
