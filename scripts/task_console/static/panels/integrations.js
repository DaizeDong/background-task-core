// Console integration inventory; never installs plugins or runs business jobs.
let INTEGRATIONS=null;
const INTEGRATION_STATES={ready:'接口可读',unchecked:'未检查',unconfigured:'未配置',unavailable:'读取失败',stale:'本次失败，有历史成功记录'};
const INTEGRATION_LAYERS={core:'控制台核心',shared:'共享服务',adapter:'数据适配器',library:'库依赖',business:'通知与线索来源',origin:'其他记录来源标签'};
async function loadIntegrations(){
  const note=$('integration-note');note.textContent='读取接入信息';
  try{INTEGRATIONS=await api('/api/integrations');renderIntegrations();note.textContent='';}
  catch(error){note.textContent='读取失败：'+error.message;}
}
function renderIntegrations(){
  if(!INTEGRATIONS) return;
  const stamp=value=>value?new Date(value*1000).toLocaleString():'尚无成功记录';
  $('integration-list').innerHTML=Object.entries(INTEGRATION_LAYERS).map(([layer,label])=>{
    const rows=INTEGRATIONS.items.filter(row=>row.layer===layer);
    const heading=`${label} <span class="faint">${rows.length}</span>`;
    const content=(rows.length?`<div class="ops-scroll"><table class="ops-table"><thead><tr><th>接入 / 依赖</th><th>提供的内容</th><th>读取状态</th><th>操作</th></tr></thead><tbody>${rows.map(row=>{
      const state=row.connection||{}, tone=state.state==='ready'?'ok':['stale','unavailable'].includes(state.state)?'warn':'idle';
      return `<tr><th scope="row">${esc(row.label)}<small>${row.depends_on.length?'依赖：'+esc(row.depends_on.join('、')):'独立'}</small></th>
        <td>${esc(row.provides)}${row.record_count!=null?`<small>${esc(row.record_count)} 条来源记录</small>`:''}<small>${esc(row.actions)}</small></td>
        <td>${statusBadge(INTEGRATION_STATES[state.state]||state.state,tone)}${!['core','library','business','origin'].includes(row.layer)?`<small>上次成功：${stamp(state.last_success)}</small>`:''}${state.reason?`<small>${esc(state.reason)}</small>`:''}</td>
        <td><button class="icon-only" data-integration-open="${esc(row.view)}" data-integration-source="${esc(row.source||'')}" title="查看"><svg class="ic" aria-hidden="true"><use href="#i-eye"/></svg><span class="control-label">查看</span></button>${row.endpoint?` <button class="icon-only" data-integration-check="${esc(row.endpoint)}" title="重读"><svg class="ic" aria-hidden="true"><use href="#i-refresh"/></svg><span class="control-label">重读</span></button>`:''}</td></tr>`;
    }).join('')}</tbody></table></div>`:`<p class="review-empty">${INTEGRATIONS.coverage.work_sources==='unavailable'?'工作服务不可读':'暂无来源记录'}</p>`);
    return layer==='origin'?`<details><summary>${heading}</summary>${content}</details>`:`<h3>${heading}</h3>${content}`;
  }).join('');
}
function startIntegrations(){
  document.addEventListener('click',async event=>{
    const open=event.target.closest('[data-integration-open]');
    if(open){
      if(open.dataset.integrationSource) setWorkFilters({source:open.dataset.integrationSource,role:'all',state:''});
      showView(open.dataset.integrationOpen,true);return;
    }
    const check=event.target.closest('[data-integration-check]');
    if(!check || check.disabled) return;
    check.disabled=true;
    try{await api(check.dataset.integrationCheck);}
    catch(error){toast('读取失败：'+error.message,'bad');}
    finally{check.disabled=false;await loadIntegrations();}
  });
}
