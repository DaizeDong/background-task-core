// File operations use the console's authenticated action API.
let CV_EDIT=null, CV_EDIT_BUSY=false, CV_DRAG=null;
function cvSession(id){
  for(const group of CONVOS?.groups || []){
    const row=group.shown.find(r=>r.id===id);
    if(row) return {...row,projectDir:cvKey(group)};
  }
  if(typeof CH_FRES!=='undefined' && CH_FRES?.newId===id) return {...CH_FRES,id,title:CH_FRES.title || id};
  if(typeof CH!=='undefined' && CH?.id===id) return CH;
  return null;
}
function cvOpenManager(kind,id){
  if(!ConsoleActions.allowWrite() || CV_EDIT_BUSY) return;
  const row=typeof id==='object'?id:cvSession(id);
  if(!row){toast('这场会话的位置已改变，请刷新列表后重试','bad');return;}
  CV_EDIT={kind,row};
  const rename=kind==='rename', locations=CONVOS?.locations || CONVOS?.groups || [];
  $('cv-dialog-title').textContent=rename?'重命名会话':'移动会话文件';
  $('cv-current').textContent=row.file || row.id;
  $('cv-title-field').hidden=!rename;$('cv-target-field').hidden=rename;
  $('cv-new-title').value=row.title || '';$('cv-new-title').required=rename;
  const options=locations.filter(g=>cvKey(g)!==row.projectDir);
  $('cv-target').innerHTML=options.map(g=>`<option value="${esc(cvKey(g))}">${esc(g.cwd || g.id)}</option>`).join('');
  $('cv-target').required=!rename;
  $('cv-submit').textContent=rename?'保存名称':'移动到此项目';
  $('cv-submit').disabled=!rename && !options.length;
  $('cv-edit-note').textContent=!rename && !options.length?'还没有其他项目目录可供迁移':'';
  $('cv-edit-note').className='cv-notice';
  cvTargetLocation();$('cv-dialog').showModal();
  const input=rename?$('cv-new-title'):$('cv-target');input.focus();if(rename) input.select();
}
function cvTargetLocation(){
  const target=(CONVOS?.locations || CONVOS?.groups || []).find(g=>cvKey(g)===$('cv-target').value);
  $('cv-target-path').textContent=target?.storagePath || '';
}
function cvUpdateSession(result,title){
  if(typeof CH!=='undefined' && CH?.id===result.id){
    Object.assign(CH,{file:result.file,projectDir:result.projectDir,cwd:result.cwd,
      storageCwd:result.cwd,storagePath:result.storagePath});
    if(title) CH.title=title;chRenderHead();
  }
  if(typeof CH_FRES!=='undefined' && CH_FRES?.newId===result.id){
    Object.assign(CH_FRES,result);if(title) CH_FRES.title=title;chRenderAct();
  }
}
async function cvMutate(kind,row,value){
  if(CV_EDIT_BUSY || !ConsoleActions.allowWrite()) return;
  CV_EDIT_BUSY=true;
  const dialog=$('cv-dialog'), visible=dialog.open;
  const body={id:row.id,expectedProject:row.projectDir};
  if(kind==='rename') body.title=value;else body.targetProject=value;
  $('cv-submit').disabled=true;$('cv-cancel').disabled=true;
  if(visible){$('cv-edit-note').className='cv-notice';$('cv-edit-note').textContent=kind==='rename'?'正在保存名称…':'正在迁移文件和关联记录…';}
  else $('cvnote').textContent='正在迁移…';
  try{
    const result=await api('/api/convo/'+kind,{method:'POST',body:JSON.stringify(body)});
    cvUpdateSession(result,result.title);
    if(kind==='move') CV_OPEN[result.projectDir]=true;
    if(visible) dialog.close();
    toast(kind==='rename'?'会话名称已保存':result.unchanged?'会话已在该目录':'会话文件和关联记录已迁移','ok');
    for(const warning of result.warnings || []) toast(warning);
    await loadConvos();
  }catch(error){
    const message=error.status?error.message:'结果尚未确认。重试会核对同一会话，不会创建副本。'+error.message;
    if(visible){$('cv-edit-note').className='cv-notice error';$('cv-edit-note').textContent=message;}
    else{$('cvnote').textContent='迁移未完成';toast(message,'bad');await loadConvos();}
  }finally{
    CV_EDIT_BUSY=false;$('cv-submit').disabled=false;$('cv-cancel').disabled=false;
  }
}
function cvClearDrag(){
  CV_DRAG=null;document.querySelectorAll('.cv-dropover').forEach(el=>el.classList.remove('cv-dropover'));
  $('cv-drop-targets').hidden=true;$('cv-drop-targets').innerHTML='';
}
function startConversationActions(){
  document.addEventListener('click',event=>{
    const rename=event.target.closest('[data-cvrename]'), move=event.target.closest('[data-cvmove],[data-cvdrag]');
    if(rename && !rename.disabled) cvOpenManager('rename',rename.dataset.cvrename);
    else if(move && !move.disabled) cvOpenManager('move',move.dataset.cvmove || move.dataset.cvdrag);
    const open=event.target.closest('[data-cvopen-new]');
    if(open && typeof openConvoChain==='function') openConvoChain(open.dataset.cvopenNew);
  });
  $('cv-form').addEventListener('submit',event=>{
    event.preventDefault();if(!CV_EDIT) return;
    cvMutate(CV_EDIT.kind,CV_EDIT.row,CV_EDIT.kind==='rename'?$('cv-new-title').value:$('cv-target').value);
  });
  $('cv-cancel').addEventListener('click',()=>{if(!CV_EDIT_BUSY) $('cv-dialog').close();});
  $('cv-dialog').addEventListener('cancel',event=>{if(CV_EDIT_BUSY) event.preventDefault();});
  $('cv-target').addEventListener('change',cvTargetLocation);
  const groups=$('cvbox');
  groups.addEventListener('dragstart',event=>{
    const handle=event.target.closest('[data-cvdrag]');
    if(!handle || handle.disabled || CV_EDIT_BUSY || !ConsoleActions.allowWrite()){event.preventDefault();return;}
    CV_DRAG=cvSession(handle.dataset.cvdrag);
    if(!CV_DRAG){event.preventDefault();return;}
    const locations=CONVOS?.locations || CONVOS?.groups || [];
    $('cv-drop-targets').innerHTML='<strong>松开以迁移到此目录</strong>'+locations.filter(g=>cvKey(g)!==CV_DRAG.projectDir)
      .map(g=>`<div class="cv-drop-destination" data-cvproject="${esc(cvKey(g))}" title="${esc(g.storagePath || '')}">${esc(g.cwd || g.id)}</div>`).join('');
    $('cv-drop-targets').hidden=false;
    event.dataTransfer.effectAllowed='move';
    event.dataTransfer.setData('application/x-task-console-conversation',CV_DRAG.id);
  });
  groups.addEventListener('dragover',event=>{
    const target=event.target.closest('[data-cvproject]');
    if(!CV_DRAG || !target || target.dataset.cvproject===CV_DRAG.projectDir) return;
    event.preventDefault();event.dataTransfer.dropEffect='move';
    groups.querySelectorAll('.cv-dropover').forEach(el=>el.classList.remove('cv-dropover'));
    target.classList.add('cv-dropover');
  });
  groups.addEventListener('dragleave',event=>{
    const group=event.target.closest('[data-cvproject]');
    if(group && !group.contains(event.relatedTarget)) group.classList.remove('cv-dropover');
  });
  groups.addEventListener('drop',event=>{
    const target=event.target.closest('[data-cvproject]'), row=CV_DRAG;
    if(!row || !target) return;
    event.preventDefault();cvClearDrag();
    if(target.dataset.cvproject!==row.projectDir) cvMutate('move',row,target.dataset.cvproject);
  });
  groups.addEventListener('dragend',cvClearDrag);
}
