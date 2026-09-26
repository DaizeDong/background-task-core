// Presentation hints never replace backend authorization or confirmation.
const ConsoleActions={
  operations:[], timer:null, disabled:new WeakSet(),
  get readOnly(){return document.querySelector('meta[name="console-read-only"]')?.content==='true';},
  reason:'只读预览：操作请使用正式控制台',
  selector:'[data-work-action],[data-work-stop],[data-act],[data-retire],[data-mt],[data-delete],[data-rpact]:not([data-rpact="copy"]):not([data-rpact="web"]),[data-fix],[data-fixall],[data-bulk]:not([data-bulk="clear"]),[data-ctfork],[data-ctexport],#lcsave,#cxdel,#delete-confirm',
  allowWrite(){if(!this.readOnly) return true;toast(this.reason,'bad');return false;},
  begin(path,options){
    let body={};try{body=JSON.parse(options.body || '{}');}catch(error){}
    const operation={path,body:{verb:body.verb,action:body.action,action_id:body.action_id},label:operationLabel(path,body),started:Date.now(),pending:true};
    this.operations.unshift(operation);this.render();return operation;
  },
  finish(operation,reply,error){
    Object.assign(operation,operationOutcome(operation.path,operation.body,reply,error),{pending:false,finished:Date.now()});
    this.operations=this.operations.filter((row,index)=>row.pending || index<8);this.render();
  },
  render(){
    clearTimeout(this.timer);this.timer=null;
    const rows=this.operations, panel=$('operation-panel');
    if(!panel) return;
    panel.hidden=!rows.length;
    const line=row=>`${row.label}：${row.pending?'处理中，已等待 '+Math.floor((Date.now()-row.started)/1000)+' 秒':row.message}`;
    const active=rows.filter(row=>row.pending), current=active[0] || rows[0];
    $('operation-current').textContent=current?line(current):'';
    $('operation-current').className=current?.pending?'warn':current?.tone || '';
    $('operation-history').innerHTML=rows.map(row=>`<li><span class="${row.pending?'warn':row.tone}">${esc(line(row))}</span></li>`).join('');
    this.sync();if(active.length) this.timer=setTimeout(()=>this.render(),1000);
  },
  sync(){
    const pending=this.operations.some(row=>row.pending);
    document.querySelectorAll?.(this.selector).forEach(button=>{
      if(this.readOnly){
        if(!button.disabled) button.disabled=true;
        button.title=this.reason;button.setAttribute('aria-describedby','console-mode');
      }else if(pending){
        if(!button.disabled){this.disabled.add(button);button.disabled=true;}
      }else if(this.disabled.has(button)){
        this.disabled.delete(button);button.disabled=false;
      }
    });
  },
  start(){
    const mode=$('console-mode');mode.hidden=!this.readOnly;
    mode.textContent='只读预览 · 无法修改';mode.title=this.reason;
    this.sync();new MutationObserver(()=>this.sync()).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled']});
    if(this.readOnly){
      document.addEventListener('click',event=>{
        if(event.target.closest(this.selector)){event.preventDefault();event.stopImmediatePropagation();}
      },true);
    }
  }
};
function operationLabel(path,body){
  if(path.endsWith('/plan')) return '准备操作预览';
  if(path==='/api/work/action' && body.action_id==='complete') return '标记完成';
  const labels={run:'运行一次',stop:'停止本次',enable:'启用定时',disable:'停用定时',
    'skill.archive':'归档技能','skill.restore':'恢复技能','plugin.enable':'启用插件','plugin.disable':'禁用插件',
    'memory.archive':'归档记忆','memory.restore':'恢复记忆','clean.tempgit':'清理临时目录',
    'repo.fetch':'获取远程更新','repo.reveal':'打开目录','repo.status':'查看改动','repo.commitpush':'提交并推送','task.retire':'停用并移出清单'};
  const endpoints={'/api/codex/delete':'删除所选转录','/api/maintenance/delete':'删除或卸载','/api/llmcall/chain':'保存调用顺序','/api/work/action':'提交工作','/api/work/stop':'停止工作','/api/convo/fork':'分叉会话','/api/convo/export':'导出 Markdown'};
  return (labels[body.action || body.verb] || endpoints[path] || '执行操作')+(body.name?' · '+body.name:'');
}
function operationOutcome(path,body,reply,error){
  const result=reply || error?.payload;
  if(error && !result) return {tone:'warn',message:'结果未确认，请先刷新核对。'+error.message};
  if(error || result?.ok===false || result?.error) return {tone:'bad',message:(result?.partial?`${result.message || '可能已删除部分内容，请刷新核对'}；`:'')+(result?.deleted!=null?`已删除 ${result.deleted} 项；`:'')+apiError(result,error?.status || 500)};
  if(path==='/api/act') return {tone:['run_requested','cleanup_uncertain','scheduler_idle'].includes(result.status)?'warn':'ok',message:taskOutcome(result,body.verb)};
  if(path.startsWith('/api/work/')){
    if(result.status==='done' && result.action?.kind==='complete' && result.action?.state==='done')
      return {tone:'ok',message:'待办已标记完成'};
    const messages={queued:'已加入队列，等待执行',task_requested:'已提交任务，执行结果尚未确认',stopped:'已请求停止，请核对最新工作状态'};
    return {tone:'warn',message:result.message || messages[result.status] || '请求已受理，执行结果尚未确认'};
  }
  if(path.endsWith('/plan')) return {tone:'ok',message:'预览已就绪，等待确认'};
  if(path==='/api/convo/fork') return {tone:'ok',message:'已新建会话 '+String(result?.newId || '').slice(0,8)+'，原会话未改动'};
  if(path==='/api/convo/export') return {tone:'ok',message:'已生成 '+(result?.filename || 'Markdown 文件')};
  return {tone:'ok',message:result?.message || (result?.deleted!=null?`已删除 ${result.deleted} 项`:'操作已完成')};
}
const taskStateLabel=state=>({Ready:'已启用',Disabled:'已停用',Running:'正在运行',Queued:'已排队'}[state] || '状态未确认');
function taskOutcome(reply,verb){
  const messages={run_requested:'已提交运行请求，执行结果尚未确认',already_running:'任务已在运行，未重复启动',
    scheduler_idle:'计划程序当前未运行此任务；子进程退出情况及关联工作状态尚未确认',
    cleanup_uncertain:'操作结果无法确认，请刷新查看最新状态'};
  if(reply.status==='applied' && ['enable','disable'].includes(verb)) return verb==='enable'?'已启用':'已停用';
  return messages[reply.status] || reply.message || '未收到操作结果说明';
}
function taskActionButtons(row,advanced=false){
  const known=['Ready','Running','Disabled','Queued'].includes(row.state);
  const disabled=!known || busy || ConsoleActions.readOnly;
  const reason=ConsoleActions.readOnly?ConsoleActions.reason:busy?'正在执行操作':!known?'任务状态未确认':'';
  const hints={enable:'恢复按计划启动',disable:'不再按计划启动；正在运行的任务继续执行',run:'立即运行一次，保留原计划',stop:'请求停止当前运行，保留后续计划'};
  const symbols={enable:'i-on',disable:'i-pause',run:'i-play',stop:'i-stop'};
  const control=(verb,label,extraReason='')=>`<button class="mini task-control" data-act="${verb}" data-name="${esc(row.name)}" ${disabled || extraReason?'disabled':''} title="${esc(reason || extraReason || hints[verb])}"><svg class="ic" aria-hidden="true"><use href="#${symbols[verb]}"/></svg>${label}</button>`;
  return '<span class="task-controls">'+
    control(row.state==='Disabled'?'enable':'disable',row.state==='Disabled'?'启用':'停用')+
    (row.state==='Running'?control('stop','停止本次'):control('run','运行一次',row.state==='Disabled'?'请先启用':row.state==='Queued'?'已在队列中':''))+
    `<button class="mini task-control" data-launch="${esc(row.name)}" title="查看完整命令、身份和运行条件">启动方式</button>`+
    (advanced?`<button class="mini task-control retire-control" data-retire="${esc(row.name)}" ${disabled?'disabled':''} title="${esc(reason || '停用任务，并移出备份与健康检查清单；需要确认')}"><svg class="ic" aria-hidden="true"><use href="#i-retire"/></svg>停用并移出清单</button>`:'')+'</span>';
}

function taskStartCommand(row){
  const quote=value=>"'"+String(value).replace(/'/g,"''")+"'";
  return `Start-ScheduledTask -TaskPath ${quote(row.taskPath || '\\')} -TaskName ${quote(row.name)}`;
}
function taskLaunchHtml(row){
  const value=v=>v==null || v===''?'未设置':esc(v);
  const actions=row.actions?.length?row.actions:[{exec:row.exec,args:row.args,cwd:row.cwd}];
  const condition=(v,yes,no)=>v==null?'未检查':v?yes:no;
  return `<p>“运行一次”立即向 Windows 计划程序提交请求。“启用定时”恢复后续计划；不会立即补做所有历史任务。</p>
    <dl class="launch-facts"><dt>身份</dt><dd>${value(row.userId)} / ${value(row.runLevel)}</dd>
    <dt>计划</dt><dd>${esc(taskSchedule(row,true))}</dd>
    <dt>补跑</dt><dd>${condition(row.catchup,'允许错过计划后补跑','不补跑错过的计划')}</dd>
    <dt>电池</dt><dd>${condition(row.refuseOnBattery,'电池供电时拒绝启动','电池供电时可启动')}；${condition(row.stopOnBattery,'切换电池时停止','切换电池后继续')}</dd>
    <dt>执行限制</dt><dd>超时 ${value(row.timeout)}；重试 ${value(row.retries)}；并发策略 ${value(row.multi)}</dd></dl>
    ${actions.map((action,index)=>`<section class="launch-action"><h3>动作 ${index+1}</h3><dl class="launch-facts"><dt>程序</dt><dd><code>${value(action.exec)}</code></dd><dt>参数</dt><dd><code>${value(action.args)}</code></dd><dt>工作目录</dt><dd><code>${value(action.cwd)}</code></dd></dl></section>`).join('')}
    <p>终端启动仍交给已登记的计划任务，沿用它的身份、环境和运行条件。任务须处于启用状态。</p>
    <pre>${esc(taskStartCommand(row))}</pre>`;
}
function openTaskLaunch(name){
  const row=ROWS.find(task=>task.name===name);
  if(!row){toast('任务数据尚未读到，请刷新后重试','bad');return;}
  $('launch-title').textContent=name+' · 启动方式';$('launch-body').innerHTML=taskLaunchHtml(row);
  $('launch-copy').dataset.command=taskStartCommand(row);$('launch-dialog').showModal();
}

let DELETE_PLAN=null, DELETE_BUSY=false;
async function previewDeletion(data){
  if(!ConsoleActions.allowWrite() || DELETE_BUSY) return;
  DELETE_BUSY=true;DELETE_PLAN=null;
  $('delete-title').textContent=data.name+' · '+(data.delete==='plugin'?'卸载预览':'删除预览');
  $('delete-body').textContent='正在核对目标和删除范围…';$('delete-state').textContent='';
  $('delete-name').value='';$('delete-confirm').disabled=true;$('delete-dialog').showModal();
  try{
    const plan=await api('/api/maintenance/plan',{method:'POST',body:JSON.stringify({kind:data.delete,name:data.name,location:data.location})});
    if(!$('delete-dialog').open) return;
    DELETE_PLAN=plan;
    $('delete-body').innerHTML=`<p>${esc(plan.message)}</p><dl class="launch-facts"><dt>名称</dt><dd>${esc(plan.name)}</dd><dt>位置</dt><dd>${esc(plan.location)}</dd><dt>路径</dt><dd><code>${esc(plan.path || '由插件管理器管理')}</code></dd>${plan.files!=null?`<dt>范围</dt><dd>${plan.files} 个文件，${plan.links} 个联接，${kb(plan.bytes)}（不含联接目标）</dd>`:''}${plan.target?`<dt>保留的目标</dt><dd><code>${esc(plan.target)}</code></dd>`:''}</dl><p>预览 5 分钟内有效。内容发生变化后必须重新预览。</p>`;
    $('delete-confirm').textContent=plan.kind==='plugin'?'确认卸载':'确认删除';
  }catch(error){$('delete-state').textContent=error.message;}
  finally{DELETE_BUSY=false;syncDeleteConfirm();}
}
function syncDeleteConfirm(){
  $('delete-confirm').disabled=DELETE_BUSY || !DELETE_PLAN || $('delete-name').value!==DELETE_PLAN.name || ConsoleActions.readOnly;
}
function startDeletionControls(){
  $('delete-name').addEventListener('input',syncDeleteConfirm);
  $('delete-cancel').addEventListener('click',()=>{if(!DELETE_BUSY){DELETE_PLAN=null;$('delete-dialog').close();}});
  $('delete-dialog').addEventListener('cancel',event=>{if(DELETE_BUSY) event.preventDefault();else DELETE_PLAN=null;});
  $('delete-confirm').addEventListener('click',async()=>{
    if(DELETE_BUSY || !DELETE_PLAN || $('delete-name').value!==DELETE_PLAN.name || !ConsoleActions.allowWrite()) return;
    const plan=DELETE_PLAN;DELETE_BUSY=true;syncDeleteConfirm();$('delete-state').textContent='正在执行，请勿重复提交…';
    try{
      const result=await api('/api/maintenance/delete',{method:'POST',body:JSON.stringify({token:plan.token})});
      $('delete-state').textContent=result.message || result.error || '未收到结果说明';
      toast($('delete-state').textContent,result.ok?'ok':'bad');
      if(result.ok) $('delete-dialog').close();
    }catch(error){$('delete-state').textContent=error.message;}
    finally{DELETE_PLAN=null;DELETE_BUSY=false;syncDeleteConfirm();await loadMaint();}
  });
}
