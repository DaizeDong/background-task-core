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
  repository:"仓库",blocked:"受阻",unsupported:"尚不支持",skill:"技能",plugin:"插件",mcp_binding:"MCP 连接",app_connector:"App 连接",agent_template:"角色模板",
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

let CATALOG_QUERY="", CATALOG_KIND="", CATALOG_LIMIT=12;
function catalogComponents(){return (typeof COMPONENTS !== "undefined" && COMPONENTS) || (MAINT && MAINT.components);}
function renderCatalog(){
  const el=$("mt-catalog"); if(!el) return;
  const components=catalogComponents(), catalog=components && components.catalog;
  const tasks=components && components.tasks || [], cov=components && components.coverage || {};
  const counts=tasks.reduce((acc,task)=>{acc[task.verdict || "unknown"]=(acc[task.verdict || "unknown"]||0)+1;return acc;},{});
  el.innerHTML=`<div class="catalog-heading"><h2>组件与来源</h2><span>${catalog && catalog.available ? (catalog.records||[]).length+" 个来源" : "来源未检查"}</span></div>
    <p class="review-caption">查找技能、插件和连接，展开查看来源与依赖。</p>
    <details class="catalog-health"><summary>健康检查 ${esc(cov.checked ?? "?")}/${esc(cov.expected ?? "?")} <span class="warn">异常 ${counts.unhealthy||0} · 需留意 ${counts.degraded||0} · 未检查 ${counts.unknown||0}</span></summary>
      <p>${esc(catalogCoverageText(catalog && catalog.coverage))}</p>${!components || !components.available ? `<p>${esc(components && components.reason || "组件证据不可用")}</p>` : tasks.map(task=>`<details class="catalog-item"><summary>${esc(task.name || task.task_id)} <span>${esc(catalogLabel(task.verdict))}</span></summary>
        <p>任务标识 ${esc(task.task_id)}</p><ul>${(task.checks||[]).map(check=>`<li>${esc(check.check_id)}：${esc(catalogLabel(check.state))} (${check.checked}/${check.expected})</li>`).join("")}</ul>
        <details class="evidence"><summary>执行与检查证据</summary><pre>${esc(JSON.stringify({execution:task.execution,run_id:task.run_id,last_success_run_id:task.last_success_run_id,checks:task.checks,unexpected_checks:task.unexpected_checks,reason_codes:task.reason_codes},null,2))}</pre></details></details>`).join("")}
    </details>
    <div class="catalog-tools"><input type="search" id="catalog-search" aria-label="搜索组件来源" placeholder="搜索名称、来源或依赖" value="${esc(CATALOG_QUERY)}">
      <select id="catalog-kind" aria-label="来源类型"><option value="">全部类型</option>${Object.entries(catalog && catalog.statistics || {}).map(([kind,count])=>`<option value="${esc(kind)}"${CATALOG_KIND===kind?" selected":""}>${esc(catalogLabel(kind))} (${count})</option>`).join("")}</select><span id="catalog-count"></span></div><div id="catalog-results"></div>`;
  $("catalog-search").addEventListener("input",event=>{CATALOG_QUERY=event.target.value;CATALOG_LIMIT=12;renderCatalogResults();});
  $("catalog-kind").addEventListener("change",event=>{CATALOG_KIND=event.target.value;CATALOG_LIMIT=12;renderCatalogResults();});
  renderCatalogResults();
}
function catalogName(source){
  return source.registry_key || (source.relative_path && source.relative_path!=="." ? source.relative_path : null)
    || `未命名${catalogLabel(source.kind)} (${source.source_id.split(":").pop().slice(0,8)})`;
}
function renderCatalogResults(){
  const components=catalogComponents(), catalog=components && components.catalog, el=$("catalog-results");if(!el) return;
  if(!catalog || !catalog.available){el.innerHTML=`<p class="review-notice">${esc(catalog && catalog.reason || "来源目录尚未读取")}</p>`;return;}
  const query=CATALOG_QUERY.trim().toLowerCase();
  const rows=(catalog.records||[]).filter(source=>(!CATALOG_KIND || source.kind===CATALOG_KIND) && (!query || JSON.stringify([source.source_id,source.registry_key,source.relative_path,catalogName(source),source.dependencies]).toLowerCase().includes(query))).sort((a,b)=>Number(catalogName(a).startsWith("未命名"))-Number(catalogName(b).startsWith("未命名")) || catalogName(a).localeCompare(catalogName(b)));
  $("catalog-count").textContent=`显示 ${Math.min(CATALOG_LIMIT,rows.length)}/${rows.length}`;
  const dimensions=["declared","enabled","cached","installed","resolved","discovered","compatible"];
  el.innerHTML=rows.slice(0,CATALOG_LIMIT).map(source=>`<details class="catalog-item"><summary><strong>${esc(catalogName(source))}</strong><span>${esc(catalogLabel(source.kind))}</span></summary>
    <dl class="catalog-evidence"><dt>来源标识</dt><dd>${esc(source.source_id)}</dd><dt>所属客户端</dt><dd>${esc(source.origin && source.origin.client || "未记录")}</dd><dt>依赖</dt><dd>${esc((source.dependencies||[]).map(d=>typeof d==="string"?d:d.name||d.id||d.kind||"未命名").join("、") || "未声明")}</dd>
      ${dimensions.map(key=>`<dt>${catalogLabel(key)}</dt><dd>${esc(catalogLabel((source.status||{})[key]))}</dd>`).join("")}
      ${["mcp_binding","app_connector"].includes(source.kind)?`<dt>认证</dt><dd>${esc(catalogLabel((source.status||{}).authenticated))}</dd>`:""}
      <dt>同步</dt><dd>${esc(catalogLabel(source.sync && source.sync.state))}</dd></dl>
    <ul>${(source.entrypoints||[]).map(entry=>`<li>${esc(catalogLabel(entry.kind))} · ${esc(entry.name || entry.relative_path)} (${esc(entry.client || "客户端未记录")})<div>${dimensions.map(key=>`${catalogLabel(key)}：${esc(catalogLabel((entry.status||{})[key]))}`).join(" · ")}</div></li>`).join("")}</ul></details>`).join("") || '<p class="review-empty">没有匹配的来源，试试其他名称或类型。</p>';
  if(rows.length>CATALOG_LIMIT){const more=document.createElement("button");more.textContent="再显示 12 项";more.className="review-more";more.addEventListener("click",()=>{CATALOG_LIMIT+=12;renderCatalogResults();});el.appendChild(more);}
  if((catalog.problems||[]).length){const note=document.createElement("p");note.className="review-notice";note.textContent=(catalog.problems||[]).map(p=>typeof p==="string"?p:p.reason||p.message||p.code||"来源检查异常").join("；");el.appendChild(note);}
}
