// Read-only projections of existing component observations. No execution or state store.
let COMPONENTS = null;
const PIPELINE_DEFS = {
  backup: {title:"每日备份与整理", name:"SyncClaudeConfig",
    purpose:"查看备份、记忆整理、日志归档和维护检查的证据。"}
};
const pipeTime = value => {
  if(value == null || value === "") return "未记录";
  const date = new Date(typeof value === "number" ? value*1000 : value);
  return Number.isNaN(date.getTime()) ? "时间无法识别" : date.toLocaleString("zh-CN", {hour12:false});
};
const pipeState = state => ({healthy:"正常",unhealthy:"异常",degraded:"部分可用",failed:"失败",success:"完成",ok:"完成",completed:"完成",running:"运行中",unknown:"待验证"}[state] || "待验证");
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
  const unknown = (title, purpose, detail) => ({title,purpose,label:"待验证",tone:"idle",detail,evidence:[]});
  const evidence = task ? [["任务",task.name],["任务标识",task.task_id],["运行标识",task.run_id || "未提供"],
    ["开始时间",pipeTime(task.execution && task.execution.started_at)]] : [];
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
  try{COMPONENTS=await api("/api/components");}
  // 读失败要和「读到了、没有配置」分得开:资源页的锚点前者画「!」,后者画「—」。
  catch(error){COMPONENTS={available:false,reason:error.message,tasks:[],readFailed:true};}
  renderPipelines(); renderCatalog();
  renderPlatformSignals();
}

function pipelineRows(){
  // 找不到这条任务有四种原因,要说的话和要做的事都不一样,不能共用一句「未读取」:
  // 计划任务还在读、读失败了、读到了但计划程序里没有它、有不止一个同名任务。
  const failed=TASKS_LOAD_ERROR || API_READS.get('/api/tasks')?.error;
  return Object.entries(PIPELINE_DEFS).map(([key,def])=>{
    const scheduled=ROWS.filter(row=>row.name===def.name);
    const row=scheduled.length===1?scheduled[0]:null;
    const missing=row?null:!DATA?(failed?['计划任务读取失败','bad']:['正在读取计划任务','pending']):
      scheduled.length>1?[`计划程序里有 ${scheduled.length} 个同名任务`,'warn']:['计划程序里没有这个任务','bad'];
    const displayRow=row || {name:def.name,triggers:missing[0],infoPending:!DATA};
    return {key,def,row,displayRow,missing};
  });
}

// 卡片开头一句合起来的结论。以前三种信号各说各的、还会互相打架:任务行上绿色的「已启用」、
// 快照「9 天前」、运行记录「× 异常」。现在按最坏的那一个定颜色,几件事用「·」连成一句,
// 例如「× 备份异常 · 记录停在 9 天前」;计划程序的状态退到任务行上的小字里。
const PIPELINE_SHORT = {backup:"备份"};
const PIPELINE_TONE_RANK = {bad:0, warn:1, pending:2, idle:3, active:4, ok:5, muted:6};
function pipelineVerdict(key, row, task, snap){
  const parts=[], tones=[];
  const add=(text,tone)=>{parts.push(text);tones.push(tone);};
  if(row && row.sk==="bad") add("任务上次运行失败","bad");
  const status=task && (task.last_run_v1 && task.last_run_v1.status || task.verdict);
  if(task){
    const tone=componentTone(status);
    add(PIPELINE_SHORT[key]+(tone==="ok"?"正常":pipeState(status)), tone);
  }else if(!COMPONENTS) add("正在读取运行记录","pending");
  else add("未找到对应记录","idle");
  if(task && snap.stale) add(`记录停在 ${snap.age}`,"warn");
  const tone=tones.sort((a,b)=>(PIPELINE_TONE_RANK[a] ?? 9)-(PIPELINE_TONE_RANK[b] ?? 9))[0] || "idle";
  return statusBadge(parts.join(" · "), tone, undefined, "pipeline-verdict");
}
function renderPipelines(){
  const box=$("pipeline-body");if(!box) return;
  const restoreDisclosures=captureDisclosures(box);
  const focused=box.contains(document.activeElement)?taskControlKey(document.activeElement):null;
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
    ? `<p class="review-notice">运行记录来自 ${esc(snap.age)}的快照，尚未更新。当前任务状态和下次运行时间见任务行。</p>`:"";
  box.innerHTML=(!COMPONENTS || !COMPONENTS.available ? `<p class="review-notice">${esc(COMPONENTS && COMPONENTS.reason || "正在读取运行记录")}</p>`:"")+stale+
    (!visible.length?'<p class="review-empty">没有符合筛选条件的同步与备份任务</p>':'')+
    visible.map(({key,def,row,displayRow,missing})=>{
      const task=pipelineTask(key,COMPONENTS), receipt=task && task.last_run_v1;
      const status=task && (receipt && receipt.status || task.verdict);
      const steps=pipelineSteps(key,COMPONENTS);
      return `<div class="card pipeline-run" id="pipeline-${key}">
        <div class="card-header"><h2 class="card-title">${esc(def.title)}</h2>${pipelineVerdict(key,row,task,snap)}</div>
        ${taskListRow(displayRow,{controls:row?taskActionButtons(row,{deletable:false,scope:'pipe'}):'',showDetails:!!row,inline:false,
          state:row?null:statusBadge(missing[0],missing[1])})}
        <details class="pipeline-record" data-disclosure-key="${esc(key)}"><summary>运行记录 ${statusBadge(task?pipeState(status):'未找到对应记录',task?componentTone(status):'idle',undefined,'review-status')} · 最近运行 ${esc(pipeTime(task && task.execution && task.execution.started_at))}</summary>
          <div class="pipeline-metrics"><span>运行标识 <b>${esc(task && task.run_id || "未提供")}</b></span>
            <span>任务标识 <b>${esc(task && task.task_id || "未关联")}</b></span>
          </div>
        </details>
        <div class="ops-scroll pipeline-steps"><table class="ops-table pipeline-table"><thead><tr><th>环节</th><th>结果</th><th>检查记录</th></tr></thead><tbody>
        ${steps.map((step,i)=>`<tr><th scope="row"><span class="step-index">${i+1}</span>${esc(step.title)}</th><td data-label="结果">${statusBadge(step.label,step.tone,step.symbol,'review-status')}</td>
          <td data-label="检查记录"><div>${esc(step.detail)}</div>
            ${(step.checks||[]).map(check=>`<span class="check-chip">${esc(check.check_id)} ${statusBadge(pipeState(check.state),componentTone(check.state))}</span>`).join("")}
            <div class="pipeline-evidence">${(step.evidence||[]).filter(([label])=>!["任务","任务标识","运行标识","开始时间"].includes(label)).map(([label,value])=>`<span>${esc(label)} <b>${esc(value ?? "未记录")}</b></span>`).join("")}</div>
          </td></tr>`).join("")}</tbody></table></div></div>`;
    }).join("");
  restoreTaskControlFocus(box,focused);
  restoreDisclosures();
  const links=$("overview-pipelines");
  if(links) links.innerHTML=Object.entries(PIPELINE_DEFS).map(([key,def])=>{
    const task=pipelineTask(key,COMPONENTS), state=task && (task.last_run_v1 && task.last_run_v1.status || task.verdict);
    return `<button data-open-pipeline="${key}"><strong>${esc(def.title)}</strong>${statusBadge(task?pipeState(state):'待验证',task?componentTone(state):'idle',undefined,'review-status')}</button>`;
  }).join("");
}
function pipelineClick(event){
  const link=event.target.closest("[data-open-pipeline]");
  if(link){
    $('pipeline-task-search').value='';$('pipeline-verdict').value='';renderPipelines();
    showView("pipelines",true);$("pipeline-"+link.dataset.openPipeline)?.scrollIntoView({block:"start"});
  }
}
