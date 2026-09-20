// Read-only projections of existing component observations. No execution or state store.
let COMPONENTS = null, PIPELINE_QUERY="";
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
    const source = unknown("读取来源", "汇集配置、技能和插件", "目录单独读取，不能据此确认本次同步结果。");
    if(catalog && catalog.available){
      source.label="已读取"; source.evidence=[["来源数",(catalog.records||[]).length],["读取时间",pipeTime(catalog.observed_at)],
        ["检查范围",catalogCoverageText(catalog.coverage)]];
    }
    const files = unknown("写入配置", "应用差异并复查", "还没有可读取的同步结果。");
    // A preview, unfinished or failed apply cannot prove alignment, even at zero changes.
    const applied = receipt && receipt.version===1 && receipt.mode==="apply" && receipt.finished_at &&
      ["ok","success","healthy","degraded"].includes(receipt.status);
    if(receipt){
      files.evidence=[...evidence,["同步结果",receipt.status],["运行模式",receipt.mode],
        ["完成时间",pipeTime(receipt.finished_at)],["本次改动",receipt.change_count ?? "未记录"],
        ["剩余差异",receipt.remaining_changes ?? "未记录"]];
      if(applied && receipt.remaining_changes===0){
        files.label="文件已对齐"; files.tone="ok";
        files.detail="文件已同步，剩余差异为 0；功能是否可用仍需验证。";
      }else{
        files.label=receipt.status==="failed"?"写入失败":"待核对";
        files.tone=receipt.status==="failed"?"bad":"warn";
        files.detail="当前记录不能确认同步后已无差异。";
      }
    }
    const findings = receipt && Array.isArray(receipt.findings) ? receipt.findings : null;
    const capability = unknown("核对能力", "检查技能、连接和钩子", "还没有技能、连接和钩子的独立检查结果。");
    const memory = unknown("导入记忆", "归档并投递授权增量", "尚未验证 Codex 能否在会话中调用这些记忆。");
    if(findings){
      const rest=findings.filter(f=>f.area!=="memory"), mem=findings.filter(f=>f.area==="memory");
      capability.findings=rest; memory.findings=mem;
      if(rest.length){capability.label=`${rest.length} 项待处理`;capability.tone="warn";capability.detail="仍有功能受限，具体原因见下方。";}
      if(mem.length){memory.label="有待处理项";memory.tone="warn";}
      capability.evidence=memory.evidence=evidence;
    }
    return [source,files,capability,memory];
  }
  const checks = task && task.checks || [];
  const checkStep = (title,purpose,ids,detail) => {
    const step=unknown(title,purpose,detail), selected=checks.filter(c=>ids.includes(c.check_id));
    step.checks=selected; step.evidence=evidence;
    if(selected.length!==ids.length) step.detail='部分检查记录缺失，无法确认本环节的结果。';
    if(selected.length===ids.length){
      const states=selected.map(c=>c.state);
      step.tone=states.includes("unhealthy")?"bad":states.includes("degraded")?"warn":states.every(s=>s==="healthy")?"ok":"idle";
      step.label=step.tone==="ok"?"输出检查正常":step.tone==="idle"?"待验证":"检查发现问题";
    }
    return step;
  };
  return [
    {...unknown("备份配置", "复制、检查差异并推送", "缺少各阶段的完成记录，无法分别确认复制和推送结果。"),evidence},
    checkStep("整理记忆", "通过 llmcall 整理变更", ["changelog"], "已检查输出文件的更新时间；尚未对应到本次模型调用。"),
    checkStep("归档日志", "保存每日工作记录", ["journal"], "已检查日志文件，但不能确认来自本次运行。"),
    checkStep("维护检查", "检查组件、记忆和变更", ["fleet-check","memory-doctor","diff-review"], "检查结果见下方；会话清理还没有独立完成记录。")
  ];
}

async function loadComponents(){
  const button=$("pipeline-refresh"); if(button) button.disabled=true;
  try{COMPONENTS=await api("/api/components");}
  catch(error){COMPONENTS={available:false,reason:error.message,tasks:[]};}
  finally{if(button) button.disabled=false;}
  renderPipelines(); renderCatalog();
  if(typeof renderPlatformSignals==='function') renderPlatformSignals();
}

function renderPipelines(){
  const box=$("pipeline-body");if(!box) return;
  $("pipeline-sample").textContent="读取时间 "+pipeTime(COMPONENTS && COMPONENTS.captured_at);
  box.innerHTML=(!COMPONENTS || !COMPONENTS.available ? `<p class="review-notice">${esc(COMPONENTS && COMPONENTS.reason || "正在读取运行记录")}</p>`:"")+
    Object.entries(PIPELINE_DEFS).map(([key,def])=>{
      const task=pipelineTask(key,COMPONENTS), receipt=task && task.last_run_v1;
      const status=task && (receipt && receipt.status || task.verdict);
      const scheduled=typeof ROWS!=="undefined" ? ROWS.filter(row=>row.name===def.name) : [];
      const row=scheduled.length===1 ? scheduled[0] : null;
      const steps=pipelineSteps(key,COMPONENTS);
      return `<div class="card pipeline-run" id="pipeline-${key}">
        <div class="card-header"><h2 class="card-title">${esc(def.title)}</h2><div class="review-actions">
          <span class="review-status ${task?pipeTone(status):"idle"}">${task?esc(pipeState(status)):"未找到对应任务"}</span>
          ${task?`<button data-task="${esc(task.name)}">任务详情</button>`:""}
          ${row?fixBtn("run",row.name):""}</div></div>
        <div class="pipeline-metrics"><span>最近运行 <b>${esc(pipeTime(task && task.execution && task.execution.started_at))}</b></span>
          <span>下次计划 <b>${esc(row ? pipeTime(row.nextRun) : "未读取")}</b></span><span>运行标识 <b>${esc(task && task.run_id || "未提供")}</b></span>
          <span>任务标识 <b>${esc(task && task.task_id || "未关联")}</b></span>
          ${receipt?`<span>模式 <b>${esc(receipt.mode || "未记录")}</b></span><span>本次改动 <b>${esc(receipt.change_count ?? "未记录")}</b></span><span>剩余差异 <b>${esc(receipt.remaining_changes ?? "未记录")}</b></span>`:""}
        </div>
        <div class="ops-scroll"><table class="ops-table pipeline-table"><thead><tr><th>环节</th><th>结果</th><th>检查记录</th></tr></thead><tbody>
        ${steps.map((step,i)=>`<tr><th scope="row">${i+1}. ${esc(step.title)}</th><td><span class="review-status ${step.tone}">${esc(step.label)}</span></td>
          <td><div>${esc(step.detail.replace("展开问题查看具体对象。","见下方问题清单。"))}</div>
            ${(step.checks||[]).map(check=>`<span class="check-chip ${pipeTone(check.state)}">${esc(check.check_id)}: ${esc(pipeState(check.state))}</span>`).join("")}
            <div class="pipeline-evidence">${(step.evidence||[]).filter(([label])=>!["任务","任务标识","运行标识","开始时间"].includes(label)).map(([label,value])=>`<span>${esc(label)} <b>${esc(value ?? "未记录")}</b></span>`).join("")}</div>
          </td></tr>`).join("")}</tbody></table></div></div>`;
    }).join("")+
    `<div class="card"><div class="card-header"><h2 class="card-title">同步中发现的问题 <span id="pipeline-issue-count" class="n"></span></h2>
    <div class="catalog-tools"><input id="pipeline-search" type="search" aria-label="搜索流水线问题" placeholder="搜索对象、类型或原因" value="${esc(PIPELINE_QUERY)}"><button data-reset-filters="pipelines">清除筛选</button><button data-goto="resources">查看技能和插件</button></div></div>
    <div id="pipeline-issues" class="ops-scroll"></div></div>`;
  $("pipeline-search").addEventListener("input",event=>{PIPELINE_QUERY=event.target.value;renderPipelineIssues();});
  renderPipelineIssues();
  const links=$("overview-pipelines");
  if(links) links.innerHTML=Object.entries(PIPELINE_DEFS).map(([key,def])=>{
    const task=pipelineTask(key,COMPONENTS), state=task && (task.last_run_v1 && task.last_run_v1.status || task.verdict);
    return `<button data-open-pipeline="${key}"><strong>${esc(def.title)}</strong><span class="review-status ${task?pipeTone(state):"idle"}">${task?esc(pipeState(state)):"待验证"}</span></button>`;
  }).join("");
}
function renderPipelineIssues(){
  const task=pipelineTask("sync",COMPONENTS), findings=task && task.last_run_v1 && task.last_run_v1.findings;
  const query=PIPELINE_QUERY.trim().toLowerCase();
  const rows=(Array.isArray(findings)?findings:[]).filter(f=>!query || JSON.stringify(f).toLowerCase().includes(query));
  $("pipeline-issue-count").textContent=Array.isArray(findings)?`显示 ${rows.length} 项，共 ${findings.length} 项`:"尚无问题清单";
  $("pipeline-issues").innerHTML=rows.length?`<table class="ops-table"><thead><tr><th>类型</th><th>对象</th><th>状态 / 原因</th></tr></thead><tbody>${rows.map(f=>`<tr><td>${esc(catalogLabel(f.area))}</td><th scope="row">${esc(f.name || "未命名")}</th><td>${esc(f.reason || catalogLabel(f.status))}</td></tr>`).join("")}</tbody></table>`:
    `<p class="review-empty">${Array.isArray(findings)?findings.length?"没有符合筛选条件的问题":"本次同步记录未列出问题":"尚未读到问题清单，无法确认功能是否可用"}</p>`;
}
function pipelineClick(event){
  const link=event.target.closest("[data-open-pipeline]");
  if(link){showView("pipelines",true);$("pipeline-"+link.dataset.openPipeline)?.scrollIntoView({block:"start"});}
}
