// Presentation hints never replace backend authorization or confirmation.
const ConsoleActions={
  get readOnly(){return document.querySelector('meta[name="console-read-only"]')?.content==='true';},
  reason:'只读预览：操作请使用正式控制台',
  selector:'[data-act],[data-retire],[data-mt],[data-rpact],[data-fix],[data-fixall],[data-bulk]:not([data-bulk="clear"]),#lcsave,#cxdel',
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
    mode.textContent='只读预览';mode.title=this.reason;
    if(this.readOnly){
      this.sync();new MutationObserver(()=>this.sync()).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled']});
      document.addEventListener('click',event=>{
        if(event.target.closest(this.selector)){event.preventDefault();event.stopImmediatePropagation();}
      },true);
    }
  }
};
function taskActionButtons(row,advanced=false){
  const known=['Ready','Running','Disabled','Queued'].includes(row.state);
  const disabled=!known || busy || ConsoleActions.readOnly;
  const reason=ConsoleActions.readOnly?ConsoleActions.reason:busy?'正在执行操作':!known?'任务状态未确认':'';
  const control=(verb,label,extraReason='')=>`<button class="mini task-control" data-act="${verb}" data-name="${esc(row.name)}" ${disabled || extraReason?'disabled':''} title="${esc(reason || extraReason || label)}">${label}</button>`;
  return '<span class="task-controls">'+
    control(row.state==='Disabled'?'enable':'disable',row.state==='Disabled'?'启用':'停用')+
    (row.state==='Running'?control('stop','停止本次'):control('run','运行一次',row.state==='Disabled'?'请先启用':row.state==='Queued'?'已在队列中':''))+
    (advanced?`<button class="mini task-control" data-retire="${esc(row.name)}" ${disabled?'disabled':''} title="${esc(reason || '停用并移出备份与健康清单')}">退役</button>`:'')+'</span>';
}
