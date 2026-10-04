// Classic script module; loaded in app.js dependency order.
async function repoAct(what){
  const r = REPOS && REPOS.repos.find(x=>x.name===RP_SEL);
  if(!r){ toast("没有选中的仓","bad"); return; }
  const out = $("rpout");
  if(what==="copy"){
    // 剪贴板在非安全上下文里不存在。失败时把路径显示出来让人自己选 ——
    // 一个静默失败的复制按钮会让人以为已经复制了,然后粘出上一次的东西。
    if(navigator.clipboard && window.isSecureContext){
      navigator.clipboard.writeText(r.path).then(()=>toast("路径已复制","ok"),
        ()=>{ if(out) out.textContent = r.path; toast("复制不了,路径显示在下面","bad"); });
    } else { if(out) out.textContent = r.path; toast("复制不了,路径显示在下面","bad"); }
    return;
  }
  if(what==="web"){
    // 只开后端确认过的地址。webUrl 为空时按钮本来就是 disabled,这里再挡一次:
    // 两处都挡不是重复,是因为 DOM 可以被改。
    if(!r.webUrl){ toast("认不出这个 remote 的网页地址","bad"); return; }
    window.open(r.webUrl,"_blank","noopener");
    return;
  }
  if(what==="reveal"){
    try{
      const res = await api("/api/maint/act",{method:"POST",
        body:JSON.stringify({action:"repo.reveal", name:r.name})});
      if(res.error){ toast(res.error,"bad"); return; }
      toast("已在资源管理器打开","ok");
    }catch(e){ toast(e.message,"bad"); }
    return;
  }
  if(what==="status"){
    // 有改动的仓详情里本来就摊着改动列表,这个按钮是「再读一次」;干净的仓读到的结果写在结果栏里。
    if(r.dirty>0 && r.state!=="error"){ await rpLoadStatus(r.name, true); return; }
    if(out) out.textContent = "读取中…";
    try{
      // 看改动只是读:标成 inspect,不进「最近操作」,也不把全页的写按钮锁住。
      const res = await api("/api/maint/act",{method:"POST",inspect:true,
        body:JSON.stringify({action:"repo.status", name:r.name})});
      if(res.error){ if(out) out.textContent = res.error; toast(res.error,"bad"); return; }
      if(out) out.textContent = rpStatusText(res);
    }catch(e){ if(out) out.textContent = e.message; toast(e.message,"bad"); }
    return;
  }
}

let REPOS=null;
const RP_LAB={clean:"干净",dirty:"有改动",unpushed:"未推送",detached:"游离头",error:"扫不动"};
const RP_ORDER=["unpushed","error","dirty","detached","clean"];

async function loadRepos(){
  $("rpnote").textContent="扫描中";
  try{ REPOS=await api("/api/repos"); $("rpnote").textContent=""; }
  // 读失败也要让诊断那一屏重画:那里的仓库格子和清单说明要从「读取中」变成「读取失败」。
  catch(e){ $("rpnote").textContent="失败:"+e.message; updateBadges(); return; }
  // 重新扫描之后改动可能已经变了,上一次读到的改动列表作废,选中的仓会按新数据重读一次。
  RP_STATUS.clear();
  renderRepos();
  updateBadges();
}

function renderRepos(){
  if(!REPOS) return;
  if(!REPOS.available){
    $("rphead").innerHTML=`<span style="color:var(--warn)">${esc(REPOS.reason)}</span>`;
    $("rplist").innerHTML=""; $("rpdetail").innerHTML=""; return;
  }
  const S=REPOS.summary, c=S.counts||{};
  // 「要人管 N」这一份删掉了:同一个数字已经在概览的指标格、侧栏徽章和要人管清单上
  // 各出现一次,连中文词都一样。徽章回答「有没有事」、清单回答「是哪几个」,是不同粒度;
  // 夹在中间的这一份信息增量为零,而每多一处就多一个会漂的地方。
  // 四段状态原来是「■未推送 4 · ■有改动 14 · ■干净 30」这样的文字,每次变的只有数字,
  // 而四个数字之间的比例完全靠读。换成一根按数量分段的条:比例是看出来的,
  // 数字印在段上,段本身可以点来过滤。
  // 每一段写着字(「未推送 4」),不再只有一个数字、含义藏在悬停里。段是按钮:Tab 能到,Enter 能按,
  // aria-pressed 说出这一段是不是正在筛。段宽仍大致按数量分,但至少放得下自己那几个字。
  const segs = RP_ORDER.filter(k=>c[k]);
  let head = `<span>共 <b>${S.total}</b></span>`
    + `<span class="rp-bar" role="group" aria-label="按状态筛选">`
    + segs.map(k=>`<button type="button" class="d-${k}${RP_STATE===k?" sel":""}" data-rpstate="${k}"
        aria-pressed="${RP_STATE===k}" style="flex:${c[k]} 1 auto"
        title="${esc(RP_LAB[k])} ${c[k]} 个 · ${RP_STATE===k?"再点一下取消这个筛选":"点一下只看这一类"}"
        ><span>${esc(RP_LAB[k])}</span> <b>${c[k]}</b></button>`).join("")
    + `</span>`;
  if(S.unknownUpstream) head += `<span title="没有 upstream 的仓:它的提交一个都没推出去过,而这和「已同步」在 0/0 下长得一样">无上游 ${S.unknownUpstream}</span>`;
  // 整块面板有多少内容不可信。这不是某一行自己的事,所以它必须出现在汇总里。
  // ⚠ 「从来没 fetch 过」单独说:它和「fetch 过但旧了」要做的事一样,
  // 但一批仓从来没连过远端是另一个量级的事实,合并之后就看不见了。
  if(S.staleBehind) head += `<span style="color:var(--warn)"
      title="这些仓的「落后」来自一份超过 ${S.behindTrustHours} 小时的本地缓存,不作数。先 fetch 再看。${
        S.neverFetched?" 其中 "+S.neverFetched+" 个从来没 fetch 过。":""}"
      >落后未知 ${S.staleBehind}${S.neverFetched?` (${S.neverFetched} 个没 fetch 过)`:""}</span>`;
  // 账号判定的计数。mismatch 单独拎出来,因为它是事故形状;unchecked 也要说,
  // 因为一个只数 mismatch 的计数器在没配身份表时是 0,而 0 读起来像「没有配错的」。
  const ic = S.identityCounts||{};
  if(ic.mismatch) head += `<span style="color:var(--bad)">账号不匹配 ${ic.mismatch}</span>`;
  if(ic.unchecked) head += `<span title="没配身份表,或表读不出来。这不是「都匹配」">账号未检查 ${ic.unchecked}</span>`;
  if(S.identityReason) head += `<span style="color:var(--warn)">${esc(S.identityReason)}</span>`;
  if(S.visibilityReason) head += `<span style="color:var(--warn)">${esc(S.visibilityReason)}</span>`;
  // 可见性三档的计数。⚠ 未知那一档即使是 0 也要打出来:
  // 一个「没有未知」的舰队和一张压根没加载的可见性表,在「不显示这一段」下长得一样。
  {
    const vc={pub:0,priv:0,unk:0};
    REPOS.repos.forEach(r=>{ vc[!r.visibility?"unk":(/pub/i.test(r.visibility)?"pub":"priv")]++; });
    head += `<span title="可见性分档。闸门对未知 remote 是按公开拦的">可见性 ${vc.pub}公开 / ${
      vc.priv}私有 / <b style="color:${vc.unk?"var(--warn)":"var(--faint)"}">${vc.unk}</b>未知</span>`;
  }
  $("rphead").innerHTML = head;

  // 账号下拉的选项从数据里来,不写死。写死一张账号表就是把舰队地图钉进公开仓。
  const accs = Array.from(new Set(REPOS.repos.map(r=>r.owner).filter(Boolean))).sort();
  const accSel = $("rpacc");
  if(accSel.dataset.filled !== String(accs.length)){
    const keep = accSel.value;
    accSel.innerHTML = `<option value="">全部账号</option>`
      + accs.map(a=>`<option value="${esc(a)}">${esc(a)}</option>`).join("");
    accSel.value = keep; accSel.dataset.filled = String(accs.length);
  }
  const kindSel = $("rpkind");
  if(!kindSel.dataset.filled){
    kindSel.innerHTML = `<option value="">全部类型</option>`
      + Object.keys(RP_KIND_LAB).map(k=>`<option value="${k}">${esc(RP_KIND_LAB[k])}</option>`).join("");
    kindSel.dataset.filled = "1";
  }
  // 上次记下的账号和类型要等选项填好才放得回去;放不回去(那个账号这次扫描里没有)就照旧「全部」。
  if(RP_PENDING){
    if(RP_PENDING.acc && accs.includes(RP_PENDING.acc)) accSel.value = RP_PENDING.acc;
    if(RP_PENDING.kind && Object.hasOwn(RP_KIND_LAB, RP_PENDING.kind)) kindSel.value = RP_PENDING.kind;
    RP_PENDING = null;
  }
  // 状态筛选在筛选条里也留一个看得见、点 × 就能去掉的芯片。它带的是同一个 data-rpstate,
  // 点它走状态条那一段的同一个处理:同一段再点一次就是取消。
  const chip = $("rpstate-chip");
  if(chip) chip.innerHTML = RP_STATE
    ? `<button type="button" class="rp-filter-chip" data-rpstate="${esc(RP_STATE)}" title="去掉状态筛选，显示全部仓库">状态：${esc(RP_LAB[RP_STATE]||RP_STATE)} <span aria-hidden="true">×</span></button>`
    : "";
  renderRepoList();
}

const RP_KIND_LAB = {skill:"skill", shared:"共享组件", other:"其它", companion:"伴生配置"};
const RP_KIND_WHY = {
  skill: "有 SKILL.md。伴生的配置仓跟在自己那一行下面。",
  shared: "被别的仓当 submodule 用(闸门、样式这类)。改一处,所有引用它的仓跟着走。",
  other: "工具、库、独立的配置仓。名字像伴生仓但在这次扫描里配不上宿主的,也在这里。",
};
const RP_ID_LAB = {ok:"匹配", mismatch:"不匹配", foreign:"第三方", unchecked:"未检查"};
let RP_SEL = null;          // 选中的仓名
let RP_STATE = "";          // 状态条上点中的那一段,空串=不按状态过滤
let RP_ISSUE = "";
let RP_AUTOSEL = false;     // 首屏自动选中只做一次,之后不再抢人的选择
let RP_PENDING = null;      // 上次记下的账号和类型,等下拉选项填好再放回去
let RP_ROUTED;              // 最近一次写进地址栏和本机存储的选中仓;undefined = 还没写过
let RP_ROUTE_REPLACE = false; // 这次选中的变化不是人点的(自动选中、扫描后原仓不见了),地址栏用替换不加历史
let RP_SHEET = false;       // 手机宽度下详情是否盖在列表上
const RP_STATUS = new Map();  // 仓名 → 自动读到的改动列表 {text} / {error} / {pending}

// 仓库页记住筛选和选中:换页再回来、刷新浏览器都还在。搜索词不记,它是临时的。
// 只是这台浏览器里的一点便利,存不进去(隐私窗口、禁用存储)就照旧从「全部」开始,不报错。
const RP_STORE = "tc.repos.v1";
function rpSaved(){
  try{
    const saved = JSON.parse(localStorage.getItem(RP_STORE) || "null");
    return saved && typeof saved === "object" ? saved : {};
  }catch(_){ return {}; }
}
function rpSave(){
  const value = id=>{ const el=$(id); return el && typeof el.value === "string" ? el.value : ""; };
  try{ localStorage.setItem(RP_STORE, JSON.stringify({acc:value("rpacc"), kind:value("rpkind"), vis:value("rpvis"),
    issue:RP_ISSUE, state:RP_STATE, sel:RP_SEL})); }catch(_){}
}

function rpMatches(r, q, acc, kind, vis){
  if(RP_ISSUE==="identity_mismatch" && r.identity?.state!=="mismatch") return false;
  if(RP_ISSUE==="identity_unchecked" && r.identity?.state!=="unchecked") return false;
  if(RP_ISSUE==="upstream" && r.unpushedKnown!==false) return false;
  if(RP_ISSUE==="behind" && r.behindKnown!==false) return false;
  if(RP_STATE && r.state !== RP_STATE) return false;
  if(acc && r.owner !== acc) return false;
  if(kind && r.kind !== kind) return false;
  if(vis){
    // 三档各自判,不写「不是公开就算私有」那种兜底 ——
    // 可见性问不出来的仓会被那种写法归进私有,而闸门对它们是按公开拦的。
    const vk = !r.visibility ? "unk" : (/pub/i.test(r.visibility) ? "pub" : "priv");
    if(vk !== vis) return false;
  }
  if(!q) return true;
  // 路径和远程地址也算:记得「放在哪个目录」或「推到哪」却记不清仓名的时候,也能搜到。
  const hay = [r.name, r.owner, r.path, r.remote].filter(Boolean).join(" ").toLowerCase();
  return hay.indexOf(q) >= 0;
}

// 要不要提交或推送:有没提交的改动、有没推出去的提交,或者上次推送失败留着待重试。
// 详情里的「提交并推送」和筛选后的批量按钮用同一个判断,两处说法不会对不上。
function rpNeedsPush(r){
  return RP_RETRIES.has(r.name) || (r.dirty||0) > 0 || !!(r.unpushedKnown && (r.ahead||0) > 0);
}

function renderRepoList(){
  if(!REPOS || !REPOS.available) return;

  // 首屏自动选中第一个要人管的仓。原来右边 1000x750 默认全白,只有一句「左边选一个仓」,
  // 而这一页自己的注释早就写过「空出来的那片读起来像坏了」。
  // ⚠ 必须在拼列表 HTML **之前**做,否则选中的那一行不会带上 .on 高亮 ——
  // 右边显示着某个仓,左边没有任何一行看起来被选中,那比不自动选更让人困惑。
  // ⚠ 只做一次:否则人手动取消选择之后,下一次刷新又会把选中抢回去。
  // 要人管的状态集合走 RP_ATT(),不在这里另写一份阈值。
  // 记下来的或地址里带的仓,这次扫描里已经没有了,就当没选,再按下面的规则自动选一次。
  if(RP_SEL && Array.isArray(REPOS.repos) && !REPOS.repos.some(r=>r.name===RP_SEL)){ RP_SEL=null; RP_ROUTE_REPLACE=true; }
  if(!RP_SEL && !RP_AUTOSEL && Array.isArray(REPOS.repos)){
    RP_AUTOSEL = true;
    const att = RP_ATT();
    const first = REPOS.repos.find(r=>att.indexOf(r.state) >= 0);
    if(first){ RP_SEL = first.name; RP_ROUTE_REPLACE = true; }
  }

  const q = ($("rpq").value||"").trim().toLowerCase();
  const acc = $("rpacc").value, kind = $("rpkind").value, vis = $("rpvis").value;
  const byHost = {};
  REPOS.repos.forEach(r=>{ if(r.kind==="companion" && r.companionOf) byHost[r.companionOf]=r; });

  // 过滤时伴生仓跟着宿主走:一个 <宿主>-config 单独出现在列表里,那个从属关系就只剩
  // 名字在说,而名字是会被读漏的。宿主命中则两行都留;伴生自己命中则把宿主一起带出来。
  const hostShown = new Set();
  const tops = REPOS.repos.filter(r=>r.kind!=="companion");
  tops.forEach(r=>{
    const comp = byHost[r.name];
    if(rpMatches(r,q,acc,kind,vis) || (comp && rpMatches(comp,q,acc,kind,vis))) hostShown.add(r.name);
  });

  const order = (REPOS.summary.kindOrder && REPOS.summary.kindOrder.length)
    ? REPOS.summary.kindOrder : ["skill","shared","other"];
  // 命中数和显示数分开数。伴生跟着宿主走这条规则会把**不匹配**的行也带出来
  // (筛「私有」时,一个私有伴生仓会把它的公开宿主一起拉进列表)。
  // 只报显示数会让计数在旁边那句「25 私有」面前自相矛盾,而人会按计数下结论。
  // 每一行的徽章仍然是它自己的真实值,所以列表没有说谎;说谎的只是一个合并的计数。
  // 账号缩写只在当前列表里出现了不止一个账号时才印:全是同一个账号时,它在每一行上都一样,
  // 一列一模一样的字母等于没写。账号不匹配和未检查是告警,不管怎样都照印。
  const listed = [];
  order.forEach(k=>tops.filter(r=>r.kind===k && hostShown.has(r.name)).forEach(r=>{
    listed.push(r); if(byHost[r.name]) listed.push(byHost[r.name]); }));
  const showAcc = new Set(listed.map(r=>r.owner||"")).size > 1;
  let shown = 0, matched = 0, html = "";
  const hits = [];
  order.forEach(k=>{
    const rows = tops.filter(r=>r.kind===k && hostShown.has(r.name));
    if(!rows.length) return;
    html += `<div class="rp-gh"><b>${esc(RP_KIND_LAB[k]||k)}</b><span class="n">${rows.length}</span></div>`;
    rows.forEach(r=>{
      html += rpListRow(r,false,showAcc); shown++;
      if(rpMatches(r,q,acc,kind,vis)){ matched++; hits.push(r); }
      const comp = byHost[r.name];
      if(comp){ html += rpListRow(comp,true,showAcc); shown++;
                if(rpMatches(comp,q,acc,kind,vis)){ matched++; hits.push(comp); } }
    });
  });
  // 按「未推送」或某个问题筛出来之后,这一批往往就是要一起推的那几个:给一个按钮把它们一次送进同一个审阅框。
  // 审阅框里逐个列出每个仓要提交和推送的内容,确认之前什么都不动。没有可推的就灰着并说明。
  let batch = "";
  if(RP_STATE==="unpushed" || RP_ISSUE){
    const names = hits.filter(rpNeedsPush).map(r=>r.name);
    batch = names.length
      ? `<div class="rp-batch"><button type="button" class="mini primary" data-fixall="commitpush" data-args="${esc(names.join(String.fromCharCode(1)))}"
          title="${esc("先逐个读取发布计划，在同一个审阅框里列出："+names.join("、"))}">审阅并推送这 ${names.length} 个仓库</button></div>`
      : `<div class="rp-batch"><button type="button" class="mini" disabled title="${esc(disabledTitle("审阅并推送","筛出的仓库没有要提交或推送的内容"))}">审阅并推送</button></div>`;
  }
  $("rplist").innerHTML = html ? batch + html : `<div class="rp-empty">没有仓匹配这个过滤条件。</div>`;
  const total = REPOS.summary.total;
  const hit = $("rphit");
  // 状态条上点中的那一段也写进计数旁边:它不是一个下拉,不说出来就看不出列表正按什么筛着。
  const stateNote = RP_STATE ? ` · 状态：${RP_LAB[RP_STATE]||RP_STATE}` : "";
  if(shown===total){ hit.textContent = `${total} 个`; hit.title = ""; }
  else if(matched===shown){ hit.textContent = `${shown} / ${total}${stateNote}`;
    hit.title = `${shown} 个匹配当前过滤条件`; }
  else { hit.textContent = `${matched} 命中 +${shown-matched} 关联 / ${total}${stateNote}`;
    hit.title = "关联 = 自己不匹配,但它的伴生仓(或宿主)匹配,所以一起列出来保住从属关系。"
              + "每一行的徽章仍然是它自己的真实值。"; }

  if(RP_SEL && !REPOS.repos.some(r=>r.name===RP_SEL)) RP_SEL=null;
  renderRepoDetail();
  rpSave();
  rpSyncRoute();
}

// 选中的仓写进地址:#repos/<仓名>。人点的选中加一条历史,浏览器后退就回到上一个选中的仓;
// 自动选中和「原来那个仓不见了」只替换当前这一条,免得后退要按好几下才离开这一页。
function rpSyncRoute(){
  if(RP_SEL===RP_ROUTED){ RP_ROUTE_REPLACE=false; return; }
  const replace = RP_ROUTE_REPLACE || RP_ROUTED===undefined;
  RP_ROUTED = RP_SEL; RP_ROUTE_REPLACE = false;
  if(typeof CURVIEW==="undefined" || CURVIEW!=="repos") return;
  if(typeof location==="undefined" || typeof history==="undefined") return;
  const want = "#" + repoRoute();
  if(location.hash===want) return;
  try{ history[replace ? "replaceState" : "pushState"](null, "", want); }catch(_){}
}
// navigation.js 的 showView 在进入仓库页时调这里:arg 是地址里 #repos/ 后面那段。
// 返回这一页此刻该写进地址的样子。地址里没带仓名时保留现在的选中,不清掉。
function repoRoute(arg){
  if(arg){
    let name = arg;
    try{ name = decodeURIComponent(arg); }catch(_){}
    if(name && name!==RP_SEL){
      RP_SEL = name; RP_ROUTED = name;
      if(REPOS && REPOS.available) renderRepoList();
    }
  }
  return RP_SEL ? "repos/" + encodeURIComponent(RP_SEL) : "repos";
}

// 账号缩写:取名字里的字母数字,大写,最多两个字符。同名两个账号会撞,而撞了也不致命 ——
// 缩写只负责「这一行和上一行是不是同一个账号」,全名在悬停和详情里。
function rpMonogram(owner){
  if(!owner) return "—";
  const parts = String(owner).replace(/[^A-Za-z0-9]+/g," ").trim().split(/\s+/);
  if(parts.length >= 2) return (parts[0][0]+parts[1][0]).toUpperCase();
  const w = parts[0]||"";
  // 驼峰(DaizeDong)取两个大写首字母;否则取前两个字符。
  const caps = w.match(/[A-Z]/g);
  return ((caps && caps.length>=2) ? caps[0]+caps[1] : w.slice(0,2)).toUpperCase();
}

// 公开用自己的标签色(青),不再借错误红:同一列里红色已经表示「未推送」,两件不相干的事撞了色。
const RP_VIS_GLYPH = {
  pub:  {g:"■", cls:"pub", t:"公开。这个仓里不能有真实运行产出。"},
  priv: {g:"□", cls:"",    t:"私有。"},
  unk:  {g:"?", cls:"unk", t:"可见性未知。闸门对未知 remote 是**按公开拦**的,所以这和「私有」不是一回事,要做的事也相反。"},
};

function rpListRow(r, sub, showAcc){
  // 伴生行只显示后缀:前缀逐字等于宿主名,完整名字仍在 title 里。
  const label = sub ? (r.name.slice((r.companionOf||"").length).replace(/^-/,"") || r.name) : r.name;
  const v = !r.visibility ? RP_VIS_GLYPH.unk
          : (/pub/i.test(r.visibility) ? RP_VIS_GLYPH.pub : RP_VIS_GLYPH.priv);
  const id = r.identity||{};
  const cls = id.state==="mismatch" ? " bad" : (id.state==="unchecked" ? " unk" : "");
  const mono = id.state==="unchecked" ? "??" : rpMonogram(r.owner);
  const accTitle = (r.owner||"没有 owner")
    + (id.state && id.state!=="ok" ? " · "+(RP_ID_LAB[id.state]||id.state) : "");
  const acc = showAcc || id.state==="mismatch" || id.state==="unchecked"
    ? `<span class="acc${cls}" title="${esc(accTitle)}">${esc(mono)}</span>` : `<span class="acc" aria-hidden="true"></span>`;
  // 零不写:「0 个改动 · 0 个未推送」在干净的仓上逐行重复,读的人得在一列零里找那几个非零。
  // 不知道的要写出来:没有上游时未推送数是未知,不是零,两者要做的事相反。
  const changes = [];
  if(r.state==="error") changes.push("改动未检查");
  else{
    if(r.dirty==null) changes.push("改动数未检查"); else if(r.dirty) changes.push(r.dirty+" 个改动");
    if(!r.unpushedKnown) changes.push("未推送数未检查"); else if(r.ahead) changes.push(r.ahead+" 个未推送");
  }
  return `<div class="rp-r${sub?" sub":""}${RP_SEL===r.name?" on":""}" data-rp="${esc(r.name)}"
      tabindex="0" role="button" title="${esc(r.path)}">
    <i class="dot d-${r.state}" title="${esc(RP_LAB[r.state]||r.state)}"></i>
    <span class="n">${esc(label)}</span>
    <span class="rp-state">${esc(RP_LAB[r.state]||r.state)}</span>
    <span class="rp-changes">${esc(changes.join(" · "))}</span>
    ${acc}
    <span class="vis ${v.cls}" title="${esc(v.t)}">${v.g}</span></div>`;
}

function rpField(label, value, cls, title){
  return `<div class="rp-f"><span class="lb">${esc(label)}</span>`
    + `<span class="vv ${cls||""}"${title?` title="${esc(title)}"`:""}>${value}</span></div>`;
}

function rpChip(n, label, tone, title){
  const cls = (n===0||n==null) ? (tone==="unk" ? "unk" : "zero") : (tone||"");
  const shown = (n==null) ? "?" : n;
  return `<span class="rp-chip ${cls}" title="${esc(title||"")}">${shown}<span class="t">${esc(label)}</span></span>`;
}

// 文字按钮:窄到放不下时(见 page-resources.css)只剩图标,名字仍在 aria-label 和 title 里。
function rpBtn(sym, label, text, extra, cls, reason, hint){
  const title = reason ? disabledTitle(label, reason) : (hint || label);
  return `<button type="button" class="mini rp-act ${cls||""}" ${extra||""}${reason?" disabled":""} title="${esc(title)}"
    aria-label="${esc(label)}"><svg class="ic" aria-hidden="true"><use href="#${sym}"/></svg><span class="rp-act-t">${esc(text)}</span></button>`;
}

// 手机宽度下列表和详情放不下两栏:点一个仓,详情整块盖在列表上,顶上有「← 返回列表」。
// 宽屏上这个类不起作用(规则只写在窄容器条件里),返回按钮也藏着。
function rpApplySheet(){
  const box = $("rpdetail");
  box?.parentElement?.classList?.toggle("rp-sheet", !!(RP_SHEET && RP_SEL));
}
function rpNarrow(){
  const back = $("rpback");
  if(!back || typeof getComputedStyle!=="function") return false;
  try{ return getComputedStyle(back).display!=="none"; }catch(_){ return false; }
}
function rpCloseSheet(){
  RP_SHEET = false; rpApplySheet();
  const row = RP_SEL && [...(document.querySelectorAll?.("#rplist [data-rp]") || [])].find(el=>el.dataset.rp===RP_SEL);
  if(row){ row.scrollIntoView?.({block:"nearest"}); row.focus?.(); }
}
// Esc 的这一页那一层:手机上详情盖着列表时,Esc 退回列表。宽屏上没有这一层。
function repoEscape(){
  if(!RP_SHEET || !RP_SEL || !rpNarrow()) return false;
  rpCloseSheet();
  return true;
}

// 选中一个有改动的仓就自动读一次改动列表(只读,不进最近操作),不用再去点「查看改动」才知道改了什么。
// 同一次扫描里每个仓只读一次;只读预览不发这个请求(那边一律拒收 POST),改为直接说明。
function rpChangesHtml(r){
  if(r.state==="error" || !(r.dirty>0)) return "";
  const got = RP_STATUS.get(r.name);
  if(!got){
    if(typeof ConsoleActions!=="undefined" && ConsoleActions.readOnly)
      return `<p class="rp-changes-note">只读预览不读取改动列表</p>`;
    RP_STATUS.set(r.name, {pending:true});
    Promise.resolve().then(()=>rpLoadStatus(r.name, false));
    return `<div class="rp-changes-box">${loadingBlock("改动列表")}</div>`;
  }
  if(got.pending) return `<div class="rp-changes-box">${loadingBlock("改动列表")}</div>`;
  if(got.error) return `<div class="rp-changes-box">${errorBlock("改动列表", got.error)}</div>`;
  return `<pre class="rp-out rp-changes-list" aria-label="改动列表">${esc(got.text)}</pre>`;
}
function rpStatusText(res){
  const files = res.files||[];
  // 「一共就这么多」和「只显示了前 N 条」必须分得开,所以条数单独说。
  const head = (res.branch? res.branch+"\n" : "")
    + (res.count ? `${res.count} 个文件有改动`
                 : "工作树干净(没有未提交的改动)")
    + (res.truncated ? `,只列出前 ${files.length} 条` : "");
  return head + (files.length? "\n"+files.join("\n") : "");
}
async function rpLoadStatus(name, loud){
  RP_STATUS.set(name, {pending:true});
  if(RP_SEL===name) renderRepoDetail();
  try{
    // 看改动只是读:标成 inspect,不进「最近操作」,也不把全页的写按钮锁住。
    const res = await api("/api/maint/act",{method:"POST",inspect:true,
      body:JSON.stringify({action:"repo.status", name})});
    if(res.error) throw new Error(res.error);
    RP_STATUS.set(name, {text:rpStatusText(res)});
  }catch(e){
    RP_STATUS.set(name, {error:e.message});
    if(loud) toast(e.message,"bad");
  }
  if(RP_SEL===name) renderRepoDetail();
}

function renderRepoDetail(){
  const box = $("rpdetail");
  rpApplySheet();
  if(!RP_SEL){
    box.innerHTML = `<div class="rp-empty">选择仓库</div>`;
    return;
  }
  const r = REPOS.repos.find(x=>x.name===RP_SEL);
  if(!r){ box.innerHTML = `<div class="rp-empty">这个仓在上一次扫描后不见了。</div>`; return; }
  const id = r.identity||{};
  const byHost = {};
  REPOS.repos.forEach(x=>{ if(x.kind==="companion" && x.companionOf) byHost[x.companionOf]=x; });
  const v = !r.visibility ? RP_VIS_GLYPH.unk
          : (/pub/i.test(r.visibility) ? RP_VIS_GLYPH.pub : RP_VIS_GLYPH.priv);

  // 标题行:名字 + 类型 + 可见性形状 + 账号。判定不是 ok 时账号才带颜色和文字,
  // 因为 ok 的那 47 个不需要每次都念一遍「匹配」。
  const idCls = id.state==="mismatch" ? "bad" : (id.state==="ok" ? "" : "note");
  const idTail = id.state==="ok" ? "" : ` <span class="${idCls}">${esc(RP_ID_LAB[id.state]||id.state||"")}</span>`;
  let h = `<button type="button" class="rp-back" id="rpback" title="回到仓库列表">← 返回列表</button>
  <div class="rp-d-h"><b>${esc(r.name)}</b>
    <span class="k">${esc(RP_KIND_LAB[r.kind]||r.kind||"")}</span>
    <span class="vis ${v.cls}" title="${esc(v.t)}" style="font-family:var(--mono,inherit)">${v.g}</span>
    <span class="k" title="${esc((r.owner||"没有 owner"))}">${esc(r.owner||"—")}${idTail}</span>
    ${r.nested?`<span class="k" title="这个仓的 .git 是文件,说明它是 submodule 或 worktree">嵌套</span>`:""}
  </div>`;

  // 数字排成一行。没有 upstream 时未推送是 null 而不是 0 —— 画成 0 会让
  // 「一个提交都没推出去」和「已同步」长得一模一样,而那正是这块面板存在的理由,
  // 所以那一档画成 `?` 并且用虚线框。
  h += `<div class="rp-chipsrow">`
    + rpChip(r.unpushedKnown ? (r.ahead||0) : null, "未推", r.unpushedKnown ? "hot" : "unk",
             r.unpushedKnown ? "本地有而上游没有的提交" : "没有 upstream 可比,所以不知道推没推出去")
    // ⚠ 落后这个数来自**本地缓存的**那份 origin/xxx(上次 fetch 时的样子),
    // 扫描过程不 fetch。一个从没 fetch 过的仓 behind 恒为 0,而屏幕上它和
    // 「真的已同步」逐字一样。所以缓存太旧或压根没有时,这一格画成未知,
    // 不画那个 0 —— 后端已经算好 behindKnown,这里不再自己推一遍。
    + (r.behindKnown
        ? rpChip(r.behind||0, "落后", "", "上游有而本地没有的提交")
        : rpChip(null, "落后", "unk",
            r.fetchAgeHours==null
              ? "这个仓从来没 fetch 过,所以「落后多少」根本不知道。0 是缓存里的值,不是结论。"
              : `上次 fetch 在 ${r.fetchAgeHours >= 48
                  ? (r.fetchAgeHours/24).toFixed(0)+" 天" : r.fetchAgeHours.toFixed(0)+" 小时"}前,`
                + "缓存太旧,「落后多少」不作数。先 fetch 再看。"))
    + rpChip(r.dirty||0, "未提交", "warn", "工作树里改过但没提交的文件数")
    + `<span class="rp-chip ${r.fetchAgeHours==null?"unk":(r.fetchAgeHours>24?"warn":"zero")}"
        title="${r.fetchAgeHours==null?"从来没 fetch 过":"上次 fetch 距今"}"
        >${r.fetchAgeHours==null?"—":(r.fetchAgeHours>=48?(r.fetchAgeHours/24).toFixed(0)+"d"
          :r.fetchAgeHours.toFixed(0)+"h")}<span class="t">上次 fetch</span></span>`
    + `<span class="rp-chip ${r.ageDays==null?"unk":(r.ageDays>30?"warn":"zero")}"
        title="最后一次提交距今">${r.ageDays==null?"—":(r.ageDays<1?"今天":r.ageDays.toFixed(0)+"d")}<span class="t">最后提交</span></span>`
    + `</div>`;

  // 分支流向。没有 upstream 时箭头是断的 ⇥,并且照样把话说出来:
  // 这是唯一一个「看漏了就会永久丢东西」的状态,不能只靠认出一个形状。
  h += `<div class="rp-flow">`
    + `<span>${r.branch?esc(r.branch):'<span class="up none">游离 HEAD</span>'}</span>`
    + (r.upstream
        ? `<span class="arr">──▸</span><span>${esc(r.upstream)}</span>`
        : `<span class="arr cut">──✕</span><span class="up none">没有 upstream</span>`)
    + `</div>`;

  // 两行事实。标签列去掉了:以盘符开头的绝对路径和以 git@ 开头的 URL 不需要人告诉它们是什么。
  h += `<div class="rp-line"><span class="ic" title="本地路径">📁</span>`
    + `<code>${esc(r.path||"—")}</code>`
    + ibtn("i-copy","复制绝对路径",'data-rpact="copy"') + `</div>`;
  h += `<div class="rp-line"><span class="ic" title="remote">☁</span>`
    + `<code>${r.remote?esc(r.remote):"没有 remote"}</code></div>`;

  // 账号判定不 ok 时把话说出来。ok 的时候一个字都不说 —— 每个仓都念一遍「匹配」
  // 是纯噪音,而噪音会让真正该看的那一条也被跳过。
  if(id.state && id.state!=="ok" && id.why){
    h += `<div class="rp-f"><span class="lb">账号</span><span class="vv note ${idCls}">${esc(id.why)}</span></div>`;
  }
  if(r.state==="error"){
    h += `<div class="rp-f"><span class="lb">扫不动</span><span class="vv bad">${esc(r.why||"")}</span></div>`;
  }

  // 关系做成小标签。三种关系以前各占一行「标签:值」。
  const rel = [];
  const comp = byHost[r.name];
  if(comp) rel.push(`伴生 ${esc(comp.name)}`);
  if(r.companionOf) rel.push(`宿主 ${esc(r.companionOf)}`);
  if(r.usedBy) rel.push(`被 ${r.usedBy} 个仓引用`);
  if(r.usesShared && r.usesShared.length) rel.push(`引用 ${esc(r.usesShared.join("、"))}`);
  if(rel.length) h += `<div class="rp-rel">` + rel.map(x=>`<span>${x}</span>`).join("") + `</div>`;

  // 改动列表默认就摊在详情里(有改动的仓才有)。
  h += rpChangesHtml(r);

  // 五个动作带字:图标里文件夹、下箭头、上箭头要悬停才分得清是「打开目录」「获取更新」还是「推送」。
  // 最要紧的「提交并推送」是实心的主按钮,并带上数目;没有可推的就灰着并说明。
  h += `<div class="rp-acts">
    ${rpBtn("i-folder","打开目录","打开目录",'data-rpact="reveal"')}`;
  // 不能点的时候 aria-label 仍是动作名,原因只进提示:读屏和悬停都先听到「这是哪个按钮」。
  h += r.webUrl
    ? rpBtn("i-web","在浏览器打开","网页",'data-rpact="web"',"","","在浏览器里打开 "+r.webUrl)
    : rpBtn("i-web","在浏览器打开","网页","","","无法识别此仓库的网页地址");
  h += rpBtn("i-diff","查看本地改动","查看改动",'data-rpact="status"',"","","重新读取本地改动文件（git status）")
    + rpBtn("i-fetch","获取远程更新","获取更新",`data-mt="repo.fetch" data-name="${esc(r.name)}"`,"","","获取远程更新（git fetch），不会合并到本地分支");
  // 提交并推送。只在真有东西要做的时候能点 —— 一个永远亮着、点下去说「没什么要做」的
  // 按钮,会让人停止相信这一排按钮的状态。数目是改动的文件数加没推出去的提交数,悬停里分开说。
  const pending = (r.dirty||0) + (r.unpushedKnown ? (r.ahead||0) : 0);
  h += rpNeedsPush(r)
    ? rpBtn("i-push","提交并推送",RP_RETRIES.has(r.name) ? "重试推送" : `提交并推送${pending?" "+pending:""}`,'data-rpact="commitpush"',"primary go","",
        (RP_RETRIES.has(r.name) ? "重试已有提交的推送。" : `提交并推送:先出一份计划给你看,确认之后才动。`)
        + (r.dirty?` 有 ${r.dirty} 个改动`:"") + ((r.ahead||0)?` · ${r.ahead} 个提交没推`:""))
    : rpBtn("i-push","提交并推送","提交并推送","","","没有未提交的改动，也没有未推送的提交");
  h += `</div><div class="rp-out" id="rpout"></div>`;
  box.innerHTML = h;
  const report=RP_REPORTS.get(r.name);
  if(report) $("rpout").textContent=report;
}

// A failed delivery retains the exact commit receipt for an explicit push-only retry.
const RP_RETRIES = new Map();
const RP_REPORTS = new Map();
let RP_PUBLISH_BUSY = false;
try {
  const saved = JSON.parse(sessionStorage.getItem("repo-publish-retries-v1") || "[]");
  if(Array.isArray(saved)) saved.forEach(([name, value])=>{
    if(typeof name === "string" && value && value.expect && value.expect.retry)
      RP_RETRIES.set(name, value);
  });
} catch(_) { /* Storage may be unavailable; the current page still retains receipts. */ }

function repoRemember(name, result, message){
  if(result.expect && result.expect.retry){
    RP_RETRIES.set(name, {expect:result.expect, message, state:result.state});
  } else if(result.ok === true && result.state === "pushed") {
    RP_RETRIES.delete(name);
  }
  try { sessionStorage.setItem("repo-publish-retries-v1", JSON.stringify([...RP_RETRIES])); }
  catch(_) { /* Do not turn completed publication into a failed action. */ }
}

function repoPlanProblem(p){
  if(p.error) return p.error;
  if(p.blocked && p.blocked.length) return p.blocked.join("；");
  if(p.ok === false) return "发布计划不可用，请重新读取并核查错误";
  if(p.aheadTruncated || p.aheadKnown === false || p.historyTruncated || p.historyKnown === false)
    return "提交历史没有完整显示，请先核对";
  if(p.filesTruncated || p.diffTruncated) return "差异没有完整显示，请缩小提交范围";
  if(!p.nothing && (!p.expect || typeof p.expect !== "object")) return "缺少完整审阅依据，请重新读取计划";
  return null;
}

function repoPlanText(name, p){
  if(p.retry) return [name, "仅重试推送已存在的提交", "提交: " + p.expect.retry.commit,
    "树: " + p.expect.retry.tree,
    "目标: " + JSON.stringify(p.expect.target), "当前未提交的改动不会加入这次推送。"].join("\n");
  return [name + "  " + p.branch + " → " + p.upstream,
    "目标: " + (p.remote || "未知"), "可见性: " + (p.visibility || "未知"),
    "待提交文件: " + (p.fileCount || 0), ...(p.files || []),
    "实际推送目标: " + (p.pushTarget?.ref || "未知"),
    "已核对的远端基点: " + (p.remoteBase || "新建远端分支；审阅全部历史"),
    "待发布的已有提交: " + (p.aheadCount ?? "未知"), ...(p.ahead || []),
    "完整待发布历史审阅: " + (p.historyCount ?? "未知") + " 个提交",
    ...(p.warn || []), "", p.diff || "没有未提交差异"].join("\n");
}

// The complete frozen review remains scrollable while the user decides.
// 外壳和别的对话框一样:标题和 ✕ 在顶上,取消和确认发布在底部右侧。点遮罩、Esc、✕、取消都等于不发布;
// 改过提交信息之后误点遮罩不算(只认 Esc 和取消),发布请求在路上时谁都关不掉。
// 打开时焦点停在「取消」上,不停在提交信息框里:框里已经有一句能用的信息,按住或多按一下 Enter
// 就会在人还没看审阅内容时发布。只有人自己点进提交信息框、并且是一次不连发的 Enter,才算确认。
function repoReview(items){
  return new Promise(resolve=>{
    const make=(tag, props)=>Object.assign(document.createElement(tag), props || {});
    const dialog=make("dialog", {className:"rp-review console-dialog"});
    dialog.setAttribute?.("closedby", "any");
    dialog.setAttribute?.("aria-label", "审阅并发布");
    const form=make("form", {method:"dialog"});
    const head=make("div", {className:"dialog-head"});
    const title=make("h2", {textContent:"审阅并发布 " + items.length + " 个仓库"});
    const close=make("button", {type:"button", className:"icon-only dialog-x", title:"关闭"});
    close.setAttribute("aria-label", "关闭");
    // 和别的对话框同一个 ✕ 图标;这里全程不写 innerHTML(审阅内容是外来数据),所以按节点拼。
    if(typeof document.createElementNS==="function"){
      const svg=document.createElementNS("http://www.w3.org/2000/svg", "svg"), use=document.createElementNS("http://www.w3.org/2000/svg", "use");
      svg.setAttribute("class", "ic");svg.setAttribute("aria-hidden", "true");use.setAttribute("href", "#i-close");
      svg.appendChild(use);close.appendChild(svg);
    }else close.textContent="✕";
    const body=make("div", {className:"dialog-body"});
    const detail=make("pre", {textContent:items.map(({name, plan})=>repoPlanText(name, plan)).join("\n\n────────\n\n")});
    const label=make("label", {textContent:"提交信息"});
    const initial=items.every(x=>x.plan.retry) ? "重试推送" : "例行数据同步";
    const input=make("input", {type:"text", value:initial, maxLength:200});
    label.appendChild(input);
    const error=make("p");error.setAttribute("role", "alert");
    const foot=make("div", {className:"dialog-foot"});
    const cancel=make("button", {type:"button", textContent:"取消", autofocus:true});
    const accept=make("button", {type:"submit", className:"primary", textContent:"确认发布"});
    let settled=false, armed=false;
    const finish=value=>{ if(settled) return; settled=true; dialog.remove(); resolve(value); };
    const dirty=()=>input.value!==initial;
    const syncDismiss=()=>dialog.setAttribute?.("closedby", dirty() ? "closerequest" : "any");
    const publish=()=>{
      const message=input.value.trim();
      if(!message || /[\r\n`$]/.test(message)){
        error.textContent="请输入一行提交信息，不能包含反引号或 $。"; return;
      }
      finish(message);
    };
    cancel.addEventListener("click", ()=>finish(null));
    close.addEventListener("click", ()=>finish(null));
    dialog.addEventListener("cancel", event=>{ event.preventDefault(); finish(null); });
    // 点遮罩由浏览器直接关掉对话框,不经过 cancel 的拦截;关了就是不发布。
    dialog.addEventListener("close", ()=>finish(null));
    input.addEventListener("focus", ()=>{ armed=true; });
    input.addEventListener("input", syncDismiss);
    input.addEventListener("keydown", event=>{
      if(event.key!=="Enter") return;
      // 输入法组字、按住连发、还没被人点进来过:这一下 Enter 都不算确认。
      if(event.isComposing || event.repeat || !armed) event.preventDefault();
    });
    form.addEventListener("submit", event=>{ event.preventDefault(); publish(); });
    accept.addEventListener("click", event=>{ event?.preventDefault?.(); publish(); });
    head.appendChild(title);head.appendChild(close);
    [detail, label, error].forEach(node=>body.appendChild(node));
    foot.appendChild(cancel);foot.appendChild(accept);
    [head, body, foot].forEach(node=>form.appendChild(node));
    dialog.appendChild(form);
    document.body.appendChild(dialog);
    dialog.showModal();
    cancel.focus?.();
  });
}

function repoResultText(name, r){
  if(r.ok === true && r.state === "pushed") return name + ": 已推送。";
  const known=r.commit ? "\n本地提交: " + r.commit : "";
  const state=r.state === "push_failed" ? "提交已保留，推送失败"
    : r.state === "unknown" ? "结果尚未确认，需要核查"
    : r.state === "committed" ? "已提交，尚未推送" : "发布未完成";
  const retry=r.expect && r.expect.retry ? "\n再次点击发布可仅重试这次推送。" : "";
  return name + ": " + state + known + retry + (r.error ? "\n" + r.error : "");
}

async function repoPublishPlans(names){
  if(RP_PUBLISH_BUSY){ toast("已有发布操作正在处理", "bad"); return; }
  RP_PUBLISH_BUSY=true;
  const items=[], done=[];
  let failure=null;
  try{
    // Collect every review before asking for approval. Execution never replans.
    for(const name of [...new Set(names)]){
      const retry=RP_RETRIES.get(name);
      const plan=retry ? {retry:true, expect:retry.expect}
        : await api("/api/repo/plan", {method:"POST", body:JSON.stringify({name})});
      const problem=repoPlanProblem(plan);
      if(problem) throw new Error(name + ": " + problem);
      if(plan.nothing) continue;
      items.push({name, plan});
    }
    if(!items.length){ toast("没有待发布的改动或提交"); return; }
    const message=await repoReview(items);
    if(message === null) return;
    for(const {name, plan} of items){
      let result;
      try{
        result=await api("/api/maint/act", {method:"POST", body:JSON.stringify({
          action:"repo.commitpush", name,
          arg:JSON.stringify({message, expect:plan.expect, push:true})})});
      }catch(e){
        failure=name + ": 未收到完整结果，需要先核查本地提交和远端状态。\n" + e.message;
        RP_REPORTS.set(name, failure);
        break;
      }
      repoRemember(name, result, message);
      const report=repoResultText(name, result) + (result.out ? "\n\n" + result.out : "");
      RP_REPORTS.set(name, report);
      if(result.ok !== true || result.state !== "pushed") { failure=report; break; }
      done.push(name);
    }
    await loadRepos();
    if(failure){
      const summary=failure + "\n\n已推送: " + (done.join("、") || "无")
        + "\n尚未执行: " + Math.max(0, items.length-done.length-1);
      const out=$("rpout"); if(out) out.textContent=summary;
      toast("发布未全部完成，详情已保留", "bad");
      // 结果写在仓库详情的结果栏里(#rpout),一直留着;不再弹浏览器自带的提示框,那个关掉就没了。
      if(out) out.scrollIntoView?.({block:"nearest"});
    }else{
      toast(done.length === 1 ? done[0] + " 已推送" : done.length + " 个仓库已推送","ok");
    }
  }catch(e){
    const out=$("rpout"); if(out) out.textContent=e.message;
    toast(e.message, "bad");
  }finally{ RP_PUBLISH_BUSY=false; }
}

async function repoCommitPush(name){ return repoPublishPlans([name]); }
async function repoCommitPushBatch(names){ return repoPublishPlans(names); }

// ── 代码仓库这一屏的挂点 ──(从 events.js 搬来,原因见 tasks.js 的 startTasksPage 上方)
function startRepositories(){
  // 上次的筛选和选中放回去。地址里带了仓名(#repos/<仓名>)的话,随后 showView 会以地址为准。
  const saved = rpSaved();
  if(typeof saved.vis==="string" && ["","pub","priv","unk"].includes(saved.vis)) $("rpvis").value = saved.vis;
  if(typeof saved.issue==="string" && [...$("rpissue").options].some(o=>o.value===saved.issue)){ $("rpissue").value = saved.issue; RP_ISSUE = saved.issue; }
  if(typeof saved.state==="string" && Object.hasOwn(RP_LAB, saved.state)) RP_STATE = saved.state;
  if(typeof saved.sel==="string" && saved.sel) RP_SEL = saved.sel;
  RP_PENDING = {acc:typeof saved.acc==="string" ? saved.acc : "", kind:typeof saved.kind==="string" ? saved.kind : ""};
  // 搜索框现在也搜路径和远程地址,提示语跟着说清楚。
  $("rpq").placeholder = "搜索名称、账号、路径或远程地址";
  // 过滤只改看得见什么,不重新扫描 —— 扫一遍所有仓要一秒多,而每敲一个字符重扫一次
  // 既慢又会让选中的那个仓在脚下换位置。
  $("rpq").addEventListener("input", renderRepoList);
  $("rpacc").addEventListener("change", renderRepoList);
  $("rpvis").addEventListener("change", renderRepoList);
  $("rpkind").addEventListener("change", renderRepoList);
  // 点一个仓 = 要看它的详情。手机上详情盖到列表上;选中本身由 events.js 那个全页点击处理器接着做。
  // 挂在列表元素上,比挂在 document 上的那个先跑,所以那边重画时已经知道要盖上来。
  $("rplist").addEventListener("click", e=>{
    if(e.target.closest("[data-rp]")){ RP_SHEET = true; requestAnimationFrame(rpRevealSheet); }
  });
  // 列表行既可点也可用键盘。给了 tabindex 却不接键,那是「看起来能聚焦却按不动」,
  // 比不可聚焦更让人困惑 —— 这条在别处已经栽过一次。
  $("rplist").addEventListener("keydown", e=>{
    if(e.key!=="Enter" && e.key!==" ") return;
    const row = e.target.closest("[data-rp]");
    if(!row) return;
    e.preventDefault();
    RP_SEL = row.dataset.rp; RP_SHEET = true; renderRepoList(); rpRevealSheet();
  });
  $("rpdetail").addEventListener("click", e=>{
    if(e.target.closest("#rpback")) rpCloseSheet();
  });
  $('rpissue').addEventListener('change',event=>{RP_ISSUE=event.target.value;renderRepoList();});
}
// 详情盖上来之后把它的顶端带进视口,焦点交给「返回列表」:不然在手机上点了一行,变化发生在屏幕外面,看起来像没反应。
function rpRevealSheet(){
  if(!RP_SHEET || !rpNarrow()) return;
  const back = $("rpback");
  $("rpdetail").scrollIntoView?.({block:"start"});
  back?.focus?.({preventScroll:true});
}
