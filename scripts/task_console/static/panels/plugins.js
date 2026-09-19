// Classic script module; loaded in app.js dependency order.
function renderPlugins(){
  if(!MAINT) return;
  const P=MAINT.plugins, pe=$("mt-plugins");
  if(!P.available){ mtUnset(pe,P.reason); }
  else{
    const on=P.plugins.filter(p=>p.enabled).length;
    pe.innerHTML=`<div class="mt-t">插件 <b>${on}</b>
        <span class="sub">共 ${P.plugins.length}</span></div>
      <div class="mt-rows">${P.plugins.map(p=>{
        const nm=p.name.split("@")[0];
        return `<div class="mt-r${p.enabled?"":" off"}">
          <span class="n" title="${esc(p.name)}">${esc(nm)}</span>
          ${p.enabled
            ? ibtn("i-off","禁用这个插件",`data-mt="plugin.disable" data-name="${esc(p.name)}"`,"danger")
            : ibtn("i-on","启用这个插件",`data-mt="plugin.enable" data-name="${esc(p.name)}"`)}
        </div>`;}).join("")}</div>`;
  }
}
