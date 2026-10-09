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
// 提交按钮为什么按不动。改名时名字没变也不让交:交上去只是一次什么都没改的写入。
function cvSubmitReason(){
  if(!CV_EDIT) return '';
  if(CV_EDIT_BUSY) return '正在处理';
  if(CV_EDIT.kind==='rename'){
    if(CV_EDIT.suggesting) return '正在生成名称';
    const title=String($('cv-new-title').value || '').trim();
    if(!title) return '先写会话名称';
    if(title===String(CV_EDIT.row.title || '').trim()) return '名称没有变化';
    return '';
  }
  if(CV_EDIT.kind==='move') return CV_EDIT.targets?'':'还没有其他项目目录可供迁移';
  return CV_EDIT.deletion?'':'请重新查看删除范围';
}
function cvSyncSubmit(){
  ConsoleActions.gate($('cv-submit'),cvSubmitReason());
  ConsoleActions.gate($('cv-suggest'),CV_EDIT_BUSY?'正在处理':CV_EDIT?.suggesting?'正在生成名称':'');
  // 改名框里打了新名字时,误点一下遮罩不该把它丢掉;Esc 和取消照样能关。
  const typed=CV_EDIT?.kind==='rename' && String($('cv-new-title').value || '').trim()!==String(CV_EDIT.row.title || '').trim();
  syncDialogDismiss($('cv-dialog'),CV_EDIT_BUSY,typed);
}
function cvOpenManager(kind,id,target){
  if(!ConsoleActions.allowWrite() || CV_EDIT_BUSY) return;
  const row=typeof id==='object'?id:cvSession(id);
  if(!row){toast('这场会话的位置已改变，请刷新列表后重试','bad');return;}
  CV_EDIT={kind,row,suggesting:false};
  if(kind==='delete'){cvOpenDelete(row);return;}
  const rename=kind==='rename', locations=CONVOS?.locations || CONVOS?.groups || [];
  $('cv-delete-field').hidden=true;$('cv-repreview').hidden=true;
  $('cv-submit').classList.remove('danger');$('cv-submit').classList.add('primary');
  $('cv-dialog-title').textContent=rename?'重命名会话':'移动会话文件';
  $('cv-current').textContent=row.file || row.id;
  $('cv-title-field').hidden=!rename;$('cv-target-field').hidden=rename;
  $('cv-new-title').value=row.title || '';$('cv-new-title').required=rename;
  const options=locations.filter(g=>cvKey(g)!==row.projectDir);
  CV_EDIT.targets=options.length;
  $('cv-target').innerHTML=options.map(g=>`<option value="${esc(cvKey(g))}">${esc(g.cwd || g.id)}</option>`).join('');
  // 拖到某个项目上松手时,目标就是那个项目:对话框里预先选好,确认一下就走。
  if(target && options.some(g=>cvKey(g)===target)) $('cv-target').value=target;
  $('cv-target').required=!rename;
  $('cv-submit').textContent=rename?'保存名称':'移动到此项目';
  $('cv-edit-note').textContent=!rename && !options.length?'还没有其他项目目录可供迁移':'';
  $('cv-edit-note').className='cv-notice';
  cvSyncSubmit();cvTargetLocation();$('cv-dialog').showModal();
  const input=rename?$('cv-new-title'):$('cv-target');input.focus?.();if(rename) input.select?.();
}
const cvDeleteKey=row=>row.projectDir+'/'+row.id;
function cvDeleteSummary(plan){
  $('cv-delete-scope').textContent=`将永久删除 ${plan.files} 个文件（${kb(plan.bytes)}），包括会话记录及其关联文件，并移除 ${plan.indexEntries} 条会话索引。`;
}
// 分叉出来的会话标题是「原标题 (fork @ 前八位)」,和原会话、和彼此都长得几乎一样。
// 删掉其中一个之后另一个还在,看上去就像删除没生效。所以删除对话框把「是哪一个」写清楚,
// 并点出列表里还有几个长得像的。只比已加载的列表:没加载的分页不在这里,说法也只说「列表里」。
const cvTitleBase=title=>String(title || '').split(' (fork @')[0].trim();
function cvLookalikes(row){
  const full=String(row?.title || '').trim(), base=cvTitleBase(full), out=[];
  if(!full) return out;
  for(const group of CONVOS?.groups || []) for(const other of group.shown || []){
    if(other.id===row.id) continue;
    const title=String(other.title || '').trim();
    if(title===full || (base && cvTitleBase(title)===base)) out.push({...other,projectDir:cvKey(group)});
  }
  return out;
}
const cvShortId=id=>String(id || '').slice(0,8);
function cvDeleteFacts(row){
  const group=(CONVOS?.groups || []).find(g=>cvKey(g)===row.projectDir);
  const cwd=group?.cwd || row.storageCwd || row.cwd || row.projectDir || '未记录';
  const age=row.ageHours!=null && typeof cvAge==='function'?cvAge(row.ageHours):'<span class="u">时间未知</span>';
  return `<span>工作目录：<code>${esc(cwd)}</code></span><span>会话编号：<code>${esc(cvShortId(row.id))}</code></span><span>最近活动：${age}</span>`;
}
function cvLookalikeNote(row,alike){
  if(!alike.length) return '';
  const ids=alike.slice(0,5).map(other=>cvShortId(other.id)).join('、')+(alike.length>5?' 等':'');
  return `列表里还有 ${alike.length} 个同名或同一来源分叉的会话（${ids}）。只会删除这一个（${cvShortId(row.id)}），其他会话不受影响。`;
}
async function cvOpenDelete(row,refresh=false){
  const dialog=$('cv-dialog');
  $('cv-dialog-title').textContent='删除会话';
  $('cv-current').textContent=row.title || row.id;
  $('cv-delete-facts').innerHTML=cvDeleteFacts(row);
  const alike=cvLookalikeNote(row,cvLookalikes(row));
  $('cv-delete-alike').textContent=alike;$('cv-delete-alike').hidden=!alike;
  $('cv-title-field').hidden=true;$('cv-target-field').hidden=true;
  $('cv-new-title').required=false;$('cv-target').required=false;
  $('cv-delete-field').hidden=false;$('cv-repreview').hidden=true;
  $('cv-submit').classList.remove('primary');$('cv-submit').classList.add('danger');$('cv-submit').textContent='永久删除';
  $('cv-edit-note').className='cv-notice';$('cv-edit-note').textContent='';
  if(!dialog.open) dialog.showModal();
  const key=cvDeleteKey(row), previous=CV_DELETIONS.get(key);
  if(previous && !refresh){
    CV_EDIT.deletion=previous;cvDeleteSummary(previous.plan);
    $('cv-edit-note').textContent='上次删除结果尚未确认。再次提交会核对同一请求。';
    $('cv-submit').textContent='重试原删除请求';cvSyncSubmit();
    return;
  }
  CV_EDIT_BUSY=true;cvSyncSubmit();$('cv-cancel').disabled=true;
  $('cv-delete-scope').textContent='正在检查文件和关联记录…';
  try{
    const plan=await api('/api/convo/delete-plan',{method:'POST',body:JSON.stringify({id:row.id,expectedProject:row.projectDir})});
    CV_EDIT.deletion={plan,requestId:crypto.randomUUID()};
    cvDeleteSummary(plan);
  }catch(error){
    CV_EDIT.deletion=null;$('cv-delete-scope').textContent='尚未取得删除范围，未执行删除。';
    $('cv-edit-note').className='cv-notice error';$('cv-edit-note').textContent=error.message;
    $('cv-repreview').hidden=false;
  }finally{CV_EDIT_BUSY=false;$('cv-cancel').disabled=false;cvSyncSubmit();}
}
async function cvDelete(){
  if(CV_EDIT_BUSY || !CV_EDIT?.deletion || !ConsoleActions.allowWrite()) return;
  const {row,deletion}=CV_EDIT, key=cvDeleteKey(row);
  CV_DELETIONS.set(key,deletion);
  CV_EDIT_BUSY=true;cvSyncSubmit();$('cv-cancel').disabled=true;
  $('cv-edit-note').className='cv-notice';$('cv-edit-note').textContent='正在删除会话文件及关联记录…';
  try{
    const result=await api('/api/convo/delete',{method:'POST',body:JSON.stringify({
      id:row.id,expectedProject:row.projectDir,fingerprint:deletion.plan.fingerprint,
      requestId:deletion.requestId,confirmed:true})});
    if(result.deleted!==true) throw new Error('服务未确认删除完成，请重试原请求。');
    CV_DELETIONS.delete(key);$('cv-dialog').close();
    if(typeof CH_ID!=='undefined' && CH_ID===row.id) chClose();
    if(typeof CH_FRES!=='undefined' && CH_FRES?.newId===row.id){CH_FRES=null;chRenderAct();}
    if(typeof CV_MENU!=='undefined' && CV_MENU===row.id) CV_MENU=null;
    const alike=cvLookalikes(row).length;
    toast(alike?`会话 ${cvShortId(row.id)} 及关联文件已永久删除。列表里另有 ${alike} 个同名或同一来源分叉的会话，它们没有被删除。`
      :'会话及关联文件已永久删除','ok');
    for(const warning of result.warnings || []) toast(warning,'warn');
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
    CV_EDIT_BUSY=false;$('cv-cancel').disabled=false;cvSyncSubmit();
  }
}
function cvTargetLocation(){
  const target=(CONVOS?.locations || CONVOS?.groups || []).find(g=>cvKey(g)===$('cv-target').value);
  $('cv-target-path').textContent=target?.storagePath || '';
}
// 保存成功后先把已加载列表里那一行的名字改掉,再整表刷新:刷新要几百毫秒,这段时间里不该还显示旧名字。
function cvApplyTitle(id,title){
  if(!title) return;
  for(const group of CONVOS?.groups || []){
    const row=(group.shown || []).find(item=>item.id===id);
    if(row){row.title=title;row.titleFrom='custom-title';if(typeof cvPaintGroup==='function') cvPaintGroup(group);}
  }
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
// 改名和迁移只有这一条请求路径:对话框和就地改名都从这里发。
function cvEditRequest(kind,row,value){
  const body={id:row.id,expectedProject:row.projectDir};
  if(kind==='rename') body.title=value;else body.targetProject=value;
  return api('/api/convo/'+kind,{method:'POST',body:JSON.stringify(body)});
}
// 名称建议是一次「借 POST 发出的读」:不改文件,所以 inspect:true,不进最近操作、不锁写按钮、不让读缓存失效。
function cvRequestSuggestion(row){
  return api('/api/convo/suggest-title',{method:'POST',inspect:true,
    body:JSON.stringify({id:row.id,expectedProject:row.projectDir})});
}
const cvSuggestedNote=result=>`已生成建议${result.provider?'（'+result.provider+'）':''}，确认无误再保存`;
// 改名对话框里的「AI 起名」:只把建议填进输入框并选中,保存仍要人按。
let CV_DIALOG_SUGGEST=0;
async function cvDialogSuggest(){
  if(!CV_EDIT || CV_EDIT.kind!=='rename' || CV_EDIT_BUSY || CV_EDIT.suggesting || !ConsoleActions.allowWrite()) return;
  const edit=CV_EDIT, seq=++CV_DIALOG_SUGGEST;
  edit.suggesting=true;cvSyncSubmit();
  $('cv-edit-note').className='cv-notice';$('cv-edit-note').textContent='正在生成…';
  try{
    const result=await cvRequestSuggestion(edit.row);
    if(CV_EDIT!==edit || seq!==CV_DIALOG_SUGGEST || !$('cv-dialog').open) return;
    $('cv-new-title').value=result.title;
    $('cv-edit-note').textContent=cvSuggestedNote(result);
    $('cv-new-title').focus?.();$('cv-new-title').select?.();
  }catch(error){
    if(CV_EDIT!==edit || seq!==CV_DIALOG_SUGGEST) return;
    $('cv-edit-note').className='cv-notice error';$('cv-edit-note').textContent='未生成名称：'+error.message;
  }finally{
    edit.suggesting=false;if(CV_EDIT===edit) cvSyncSubmit();
  }
}
async function cvMutate(kind,row,value){
  if(CV_EDIT_BUSY || !ConsoleActions.allowWrite()) return;
  CV_EDIT_BUSY=true;
  const dialog=$('cv-dialog'), visible=dialog.open;
  cvSyncSubmit();$('cv-cancel').disabled=true;
  if(visible){$('cv-edit-note').className='cv-notice';$('cv-edit-note').textContent=kind==='rename'?'正在保存名称…':'正在迁移文件和关联记录…';}
  else $('cvnote').textContent='正在迁移…';
  try{
    const result=await cvEditRequest(kind,row,value);
    if(kind==='rename') cvApplyTitle(result.id || row.id,result.title);
    cvUpdateSession(result,result.title);
    if(kind==='move') CV_OPEN[result.projectDir]=true;
    if(visible) dialog.close();
    toast(kind==='rename'?'会话名称已保存':result.unchanged?'会话已在该目录':'会话文件和关联记录已迁移','ok');
    for(const warning of result.warnings || []) toast(warning,'warn');
    await loadConvos();
  }catch(error){
    const message=error.status?error.message:'结果尚未确认。重试会核对同一会话，不会创建副本。'+error.message;
    if(visible){$('cv-edit-note').className='cv-notice error';$('cv-edit-note').textContent=message;}
    else{$('cvnote').textContent='迁移未完成';toast(message,'bad');await loadConvos();}
  }finally{
    CV_EDIT_BUSY=false;$('cv-cancel').disabled=false;cvSyncSubmit();
  }
}
// ── 就地改名 ──
// 列表每行标题旁的铅笔、对话链卡片标题本身,点一下就把标题换成一个输入框。同一时间只有一个。
// 状态都在 CV_INLINE 里,输入框是按它重画出来的:列表加载更多、对话链重画时,打了一半的字不会丢。
// Enter 保存,Esc 取消(navigation.js 的 handleEscape 先问这里);点别处只在名字没改过时才收起,
// 改过的名字不会因为一下误点就没了。
let CV_INLINE=null, CV_INLINE_SEQ=0;
const cvInlineDirty=()=>!!CV_INLINE && String(CV_INLINE.value || '').trim()!==String(CV_INLINE.original || '').trim();
function cvInlineReason(kind){
  const state=CV_INLINE;
  if(!state) return '';
  if(kind==='cancel') return state.busy?'正在保存，完成后才能关闭':'';
  if(state.busy) return '正在保存';
  if(state.suggesting) return '正在生成名称';
  if(kind==='save'){
    const title=String(state.value || '').trim();
    if(!title) return '先写会话名称';
    if(title===String(state.original || '').trim()) return '名称没有变化';
  }
  return '';
}
const CV_INLINE_BUTTONS=[['ai','AI 起名',''],['save','保存','primary'],['cancel','取消','']];
function cvInlineHtml(scope,id){
  const state=CV_INLINE;
  if(!state || state.scope!==scope || state.id!==id) return '';
  const button=([key,label,cls])=>{
    const why=cvInlineReason(key);
    return `<button type="button" id="cv-inline-${key}" class="mini${cls?' '+cls:''}" data-cvinline-${key}${why?' disabled':''} data-label="${label}" title="${esc(why?disabledTitle(label,why):label)}">${label}</button>`;
  };
  // 保存或生成进行中输入框也锁住:否则人打的字会被随后回来的建议盖掉。
  const locked=state.busy || state.suggesting;
  return `<span class="cv-inline" data-cvinline="${esc(id)}"><input id="cv-inline-input" type="text" maxlength="200" autocomplete="off" aria-label="会话名称" aria-describedby="cv-inline-note" value="${esc(state.value)}"${locked?` disabled title="${esc(cvInlineReason('ai'))}"`:''}>`
    +`<span class="cv-inline-actions">${CV_INLINE_BUTTONS.map(button).join('')}</span>`
    +`<span id="cv-inline-note" class="cv-inline-note${state.tone==='error'?' error':''}" role="status" aria-live="polite">${esc(state.note || '')}</span></span>`;
}
// 按钮的可用与否随打字变化:只改按钮,不重画输入框,光标和输入法都不受打扰。
function cvInlineSync(){
  if(!CV_INLINE) return;
  for(const [key] of CV_INLINE_BUTTONS){
    const button=$('cv-inline-'+key);
    if(key==='save') ConsoleActions.gate(button,cvInlineReason(key));else setDisabled(button,cvInlineReason(key));
  }
  const note=$('cv-inline-note');
  if(note){note.textContent=CV_INLINE.note || '';note.className='cv-inline-note'+(CV_INLINE.tone==='error'?' error':'');}
}
function cvInlineFocus(select){
  const input=$('cv-inline-input');
  if(!input || input.disabled) return;
  try{input.focus?.({preventScroll:true});}catch(error){}
  if(select) input.select?.();
}
function cvInlineGroup(id){
  return (CONVOS?.groups || []).find(group=>(group.shown || []).some(row=>row.id===id));
}
// 重画编辑器所在的那一处。列表那一处跟着整组重画(cvRows 认得 CV_INLINE),对话链只换标题。
function cvInlineRepaint(scope,id,focus){
  if(scope==='list'){
    const group=cvInlineGroup(id);
    if(group && typeof cvPaintGroup==='function') cvPaintGroup(group);
    else if(typeof renderConvos==='function') renderConvos();
  }else if(typeof chRenderHead==='function') chRenderHead();
  if(focus) cvInlineFocus(focus==='select');
}
function cvInlineNote(note,tone=''){
  if(!CV_INLINE) return;
  CV_INLINE.note=note;CV_INLINE.tone=tone;cvInlineSync();
}
function cvQuickRename(id,scope){
  if(!ConsoleActions.allowWrite()) return;
  if(CV_INLINE){
    if(CV_INLINE.id===id && CV_INLINE.scope===scope){cvInlineFocus(false);return;}
    if(CV_INLINE.busy || cvInlineDirty()){
      cvInlineNote('这里有一个还没保存的名称：先保存，或按「取消」放弃','error');cvInlineFocus(false);return;
    }
    cvInlineClose();
  }
  const row=cvSession(id);
  if(!row){toast('这场会话的位置已改变，请刷新列表后重试','bad');return;}
  const title=String(row.title || '');
  CV_INLINE={id,scope,row,original:title,value:title,busy:false,suggesting:false,note:'',tone:''};
  cvInlineRepaint(scope,id,'select');
}
function cvInlineClose(){
  const state=CV_INLINE;
  if(!state) return;
  CV_INLINE=null;CV_INLINE_SEQ++;
  cvInlineRepaint(state.scope,state.id);
}
// 列表或对话链整块重画以后,焦点会掉回 body。编辑器还开着就把焦点还给它;人已经点到别处去了就不抢。
function cvInlineAfterPaint(){
  if(!CV_INLINE || CV_INLINE.busy) return;
  const active=document.activeElement;
  if(!active || active===document.body) cvInlineFocus(false);
}
async function cvInlineSave(){
  const state=CV_INLINE;
  if(!state || cvInlineReason('save') || !ConsoleActions.allowWrite()) return;
  state.busy=true;state.note='正在保存名称…';state.tone='';
  cvInlineRepaint(state.scope,state.id);
  try{
    const result=await cvEditRequest('rename',state.row,state.value);
    if(CV_INLINE===state) CV_INLINE=null;
    cvApplyTitle(result.id || state.row.id,result.title);
    cvUpdateSession(result,result.title);
    cvInlineRepaint(state.scope,state.id);
    toast('会话名称已保存','ok');
    for(const warning of result.warnings || []) toast(warning,'warn');
    await loadConvos();
  }catch(error){
    if(CV_INLINE!==state) return;
    state.busy=false;state.tone='error';
    state.note=error.status?error.message:'结果尚未确认。重试会核对同一会话，不会创建副本。'+error.message;
    cvInlineRepaint(state.scope,state.id,'keep');
  }
}
async function cvInlineSuggest(){
  const state=CV_INLINE;
  if(!state || cvInlineReason('ai') || !ConsoleActions.allowWrite()) return;
  const seq=++CV_INLINE_SEQ;
  state.suggesting=true;state.note='正在生成…';state.tone='';
  cvInlineRepaint(state.scope,state.id);
  const fresh=()=>CV_INLINE===state && seq===CV_INLINE_SEQ;
  try{
    const result=await cvRequestSuggestion(state.row);
    if(!fresh()) return;
    state.value=result.title;state.note=cvSuggestedNote(result);
  }catch(error){
    if(!fresh()) return;
    state.tone='error';state.note='未生成名称：'+error.message;
  }finally{
    if(fresh()){state.suggesting=false;cvInlineRepaint(state.scope,state.id,'select');}
  }
}
// Esc 的一层。保存进行中吃掉这一下但不关:请求已经发出去了,关掉只会让人看不到结果。
function cvInlineEscape(){
  if(!CV_INLINE) return false;
  if(typeof CURVIEW!=='undefined' && CURVIEW!=='convos') return false;
  if(!CV_INLINE.busy) cvInlineClose();
  return true;
}
function cvInlinePointerDown(event){
  if(!CV_INLINE || CV_INLINE.busy) return;
  if(event.target?.closest?.('[data-cvinline]')) return;
  if(cvInlineDirty()){cvInlineNote('名称还没保存：点「保存」，或点「取消」放弃','error');return;}
  cvInlineClose();
}
function cvClearDrag(){
  CV_DRAG=null;document.querySelectorAll('.cv-dropover').forEach(el=>el.classList.remove('cv-dropover'));
  $('cv-drop-targets').hidden=true;$('cv-drop-targets').innerHTML='';
}
// 拖到另一个项目上松手:不再当场迁移,而是打开同一个移动对话框并预先选好目标。
// 拖放很容易松错地方,迁移又会把文件挪走;多一次确认(Enter 或「移动到此项目」)比挪错了再挪回来便宜。
function cvDrop(event){
  const target=event.target.closest('[data-cvproject]'), row=CV_DRAG;
  if(!row || !target) return;
  event.preventDefault();cvClearDrag();
  if(target.dataset.cvproject!==row.projectDir) cvOpenManager('move',row,target.dataset.cvproject);
}
function startConversationActions(){
  document.addEventListener('click',event=>{
    const remove=event.target.closest('[data-cvdelete]'), rename=event.target.closest('[data-cvrename]'), move=event.target.closest('[data-cvmove],[data-cvdrag]');
    const chosen=[remove,rename,move].find(el=>el && !el.disabled);
    if(chosen) closeMenuFor(chosen);
    if(remove && !remove.disabled) cvOpenManager('delete',remove.dataset.cvdelete);
    else if(rename && !rename.disabled) cvOpenManager('rename',rename.dataset.cvrename);
    else if(move && !move.disabled) cvOpenManager('move',move.dataset.cvmove || move.dataset.cvdrag);
    const open=event.target.closest('[data-cvopen-new]');
    if(open && typeof openConvoChain==='function') openConvoChain(open.dataset.cvopenNew);
    const quick=event.target.closest('[data-cvquick],[data-cvquick-chain]');
    if(quick && !quick.disabled) cvQuickRename(quick.dataset.cvquick || quick.dataset.cvquickChain,quick.dataset.cvquick?'list':'chain');
    const inline=event.target.closest('[data-cvinline-ai],[data-cvinline-save],[data-cvinline-cancel]');
    if(inline && !inline.disabled){
      if(inline.dataset.cvinlineAi!=null) cvInlineSuggest();
      else if(inline.dataset.cvinlineSave!=null) cvInlineSave();
      else if(!CV_INLINE?.busy) cvInlineClose();
    }
  });
  // 点在编辑器外面:捕获阶段先于任何点击处理,所以点另一行的铅笔时,这一个先按规矩收起或留下。
  document.addEventListener('pointerdown',cvInlinePointerDown,true);
  document.addEventListener('input',event=>{
    if(event.target?.id!=='cv-inline-input' || !CV_INLINE) return;
    CV_INLINE.value=event.target.value;
    if(CV_INLINE.tone==='error'){CV_INLINE.note='';CV_INLINE.tone='';}
    cvInlineSync();
  });
  // Enter 就是按「保存」。输入法组字时的 Enter 是在选字,不算。
  document.addEventListener('keydown',event=>{
    if(event.target?.id!=='cv-inline-input') return;
    if(event.key!=='Enter' || event.isComposing || event.repeat || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
    event.preventDefault();cvInlineSave();
  });
  $('cv-suggest').addEventListener('click',cvDialogSuggest);
  $('cv-form').addEventListener('submit',event=>{
    event.preventDefault();if(!CV_EDIT || $('cv-submit').disabled) return;
    if(CV_EDIT.kind==='delete') cvDelete();
    else cvMutate(CV_EDIT.kind,CV_EDIT.row,CV_EDIT.kind==='rename'?$('cv-new-title').value:$('cv-target').value);
  });
  $('cv-new-title').addEventListener('input',cvSyncSubmit);
  // 下拉框不参与表单的隐式提交,在它上面按 Enter 本来什么都不发生。移动对话框里它是唯一的输入,
  // 所以这里补上:提交按钮能按时,Enter 就等于按它。永久删除没有输入框,仍然必须点按钮。
  $('cv-target').addEventListener('keydown',event=>{
    if(event.key!=='Enter' || event.isComposing || event.repeat || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
    event.preventDefault();
    if(CV_EDIT?.kind==='move' && !$('cv-submit').disabled) $('cv-form').requestSubmit();
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
    // 拖动从菜单里的「移动」开始,菜单浮在最上层会盖住落点:拖起来之后再收起它。
    const menu=handle.closest('[popover]');
    if(menu) setTimeout(()=>{try{menu.hidePopover();}catch(error){}},0);
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
  groups.addEventListener('drop',cvDrop);
  groups.addEventListener('dragend',cvClearDrag);
}
