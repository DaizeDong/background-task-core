// Presentation hints never replace backend authorization or confirmation.
const ConsoleActions={
  operations:[], timer:null, disabled:new WeakSet(),
  // 操作面板的两种「让开」:collapsed 是全部结束 5 秒后自动收成一行,dismissed 是人点了 ✕。
  // 两者都在下一次操作开始时作废。
  collapsed:false, dismissed:false, collapseTimer:null, collapseDelay:5000, shownCollapsed:false,
  get readOnly(){return document.querySelector('meta[name="console-read-only"]')?.content==='true';},
  reason:'只读预览，请用正式控制台',
  // 只在本页改草稿的几样也在这里:调用顺序的上下移、放弃修改,清理列表的全选和清空。
  // 它们自己不发请求,可在只读预览里点了只会攒出一份永远存不下来的草稿。
  selector:'[data-work-action],[data-work-stop],[data-act],[data-task-repair],[data-task-delete],[data-mt],[data-delete],[data-rpact]:not([data-rpact="copy"]):not([data-rpact="web"]),[data-fix],[data-fixall],[data-bulk]:not([data-bulk="clear"]),[data-ctfork],[data-cvdelete],[data-cvrename],[data-cvmove],[data-cvdrag],[data-mv],#cv-submit,#lcsave,#lcreset,#cxall,#cxnone,#cxdel,#delete-confirm,#task-delete-preview,#task-delete-confirm,#repair-submit',
  // 页面自己的禁用原因走这里:只读预览的原因压过它,因为那时按钮无论如何都点不了。
  gate(button,reason){setDisabled(button,this.readOnly?this.reason:reason,this.readOnly?'console-mode':undefined);},
  allowWrite(){if(!this.readOnly) return true;toast(this.reason,'bad');return false;},
  begin(path,options){
    let body={};try{body=JSON.parse(options.body || '{}');}catch(error){}
    const operation={path,body:{verb:body.verb,action:body.action,action_id:body.action_id},label:operationLabel(path,body),started:Date.now(),pending:true};
    clearTimeout(this.collapseTimer);this.collapseTimer=null;this.collapsed=false;this.dismissed=false;
    this.operations.unshift(operation);this.render();return operation;
  },
  finish(operation,reply,error){
    Object.assign(operation,operationOutcome(operation.path,operation.body,reply,error),{pending:false,finished:Date.now()});
    this.operations=this.operations.filter((row,index)=>row.pending || index<8);this.render();
    // 全部结束 5 秒后收成一行「最近操作 N · 最后结果」,点一下再展开。还有操作在跑、
    // 或者最后一个结果是失败时不收:失败要一直摆在眼前,等人读完。
    clearTimeout(this.collapseTimer);this.collapseTimer=null;
    if(!this.operations.some(row=>row.pending) && this.lastFinished()?.tone!=='bad')
      this.collapseTimer=setTimeout(()=>{this.collapseTimer=null;this.collapsed=true;this.render();},this.collapseDelay);
  },
  lastFinished(){
    return this.operations.filter(row=>!row.pending).sort((a,b)=>(b.finished || 0)-(a.finished || 0))[0];
  },
  // ✕:藏到下一次操作开始。还有操作在跑时不能关,那一行正是灰着的按钮在等的东西。
  dismiss(){
    if(this.operations.some(row=>row.pending)) return;
    clearTimeout(this.collapseTimer);this.collapseTimer=null;this.dismissed=true;this.render();
  },
  expand(){
    if(!this.collapsed) return;
    this.collapsed=false;this.render();
  },
  render(){
    clearTimeout(this.timer);this.timer=null;
    const rows=this.operations, panel=$('operation-panel');
    if(!panel) return;
    panel.hidden=!rows.length || this.dismissed;
    const line=row=>`${row.label}：${row.pending?'处理中，已等待 '+Math.floor((Date.now()-row.started)/1000)+' 秒':row.message}`;
    const active=rows.filter(row=>row.pending), current=active[0] || rows[0];
    // Sticky only while something is still running. A finished record stays in the page flow at
    // the top; pinned, it covered whatever the owner was looking at (the conversation chain card
    // right after a fork, by about 140px at phone width) for the rest of the page session.
    panel.classList?.toggle('settled',!active.length);
    const collapsed=this.collapsed && !active.length, last=this.lastFinished();
    panel.classList?.toggle('collapsed',collapsed);
    $('operation-current').textContent=current?line(current):'';
    $('operation-current').className=current?.pending?'warn':current?.tone || '';
    $('operation-history').innerHTML=rows.map(row=>`<li><span class="${row.pending?'warn':row.tone}">${esc(line(row))}</span></li>`).join('');
    // 收起时那一行就是 <details> 的 summary:数量加最后一个结果,点它就展开。
    const summary=$('operation-summary'), details=$('operation-details');
    if(summary){
      summary.textContent=collapsed && last?`最近操作 ${rows.length} · ${last.message}`:'最近操作';
      summary.className=collapsed && last?last.tone || '':'';
    }
    // 只在收起状态变了的那一下改 open:人自己合上历史列表时,每秒一次的重绘不能再把它打开。
    if(details && this.shownCollapsed!==collapsed) details.open=!collapsed;
    this.shownCollapsed=collapsed;
    const close=$('operation-close');
    if(close) setDisabled(close,active.length?'操作进行中，完成后才能关闭':'');
    this.sync();syncStickyOffsets();if(active.length) this.timer=setTimeout(()=>this.render(),1000);
  },
  sync(){
    const active=this.operations.find(row=>row.pending);
    document.querySelectorAll?.(this.selector).forEach(button=>{
      // 原来这里把 title 整句换成只读原因,一排图标悬停上去句句相同,认不出哪个是哪个。
      // 现在一律是「动作名（不可用：原因）」,动作名只取一次,重复同步不会叠后缀。
      if(this.readOnly){
        setDisabled(button,this.reason,'console-mode');
      }else if(active){
        // 全页写锁本身不变(写操作有意串行),只是把「在等谁」说出来;原本就灰着的按钮保留自己的原因。
        if(!button.disabled || this.disabled.has(button)){
          this.disabled.add(button);setDisabled(button,`等待「${active.label}」完成`,'operation-current');
        }
      }else if(this.disabled.has(button)){
        this.disabled.delete(button);setDisabled(button,'','operation-current');
      }
    });
    // 拖拽排序是同一份草稿的另一条路,只读时也要一起关掉。
    if(this.readOnly) document.querySelectorAll?.('#lclist li[draggable="true"]').forEach(item=>item.setAttribute('draggable','false'));
  },
  start(){
    syncStickyOffsets();startDialogs();
    $('operation-close')?.addEventListener('click',()=>this.dismiss());
    // 收起后人点开 summary:浏览器先把 details 打开,这里把面板退回展开态。
    // render() 自己改 open 时也会触发 toggle,那时状态已经一致,expand() 什么都不做。
    $('operation-details')?.addEventListener('toggle',()=>{if($('operation-details').open) this.expand();});
    if(typeof ResizeObserver==='function'){
      const observer=new ResizeObserver(()=>syncStickyOffsets());
      ['bar','operation-panel'].forEach(id=>{if($(id)) observer.observe($(id));});
    }
    if(typeof window!=='undefined') window.addEventListener?.('resize',syncStickyOffsets);
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
// The page's sticky stack, measured rather than assumed: #bar wraps to two rows on mid widths and
// is not sticky at all at phone width, where a hard-coded 54px left a gap above the operation
// panel with page content showing through it. --bar-stick places the panel directly under the
// bar; --sticky-stack feeds html scroll-padding-top so scrollIntoView and anchor jumps (the chain
// card opening, a pipeline run) land below whatever is actually pinned.
function syncStickyOffsets(){
  const root=document.documentElement;
  if(!root?.style?.setProperty || typeof getComputedStyle!=='function') return;
  const pinned=el=>el && !el.hidden && getComputedStyle(el).position==='sticky' ? Math.ceil(el.getBoundingClientRect().height) : 0;
  const bar=pinned($('bar'));
  root.style.setProperty('--bar-stick',bar+'px');
  root.style.setProperty('--sticky-stack',(bar+pinned($('operation-panel')))+'px');
}
function operationLabel(path,body){
  // 删除任务的预览排在通用的 /plan 之前:操作记录里要看得出这是一次删除,而不只是「某个预览」。
  if(path==='/api/task/delete/plan') return '准备删除预览'+(body.name?' · '+body.name:'');
  if(path.endsWith('/plan') || path==='/api/convo/delete-plan') return '准备操作预览';
  if(path==='/api/work/action' && body.action_id==='complete') return '标记完成';
  const labels={run:'运行一次',stop:'停止本次',enable:'启用定时',disable:'停用定时',
    'skill.archive':'归档技能','skill.restore':'恢复技能','plugin.enable':'启用插件','plugin.disable':'禁用插件',
    'memory.archive':'归档记忆','memory.restore':'恢复记忆','clean.tempgit':'清理临时目录',
    'repo.fetch':'获取远程更新','repo.reveal':'打开目录','repo.status':'查看改动','repo.commitpush':'提交并推送'};
  const endpoints={'/api/codex/delete':'删除所选转录','/api/maintenance/delete':'删除或卸载','/api/llmcall/chain':'保存调用顺序','/api/work/action':'提交工作','/api/work/stop':'停止工作','/api/convo/fork':'分叉会话','/api/convo/rename':'重命名会话','/api/convo/move':'迁移会话文件','/api/convo/delete':'永久删除会话',
    '/api/task/delete/apply':'删除任务','/api/task/repair/preview':'读取任务事实','/api/task/repair':'提交修复工单'};
  return (labels[body.action || body.verb] || endpoints[path] || '执行操作')+(body.name?' · '+body.name:'');
}
function operationOutcome(path,body,reply,error){
  const result=reply || error?.payload;
  if(error && !result) return {tone:'warn',message:'结果未确认，请先刷新核对。'+error.message};
  // 删除的结果有三种,而「部分完成」的回复 ok 也是 false:放进下面的通用失败分支会被说成失败,
  // 放进成功分支又会被说成删完了。两种都不对,所以按 status 单独说。计划程序那步失败是 HTTP 500,正文照样是结果。
  if(path==='/api/task/delete/apply' && ['ok','partial','failed','unknown'].includes(result?.status)){
    const tone={ok:'ok',partial:'warn',failed:'bad',unknown:'bad'}[result.status];
    const fallback={ok:'任务已删除，各项清理都已完成',partial:'任务已从计划程序删除，但还有没完成的清理',failed:'没有删除',
      unknown:'无法确认是否已删除，请刷新核对'}[result.status];
    return {tone,message:(result.status==='partial'?'部分完成：':'')+(result.message || fallback)};
  }
  // 修复工单的回复永远只是「受理」:Agent 还没开始诊断,任务也没有被改动,所以成功也只给警示色。
  if(path==='/api/task/repair' && result && !error){
    if(result.ok) return {tone:'warn',message:result.existing && !result.receipt?'这个任务已有修复工单，没有重复建立':'已受理，修复结果会出现在工作记录和 Discord；任务尚未修复'};
    if(result.uncertain) return {tone:'warn',message:(result.message || '未收到确认')+'；再次点击会核对原请求'};
  }
  if(error || result?.ok===false || result?.error) return {tone:'bad',message:(result?.partial?`${result.message || '可能已删除部分内容，请刷新核对'}；`:'')+(result?.deleted!=null?`已删除 ${result.deleted} 项；`:'')+apiError(result,error?.status || 500)};
  if(path==='/api/act') return {tone:['run_requested','cleanup_uncertain','scheduler_idle'].includes(result.status)?'warn':'ok',message:taskOutcome(result,body.verb)};
  if(path.startsWith('/api/work/')){
    if(result.status==='done' && result.action?.kind==='complete' && result.action?.state==='done')
      return {tone:'ok',message:'待办已标记完成'};
    const messages={queued:'已加入队列，等待执行',task_requested:'已提交任务，执行结果尚未确认',stopped:'已请求停止，请核对最新工作状态'};
    return {tone:'warn',message:result.message || messages[result.status] || '请求已受理，执行结果尚未确认'};
  }
  if(path==='/api/task/delete/plan' && result?.applicable===false) return {tone:'warn',message:'预览已就绪，但有不能删除的原因'};
  if(path.endsWith('/plan') || path==='/api/convo/delete-plan') return {tone:'ok',message:'预览已就绪，等待确认'};
  if(path==='/api/task/repair/preview') return {tone:'ok',message:result?.existing?'已读取任务事实；这个任务已有修复工单':'已读取任务事实，尚未提交'};
  if(path==='/api/convo/fork') return {tone:'ok',message:'已新建会话 '+String(result?.newId || '').slice(0,8)+'，原会话未改动'};
  if(path==='/api/convo/delete') return {tone:result.deleted===true?'ok':'warn',message:result.deleted===true?'会话及关联文件已永久删除':'删除结果尚未确认'};
  if(path==='/api/convo/rename') return {tone:'ok',message:'已保存会话名称'};
  if(path==='/api/convo/move') return {tone:'ok',message:result.unchanged?'会话已在目标目录':'已迁移会话文件和关联记录'};
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
// 「同步与备份」流水线上的任务是备份本身的骨架:删掉它们,删除这件事的后续清理(包括把被删任务移出备份)
// 也就跟着没了着落。所以它们在任何一个标签页都没有删除按钮,而不只是在流水线卡片上;后端同样拒绝
// (task_delete.PIPELINE_BLOCK)。修复只开工单不改任务,哪里都可以给。
const taskIsPipeline=name=>typeof PIPELINE_DEFS!=='undefined' &&
  Object.values(PIPELINE_DEFS).some(def=>String(def.name).toLowerCase()===String(name).toLowerCase());
function taskActionButtons(row,{deletable=!taskIsPipeline(row.name)}={}){
  const known=['Ready','Running','Disabled','Queued'].includes(row.state);
  const reason=ConsoleActions.readOnly?ConsoleActions.reason:busy?'正在执行操作':!known?'任务状态未确认':'';
  const hints={enable:'恢复按计划启动',disable:'不再按计划启动；正在运行的任务继续执行',run:'立即运行一次，保留原计划',stop:'请求停止当前运行，保留后续计划'};
  // 停用是把定时关掉,不是暂停这一次运行:用开关的「关」,不用暂停键。
  const symbols={enable:'i-on',disable:'i-toggle-off',run:'i-play',stop:'i-stop'};
  // 灰着的按钮也先说自己是哪个动作:一行里四个图标,只写原因的话悬停上去分不出是谁。
  const title=(label,why,hint)=>esc(why?disabledTitle(label,why):hint);
  const control=(verb,label,extraReason='')=>`<button class="mini task-control icon-only" data-act="${verb}" data-name="${esc(row.name)}" ${reason || extraReason?'disabled':''} title="${title(label,reason || extraReason,hints[verb])}"><svg class="ic" aria-hidden="true"><use href="#${symbols[verb]}"/></svg><span class="control-label">${label}</span></button>`;
  // 修复不看任务状态:状态读不出来、任务在跑,恰恰是最需要诊断的时候。
  const repairReason=ConsoleActions.readOnly?ConsoleActions.reason:'';
  const removeReason=ConsoleActions.readOnly?ConsoleActions.reason:busy?'正在执行操作':row.state==='Running'?'任务正在运行，请先停止本次运行再删除':'';
  return '<span class="task-controls">'+
    control(row.state==='Disabled'?'enable':'disable',row.state==='Disabled'?'启用':'停用')+
    (row.state==='Running'?control('stop','停止本次'):control('run','运行一次',row.state==='Disabled'?'请先启用':row.state==='Queued'?'已在队列中':''))+
    `<button class="icon-only mini task-control" data-launch="${esc(row.name)}" title="查看完整命令、身份和运行条件"><svg class="ic" aria-hidden="true"><use href="#i-terminal"/></svg><span class="control-label">启动方式</span></button>`+
    `<button class="mini task-control icon-only" data-task-repair="${esc(row.name)}" ${repairReason?'disabled':''} title="${title('修复',repairReason,'修复：查看任务事实，开一张工单交给 Agent 诊断；不会改动任务本身')}"><svg class="ic" aria-hidden="true"><use href="#i-repair"/></svg><span class="control-label">修复</span></button>`+
    (deletable?`<button class="mini task-control icon-only" data-task-delete="${esc(row.name)}" ${removeReason?'disabled':''} title="${title('删除',removeReason,'删除：先预览每一步要改哪里，输入完整任务名后才执行')}"><svg class="ic" aria-hidden="true"><use href="#i-trash"/></svg><span class="control-label">删除</span></button>`:'')+'</span>';
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


// ================= 对话框与菜单的通用约定 ===========================================
// 点遮罩和 Esc 交给浏览器原生的 closedby,不自己判断「点在了对话框外面」:自己判断的写法,
// 在输入框里拖选文字、松手落在遮罩上时也会把对话框关掉,点到对话框自己的内边距也会。
// 三档:请求在路上时谁都关不掉(none,原有的 cancel 守卫照样在);框里有人打的字时只认 Esc 和取消
// (closerequest),误点一下遮罩不会把字丢掉;其余时候点哪儿都能关(any)。
function dialogDismissMode(busy,dirty){return busy?'none':dirty?'closerequest':'any';}
function syncDialogDismiss(dialog,busy,dirty){
  if(!dialog) return '';
  const mode=dialogDismissMode(!!busy,!!dirty);
  if(typeof dialog.setAttribute==='function') dialog.setAttribute('closedby',mode);
  else dialog.closedBy=mode;
  // ✕ 和取消是同一个动作:忙着的时候取消按不动,✕ 也一起灰着,而不是点了没反应。
  const close=dialog.querySelector?.('.dialog-x[data-dismiss]');
  if(close) setDisabled(close,busy?'正在执行，完成后才能关闭':'');
  return mode;
}
// 手打的名称和要删的名称差在哪。还没打完就说还差几个字,打错了就说不一致;
// 只写「名称不对」的话,人分不清是没打完还是打错了字。
function nameMismatchReason(typed,expected){
  typed=String(typed ?? '');expected=String(expected ?? '');
  if(typed===expected) return '';
  if(!typed) return '先输入完整名称';
  if(expected.startsWith(typed)) return `名称还差 ${[...expected].length-[...typed].length} 个字符`;
  return '名称不一致';
}
// 要照着打的名称画成等宽的一块,可以整块选中,旁边一个复制按钮。
function confirmNameHtml(name){
  return `<code class="confirm-name">${esc(name)}</code><button type="button" class="icon-only mini" data-copy-text="${esc(name)}" title="复制名称"><svg class="ic" aria-hidden="true"><use href="#i-copy"/></svg><span class="control-label">复制名称</span></button>`;
}
// 菜单里的一项被选中后先收起菜单、把焦点交回打开它的按钮。接着弹出的对话框关掉时,
// 焦点回到它打开前的位置;那时菜单项已经藏起来了,焦点会掉回页面开头。
function closeMenuFor(el){
  const menu=el?.closest?.('[popover]');
  if(!menu) return;
  try{ if(menu.matches(':popover-open')) menu.hidePopover(); }catch(error){}
  const opener=menu.id && typeof CSS!=='undefined'?document.querySelector?.(`[popovertarget="${CSS.escape(menu.id)}"]`):null;
  opener?.focus?.({preventScroll:true});
}
function startDialogs(){
  document.addEventListener('click',event=>{
    // ✕ 就是那个对话框自己的取消按钮,走同一段处理(忙着时拦下、清掉预览),不另写一份。
    const dismiss=event.target.closest?.('[data-dismiss]');
    if(dismiss){const target=$(dismiss.dataset.dismiss);if(target && !target.disabled) target.click();return;}
    const copy=event.target.closest?.('[data-copy-text]');
    if(copy){
      navigator.clipboard.writeText(copy.dataset.copyText).then(()=>toast('已复制','ok'),()=>toast('无法访问剪贴板，请手动选中复制','bad'));
    }
  });
}

// ================= 页面内的确认框 ===================================================
// 替代浏览器自带的 confirm():那种框样式和别处不一样、点外面关不掉、长名单挤成一段纯文字、
// 还会把整页卡住。这里返回 Promise<boolean>:确认是 true,取消、Esc、点遮罩都是 false。
let CONFIRM_SETTLE=null;
function confirmSettle(value){
  const settle=CONFIRM_SETTLE;CONFIRM_SETTLE=null;
  const dialog=$('confirm-dialog');
  if(dialog?.open) dialog.close();
  if(settle) settle(!!value);
}
function wireConfirmDialog(){
  const dialog=$('confirm-dialog');
  if(!dialog || dialog.dataset.wired) return;
  dialog.dataset.wired='1';
  $('confirm-form').addEventListener('submit',event=>{event.preventDefault();confirmSettle(true);});
  $('confirm-cancel').addEventListener('click',()=>confirmSettle(false));
  // Esc 和点遮罩由浏览器直接关掉对话框,关掉就是「不」。close 事件是异步来的:
  // 那时对话框若已经为下一个问题重新打开,这一下属于上一个问题,不能拿来回答新的。
  dialog.addEventListener('close',()=>{if(!dialog.open) confirmSettle(false);});
}
function askConfirm({title,body='',items=[],more='',confirmLabel='确认',danger=false}={}){
  wireConfirmDialog();
  // 同一时间只问一件事:上一个还没答的问题算「不」。
  confirmSettle(false);
  $('confirm-title').textContent=title || '确认操作';
  $('confirm-body').textContent=body;
  const list=$('confirm-items');
  list.innerHTML=items.map(item=>item && typeof item==='object'
    ?`<li>${esc(item.text)}${item.note?` <small class="confirm-note">${esc(item.note)}</small>`:''}</li>`:`<li>${esc(item)}</li>`).join('');
  list.hidden=!items.length;
  $('confirm-more').textContent=more;$('confirm-more').hidden=!more;
  const ok=$('confirm-ok');
  ok.textContent=confirmLabel;ok.className=danger?'danger':'primary';
  return new Promise(resolve=>{
    CONFIRM_SETTLE=resolve;
    $('confirm-dialog').showModal();
    // 危险的确认停在「取消」上:手滑多按一下 Enter 不能把东西删掉。
    (danger?$('confirm-cancel'):ok).focus?.();
  });
}

// ================= 技能与插件的删除 =================================================
let DELETE_PLAN=null, DELETE_BUSY=false;
async function previewDeletion(data){
  if(!ConsoleActions.allowWrite() || DELETE_BUSY) return;
  DELETE_BUSY=true;DELETE_PLAN=null;
  $('delete-title').textContent=data.name+' · '+(data.delete==='plugin'?'卸载预览':'删除预览');
  $('delete-body').textContent='正在核对目标和删除范围…';$('delete-state').textContent='';
  $('delete-name').value='';$('delete-expected').hidden=true;$('delete-expected').innerHTML='';
  syncDeleteConfirm();$('delete-dialog').showModal();
  try{
    const plan=await api('/api/maintenance/plan',{method:'POST',body:JSON.stringify({kind:data.delete,name:data.name,location:data.location})});
    if(!$('delete-dialog').open) return;
    DELETE_PLAN=plan;
    $('delete-body').innerHTML=`<p>${esc(plan.message)}</p><dl class="launch-facts"><dt>名称</dt><dd>${esc(plan.name)}</dd><dt>位置</dt><dd>${esc(plan.location)}</dd><dt>路径</dt><dd><code>${esc(plan.path || '由插件管理器管理')}</code></dd>${plan.files!=null?`<dt>范围</dt><dd>${plan.files} 个文件，${plan.links} 个联接，${kb(plan.bytes)}（不含联接目标）</dd>`:''}${plan.target?`<dt>保留的目标</dt><dd><code>${esc(plan.target)}</code></dd>`:''}</dl><p>预览 5 分钟内有效。内容发生变化后必须重新预览。</p>`;
    $('delete-confirm').textContent=plan.kind==='plugin'?'确认卸载':'确认删除';
    $('delete-expected').innerHTML=confirmNameHtml(plan.name);$('delete-expected').hidden=false;
  }catch(error){$('delete-state').textContent=error.message;}
  finally{
    DELETE_BUSY=false;syncDeleteConfirm();
    // 预览到了就把光标放进名称框,省得再去点一下;人已经点到别处(比如复制按钮)就不抢。
    const active=document.activeElement;
    if(DELETE_PLAN && $('delete-dialog').open && (!active || active===$('delete-cancel') || active===$('delete-dialog'))) $('delete-name').focus?.();
  }
}
function deleteConfirmReason(){
  if(DELETE_BUSY) return DELETE_PLAN?'正在执行':'正在读取删除范围';
  if(!DELETE_PLAN) return '没有可用的删除预览，请关闭后重新打开';
  return nameMismatchReason($('delete-name').value,DELETE_PLAN.name);
}
function syncDeleteConfirm(){
  const reason=deleteConfirmReason();
  ConsoleActions.gate($('delete-confirm'),reason);
  $('delete-hint').textContent=DELETE_PLAN && !reason?'名称一致，可以确认':reason;
  syncDialogDismiss($('delete-dialog'),DELETE_BUSY,$('delete-name').value!=='');
}
// 确认按钮是表单的提交按钮:点它和在名称框里按 Enter 都走这里。按钮灰着时浏览器不会隐式提交,
// 这里再按同样的条件核一遍,因为提交也可能从别的路径来。
async function confirmDeletion(){
  if(DELETE_BUSY || !DELETE_PLAN || $('delete-name').value!==DELETE_PLAN.name || !ConsoleActions.allowWrite()) return;
  const plan=DELETE_PLAN;DELETE_BUSY=true;syncDeleteConfirm();$('delete-state').textContent='正在执行，请勿重复提交…';
  try{
    const result=await api('/api/maintenance/delete',{method:'POST',body:JSON.stringify({token:plan.token})});
    $('delete-state').textContent=result.message || result.error || '未收到结果说明';
    toast($('delete-state').textContent,result.ok?'ok':'bad');
    if(result.ok) $('delete-dialog').close();
  }catch(error){$('delete-state').textContent=error.message;}
  finally{DELETE_PLAN=null;DELETE_BUSY=false;syncDeleteConfirm();await loadMaint();}
}
function startDeletionControls(){
  $('delete-name').addEventListener('input',syncDeleteConfirm);
  $('delete-cancel').addEventListener('click',()=>{if(!DELETE_BUSY){DELETE_PLAN=null;$('delete-dialog').close();}});
  $('delete-dialog').addEventListener('cancel',event=>{if(DELETE_BUSY) event.preventDefault();else DELETE_PLAN=null;});
  $('delete-form').addEventListener('submit',event=>{event.preventDefault();confirmDeletion();});
}
