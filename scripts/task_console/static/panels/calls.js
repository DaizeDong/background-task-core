// Classic script module; loaded in app.js dependency order.
let LLM=null, LCDRAFT=null, LMROWS=null, LMTOTAL=0, LMOPEN=null, LMBODY={};
let LM_REQUEST=0;
// 明细读失败时整张表换成红色的失败块(带重试),不再是标题旁一行灰字,也不画成「没有符合条件的记录」:
// 读坏了和真的没有是两件事。
let LMERR=null;
let LLM_LOADING=null;
// 默认最新在前:账本按写入顺序排,正序的第一页是最旧、多半没有时间戳的那批,
// 要找的最近调用原来在几千页之后。
const LMQ = {offset:0, limit:20, provider:"", ok:"", q:"", caller:"", order:"desc"};
// 从「失败与回退」跳过来的那一行,在表里标出来,不然一页二十行里分不出是哪一条。
let LMJUMP = null;

// 筛选和顺序记在本机,刷新后不用重选;搜索词不记:一个留在框里的旧词会让人以为表里只有这么几条。
// 存储被禁用(隐私窗口、策略)时读写都会抛错,那时就当没记过。
const LM_PREF_KEY = "tc.llm.calls";
function lmLoadPrefs(){
  try{
    const p = JSON.parse(localStorage.getItem(LM_PREF_KEY) || "null");
    if(!p || typeof p !== "object") return;
    if(typeof p.provider === "string") LMQ.provider = p.provider;
    if(p.ok === "" || p.ok === "0" || p.ok === "1") LMQ.ok = p.ok;
    if(typeof p.caller === "string") LMQ.caller = p.caller;
    if(p.order === "asc" || p.order === "desc") LMQ.order = p.order;
  }catch(e){}
}
function lmSavePrefs(){
  try{ localStorage.setItem(LM_PREF_KEY, JSON.stringify({provider:LMQ.provider, ok:LMQ.ok, caller:LMQ.caller, order:LMQ.order})); }catch(e){}
}
lmLoadPrefs();
// 记下的服务或调用方可能已经不在账本里了。留着它,下拉框会显示「全部」而表却按一个看不见的条件筛着。
function lmDropStalePrefs(){
  if(!LLM || LLM.error) return;
  const provs = new Set((LLM.rungs || []).map(r=>r.name).concat(["NONE"]));
  if(LMQ.provider && !provs.has(LMQ.provider)) LMQ.provider = "";
  const callers = new Set((LLM.callers || []).map(c=>c.name));
  if(LMQ.caller && !callers.has(LMQ.caller)) LMQ.caller = "";
}

const lnum = n => fmtNum(n);
// 没量到 / 量到零 / 有值,三种要长得不一样。这个函数只负责前两种的区分。
const lpct = f => f==null ? '<span class="faint">未计</span>' : (f*100).toFixed(0)+"%";
// 没有时间戳的记录在这一屏有十几万条,每行写一遍「无时间戳」只是噪音:写成淡色的「—」,原因放在悬停里。
const lwhen = t => t==null ? '<span class="when unk" title="无时间戳">—</span>' : timeTag(t);

function loadLLM(){
  if(LLM_LOADING) return LLM_LOADING;
  $("lcnote").textContent='正在读取完整调用账本…';
  LLM_LOADING=(async()=>{
  try{ LLM = await api("/api/llmcall"); $("lcnote").textContent=""; }
  catch(e){ LLM = {error: e.message}; }
  LCDRAFT = null;
  renderLLM();
  lmDropStalePrefs();
  await loadCalls();
  })().finally(()=>{LLM_LOADING=null;});
  return LLM_LOADING;
}

function renderLLM(){
  if(!LLM) return;
  if(LLM.error){
    // 读坏了是红色的一块并带重试,不是标题旁一行灰字:灰字在这里读起来像一句普通的副标题。
    $("lcnote").textContent = "";
    $("lclist").innerHTML = "";
    $("lwins").innerHTML = errorBlock("调用统计", LLM.error, "data-llm-retry");
    $("lledger").innerHTML = "";
    $("lrtab").innerHTML = ""; $("lstab").innerHTML = "";
    return;
  }
  renderChain(); renderWins(); renderRungs(); renderRuns(); llmBadge();
}

// 侧栏徽章(工作记录)和「模型调用」标签上的那一枚。这一屏平时不需要人盯着,所以徽章只在**有事**的时候亮,
// 而「有事」在这里有两种,数的是同一个徽章但理由不同:
//   近 7 日里有整链失败(四级全挂,那次调用没有答案):失败是红的,数字就是失败次数;
//   或者链的顺序被一个看不见的环境变量压着,页面上的设置不作数:它不是「几件事」,
//   是「这一屏在骗你」,至少亮成 1 并写进说明。
// 以前这里把徽章的字改写成「7日」,数字反而看不见了;时间范围改写在说明里。
function llmBadge(){
  const w = (LLM.windows || [])[1] || {};
  const c = LLM.chain || {};
  const n = (w.failed || 0); // Undated/cumulative parse errors belong in ledger diagnostics.
  setBadge("llm", c.shadowed_by_env ? Math.max(n, 1) : n, false,
    `模型调用：近 7 日整链失败 ${n} 次${c.shadowed_by_env ? "；调用顺序被环境变量覆盖，页面上的设置不生效" : ""}`);
}

// ── 链顺序 ──
function renderChain(){
  const c = LLM.chain || {};
  const eff = LCDRAFT || (c.effective || []);
  const src = c.source || "builtin";
  // 四种来源各说各的话。`observed` 那一句刻意点出行号:这个顺序不是一份配置,
  // 是**从真实调用里观测到的**,而观测是可以被指认的 —— 点得到那一行。
  // 早先这里只有「内置默认」一个说法,而那份内置常量是控制台自己存的一份拷贝,
  // 和 llmcall 真正的默认已经漂开过一次,徽章却显得很权威。
  const label = {env: "来自环境变量", file: "来自配置文件",
                 observed: "最近一次真实调用" + (c.observed_i != null ? "(第 " + lnum(c.observed_i) + " 行)" : ""),
                 builtin: "控制台内置的一份拷贝"}[src] || src;
  const dirty = LCDRAFT && JSON.stringify(LCDRAFT) !== JSON.stringify(c.effective || []);
  // 排过的顺序只在点了保存才生效,切走再回来草稿还在:标题旁挂一枚「未保存」,别让人以为已经改好了。
  $("lcnote").innerHTML = '<span class="lc-src '+esc(src)+'">'+esc(label)+'</span>'
    + (dirty ? statusBadge('未保存','warn','!','lc-dirty') : '');
  // 配置写着一个顺序,而最近一次真实调用走的是另一个 —— 这句话只有在 observed
  // 恒填时才说得出来,而它正是「我改了顺序但没生效」最直接的证据。
  const drift = (src === "env" || src === "file") && c.observed
    && JSON.stringify(c.observed) !== JSON.stringify(c.effective);
  $("lcdrift").innerHTML = drift
    ? '<div class="lc-warn">配置里是 <b>' + esc((c.effective || []).join(" → "))
      + "</b>，最近一次调用（第 " + lnum(c.observed_i) + " 行）使用 <b>"
      + esc(c.observed.join(" → ")) + "</b>。可能是该次调用指定了顺序，也可能是调用程序尚未读取新配置。</div>"
    : "";

  // 这条告示是这一屏最重要的一行。LLMCALL_CHAIN 一旦存在就压过文件里的一切,
  // 而它在页面上是看不见的:少了这条,用户在这里排好顺序、按下保存、看到成功提示,
  // 然后所有调用照旧走那个被环境变量钉死的顺序。
  $("lcwarn").innerHTML = c.shadowed_by_env
    ? '<div class="lc-warn">环境变量 <b>LLMCALL_CHAIN=' + esc(c.env_value || "")
      + '</b> 优先于配置文件。这里保存的顺序<b>当前不会生效</b>，需先移除该环境变量。</div>'
    : (src === "env"
       ? '<div class="lc-warn">当前顺序由环境变量 <b>LLMCALL_CHAIN</b> 指定。这里保存的顺序需移除该变量后才会生效。</div>'
       : "");

  // 只读预览里排序存不下来,所以连拖拽也不给:拖完一份永远保存不了的草稿只会误导人。
  const drag = !ConsoleActions.readOnly;
  const mv = (dir,i,label,edge) => '<button data-mv="'+dir+'" data-i="'+i+'" title="'
    + esc(edge ? disabledTitle(label, edge) : label) + '"' + (edge ? " disabled" : "") + '>' + (dir === "up" ? "↑" : "↓") + '</button>';
  $("lclist").innerHTML = eff.map((p,i)=>
    '<li draggable="'+drag+'" data-i="'+i+'"><span class="ord">'+(i+1)+'</span>'
    + '<span class="nm">'+esc(p)+'</span>'
    + '<span class="mv">' + mv("up", i, "上移", i===0 ? "已在最前" : "")
    + mv("dn", i, "下移", i===eff.length-1 ? "已在最后" : "") + '</span></li>'
  ).join("");
  ConsoleActions.gate($("lcsave"), busy ? '正在保存' : dirty ? '' : '顺序没有改动，先用上下箭头调整');
  if(dirty && !busy && !ConsoleActions.readOnly) $("lcsave").title = '保存当前排列顺序';
  ConsoleActions.gate($("lcreset"), LCDRAFT ? '' : '当前没有未保存的修改');
  if(LCDRAFT && !ConsoleActions.readOnly) $("lcreset").title = '放弃未保存的排列修改';
  $("lcpath").textContent = c.file_path || "";
}

function lcMove(i, d){
  const c = LLM.chain || {};
  const a = (LCDRAFT || (c.effective || [])).slice();
  const j = i + d;
  if(j < 0 || j >= a.length) return;
  const t = a[i]; a[i] = a[j]; a[j] = t;
  LCDRAFT = a;
  renderChain();
}

async function lcSave(){
  if(!LCDRAFT || busy) return;
  busy = true; $("lcsave").disabled = true;
  try{
    const r = await api("/api/llmcall/chain", {method:"POST", body: JSON.stringify({chain: LCDRAFT})});
    LLM.chain = r;
    LCDRAFT = null;
    // 成功提示里必须带上「是否生效」。一个只说「已保存」的提示,在被环境变量压着的时候
    // 说的是真话,但传达的是假消息。
    toast(r.shadowed_by_env
      ? "顺序已保存，但环境变量 LLMCALL_CHAIN 优先，当前不会生效"
      : "顺序已保存:" + (r.effective || []).join(" → "),
      r.shadowed_by_env ? "bad" : "ok");
    renderChain();
  }catch(e){ toast("保存失败:" + e.message, "bad"); }
  finally{ busy = false; renderChain(); }
}

// ── 用量窗口 ──
function renderWins(){
  const L0 = LLM.ledger || {};
  // 账本文件根本不在,和账本在、里面今天没有调用,是两件事。
  // 后者是「今天很闲」,前者是「这一屏什么都没看」—— 而把前者画成一排 0,
  // 正是这台台子从头到尾在反对的那种绿色。
  if(!L0.exists){
    $("lwins").innerHTML = '<div class="l-win l-unk"><h4>调用记录</h4>'
      + '<div class="big">未检查</div><div class="sub">'
      + esc(L0.path || "路径未知") + " 不存在，无法统计。</div></div>";
    $("lledger").innerHTML = "请检查调用记录路径 <code>TASK_CONSOLE_LLMCALL_LEDGER</code>，并确认 llmcall 已开启记录。";
    $("lunote").textContent = "未找到调用记录文件";
    return;
  }
  const wins = LLM.windows || [];
  $("lwins").innerHTML = wins.map(w=>{
    // 窗口里一条都没有,但账本里明明有十一万条 —— 这不是「没调用」,是「问不出来」。
    const blind = (w.calls === 0 && (w.unstamped_excluded || 0) > 0);
    const cls = blind ? "l-win l-unk" : "l-win";
    const big = blind ? "无从得知" : lnum(w.calls) + " 次";
    // 无时间戳的条数每个时段都一样,只在下面账本那一行说一次;每张卡各写一遍,读起来像四件事。
    const sub = blind
      ? "这段时间里没有带时间戳的调用，无法统计"
      : ('<span class="ok">' + lnum(w.ok) + " 成功</span> · "
         + (w.failed ? '<span class="bad">' + lnum(w.failed) + " 失败</span>" : "0 失败")
         + " · 低成本服务占比 " + lpct(w.cheap_share)
         + (w.avg_ms != null ? " · 平均 " + lnum(Math.round(w.avg_ms)) + "ms" : ""));
    return '<div class="' + cls + '"><h4>' + esc(w.label) + "</h4>"
      + '<div class="big">' + esc(big) + "</div>"
      + '<div class="sub">' + sub + "</div></div>";
  }).join("");

  const L = LLM.ledger || {};
  $("lledger").innerHTML = "已读取 " + kb(L.bytes)
    + " · " + lnum(L.parsed) + " 条可解析"
    + (L.malformed ? ' · <span class="bad">' + lnum(L.malformed) + " 条坏行</span>" : "")
    + " · 带时间戳 " + lnum(L.stamped) + " 条 / 无时间戳 " + lnum(L.unstamped) + " 条"
    + (L.unstamped ? "（不计入上面各时段）" : "")
    // 读侧承诺 parsed + malformed + blank == lines。这里当场验算,不是装饰:
    // 一个漏读了一段文件的解析器,报出来的每个数都自洽、都好看,
    // 只是全都偏小 —— 而偏小的统计和真实的低用量在屏幕上是同一个数字。
    + ((L.exists && L.lines != null
        && (L.parsed || 0) + (L.malformed || 0) + (L.blank || 0) !== L.lines)
       ? ' · <span class="bad">对不上:' + lnum(L.parsed) + "+" + lnum(L.malformed)
         + "+" + lnum(L.blank) + " ≠ " + lnum(L.lines) + " 行,解析器漏了东西</span>"
       : "");
  $("lunote").textContent = "";
}

// ── 每一级 ──
function renderRungs(){
  const rs = LLM.rungs || [];
  $("lrtab").innerHTML =
    '<tr><th>模型服务</th><th>成功应答</th><th>调用失败</th><th title="前面的服务已应答，未尝试此服务">未轮到调用</th><th title="该次调用跳过了此服务">已跳过</th></tr>'
    + rs.map(r=>"<tr><td>" + esc(r.name)
      // 链里出现了配置之外的 provider 时它照样进这张表 —— 丢掉它等于让一个
      // 没人登记过的调用路径在统计上不存在。标出来,别藏。
      // 「表外」= 这一级出现在某次调用的链里,但不在当前配置的链里。
      // ⚠ 这一列在账本干净的时候是空的,看起来像个没用的字段 —— 别因此删掉它。
      // 它的价值只在「有人跑出了一条没人登记过的链」那一刻兑现,而那种时候
      // 页面上没有别的东西会说话:那条链的调用会安安静静地被算进总数里。
      + (r.in_known_chain === false ? ' <span class="faint">(表外)</span>' : "") + "</td>"
      + '<td class="' + (r.served ? "" : "z") + '">' + lnum(r.served) + "</td>"
      + '<td class="' + (r.failed ? "f" : "z") + '">' + lnum(r.failed) + "</td>"
      + '<td class="' + (r.not_reached ? "" : "z") + '">' + lnum(r.not_reached) + "</td>"
      + '<td class="' + (r.skipped ? "" : "z") + '">' + lnum(r.skipped) + "</td></tr>").join("");
  $("lrnote").textContent = rs.length ? "" : "无记录";

  // 上面那张表的每一个数,都建在「chain[attempts-1] 就是应答那一级」这条判据上。
  // 判据一旦不成立,表照样画得很漂亮 —— 所以这里必须能看见反例数不是零。
  // 两种坏法不是一回事,措辞也不能混。**自相矛盾**是判据被证伪了,上表整个不可信;
  // **无法判定**只是那几条记录本身缺字段,剩下的数照样成立。
  // 早先这里把两者合成一句「这几列的数不能当真」,于是 0 条矛盾 + 35 条缺字段
  // 被报成了一条红色警报 —— 一个会为无害情况尖叫的指示灯,和一个坏掉的指示灯,
  // 在被忽略这件事上是一样的。
  const v = LLM.verify || {};
  const bad = v.contradictions || 0;
  const meh = v.unusable || 0;
  $("lrverify").innerHTML = v.checked == null ? "" :
    '<div class="l-verify' + (bad ? " bad" : "") + '">'
    + (bad
       ? "已核对 " + lnum(v.checked) + " 条，其中 " + lnum(bad)
         + " 条的应答服务与尝试顺序不符，上表统计可能不准确。"
       : "已核对 " + lnum(v.checked) + " 条，应答服务与尝试顺序一致。")
    + (meh ? '<span class="faint"> 另有 ' + lnum(meh)
             + " 条字段不全、无法参与核对,已排除在外。</span>" : "")
    + "</div>";
}

// ── 连续降级段 ──
function renderRuns(){
  const rs = LLM.runs || [];
  if(!rs.length){
    $("lstab").innerHTML = '<p class="faint" style="margin:0">没有达到统计阈值的连续失败或连续使用备用服务记录。</p>';
    $("lsnote").textContent = "";
    return;
  }
  // 整链失败和「只是降了一档」是两类事,不混排。
  // 混在一起按长度排,最狠的那种(一连串调用压根没有答案)会被更长、但没那么致命的
  // 降级段压到下面去 —— 实测账本里最长的降级段是 82 连,而 23 连的整链失败排第九。
  const dead = rs.filter(r=>r.total_failure).sort((a,b)=>b.length-a.length);
  const down = rs.filter(r=>!r.total_failure).sort((a,b)=>b.length-a.length);
  const max = Math.max.apply(null, rs.map(r=>r.length));
  $("lsnote").textContent = rs.length + " 段 · 最长 " + max + " 次"
    + (dead.length ? " · 其中 " + dead.length + " 段整链失败" : "");
  // 实测这个账本上有两千多段。全画出来这一屏会长到没人往下翻,而真正要看的
  // 永远是最长的那几段。所以每类只画前 LRUN_TOP 段,并且**把没画的那些数出来** ——
  // 一张悄悄截断的表和一张本来就这么短的表,看起来一模一样。
  $("lstab").innerHTML =
    (dead.length ? '<div class="l-run-group"><div class="l-sub"><b>调用失败</b>：未获得回答</div>'
                   + runRows(dead.slice(0, LRUN_TOP), max) + more(dead) + '</div>' : "")
    + (down.length ? '<div class="l-run-group"><div class="l-sub"><b>备用服务应答</b>：由调用顺序中靠后的服务回答</div>'
                     + runRows(down.slice(0, LRUN_TOP), max) + more(down) + '</div>' : "");
}

const LRUN_TOP = 12;
function more(list){
  const n = list.length - LRUN_TOP;
  if(n <= 0) return "";
  const covered = list.slice(LRUN_TOP).reduce((s, r)=>s + r.length, 0);
  return '<div class="faint l-more">另有 ' + lnum(n) + " 段较短的没画出来,合计 "
    + lnum(covered) + " 次调用。</div>";
}

function runRows(rs, max){
  return rs.map(r=>{
    const when = r.start_ts != null
      ? '<span class="when">' + timeTag(r.start_ts) + "</span>"
      : lwhen(null);
    // 整链失败的那一段里没有任何一级给出答案。判据只认后端给的布尔,不认 provider
    // 的值:整链失败在账本里是 null,而读侧为了让它能进计数器把它写成字符串 "NONE",
    // 于是页面上一度原样印出了四个字母 NONE —— 一个内部占位符漏到了人眼前。
    const dead = !!r.total_failure;
    // 「调用失败」这一组的标题已经说了没有答案,每行再印一个红色「整链失败」是同一句话说十二遍。
    // 这一组每行说的是**为什么**失败:那段里最常见的错误文本。
    if(dead){
      return '<div class="l-run jump" role="button" tabindex="0" data-jump="' + r.start_offset + '" title="看这一段的明细 · 起自第 ' + r.start_i + ' 行">'
        + '<span class="why' + (r.error ? "" : " unk") + '" title="' + esc(r.error || "这一段没有记下错误信息") + '">'
        + esc(r.error || "未记录错误信息") + "</span>"
        + '<span class="barw"><span class="bar dead" style="width:' + (r.length / max * 100).toFixed(1) + '%"></span></span>'
        + '<span class="len">' + r.length + " 连</span>" + when + "</div>";
    }
    const who = r.provider;
    // 看见一段「连着 82 次降级」之后,人接下来一定想问「那 82 次长什么样」。
    // 在这之前那是个死路:得自己记下行号,滚到下面那张表,再一页页翻过去。
    // 整段可点,点了把明细直接翻到那一段的第一行。
    // data-jump 收的是**列表下标**(能直接喂给分页),而 title 里给人看的是
    // **物理行号**(和明细表 # 列同一套)。账本里有空行,两者会差几位,
    // 所以这两个数必须各取各的字段,不能图省事用同一个。
    return '<div class="l-run jump" role="button" tabindex="0" data-jump="' + r.start_offset + '" title="看这一段的明细 · 起自第 ' + r.start_i + ' 行">'
      + '<span class="who">' + esc(who) + "</span>"
      + (r.error ? '<span class="faint" title="' + esc(r.error) + '">' + esc(r.error.slice(0, 40)) + "</span>" : "")
      + '<span class="barw"><span class="bar" style="width:' + (r.length / max * 100).toFixed(1) + '%"></span></span>'
      + '<span class="len">' + r.length + " 连</span>" + when + "</div>";
  }).join("");
}

// ── 明细 ──
async function loadCalls(){
  const request=++LM_REQUEST;
  $("lmnote").textContent='读取中';
  // 每次查询都是筛选变了的时候:在这里记一次,清除筛选、跳转清筛选也都经过这里。
  lmSavePrefs();
  const q = "?offset=" + LMQ.offset + "&limit=" + LMQ.limit
    + (LMQ.order === "desc" ? "&order=desc" : "")
    + (LMQ.provider ? "&provider=" + encodeURIComponent(LMQ.provider) : "")
    + (LMQ.ok !== "" ? "&ok=" + LMQ.ok : "")
    + (LMQ.q ? "&q=" + encodeURIComponent(LMQ.q) : "")
    + (LMQ.caller ? "&caller=" + encodeURIComponent(LMQ.caller) : "");
  try{
    const r = await api("/api/llmcall/calls" + q);
    if(request!==LM_REQUEST) return;
    LMROWS = r.rows || []; LMTOTAL = r.total || 0; LMERR = null;
    $("lmnote").textContent = "";
  }catch(e){ if(request!==LM_REQUEST) return; LMROWS = []; LMTOTAL = 0; LMERR = e; $("lmnote").textContent = ""; }
  renderCalls();
}
const lmPages = () => Math.max(1, Math.ceil(LMTOTAL / LMQ.limit));
function lmGoPage(n){
  const page = Math.max(1, Math.min(lmPages(), Math.floor(Number(n)) || 1));
  const offset = (page - 1) * LMQ.limit;
  if(offset === LMQ.offset) return false;
  LMQ.offset = offset; LMOPEN = null;
  loadCalls();
  return true;
}

function renderCalls(){
  if(!LMROWS) return;
  const sel = $("lmprov");
  if(sel.options.length <= 1 && LLM && LLM.rungs){
    sel.insertAdjacentHTML("beforeend",
      LLM.rungs.map(r=>'<option value="' + esc(r.name) + '">' + esc(r.name) + "</option>").join("")
      + '<option value="NONE">整链失败</option>');
    sel.value = LMQ.provider;
  }
  // 调用方清单来自后端对整个账本的统计,不是当前这 50 行 ——
  // 从一页记录里凑出来的下拉框,会让「这个调用方今天没出现」变成「这个调用方不存在」。
  const cs = $("lmcaller");
  if(cs.options.length <= 1 && LLM && LLM.callers && LLM.callers.length){
    cs.insertAdjacentHTML("beforeend",
      LLM.callers.map(c=>'<option value="' + esc(c.name) + '">' + esc(c.name)
        + " (" + lnum(c.calls) + ")</option>").join(""));
    cs.value = LMQ.caller;
  }
  const rows = LMROWS.map(r=>{
    const open = LMOPEN === r.i;
    const who = r.provider == null
      ? '<td class="who none">整链失败</td>'
      : '<td class="who">' + esc(r.provider) + "</td>";
    // 三种「不知道是谁调的」要分开:字段不存在 = 这条记录早于 caller 功能;
    // 字段是 null = 推断失败(嵌入式调用、REPL);有值就是有值。
    // 把前两种都写成「未知」,就把「还没开始记」和「记了但推断不出来」焊死了。
    const caller = !("caller" in r) ? '<td class="unk">—</td>'
      : r.caller == null ? '<td class="unk">推断不出</td>'
      : '<td class="who">' + esc(r.caller) + "</td>";
    // 每行自己说成没成:以前一次失败的调用和成功的长得一样,只有应答服务为空时才看得出来。
    const result = '<td class="res">' + (r.ok ? statusBadge("成功", "ok") : statusBadge("失败", "bad")) + "</td>";
    // 行首的三角和 aria-expanded:一眼看得出这一行点了能展开。
    let tr = '<tr class="lrow' + (open ? " open" : "") + (r.ok ? "" : " failed") + (LMJUMP === r.i ? " jumped" : "")
      + '" data-i="' + r.i + '" aria-expanded="' + open + '" title="' + (open ? "收起明细" : "展开明细") + '">'
      + '<td class="no"><span class="caret" aria-hidden="true">' + (open ? "▼" : "▶") + "</span>" + r.i + "</td>"
      + "<td>" + lwhen(r.ts) + "</td>" + result + caller + who
      + '<td class="sk">' + (r.skipped && r.skipped.length ? esc(r.skipped.join(",")) : "—") + "</td>"
      + "<td>" + esc(r.mode || "") + "</td>"
      + '<td class="r">' + lnum(r.prompt_chars) + "</td>"
      + '<td class="r">' + lnum(r.reply_chars) + "</td>"
      + '<td class="r">' + (r.ms == null ? '<span class="faint">—</span>' : lnum(r.ms)) + "</td>"
      + '<td class="r">' + r.attempts + "</td></tr>";
    // 明细可能比一屏还高:收起按钮钉在明细右上角,不用回头去找那一行。
    if(open) tr += '<tr><td class="det" colspan="' + LM_COLS + '"><div class="det-bar"><button type="button" class="mini det-close" data-call-close title="收起明细"><svg class="ic" aria-hidden="true"><use href="#i-up"/></svg>收起</button></div>' + detailHTML(r) + "</td></tr>";
    return tr;
  }).join("");
  const head = '<tr><th>行号</th><th>时间</th><th>结果</th><th>调用方</th><th>应答服务</th><th>已跳过</th><th>模式</th>'
    + '<th class="r">输入字符</th><th class="r">回复字符</th><th class="r">耗时（ms）</th><th class="r">尝试次数</th></tr>';
  // 空结果必须说出**为什么**空。「搜错误文本」+「只看成功」是一个天然的空集:
  // 成功的调用根本没有错误文本。不说破的话,一个用对了工具的人会以为工具坏了,
  // 而这和工具真的坏了在屏幕上是同一句话。
  const filtered = LMQ.provider || LMQ.ok !== "" || LMQ.q || LMQ.caller ? "llm" : "";
  const empty = LMQ.q && LMQ.ok === "1"
    ? "成功记录没有错误信息。请清除搜索词，或将结果筛选改为「全部结果」。"
    : LMQ.q ? "没有哪条调用的错误文本里含「" + LMQ.q + "」。"
    : filtered ? "没有符合筛选条件的调用记录。" : "账本里还没有调用记录。";
  $("lmtab").innerHTML = head
    + (LMERR ? '<tr><td colspan="' + LM_COLS + '">' + errorBlock("调用记录", LMERR, "data-lm-retry") + "</td></tr>"
       : rows || '<tr><td colspan="' + LM_COLS + '">' + emptyBlock(empty, {filtered}) + "</td></tr>");

  const from = LMTOTAL ? LMQ.offset + 1 : 0;
  const to = Math.min(LMQ.offset + LMQ.limit, LMTOTAL);
  const pages = lmPages(), page = Math.floor(LMQ.offset / LMQ.limit) + 1;
  const atFirst = LMQ.offset <= 0 ? "已是第一页" : "", atLast = to >= LMTOTAL ? "已是最后一页" : "";
  // 页码框重画后焦点会掉:人刚在框里按了 Enter,焦点应该还在框里,接着能改下一个页码。
  const typing = document.activeElement && document.activeElement.id === "lmpgno";
  const txt = (id, label, reason) => '<button type="button" class="mini" id="' + id + '"' + (reason ? " disabled" : "")
    + ' title="' + esc(reason ? disabledTitle(label, reason) : label) + '">' + label + "</button>";
  $("lmpage").innerHTML = LMERR ? "" :
    txt("lmfirst", "首页", atFirst)
    + ibtn('i-left','上一页','id="lmprev"','',atFirst)
    + '<label class="l-pgno">第 <input type="number" id="lmpgno" min="1" max="' + pages + '" value="' + page + '"'
    + ' aria-label="页码，按 Enter 跳转" title="输入页码后按 Enter 跳转"' + (pages <= 1 ? " disabled" : "") + '> / ' + lnum(pages) + " 页</label>"
    + ibtn('i-right','下一页','id="lmnext"','',atLast)
    + txt("lmlast", "末页", atLast)
    + "<span>" + lnum(from) + "–" + lnum(to) + " / 共 " + lnum(LMTOTAL) + " 条</span>";
  if(typing){ try{ $("lmpgno").focus(); }catch(e){} }
}
const LM_COLS = 11;

function detailHTML(r){
  const b = LMBODY[r.i];
  let s = "<dl>"
    + "<dt>调用顺序</dt><dd>" + esc((r.chain || []).join(" → ")) + "</dd>"
    + "<dt>联网</dt><dd>" + (r.web ? "是" : "否") + "</dd>"
    + "<dt>结果</dt><dd>" + (r.ok ? "成功" : "失败") + "</dd>"
    + (r.error ? "<dt>错误</dt><dd>" + esc(r.error) + "</dd>" : "")
    + (r.id ? "<dt>正文 id</dt><dd>" + esc(r.id) + "</dd>" : "")
    + "</dl>";
  // 逐级明细。顶层那个 error 记的是**最后一级**的失败原因,而最后一级往往正是
  // 因为预算被前面烧光才没真的试过 —— 于是一整段故障的唯一一句话,说的是那个
  // 什么都没做的那一级。这张小表就是为了把真正的病因摆出来。
  if(r.attempts_detail && r.attempts_detail.length){
    s += '<table class="l-att">'
      + r.attempts_detail.map(a=>
          "<tr><td>" + esc(a.name) + "</td>"
          + '<td class="' + (a.ok ? "y" : "n") + '">' + (a.ok ? "成功" : "失败") + "</td>"
          + '<td class="r">' + (a.ms == null ? "—" : lnum(a.ms) + "ms") + "</td>"
          + "<td>" + esc(a.error || "") + "</td></tr>").join("")
      + "</table>";
  } else if(!r.ok){
    s += '<p class="faint" style="margin:6px 0 0">这条记录早于逐级明细,只留下了最后一级的原因。</p>';
  }
  if(b === undefined) s += '<p class="faint" style="margin:6px 0 0">正在取正文…</p>';
  else if(b === null || !b.available){
    // 「没开正文记录」「伴生仓没配」「过了保留期」「文件读不动」是四件不同的事,
    // 后端分成六种 reason_code 说明是哪一种。把它们合成一句「没有正文」,
    // 等于让一次配置错误看起来像一次正常的空。
    s += '<p class="faint" style="margin:6px 0 0">' + esc((b && b.reason) || "正文未记录。") + "</p>";
    // 「找不到」不说自己找过哪里,人只能靠猜。把走过的每一级和结论都摊开。
    // 「找不到」还要说「怎么办」。这个 fleet 的伴生仓约定是兄弟目录,
    // 而解析链只认家目录下那两个位置,所以一个建在别处的伴生仓必须靠一个环境变量被指到。
    // 不写这句的话,人会去重建一个已经建好的仓。
    if(b && b.reason_code === "uninitialised")
      s += '<p class="faint" style="margin:4px 0 0">伴生仓若建在别处,设 '
         + "<code>LLMCALL_CONFIG</code> 指向它(正文会落到它下面的 "
         + "<code>data/bodies/</code>),或者用 <code>TASK_CONSOLE_LLMCALL_BODIES</code> "
         + "直接指定正文目录。正文记录本身还要 <code>LLMCALL_RECORD_BODIES=1</code> 才会开。</p>";
    if(b && b.tried && b.tried.length)
      s += '<table class="l-att">' + b.tried.map(t=>
        "<tr><td>" + esc(t.source || "") + "</td><td>" + esc(t.path || "") + "</td>"
        + '<td class="' + (t.is_dir ? "y" : "n") + '">' + (t.is_dir ? "在" : "不在") + "</td></tr>"
      ).join("") + "</table>";
  }
  else s += "<pre>" + esc(b.prompt || "") + "</pre>"
    + (b.reply ? "<pre>" + esc(b.reply) + "</pre>" : "")
    // 一段被悄悄砍掉一半的 prompt,和一段本来就那么长的 prompt,在页面上长得一样。
    + (b.truncated ? '<p class="faint" style="margin:4px 0 0">已截断显示 · 原始长度 '
        + lnum(b.prompt_chars) + " / " + lnum(b.reply_chars) + " 字符</p>" : "");
  return s;
}

// 从一段连续降级跳到它的明细。
// 偏移必须先把筛选清掉:`offset` 是**筛选之后**的序号,而段的起点是**绝对行号**。
// 带着筛选去翻页会落在一个看起来很像、其实完全不相干的位置上,而且不会报错。
// 参数是**列表下标**(后端的 start_offset),不是行号 —— 直接就是 page 的 offset,
// 不许再减一。这里原来写着 `startIndex - 1`,是当初以为传进来的是 1 基行号时留下的,
// 于是每次跳转都稳定地落在段首的前一条上。差一条看起来非常像「对的」:
// 落点仍在那段附近,屏幕上第一行甚至常常长得和段内的记录一样。
// 最新在前时,段首在倒序列表里的位置是 total-1-start_offset。total 要现问一次不带筛选的总数:
// 账本一直在长,用上一次查询(可能还带着筛选)留下的总数换算,会落到几行之外。
// 段首放在那一页的最后一行:倒序页里它上面的几行正是这一段后面的调用,下面的是段前的,不相干。
async function lmJumpOffset(startOffset){
  if(LMQ.order !== "desc") return Math.max(0, startOffset);
  const r = await api("/api/llmcall/calls?offset=0&limit=1");
  const pos = (r.total || 0) - 1 - startOffset;
  return Math.max(0, pos - (LMQ.limit - 1));
}
function jumpToCalls(startOffset){
  const had = LMQ.provider || LMQ.ok !== "" || LMQ.q || LMQ.caller;
  LMQ.provider = ""; LMQ.ok = ""; LMQ.q = ""; LMQ.caller = "";
  $("lmprov").value = ""; $("lmok").value = ""; $("lmq").value = ""; $("lmcaller").value = "";
  LMOPEN = null;
  const seg = ((LLM && LLM.runs) || []).find(r=>r.start_offset === startOffset);
  LMJUMP = seg ? seg.start_i : null;
  return lmJumpOffset(startOffset).then(offset=>{ LMQ.offset = offset; return loadCalls(); }, e=>{
    // 问不到总数就退回正序翻过去,不让跳转静默地落在一个算错的位置上。
    LMQ.order = "asc"; LMQ.offset = Math.max(0, startOffset);
    if($("lmorder")) $("lmorder").value = "asc";
    toast("读不到调用总数，已改为最早在前显示这一段：" + e.message, "warn");
    return loadCalls();
  }).then(()=>{
    const row = LMJUMP != null && document.querySelector ? document.querySelector('#lmtab tr.lrow[data-i="' + LMJUMP + '"]') : null;
    (row || $("lcalls")).scrollIntoView({block: row ? "center" : "start"});
    if(had) toast("已清掉明细的筛选条件,否则跳过去的位置对不上");
  });
}

// 收起按钮和 Esc 共用。收起之后把原来那一行滚回眼前:明细可能把它顶出了屏幕。
function closeCall(){
  const i = LMOPEN;
  if(i == null) return false;
  LMOPEN = null;
  renderCalls();
  const row = document.querySelector('#lmtab tr.lrow[data-i="' + i + '"]');
  if(row && row.scrollIntoView) row.scrollIntoView({block: "nearest"});
  return true;
}

// 敲一个字就发一次请求,在一个十一万行的账本上是每次全表扫。等人停手再发。
// Enter 是「我打完了」:不再等防抖,立刻查。
let LMQT = null;
function callSearchInput(value){
  clearTimeout(LMQT);
  LMQT = setTimeout(()=>{ LMQT = null; LMQ.q = value; LMQ.offset = 0; loadCalls(); }, 260);
}
function commitCallSearch(){
  clearTimeout(LMQT); LMQT = null;
  LMQ.q = $("lmq").value; LMQ.offset = 0;
  return loadCalls();
}

async function openCall(i){
  LMOPEN = (LMOPEN === i) ? null : i;
  renderCalls();
  if(LMOPEN == null || LMBODY[i] !== undefined) return;
  try{ LMBODY[i] = await api("/api/llmcall/call/" + i); }
  catch(e){ LMBODY[i] = {available:false, reason:"取正文失败:" + e.message}; }
  if(LMOPEN === i) renderCalls();
}

// 路由就是 location.hash,没有路由库。一把分区、一个哈希,引一个库只会多一份要维护的东西。
// (这里原来写着「五个分区」。分区数是会变的,而散文里的数字不会跟着变 : 上一次加分区时
//  它就已经过期了。真值永远只有下面那个 VIEWS 数组,别在注释里复写它的长度。)
// hash 还顺带给了两个白送的好处:刷新回到原来那一屏,以及可以把某一屏发给自己。

// ── 模型调用这一屏的挂点 ──(从 events.js 搬来,原因见 tasks.js 的 startTasksPage 上方)
let LCFROM = null;
function startCalls(){
  $("lcsave").addEventListener("click", lcSave);
  $("lcreset").addEventListener("click", ()=>{ LCDRAFT = null; renderChain(); });
  // 顺序不是筛选:清除筛选不动它,所以它不进 RESET_FILTER_COUNTS,挂在筛选前面。
  $("lmprov").insertAdjacentHTML("beforebegin", '<select id="lmorder" class="l-sel" title="调用记录的排列顺序" aria-label="调用记录的排列顺序">'
    + '<option value="desc">最新在前</option><option value="asc">最早在前</option></select>');
  $("lmorder").value = LMQ.order;
  $("lmorder").addEventListener("change", e=>{ LMQ.order = e.target.value === "asc" ? "asc" : "desc"; LMQ.offset = 0; LMOPEN = null; LMJUMP = null; loadCalls(); });
  $("lmok").value = LMQ.ok;
  $("lmprov").addEventListener("change", e=>{ LMQ.provider = e.target.value; LMQ.offset = 0; LMJUMP = null; loadCalls(); });
  $("lmok").addEventListener("change", e=>{ LMQ.ok = e.target.value; LMQ.offset = 0; LMJUMP = null; loadCalls(); });
  $("lmcaller").addEventListener("change", e=>{ LMQ.caller = e.target.value; LMQ.offset = 0; LMJUMP = null; loadCalls(); });
  $("lmq").addEventListener("input", e=>callSearchInput(e.target.value));
  // 上一页 / 下一页由 events.js 的总监听接;首页、末页、页码框和重试是这一屏新加的,挂在这里。
  $("lmpage").addEventListener("click", e=>{
    const b = e.target.closest("button");
    if(!b || b.disabled) return;
    if(b.id === "lmfirst") lmGoPage(1);
    else if(b.id === "lmlast") lmGoPage(lmPages());
  });
  $("lmpage").addEventListener("keydown", e=>{
    if(e.target.id !== "lmpgno" || e.key !== "Enter" || e.isComposing || e.repeat) return;
    e.preventDefault();
    // 页码超出范围时按首末页算;算下来就是当前页的话不发请求,框里改回当前页码,别留着一个没生效的数。
    if(!lmGoPage(e.target.value)) e.target.value = Math.floor(LMQ.offset / LMQ.limit) + 1;
  });
  $("lcalls").addEventListener("click", e=>{ if(e.target.closest("[data-lm-retry]")) loadCalls(); });
  $("luse").addEventListener("click", e=>{ if(e.target.closest("[data-llm-retry]")) loadLLM(); });
  // 拖拽排序。上下箭头按钮是同一件事的键盘可达版本,两条路都留着:
  // 只有拖拽的话,这个控件对键盘用户不存在。
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
}
