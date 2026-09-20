// Presentation hints never replace backend authorization or confirmation.
const ConsoleActions={
  get readOnly(){return document.querySelector('meta[name="console-read-only"]')?.content==='true';},
  reason:'只读预览：操作请使用正式控制台',
  selector:'[data-work-action],[data-work-stop],[data-act],[data-retire],[data-mt],[data-rpact]:not([data-rpact="copy"]):not([data-rpact="web"]),[data-fix],[data-fixall],[data-bulk]:not([data-bulk="clear"]),#lcsave,#cxdel',
  allowWrite(){if(!this.readOnly) return true;toast(this.reason,'bad');return false;},
  sync(){
    if(!this.readOnly) return;
    document.querySelectorAll(this.selector).forEach(button=>{
      if(!button.disabled) button.disabled=true;
      button.title=this.reason;button.setAttribute('aria-describedby','console-mode');
    });
  },
  start(){
    const mode=$('console-mode');mode.hidden=!this.readOnly;
    mode.textContent='只读预览 · 无法修改';mode.title=this.reason;
    if(this.readOnly){
      this.sync();new MutationObserver(()=>this.sync()).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled']});
      document.addEventListener('click',event=>{
        if(event.target.closest(this.selector)){event.preventDefault();event.stopImmediatePropagation();}
      },true);
    }
  }
};
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
    (advanced?`<button class="mini task-control retire-control" data-retire="${esc(row.name)}" ${disabled?'disabled':''} title="${esc(reason || '停用任务，并移出备份与健康检查清单；需要确认')}"><svg class="ic" aria-hidden="true"><use href="#i-retire"/></svg>停用并移出清单</button>`:'')+'</span>';
}
