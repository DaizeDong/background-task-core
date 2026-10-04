// Console integration inventory; never installs plugins or runs business jobs.
let INTEGRATIONS=null;
const INTEGRATION_STATES={ready:'接口可读',unchecked:'未检查',unconfigured:'未配置',unavailable:'读取失败',stale:'本次失败，有历史成功记录'};
const INTEGRATION_LAYERS={core:'控制台核心',shared:'共享服务',adapter:'数据适配器',library:'库依赖',business:'通知与线索来源',origin:'其他记录来源标签'};
// 后端给的原因有时是一句中文,有时是 work_reader_failed 这种机器代号。代号换成人话,原文进悬停。
const INTEGRATION_REASONS={work_reader_failed:'工作服务读取失败',work_reader_not_configured:'工作服务未配置',
  work_binding_invalid:'工作服务的接入配置无效',work_contract_invalid:'工作服务返回的数据格式不符',not_configured:'未配置'};
// 没读过的先排前面:接口可读的那些是不用管的,要人看的是读失败、过期、未配置和未检查。
const INTEGRATION_ORDER={unavailable:0,stale:1,unconfigured:2,unchecked:3,ready:4};
// 每一行重读的结果闪一下:「刚刚读取成功」或读不到的原因。键是那一行的 endpoint。
const INTEGRATION_FLASH=new Map();
async function loadIntegrations(){
  const note=$('integration-note');note.textContent='读取接入信息';
  try{INTEGRATIONS=await api('/api/integrations');renderIntegrations();note.textContent='';}
  catch(error){note.textContent='读取失败：'+error.message;}
}
function integrationReason(reason){
  const raw=String(reason ?? '').trim();
  if(!raw) return '';
  if(!/^[a-z0-9_]+$/.test(raw)) return `<small>${esc(raw)}</small>`;
  const text=INTEGRATION_REASONS[raw] || (typeof reasonLabel==='function' ? reasonLabel(raw) : '原因未识别');
  return `<small title="${esc('原始代号：'+raw)}">${esc(text)}</small>`;
}
function integrationRow(row){
  const state=row.connection||{}, tone=state.state==='ready'?'ok':['stale','unavailable'].includes(state.state)?'warn':'idle';
  // 「上次成功」只对读过的那几层有意义;没检查过的行写一句「尚无成功记录」只是把「未检查」再说一遍。
  const showLast=!['core','library','business','origin'].includes(row.layer) && state.state!=='unchecked';
  const last=state.last_success?`<small>上次成功：${timeTag(state.last_success)}</small>`:'<small>上次成功：尚无成功记录</small>';
  const flash=row.endpoint && INTEGRATION_FLASH.get(row.endpoint);
  const flashHtml=flash?`<small class="integration-flash ${flash.ok?'ok':'bad'}" role="status">${esc(flash.text)}</small>`:'';
  return `<tr data-integration-row="${esc(row.endpoint||'')}"><th scope="row" data-label="接入 / 依赖">${esc(row.label)}<small>${row.depends_on.length?'依赖：'+esc(row.depends_on.join('、')):'独立'}</small></th>
    <td data-label="提供的内容">${esc(row.provides)}${row.record_count!=null?`<small>${esc(row.record_count)} 条来源记录</small>`:''}<small>${esc(row.actions)}</small></td>
    <td data-label="读取状态">${statusBadge(INTEGRATION_STATES[state.state]||state.state||'未检查',tone)}${showLast?last:''}${integrationReason(state.reason)}${flashHtml}</td>
    <td data-label="操作" class="integration-acts"><button class="icon-only" data-integration-open="${esc(row.view)}" data-integration-source="${esc(row.source||'')}" title="查看"><svg class="ic" aria-hidden="true"><use href="#i-eye"/></svg><span class="control-label">查看</span></button>${row.endpoint?` <button class="icon-only" data-integration-check="${esc(row.endpoint)}" title="重新读取这一项"><svg class="ic" aria-hidden="true"><use href="#i-refresh"/></svg><span class="control-label">重读</span></button>`:''}</td></tr>`;
}
// 一张表,各层是表里的分组标题行:以前每层一张表、各自定列宽,「读取状态」和「操作」两列在层与层之间左右跳。
// 也不再有内部滚动框:页面本身有的是地方,九行的适配器表被截在 440px 里反而要人去找滚动条。
function renderIntegrations(){
  if(!INTEGRATIONS) return;
  const body=Object.entries(INTEGRATION_LAYERS).map(([layer,label])=>{
    const rows=INTEGRATIONS.items.filter(row=>row.layer===layer)
      .map((row,index)=>({row,index,rank:INTEGRATION_ORDER[(row.connection||{}).state] ?? 3}))
      .sort((a,b)=>a.rank-b.rank || a.index-b.index).map(item=>item.row);
    const head=`<tr class="integration-layer"><th scope="rowgroup" colspan="4">${esc(label)} <span class="faint">${rows.length}</span></th></tr>`;
    if(rows.length) return head+rows.map(integrationRow).join('');
    // 这一层是空的。读不到工作服务和「读到了、确实没有记录」是两回事:前者是故障,用告警样式;后者才是灰色的空。
    const broken=layer==='business' && INTEGRATIONS.coverage && INTEGRATIONS.coverage.work_sources==='unavailable';
    const why=broken && INTEGRATIONS.coverage.work_reason ? integrationReason(INTEGRATIONS.coverage.work_reason) : '';
    return head+`<tr class="integration-empty"><td colspan="4">${broken
      ?`<p class="review-notice">工作服务不可读，这一层的来源记录没有读到${why?'：'+why:''}</p>`
      :emptyBlock('暂无来源记录')}</td></tr>`;
  }).join('');
  $('integration-list').innerHTML=`<table class="ops-table integration-table"><thead><tr><th>接入 / 依赖</th><th>提供的内容</th><th>读取状态</th><th>操作</th></tr></thead><tbody>${body}</tbody></table>`;
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
    // 重读期间这一行的按钮灰着、图标转着;读完整张表重画,但滚动位置不动,结果闪在这一行的状态格里。
    const endpoint=check.dataset.integrationCheck, top=window.scrollY;
    setDisabled(check,'正在重新读取这一项');check.classList.add('spinning');
    INTEGRATION_FLASH.delete(endpoint);
    try{await api(endpoint);INTEGRATION_FLASH.set(endpoint,{ok:true,text:'刚刚读取成功'});}
    catch(error){INTEGRATION_FLASH.set(endpoint,{ok:false,text:'读取失败：'+error.message});toast('读取失败：'+error.message,'bad');}
    await loadIntegrations();
    // 清单没能重画(读接入信息本身失败)时,旧按钮还在页面上,把它恢复过来。
    if(check.isConnected){setDisabled(check,'');check.classList.remove('spinning');}
    window.scrollTo?.(0,top);
    // 成功的那句过一会儿自己消失;失败的留着,直到下一次重读。
    const flash=INTEGRATION_FLASH.get(endpoint);
    if(flash && flash.ok) setTimeout(()=>{
      if(INTEGRATION_FLASH.get(endpoint)!==flash) return;
      INTEGRATION_FLASH.delete(endpoint);
      document.querySelector(`[data-integration-row="${CSS.escape(endpoint)}"] .integration-flash`)?.remove();
    },6000);
  });
}
