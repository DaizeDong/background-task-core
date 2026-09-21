// Classic script module; loaded in app.js dependency order.
let MAINT=null, RUNTIME_QUERY="", RUNTIME_STATE="", RUNTIME_SORT="name";
function runtimeRows(rows,kind){
  const query=RUNTIME_QUERY.trim().toLowerCase();
  return rows.filter(row=>{
    const active=kind==="skill"?!row.archived:!!row.enabled;
    return (!query || row.name.toLowerCase().includes(query)) && (!RUNTIME_STATE || (RUNTIME_STATE==="active"?active:!active));
  }).sort((a,b)=>RUNTIME_SORT==="budget" && kind==="skill" ? (b.chars||0)-(a.chars||0) || a.name.localeCompare(b.name) : a.name.localeCompare(b.name));
}
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
    const selected=runtimeRows(S.skills,"skill");
    const rows=selected.map(k=>`<div class="mt-r bar${k.archived?" off":""}">
      <span class="n" title="${esc(k.name)}">${esc(k.name)}</span>
      ${k.linked?`<span class="lk" title="目录联接（junction），指向其他仓库">↗</span>`:""}
      <span class="ub" aria-hidden="true"><i style="width:${
        k.archived?0:Math.round((k.chars||0)/maxC*100)}%"></i></span>
      <span class="c">${k.archived?"":k.chars}</span>
      ${k.archived
        ? ibtn("i-restore","恢复技能，下次会话生效",`data-mt="skill.restore" data-name="${esc(k.name)}"`)
        : ibtn("i-archive","归档技能，下次会话不再加载",
               `data-mt="skill.archive" data-name="${esc(k.name)}"`,"danger")}
    </div>`).join("");
    el.innerHTML=`<div class="mt-t">技能 <b>${selected.length}/${S.skills.length}</b>
        <span class="sub">${S.budgetChars} 字符${
          S.descUnreadable ? ` · <span style="color:var(--warn)">${S.descUnreadable} 份描述读不出来,这个数偏低</span>` : ""}</span></div>
      <div class="mt-meter"><i style="width:${Math.min(S.budgetPct,100).toFixed(1)}%;
        background:${`var(--${toneOf(S.verdict)})`}"></i></div>
      <div class="mt-rows">${rows || '<p class="review-empty">没有匹配的技能</p>'}</div>`;
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
  repository:"仓库",blocked:"受阻",unsupported:"尚不支持",skill:"技能",plugin:"插件",mcp_binding:"MCP 连接",app_connector:"App 连接",agent_template:"角色模板",
  skill_roots:"技能目录",plugin_registries:"插件登记",declared:"已登记",enabled:"已启用",cached:"已缓存",
  installed:"已安装",resolved:"路径有效",discovered:"客户端可见",compatible:"兼容",
  external_skill_repos:"外部技能仓",repo_roots:"仓库目录",private_bindings:"私有绑定",runtime_discovery:"运行时发现",workflow_roots:"工作流",plugin_descriptors:"插件描述",native_discovery:"原生发现",skills:"技能",agents:"角色",hooks:"钩子",memory:"记忆",mcp:"MCP"};
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

let CATALOG_QUERY="", CATALOG_KIND="", CATALOG_CLIENT="", CATALOG_STATE="", HEALTH_STATE="";
function catalogComponents(){return (typeof COMPONENTS !== "undefined" && COMPONENTS) || (MAINT && MAINT.components);}
function catalogName(source){
  return source.registry_key || (source.relative_path && source.relative_path!=="." ? source.relative_path : null)
    || `未命名${catalogLabel(source.kind)} (${source.source_id.split(":").pop().slice(0,8)})`;
}
function catalogClients(source){
  const clients=[source.origin && source.origin.client,...(source.entrypoints||[]).map(entry=>entry.client)].filter(Boolean);
  return clients.length?[...new Set(clients)]:["unknown"];
}
function catalogMatches(source){
  const query=CATALOG_QUERY.trim().toLowerCase();
  if(CATALOG_KIND && source.kind!==CATALOG_KIND) return false;
  if(CATALOG_CLIENT && !catalogClients(source).includes(CATALOG_CLIENT)) return false;
  if(CATALOG_STATE){
    const [field,value]=CATALOG_STATE.split(":");
    if(field==="authenticated" && !["mcp_binding","app_connector"].includes(source.kind)) return false;
    const state=field==="sync" ? source.sync && source.sync.state : (source.status||{})[field];
    if((state || "unknown")!==value) return false;
  }
  return !query || JSON.stringify([source.source_id,catalogName(source),source.relative_path,source.dependencies,source.entrypoints]).toLowerCase().includes(query);
}
function renderCatalog(){
  const el=$("mt-catalog");if(!el) return;
  const components=catalogComponents(), catalog=components && components.catalog;
  const records=catalog && catalog.records || [], cov=components && components.coverage || {};
  const tasks=components && components.tasks || [], coverageTone=['ok','warn','bad','idle'].includes(cov.tone)?cov.tone:'idle';
  const problemCount=tasks.filter(task=>['unhealthy','degraded'].includes(task.verdict)).length;
  const uncheckedCount=tasks.filter(task=>!task.verdict || task.verdict==='unknown').length;
  const clients=[...new Set(records.flatMap(catalogClients))].sort();
  const states=[["compatible:no","不兼容"],["compatible:unknown","兼容性未检查"],["authenticated:no","未认证"],["authenticated:unknown","认证未检查"],
    ...[...new Set(records.map(source=>source.sync && source.sync.state || "unknown"))].sort().map(state=>["sync:"+state,"同步: "+catalogLabel(state)])];
  el.innerHTML=`<div class="catalog-heading"><h2>已登记的技能和插件</h2><span>${catalog && catalog.available?records.length+" 项":"尚未读取目录"}</span></div>
    <div class="catalog-tools"><input type="search" id="catalog-search" aria-label="搜索已登记的技能和插件" placeholder="搜索名称、路径或依赖" value="${esc(CATALOG_QUERY)}">
      <select id="catalog-kind" aria-label="来源类型"><option value="">全部类型</option>${Object.entries(catalog && catalog.statistics || {}).map(([kind,count])=>`<option value="${esc(kind)}"${CATALOG_KIND===kind?" selected":""}>${esc(catalogLabel(kind))} (${count})</option>`).join("")}</select>
      <select id="catalog-client" aria-label="涉及客户端"><option value="">全部客户端</option>${clients.map(client=>`<option value="${esc(client)}"${client===CATALOG_CLIENT?" selected":""}>${esc(catalogLabel(client))}</option>`).join("")}</select>
      <select id="catalog-state" aria-label="组件状态"><option value="">全部状态</option>${states.map(([value,label])=>`<option value="${esc(value)}"${value===CATALOG_STATE?" selected":""}>${esc(label)}</option>`).join("")}</select><button data-reset-filters="catalog">清除筛选</button><span id="catalog-count"></span></div>
    <div id="catalog-results" class="ops-scroll"></div>
    <div class="catalog-heading health-heading"><h2>自动化检查结果 <span style="color:var(--${coverageTone})">${esc(cov.checked ?? "?")}/${esc(cov.expected ?? "?")}</span></h2><span>异常 ${problemCount} · 未检查 ${uncheckedCount}</span>
      <select id="health-state" aria-label="健康检查状态"><option value="">全部结论</option>${["healthy","degraded","unhealthy","unknown"].map(state=>`<option value="${state}"${state===HEALTH_STATE?" selected":""}>${catalogLabel(state)}</option>`).join("")}</select></div>
    <div class="catalog-tools"><span class="faint">${esc(catalogCoverageText(catalog && catalog.coverage))}</span><span id="health-count"></span></div><div id="health-results" class="ops-scroll"></div>`;
  $("catalog-search").addEventListener("input",event=>{CATALOG_QUERY=event.target.value;renderCatalogResults();renderCatalogHealth();});
  $("catalog-kind").addEventListener("change",event=>{CATALOG_KIND=event.target.value;renderCatalogResults();});
  $("catalog-client").addEventListener("change",event=>{CATALOG_CLIENT=event.target.value;renderCatalogResults();});
  $("catalog-state").addEventListener("change",event=>{CATALOG_STATE=event.target.value;renderCatalogResults();});
  $("health-state").addEventListener("change",event=>{HEALTH_STATE=event.target.value;renderCatalogHealth();});
  renderCatalogResults();renderCatalogHealth();
}
function renderCatalogHealth(){
  const components=catalogComponents(), el=$("health-results");if(!el) return;
  if(!components || !components.available){el.innerHTML=`<p class="review-notice">${esc(components && components.reason || "组件证据不可用")}</p>`;return;}
  const tasks=components.tasks || [], query=CATALOG_QUERY.trim().toLowerCase();
  const rows=tasks.filter(task=>(!HEALTH_STATE || (task.verdict||"unknown")===HEALTH_STATE) && (!query || JSON.stringify([task.name,task.task_id,task.checks]).toLowerCase().includes(query)));
  $("health-count").textContent=`${rows.length}/${tasks.length} 个任务`;
  el.innerHTML=`<table class="ops-table"><thead><tr><th>任务</th><th>结论</th><th>检查项</th><th>最近执行记录</th><th>操作</th></tr></thead><tbody>${rows.map(task=>`<tr>
    <th scope="row">${esc(task.name || task.task_id)}<small>${esc(task.task_id)}</small></th>
    <td>${statusBadge(catalogLabel(task.verdict),componentTone(task.verdict))}</td><td>${(task.checks||[]).map(check=>`<div class="check-line"><span>${esc(check.check_id)}</span> ${statusBadge(catalogLabel(check.state),componentTone(check.state))} <span class="faint">${esc(check.checked)}/${esc(check.expected)}</span></div>`).join("") || "未检查"}</td>
    <td><div>运行 ${esc(task.run_id || "未提供")}</div><div>${statusBadge(catalogLabel(task.execution && task.execution.state),componentTone(task.execution && task.execution.state))}</div>${task.verdict!=="healthy"?`<small>${esc((task.reason_codes||[]).join(" · "))}</small>`:""}</td>
    <td>${task.name?`<button data-task="${esc(task.name)}">查看任务</button>`:"未关联"}</td></tr>`).join("")}</tbody></table>`;
}
const CATALOG_DIMENSIONS=["declared","enabled","cached","installed","resolved","discovered","compatible"];
function catalogStateCell(value,dimension){
  const label=catalogLabel(value), symbol=({yes:"是",no:"否",unknown:"未查",not_applicable:"—"})[value || "unknown"] || label;
  const tone=value==='yes'?'ok':value==='no'?(dimension==='compatible'?'warn':'muted'):value==='not_applicable'?'muted':'idle';
  return `<td class="catalog-state" title="${esc(label)}">${value==='not_applicable'?`<span class="faint" aria-label="${esc(label)}">—</span>`:statusBadge(symbol,tone)}</td>`;
}
function catalogRow(source,entry){
  const item=entry || source, status=item.status || {};
  const name=entry ? entry.name || entry.relative_path : catalogName(source);
  const client=entry ? entry.client : catalogClients(source).map(catalogLabel).join(" / ");
  const dependencies=!entry ? (source.dependencies||[]).map(d=>typeof d==="string"?d:d.name||d.id||d.kind||"未命名").join("、") : "";
  return `<tr${entry?' class="catalog-entry"':''}><th scope="row" title="${esc(source.source_id)}">${entry?'↳ ':''}${esc(name)}</th>
    <td>${entry?'入口: ':''}${esc(catalogLabel(item.kind))}<small>${esc(client || "未记录")}</small></td>
    ${CATALOG_DIMENSIONS.map(key=>catalogStateCell(status[key],key)).join("")}
    <td>${entry?'—':esc(catalogLabel(source.sync && source.sync.state))}
    ${!entry && ["mcp_binding","app_connector"].includes(source.kind)?`<dl class="auth-state"><dt>认证</dt><dd>${esc(catalogLabel(status.authenticated))}</dd></dl>`:''}</td>
    <td>${esc(dependencies || "—")}</td></tr>`;
}
function renderCatalogResults(){
  const components=catalogComponents(), catalog=components && components.catalog, el=$("catalog-results");if(!el) return;
  if(!catalog || !catalog.available){el.innerHTML=`<p class="review-notice">${esc(catalog && catalog.reason || "来源目录尚未读取")}</p>`;return;}
  const rows=(catalog.records||[]).filter(catalogMatches).sort((a,b)=>Number(catalogName(a).startsWith("未命名"))-Number(catalogName(b).startsWith("未命名")) || catalogName(a).localeCompare(catalogName(b)));
  $("catalog-count").textContent=`显示 ${rows.length}/${(catalog.records||[]).length}`;
  el.innerHTML=rows.length?`<table class="ops-table catalog-table"><thead><tr><th>名称 / 入口</th><th>类型 / 客户端</th>${CATALOG_DIMENSIONS.map(key=>`<th>${catalogLabel(key)}</th>`).join("")}<th>同步 / 认证</th><th>依赖</th></tr></thead><tbody>${rows.map(source=>catalogRow(source)+(source.entrypoints||[]).map(entry=>catalogRow(source,entry)).join("")).join("")}</tbody></table>`:'<p class="review-empty">没有符合筛选条件的技能或插件</p>';
  if((catalog.problems||[]).length){const note=document.createElement("p");note.className="review-notice";note.textContent=(catalog.problems||[]).map(p=>typeof p==="string"?p:p.reason||p.message||p.code||"来源检查异常").join("；");el.appendChild(note);}
}
