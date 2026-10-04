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
const pipeState = state => ({healthy:"正常",unhealthy:"异常",degraded:"部分可用",failed:"失败",success:"完成",ok:"完成",completed:"完成",running:"运行中",unknown:"待验证"}[state] || "待验证");
// 同步写进收据的是原因码。页面上给人看中文,原码留在悬停提示里;认不出的原因码照原样显示。
const PIPELINE_REASONS = {
  managed_block_modified_by_user:"同步管理的配置段被手动改过，没有覆盖",
  managed_role_or_entry_modified_missing_or_unowned:"同步管理的 agent 条目被改过、缺失或不归同步管",
  managed_agents_dependency_conflict:"agent 配置的依赖关系冲突",
  skill_routing_ownership_or_layout_conflict:"技能路由的归属或目录结构冲突",
  source_command_not_reviewed_for_native_adapter:"钩子命令还没审核，没有转成 Codex 原生钩子",
  external_installer_required:"由外部安装程序管理，需另行安装"
};
const pipelineReason = code => Object.hasOwn(PIPELINE_REASONS, code) ? PIPELINE_REASONS[code] : code;
// 运行记录来自一份采集好的快照,不是打开页面时现查的。采集停了以后,「最近运行」和各环节结果
// 会一直停在那一刻,而它们看起来和刚采集的记录一模一样,所以快照多旧必须说出来。
// 两条流水线都至少每天跑一次,超过一天没更新的快照一定已经过时。
const PIPELINE_STALE_HOURS = 24;
function pipelineSnapshotAge(value, now=Date.now()){
  if(value == null || value === "") return {text:"快照时间未记录", stale:false, age:""};
  const date = new Date(typeof value === "number" ? value*1000 : value);
  if(Number.isNaN(date.getTime())) return {text:"快照时间无法识别", stale:false, age:""};
  const hours=(now-date.getTime())/3600000;
  if(hours<-0.1) return {text:`快照于 ${pipeTime(value)}（晚于本机时间）`, stale:false, age:""};
  const age=hours<1?"不到 1 小时前":hours<48?`${Math.floor(hours)} 小时前`:`${Math.floor(hours/24)} 天前`;
  return {text:`快照于 ${pipeTime(value)}（${age}）`, stale:hours>PIPELINE_STALE_HOURS, age};
}

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
      source.label="已读取"; source.tone="muted"; source.symbol="✓"; source.evidence=[["来源数",(catalog.records||[]).length],["读取时间",pipeTime(catalog.observed_at)],
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

function pipelineRows(){
  // 找不到这条任务有四种原因,要说的话和要做的事都不一样,不能共用一句「未读取」:
  // 计划任务还在读、读失败了、读到了但计划程序里没有它、有不止一个同名任务。
  const read=typeof API_READS!=='undefined'?API_READS.get('/api/tasks'):null;
  const failed=(typeof TASKS_LOAD_ERROR!=='undefined' && TASKS_LOAD_ERROR) || read?.error;
  return Object.entries(PIPELINE_DEFS).map(([key,def])=>{
    const scheduled=ROWS.filter(row=>row.name===def.name);
    const row=scheduled.length===1?scheduled[0]:null;
    const missing=row?null:!DATA?(failed?['计划任务读取失败','bad']:['正在读取计划任务','pending']):
      scheduled.length>1?[`计划程序里有 ${scheduled.length} 个同名任务`,'warn']:['计划程序里没有这个任务','bad'];
    const displayRow=row || {name:def.name,triggers:missing[0],infoPending:!DATA};
    return {key,def,row,displayRow,missing};
  });
}

function renderPipelines(){
  const box=$("pipeline-body");if(!box) return;
  const focused=box.contains?.(document.activeElement)?taskControlKey(document.activeElement):null;
  const query=$('pipeline-task-search').value, verdict=$('pipeline-verdict').value;
  // 卡片标题(「每日备份与整理」)也要搜得到:人是照着卡片上的字搜的,不是照着任务名。
  const q=query.trim().toLowerCase();
  const candidates=pipelineRows().filter(item=>taskMatches(item.displayRow,query) ||
    [item.def.title,item.def.purpose].join(' ').toLowerCase().includes(q));
  updateTaskVerdictFilter('pipeline-verdict',candidates.map(item=>item.displayRow),verdict);
  const visible=candidates.filter(item=>taskMatchesVerdict(item.displayRow,verdict));
  const snap=pipelineSnapshotAge(COMPONENTS && COMPONENTS.captured_at), sample=$("pipeline-sample");
  sample.textContent=snap.text;
  sample.className=snap.stale?"pipeline-stale":"faint";
  const stale=COMPONENTS && COMPONENTS.available && snap.stale
    ? `<p class="review-notice">下面的运行记录来自 ${esc(snap.age)}采集的快照，之后没有再更新，最近运行和各环节结果都停在那一刻。任务现在的状态看每张卡片上的状态和下次运行时间。</p>`:"";
  box.innerHTML=(!COMPONENTS || !COMPONENTS.available ? `<p class="review-notice">${esc(COMPONENTS && COMPONENTS.reason || "正在读取运行记录")}</p>`:"")+stale+
    (!visible.length?'<p class="review-empty">没有符合筛选条件的同步与备份任务</p>':'')+
    visible.map(({key,def,row,displayRow,missing})=>{
      const task=pipelineTask(key,COMPONENTS), receipt=task && task.last_run_v1;
      const status=task && (receipt && receipt.status || task.verdict);
      const steps=pipelineSteps(key,COMPONENTS);
      return `<div class="card pipeline-run" id="pipeline-${key}">
        <div class="card-header"><h2 class="card-title">${esc(def.title)}</h2></div>
        ${taskListRow(displayRow,{controls:row?taskActionButtons(row,{deletable:false}):'',showDetails:!!row,
          state:row?null:statusBadge(missing[0],missing[1])})}
        <div class="pipeline-result">运行记录 ${statusBadge(task?pipeState(status):'未找到对应记录',task?componentTone(status):'idle',undefined,'review-status')}</div>
        <div class="pipeline-metrics"><span>最近运行 <b>${esc(pipeTime(task && task.execution && task.execution.started_at))}</b></span>
          <span>运行标识 <b>${esc(task && task.run_id || "未提供")}</b></span>
          <span>任务标识 <b>${esc(task && task.task_id || "未关联")}</b></span>
          ${receipt?`<span>模式 <b>${esc(receipt.mode || "未记录")}</b></span><span>本次改动 <b>${esc(receipt.change_count ?? "未记录")}</b></span><span>剩余差异 <b>${esc(receipt.remaining_changes ?? "未记录")}</b></span>`:""}
        </div>
        <div class="ops-scroll"><table class="ops-table pipeline-table"><thead><tr><th>环节</th><th>结果</th><th>检查记录</th></tr></thead><tbody>
        ${steps.map((step,i)=>`<tr><th scope="row"><span class="step-index">${i+1}</span>${esc(step.title)}</th><td>${statusBadge(step.label,step.tone,step.symbol,'review-status')}</td>
          <td><div>${esc(step.detail.replace("展开问题查看具体对象。","见下方问题清单。"))}</div>
            ${(step.checks||[]).map(check=>`<span class="check-chip">${esc(check.check_id)} ${statusBadge(pipeState(check.state),componentTone(check.state))}</span>`).join("")}
            <div class="pipeline-evidence">${(step.evidence||[]).filter(([label])=>!["任务","任务标识","运行标识","开始时间"].includes(label)).map(([label,value])=>`<span>${esc(label)} <b>${esc(value ?? "未记录")}</b></span>`).join("")}</div>
          </td></tr>`).join("")}</tbody></table></div></div>`;
    }).join("")+
    `<div class="card"><div class="card-header"><h2 class="card-title">同步中发现的问题 <span id="pipeline-issue-count" class="n"></span></h2>
    <div class="catalog-tools"><input id="pipeline-search" type="search" aria-label="搜索流水线问题" placeholder="搜索对象、类型或原因" value="${esc(PIPELINE_QUERY)}"><button class="icon-only" data-reset-filters="pipelines" title="清除筛选"><svg class="ic" aria-hidden="true"><use href="#i-filter-clear"/></svg><span class="control-label">清除筛选</span></button><button class="icon-only" data-goto="resources" title="查看技能和插件"><svg class="ic" aria-hidden="true"><use href="#i-puzzle"/></svg><span class="control-label">查看技能和插件</span></button></div></div>
    <div id="pipeline-issues" class="ops-scroll"></div></div>`;
  $("pipeline-search").addEventListener("input",event=>{PIPELINE_QUERY=event.target.value;renderPipelineIssues();});
  restoreTaskControlFocus(box,focused);
  renderPipelineIssues();
  const links=$("overview-pipelines");
  if(links) links.innerHTML=Object.entries(PIPELINE_DEFS).map(([key,def])=>{
    const task=pipelineTask(key,COMPONENTS), state=task && (task.last_run_v1 && task.last_run_v1.status || task.verdict);
    return `<button data-open-pipeline="${key}"><strong>${esc(def.title)}</strong>${statusBadge(task?pipeState(state):'待验证',task?componentTone(state):'idle',undefined,'review-status')}</button>`;
  }).join("");
}
function renderPipelineIssues(){
  const task=pipelineTask("sync",COMPONENTS), findings=task && task.last_run_v1 && task.last_run_v1.findings;
  const query=PIPELINE_QUERY.trim().toLowerCase();
  // 中文说法也要搜得到,原因码照旧能搜。
  const rows=(Array.isArray(findings)?findings:[]).filter(f=>!query ||
    (JSON.stringify(f)+" "+(f.reason?pipelineReason(f.reason):"")).toLowerCase().includes(query));
  $("pipeline-issue-count").textContent=Array.isArray(findings)?`显示 ${rows.length} 项，共 ${findings.length} 项`:"尚无问题清单";
  const reasonCell=f=>f.reason
    ? `<td${pipelineReason(f.reason)!==f.reason?` title="原因码 ${esc(f.reason)}"`:""}>${esc(pipelineReason(f.reason))}</td>`
    : `<td>${esc(catalogLabel(f.status))}</td>`;
  $("pipeline-issues").innerHTML=rows.length?`<table class="ops-table"><thead><tr><th>类型</th><th>对象</th><th>状态 / 原因</th></tr></thead><tbody>${rows.map(f=>`<tr><td>${esc(catalogLabel(f.area))}</td><th scope="row">${esc(f.name || "未命名")}</th>${reasonCell(f)}</tr>`).join("")}</tbody></table>`:
    `<p class="review-empty">${Array.isArray(findings)?findings.length?"没有符合筛选条件的问题":"本次同步记录未列出问题":"尚未读到问题清单，无法确认功能是否可用"}</p>`;
}
function pipelineClick(event){
  const link=event.target.closest("[data-open-pipeline]");
  if(link){
    $('pipeline-task-search').value='';$('pipeline-verdict').value='';renderPipelines();
    showView("pipelines",true);$("pipeline-"+link.dataset.openPipeline)?.scrollIntoView({block:"start"});
  }
}
