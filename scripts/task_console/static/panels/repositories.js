// Classic script module; loaded in app.js dependency order.
async function repoAct(what){
  const r = REPOS && REPOS.repos.find(x=>x.name===RP_SEL);
  if(!r){ toast("没有选中的仓","bad"); return; }
  const out = $("rpout");
  if(what==="copy"){
    // 剪贴板在非安全上下文里不存在。失败时把路径显示出来让人自己选 ——
    // 一个静默失败的复制按钮会让人以为已经复制了,然后粘出上一次的东西。
    if(navigator.clipboard && window.isSecureContext){
      navigator.clipboard.writeText(r.path).then(()=>toast("路径已复制"),
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
      toast("已在资源管理器打开");
    }catch(e){ toast(e.message,"bad"); }
    return;
  }
  if(what==="status"){
    if(out) out.textContent = "读取中…";
    try{
      const res = await api("/api/maint/act",{method:"POST",
        body:JSON.stringify({action:"repo.status", name:r.name})});
      if(res.error){ if(out) out.textContent = res.error; toast(res.error,"bad"); return; }
      const files = res.files||[];
      // 「一共就这么多」和「只显示了前 N 条」必须分得开,所以条数单独说。
      const head = (res.branch? res.branch+"\n" : "")
        + (res.count ? `${res.count} 个文件有改动`
                     : "工作树干净(没有未提交的改动)")
        + (res.truncated ? `,只列出前 ${files.length} 条` : "");
      if(out) out.textContent = head + (files.length? "\n"+files.join("\n") : "");
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
  catch(e){ $("rpnote").textContent="失败:"+e.message; return; }
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
  const segs = RP_ORDER.filter(k=>c[k]);
  let head = `<span>共 <b>${S.total}</b></span>`
    + `<span class="rp-bar" role="img" aria-label="${esc(segs.map(k=>RP_LAB[k]+" "+c[k]).join(","))}">`
    + segs.map(k=>`<i class="d-${k}${RP_STATE===k?" sel":""}" data-rpstate="${k}"
        style="flex:${c[k]}" title="${esc(RP_LAB[k])} ${c[k]} 个 · 点一下只看这一类"
        ><b>${c[k]}</b></i>`).join("")
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
  const hay = (r.name+" "+(r.owner||"")).toLowerCase();
  return hay.indexOf(q) >= 0;
}

function renderRepoList(){
  if(!REPOS || !REPOS.available) return;

  // 首屏自动选中第一个要人管的仓。原来右边 1000x750 默认全白,只有一句「左边选一个仓」,
  // 而这一页自己的注释早就写过「空出来的那片读起来像坏了」。
  // ⚠ 必须在拼列表 HTML **之前**做,否则选中的那一行不会带上 .on 高亮 ——
  // 右边显示着某个仓,左边没有任何一行看起来被选中,那比不自动选更让人困惑。
  // ⚠ 只做一次:否则人手动取消选择之后,下一次刷新又会把选中抢回去。
  // 要人管的状态集合走 RP_ATT(),不在这里另写一份阈值。
  if(!RP_SEL && !RP_AUTOSEL && Array.isArray(REPOS.repos)){
    RP_AUTOSEL = true;
    const att = RP_ATT();
    const first = REPOS.repos.find(r=>att.indexOf(r.state) >= 0);
    if(first) RP_SEL = first.name;
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
  let shown = 0, matched = 0, html = "";
  order.forEach(k=>{
    const rows = tops.filter(r=>r.kind===k && hostShown.has(r.name));
    if(!rows.length) return;
    html += `<div class="rp-gh"><b>${esc(RP_KIND_LAB[k]||k)}</b><span class="n">${rows.length}</span></div>`;
    rows.forEach(r=>{
      html += rpListRow(r,false); shown++;
      if(rpMatches(r,q,acc,kind,vis)) matched++;
      const comp = byHost[r.name];
      if(comp){ html += rpListRow(comp,true); shown++;
                if(rpMatches(comp,q,acc,kind,vis)) matched++; }
    });
  });
  $("rplist").innerHTML = html || `<div class="rp-empty">没有仓匹配这个过滤条件。</div>`;
  const total = REPOS.summary.total;
  const hit = $("rphit");
  if(shown===total){ hit.textContent = `${total} 个`; hit.title = ""; }
  else if(matched===shown){ hit.textContent = `${shown} / ${total}`;
    hit.title = `${shown} 个匹配当前过滤条件`; }
  else { hit.textContent = `${matched} 命中 +${shown-matched} 关联 / ${total}`;
    hit.title = "关联 = 自己不匹配,但它的伴生仓(或宿主)匹配,所以一起列出来保住从属关系。"
              + "每一行的徽章仍然是它自己的真实值。"; }

  if(RP_SEL && !REPOS.repos.some(r=>r.name===RP_SEL)) RP_SEL=null;
  renderRepoDetail();
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

const RP_VIS_GLYPH = {
  pub:  {g:"■", cls:"pub", t:"公开。这个仓里不能有真实运行产出。"},
  priv: {g:"□", cls:"",    t:"私有。"},
  unk:  {g:"?", cls:"unk", t:"可见性未知。闸门对未知 remote 是**按公开拦**的,所以这和「私有」不是一回事,要做的事也相反。"},
};

function rpListRow(r, sub){
  // 伴生行只显示后缀:前缀逐字等于宿主名,完整名字仍在 title 里。
  const label = sub ? (r.name.slice((r.companionOf||"").length).replace(/^-/,"") || r.name) : r.name;
  const v = !r.visibility ? RP_VIS_GLYPH.unk
          : (/pub/i.test(r.visibility) ? RP_VIS_GLYPH.pub : RP_VIS_GLYPH.priv);
  const id = r.identity||{};
  const cls = id.state==="mismatch" ? " bad" : (id.state==="unchecked" ? " unk" : "");
  const mono = id.state==="unchecked" ? "??" : rpMonogram(r.owner);
  const accTitle = (r.owner||"没有 owner")
    + (id.state && id.state!=="ok" ? " · "+(RP_ID_LAB[id.state]||id.state) : "");
  return `<div class="rp-r${sub?" sub":""}${RP_SEL===r.name?" on":""}" data-rp="${esc(r.name)}"
      tabindex="0" role="button" title="${esc(r.path)}">
    <i class="dot d-${r.state}" title="${esc(RP_LAB[r.state]||r.state)}"></i>
    <span class="n">${esc(label)}</span>
    <span class="acc${cls}" title="${esc(accTitle)}">${esc(mono)}</span>
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

function renderRepoDetail(){
  const box = $("rpdetail");
  if(!RP_SEL){
    box.innerHTML = `<div class="rp-empty">左边选一个仓,这里显示它的信息和可做的操作。</div>`;
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
  let h = `<div class="rp-d-h"><b>${esc(r.name)}</b>
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

  // 四个动作改成图标。每个都带 title 和 aria-label —— 图标按钮没有可读名字
  // 就等于把「字太多」换成了「什么都没有」。
  h += `<div class="rp-acts">
    ${ibtn("i-folder","打开目录",'data-rpact="reveal"')}`;
  h += r.webUrl
    ? ibtn("i-web","在浏览器里打开 "+r.webUrl,'data-rpact="web"')
    : ibtn("i-web","无法识别此仓库的网页地址","disabled");
  h += ibtn("i-diff","查看本地改动文件（git status）",'data-rpact="status"')
    + ibtn("i-fetch","获取远程更新（git fetch），不会合并到本地分支",`data-mt="repo.fetch" data-name="${esc(r.name)}"`);
  // 提交并推送。只在真有东西要做的时候出现 —— 一个永远亮着、点下去说「没什么要做」的
  // 按钮,会让人停止相信这一排按钮的状态。
  const needs = RP_RETRIES.has(r.name) || (r.dirty||0) > 0 || (r.unpushedKnown && (r.ahead||0) > 0);
  h += needs
    ? ibtn("i-push", (RP_RETRIES.has(r.name) ? "重试已有提交的推送。" : `提交并推送:先出一份计划给你看,确认之后才动。`)
        + (r.dirty?` 有 ${r.dirty} 个改动`:"") + ((r.ahead||0)?` · ${r.ahead} 个提交没推`:""),
        'data-rpact="commitpush"', "go")
    : ibtn("i-push","没有未提交的改动,也没有未推送的提交","disabled");
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
function repoReview(items){
  return new Promise(resolve=>{
    const dialog=document.createElement("dialog");
    dialog.className="rp-review";
    const title=document.createElement("h2");
    title.textContent="审阅并发布 " + items.length + " 个仓库";
    const detail=document.createElement("pre");
    detail.textContent=items.map(({name, plan})=>repoPlanText(name, plan)).join("\n\n────────\n\n");
    const label=document.createElement("label");
    label.textContent="提交信息";
    const input=document.createElement("input");
    input.value=items.every(x=>x.plan.retry) ? "重试推送" : "例行数据同步";
    input.maxLength=200;
    label.appendChild(input);
    const cancel=document.createElement("button"); cancel.textContent="取消";
    const accept=document.createElement("button"); accept.textContent="确认发布";
    const error=document.createElement("p"); error.setAttribute("role", "alert");
    let settled=false;
    const finish=value=>{ if(settled) return; settled=true; dialog.remove(); resolve(value); };
    cancel.addEventListener("click", ()=>finish(null));
    dialog.addEventListener("cancel", event=>{ event.preventDefault(); finish(null); });
    accept.addEventListener("click", ()=>{
      const message=input.value.trim();
      if(!message || /[\r\n`$]/.test(message)){
        error.textContent="请输入一行提交信息，不能包含反引号或 $。"; return;
      }
      finish(message);
    });
    [title, detail, label, error, cancel, accept].forEach(node=>dialog.appendChild(node));
    document.body.appendChild(dialog);
    dialog.showModal();
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
      if(items.length > 1) alert(summary);
    }else{
      toast(done.length === 1 ? done[0] + " 已推送" : done.length + " 个仓库已推送");
    }
  }catch(e){
    const out=$("rpout"); if(out) out.textContent=e.message;
    toast(e.message, "bad");
  }finally{ RP_PUBLISH_BUSY=false; }
}

async function repoCommitPush(name){ return repoPublishPlans([name]); }
async function repoCommitPushBatch(names){ return repoPublishPlans(names); }
