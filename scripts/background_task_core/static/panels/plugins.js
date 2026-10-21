// Classic script module; loaded in app.js dependency order.
// 插件的启用状态画成一个开关(role=switch):开关本身说「现在是开是关」,点它就切换。
// 原来是一个画着动作的图标(⊘ = 禁用),于是已启用的插件旁边挂着一个「⊘」,读起来像「已禁用」。
// 启用状态没读到(不是 true 也不是 false)时写一个「?」,不画成一个关着的开关:没读到不等于关着。
function pluginSwitch(p,name){
  if(typeof p.enabled!=="boolean")
    return `<span class="plugin-unknown" title="启用状态没有读到">?</span>`;
  const action=p.enabled?"禁用":"启用", label=`${name} 已启用`;
  // 项目或托管范围的插件不在这里开关;开关照样画出当前状态,只是灰着并说明原因。
  const managed=p.scope && p.scope!=="user";
  const title=managed?disabledTitle(label,"请在所属项目管理"):`${p.enabled?"已启用":"已停用"}，点一下${action}`;
  return `<button type="button" class="mt-switch" role="switch" aria-checked="${p.enabled}" aria-label="${esc(label)}"
    data-mt="${p.enabled?"plugin.disable":"plugin.enable"}" data-name="${esc(p.name)}"${managed?" disabled":""} title="${esc(title)}"><span class="knob" aria-hidden="true"></span></button>`;
}
function renderPlugins(){
  if(!MAINT) return;
  const P=MAINT.plugins, pe=$("mt-plugins");
  if(!P.available){ mtUnset(pe,P.reason); }
  else{
    const selected=runtimeRows(P.plugins,"plugin");
    pe.innerHTML=`<div class="mt-t">插件 <span class="sub">${matchCount(selected.length,P.plugins.length,"个")}</span></div>
      <div class="mt-rows">${selected.map(p=>{
        const nm=p.name.split("@")[0];
        // 范围只在不是 user 时写出来:每一行都印「user」等于没印。范围没读到时照写「范围未检查」。
        return `<div class="mt-r plugin-row${p.enabled===false?" off":""}">
          ${pluginSwitch(p,nm)}
          <span class="n" title="${esc(p.name)}">${esc(nm)}</span>
          ${p.scope==="user"?"":`<span class="sub">${esc(p.scope || '范围未检查')}</span>`}
          ${(p.issues || []).length?`<span class="warn">加载异常：${esc(p.issues.join('；'))}</span>`:''}
          <span class="mt-acts">${p.removal?`<button class="mini danger icon-only" data-delete="${esc(p.removal.kind)}" data-name="${esc(p.removal.name)}" data-location="${esc(p.removal.location)}" title="${p.removal.kind==='skill'?'预览技能删除范围；联接只移除联接本身':'预览卸载；保留插件运行数据'}" aria-label="${p.removal.kind==='skill'?'删除技能':'卸载插件'}"><svg class="ic" aria-hidden="true"><use href="#i-trash"/></svg></button>`:`<span class="faint">${esc(p.removalReason || '')}</span>`}</span>
        </div>`;}).join("") || emptyBlock(P.plugins.length?"没有匹配的插件":"没有插件",RUNTIME_QUERY||RUNTIME_STATE?{filtered:"resources"}:{})}</div>`;
  }
}
