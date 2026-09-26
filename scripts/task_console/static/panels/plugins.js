// Classic script module; loaded in app.js dependency order.
function renderPlugins(){
  if(!MAINT) return;
  const P=MAINT.plugins, pe=$("mt-plugins");
  if(!P.available){ mtUnset(pe,P.reason); }
  else{
    const selected=runtimeRows(P.plugins,"plugin");
    pe.innerHTML=`<div class="mt-t">插件 <b>${selected.length}/${P.plugins.length}</b>
        <span class="sub">当前匹配</span></div>
      <div class="mt-rows">${selected.map(p=>{
        const nm=p.name.split("@")[0];
        return `<div class="mt-r${p.enabled?"":" off"}">
          <span class="n" title="${esc(p.name)}">${esc(nm)}</span>
          <span class="sub">${esc(p.scope || '范围未检查')}</span>
          ${p.scope && p.scope!=='user'?'<span class="faint">请在所属项目管理</span>':p.enabled
            ? ibtn("i-off","禁用这个插件",`data-mt="plugin.disable" data-name="${esc(p.name)}"`,"danger")
            : ibtn("i-on","启用这个插件",`data-mt="plugin.enable" data-name="${esc(p.name)}"`)}
          ${p.removal?`<button class="mini danger" data-delete="${esc(p.removal.kind)}" data-name="${esc(p.removal.name)}" data-location="${esc(p.removal.location)}" title="${p.removal.kind==='skill'?'预览技能删除范围；联接只移除联接本身':'预览卸载；保留插件运行数据'}">${p.removal.kind==='skill'?'删除技能…':'卸载…'}</button>`:`<span class="faint">${esc(p.removalReason || '')}</span>`}
          ${(p.issues || []).length?`<span class="warn">加载异常：${esc(p.issues.join('；'))}</span>`:''}
        </div>`;}).join("") || '<p class="review-empty">没有匹配的插件</p>'}</div>`;
  }
}
