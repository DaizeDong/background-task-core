// Classic script module; loaded in app.js dependency order.
let LLM=null, LCDRAFT=null, LMROWS=null, LMTOTAL=0, LMOPEN=null, LMBODY={};
let LM_REQUEST=0;
const LMQ = {offset:0, limit:20, provider:"", ok:"", q:"", caller:""};

const lnum = n => n==null ? "—" : Number(n).toLocaleString("en-US");
// 没量到 / 量到零 / 有值,三种要长得不一样。这个函数只负责前两种的区分。
const lpct = f => f==null ? '<span class="faint">未计</span>' : (f*100).toFixed(0)+"%";
function lts(t){
  if(t==null) return null;
  const d = new Date(t*1000);
  const p = n => String(n).padStart(2,"0");
  return d.getFullYear()+"-"+p(d.getMonth()+1)+"-"+p(d.getDate())+" "+p(d.getHours())+":"+p(d.getMinutes());
}

async function loadLLM(){
  try{ LLM = await api("/api/llmcall"); $("lcnote").textContent=""; }
  catch(e){ LLM = {error: e.message}; }
  LCDRAFT = null;
  renderLLM();
  await loadCalls();
}

function renderLLM(){
  if(!LLM) return;
  if(LLM.error){
    $("lcnote").textContent = LLM.error;
    $("lclist").innerHTML = "";
    $("lwins").innerHTML = '<div class="l-win l-unk"><h4>用量</h4><div class="big">读不到</div>'
      + '<div class="sub">'+esc(LLM.error)+'</div></div>';
    $("lrtab").innerHTML = ""; $("lstab").innerHTML = "";
    return;
  }
  renderChain(); renderWins(); renderRungs(); renderRuns(); llmBadge();
}

// 侧栏徽章。这一屏平时不需要人盯着,所以徽章要只在**有事**的时候亮,
// 而「有事」在这里有两种,数的是同一个徽章但理由不同:
//   近 7 日里有整链失败(四级全挂,那次调用没有答案);
//   或者链的顺序被一个看不见的环境变量压着,页面上的设置不作数。
// 第二种不加进计数而是单独把徽章标红:它不是「几件事」,是「这一屏在骗你」。
function llmBadge(){
  const w = (LLM.windows || [])[1] || {};
  const c = LLM.chain || {};
  const bad = (LLM.ledger || {}).malformed || 0;
  const n = (w.failed || 0); // Undated/cumulative parse errors belong in ledger diagnostics.
  setBadge("llm", c.shadowed_by_env ? Math.max(n, 1) : n, true);
  const badge=$("bg-llm");
  if(badge){badge.textContent="7日";badge.title=`近 7 日失败 ${n} 次${c.shadowed_by_env ? "；路由被环境覆盖" : ""}`;badge.setAttribute("aria-label",badge.title);}
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
  $("lcnote").innerHTML = '<span class="lc-src '+esc(src)+'">'+esc(label)+'</span>';
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

  $("lclist").innerHTML = eff.map((p,i)=>
    '<li draggable="true" data-i="'+i+'"><span class="ord">'+(i+1)+'</span>'
    + '<span class="nm">'+esc(p)+'</span>'
    + '<span class="mv"><button data-mv="up" data-i="'+i+'" title="上移"'+(i===0?" disabled":"")+'>↑</button>'
    + '<button data-mv="dn" data-i="'+i+'" title="下移"'+(i===eff.length-1?" disabled":"")+'>↓</button></span></li>'
  ).join("");
  const dirty = LCDRAFT && JSON.stringify(LCDRAFT) !== JSON.stringify(c.effective || []);
  $("lcsave").disabled = !dirty || busy;
  $("lcsave").title=busy?'正在保存':dirty?'保存当前排列顺序':'先用上下箭头调整顺序';
  $("lcreset").disabled = !LCDRAFT;
  $("lcreset").title=LCDRAFT?'放弃未保存的排列修改':'当前没有未保存的修改';
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
      r.shadowed_by_env ? "bad" : "");
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
    const sub = blind
      ? (lnum(w.unstamped_excluded) + " 条历史记录没有时间戳,无法归入这个窗口")
      : ('<span class="ok">' + lnum(w.ok) + " 成功</span> · "
         + (w.failed ? '<span class="bad">' + lnum(w.failed) + " 失败</span>" : "0 失败")
         + " · 低成本服务占比 " + lpct(w.cheap_share)
         + (w.avg_ms != null ? " · 平均 " + lnum(Math.round(w.avg_ms)) + "ms" : "")
         + (w.unstamped_excluded ? " · 另有 " + lnum(w.unstamped_excluded) + " 条无时间戳未计入" : ""));
    return '<div class="' + cls + '"><h4>' + esc(w.label) + "</h4>"
      + '<div class="big">' + esc(big) + "</div>"
      + '<div class="sub">' + sub + "</div></div>";
  }).join("");

  const L = LLM.ledger || {};
  $("lledger").innerHTML = "已读取 " + kb(L.bytes)
    + " · " + lnum(L.parsed) + " 条可解析"
    + (L.malformed ? ' · <span class="bad">' + lnum(L.malformed) + " 条坏行</span>" : "")
    + " · 带时间戳 " + lnum(L.stamped) + " 条 / 无时间戳 " + lnum(L.unstamped) + " 条"
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
    (dead.length ? '<div class="l-sub"><b>调用失败</b>：未获得回答</div>'
                   + runRows(dead.slice(0, LRUN_TOP), max) + more(dead) : "")
    + (down.length ? '<div class="l-sub"><b>备用服务应答</b>：由调用顺序中靠后的服务回答</div>'
                     + runRows(down.slice(0, LRUN_TOP), max) + more(down) : "");
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
      ? '<span class="when">' + esc(lts(r.start_ts)) + "</span>"
      : '<span class="when unk">无时间戳</span>';
    // 整链失败的那一段里没有任何一级给出答案。判据只认后端给的布尔,不认 provider
    // 的值:整链失败在账本里是 null,而读侧为了让它能进计数器把它写成字符串 "NONE",
    // 于是页面上一度原样印出了四个字母 NONE —— 一个内部占位符漏到了人眼前。
    const dead = !!r.total_failure;
    const who = dead ? "整链失败" : r.provider;
    // 看见一段「连着 82 次降级」之后,人接下来一定想问「那 82 次长什么样」。
    // 在这之前那是个死路:得自己记下行号,滚到下面那张表,再一页页翻过去。
    // 整段可点,点了把明细直接翻到那一段的第一行。
    // data-jump 收的是**列表下标**(能直接喂给分页),而 title 里给人看的是
    // **物理行号**(和明细表 # 列同一套)。账本里有空行,两者会差几位,
    // 所以这两个数必须各取各的字段,不能图省事用同一个。
    return '<div class="l-run jump" role="button" tabindex="0" data-jump="' + r.start_offset + '" title="看这一段的明细 · 起自第 ' + r.start_i + ' 行">'
      + '<span class="who' + (dead ? " none" : "") + '">'
      + esc(who) + "</span>"
      + (r.error ? '<span class="faint" title="' + esc(r.error) + '">' + esc(r.error.slice(0, 40)) + "</span>" : "")
      + '<span class="barw"><span class="bar' + (r.total_failure ? " dead" : "")
      + '" style="width:' + (r.length / max * 100).toFixed(1) + '%"></span></span>'
      + '<span class="len">' + r.length + " 连</span>" + when + "</div>";
  }).join("");
}

// ── 明细 ──
async function loadCalls(){
  const request=++LM_REQUEST;
  $("lmnote").textContent='读取中';
  const q = "?offset=" + LMQ.offset + "&limit=" + LMQ.limit
    + (LMQ.provider ? "&provider=" + encodeURIComponent(LMQ.provider) : "")
    + (LMQ.ok !== "" ? "&ok=" + LMQ.ok : "")
    + (LMQ.q ? "&q=" + encodeURIComponent(LMQ.q) : "")
    + (LMQ.caller ? "&caller=" + encodeURIComponent(LMQ.caller) : "");
  try{
    const r = await api("/api/llmcall/calls" + q);
    if(request!==LM_REQUEST) return;
    LMROWS = r.rows || []; LMTOTAL = r.total || 0;
    $("lmnote").textContent = "";
  }catch(e){ if(request!==LM_REQUEST) return; LMROWS = []; LMTOTAL = 0; $("lmnote").textContent = '读取失败：'+e.message; }
  renderCalls();
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
    const when = r.ts != null
      ? "<td>" + esc(lts(r.ts)) + "</td>"
      : '<td class="unk">无时间戳</td>';
    let tr = '<tr class="lrow' + (open ? " open" : "") + '" data-i="' + r.i + '">'
      + "<td>" + r.i + "</td>" + when + caller + who
      + '<td class="sk">' + (r.skipped && r.skipped.length ? esc(r.skipped.join(",")) : "—") + "</td>"
      + "<td>" + esc(r.mode || "") + "</td>"
      + '<td class="r">' + lnum(r.prompt_chars) + "</td>"
      + '<td class="r">' + lnum(r.reply_chars) + "</td>"
      + '<td class="r">' + (r.ms == null ? '<span class="faint">—</span>' : lnum(r.ms)) + "</td>"
      + '<td class="r">' + r.attempts + "</td></tr>";
    if(open) tr += '<tr><td class="det" colspan="10">' + detailHTML(r) + "</td></tr>";
    return tr;
  }).join("");
  $("lmtab").innerHTML =
    '<tr><th>行号</th><th>时间</th><th>调用方</th><th>应答服务</th><th>已跳过</th><th>模式</th>'
    + '<th class="r">输入字符</th><th class="r">回复字符</th><th class="r">耗时（ms）</th><th class="r">尝试次数</th></tr>'
    // 空结果必须说出**为什么**空。「搜错误文本」+「只看成功」是一个天然的空集:
    // 成功的调用根本没有错误文本。不说破的话,一个用对了工具的人会以为工具坏了,
    // 而这和工具真的坏了在屏幕上是同一句话。
    + (rows || '<tr><td colspan="10" class="faint">'
       + (LMQ.q && LMQ.ok === "1"
          ? "成功记录没有错误信息。请清除搜索词，或将结果筛选改为「全部结果」。"
          : LMQ.q ? "没有哪条调用的错误文本里含「" + esc(LMQ.q) + "」。"
                  : "没有符合条件的记录。")
       + "</td></tr>");

  const from = LMTOTAL ? LMQ.offset + 1 : 0;
  const to = Math.min(LMQ.offset + LMQ.limit, LMTOTAL);
  $("lmpage").innerHTML =
    '<button class="mini" id="lmprev"' + (LMQ.offset <= 0 ? " disabled" : "") + ">上一页</button>"
    + '<button class="mini" id="lmnext"' + (to >= LMTOTAL ? " disabled" : "") + ">下一页</button>"
    + "<span>" + lnum(from) + "–" + lnum(to) + " / 共 " + lnum(LMTOTAL) + " 条</span>";
}

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
function jumpToCalls(startOffset){
  const had = LMQ.provider || LMQ.ok !== "" || LMQ.q || LMQ.caller;
  LMQ.provider = ""; LMQ.ok = ""; LMQ.q = ""; LMQ.caller = "";
  $("lmprov").value = ""; $("lmok").value = ""; $("lmq").value = ""; $("lmcaller").value = "";
  LMQ.offset = Math.max(0, startOffset);
  LMOPEN = null;
  loadCalls().then(()=>{
    $("lcalls").scrollIntoView({block: "start"});
    if(had) toast("已清掉明细的筛选条件,否则跳过去的位置对不上");
  });
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
