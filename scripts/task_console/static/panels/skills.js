// Classic script module; loaded in app.js dependency order.
let MAINT=null, MAINT_ERROR="", RUNTIME_QUERY="", RUNTIME_STATE="", RUNTIME_SORT="name";
function runtimeRows(rows,kind){
  const query=RUNTIME_QUERY.trim().toLowerCase();
  return rows.filter(row=>{
    const active=kind==="skill"?!row.archived:!!row.enabled;
    return (!query || row.name.toLowerCase().includes(query)) && (!RUNTIME_STATE || (RUNTIME_STATE==="active"?active:!active));
  }).sort((a,b)=>RUNTIME_SORT==="budget" && kind==="skill" ? (b.chars||0)-(a.chars||0) || a.name.localeCompare(b.name) : a.name.localeCompare(b.name));
}
async function loadMaint(){
  $("mtnote").textContent="读取中";MAINT_ERROR="";renderResourceAnchors();
  try{ MAINT=await api("/api/maint"); $("mtnote").textContent=""; }
  catch(e){ MAINT_ERROR=e.message; $("mtnote").textContent="读取失败:"+e.message; renderResourceAnchors(); return; }
  renderMaint();
}

// 页顶锚点后面跟着各块的数目:「技能 22 · 插件 17 · 记忆 120 · 目录 341」。
// 数目用计数格子的五种样子写:读取中「…」、读坏了「!」、没检查「—」、零是淡色的 0,
// 免得一个还没读回来的块在锚点上显示成 0。
function renderResourceAnchors(){
  const anchor=goto=>document.querySelector?.(`#resources .resources-anchors [data-goto="${goto}"]`);
  const paint=(goto,label,cell)=>{ const el=anchor(goto); if(el) el.innerHTML=`${esc(label)} ${cell}`; };
  const maint=part=>MAINT_ERROR?countCell("broken",null,MAINT_ERROR):!MAINT?countCell("loading"):
    MAINT[part] && MAINT[part].available?countCell("ok",(MAINT[part][part]||[]).length):countCell("unchecked");
  paint("#mt-skills","技能",maint("skills"));
  paint("#mt-plugins","插件",maint("plugins"));
  const mem=typeof MEM==="undefined"?null:MEM;
  paint("#membox","记忆",!mem?countCell("loading"):mem.error?countCell("broken",null,mem.error):
    !mem.available?countCell("unchecked"):countCell("ok",(mem.live||0)+(mem.cold||0)));
  const components=catalogComponents(), catalog=components && components.catalog;
  paint("#catalog-box","目录",!components?countCell("loading"):catalog && catalog.available?countCell("ok",(catalog.records||[]).length):countCell("unchecked"));
}

// 机器代号换成人话。原代号仍放在悬停里,排查时照样查得到。不认识的代号按形状猜一个大类,
// 不把 missing_workload_observer 这种字直接摆给人看。
const REASON_LABELS={stale:"记录过期",observed:"已观测到",workload_observed:"观测到工作进程",
  local_process_observed:"观测到本机进程",local_listener_observed:"观测到本机监听端口",
  missing_workload_observer:"缺少观测器",observer_stale:"观测器记录过期",observer_unknown:"观测器状态未知",
  missing_observer_timestamp:"观测器没有时间戳",clock_skew:"时间戳在未来",terminated:"进程已退出",
  disabled:"已停用",query_failed:"查询失败",never_run:"从未运行",skipped_busy:"上次因忙碌跳过",deferred:"已推迟",
  missing_source:"缺少数据来源",invalid_declaration:"声明无效",missing_check:"缺少检查项",missing_field:"缺少字段",
  launch_failed:"启动失败",exit_failed:"退出码失败",execution_failed:"执行失败",receipt_missing:"缺少运行回执",
  listener_missing:"监听端口不在",missing_external_observation:"缺少外部观测"};
function reasonLabel(code){
  const raw=String(code ?? "");
  if(REASON_LABELS[raw]) return REASON_LABELS[raw];
  if(/^missing_/.test(raw) || /_missing$/.test(raw)) return "缺少必要记录";
  if(/_failed$/.test(raw)) return "某一步失败";
  if(/_stale$/.test(raw)) return "记录过期";
  if(/_observed$/.test(raw)) return "已观测到";
  return "其他原因";
}
function reasonTags(codes){
  return (codes||[]).map(code=>`<span class="reason-tag" title="${esc("原始代号："+code)}">${esc(reasonLabel(code))}</span>`).join(" · ");
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
    // 归档是可逆的,用普通按钮;删除才是红的,两个之间隔开一段,免得手一滑点到隔壁。
    // 联接写成字「联接」,不再是一个要悬停才知道意思的 ↗。
    const rows=selected.map(k=>`<div class="mt-r bar skill-row${k.archived?" off":""}">
      <span class="n" title="${esc(k.name)}">${esc(k.name)}${k.linked?` <span class="lk lk-tag" title="目录联接（junction），指向其他仓库">联接</span>`:""}</span>
      <span class="ub" aria-hidden="true"><i style="width:${
        k.archived?0:Math.round((k.chars||0)/maxC*100)}%"></i></span>
      <span class="c">${k.archived?"已归档":fmtNum(k.chars)+" 字"}</span>
      <span class="mt-acts">${k.archived
        ? ibtn("i-restore","恢复技能，下次会话生效",`data-mt="skill.restore" data-name="${esc(k.name)}"`)
        : ibtn("i-archive","归档技能，下次会话不再加载",`data-mt="skill.archive" data-name="${esc(k.name)}"`)}
      <button class="icon-only mini danger" data-delete="skill" data-name="${esc(k.name)}" data-location="${k.archived?'archive':'live'}" title="预览删除范围；联接只移除联接本身"><svg class="ic" aria-hidden="true"><use href="#i-trash"/></svg><span class="control-label">删除…</span></button></span>
    </div>`).join("");
    // 预算条带上自己的名字和数:「描述预算 12,345 / 18,600 字」。原来是一根无名的条,旁边一个没有单位的数。
    const limit=S.budgetLimit || 18600, pct=Math.min(S.budgetPct,100);
    el.innerHTML=`<div class="mt-t">技能 <span class="sub">${matchCount(selected.length,S.skills.length,"个")}</span></div>
      <div class="mt-budget"><span class="lb">描述预算</span>
        <div class="mt-meter" role="meter" aria-label="技能描述预算" aria-valuemin="0" aria-valuemax="${esc(limit)}" aria-valuenow="${esc(S.budgetChars)}"
          aria-valuetext="${esc(`已用 ${fmtNum(S.budgetChars)} 字，上限 ${fmtNum(limit)} 字`)}"><i style="width:${pct.toFixed(1)}%;
          background:${`var(--${toneOf(S.verdict)})`}"></i></div>
        <span class="v">${fmtNum(S.budgetChars)} / ${fmtNum(limit)} 字</span></div>
      ${S.descUnreadable ? `<div class="warn-line">${S.descUnreadable} 份描述读不出来，上面的字数偏低</div>` : ""}
      <div class="mt-rows">${rows || emptyBlock(S.skills.length?"没有匹配的技能":"没有技能",RUNTIME_QUERY||RUNTIME_STATE?{filtered:"resources"}:{})}</div>`;
  }
  // 记忆池那一栏由 renderMem 单独渲染(它有自己的端点和诊断)。
  // --- 插件:整包装卸,disable 可逆,不用重新下载 ---

}

// 仓库详情里的三个动作。它们各自有各自的回显方式,所以不走 maintAct 那条通用路:
// 复制路径根本不需要后端,打开网页由前端直接开一个**已知**的地址,而看改动要把
// 文件列表铺在面板里。硬塞进通用路的结果是三种回显被压成一句 toast。

function renderClientPlugins(){
  if(typeof renderPlugins==='function') renderPlugins();
  else mtUnset($('mt-plugins'),'Claude 插件面板加载失败，请刷新页面重试');
}
function renderMaint(){ if(!MAINT) return; renderSkills(); renderClientPlugins(); renderCatalog(); renderResourceAnchors(); }

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

// 来源覆盖压成一枚芯片:「来源覆盖：部分检查 3/4」,逐项的说法在悬停里。原来是一整句灰字。
function catalogCoverageChip(coverage){
  const c=coverage||{};
  const detail=catalogCoverageText(c);
  if(c.status){
    const tone=["checked","complete"].includes(c.status)?"ok":c.status==="partial"?"warn":"idle";
    return `<span class="coverage-chip" title="${esc(detail)}">${statusBadge("来源覆盖："+catalogLabel(c.status),tone)}</span>`;
  }
  const parts=Object.values(c).filter(part=>part && typeof part==="object");
  const counted=parts.length>0 && parts.every(part=>part.checked!=null && part.expected!=null);
  const checked=parts.reduce((sum,part)=>sum+(Number(part.checked)||0),0), expected=parts.reduce((sum,part)=>sum+(Number(part.expected)||0),0);
  const status=!parts.length?"unchecked":parts.every(part=>["checked","complete"].includes(part.status))?"checked"
    :parts.some(part=>["checked","complete","partial"].includes(part.status))?"partial":"unchecked";
  const tone=status==="checked"?"ok":status==="partial"?"warn":"idle";
  return `<span class="coverage-chip" title="${esc(detail)}">${statusBadge(`来源覆盖：${catalogLabel(status)}${counted?` ${checked}/${expected}`:""}`,tone)}</span>`;
}

let CATALOG_QUERY="", CATALOG_KIND="", CATALOG_CLIENT="", CATALOG_STATE="", HEALTH_STATE="";
// 表头可点排序;默认(空 key)是有问题的在前、没查全的其次、正常的最后,同档按名字。
// 展开过的行记在 CATALOG_OPEN 里,重画时保持展开。
let CATALOG_SORT={key:"",asc:true};
const CATALOG_OPEN=new Set();
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
  // 目录不再有自己的搜索框:页顶那一个同时筛技能、插件和这里(见 startResources)。
  // 数目只写一处:「显示 N / 共 M 项」(#catalog-count)。原来旁边还有一个「M 项」,同一个数读两遍。
  el.innerHTML=`<div class="catalog-tools"><select id="catalog-kind" aria-label="来源类型"><option value="">全部类型</option>${Object.entries(catalog && catalog.statistics || {}).map(([kind,count])=>`<option value="${esc(kind)}"${CATALOG_KIND===kind?" selected":""}>${esc(catalogLabel(kind))} (${count})</option>`).join("")}</select>
      <select id="catalog-client" aria-label="涉及客户端"><option value="">全部客户端</option>${clients.map(client=>`<option value="${esc(client)}"${client===CATALOG_CLIENT?" selected":""}>${esc(catalogLabel(client))}</option>`).join("")}</select>
      <select id="catalog-state" aria-label="组件状态"><option value="">全部状态</option>${states.map(([value,label])=>`<option value="${esc(value)}"${value===CATALOG_STATE?" selected":""}>${esc(label)}</option>`).join("")}</select><button class="icon-only" data-reset-filters="catalog" title="清除筛选"><svg class="ic" aria-hidden="true"><use href="#i-filter-clear"/></svg><span class="control-label">清除筛选</span></button><span id="catalog-count">${catalog && catalog.available?"":"尚未读取目录"}</span>${catalog && catalog.available?catalogCoverageChip(catalog.coverage):""}</div>
    <div id="catalog-results" class="catalog-results"></div>
    <div class="catalog-heading health-heading"><h2>自动化检查结果 <span style="color:var(--${coverageTone})">${esc(cov.checked ?? "?")}/${esc(cov.expected ?? "?")}</span></h2><span>异常 ${problemCount} · 未检查 ${uncheckedCount}</span>
      <select id="health-state" aria-label="健康检查状态"><option value="">全部结论</option>${["healthy","degraded","unhealthy","unknown"].map(state=>`<option value="${state}"${state===HEALTH_STATE?" selected":""}>${catalogLabel(state)}</option>`).join("")}</select><span id="health-count"></span></div>
    <div id="health-results" class="catalog-results"></div>`;
  $("catalog-kind").addEventListener("change",event=>{CATALOG_KIND=event.target.value;renderCatalogResults();});
  $("catalog-client").addEventListener("change",event=>{CATALOG_CLIENT=event.target.value;renderCatalogResults();});
  $("catalog-state").addEventListener("change",event=>{CATALOG_STATE=event.target.value;renderCatalogResults();});
  $("health-state").addEventListener("change",event=>{HEALTH_STATE=event.target.value;renderCatalogHealth();});
  renderCatalogResults();renderCatalogHealth();
  renderResourceAnchors();
  // 工具栏整块重画,清除筛选按钮是新造的,要当场按现有筛选定亮灭。
  if(typeof syncResetFilters==='function') syncResetFilters();
}
function renderCatalogHealth(){
  const components=catalogComponents(), el=$("health-results");if(!el) return;
  if(!components || !components.available){el.innerHTML=`<p class="review-notice">${esc(components && components.reason || "组件证据不可用")}</p>`;return;}
  const tasks=components.tasks || [], query=CATALOG_QUERY.trim().toLowerCase();
  const rows=tasks.filter(task=>(!HEALTH_STATE || (task.verdict||"unknown")===HEALTH_STATE) && (!query || JSON.stringify([task.name,task.task_id,task.checks]).toLowerCase().includes(query)));
  $("health-count").textContent=matchCount(rows.length,tasks.length,"个任务");
  // 原因代号换成人话(记录过期、缺少观测器……),代号本身在悬停里。
  el.innerHTML=`<table class="ops-table health-table"><thead><tr><th>任务</th><th>结论</th><th>检查项</th><th>最近执行记录</th><th>操作</th></tr></thead><tbody>${rows.map(task=>`<tr>
    <th scope="row" data-label="任务">${esc(task.name || task.task_id)}<small>${esc(task.task_id)}</small></th>
    <td data-label="结论">${statusBadge(catalogLabel(task.verdict),componentTone(task.verdict))}</td><td data-label="检查项">${(task.checks||[]).map(check=>`<div class="check-line"><span>${esc(check.check_id)}</span> ${statusBadge(catalogLabel(check.state),componentTone(check.state))} <span class="faint">${esc(check.checked)}/${esc(check.expected)}</span></div>`).join("") || "未检查"}</td>
    <td data-label="最近执行记录"><div>${task.run_id?`运行编号 ${esc(task.run_id)}`:"没有运行编号"}</div><div>${statusBadge(catalogLabel(task.execution && task.execution.state),componentTone(task.execution && task.execution.state))}</div>${task.verdict!=="healthy" && (task.reason_codes||[]).length?`<small>${reasonTags(task.reason_codes)}</small>`:""}</td>
    <td data-label="操作">${task.name?`<button class="icon-only" data-task="${esc(task.name)}" title="查看任务"><svg class="ic" aria-hidden="true"><use href="#i-eye"/></svg><span class="control-label">查看任务</span></button>`:"未关联"}</td></tr>`).join("")}</tbody></table>`;
}
const CATALOG_DIMENSIONS=["declared","enabled","cached","installed","resolved","discovered","compatible"];
// 这几项为「否」时是要人管的问题;启用和缓存为「否」只是状态(插件停着、还没缓存),不算问题。
const CATALOG_PROBLEMS={declared:"未登记",installed:"未安装",resolved:"路径无效",discovered:"客户端不可见",compatible:"不兼容"};
// 一行的结论只有三种说法:有问题(说出是哪几项)、没查全(查了几项/该查几项)、正常。
// ⚠ 有一项没查,就不许说「正常」:七项里六项是、一项未知,和七项全是不是一回事。
function catalogSummary(status){
  const s=status||{};
  const applicable=CATALOG_DIMENSIONS.filter(key=>s[key]!=="not_applicable");
  const problems=Object.keys(CATALOG_PROBLEMS).filter(key=>s[key]==="no");
  const known=applicable.filter(key=>s[key]==="yes" || s[key]==="no");
  if(problems.length) return {rank:0,tone:"warn",text:"有问题："+problems.map(key=>CATALOG_PROBLEMS[key]).join("、")};
  if(known.length<applicable.length || !applicable.length) return {rank:1,tone:"idle",text:`未全查 ${known.length}/${applicable.length}`};
  return {rank:2,tone:"ok",text:s.enabled==="no"?"正常（未启用）":"正常"};
}
function catalogDim(value,dimension){
  const label=catalogLabel(value), symbol=({yes:"是",no:"否",unknown:"未查",not_applicable:"—"})[value || "unknown"] || label;
  const tone=value==='yes'?'ok':value==='no'?(CATALOG_PROBLEMS[dimension]?'warn':'muted'):value==='not_applicable'?'muted':'idle';
  return `<span class="catalog-dim"><span class="lb">${esc(catalogLabel(dimension))}</span>${statusBadge(symbol,tone)}</span>`;
}
// 名字以 .system/ 开头的是客户端自带的内部组件,单独成组排在最后:原来它们因为开头的点排在最前面。
const catalogSystem=source=>catalogName(source).startsWith(".system/");
function catalogRow(source){
  const open=CATALOG_OPEN.has(source.source_id), entries=source.entrypoints||[], status=source.status||{}, summary=catalogSummary(status);
  const dependencies=(source.dependencies||[]).map(d=>typeof d==="string"?d:d.name||d.id||d.kind||"未命名").join("、");
  const entryKinds=[...new Set(entries.map(entry=>catalogLabel(entry.kind)))].join("、");
  let html=`<tr class="catalog-row${open?" open":""}"><th scope="row" data-label="名称" title="${esc(source.source_id)}">
      <button type="button" class="catalog-toggle" data-catalog-toggle="${esc(source.source_id)}" aria-expanded="${open}" title="${open?"收起":"展开"}七项检查${entries.length?`和 ${entries.length} 个入口`:""}"><span class="caret" aria-hidden="true">${open?"▾":"▸"}</span>${esc(catalogName(source))}</button>${entries.length?`<small>${entries.length} 个入口：${esc(entryKinds)}</small>`:""}</th>
    <td data-label="类型 / 客户端">${esc(catalogLabel(source.kind))}<small>${esc(catalogClients(source).map(catalogLabel).join(" / ") || "未记录")}</small></td>
    <td data-label="状态">${statusBadge(summary.text,summary.tone)}</td>
    <td data-label="同步 / 认证">${esc(catalogLabel(source.sync && source.sync.state))}
    ${["mcp_binding","app_connector"].includes(source.kind)?`<dl class="auth-state"><dt>认证</dt><dd>${esc(catalogLabel(status.authenticated))}</dd></dl>`:''}</td>
    <td data-label="依赖">${esc(dependencies || "—")}</td></tr>`;
  if(open){
    // 展开后才摊出七项检查和每个入口(↳)。入口只报它自己有记录的那几项。
    html+=`<tr class="catalog-detail"><td colspan="5"><div class="catalog-dims">${CATALOG_DIMENSIONS.map(key=>catalogDim(status[key],key)).join("")}</div>${entries.map(entry=>{
      const own=entry.status||{};
      return `<div class="catalog-entry catalog-entry-line"><span>↳ 入口：${esc(catalogLabel(entry.kind))} · ${esc(entry.name || entry.relative_path || "未命名")}</span><small>${esc(catalogLabel(entry.client) || "未记录")}</small>
        <span class="catalog-dims">${CATALOG_DIMENSIONS.filter(key=>own[key] && own[key]!=="unknown").map(key=>catalogDim(own[key],key)).join("") || '<span class="faint">没有入口自己的检查记录</span>'}</span></div>`;
    }).join("")}</td></tr>`;
  }
  return html;
}
function catalogCompare(a,b){
  const name=(a,b)=>Number(catalogName(a).startsWith("未命名"))-Number(catalogName(b).startsWith("未命名")) || catalogName(a).localeCompare(catalogName(b));
  const dir=CATALOG_SORT.asc?1:-1;
  if(CATALOG_SORT.key==="name") return dir*name(a,b);
  if(CATALOG_SORT.key==="kind") return dir*catalogLabel(a.kind).localeCompare(catalogLabel(b.kind)) || name(a,b);
  if(CATALOG_SORT.key==="state") return dir*(catalogSummary(a.status).rank-catalogSummary(b.status).rank) || name(a,b);
  return catalogSummary(a.status).rank-catalogSummary(b.status).rank || name(a,b);
}
function renderCatalogResults(){
  const components=catalogComponents(), catalog=components && components.catalog, el=$("catalog-results");if(!el) return;
  if(!catalog || !catalog.available){el.innerHTML=`<p class="review-notice">${esc(catalog && catalog.reason || "来源目录尚未读取")}</p>`;return;}
  const all=catalog.records||[], rows=all.filter(catalogMatches).sort(catalogCompare);
  $("catalog-count").textContent=matchCount(rows.length,all.length);
  const main=rows.filter(source=>!catalogSystem(source)), system=rows.filter(catalogSystem);
  const sortHead=(key,label)=>{
    const on=CATALOG_SORT.key===key, sort=on?(CATALOG_SORT.asc?"ascending":"descending"):"none";
    return `<th aria-sort="${sort}"><button type="button" class="sort-head" data-catalog-sort="${key}" title="按${label}排序${on?"，再点一下反过来":""}">${label}<span aria-hidden="true">${on?(CATALOG_SORT.asc?" ▲":" ▼"):""}</span></button></th>`;
  };
  el.innerHTML=rows.length?`<table class="ops-table catalog-table"><thead><tr>${sortHead("name","名称")}${sortHead("kind","类型 / 客户端")}${sortHead("state","状态")}<th>同步 / 认证</th><th>依赖</th></tr></thead>
    <tbody>${main.map(catalogRow).join("")}</tbody>${system.length?`<tbody class="catalog-system"><tr class="catalog-group"><th scope="rowgroup" colspan="5">客户端内部组件（.system） <span class="faint">${system.length}</span></th></tr>${system.map(catalogRow).join("")}</tbody>`:""}</table>`
    :emptyBlock("没有符合筛选条件的技能或插件",{filtered:CATALOG_QUERY.trim()?"resources":"catalog"});
  if((catalog.problems||[]).length){const note=document.createElement("p");note.className="review-notice";note.textContent=(catalog.problems||[]).map(p=>typeof p==="string"?p:p.reason||p.message||p.code||"来源检查异常").join("；");el.appendChild(note);}
}

// ── 客户端技能与记忆这一屏的挂点 ──(从 events.js 搬来,原因见 tasks.js 的 startTasksPage 上方)
function startResources(){
  // 一个搜索词同时写进两个筛选:技能与插件按名字,资源目录按名称、路径和依赖。
  $('resources-search').addEventListener('input',event=>{
    RUNTIME_QUERY=CATALOG_QUERY=event.target.value;
    renderSkills();renderClientPlugins();renderCatalogResults();renderCatalogHealth();
  });
  $('runtime-state').addEventListener('change',event=>{RUNTIME_STATE=event.target.value;renderSkills();renderClientPlugins();});
  $('runtime-sort').addEventListener('change',event=>{RUNTIME_SORT=event.target.value;renderSkills();});
  // 目录表头排序和逐行展开。目录整块重画,所以挂在不重画的 #mt-catalog 上。
  $('mt-catalog').addEventListener('click',event=>{
    const sort=event.target.closest('[data-catalog-sort]');
    if(sort){
      const key=sort.dataset.catalogSort;
      CATALOG_SORT=CATALOG_SORT.key===key?{key,asc:!CATALOG_SORT.asc}:{key,asc:true};
      renderCatalogResults();$('catalog-results').querySelector(`[data-catalog-sort="${key}"]`)?.focus();return;
    }
    const toggle=event.target.closest('[data-catalog-toggle]');
    if(toggle){
      const id=toggle.dataset.catalogToggle;
      if(CATALOG_OPEN.has(id)) CATALOG_OPEN.delete(id); else CATALOG_OPEN.add(id);
      renderCatalogResults();
      [...$('catalog-results').querySelectorAll('[data-catalog-toggle]')].find(button=>button.dataset.catalogToggle===id)?.focus();
    }
  });
  if(typeof startMemory==='function') startMemory();
  renderResourceAnchors();
}
