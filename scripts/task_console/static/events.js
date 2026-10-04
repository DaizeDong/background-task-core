// Classic script module; loaded in app.js dependency order.
// 下钻的点击在捕获阶段先记下「从哪一屏点的」(navigation.js 的 markDrill),紧接着的 showView 取走它。
// 捕获阶段是因为真正跳转的处理器散在好几个模块里,而且有的会 stopImmediatePropagation。
// 这一下没有跳成(比如点的是行里的删除按钮),下一个任务开头就清掉,不会算到之后无关的那次切换头上。
document.addEventListener("click", e=>{ if(markDrill(e.target)) setTimeout(()=>{ DRILL_FROM=null; },0); }, true);
document.addEventListener("click", e=>{
  const target=e.target.closest('button') || e.target;
  const launch=e.target.closest('[data-launch]');
  if(launch){e.preventDefault();e.stopImmediatePropagation();openTaskLaunch(launch.dataset.launch);return;}
  const deletion=e.target.closest('[data-delete]');
  if(deletion){e.preventDefault();e.stopImmediatePropagation();if(!deletion.disabled) previewDeletion(deletion.dataset);return;}
  // 任务行上的修复、删除和修复进度。这三样长在运行详情的行里,而那一行本身点了会展开明细
  // (下面第二个监听的最后一段):不在这里先接走,点「删除」会同时把明细展开或收起。
  const repairOrder=e.target.closest('[data-repair-order]');
  if(repairOrder){e.preventDefault();e.stopImmediatePropagation();openRepairOrder(repairOrder.dataset.repairOrder);return;}
  const taskRepair=e.target.closest('[data-task-repair]');
  if(taskRepair){e.preventDefault();e.stopImmediatePropagation();if(!taskRepair.disabled) openTaskRepair(taskRepair.dataset.taskRepair);return;}
  const taskDelete=e.target.closest('[data-task-delete]');
  if(taskDelete){e.preventDefault();e.stopImmediatePropagation();if(!taskDelete.disabled) openTaskDelete(taskDelete.dataset.taskDelete);return;}
  const reset=e.target.closest('[data-reset-filters]');
  if(reset){
    const scope=reset.dataset.resetFilters;resetFilters(scope);
    $(RESET_FILTER_SEARCH[scope])?.focus?.({preventScroll:true});return;
  }
  // ⚠ 这一段必须留在**这个**监听里,而且在最前面。
  // 页面上有两个 document 级的 click 监听,先注册的先跑,而 stopPropagation
  // 拦不住同一个元素上的另一个监听(那要 stopImmediatePropagation)。
  // 这几个按钮长在带 data-task / data-goto 的行里:放进另一个监听时,
  // 实测点「跑一次」会先被下面那条跳转接走 —— 任务确实跑了,但人被弹到了另一屏,
  // 看起来像按钮点错了地方。
  const fx = e.target.closest("[data-fix]");
  if(fx){
    const a = fx.dataset.arg;
    if(fx.dataset.fix === "commitpush"){ showView("repos"); RP_SEL = a; renderRepoList();
                                         repoCommitPush(a); }
    // ⚠ act 的签名是 (names, verb),两个都是位置参数而且顺序反直觉。
    // 写成 act("run", a) 会把动词当成任务名去跑,而那种调用不报错,只会静默什么都不做。
    else if(fx.dataset.fix === "run"){ act([a], "run"); }
    return;
  }
  if(target.id==="cxall"){
    (CXL&&CXL.items||[]).forEach(i=>CXSEL.add(i.rel)); renderCxList(); return; }
  if(target.id==="cxnone"){ CXSEL.clear(); renderCxList(); return; }
  if(target.id==="cxdel"){ cxDelete(); return; }
  const cx = e.target.closest("[data-cxrel]");
  if(cx){ const k=cx.dataset.cxrel;
    // ⚠ 只改这一行,不重画整张表。2382 行重画一次要几十毫秒,而且会把滚动位置
    // 弹回顶部 —— 在一张两千行的清单上挑东西时,那等于每勾一个就把人送回开头。
    // (实测还有一个更隐蔽的后果:重画会把已有的行节点全部换掉,
    // 于是任何「先取一批节点再逐个点」的用法只有第一次生效。)
    if(CXSEL.has(k)) CXSEL.delete(k); else CXSEL.add(k);
    cx.classList.toggle("on", CXSEL.has(k));
    const box=cx.querySelector("input"); if(box) box.checked=CXSEL.has(k);
    cxSelSummary(); return; }
  // 调用屏。和上面那几段同理,这些挂点必须留在**先注册的**这个监听里:
  // 明细表的行同时是可点开的,而下面那个监听里有若干 closest 会先把点击接走。
  // 明细里的「收起」。明细行本身不带 data-name / data-i,不会被下面的行展开处理器接走,
  // 但放在这里和其余调用屏挂点一起,读的人不用去两个监听里找。
  if(e.target.closest("[data-detail-close]")){ collapseTaskDetail(); return; }
  if(e.target.closest("[data-call-close]")){ closeCall(); return; }
  const mv = e.target.closest("[data-mv]");
  if(mv){ lcMove(Number(mv.dataset.i), mv.dataset.mv === "up" ? -1 : 1); return; }
  if(target.id === "lmprev"){ LMQ.offset = Math.max(0, LMQ.offset - LMQ.limit); loadCalls(); return; }
  if(target.id === "lmnext"){ LMQ.offset += LMQ.limit; loadCalls(); return; }
  const lr = e.target.closest(".l-calls tr.lrow");
  if(lr){ openCall(Number(lr.dataset.i)); return; }
  const jp = e.target.closest("[data-jump]");
  if(jp){ jumpToCalls(Number(jp.dataset.jump)); return; }

  const fa = e.target.closest("[data-fixall]");
  if(fa){
    const names = fa.dataset.args.split(String.fromCharCode(1)).filter(Boolean);
    if(fa.dataset.fixall === "commitpush") repoCommitPushBatch(names);
    return;
  }
  const tk = e.target.closest("[data-task]");
  if(tk){ focusTask(tk.dataset.task); return; }
  const tl = e.target.closest("[data-goto]");
  if(tl){
    const g = tl.dataset.goto;
    // 以 # 开头的目标是「滚到本分区里的某一块」,不是切分区。
    if(g.charAt(0) === "#"){
      const el2 = document.querySelector(g);
      if(el2) el2.scrollIntoView({block:"start", behavior:"smooth"});
      return;
    }
    showView(g, true); return;
  }
  const nv = e.target.closest(".nv");
  // 分区项现在是 <a href="#xxx">。让浏览器自己跳会同时触发 hashchange,
  // 于是 showView 跑两遍;更糟的是原生跳转会把页面滚到那个 id 上(并不存在)。
  // 回到这个分组上次停留的标签;从侧栏进来不算下钻,不带「← 返回」。
  if(nv){ if(e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return; e.preventDefault(); showView(groupEntry(nv.dataset.view), true, {reset:true}); return; }
});
// 方向键在侧栏项之间移焦点,但**不切视图**。APG 把 tab 模式拆成 manual 和 automatic
// 两种,面板要发请求的场景必须选 manual:仓库、配置、会话三个分区各自会真扫一遍,
// 会话那一屏首扫一千多份转录要好几秒。焦点扫一遍就打四次真实扫描,那不是导航,是压测。
document.getElementById("side").addEventListener("keydown", e=>{
  const navs = [...document.querySelectorAll("#side .nv")];
  const i = navs.indexOf(document.activeElement);
  if(i < 0) return;
  let j = -1;
  if(e.key==="ArrowDown"||e.key==="ArrowRight") j = (i+1) % navs.length;
  else if(e.key==="ArrowUp"||e.key==="ArrowLeft") j = (i-1+navs.length) % navs.length;
  else if(e.key==="Home") j = 0;
  else if(e.key==="End") j = navs.length-1;
  else return;
  e.preventDefault();
  // 必须停止冒泡。上面那段注释说「方向键只移焦点不切视图」,理由是三个分区各自会真扫一遍;
  // 方向键在侧栏里已经有了意思,文档级的处理器不该再看到同一次按键。
  e.stopPropagation();
  // 旧项要收回 -1。只设新项的话,方向键走过几次之后侧栏在 Tab 序列里就占了好几格 ——
  // 而 roving tabindex 的全部意义就是「整条侧栏只占一格」。
  // 这类退化不会报错,只会让 Tab 键越按越啰嗦,而没有人会把那件事和方向键联系起来。
  navs.forEach((n, k) => { n.tabIndex = (k === j) ? 0 : -1; });
  navs[j].focus();
});
// 表头用 Enter / 空格排序。它们是 th 不是 button,所以要自己接;
// 不接的话这张表的排序对纯键盘用户完全不存在。
document.addEventListener("keydown", e=>{
  if(e.key !== "Enter" && e.key !== " ") return;
  // 概览那几类格子是 div/i,不是 button:给了 tabindex 就必须自己接 Enter 和空格,
  // 否则它们「看起来能聚焦」却按不动,比不可聚焦更让人困惑。
  //
  // ⚠ 这里原来还列着 [data-scat] / [data-cvfile] / .cv-gh / .tlrow[data-task] 四类,
  // **而它们一个都聚焦不到**:全文件只有 .fr-c[data-fr] 在渲染时给了 tabindex="0",
  // 那四类既没有 tabindex 也不是原生可聚焦元素,于是这四条分支永远不会被走到。
  // 一段覆盖了五类、其中四类是死的处理器,读起来像「键盘可达性已经做过了」——
  // 而真相是只做了五分之一,剩下四类连一个 tab 停靠点都没有。
  //
  // 不给那四类补 tabindex,是因为代价是几百个 tab 停靠点(表格每行、会话每行、
  // 时间轴每行),侧栏当初正是为此改成 roving tabindex。
  // 任务表的行另有出路:每行的标题是一个真按钮(.row-toggle),Tab 停得上去,Enter 由浏览器自己接。
  // 所以这里只留真的能聚焦的那一类,并由 test_page_js.py 钉住这条对应关系。
  // [data-jump] 是调用屏里那些「连续降级段」,它们自带 tabindex="0"。
  // 加在这里而不是只给个 tabindex:一个能用 Tab 停上去、按回车却没反应的元素,
  // 比一个根本停不上去的元素更糟 —— 前者看起来是可操作的。
  const act = e.target.closest && e.target.closest("[data-fr],[data-jump]");
  if(act){ e.preventDefault(); e.stopPropagation(); act.click(); return; }
  const th = e.target.closest && e.target.closest("#tbl th[data-k]");
  if(!th) return;
  e.preventDefault(); e.stopPropagation();
  th.click();
});
window.addEventListener("hashchange", ()=>showView(location.hash.slice(1), false));

// 概览的关键指标。每一格都是别的分区早就算好的数字,搬到这里只是让人不用先点进去;
// 点一下就跳过去看细节。所以它既是概览也是导航。
// 拿不到的那一格显示「未检查」而不是零 : 一个还没读到的数字和一个真的是零的数字,
// 在一块大字号的板子上长得一模一样,而这块板子正是给人扫一眼用的。
// 纯图标按钮。title 同时当 aria-label,所以不可能造出一个没有可读名字的图标按钮 ——
// 少写一个参数就少一个名字,这种事迟早会发生,于是干脆不给它发生的机会。
// extra 里放 data-* 或 disabled。
// pct / pctWhat 给这一格加一条比例条。
// ⚠ pct 传 null 时**不画轨道**,而不是画一条 0% 的空轨道:一条空轨道会被读成「0%」,
// 那正是这一页到处在防的「没检查和零长得一样」。所以「未检查」那几档一律不传。
// ⚠ 条的含义必须写进 pctWhat 并进 title:一条填充含义和大字不同的条,比没有条更糟。
document.addEventListener("click",e=>{
  // 点灯板上的一格 = 把下面的任务表过滤到它。再点一次同一格就清空过滤。
  const fc=e.target.closest("[data-fr]");
  if(fc){ focusTask(fc.dataset.fr); return; }
  const sc=e.target.closest("[data-scat]");
  if(sc){ focusCategory(sc.dataset.scat); return; }
  // 表头的全选框。和行上的勾选框一样要停止冒泡,而它不在 th[data-k] 里,不会被当成排序。
  const sa=e.target.closest("input.selall");
  if(sa){ e.stopPropagation(); toggleSelectAll(); return; }
  const sb=e.target.closest("input.selbox");
  if(sb){
    // stopPropagation 必须有:不然这一下会冒泡到行,把明细展开一起触发。
    e.stopPropagation();
    const n=sb.dataset.selname;
    sb.checked ? sel.add(n) : sel.delete(n);
    renderBulk(); $("cnt") && render();
    return;
  }
  const mt=e.target.closest("button[data-mt]");
  if(mt){ e.stopPropagation(); maintAct(mt.dataset.mt, mt.dataset.name); return; }
  // 仓库详情里的动作。放在 data-mt 之后,因为 fetch 仍然走那条通用路;
  // 这三个各有各的回显,所以单独接。
  const ra=e.target.closest("button[data-rpact]");
  if(ra){ e.stopPropagation();
          if(ra.dataset.rpact==="commitpush"){ repoCommitPush(RP_SEL); return; }
          repoAct(ra.dataset.rpact); return; }
  // 选中一个仓。整行可点 —— 之前整块面板只有每行末尾一个 fetch 按钮可以点,
  // 而卡片本身看着像能点、实际不能,那种「看起来可点却不可点」比没有交互更糟。
  // 状态条的一段。再点同一段是取消,所以这个过滤永远有路回到「全部」——
  // 一个只能进不能出的过滤器,会让人以为仓库列表里的东西不见了。
  // ⚠ 走 renderRepos() 而不是 renderRepoList():条本身是在 renderRepos() 里拼的,
  // 只重画列表的话选中那一段的高亮永远不会出现 —— 过滤生效了但看不出在按什么过滤。
  const rs=e.target.closest("[data-rpstate]");
  if(rs){ RP_STATE = (RP_STATE===rs.dataset.rpstate) ? "" : rs.dataset.rpstate;
          renderRepos(); return; }
  const rr=e.target.closest("[data-rp]");
  if(rr){ RP_SEL=rr.dataset.rp; renderRepoList(); return; }
  const b=e.target.closest("button[data-act]");
  if(b){ e.stopPropagation(); act([b.dataset.name], b.dataset.act); return; }
  const bl=e.target.closest("button[data-bulk]");
  if(bl){ if(bl.dataset.bulk==="clear"){ sel.clear(); render(); } else act(Array.from(sel), bl.dataset.bulk); return; }
  const th=e.target.closest("#tbl th[data-k]");
  if(th){ if(sortKey===th.dataset.k) asc=!asc; else {sortKey=th.dataset.k; asc=true;} render(); return; }
  const tr=e.target.closest("#tbl tbody tr[data-name]");
  if(tr){
    // 拖选一段文字(复制任务名或说明)松手时也会发一次 click。那一下不是要开合明细:
    // 开合会整表重建,刚选中的文字跟着没了。
    const picked=window.getSelection ? window.getSelection() : null;
    if(picked && String(picked) && (tr.contains(picked.anchorNode) || tr.contains(picked.focusNode))) return;
    cur=+tr.dataset.i; const d=tr.nextElementSibling;
    if(d&&d.classList.contains("det")){ closeDetail(); render(); }
    else { render(); const t2=document.querySelector(`#tbl tbody tr[data-i="${cur}"]`); if(t2) openDetail(t2); } }
});
// 键盘约定只有两条,逻辑都在 navigation.js(那里能被 node:vm 测到):
// Enter 提交搜索框,Esc 退一层。这一页刻意没有单键快捷键。
document.addEventListener("keydown",e=>{
  if(handleSearchEnter(e)) return;
  handleEscape(e);
});

// 时间轴:滚轮缩放(以指针为中心)、按住拖动平移、双击回到整天
(function(){
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
  let dragging=false, lastX=0, w=1;
  tl.addEventListener("pointerdown",e=>{
    const track=e.target.closest(".tltrack");
    if(!track) return;
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
  tl.addEventListener("pointerup",endDrag);
  tl.addEventListener("pointercancel",endDrag);
  tl.addEventListener("dblclick",tlReset);
})();
// 每张卡片自己的刷新按钮都删了:顶栏的刷新会把这一屏的全部读取再跑一遍,同一屏摆两个一样的图标只会让人猜哪个更全。
// 只留会话库的「重新扫描」:它只扫所选的那个库,比整页刷新便宜得多。扫描期间灰着,免得被连点。
$("cxload").addEventListener("click",async()=>{
  const button=$("cxload");setDisabled(button,"正在扫描所选的会话库");
  try{ await loadCxList(); }finally{ setDisabled(button,""); }
});
// 换库要重扫,换排序不用:排序是纯前端的事,重扫一遍几千个文件只为了换个顺序,
// 会让这个下拉用起来像卡住了。
$("cxwhich").addEventListener("change",loadCxList);
$("cxsort").addEventListener("change",()=>{ if(CXL) renderCxList(); });
$("scktog").addEventListener("click",()=>{const open=$("sckd").classList.toggle("on");setIconControl($("scktog"),open?'i-up':'i-down',open?'收起检查':'展开检查');});
// 过滤只改看得见什么,不重新扫描 —— 扫一遍所有仓要一秒多,而每敲一个字符重扫一次
// 既慢又会让选中的那个仓在脚下换位置。
$("rpq").addEventListener("input", renderRepoList);
$("rpacc").addEventListener("change", renderRepoList);
$("rpvis").addEventListener("change", renderRepoList);
$("rpkind").addEventListener("change", renderRepoList);
// 列表行既可点也可用键盘。给了 tabindex 却不接键,那是「看起来能聚焦却按不动」,
// 比不可聚焦更让人困惑 —— 这条在别处已经栽过一次。
$("rplist").addEventListener("keydown", e=>{
  if(e.key!=="Enter" && e.key!==" ") return;
  const row = e.target.closest("[data-rp]");
  if(!row) return;
  e.preventDefault();
  RP_SEL = row.dataset.rp; renderRepoList();
});

// ── 调用屏的挂点 ──
$("lcsave").addEventListener("click", lcSave);
$("lcreset").addEventListener("click", ()=>{ LCDRAFT = null; renderChain(); });
$("lmprov").addEventListener("change", e=>{ LMQ.provider = e.target.value; LMQ.offset = 0; loadCalls(); });
$("lmok").addEventListener("change", e=>{ LMQ.ok = e.target.value; LMQ.offset = 0; loadCalls(); });
$("lmcaller").addEventListener("change", e=>{ LMQ.caller = e.target.value; LMQ.offset = 0; loadCalls(); });
$("lmq").addEventListener("input", e=>callSearchInput(e.target.value));
// 拖拽排序。上下箭头按钮是同一件事的键盘可达版本,两条路都留着:
// 只有拖拽的话,这个控件对键盘用户不存在。
let LCFROM = null;
$("lclist").addEventListener("dragstart", e=>{
  const li = e.target.closest("li[data-i]");
  if(!li) return;
  LCFROM = Number(li.dataset.i);
  li.classList.add("drag");
  e.dataTransfer.effectAllowed = "move";
  // Firefox 不设 data 就不发 drop。值本身没人读。
  try{ e.dataTransfer.setData("text/plain", String(LCFROM)); }catch(err){}
});
$("lclist").addEventListener("dragover", e=>{
  const li = e.target.closest("li[data-i]");
  if(!li || LCFROM == null) return;
  e.preventDefault();
  [...$("lclist").children].forEach(x=>x.classList.toggle("over", x === li));
});
$("lclist").addEventListener("drop", e=>{
  const li = e.target.closest("li[data-i]");
  if(!li || LCFROM == null) return;
  e.preventDefault();
  const to = Number(li.dataset.i);
  const a = (LCDRAFT || ((LLM && LLM.chain && LLM.chain.effective) || [])).slice();
  if(LCFROM !== to && LCFROM < a.length){
    a.splice(to, 0, a.splice(LCFROM, 1)[0]);
    LCDRAFT = a;
  }
  LCFROM = null;
  renderChain();
});
$("lclist").addEventListener("dragend", ()=>{ LCFROM = null; renderChain(); });

// The full call ledger is loaded when the user opens that workspace.
$("sidetoggle").addEventListener("click",()=>{
  const n=$("side").classList.toggle("navbar-folded");
  setIconControl($("sidetoggle"),'i-sidebar',n?'展开侧栏':'收起侧栏');
  try{ localStorage.setItem("tc.narrow", n?"1":"0"); }catch(e){}
});
try{ if(localStorage.getItem("tc.narrow")==="1") $("sidetoggle").click(); }catch(e){}
ConsoleTheme.apply();
// 「⋯」菜单里的外观三选一。菜单留着不关,换完能直接看到效果再决定;点别处或 Esc 收起。
$('theme-select').addEventListener('click',event=>{
  const choice=event.target.closest('[data-theme-choice]');
  if(choice) ConsoleTheme.set(choice.dataset.themeChoice);
});
$('page-refresh').addEventListener('click',refreshPage);
$('page-export').addEventListener('click',()=>{
  try{ $('page-menu').hidePopover(); }catch(error){}
  exportPage();
});
$('review-search').addEventListener('input',event=>{REVIEW_QUERY=event.target.value;renderTodo();});
startConvos();
if(typeof startConversationActions==='function') startConversationActions();
$('runtime-search').addEventListener('input',event=>{RUNTIME_QUERY=event.target.value;renderSkills();renderClientPlugins();});
$('runtime-state').addEventListener('change',event=>{RUNTIME_STATE=event.target.value;renderSkills();renderClientPlugins();});
$('runtime-sort').addEventListener('change',event=>{RUNTIME_SORT=event.target.value;renderSkills();});
$('rpissue').addEventListener('change',event=>{RP_ISSUE=event.target.value;renderRepoList();});
ConsoleActions.start();
startDeletionControls();
startTaskOperations();
$('launch-close').addEventListener('click',()=>$('launch-dialog').close());
$('launch-copy').addEventListener('click',async()=>{
  try{await navigator.clipboard.writeText($('launch-copy').dataset.command);toast('启动命令已复制','ok');}
  catch(error){toast('无法访问剪贴板，请在启动说明中手动复制命令','bad');}
});
startWorkPlatform();
startWorkActions();
if(typeof startIntegrations==='function') startIntegrations();
if(typeof startConvoChain==='function') startConvoChain();
showView(location.hash.slice(1) || VIEWS[0], false);
// 徽章要在人点进各分区之前就有数:当前这一屏读完后,把徽章还没读过的来源补读一遍。
loadPageOnce(CURVIEW).then(prefetchBadgeSources);
// 「N 分钟前刷新」要跟着时间走,半分钟重写一次就够。
setInterval(renderRefreshAge,30000);
$("tlin").addEventListener("click",()=>tlZoom(0.7,0.5));
$("tlout").addEventListener("click",()=>tlZoom(1.4,0.5));
$("tlreset").addEventListener("click",tlReset);
function syncHygBtn(){
  const b=$("hygtog"); if(!b) return;
  setIconControl(b,'i-tools',HYG_OPEN?'收起保障配置':'保障配置');
}
$("hygtog").addEventListener("click",()=>{
  HYG_OPEN = !HYG_OPEN;
  try{ localStorage.setItem("tc.hyg", HYG_OPEN?"1":"0"); }catch(e){}
  syncHygBtn(); if(DATA) render();
});
syncHygBtn();
renderTaskColumns();
$('task-column-options').addEventListener('change',event=>{
  const key=event.target.dataset.taskColumn;if(!key) return;
  if(event.target.checked) TASK_COLUMNS.add(key);else TASK_COLUMNS.delete(key);
  try{localStorage.setItem('tc.taskColumns',JSON.stringify([...TASK_COLUMNS]));}catch(e){}
  if(DATA) render();
});
$('pipeline-task-search').addEventListener('input',renderPipelines);
$('pipeline-verdict').addEventListener('change',renderPipelines);
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
["cat","only","hideoff","task-verdict"].forEach(id=>$(id).addEventListener("change",()=>{ if(DATA) render(); }));

$("review-filter").addEventListener("change",event=>{REVIEW_FILTER=event.target.value;renderTodo();});

document.addEventListener("click",pipelineClick);
document.addEventListener("click",reviewClick);
// 清除筛选按钮的亮灭跟着筛选走。挂在 document 上、而且最后注册:各个筛选控件自己的监听先把
// 状态写好,这里再数;点击也算,因为仓库状态条、来源按钮和跳转链接都是点一下就换了筛选。
["input","change","click"].forEach(type=>document.addEventListener(type,()=>syncResetFilters()));
syncResetFilters();
