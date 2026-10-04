// Classic script module; loaded in app.js dependency order.

let DATA=null, ROWS=[], VIEW=[], cur=0, sel=new Set(), sortKey="severity", asc=true, busy=false;
// Keep routine operation compact; retain saved column and safeguards choices.
const HYGIENE = ["catchup","retries","timeout","artifact","inAllow","inHealth"];
// 显示哪些列只存一处:tc.taskColumns。保障配置那六列以前由工具栏上单独一个按钮整组开合,存在 tc.hyg;
// 现在它们在「显示列」里自成一组、逐列勾选。旧的 tc.hyg 读进来并入一次就删掉,免得两份设置互相打架。
// 存储读不了(隐私窗口、禁用存储)就用默认列,这只是本机浏览器里的一点便利。
function loadTaskColumns(getStorage){
  let columns=new Set(['sl','ops','triggers','nextRun']);
  try{
    const storage=getStorage();
    const saved=JSON.parse(storage.getItem('tc.taskColumns'));
    if(Array.isArray(saved)) columns=new Set(saved.filter(key=>typeof key==='string'));
    const legacy=storage.getItem('tc.hyg');
    if(legacy==='1'){
      HYGIENE.forEach(key=>columns.add(key));
      storage.setItem('tc.taskColumns',JSON.stringify([...columns]));
    }
    if(legacy!=null) storage.removeItem('tc.hyg');
  }catch(e){}
  return columns;
}
let TASK_COLUMNS = loadTaskColumns(()=>localStorage);
// 默认按严重程度排:失败、有警告、正常、停用,同一档里按标题。按名字排时,失败的任务散在第 7 到第 42 行,
// 要找有事的得把整张表读一遍。人点了列标题换过排序,就记在本机浏览器里(tc.taskSort),下次照旧。
// 存的列现在被「显示列」藏起来了,render() 会退回默认排序;存储读不了就用默认,这只是一点便利。
const TASK_SORT_DEFAULT='severity';
function loadTaskSort(getStorage){
  try{
    const saved=JSON.parse(getStorage().getItem('tc.taskSort'));
    if(saved && typeof saved.key==='string' && saved.key) return {key:saved.key,asc:saved.asc!==false};
  }catch(e){}
  return {key:TASK_SORT_DEFAULT,asc:true};
}
({key:sortKey,asc}=loadTaskSort(()=>localStorage));
let SAVED_TASK_SORT=sortKey+':'+asc;
function saveTaskSort(){
  const value=sortKey+':'+asc;
  if(value===SAVED_TASK_SORT) return;
  SAVED_TASK_SORT=value;
  try{localStorage.setItem('tc.taskSort',JSON.stringify({key:sortKey,asc}));}catch(e){}
}
// 问题等级:有一条 bad 就是 bad,否则有 warn 就是 warn。issues 缺失的行(同步与备份卡片上占位用的那种)当没有问题。
const taskIssueLevel=row=>{const issues=row.issues || [];return issues.some(i=>i[0]==='bad')?'bad':issues.some(i=>i[0]==='warn')?'warn':'';};
function taskSeverityRank(row){
  if(row.sk==='bad') return 0;
  if(row.state==='Disabled') return 3;
  return taskIssueLevel(row)?1:2;
}
function taskSeverityCompare(a,b){
  return taskSeverityRank(a)-taskSeverityRank(b) || taskText(a).title.localeCompare(taskText(b).title,'zh') || String(a.name).localeCompare(String(b.name));
}
// 列表本身的顺序(任务开关、同步与备份照它排):分组照后端给的,组内失败的排到最前,其余保持原样。
function taskRowsInOrder(groups){
  return groups.flatMap(group=>{
    const rows=group.rows.map(row=>Object.assign({cat:group.cat},row));
    return [...rows.filter(row=>row.sk==='bad'),...rows.filter(row=>row.sk!=='bad')];
  });
}
// 排序状态写在任务列表的卡片标题旁:默认那一档不在任何列标题上,不写出来就没人知道表是按什么排的。
function syncTaskSortState(){
  const state=$('task-sort-state'), back=$('task-sort-severity');
  const column=C.find(c=>c[0]===sortKey);
  if(state) state.textContent=sortKey===TASK_SORT_DEFAULT?'按严重程度：失败在前':`按「${column?column[1]:sortKey}」${asc?'升序':'降序'}`;
  if(back) setDisabled(back,sortKey===TASK_SORT_DEFAULT?'已经按严重程度排序':'');
}
function sortTasksBySeverity(){
  sortKey=TASK_SORT_DEFAULT;asc=true;
  if(DATA) render(); else syncTaskSortState();
}
const shownCols = () => C.filter(c=>['selc','name'].includes(c[0]) || TASK_COLUMNS.has(c[0]));
// 运行状态筛选,由表格上方那四个计数按钮切换:'' 全部,bad 上次运行失败,warn 有警告,off 已停用。
// 以前只有一个「只看异常与警告」勾选框,把失败和警告混在一起,而那四个计数看着像按钮却点不动。
// 「有警告」和行上那枚健康芯片用同一个判定(taskHealth):上次运行正常、但带着问题的任务。
// 以前数的是「有任何问题」,失败、停用、常驻的任务也算进去,按钮写 15,筛出来只有 3 行挂着「有警告」,
// 失败的任务在「失败」和「有警告」里各数一次。
let TASK_STATUS='';
const TASK_STATUS_LABEL={'':'全部任务',bad:'只看失败',warn:'只看有警告',off:'只看停用'};
const TASK_STATUS_EMPTY={bad:'没有上次运行失败的任务',warn:'没有带警告的任务',off:'没有停用的任务'};
function taskMatchesStatus(row,status=TASK_STATUS){
  if(status==='bad') return row.sk==='bad';
  if(status==='warn') return taskHealth(row).tone==='warn';
  if(status==='off') return row.state==='Disabled';
  return true;
}
function taskStatusCounts(rows){
  return {'':rows.length,bad:rows.filter(row=>taskMatchesStatus(row,'bad')).length,
    warn:rows.filter(row=>taskMatchesStatus(row,'warn')).length,off:rows.filter(row=>taskMatchesStatus(row,'off')).length};
}
// 按钮的按下态跟着筛选走。数到零的那一类灰着并说明为什么(正在用的那一个除外,它还要能点回全部);
// 计划任务还没读到时四个都灰着:那时点下去什么也筛不出来。
function syncTaskStatus(counts){
  document.querySelectorAll?.('.task-summary [data-task-status]').forEach(button=>{
    const key=button.dataset.taskStatus, active=key===TASK_STATUS;
    button.setAttribute('aria-pressed',String(active));
    button.dataset.label=TASK_STATUS_LABEL[key];
    const reason=!counts?(TASKS_LOAD_ERROR?'计划任务读取失败':'计划任务还没读到'):(key && !active && !counts[key])?TASK_STATUS_EMPTY[key]:'';
    setDisabled(button,reason);
    if(!reason) button.title=!key?'显示全部任务，清除状态筛选':active?`${TASK_STATUS_LABEL[key]}：再点一次显示全部任务`:`${TASK_STATUS_LABEL[key]}，再点一次显示全部`;
  });
}
function setTaskStatus(status){
  TASK_STATUS=(status && status!==TASK_STATUS)?status:'';
  // 选「只看停用」时把「隐藏停用」取消掉:两个同时生效,表就是空的,而人看不出是哪一个造成的。
  if(TASK_STATUS==='off' && $('hideoff').checked) $('hideoff').checked=false;
  if(DATA) render(); else syncTaskStatus(null);
}

// Metadata presentation is shared by all automation tabs. No inferred verdicts.
// 建议徽章不借用状态徽章的符号(✓ 正常、Ⅱ 停用),外框另用虚线(workbench.css 的 .task-verdict):
// 「建议保持停用」和「此刻已停用」是两件事,配置过期之后它们会不一致,屏幕上必须分得开。
const TASK_VERDICTS = {
  urgent:{label:'急修',tone:'bad',symbol:'!',className:'task-verdict-urgent'},
  fix:{label:'要修',tone:'bad',symbol:'!'},
  adjust:{label:'调整',tone:'warn',symbol:'↻'},
  decide:{label:'待定',tone:'pending',symbol:'?'},
  remove:{label:'可删',tone:'muted',symbol:'·'},
  disabled:{label:'保持停用',tone:'muted',symbol:'·'},
  keep:{label:'保留',tone:'muted',symbol:'·'}
};
// 兜底桶分开数:「没写建议」是缺失,「写了但认不出 / 整条说明写坏了」是坏掉,
// 「计划任务还没读到」是还不知道。三种要做的事不一样,计数也不能混在一起。
const TASK_VERDICT_EXTRA = {unrecognized:{label:'无法识别'}, unassessed:{label:'未评估'}, pending:{label:'建议未读取'}};
function taskText(row){
  const desc=typeof row.desc==='string'?row.desc.trim():'';
  // 半角冒号后面跟数字、斜杠或反斜杠时不是分隔符:08:00、C:\、https:// 都不能被切成标题和摘要。
  const separator=desc.match(/[。]|[：:](?![\d\/\\])/);
  const title=separator?desc.slice(0,separator.index).trim():desc;
  const summary=separator?desc.slice(separator.index+1).trim():'';
  const infoTitle=row.info?.title?.trim();
  // 标题来自 info、desc 又切不出摘要时,整段 desc 是这条任务仅有的说明,不能因此整段消失。
  const fallback=infoTitle && !summary && desc && desc!==infoTitle ? desc : summary;
  return {title:infoTitle || title || row.name || '未命名任务',
    summary:row.info?.summary?.trim() || fallback};
}
function taskVerdict(row){
  const key=row.info?.verdict;
  return Object.hasOwn(TASK_VERDICTS,key)?key:'';
}
// 服务端认得、这里不认得的建议值(两边的表只改了一边)也算认不出,不能落成「未评估」。
function taskVerdictUnknown(row){
  const raw=row.info?.verdict;
  if(typeof raw==='string' && raw && !Object.hasOwn(TASK_VERDICTS,raw)) return raw;
  return row.info?.verdictUnrecognized || '';
}
function taskVerdictBucket(row){
  if(row.infoPending) return 'pending';
  const key=taskVerdict(row);
  if(key) return key;
  return row.infoInvalid || taskVerdictUnknown(row) ? 'unrecognized' : 'unassessed';
}
function taskVerdictBadge(row){
  if(row.infoPending) return statusBadge('建议未读取','idle','?','task-verdict');
  if(row.infoInvalid) return statusBadge('说明写法不对','bad','!','task-verdict');
  const verdict=TASK_VERDICTS[taskVerdict(row)];
  if(verdict) return statusBadge(verdict.label,verdict.tone,verdict.symbol,'task-verdict '+(verdict.className || ''));
  if(taskVerdictUnknown(row)) return statusBadge('建议无法识别','warn','?','task-verdict');
  return statusBadge('未评估','idle','?','task-verdict');
}
function taskMatches(row,query){
  const q=query.trim().toLowerCase(), text=taskText(row);
  return !q || [text.title,text.summary,row.info?.advice,row.name,row.desc,row.cat].join(' ').toLowerCase().includes(q);
}
function taskMatchesVerdict(row,verdict){
  return !verdict || taskVerdictBucket(row)===verdict;
}
function taskVerdictCounts(rows){
  const counts={};
  rows.forEach(row=>{const key=taskVerdictBucket(row);counts[key]=(counts[key] || 0)+1;});
  return counts;
}
function taskVerdictOptions(rows,selected=''){
  const counts=taskVerdictCounts(rows);
  return `<option value="">全部建议 (${rows.length})</option>`+
    [...Object.entries(TASK_VERDICTS),...Object.entries(TASK_VERDICT_EXTRA)]
      // 「建议未读取」只在计划任务还没读到时才有行;平时列一个恒为 0 的选项只是噪音。
      // 选中着的选项必须留着,否则下拉框会显示「全部建议」而表格仍按它在筛。
      .filter(([key])=>key!=='pending' || counts[key] || key===selected)
      .map(([key,value])=>`<option value="${key}">${value.label} (${counts[key] || 0})</option>`).join('');
}
function updateTaskVerdictFilter(id,rows,selected){
  const control=$(id);
  if(!control) return;
  control.innerHTML=taskVerdictOptions(rows,selected);control.value=selected;
}
function taskVerdictSummary(rows){
  const counts=taskVerdictCounts(rows);
  return [...Object.entries(TASK_VERDICTS),...Object.entries(TASK_VERDICT_EXTRA)]
    .filter(([key])=>counts[key]).map(([key,value])=>`${value.label} ${counts[key]}`).join(' · ');
}
// ================= 健康状况 ========================================================
// 计划程序的「已启用」只说它会不会按时启动,不说上次跑得好不好。任务开关和同步与备份以前只摆前者,
// 于是十几个失败的任务个个挂着绿色的「✓ 已启用」,和健康的一模一样。现在三处都先摆健康状况,
// 和运行详情的「状态」列是同一个芯片;计划程序的状态退成旁边一行小字。
// 上次运行结果码。常见的几种直接说人话,其余只给十进制的退出码;十六进制原码一律留在悬停里。
const TASK_RC_MEANINGS={'0x1':'程序以 1 退出，通常是脚本自己报错','0x2':'找不到要运行的文件','0x41306':'任务被强行终止',
  '0x8004131f':'上一次运行还没结束，这次没有启动','0x800710e0':'计划程序拒绝了这次启动，常见于账户或登录条件不满足',
  '0xc000013a':'程序被中断（Ctrl+C 或关机）','0xffffffff':'程序以 -1 退出','0x80070002':'找不到要运行的文件',
  // 这几个是计划程序自己的状态码,不是程序的退出码:写成「267009」没人看得懂。
  '0x0':'成功','0x41300':'任务已就绪','0x41301':'正在运行','0x41302':'任务已停用','0x41303':'还没有运行过'};
function taskExitCode(row){
  const raw=String(row.rcHex || (/^失败\s+(\S+)$/.exec(row.sl || '') || [])[1] || '').trim();
  const match=/^0x([0-9a-f]+)$/i.exec(raw);
  if(!match){const text=raw && raw!=='?'?raw:'未知';return {text,hex:raw,meaning:'',label:'退出码 '+text};}
  const value=parseInt(match[1],16), hex='0x'+match[1].toUpperCase();
  // 32 位的系统错误码按无符号十进制写是一串没人认得的长数字;按有符号写,和命令行里 %ERRORLEVEL% 看到的一样。
  const meaning=TASK_RC_MEANINGS['0x'+match[1].toLowerCase().replace(/^0+(?=.)/,'')] || '';
  return {text:String(value>0x7fffffff?value-0x100000000:value),hex,meaning,
    // 大于 0xFFFF 的是系统或计划程序的码,十进制没有意义;认得的就只说含义。
    label:meaning && value>0xffff?meaning:'退出码 '+String(value>0x7fffffff?value-0x100000000:value)};
}
// 「退出码 1（程序以 1 退出…）」;系统码认得时只说含义,例如「正在运行」。
const taskExitCodeText=rc=>rc.label+(rc.meaning && rc.label.startsWith('退出码')?'（'+rc.meaning+'）':'');
function taskHealth(row){
  if(row.state==='Disabled' || row.sk==='disabled') return {label:'已停用',tone:'muted',symbol:'Ⅱ',title:'已停用，不会按计划运行'};
  if(row.sk==='bad'){
    const rc=taskExitCode(row);
    return {label:`失败 · ${rc.text==='未知'?'退出码未知':rc.label}`,tone:'bad',title:`上次运行结果 ${rc.hex || '未知'}${rc.meaning?'：'+rc.meaning:''}`};
  }
  if(row.sk==='running') return {label:row.sl || '运行中',tone:'active',title:'任务正在运行'};
  if(row.sk==='pending') return {label:row.sl || '尚未首跑',tone:'pending',title:'还没有运行过'};
  if(row.sk==='unknown') return {label:row.sl || '信息读不到',tone:'idle',title:'读不到这个任务的运行信息'};
  if(row.sk==='ok'){
    const first=(row.issues || [])[0];
    if(taskIssueLevel(row)) return {label:'有警告',tone:'warn',title:first?first[1]:'有警告'};
    return {label:row.sl || '正常',tone:'ok',title:'上次运行正常'};
  }
  return {label:'未知',tone:'idle',title:'没有读到上次运行结果'};
}
function taskHealthBadge(row){
  const health=taskHealth(row);
  return `<span class="task-health" title="${esc(health.title)}">${statusBadge(health.label,health.tone,health.symbol,'st')}</span>`;
}
// toggle=true 只给运行详情那张表用:标题做成一个真按钮,展开和点整行是同一件事。
// 键盘上没有了 j/k,这个按钮就是走到一行、打开它的那条路;另外两处列表的标题照旧是文字。
function taskIdentityHtml(row,{toggle=false}={}){
  const text=taskText(row);
  const title=toggle?`<button type="button" class="row-toggle" data-row-toggle="${esc(row.name)}" aria-expanded="${OPEN_DETAIL===row.name}"><strong>${esc(text.title)}</strong></button>`:`<strong>${esc(text.title)}</strong>`;
  return `<div class="automation-name"><div class="task-heading">${title}${taskVerdictBadge(row)}</div>`+
    (text.summary?`<p class="task-summary-text" title="${esc(text.summary)}">${esc(text.summary)}</p>`:'')+
    `<small class="task-machine-name">${esc(row.name)}</small></div>`;
}
// relative=true 是表格那一窄列用的短说法,完整说法进 title。几种情况的短说法也必须互不相同。
function taskNextRun(row,relative=false){
  // 停用的任务计划程序照样按触发器推算出下次时间,但它不会跑(时间轴同样跳过停用任务);
  // 照抄那个时间,等于在停用的任务旁边写「1 分钟后」。
  if(row.state==='Disabled') return relative?'不会运行':'已停用，不会运行';
  if(row.infoError) return relative?'读取失败':'下次运行时间读取失败';
  if(!Object.hasOwn(row,'nextRun')) return relative?'未读取':'未读取下次运行时间';
  if(!row.nextRun) return relative?'无下次时间':'没有下次运行时间';
  if(Number.isNaN(new Date(row.nextRun).getTime())) return '时间无法识别';
  // 完整说法是悬停里那个绝对时刻。workTime 默认写相对时间,用它的话悬停只是把「51 分钟后」再说一遍。
  return relative?relTime(row.nextRun):fullTime(String(row.nextRun).replace(' ','T'));
}
// 下次运行写成相对时间(「2 小时后」),和运行详情的「下次」列一个说法;完整时刻在悬停里。
// 停用、读取失败、没有时间这几种照旧用整句,它们之间必须分得开。
function taskNextRunLine(row){
  const valid=row.nextRun && !row.infoError && row.state!=='Disabled' && Number.isFinite(timeValue(String(row.nextRun).replace(' ','T')));
  if(!valid) return `<small>${esc(taskNextRun(row))}</small>`;
  return `<small title="${esc('下次运行 '+fullTime(String(row.nextRun).replace(' ','T')))}">下次 ${esc(taskNextRun(row,true))}</small>`;
}
// 任务开关上「查看详情」就地展开在这一行下面(AUTO_DETAIL 记的是哪一个),不再跳到运行详情、
// 顺手把那边的搜索框和筛选全改掉。同步与备份的卡片上 inline=false,照旧跳去运行详情。
let AUTO_DETAIL=null;
function taskListRow(row,{controls=taskActionButtons(row,{scope:'auto'}),state=null,showDetails=true,inline=true}={}){
  const badge=state || taskHealthBadge(row);
  // 计划程序的状态退成小字;停用时芯片本身已经说了,不再重复。
  const scheduler=state || row.state==='Disabled' || row.sk==='disabled'?'':`<small class="automation-scheduler" title="Windows 计划程序里的状态">计划程序：${esc(taskStateLabel(row.state))}</small>`;
  const open=inline && showDetails && AUTO_DETAIL===row.name;
  const details=!showDetails?'':inline
    ?`<button type="button" class="mini task-peek" data-task-peek="${esc(row.name)}" aria-expanded="${open}" title="${open?'收起这个任务的明细':'在这一行下面展开明细'}"><svg class="ic" aria-hidden="true"><use href="#${open?'i-up':'i-eye'}"/></svg><span class="task-op-label">${open?'收起':'详情'}</span></button>`
    :`<button class="icon-only record-link" data-task="${esc(row.name)}" title="在运行详情里查看"><svg class="ic" aria-hidden="true"><use href="#i-eye"/></svg><span class="control-label">在运行详情里查看</span></button>`;
  return `<article class="automation-row${row.sk==='bad'?' is-failing':''}" data-task-row="${esc(row.name)}">${taskIdentityHtml(row)}
    <div class="automation-schedule" title="${esc(taskSchedule(row,true))}">${esc(taskSchedule(row))}${taskNextRunLine(row)}</div>
    <div class="automation-state">${badge}${scheduler}${taskRepairChip(row.name)}</div><div class="automation-actions">${controls}
    ${details}</div>${open?`<div class="automation-detail det">${taskDetailBody(row,{close:`data-task-peek="${esc(row.name)}"`})}</div>`:''}</article>`;
}
function toggleAutomationDetail(name){
  AUTO_DETAIL=AUTO_DETAIL===name?null:name;
  renderAutomations();
}
// 搜索框里按 Enter:只剩一个任务时就地展开它。
// 剩下几个按列表自己的那套筛选算,不数 DOM:列表还没画出来时也答得对。
function openOnlyAutomation(){
  if(!DATA) return false;
  const verdict=$('automation-verdict')?.value || '';
  const rows=automationRows(ROWS,AUTO_QUERY,AUTO_STATE).filter(row=>taskMatchesVerdict(row,verdict));
  if(rows.length!==1) return false;
  const name=rows[0].name;
  if(AUTO_DETAIL!==name){AUTO_DETAIL=name;renderAutomations();}
  return true;
}
function collapseAutomationDetail(){
  if(!AUTO_DETAIL) return false;
  const name=AUTO_DETAIL;AUTO_DETAIL=null;renderAutomations();
  const button=findTaskControl($('automation-list'),{attr:'data-task-peek',value:name,name});
  if(button) try{button.focus({preventScroll:true});button.scrollIntoView({block:'nearest'});}catch(e){}
  return true;
}
function taskInfoHtml(row){
  // 写坏了的说明不能画成一排「未填写」:那会让人去补写,而真正要做的是去改那条写错的配置。
  // 坏在哪里就地说出来:汇总警告在「诊断」分区,自动化的三个标签页上都看不到它。
  if(row.infoInvalid){
    const why=typeof row.infoInvalid==='string'?`:${row.infoInvalid}`:'';
    return `<section class="task-info" aria-label="任务说明"><p class="review-notice">这条任务在分类配置里的 taskInfo 写法不对,已整条忽略${esc(why)}。</p></section>`;
  }
  const info=row.info || {}, text=taskText(row), unknownVerdict=taskVerdictUnknown(row);
  const value=(v,missing)=>v?esc(v):`<span class="u">${missing}</span>`;
  const verdictNote=unknownVerdict?`<small class="task-info-date">建议值「${esc(unknownVerdict)}」认不出,按「建议无法识别」处理</small>`:'';
  return `<section class="task-info" aria-label="任务说明"><dl class="task-info-fields">
    <dt>用途</dt><dd>${value(text.summary,'未填写用途摘要')}</dd>
    <dt>频率</dt><dd>${value(info.cadence,'未填写频率说明')}</dd>
    <dt>现状</dt><dd>${value(info.status,'未填写现状')}<small class="task-info-date">${info.asOf?'核对于 '+esc(info.asOf):'未填写核对日期'}</small></dd>
    <dt>建议</dt><dd>${value(info.advice,'未填写建议')}${verdictNote}</dd></dl></section>`;
}
function renderTaskColumns(){
  const box=$('task-column-options');if(!box) return;
  const option=c=>`<label><input type="checkbox" data-task-column="${c[0]}"${TASK_COLUMNS.has(c[0])?' checked':''}> ${c[1]}</label>`;
  const group=(label,columns)=>`<div class="task-column-group" role="group" aria-label="${label}"><div class="task-column-heading" aria-hidden="true">${label}</div>${columns.map(option).join('')}</div>`;
  box.innerHTML=group('常用',C.filter(c=>!['selc','name',...HYGIENE].includes(c[0])))
    +group('保障配置',C.filter(c=>HYGIENE.includes(c[0])));
}
// 时间轴可视窗口,单位分钟。整天是 [0,1440];缩放和拖动只改这两个数,所有位置都由它们算出来。
let tlFrom=0, tlTo=1440;
const TL_MIN_SPAN=5;    // 最小窗口 5 分钟。实测深度缩放时一帧 1.1ms,成本由 25 行固定的
                        // 行名和轨道 div 主导而不是标记数,所以收窄下限不额外花钱。
const mins = t => { const p=String(t).split(":"); return (+p[0])*60+(+p[1]); };

// Format the owner's trigger fields; do not calculate or infer future runs here.
function taskDuration(value){
  const match=/^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$/.exec(value || '');
  if(!match || !match.slice(1).some(part=>part!==undefined)) return value || '未设置';
  const units=['天','小时','分钟','秒'];
  return match.slice(1).map((part,i)=>Number(part)?Number(part)+' '+units[i]:'').filter(Boolean).join(' ') || '0 秒';
}
function taskSchedule(row,full=false){
  if(!row.triggersRaw?.length) return row.triggers || '未记录触发计划';
  return row.triggersRaw.map(trigger=>{
    const kind=(trigger.kind || '').toLowerCase();
    // 整分的时刻不写秒:「每天 22:40」,不是「每天 22:40:00」。秒不是零时照写,那是真有的信息。
    const clock=(trigger.start?.match(/T(\d\d:\d\d(?::\d\d)?)/)?.[1] || '').replace(/^(\d\d:\d\d):00$/,'$1');
    const time=clock?' '+clock:'';
    const events={logon:'登录时',boot:'开机时',registration:'注册任务时',idle:'系统空闲时',event:'指定事件发生时',sessionstatechange:'会话状态变化时'};
    let label=events[kind];
    if(kind==='daily') label=(trigger.days===1?'每天':trigger.days?'每 '+trigger.days+' 天':'按日计划')+time;
    if(kind==='weekly'){
      const days=['日','一','二','三','四','五','六'].filter((_,i)=>(Number(trigger.dow)&(1<<i))!==0);
      label=(trigger.weeks===1?'每周':trigger.weeks?'每 '+trigger.weeks+' 周':'按周计划')+(days.length?' '+days.map(day=>'周'+day).join('、'):'')+time;
    }
    if(kind==='time') label=trigger.interval?'':'指定时间'+(trigger.start?' '+trigger.start:'');
    if(label===undefined) label='触发条件未识别'+(trigger.kind?'（'+trigger.kind+'）':'');
    if(trigger.interval) label+=(label?'，':'')+'每 '+taskDuration(trigger.interval)+' 重复'+(trigger.duration?'，持续 '+taskDuration(trigger.duration):'');
    if(trigger.enabled===false) label+='（此触发器已停用）';
    if(full) label+=(trigger.start?'；开始 '+trigger.start:'')+(trigger.end?'；结束 '+trigger.end:'');
    else if(trigger.end) label+='，有截止时间';
    return label;
  }).join('；');
}

// 最近一次读计划任务失败的原因。读失败和还没读完在别的分区(同步与备份)要显示成两回事。
let TASKS_LOAD_ERROR=null;
// 计划任务还没读到时,顶上四个计数是「…」;读失败是红色「!」,原因在悬停里。以前两种情况都是同一个「-」。
// 读到了以后由 render() 写数字。
function renderTaskCountsPending(){
  const cell=TASKS_LOAD_ERROR?countCell('broken',null,TASKS_LOAD_ERROR):countCell('loading');
  const title=TASKS_LOAD_ERROR?'读取失败：'+TASKS_LOAD_ERROR:'正在读取';
  ['s-total','s-bad','s-iss','s-off'].forEach(id=>{const el=$(id);if(!el) return;el.innerHTML=cell;el.className='';el.title=title;});
}
async function load(){
  // 新的一次读取一开始就清掉上一次的失败:读取中的那段时间,同步与备份要说「正在读取」,
  // 而不是把已经过去的那次失败当成现在的状态。
  TASKS_LOAD_ERROR=null;
  if(!DATA) renderTaskCountsPending();
  try{
    DATA=await api("/api/tasks");
    if(DATA.error) throw new Error(DATA.error);
    ROWS=taskRowsInOrder(DATA.groups);
    const s=$("cat"), keep=s.value;
    s.innerHTML='<option value="">全部分类</option>'+DATA.groups.map(g=>`<option>${esc(g.cat)}</option>`).join("");
    s.value=keep;
    render();
    if(typeof renderPipelines==="function") renderPipelines();
  }catch(e){ DATA=null; ROWS=[]; TASKS_LOAD_ERROR=e.message || '读取失败'; syncTaskStatus(null); renderTaskCountsPending(); updateBadges(); if(typeof renderPipelines==="function") renderPipelines(); $("tbl").innerHTML=`<tbody><tr><td>${errorBlock('计划任务',e)}</td></tr></tbody>`; }
}

// 把渲染合并到一帧里。之前滚轮和拖动都是每个事件同步渲染一次,而浏览器一次拖动可以
// 派发出远多于一帧的 pointermove,于是同一帧内白渲染好几遍。
let tlPending=false;
function scheduleTL(){
  if(tlPending) return;
  tlPending=true;
  requestAnimationFrame(()=>{ tlPending=false; if(DATA) renderTL(); });
}
function tlPos(m){ return (m - tlFrom) / (tlTo - tlFrom) * 100; }
function tlTicks(){
  // 刻度密度跟着窗口走。整天时每 2 小时一格,放大到半小时窗口时每 5 分钟一格,
  // 否则要么挤成一片黑要么整条轴上一个标都没有。
  const span = tlTo - tlFrom;
  const step = span > 720 ? 120 : span > 240 ? 60 : span > 120 ? 30 : span > 40 ? 10 : 5;
  const out = [];
  for (let m = Math.ceil(tlFrom/step)*step; m <= tlTo; m += step){
    const h = String(Math.floor(m/60)%24).padStart(2,"0"), mi = String(m%60).padStart(2,"0");
    out.push(`<i style="left:${tlPos(m)}%">${step>=60?h:h+":"+mi}</i>`);
  }
  return out.join("");
}
// 缩放按钮到了头就灰着并说为什么:整天时缩小和回到整天什么也不做,放到 TL_MIN_SPAN 时放大也一样。
function syncTLZoom(){
  const span=tlTo-tlFrom, full=span>=1440 && tlFrom<=0;
  setDisabled($("tlout"),span>=1440?'已是整天':'');
  setDisabled($("tlreset"),full?'已是整天':'');
  setDisabled($("tlin"),span<=TL_MIN_SPAN?'已放到最大':'');
}
// 时间轴上按下又松开、中间移动不到 4px,算一次点击,打开那一行的任务;再多就是拖动平移。
// #tl 上有指针捕获,浏览器自己的 click 落不到那一行上,所以点击要从按下和松开两头自己认。
const TL_CLICK_SLOP=4;
function tlClickTask(down,up){
  if(!down || !down.task || !up) return null;
  return Math.hypot(up.x-down.x,up.y-down.y)<TL_CLICK_SLOP?down.task:null;
}
function renderTL(){
  syncTLZoom();
  const T=DATA.timeline;
  if(!T||!T.rows||!T.rows.length){ $("tlbox").hidden=true; return; }
  $("tlbox").hidden=false;
  const rlDown = !(DATA.runlog && DATA.runlog.available);
  // ⚠ 这里原来把三样东西拼成一个字符串:日期与行数(体征)、T.note(通用说明,
  // 读一次就够)、以及「运行日志不可用」(**真实状态,必须有人看见**)。
  // 一起折叠就等于把那条告警藏进灰字堆;所以先拆开,状态单独升成警告条。
  $("tlnote").textContent=`${T.date} 现在 ${T.now} · ${T.rows.length} 行`;
  const help=$("tlhelp");
  if(help){ help.textContent="说明"; help.dataset.full=T.note||""; help.title=T.note||""; }
  const w=$("tlwarn");
  if(w){ w.hidden=!rlDown;
    w.textContent="暂时读不到运行日志。本图只显示计划，无法确认任务实际是否运行。"; }
  const fmt=m=>`${String(Math.floor(m/60)).padStart(2,"0")}:${String(Math.round(m)%60).padStart(2,"0")}`;
  $("tlrange").textContent=`${fmt(tlFrom)}-${fmt(Math.min(tlTo,1439))}`;
  const nowM=mins(T.now);
  // 视窗外的标记直接不渲染。让它们留在 DOM 里靠 overflow 裁掉,在放大到几十分钟时
  // 等于每行仍要摆几百个绝对定位元素,滚动会明显掉帧。
  const vis=m=>m>=tlFrom-1&&m<=tlTo+1;
  // timeline.build 拼的行只有 name/cat/points/spans/eventDriven/unknownTriggers/actual/sk,
  // 不带说明;说明在任务表那条通路上(ROWS)。行名从那里取中文标题,机器名留在悬停提示里。
  // 任务表还没读到、或者里面没有这一行时,照旧显示机器名,不编一个名字出来。
  const byName=new Map(ROWS.map(t=>[t.name,t]));
  const rows=T.rows.map(r=>{
    const known=byName.get(r.name), label=known?taskText(known).title:r.name;
    const nameTip=label===r.name?r.name:`${label} · ${r.name}`;
    const marks=r.points.filter(p=>vis(mins(p))).map(p=>
      `<i class="tlpt${mins(p)<=nowM?" done":""}" style="left:${tlPos(mins(p))}%" title="${esc(label)} 计划 ${p}"></i>`).join("");
    const spans=r.spans.map(sp=>{
      const a=Math.max(mins(sp.from),tlFrom), b=Math.min(mins(sp.to),tlTo);
      if(b<=a) return "";
      // truncated 后端算了、传了、也测了,而这里从来没读过 ——
      // 于是一个十秒级的任务会在 tooltip 里声称「00:00-13:53 共 5000 次」,
      // 而真实是全天 8640 次。**一个数了一半却报出确定数字的结果,比不报还糟。**
      const cnt = sp.truncated ? `至少 ${sp.count} 次(展开撞上上限,没数完)` : `共 ${sp.count} 次`;
      return `<i class="tlspan${sp.truncated?" trunc":""}" style="left:${tlPos(a)}%;width:${Math.max(tlPos(b)-tlPos(a),0.4)}%" title="${esc(label)} ${sp.from}-${sp.to} 每 ${esc(sp.every)} ${cnt}"></i>`;
    }).join("");
    const acts=(r.actual||[]).filter(a=>vis(mins(a))).map(a=>
      `<i class="tlact" style="left:${tlPos(mins(a))}%" title="${esc(label)} 实际运行 ${a}"></i>`).join("");
    const evt=(r.eventDriven.length&&!r.points.length&&!r.spans.length)
      ? `<span class="tlevt" title="${esc(r.eventDriven.join("/"))} 触发,无固定时刻"
          ><b class="tlbadge">${esc(r.eventDriven.join("/"))}</b></span>`:"";
    // 认不出的触发器类型。后端一直在收集它,而这一行以前**整个不渲染**,
    // 连带那些只有认不出的触发器的任务在今日时间轴上一行都没有 ——
    // 屏幕表现与「这个任务今天本来就不该跑」逐像素相同。
    // 说「我不认识」比装作没有强:前者能被人去查,后者不能。
    const unk=(r.unknownTriggers&&r.unknownTriggers.length&&!r.points.length&&!r.spans.length)
      ? `<span class="tlevt warnish" title="触发器类型 ${esc(r.unknownTriggers.join("/"))} 认不出,今日时刻算不出来"
          ><b class="tlbadge">? ${esc(r.unknownTriggers.join("/"))}</b></span>`:"";
    const nowBar=vis(nowM)?`<i class="tlnow" style="left:${tlPos(nowM)}%"></i>`:"";
    return `<div class="tlrow" data-task="${esc(r.name)}"><div class="tlname" title="${esc(nameTip)}">${esc(label)}</div>
      <div class="tltrack">${spans}${marks}${acts}${evt}${unk}${nowBar}</div></div>`;
  }).join("");
  $("tl").innerHTML=`<div class="hours">${tlTicks()}</div>${rows}`;
}
function tlZoom(factor, anchorPct){
  const span=tlTo-tlFrom;
  let ns=Math.min(1440, Math.max(TL_MIN_SPAN, span*factor));
  const anchorM=tlFrom+span*anchorPct;         // 以指针所在时刻为锚,缩放后它仍在指针下
  let nf=anchorM-ns*anchorPct;
  if(nf<0) nf=0;
  if(nf+ns>1440) nf=1440-ns;
  tlFrom=nf; tlTo=nf+ns;
  scheduleTL();
}
function tlPan(dxPct){
  const span=tlTo-tlFrom;
  let nf=tlFrom-span*dxPct;
  nf=Math.max(0,Math.min(1440-span,nf));
  tlFrom=nf; tlTo=nf+span;
  scheduleTL();
}
function tlReset(){ tlFrom=0; tlTo=1440; scheduleTL(); }

// 维护面板。单独一次 fetch:它要跑 `claude plugin list`,几秒起步,不该拖住主表。
// 相对时间走全页统一的 fmtTime(「5 分钟后」「2 小时后」),任务开关、运行详情、同步与备份一个说法。
// 一分钟以内的将来说「即将运行」:「0 分后」读起来像出错了。
function relTime(s){
  const t=timeValue(String(s).replace(" ","T"));
  if(!Number.isFinite(t)) return String(s).slice(5);   // 解析不了就照实回显原文,不编一个数出来
  return fmtTime(t,{soon:"即将运行"});
}

function pc(v){ if(v==null) return `<td class="num u" title="未检查">-</td>`;
  const c=v>=95?"var(--ok)":v>=80?"var(--warn)":"var(--bad)";
  return `<td class="num"><span class="pcb"><i style="width:${
    Math.min(100,Math.max(0,v))}%;background:${c}"></i></span><span
    style="color:${c}">${v.toFixed(1)}</span></td>`; }
function renderScores(){
  $("scores").innerHTML=`<thead><tr><th>大类</th><th class="num">任务数</th><th class="num">检查通过率</th>
    <th class="num">备份%</th><th class="num">监控%</th><th class="num" title="本类里「没有任何 warn 项」的任务占比。注意它和上面那个按钮说的
「卫生六列」不是一回事:那六列包含备份与监控,而这个百分比不看这两项,却多算了两条电池规则。
一个大类可能六列全是 N 而这里是 100.0">无警告项%</th></tr></thead><tbody>`
    // 每一行挂上它自己的大类。之前这张表的格子有 cursor:pointer 却没有任何处理器,
    // 那是最糟的一种:它主动告诉你可以点,然后什么也不做。
    // 健康% 那一格单独出:它的分母和同一行的「数」不是一回事,而四列长得一模一样。
    // 分母不足时用未检查色阶,和 pc() 的 null 分支一致 : 一个 1 个样本的 100%
    // 和一个 40 个样本的 100% 不该长得一样。
    +(DATA.scores||[]).map(s=>{
      const hn = (s.healthN != null) ? s.healthN : s.n;
      const thin = hn < s.n;
      const hcell = (s.health == null)
        ? `<td class="num u" title="没有任何任务有观察记录">-</td>`
        : `<td class="num" title="${hn}/${s.n} 个任务有观察记录"${
            thin ? ' style="color:var(--faint)"' : ""}>${s.health}</td>`;
      return `<tr data-scat="${esc(s.cat)}" title="只看这一类"><td>${esc(s.cat)}</td>`
        + `<td class="num">${s.n}</td>${hcell}${pc(s.backup)}${pc(s.watched)}${pc(s.hygiene)}</tr>`;
    }).join("")
    +`</tbody>`;
}

const C=[
 // 原来这里是一个 14px 宽、显示 * 的格子:它让人以为点它能选中,点下去展开的却是明细。
 // 现在每行一个勾选框,表头一个全选框(见 render 里的 selectAllHtml):选择不靠任何按键。
 ["selc","",r=>`<td style="width:20px"><input type="checkbox" class="selbox"
   data-selname="${esc(r.name)}"${sel.has(r.name)?" checked":""}
   aria-label="选中 ${esc(r.name)}"></td>`,()=>0],
 ["name","任务",r=>`<td class="nm">${taskIdentityHtml(r,{toggle:true})}</td>`,r=>taskText(r).title],
 // 和任务开关、同步与备份同一个健康芯片。按这一列排就是按严重程度的档位排,不按芯片上的字排。
 ["sl","状态",r=>`<td>${taskHealthBadge(r)}${taskRepairChip(r.name)}</td>`,r=>taskSeverityRank(r)],
 ["cat","大类",r=>`<td class="dim">${esc(r.cat)}</td>`,r=>r.cat],
 // 健康% 旁边要能看出它是拿什么算出来的。判词表认不出来的那些进 other 桶,
 // 它只进分母不出现在任何地方:监控器换一种措辞之后,每一行会显示 0.0% 而
 // ok/bad/stale 全是 0,同一行里两个数字互相矛盾而没有任何字段说明观察去哪了。
 // 一格顶原来的四格。原来「健康% / 实跑 / 失败 / 陈旧」各占一列,42 行乘 4 列
 // 是 168 个数字,其中绝大多数是 100 / 0 / -,而真正要回答的问题只有一个:
 // 这一行有没有事。于是健康率画成一条按 ok/失败/陈旧 分段的微型条,数字留在旁边;
 // 失败、陈旧、实跑三列的精确值搬进展开行(见 detail 里的「观察」一条)。
 //
 // ⚠ 三种「不能长得一样」的状态在条上各有各的形状,不是各有各的颜色:
 //   没有观察记录 -> 虚线空框(不是一条 0 宽的条,那会读成「全坏」)
 //   样本不足     -> 整条降饱和 + 虚线外框
 //   判词认不出   -> 分母里留一段**背景色缺口**,把原来只在 tooltip 里的那个缺陷画出来
 ["health","检查结果",r=>{
   const h=r.hist||{}, v=h.health;
   const oth=h.other||0;
   const tip = v==null ? "没有观察记录"
     : `${h.ok}/${h.judged} 条观察判为正常`
       + (h.bad?` · 失败 ${h.bad}`:"") + (h.stale?` · 陈旧 ${h.stale}`:"")
       + (oth ? ` · ${oth} 条判词认不出来,它们只进了分母` : "");
   // 小样本降色。紧挨着的两处都做了这件事(动作成功率 在 j<5 时降 faint,大类评分表在
   // healthN<n 时降色),唯独这一列没有:**一个 1 条观察的 100% 和一个 4000 条观察的
   // 100% 在屏幕上长得完全一样**,而分母只在 hover 的 tooltip 里。
   // 一个刚被监控器纳入、只轮询到一次的任务因此显示满格绿色。
   const thin = h.judged != null && h.judged < 5;
   const col = v==null?"var(--faint)"
     :thin?"var(--faint)"
     :oth&&oth>=h.judged*0.5?"var(--faint)"
     :v>=95?"var(--ok)":v>=80?"var(--warn)":"var(--bad)";
   // 样本太少时把分母印出来。降色只说「别太当真」,印出 n 才说清为什么。
   const suffix = oth ? "*" : (thin && v != null ? `<span class="m"> n=${h.judged}</span>` : "");
   if(v==null)
     return `<td class="num" title="${esc(tip)}"><span class="vit unk"></span><span
       class="u">-</span></td>`;
   // 分母用 judged,不是 ok+bad+stale:差出来的那一段就是「判词认不出」的那些,
   // 它们留成背景色的缺口 —— 那个缺陷原来只活在 tooltip 里。
   const tot=Math.max(h.judged||1,1), w=n=>(100*(n||0)/tot).toFixed(1)+"%";
   return `<td class="num" title="${esc(tip)}"><span class="vit${thin?" thin":""}"
       ><i class="k-ok" style="width:${w(h.ok)}"></i
       ><i class="k-bad" style="width:${w(h.bad)}"></i
       ><i class="k-st" style="width:${w(h.stale)}"></i></span><span
       style="color:${col}">${v}${suffix}</span></td>`;
 },r=>(r.hist&&r.hist.health!=null)?r.hist.health:-1],
 // 分母要说出来。judged 是动作返回码事件数,不是旁边那列的「实跑」(启动事件数):
 // 多动作任务每次运行写多条 201,分母大于实跑;rc 事件被日志滚动截断的任务分母小于实跑。
 // 一个 1 个样本的 100% 和一个 4000 个样本的 100% 不该长得一样。
 ["realOk","动作成功率",r=>{
   const R2=r.runs||{}, v=R2.successRate, j=R2.judged, st=R2.starts;
   const thin = (j != null && j < 5) || (st != null && j != null && j < st * 0.5);
   const tip = v==null ? "运行日志里还没有这个任务的动作返回码"
     : `${R2.good}/${j} 个动作返回码判为成功` + (st!=null?` · 启动 ${st} 次`:"");
   const col = v==null||thin ? "var(--faint)" : v>=95?"var(--ok)":"var(--bad)";
   return `<td class="num" title="${esc(tip)}" style="color:${col}">${v==null?"-":v}</td>`;
 },r=>(r.runs&&r.runs.successRate!=null)?r.runs.successRate:-1],
 // 实跑 / 失败 / 陈旧 三列已经并进「体征」那一格的分段条,精确值在展开行的「观察」一条。
 // 它们留在表上时是三列几乎全 0 的数字,而三个标签每次都一样。
 ["triggers","运行计划",r=>`<td class="dim" title="${esc(taskSchedule(r,true))}">${esc(taskSchedule(r))}</td>`,r=>r.triggers||""],
 // 84 个等宽时间戳(42 行两列),而读它们的目的基本只有「多久没跑了」「还有多久跑」。
 // 相对时间直接回答那个问题,绝对时刻进 title,一个都没丢。
 // ⚠ 「从未」和「-」保持两个不同的词:前者是确定没跑过,后者是没有下次计划。
 // 排序键仍取原始字符串,排序结果与改之前逐行一致。
 ["lastRun","上次",r=>`<td class="dim num" data-col="lastRun" title="${esc(r.lastRun||"从未")}">${
    r.lastRun?esc(relTime(r.lastRun)):'<span class="u">从未</span>'}</td>`,r=>r.lastRun||""],
 // 停用任务的排序键取空串:它不会运行,不该按计划程序推算出的那个时间排进「即将运行」的前面。
 ["nextRun","下次",r=>`<td class="dim num" data-col="nextRun" title="${esc(taskNextRun(r))}">${esc(taskNextRun(r,true))}</td>`,r=>r.state==="Disabled"?"":(r.nextRun||"")],
 ["ops","操作",r=>`<td class="ops">${taskActionButtons(r)}</td>`,r=>r.state],
 ["catchup","补跑",r=>`<td class="${r.catchup?"y":"n"}">${r.catchup?"是":"否"}</td>`,r=>r.catchup?1:0],
 ["retries","重试",r=>`<td class="num">${r.retries}</td>`,r=>r.retries],
 ["timeout","超时限制",r=>{const i=(r.timeout==="PT72H"||r.timeout==="PT0S");return `<td data-col="timeout" title="${esc(r.timeout)}" style="color:${i?"var(--warn)":"var(--dim)"}">${esc(r.timeout==='PT0S'?'不限时':taskDuration(r.timeout))}</td>`},r=>r.timeout||""],
 ["artifact","产物",r=>`<td class="${r.artifact?(r.cannotProve?"u":"y"):"u"}" title="${esc(r.artifact||"未声明")}">${r.artifact?(r.cannotProve?"不足以验证":"已设置"):"-"}</td>`,r=>r.artifact?(r.cannotProve?1:2):0],
 ["inAllow","备份",r=>{const u=!DATA.summary.allowChecked;return `<td class="${u?"u":r.inAllow?"y":"n"}">${u?"?":r.inAllow?"是":"否"}</td>`},r=>r.inAllow?1:0],
 ["inHealth","监控",r=>`<td class="${r.inHealth?"y":r.elsewhere?"u":"n"}" title="${esc(r.elsewhere||"")}">${r.inHealth?"是":r.elsewhere?"另有监控":"否"}</td>`,r=>r.inHealth?1:0],
];

// 明细一打开先说为什么出事:失败或有问题时,第一块是一条色条「上次运行 · 退出码 · 第一条问题」,
// 旁边就是修复和运行一次。以前原因列表排在最末,在命令和身份那一大段下面,读完还得回到行上去找按钮。
// 这两个按钮和行上的不是同一套的残缺副本:它们只为眼前这条原因服务,所以只放这两个。
function taskAlertHtml(r){
  const issues=r.issues || [], failing=r.sk==='bad';
  if(!failing && !issues.length) return '';
  const tone=failing || taskIssueLevel(r)==='bad'?'bad':'warn';
  const parts=[];
  if(r.lastRun) parts.push(`<span title="${esc(fullTime(String(r.lastRun).replace(' ','T')) || r.lastRun)}">上次运行 ${esc(relTime(r.lastRun))}</span>`);
  else if(failing) parts.push('从未成功运行');
  if(failing){
    const rc=taskExitCode(r);
    parts.push(`<span title="${esc('上次运行结果 '+(rc.hex || '未知'))}">${esc(taskExitCodeText(rc))}</span>`);
  }
  const first=issues.find(i=>i[0]==='bad') || issues[0];
  if(first) parts.push(esc(first[1]));
  const rest=issues.filter(i=>i!==first);
  const repair=`<button type="button" class="mini" data-task-repair="${esc(r.name)}" data-label="修复" ${ConsoleActions.readOnly?'disabled':''} title="${taskControlTitle('修复',ConsoleActions.readOnly?ConsoleActions.reason:'','修复：查看任务事实，开一张工单交给 Agent 诊断；不会改动任务本身')}"><svg class="ic" aria-hidden="true"><use href="#i-repair"/></svg>修复</button>`;
  return `<div class="task-alert ${tone}" role="note"><p><span class="status-symbol" aria-hidden="true">${tone==='bad'?'×':'!'}</span>${parts.join(' · ')}</p>`+
    `<span class="task-alert-actions">${repair}${taskVerbButton(r,'run','运行一次',{extraReason:taskRunReason(r)})}</span></div>`+
    (rest.length?`<ul class="iss">${rest.map(i=>`<li class="${esc(i[0])}">${esc(i[1])}</li>`).join("")}</ul>`:"");
}
// 明细的正文,运行详情的表格行和任务开关的就地展开共用。close 是收起按钮的属性:
// 表格里是 data-detail-close(events.js 接),任务开关里是那一行自己的 data-task-peek(再点一次就是收起)。
function taskDetailBody(r,{close='data-detail-close'}={}){
  // 返回码按「码 ×次数」写,码是十进制:原来写成「0x7755」,读起来像一个十六进制错误码。
  const rcs=r.runs?Object.keys(r.runs.rcs||{}).map(k=>`${k} ×${r.runs.rcs[k]}`).join(" · "):"";
  const rc=taskExitCode(r);
  const facts=[
   ["运行计划",esc(taskSchedule(r,true))],
   ["退出码",rc.hex?`<span title="${esc(rc.hex)}">${esc(taskExitCodeText(rc).replace(/^退出码 /,''))}</span>${r.okCodes?` <span class="faint">声明 ${esc(r.okCodes)} 也算正常</span>`:""}`:'<span class="u">未读取</span>'],
   ["产物",r.artifact?`${esc(r.artifact)} · ${esc(r.artifactMax)}h`:"未声明"],
   // 「体征」那一格只画比例,精确值在这里。少了这一条,失败与陈旧的具体条数
   // 就只剩 tooltip 一个出口 —— 而 tooltip 是发现不了的。
   ["观察",r.hist?`正常 ${r.hist.ok} · 失败 ${r.hist.bad} · 陈旧 ${r.hist.stale}`
     +(r.hist.other?` · 判词认不出 ${r.hist.other}`:"")
     +` / 共 ${r.hist.judged} 条`:'<span class="u">没有观察记录</span>'],
   ["真实运行",r.runs?`启动 ${r.runs.starts} · 完成 ${r.runs.done} · 被终止 ${r.runs.killed} · 超时 ${r.runs.timedOut} · 启动失败 ${r.runs.failStart} · 返回码 ${rcs||"无"}${r.runs.okApplied?` (声明 ${r.runs.okApplied.join(",")} 也算成功)`:""}`:'<span class="u">运行日志里还没有记录</span>'],
  ];
  // 命令、工作目录、身份、电池是排查时才看的,放到最后一块「技术细节」。
  const technical=[
   ["命令",`<code>${esc(r.exec)} ${esc(r.args)}</code>`],
   ["工作目录",esc(r.cwd || '未设置')],
   ["身份",`${esc(r.userId)} · ${esc(r.runLevel)} · ${esc(r.multi)}`],
   ["电池",`${r.refuseOnBattery?"用电池时拒绝启动":"电池可启动"} · ${r.stopOnBattery?"拔电源时停止":"拔电源后继续运行"}`],
  ];
  const list=rows=>rows.map(x=>`<dt>${x[0]}</dt><dd>${x[1]}</dd>`).join("");
  // 明细有十几行高,原来只能回头找到那一行再点一次才收得起来。收起按钮钉在明细右上角。
  return `<div class="det-bar"><button type="button" class="mini det-close" ${close} title="收起明细"><svg class="ic" aria-hidden="true"><use href="#i-up"/></svg>收起</button></div>${taskAlertHtml(r)}${taskInfoHtml(r)}<dl class="task-technical-fields">${list(facts)}</dl><h4 class="task-technical-heading">技术细节</h4><dl class="task-technical-fields">${list(technical)}</dl>`;
}
function detail(r){
  return `<tr class="det"><td colspan="${shownCols().length}"><div class="det">${taskDetailBody(r)}</div></td></tr>`;
}

// 行内控件按「是哪一种控件 + 属于哪个任务」记下,不记 DOM 节点:整块 innerHTML 重建之后节点全是新的。
// 以前只认 data-act,焦点落在启动方式、修复、删除或修复进度上时,重建一次就掉回整行甚至 body,
// 下一次 Tab 从头来过。三处列表(运行详情、任务开关、同步与备份)共用这一份。
const TASK_ROW_CONTROLS=['data-row-toggle','data-act','data-launch','data-task-repair','data-task-delete','data-repair-order','data-task-menu','data-task-peek'];
function taskControlKey(el){
  const attr=el && el.getAttribute ? TASK_ROW_CONTROLS.find(name=>el.hasAttribute(name)) : null;
  if(!attr) return null;
  return {attr, value:el.getAttribute(attr), name:(el.dataset && el.dataset.name) || el.getAttribute(attr)};
}
function findTaskControl(scope,key){
  if(!key || !scope || !scope.querySelectorAll) return null;
  return [...scope.querySelectorAll(`[${key.attr}]`)].find(el=>el.getAttribute(key.attr)===key.value &&
    ((el.dataset && el.dataset.name) || el.getAttribute(key.attr))===key.name) || null;
}
function restoreTaskControlFocus(scope,key){
  // 菜单先打开,焦点才放得进菜单里的那一项。
  reopenTaskMenu(scope);
  const target=findTaskControl(scope,key);
  if(target) try{ target.focus({preventScroll:true}); }catch(e){}
}
// 「⋯」菜单开着时列表被重画(修复进度轮询、刷新),菜单元素被整个换掉,会一声不响地关上。
// TASK_MENU_OPEN 跟着 toggle 事件记下开着的是哪一个,重画之后再把它打开。
let TASK_MENU_OPEN=null;
// 人自己关上菜单(点外面、Esc、再点一次「⋯」)时,beforetoggle 是同步发的,在这里立刻忘掉它。
// 只等 toggle 不够:toggle 是排队发的,同一下点击引起的重画先到,旧菜单已经被换掉,toggle 那边认不出它,
// 重画就把人刚关上的菜单又打开了。列表重画时菜单被整个移走不发 beforetoggle,那种关闭照旧由重画后打开回来。
function forgetClosedTaskMenu(event){
  const menu=event.target;
  if(event.newState!=='closed' || !menu || !menu.classList || !menu.classList.contains('task-menu')) return;
  if(TASK_MENU_OPEN===menu.id) TASK_MENU_OPEN=null;
}
function reopenTaskMenu(scope){
  if(!TASK_MENU_OPEN || !scope || typeof document.getElementById!=='function') return;
  const menu=document.getElementById(TASK_MENU_OPEN);
  if(!menu || typeof menu.showPopover!=='function' || !scope.contains?.(menu)) return;
  const opener=typeof CSS!=='undefined'?scope.querySelector?.(`[popovertarget="${CSS.escape(menu.id)}"]`):null;
  try{ if(!menu.matches(':popover-open')) menu.showPopover(opener?{source:opener}:undefined); }catch(error){}
}

function render(){
  const S=DATA.summary;
  // 零态改色而不是隐藏。「0 个失败」和「这一项没采到」必须保持可分辨 ——
  // 藏起来之后它们都表现为「顶栏上没有这一段」。
  // 计数取自同一份行和同一个判定,点下去筛出来的行数就等于按钮上的数。
  // 「有警告」数的是带警告的任务个数;条数(一个任务可以有好几条)只放进提示里,而且只数这几个任务的。
  // 后端那个 issues 是全部任务的问题条数,失败的也算在内,写在这里会是「3 个任务带警告，共 20 条」。
  const counts=taskStatusCounts(ROWS);
  const warnIssues=ROWS.filter(r=>taskMatchesStatus(r,'warn')).reduce((n,r)=>n+(r.issues || []).length,0);
  $("s-total").title="";$("s-bad").title="";$("s-off").title="";
  $("s-total").textContent=S.total;
  $("s-bad").textContent=counts.bad; $("s-bad").className=counts.bad?"bad":"zero";
  $("s-iss").textContent=counts.warn; $("s-iss").className=counts.warn?"warn":"zero";
  $("s-iss").title=`${counts.warn} 个任务带警告，共 ${warnIssues} 条`;
  $("s-off").textContent=counts.off; $("s-off").className=counts.off?"":"zero";
  syncTaskStatus(counts);
  renderDataLine();
  renderFresh();
  updateBadges();
  renderTL(); renderHeat(); renderScores();

  const q=$("q").value.trim().toLowerCase(), cat=$("cat").value;
  const hideOff=$("hideoff").checked;
  const candidates=ROWS.filter(r=>(!cat||r.cat===cat)&&!(hideOff&&r.state==="Disabled")
    &&taskMatchesStatus(r)
    &&taskMatches(r,q));
  const verdict=$('task-verdict').value;
  updateTaskVerdictFilter('task-verdict',candidates,verdict);
  // cur 是下标,高亮跟着任务名走。筛选或排序一变,同一个下标会指到另一个任务上,
  // 高亮就跳到一个人没点过的行。所以先记下光标所在的任务,重排之后按名字找回来。
  // 从别处点「查看详情」时 PENDING_CUR 指定要落在哪一行:高亮和展开的明细必须是同一行。
  const curName=PENDING_CUR || (VIEW[cur]&&VIEW[cur].name);
  PENDING_CUR=null;
  VIEW=candidates.filter(r=>taskMatchesVerdict(r,verdict));
  const CC=shownCols();
  // 排序列被「显示列」藏起来时,表还按一列看不见的列排着,表头上却没有任何一处显示方向。
  // 退回默认的按严重程度排,并把 sortKey 一起改过去,让卡片标题旁写的排序和实际顺序一致。
  if(sortKey!==TASK_SORT_DEFAULT && !CC.some(c=>c[0]===sortKey)){ sortKey=TASK_SORT_DEFAULT; asc=true; }
  if(sortKey===TASK_SORT_DEFAULT) VIEW.sort(taskSeverityCompare);
  else{
    const col=C.find(c=>c[0]===sortKey)||C[1];
    VIEW.sort((a,b)=>{const x=col[3](a),y=col[3](b);
      const c=(typeof x==="number"&&typeof y==="number")?x-y:String(x).localeCompare(String(y),"zh");
      return asc?c:-c;});
  }
  saveTaskSort();syncTaskSortState();
  const found=curName?VIEW.findIndex(r=>r.name===curName):-1;
  if(found>=0) cur=found;
  else if(cur>=VIEW.length) cur=Math.max(0,VIEW.length-1);
  $("cnt").textContent=matchCount(VIEW.length,ROWS.length,"个任务")+(sel.size?` · 已选 ${sel.size}`:"");
  // 记下焦点落在哪一行的哪个控件上,重建之后放回去。
  const ae = document.activeElement;
  const aeRow = ae && ae.closest ? ae.closest("#tbl tbody tr[data-name]") : null;
  const keep = aeRow ? {name: aeRow.dataset.name,
                        control: taskControlKey(ae),
                        box: ae.classList && ae.classList.contains("selbox")} : null;
  const keepAll = !!(ae && ae.classList && ae.classList.contains("selall"));
  const pick = selectAllState();
  // 排序是这张表最主要的整理手段,原来只有 click:纯键盘用户完全用不了,
  // 读屏用户既按不动也听不出当前按哪一列排(方向只存在于 ::after,没有 aria-sort 兜底)。
  // selc 那一列的排序键是常量,点它会重排一次却看不出任何变化 :
  // 一个会响应但没有效果的可点区域,比不可点更让人怀疑自己看错了,所以它不给 data-k。
  $('tbl').className='g'+(CC.length>6?' task-table-expanded':'');
  $("tbl").innerHTML=`<thead><tr>${CC.map(c=>{
      const sortable = c[0] !== "selc";
      const cur = sortKey===c[0];
      const aria = !sortable ? "" : ` aria-sort="${cur ? (asc?"ascending":"descending") : "none"}"`;
      const tab = sortable ? ' tabindex="0" role="columnheader"' : "";
      return `<th data-column="${c[0]}"${sortable?` data-k="${c[0]}"`:""}${tab}${aria} class="${cur?"s"+(asc?" a":""):""}">${c[0]==="selc"?selectAllHtml(pick):c[1]}</th>`;
    }).join("")}</tr></thead>`
    // tabindex=-1 让 focusCur() 能把焦点放到从别处跳来的那一行上,aria-selected 让选中态可播报。
    // 每一格带上列名(data-c 是列键,data-label 是列标题):窄屏上表格拆成一张张卡片时,格子靠它们认出自己是哪一列。
    // 展开着的那一行,明细跟着一起画出来:整表重建(切换分区后的重读、定时刷新)不会把它抹掉。
    +`<tbody>${VIEW.map((r,i)=>`<tr data-i="${i}" data-name="${esc(r.name)}" tabindex="-1" aria-selected="${sel.has(r.name)}" class="${i===cur?"cur":""}${sel.has(r.name)?" sel":""}${r.sk==="bad"?" is-failing":""}">${CC.map(c=>c[2](r).replace(/^<td/,`<td data-c="${c[0]}" data-label="${esc(c[1])}"`)).join("")}</tr>${r.name===OPEN_DETAIL?detail(r):""}`).join("")}</tbody>`;
  // 半选态只能用属性设,写不进 HTML。
  const allBox = $("tbl").querySelector ? $("tbl").querySelector("input.selall") : null;
  if(allBox) allBox.indeterminate = pick.some && !pick.all;
  // render() 用 innerHTML 整表重建,焦点持有者被移出文档,activeElement 掉回 body,
  // 下一次 Tab 从页面开头重来。勾选、全选和展开都调 render(),
  // 所以任何一次选择都会打断 Tab 序列,而同一操作用鼠标毫无代价。
  if(keepAll && allBox){ try{ allBox.focus({preventScroll:true}); }catch(e){} }
  if(keep && keep.name){
    const row = document.querySelector(`#tbl tbody tr[data-name="${CSS.escape(keep.name)}"]`);
    if(row){
      const target = keep.control
        ? findTaskControl(row, keep.control) || row
        : (keep.box ? row.querySelector("input.selbox") : row);
      try{ (target||row).focus({preventScroll:true}); }catch(e){}
    }
  }
  reopenTaskMenu($("tbl"));
  renderBulk();
}

// 表头全选框只管当前列表(VIEW)。被筛掉的已选任务不因为点了全选或全不选而变化。
function selectAllState(){
  const picked=VIEW.filter(r=>sel.has(r.name)).length;
  return {all:VIEW.length>0 && picked===VIEW.length, some:picked>0};
}
function selectAllHtml(state){
  return `<input type="checkbox" class="selall" aria-label="全选当前列表"${state.all?" checked":""}${VIEW.length?' title="全选当前列表"':' disabled title="当前列表没有任务"'}>`;
}
function toggleSelectAll(){
  const {all}=selectAllState();
  VIEW.forEach(r=>all?sel.delete(r.name):sel.add(r.name));
  render();
}

// 批量操作条浮在页面底部,所以只在运行详情里出现:在别的分区它的按钮会作用在一批看不见的任务上。
// 选择本身留着,回到运行详情还在。
function renderBulk(){
  const b=$("bulk");
  if(!sel.size || CURVIEW!=="tasks"){ b.hidden=true; return; }
  b.hidden=false;
  b.innerHTML=`<span>已选 <b>${sel.size}</b> 个任务</span>
    <button class="mini" data-bulk="run" title="运行选中任务"><svg class="ic" aria-hidden="true"><use href="#i-play"/></svg>运行</button>
    <button class="mini" data-bulk="enable" title="启用选中任务"><svg class="ic" aria-hidden="true"><use href="#i-on"/></svg>启用</button>
    <button class="mini danger" data-bulk="disable" title="停用选中任务"><svg class="ic" aria-hidden="true"><use href="#i-toggle-off"/></svg>停用</button>
    <button class="mini" data-bulk="clear" title="取消选择"><svg class="ic" aria-hidden="true"><use href="#i-close"/></svg>取消选择</button>`;
}

async function act(names, verb){
  if(busy||!names.length) return;
  if(!ConsoleActions.allowWrite()) return;
  // 停用和删除一样用页面内的确认框:列出任务的中文标题,机器名用小字跟在后面,人认得出是哪几个。
  if(verb==="disable"){
    const items=names.map(n=>{const row=ROWS.find(r=>r.name===n);const title=row?taskText(row).title:n;return {text:title,note:title===n?"":n};});
    const ok=await askConfirm({title:`停用 ${names.length} 个任务`,
      body:"它们将不再按计划启动，直到重新启用。正在运行的任务不会因此停止。",
      items,confirmLabel:"停用",danger:true});
    if(!ok || busy) return;
  }
  busy=true;
  renderAutomations();
  if(typeof renderPipelines==='function') renderPipelines();
  render();
  let ok=0, fail=0;
  for(const n of names){
    try{
      const r=await api("/api/act",{method:"POST",body:JSON.stringify({name:n,verb:verb})});
      if(r.ok){ ok++; if(names.length===1) toast(`${n}：${taskOutcome(r,verb)}`,"ok"); }
      else { fail++; toast(`${n}：${taskOutcome(r,verb)}`,"bad"); }
    }catch(e){ fail++; toast(`${n}:${e.message}`,"bad"); }
  }
  if(names.length>1) toast(`${{run:'运行请求',stop:'停止请求',enable:'启用',disable:'停用'}[verb] || verb}：已受理 ${ok}，失败 ${fail}`, fail?"bad":"ok");
  busy=false;
  await load();
}

// ================= 跨区跳转 ========================================================
// 分区改造引入了一整类静默失效的动作:「点这边、改那边」。灯板上一格的处理器一直都在,
// 它把任务表过滤到那一格 : 可任务表在 tasks 分区里,而你点格子的时候人在 overview,
// 那张表是 display:none 的。过滤真的跑了,只是发生在一块看不见的地方,不报错、不提示。
//
// 所以凡是跨区的动作都必须先把目标分区切出来。下面两个函数是唯一的入口,
// 所有「显示了任务名的地方」都走它们,而不是各自去改一次过滤框。
// 已经在运行详情里(时间轴、热力图上点一个任务)就不再切分区:切换会把页面滚回顶上,
// 而要看的那一行就在下面。
let PENDING_CUR=null;
function focusTask(name){
  if(CURVIEW!=="tasks") showView("tasks", true);
  const q = $("q");
  // A detail link always opens the named task, even with stale filters or selections.
  q.value = name;
  $("cat").value = "";$('task-verdict').value='';TASK_STATUS='';$("hideoff").checked=false;sel.clear();
  // 搜索框里是子串匹配,同名前缀的任务会一起留下。高亮和明细都要落在名字完全相同的那一行:
  // 以前先重画再定位,高亮画在了旧下标指的那一行上,明细却展开在另一行下面。
  const known=ROWS.some(row=>row.name===name);
  if(known){ OPEN_DETAIL=name; PENDING_CUR=name; }
  if (DATA) render();
  if(known && VIEW[cur] && VIEW[cur].name===name) focusCur();
  else toast('没有读到该任务，请刷新运行详情','bad');
}

function focusCategory(cat){
  showView("tasks", true);
  $("q").value = "";
  const sel2 = $("cat");
  sel2.value = (sel2.value === cat) ? "" : cat;
  if (DATA) render();
}

// 展开的是哪个任务,按名字记下来。render() 整表重建会把明细行一起抹掉,而切换分区后的重读、
// 定时刷新都会调 render():以前点「查看详情」打开的明细几秒后就自己收起来了。
let OPEN_DETAIL=null;
// 标题按钮的 aria-expanded 跟着展开的那一行走。开合不重建整表,所以要单独同步。
function syncRowToggles(){
  document.querySelectorAll("#tbl .row-toggle").forEach(b=>b.setAttribute("aria-expanded",String(b.dataset.rowToggle===OPEN_DETAIL)));
}
function openDetail(tr){
  document.querySelectorAll("tr.det").forEach(x=>x.remove());
  const r=VIEW[+tr.dataset.i];
  if(r){ tr.insertAdjacentHTML("afterend", detail(r)); OPEN_DETAIL=r.name; }
  syncRowToggles();
}
function closeDetail(){
  document.querySelectorAll("tr.det").forEach(x=>x.remove());
  OPEN_DETAIL=null;
  syncRowToggles();
}
// 收起按钮和 Esc 共用:收起之后把那一行滚回眼前,焦点放回它的标题按钮,接着 Tab 不用从头来。
function collapseTaskDetail(){
  const name=OPEN_DETAIL;
  closeDetail();
  const row=name && [...document.querySelectorAll("#tbl tbody tr[data-name]")].find(tr=>tr.dataset.name===name);
  if(!row) return name;
  try{ row.scrollIntoView({block:"nearest"}); }catch(e){}
  const toggle=row.querySelector ? row.querySelector(".row-toggle") : null;
  try{ (toggle || row).focus({preventScroll:true}); }catch(e){}
  return name;
}
// 搜索框里按 Enter:只剩一个任务时就地展开它,不动筛选。已经展开着就保持展开。
function openOnlyTask(){
  if(VIEW.length!==1) return false;
  if(OPEN_DETAIL===VIEW[0].name) return true;
  const tr=document.querySelector(`#tbl tbody tr[data-i="0"]`);
  if(!tr || !tr.dataset) return false;
  openDetail(tr);
  return true;
}
function focusCur(){
  const tr=document.querySelector(`#tbl tbody tr[data-i="${cur}"]`);
  if(!tr) return;
  tr.scrollIntoView({block:"nearest"});
  // 真的把焦点放上去,不只是滚过去(从别处的「查看详情」跳来时用)。否则读屏那边始终停在 body,
  // 「我在第几行」这件事只存在于一条视觉上的高亮里。
  try{ tr.focus({preventScroll:true}); }catch(e){ tr.focus(); }
}


// 提交并推送。两步:先拿一份只读计划摊开给人看,确认之后才带着那份清单去执行。
//
// 这是这个台子上唯一一个把东西送出这台机器的动作,所以它刻意**不是一键**:
// 一次误击只会打开一份计划。但确认之后的那一串(add 逐条路径、commit、push、
// 等钩子跑完、把钩子输出原样摊出来)全部由这里完成 —— 要省掉的是那串机械动作,
// 不是那个决定。

// ── 运行详情这一屏自己的挂点 ──
// 从 events.js 搬到这里:每个页面的包只改自己的文件,不必都去挤 events.js。
// events.js 在原来的位置调用它们,挂上的都是元素自己的监听,先后顺序不影响结果。
// 时间轴:滚轮缩放(以指针为中心)、按住拖动平移、双击回到整天
function startTimeline(){
  const tl=$("tl");
  tl.addEventListener("wheel",e=>{
    const track=e.target.closest(".tltrack")||tl.querySelector(".tltrack");
    if(!track) return;
    // 无条件接管滚轮会造出一条**整页宽的滚轮死区**:.tl 有 820px 最小宽度,窄窗口里它
    // 横向铺满整屏、高约 400px,鼠标落进去就滚不动页面,只会把时间轴越缩越小 ——
    // 而人在那一刻想做的多半只是往下看后面的内容。
    // 所以只有明确表达了缩放意图(Ctrl / Shift / 横向滚轮)才接管,平滚一律交回页面。
    // 图例里写了这句提示:一个只有作者知道的手势等于没有。
    if(!(e.ctrlKey||e.metaKey||e.shiftKey||Math.abs(e.deltaX)>Math.abs(e.deltaY))) return;
    e.preventDefault();
    const rect=track.getBoundingClientRect();
    const pct=Math.min(1,Math.max(0,(e.clientX-rect.left)/rect.width));
    const d = e.deltaY || e.deltaX;
    tlZoom(d>0?1.25:0.8, pct);   // tlZoom 内部走 scheduleTL,一帧只渲染一次
  },{passive:false});
  // 这里是拖动真正的 bug:原来在 .tltrack 上 setPointerCapture,而 renderTL() 第一次平移
  // 就把那个元素连同整个 innerHTML 换掉了。捕获目标一消失,后续 pointermove 就不再送到
  // 这个监听器,拖动于是走走停停。捕获必须放在渲染不会替换的元素上,也就是 #tl 本身。
  // (2026-09-02 实测:渲染成本 2.1ms 中位数,从来不是瓶颈,我之前的诊断错了。)
  let dragging=false, lastX=0, w=1, down=null;
  tl.addEventListener("pointerdown",e=>{
    const track=e.target.closest(".tltrack");
    if(!track) return;
    const row=track.closest(".tlrow");
    down={x:e.clientX,y:e.clientY,task:row && row.dataset.task};
    dragging=true; lastX=e.clientX;
    w=track.getBoundingClientRect().width || 1;
    tl.classList.add("drag");
    tl.setPointerCapture(e.pointerId);
  });
  tl.addEventListener("pointermove",e=>{
    if(!dragging) return;
    const dx=e.clientX-lastX; lastX=e.clientX;
    tlPan(dx/w);
  });
  const endDrag=e=>{
    if(!dragging) return;
    dragging=false; tl.classList.remove("drag");
    try{ tl.releasePointerCapture(e.pointerId); }catch(_){}
  };
  tl.addEventListener("pointerup",e=>{
    const name=dragging?tlClickTask(down,{x:e.clientX,y:e.clientY}):null;
    endDrag(e);down=null;
    if(name) focusTask(name);
  });
  tl.addEventListener("pointercancel",endDrag);
  tl.addEventListener("dblclick",tlReset);
  $("tlin").addEventListener("click",()=>tlZoom(0.7,0.5));
  $("tlout").addEventListener("click",()=>tlZoom(1.4,0.5));
  $("tlreset").addEventListener("click",tlReset);
}
// 任务开关这一屏的 Esc 和 Enter。键盘约定的表在 navigation.js,这里只往里登记本屏自己的一层:
// Esc 先收起就地展开的明细,Enter 在搜索框里展开唯一剩下的那个任务。原来登记过的那一项(如果有)照旧接在后面。
function registerAutomationKeys(){
  const previous=ESC_STEPS.automations;
  ESC_STEPS.automations=()=>collapseAutomationDetail() || (typeof previous==='function' && previous());
  SEARCH_ENTER['automation-search']=()=>openOnlyAutomation();
}
// 排序状态和「改回按严重程度」放进任务列表的卡片标题行。console.html 不归这一页改,所以由这里补上。
function mountTaskSortState(){
  const header=document.querySelector('#dtbox .card-header');
  if(!header || $('task-sort-state')) return;
  header.insertAdjacentHTML('beforeend','<div class="card-actions task-sort"><span id="task-sort-state" class="faint" role="status"></span>'+
    '<button type="button" class="mini" id="task-sort-severity" title="改回按严重程度排序：失败在前">按严重程度</button></div>');
  $('task-sort-severity').addEventListener('click',()=>{ if(!$('task-sort-severity').disabled) sortTasksBySeverity(); });
  syncTaskSortState();
}
function startTasksPage(){
  $('tasks').querySelector('.task-summary').addEventListener('click',event=>{
    const button=event.target.closest('[data-task-status]');
    if(button && !button.disabled) setTaskStatus(button.dataset.taskStatus);
  });
  syncTaskStatus(null);
  if(!DATA){
    renderTaskCountsPending();
    $('tbl').innerHTML=`<tbody><tr><td>${loadingBlock('计划任务')}</td></tr></tbody>`;
  }
  mountTaskSortState();
  registerAutomationKeys();
  // 任务开关上的「详情」和明细里的「收起」:就地开合,不跳分区。
  $('automation-list').addEventListener('click',event=>{
    const peek=event.target.closest('[data-task-peek]');
    if(peek){ event.preventDefault(); toggleAutomationDetail(peek.dataset.taskPeek); }
  });
  // 「⋯」菜单开合时记下是哪一个,列表重画后 reopenTaskMenu() 照着再打开。toggle 不冒泡,所以在捕获阶段听。
  document.addEventListener('toggle',event=>{
    const menu=event.target;
    if(!menu || !menu.classList || !menu.classList.contains('task-menu')) return;
    if(event.newState==='open') TASK_MENU_OPEN=menu.id;
    else if(TASK_MENU_OPEN===menu.id && document.getElementById(menu.id)===menu) TASK_MENU_OPEN=null;
  },true);
  document.addEventListener('beforetoggle',forgetClosedTaskMenu,true);
  // 运行详情的一整行点了会开合明细(events.js 的 document 监听)。「⋯」和菜单的空白处不能把这一下传上去,
  // 否则开菜单的同时明细也跟着开合。菜单里的动作项不在这里拦:events.js 自己会接走它们并停止传播。
  $('tbl').addEventListener('click',event=>{
    const target=event.target.closest && event.target.closest('.task-more,.task-menu');
    if(target && !event.target.closest('.task-menu [data-launch],.task-menu [data-task-repair],.task-menu [data-task-delete]')) event.stopPropagation();
  });
  // 菜单里的一项点下去:先收起菜单、焦点交回「⋯」,再由 events.js 打开对应的对话框。
  // 捕获阶段先跑,对话框关掉时焦点才回得到一个还看得见的按钮上。
  document.addEventListener('click',event=>{
    const item=event.target.closest && event.target.closest('.task-menu .menu-item');
    if(item && !item.disabled){ TASK_MENU_OPEN=null; closeMenuFor(item); }
  },true);
  renderTaskColumns();
  $('task-column-options').addEventListener('change',event=>{
    const key=event.target.dataset.taskColumn;if(!key) return;
    if(event.target.checked) TASK_COLUMNS.add(key);else TASK_COLUMNS.delete(key);
    try{localStorage.setItem('tc.taskColumns',JSON.stringify([...TASK_COLUMNS]));}catch(e){}
    if(DATA) render();
  });
  // 明细行要贴着可视区左沿,所以它需要知道 .pad 现在多宽。CSS 算不出这个数(td 跨满整张表,
  // 而表可以比容器宽),只能量。用 ResizeObserver 而不是 window.resize:侧栏折叠、
  // 纵向滚动条出现/消失都会改变可视宽度,而这两件事都不触发 window.resize ——
  // 一个只听 resize 的版本在最常见的那两种情况下会安静地用一个过期的数。
  (function(){
    const pad = document.querySelector("#dtbox .pad");
    if(!pad) return;
    // 宽度为 0 只有一个含义:这一屏当前是隐藏的(section[hidden] 走 display:none)。
    // 把 0 写进去会让明细行的 width:var(--padw) 变成零宽 —— 一个「量不到」被当成一个值用。
    // 隐藏时什么都不写,保留上一次的好值;分区一显示,观察器立刻带着真实宽度再触发一次。
    const set = () => { const w = pad.clientWidth; if(w > 0) pad.style.setProperty("--padw", w + "px"); };
    set();
    // 没有 ResizeObserver 就退回 window.resize。少量场景会用到过期的宽度,
    // 但 --padw 缺席时 CSS 回落到 100%,也就是改动前的行为,不会塌。
    if(window.ResizeObserver) new ResizeObserver(set).observe(pad);
    else window.addEventListener("resize", set);
  })();
  $("q").addEventListener("input",()=>{ if(DATA) render(); });
  ["cat","hideoff","task-verdict"].forEach(id=>$(id).addEventListener("change",()=>{ if(DATA) render(); }));
}
