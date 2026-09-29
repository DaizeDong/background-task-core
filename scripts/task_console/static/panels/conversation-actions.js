// File operations use the console's authenticated action API.
let CV_EDIT=null, CV_EDIT_BUSY=false, CV_DRAG=null;
const CV_DELETIONS=new Map();
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
  if(kind==='delete'){cvOpenDelete(row);return;}
  const rename=kind==='rename', locations=CONVOS?.locations || CONVOS?.groups || [];
  $('cv-delete-field').hidden=true;$('cv-repreview').hidden=true;
  $('cv-submit').classList.remove('cv-danger');
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
const cvDeleteKey=row=>row.projectDir+'/'+row.id;
function cvDeleteSummary(plan){
  $('cv-delete-scope').textContent=`将永久删除 ${plan.files} 个文件（${kb(plan.bytes)}），包括会话记录及其关联文件，并移除 ${plan.indexEntries} 条会话索引。`;
}
async function cvOpenDelete(row,refresh=false){
  const dialog=$('cv-dialog');
  $('cv-dialog-title').textContent='删除会话';
  $('cv-current').textContent=row.title || row.id;
  $('cv-title-field').hidden=true;$('cv-target-field').hidden=true;
  $('cv-new-title').required=false;$('cv-target').required=false;
  $('cv-delete-field').hidden=false;$('cv-repreview').hidden=true;
  $('cv-submit').classList.add('cv-danger');$('cv-submit').textContent='永久删除';
  $('cv-edit-note').className='cv-notice';$('cv-edit-note').textContent='';
  if(!dialog.open) dialog.showModal();
  const key=cvDeleteKey(row), previous=CV_DELETIONS.get(key);
  if(previous && !refresh){
    CV_EDIT.deletion=previous;cvDeleteSummary(previous.plan);
    $('cv-edit-note').textContent='上次删除结果尚未确认。再次提交会核对同一请求。';
    $('cv-submit').textContent='重试原删除请求';$('cv-submit').disabled=false;
    return;
  }
  CV_EDIT_BUSY=true;$('cv-submit').disabled=true;$('cv-cancel').disabled=true;
  $('cv-delete-scope').textContent='正在检查文件和关联记录…';
  try{
    const plan=await api('/api/convo/delete-plan',{method:'POST',body:JSON.stringify({id:row.id,expectedProject:row.projectDir})});
    CV_EDIT.deletion={plan,requestId:crypto.randomUUID()};
    cvDeleteSummary(plan);$('cv-submit').disabled=false;
  }catch(error){
    CV_EDIT.deletion=null;$('cv-delete-scope').textContent='尚未取得删除范围，未执行删除。';
    $('cv-edit-note').className='cv-notice error';$('cv-edit-note').textContent=error.message;
    $('cv-repreview').hidden=false;
  }finally{CV_EDIT_BUSY=false;$('cv-cancel').disabled=false;}
}
async function cvDelete(){
  if(CV_EDIT_BUSY || !CV_EDIT?.deletion || !ConsoleActions.allowWrite()) return;
  const {row,deletion}=CV_EDIT, key=cvDeleteKey(row);
  CV_DELETIONS.set(key,deletion);
  CV_EDIT_BUSY=true;$('cv-submit').disabled=true;$('cv-cancel').disabled=true;
  $('cv-edit-note').className='cv-notice';$('cv-edit-note').textContent='正在删除会话文件及关联记录…';
  try{
    const result=await api('/api/convo/delete',{method:'POST',body:JSON.stringify({
      id:row.id,expectedProject:row.projectDir,fingerprint:deletion.plan.fingerprint,
      requestId:deletion.requestId,confirmed:true})});
    if(result.deleted!==true) throw new Error('服务未确认删除完成，请重试原请求。');
    CV_DELETIONS.delete(key);$('cv-dialog').close();
    if(typeof CH_ID!=='undefined' && CH_ID===row.id) chClose();
    if(typeof CH_FRES!=='undefined' && CH_FRES?.newId===row.id){CH_FRES=null;chRenderAct();}
    CV_DETAILS.delete(row.id);
    toast('会话及关联文件已永久删除','ok');
    for(const warning of result.warnings || []) toast(warning);
    await loadConvos();
  }catch(error){
    const code=error.payload?.code;
    $('cv-edit-note').className='cv-notice error';
    $('cv-edit-note').textContent=error.status?error.message:'删除结果尚未确认。重试会核对原请求。'+error.message;
    if(code==='conflict'){
      CV_DELETIONS.delete(key);CV_EDIT.deletion=null;
      $('cv-repreview').hidden=false;$('cv-submit').textContent='请重新查看删除范围';
    }else $('cv-submit').textContent='重试原删除请求';
  }finally{
    CV_EDIT_BUSY=false;$('cv-submit').disabled=!CV_EDIT?.deletion;$('cv-cancel').disabled=false;
  }
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
    const remove=event.target.closest('[data-cvdelete]'), rename=event.target.closest('[data-cvrename]'), move=event.target.closest('[data-cvmove],[data-cvdrag]');
    if(remove && !remove.disabled) cvOpenManager('delete',remove.dataset.cvdelete);
    else if(rename && !rename.disabled) cvOpenManager('rename',rename.dataset.cvrename);
    else if(move && !move.disabled) cvOpenManager('move',move.dataset.cvmove || move.dataset.cvdrag);
    const open=event.target.closest('[data-cvopen-new]');
    if(open && typeof openConvoChain==='function') openConvoChain(open.dataset.cvopenNew);
  });
  $('cv-form').addEventListener('submit',event=>{
    event.preventDefault();if(!CV_EDIT) return;
    if(CV_EDIT.kind==='delete') cvDelete();
    else cvMutate(CV_EDIT.kind,CV_EDIT.row,CV_EDIT.kind==='rename'?$('cv-new-title').value:$('cv-target').value);
  });
  $('cv-repreview').addEventListener('click',()=>{if(!CV_EDIT_BUSY && CV_EDIT?.kind==='delete') cvOpenDelete(CV_EDIT.row,true);});
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
