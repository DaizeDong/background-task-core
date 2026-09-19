// Classic script module; loaded in app.js dependency order.
let MAINT=null;
async function loadMaint(){
  $("mtnote").textContent="读取中";
  try{ MAINT=await api("/api/maint"); $("mtnote").textContent=""; }
  catch(e){ $("mtnote").textContent="读取失败:"+e.message; return; }
  renderMaint();
}

function renderSkills(){
  if(!MAINT) return;
  // --- skill:预算是名字加描述的字符数,超了尾部条目的描述会被静默丢弃 ---
  const S=MAINT.skills, el=$("mt-skills");
  if(!S.available){ mtUnset(el,S.reason); }
  else{
    // 每行一条微型条。22 个字符数原来只能逐行读,谁在吃预算看不出来。
    // 归一化用当前最大值而不是 18600 的预算上限:单个 skill 从来到不了上限,
    // 按上限归一会让 22 根条全部贴着左边,谁也分不出谁。
    const maxC=Math.max(1,...S.skills.filter(k=>!k.archived).map(k=>k.chars||0));
    const rows=S.skills.map(k=>`<div class="mt-r bar${k.archived?" off":""}">
      <span class="n" title="${esc(k.name)}">${esc(k.name)}</span>
      ${k.linked?`<span class="lk" title="junction,指向别处的仓库">↗</span>`:""}
      <span class="ub" aria-hidden="true"><i style="width:${
        k.archived?0:Math.round((k.chars||0)/maxC*100)}%"></i></span>
      <span class="c">${k.archived?"":k.chars}</span>
      ${k.archived
        ? ibtn("i-restore","从归档区还原回来",`data-mt="skill.restore" data-name="${esc(k.name)}"`)
        : ibtn("i-archive","移出到归档区,不再占描述预算",
               `data-mt="skill.archive" data-name="${esc(k.name)}"`,"danger")}
    </div>`).join("");
    el.innerHTML=`<div class="mt-t">SKILL <b>${S.liveCount}</b>
        <span class="sub">${S.budgetChars} 字符${
          S.descUnreadable ? ` · <span style="color:var(--warn)">${S.descUnreadable} 份描述读不出来,这个数偏低</span>` : ""}</span></div>
      <div class="mt-meter"><i style="width:${Math.min(S.budgetPct,100).toFixed(1)}%;
        background:${`var(--${toneOf(S.verdict)})`}"></i></div>
      <div class="mt-rows">${rows}</div>`;
  }
  // 记忆池那一栏由 renderMem 单独渲染(它有自己的端点和诊断)。
  // --- 插件:整包装卸,disable 可逆,不用重新下载 ---

}

// 仓库详情里的三个动作。它们各自有各自的回显方式,所以不走 maintAct 那条通用路:
// 复制路径根本不需要后端,打开网页由前端直接开一个**已知**的地址,而看改动要把
// 文件列表铺在面板里。硬塞进通用路的结果是三种回显被压成一句 toast。

function renderMaint(){ if(!MAINT) return; renderSkills(); renderPlugins(); renderCatalog(); }

const CATALOG_LABELS={yes:"是",no:"否",unknown:"未检查",not_applicable:"不适用",
  checked:"已检查",partial:"部分检查",unchecked:"未检查",complete:"完整",zero:"无检查项",
  healthy:"正常",degraded:"需留意",unhealthy:"异常",completed:"已完成",running:"运行中",
  skill:"技能",plugin:"插件",mcp_binding:"MCP 连接",app_connector:"App 连接",agent_template:"角色模板",
  skill_roots:"技能目录",plugin_registries:"插件登记",declared:"已声明",enabled:"已启用",cached:"已缓存",
  installed:"已安装",resolved:"来源可定位",discovered:"客户端已发现",compatible:"兼容"};
const catalogLabel=value=>CATALOG_LABELS[value] || value || "未检查";
function catalogCoverageText(coverage){
  const c=coverage||{};
  if(c.status) return catalogLabel(c.status);
  return Object.entries(c).map(([name,part])=>{
    if(!part || typeof part!=="object") return `${catalogLabel(name)}: ${catalogLabel(part)}`;
    const count=part.checked!=null && part.expected!=null?` ${part.checked}/${part.expected}`:"";
    return `${catalogLabel(name)} ${catalogLabel(part.status)}${count}`;
  }).join(" · ") || "未检查";
}

function renderCatalog(){
  const el=$("mt-catalog");
  if(!el) return;
  const components=MAINT && MAINT.components;
  const catalog=components && components.catalog;
  let html='<div class="mt-t">组件与来源</div>';
  if(!components || !components.available){
    html+=`<div class="mt-note">${esc(components && components.reason || "组件状态未检查")}</div>`;
  }else{
    const cov=components.coverage || {};
    const tasks=components.tasks||[];
    const counts=tasks.reduce((result,task)=>{const verdict=task.verdict||"unknown";result[verdict]=(result[verdict]||0)+1;return result;},{});
    html+=`<div class="mt-note" style="color:var(--${esc(toneOf(cov))})">健康检查 ${esc(cov.checked ?? "?")}/${esc(cov.expected ?? "?")} · ${esc(catalogLabel(cov.state))}</div>`;
    html+=`<div class="mt-note">正常 ${counts.healthy||0} · <span style="color:var(--bad)">异常 ${counts.unhealthy||0}</span> · <span style="color:var(--warn)">需留意 ${counts.degraded||0} · 未检查 ${counts.unknown||0}</span></div>`;
    html+=tasks.map(task=>`<details><summary>${esc(task.task_id)} · ${esc(catalogLabel(task.verdict))} · ${esc(task.state || "unknown")}</summary>
      <div class="mt-note">Current ${esc(task.execution && task.execution.state)} · last success ${esc(task.last_success_run_id || "unknown")}</div>
      <ul>${(task.checks||[]).map(check=>`<li>${esc(check.check_id)}: ${esc(check.state)} · ${check.checked}/${check.expected}</li>`).join("")}</ul>
      ${(task.unexpected_checks||[]).map(check=>`<div class="mt-note">Unexpected check ${esc(check.id || check.check_id)}: ${esc(check.state)}</div>`).join("")}
      <div class="mt-note">${esc((task.reason_codes||[]).join(", "))}</div></details>`).join("");
  }
  if(!catalog || !catalog.available){
    el.innerHTML=html+`<div class="mt-note">${esc(catalog && catalog.reason || "来源目录未检查")}</div>`;
    return;
  }
  const dimensions=["declared","enabled","cached","installed","resolved","discovered","compatible"];
  const rows=catalog.records || [];
  html+=`<div class="mt-note">${rows.length} 个来源 · ${esc(catalogCoverageText(catalog.coverage))}</div>`;
  html+=`<div class="mt-note">${Object.entries(catalog.statistics||{}).map(([kind,count])=>`${esc(catalogLabel(kind))}: ${count}`).join(" · ")}</div>`;
  html+=rows.map(source=>{
    const entries=source.entrypoints||[];
    return `<details><summary>${esc(source.registry_key || source.relative_path || source.source_id)} · ${esc(catalogLabel(source.kind))}</summary>
      <div class="mt-note">来源 ${esc(source.source_id)} · ${esc(source.origin && source.origin.client || "客户端未记录")}</div>
      <div class="mt-note">${dimensions.map(key=>`${catalogLabel(key)}: ${esc(catalogLabel(source.status[key]))}`).join(" · ")}</div>
      ${["mcp_binding","app_connector"].includes(source.kind)?`<div class="mt-note">认证: ${esc(catalogLabel(source.status.authenticated))}</div>`:""}
      <div class="mt-note">同步: ${esc(catalogLabel(source.sync && source.sync.state))} · 依赖: ${esc((source.dependencies||[]).map(d=>typeof d==="string"?d:(d.name||d.id||d.kind||"未命名依赖")).join("、")||"未声明")}</div>
      <ul>${entries.map(entry=>`<li>${esc(catalogLabel(entry.kind))} · ${esc(entry.name || entry.relative_path)} · ${esc(entry.client || "客户端未记录")}
        <div class="mt-note">${dimensions.map(key=>`${catalogLabel(key)}: ${esc(catalogLabel(entry.status[key]))}`).join(" · ")}</div></li>`).join("")}</ul>
    </details>`;
  }).join("");
  html+=(catalog.problems||[]).map(problem=>`<div class="mt-note">${esc(typeof problem==="string"?problem:(problem.reason||problem.message||problem.code||"来源检查异常"))}</div>`).join("");
  el.innerHTML=html;
}
