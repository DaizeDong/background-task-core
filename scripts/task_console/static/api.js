// Classic script module; loaded in app.js dependency order.
let TOKEN = document.querySelector('meta[name="console-token"]').content;
let API_TOKEN_REFRESH=null;
async function refreshConsoleToken(rejectedToken){
  if(TOKEN!==rejectedToken) return;
  if(API_TOKEN_REFRESH) return API_TOKEN_REFRESH;
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),10000);
  API_TOKEN_REFRESH=(async()=>{
    try{
      // The same Host and same-origin controls protect the initial page and this refresh.
      const response=await fetch('/',{cache:'no-store',credentials:'same-origin',signal:controller.signal});
      if(!response.ok) throw new Error(`HTTP ${response.status}`);
      const page=new DOMParser().parseFromString(await response.text(),'text/html');
      const token=page.querySelector('meta[name="console-token"]')?.content;
      if(!token) throw new Error('missing token');
      TOKEN=token;document.querySelector('meta[name="console-token"]').content=token;
    }catch(cause){
      throw Object.assign(new Error('页面认证已失效，自动恢复失败；请刷新页面后重试'),
        {status:403,payload:{error:'页面认证已失效，自动恢复失败；请刷新页面后重试'},requestRejected:true,cause});
    }finally{clearTimeout(timer);}
  })();
  try{await API_TOKEN_REFRESH;}finally{API_TOKEN_REFRESH=null;}
}
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const $ = id => document.getElementById(id);

// Presentation only: callers supply the owner's label and its display tone.
const componentTone=state=>({healthy:'ok',unhealthy:'bad',degraded:'warn',failed:'bad',success:'ok',ok:'ok',running:'active',completed:'ok'}[state] || 'idle');
function statusBadge(label,tone='idle',symbol,extraClass=''){
  const symbols={ok:'✓',bad:'×',warn:'!',active:'▶',pending:'◷',idle:'?',muted:'—'};
  if(!Object.hasOwn(symbols,tone)) tone='idle';
  return `<span class="status-chip ${tone} ${esc(extraClass)}"><span class="status-symbol" aria-hidden="true">${esc(symbol || symbols[tone])}</span><span>${esc(label)}</span></span>`;
}

// 右下角的提示条。四种语气:ok 成功、info 说明、warn 要留意、bad 出错。
// 成功 3 秒自己走;出错一直留着,直到人点掉:以前出错 9 秒就消失,而它恰恰是最需要读完的那条,
// 长原因常常还没读完就没了。鼠标停在上面、或者焦点在里面时暂停计时,移开再重新计。
// 点提示本身或它的 ✕ 都能关;正选着里面的文字时点一下不关,好让人把错误原因复制走。
// 同时最多三条,新来的先挤掉最旧的非出错提示。它不进 Esc 那一套:Esc 只管对话框和菜单。
const TOAST_LIMIT=3, TOAST_LIFE={ok:3000,info:5000,warn:8000,bad:0};
function toast(message,tone){
  const box=$("toast");
  tone=Object.hasOwn(TOAST_LIFE,tone)?tone:"info";
  const item=document.createElement("div");
  // 测试用的假 DOM 没有事件和子节点,这时什么都不做,也不留下定时器。
  if(!box?.appendChild || typeof item?.addEventListener!=="function") return null;
  item.className="toast-item "+tone;
  item.innerHTML=`<span class="toast-text"></span><button type="button" class="icon-only toast-x" title="关闭提示" aria-label="关闭提示"><svg class="ic" aria-hidden="true"><use href="#i-close"/></svg></button>`;
  item.querySelector(".toast-text").textContent=String(message ?? "");
  let timer=null;
  const close=()=>{clearTimeout(timer);item.remove();};
  const arm=()=>{clearTimeout(timer);if(TOAST_LIFE[tone]) timer=setTimeout(close,TOAST_LIFE[tone]);};
  item.addEventListener("click",event=>{
    if(!event.target.closest?.(".toast-x") && String(window.getSelection?.() || "")) return;
    close();
  });
  item.addEventListener("mouseenter",()=>clearTimeout(timer));
  item.addEventListener("mouseleave",()=>{if(!item.contains(document.activeElement)) arm();});
  item.addEventListener("focusin",()=>clearTimeout(timer));
  item.addEventListener("focusout",event=>{if(!item.contains(event.relatedTarget) && !item.matches(":hover")) arm();});
  box.appendChild(item);
  const items=[...box.children];
  while(items.length>TOAST_LIMIT){
    const drop=items.find(node=>!node.classList.contains("bad")) || items[0];
    items.splice(items.indexOf(drop),1);drop.remove();
  }
  arm();
  return item;
}

let API_SEQUENCE=0;
const API_READS=new Map();
const API_PENDING=new Map();
const API_LATEST_READS=new Map(), API_ACTIVE_READS=new Map();
let API_READ_EPOCH=0;
function apiError(payload,status){
  const detail=payload?.error;
  const code=detail?.code || payload?.code;
  const message=typeof detail==='string'?detail:payload?.message || payload?.reason || detail?.message || detail?.code;
  return message ? String(message)+(code && message!==code?` (${code})`:'') : `HTTP ${status}`;
}
function api(p,o){
  const options=o || {}, method=(options.method || 'GET').toUpperCase(), read=method==='GET';
  // inspect:true 标的是「借 POST 发出的读」,比如看一眼 git status:它不改任何东西,
  // 所以不记进「最近操作」、不锁全页的写按钮、也不让读缓存失效。
  // 只读预览照样拒绝它:那边的服务一律不收 POST,提前说清楚比等一个 405 好。
  const inspect=!read && options.inspect===true;
  if(!read && typeof ConsoleActions!=='undefined' && ConsoleActions.readOnly) return Promise.reject(new Error(ConsoleActions.reason));
  const epoch=API_READ_EPOCH, identity=JSON.stringify([p,{...options,method}]);
  const key=JSON.stringify([read?epoch:null,identity]);
  if(!options.signal && API_PENDING.has(key)) return API_PENDING.get(key);
  if(!read && !inspect) API_READ_EPOCH++;
  const sequence=++API_SEQUENCE;
  // 侧栏徽章靠这份记录分辨「还在读」和「读不到」,所以每记一次就告诉它(navigation.js 的 noteRead)。
  const record=state=>{if(read && (API_READS.get(p)?.sequence || 0)<=sequence){
    API_READS.set(p,{path:p,sequence,observedAt:new Date().toISOString(),...state});
    if(typeof noteRead==='function') noteRead(p,state);
  }};
  record({pending:true});
  const operation=!read && !inspect && typeof ConsoleActions!=='undefined' ? ConsoleActions.begin?.(p,options) : null;
  const controller=!options.signal && typeof AbortController!=='undefined' ? new AbortController() : null;
  const timer=controller ? setTimeout(()=>controller.abort(),read?90000:360000) : null;
  if(read) API_ACTIVE_READS.set(identity,(API_ACTIVE_READS.get(identity)||0)+1);
  const request=(async()=>{
    await Promise.resolve(); // Publish the pending entry even if fetch throws synchronously.
    try{
      let r,j;
      for(let attempt=0;attempt<2;attempt++){
        const sentToken=TOKEN;
        r=await fetch(p,{...options,method,signal:options.signal || controller?.signal,
          headers:{...options.headers,"X-Console-Token":sentToken,"Content-Type":"application/json"}});
        try{j=await r.json();}catch(cause){throw new Error(`服务响应无法解析 (HTTP ${r.status})`);}
        // A bad-token response is issued before dispatch, so this one replay is safe.
        // Network failures and all other responses must retain their original outcome.
        if(attempt===0 && r.status===403 && j?.error==='bad token') await refreshConsoleToken(sentToken);
        else break;
      }
      if(read && epoch!==API_READ_EPOCH && !options.signal){
        const latest=API_LATEST_READS.get(identity);
        return await (latest?.epoch===API_READ_EPOCH ? latest.request : api(p,options));
      }
      if(!r.ok){
        if(r.status===403 && j?.error==='bad token') j={...j,error:'页面认证已失效，请刷新页面后重试'};
        throw Object.assign(new Error(apiError(j,r.status)),{payload:j,status:r.status,
          requestRejected:[400,403,404,405].includes(r.status)});
      }
      record({pending:false,error:j?.error?apiError(j,r.status):(j?.available===false?j.reason || '不可用':null)});
      if(operation) ConsoleActions.finish(operation,j);
      return j;
    }catch(error){
      if(error.name==='AbortError') error=new Error(read?'读取超时，请重试':'等待操作结果超时，结果尚未确认；请先刷新核对');
      record({pending:false,error:error.message});
      if(operation) ConsoleActions.finish(operation,null,error);
      throw error;
    }finally{
      if(timer!==null) clearTimeout(timer);
      if(!read && !inspect) API_READ_EPOCH++;
      if(API_PENDING.get(key)===request) API_PENDING.delete(key);
      if(read){
        const remaining=API_ACTIVE_READS.get(identity)-1;
        if(remaining) API_ACTIVE_READS.set(identity,remaining);
        else {API_ACTIVE_READS.delete(identity);API_LATEST_READS.delete(identity);}
      }
    }
  })();
  if(!options.signal) API_PENDING.set(key,request);
  if(read) API_LATEST_READS.set(identity,{epoch,request});
  return request;
}

// 字节数。以前到 M 就停了,八个多 G 的目录显示成「8482.0M」,要人自己除一千。
const kb = n => n==null ? "-" : n<1024 ? n+"B" : n<1048576 ? (n/1024).toFixed(0)+"K"
  : n<1073741824 ? (n/1048576).toFixed(1)+"M" : (n/1073741824).toFixed(1)+"G";

// ================= 时间与数字的统一写法 =============================================
// 原来七个格式化各写各的:「2.3小时后」「27分后」「426.4h 前」「2026/10/4 02:39:00」,
// 同一个时刻在两页上要人自己换算。现在相对时间只有一种说法,悬停总能看到完整的年月日时分秒。
// 数字可以是毫秒、秒(小于 1e11 的数按秒算,后端的 time.time() 就是秒)、Date 或 ISO 字符串。
function timeValue(ts){
  if(ts==null || ts==="") return NaN;
  if(ts instanceof Date) return ts.getTime();
  if(typeof ts==="number") return Math.abs(ts)<1e11 ? ts*1000 : ts;
  return /^\d+(\.\d+)?$/.test(String(ts)) ? timeValue(Number(ts)) : Date.parse(ts);
}
const pad2=n=>String(n).padStart(2,"0");
// 悬停用的完整时刻:YYYY-MM-DD HH:mm:ss,本机时区。
function fullTime(ts){
  const t=timeValue(ts);
  if(!Number.isFinite(t)) return "";
  const d=new Date(t);
  return `${d.getFullYear()}-${pad2(d.getMonth()+1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}
// 相对时间:一分钟内「刚刚」(或调用方给的 soon,比如「即将运行」),一小时内「N 分钟前/后」,
// 48 小时内「N 小时前/后」(整数,不再有 2.3 小时),再往前一个月内「N 天前」,
// 其余写日期「MM-DD HH:mm」,不是今年再带上年份。relative:false 时直接写日期。
function fmtTime(ts,{relative=true,now=Date.now(),soon=""}={}){
  const t=timeValue(ts);
  if(!Number.isFinite(t)) return "时间未知";
  const d=new Date(t), diff=t-timeValue(now), ago=diff<=0, abs=Math.abs(diff);
  const date=()=>(d.getFullYear()===new Date(timeValue(now)).getFullYear()?"":d.getFullYear()+"-")+
    `${pad2(d.getMonth()+1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  if(!relative) return date();
  if(abs<60000) return !ago && soon ? soon : "刚刚";
  const minutes=Math.round(abs/60000);
  if(minutes<60) return `${minutes} 分钟${ago?"前":"后"}`;
  const hours=Math.round(abs/3600000);
  if(hours<48) return `${hours} 小时${ago?"前":"后"}`;
  const days=Math.floor(abs/86400000);
  return ago && days<30 ? `${days} 天前` : date();
}
// 带悬停全称的时间标签,给要插进 HTML 的地方用。
function timeTag(ts,options){
  const full=fullTime(ts);
  return full?`<time datetime="${esc(new Date(timeValue(ts)).toISOString())}" title="${esc(full)}">${esc(fmtTime(ts,options))}</time>`:`<span class="u">时间未知</span>`;
}
// 数字按中文习惯每三位一个逗号。没有值时是「—」,不是 0:没有读到不等于零。
const NUM_FORMAT=new Intl.NumberFormat("zh-CN");
const fmtNum=n=>n==null || n==="" || !Number.isFinite(Number(n)) ? "—" : NUM_FORMAT.format(Number(n));

// ================= 读取中、未检查、零、读取失败:四种状态四种样子 ==================
// 这页的规矩是这四种状态不许长得一样:还在读、根本没查、查了是零、读坏了。
// 以前各页各写各的:有的空白一块,有的写「-」,读取中用了警告色,读取失败写进灰色小字里像个副标题,
// 还把 work_reader_failed 这种机器代号直接摆给人看。下面几个函数每种只有一种措辞和一种样式,
// 各页在自己的改动里换用。
function loadingBlock(what=""){
  return `<p class="state-block state-loading" role="status"><span class="state-spinner" aria-hidden="true"></span>正在读取${esc(what)}…</p>`;
}
// filtered 给筛选范围名(和清除筛选按钮的 data-reset-filters 同一个值):这是「筛掉了」,
// 不是「真的没有」,所以带一个清除筛选的链接,措辞和底色也和真正的零分开。
function emptyBlock(text,{filtered=""}={}){
  if(!filtered) return `<p class="state-block state-empty">${esc(text)}</p>`;
  return `<p class="state-block state-empty state-filtered">${esc(text)}<button type="button" class="link-button" data-reset-filters="${esc(filtered)}">清除筛选</button></p>`;
}
// 读取失败的原因分两层:给人看的话,和给排查用的原始代号。只有代号(work_reader_failed、
// HTTP 500)或者「说明 (代号)」里括号那段只进悬停,不摆在正文里。
function failureReason(error){
  const raw=String(error?.message ?? error ?? "").trim();
  const human=raw.replace(/\s*\([A-Za-z0-9_.:\- ]+\)$/,"");
  const machine=!human || /^[A-Za-z0-9_.:\-]+$/.test(human) || /^HTTP \d+$/.test(human);
  return {raw,text:machine?"":human};
}
// retryAttr 是调用方写死的属性串(比如 data-reload="work"),按钮由页面自己的监听接走。
function errorBlock(what,error,retryAttr=""){
  const {raw,text}=failureReason(error);
  return `<div class="state-block state-error" role="alert"${raw?` title="${esc("原始错误："+raw)}"`:""}><span><b>${esc(what)}读取失败</b>${text?"："+esc(text):""}</span>${retryAttr?`<button type="button" ${retryAttr}>重试</button>`:""}</div>`;
}
// 计数格子的五种样子:读取中「…」、未检查是虚线框里的「—」、读坏了是红色「!」、
// 零是淡色的「0」、其余是按三位分组的数字。state 用 loading / unchecked / broken / ok。
function countCell(state,n,reason=""){
  if(state==="loading") return `<span class="count-cell count-loading" title="正在读取" aria-label="正在读取">…</span>`;
  if(state==="unchecked") return `<span class="count-cell count-unchecked" title="未检查" aria-label="未检查">—</span>`;
  if(state==="broken"){
    const why=failureReason(reason);
    return `<span class="count-cell count-broken" title="${esc("读取失败："+(why.text || why.raw || "原因未知"))}" aria-label="读取失败">!</span>`;
  }
  if(n==null || !Number.isFinite(Number(n))) return `<span class="count-cell count-unchecked" title="未检查" aria-label="未检查">—</span>`;
  if(Number(n)===0) return `<span class="count-cell count-zero">0</span>`;
  return `<span class="count-cell">${fmtNum(n)}</span>`;
}
// 筛选后的计数只有一种说法:「显示 N / 共 M 项」。
const matchCount=(n,m,unit="项")=>`显示 ${fmtNum(n)} / 共 ${fmtNum(m)} ${unit}`;

function mtUnset(el,reason){ el.innerHTML=`<div class="mt-note">${esc(reason)}</div>`; }

// 按钮不可用时,提示里先说它是哪个动作,再说为什么不能点。只写原因的话,一排图标按钮
// 悬停上去句句相同,分不出哪个是暂停、哪个是删除;只写动作名又等于没解释为什么灰着。
const disabledTitle=(label,reason)=>label?`${label}（不可用：${reason}）`:`不可用：${reason}`;
// 动作自己的名字:aria-label 优先,其次是图标按钮里藏着的文字,再次是原来的 title。
// 已经拼过原因的 title 要先剥掉后缀,不然第二次禁用会叠成「X（不可用：…）（不可用：…）」。
function controlName(button){
  if(button.dataset?.label) return button.dataset.label;
  const own=button.getAttribute?.('aria-label') || button.querySelector?.('.control-label')?.textContent ||
    button.title || button.textContent || '';
  return String(own).trim().replace(/（不可用：[^）]*）$/,'');
}
// reason 为空就恢复可用;title 只在仍是我们写的那句时才还原,渲染方中途改过的 title 不去覆盖。
function setDisabled(button,reason,describedBy){
  if(!button) return;
  const dataset=button.dataset || (button.dataset={});
  if(!dataset.label) dataset.label=controlName(button);
  if(reason){
    if(dataset.lockedTitle!==button.title) dataset.plainTitle=button.title || '';
    const title=disabledTitle(dataset.label,reason);
    // 只在状态真的变了时才写 disabled:同值也会触发属性变更通知,而 ConsoleActions 正是靠
    // 监听 disabled 来重新同步的,无条件写一次就是一个停不下来的循环。
    if(!button.disabled) button.disabled=true;
    button.title=title;dataset.lockedTitle=title;
    if(describedBy) button.setAttribute?.('aria-describedby',describedBy);
    return;
  }
  if(button.disabled) button.disabled=false;
  if(dataset.lockedTitle && button.title===dataset.lockedTitle) button.title=dataset.plainTitle || dataset.label;
  delete dataset.lockedTitle;
  if(describedBy) button.removeAttribute?.('aria-describedby');
}

// reason 给了就渲染成不可用:aria-label 仍是动作名,原因只进 title。
// hint 是可用时的完整说明(比如带上具体地址),省略时就用动作名。
function ibtn(sym, label, extra, cls, reason, hint){
  const title=reason?disabledTitle(label,reason):(hint || label);
  return `<button class="mini icon-only ${cls||""}" ${extra||""}${reason?" disabled":""} title="${esc(title)}"
    aria-label="${esc(label)}"><svg class="ic" aria-hidden="true"><use href="#${sym}"/></svg></button>`;
}

function setIconControl(button,icon,label){
  button.classList.add('icon-only');button.title=label;button.setAttribute('aria-label',label);
  button.innerHTML=`<svg class="ic" aria-hidden="true"><use href="#${esc(icon)}"/></svg>`;
}

async function maintAct(action,name){
  const label=action.split(".")[1];
  // 唯一会删东西的维护动作先确认;不管从哪里调到这里,没点「清理」就不发请求。
  if(action==="clean.tempgit" && !(await confirmTempgitCleanup())) return;
  try{
    const r=await api("/api/maint/act",{method:"POST",body:JSON.stringify({action,name})});
    if(r.error || r.ok===false) throw new Error(apiError(r,500));
    // ⚠ 这句原来的兜底是「启用」:凡是不认识的动作后半段一律说「已启用」,
    // 于是 repo.fetch 报「已启用」、clean.tempgit 也报「已启用」——
    // **一个报告了它没做的事的成功提示,比不报告更糟**:人会据此以为某个东西被打开了。
    // 现在只对认识的动作说具体做了什么,不认识的就说动作名本身。
    const DONE = {archive: "归档", restore: "还原", disable: "禁用", enable: "启用"};
    const what = DONE[label];
    toast(r.message || (what ? `${name} 已${what}` : `${name}: ${action} 完成`),'ok');
  }catch(e){ toast(`${name}: ${e.message}`,"bad"); }
  finally{
    // Re-read the affected source even after failure, which may have been partial.
    if(action.startsWith("repo.")) await loadRepos();
    else if(action.startsWith("clean.")) await loadSys();
    else if(action.startsWith("memory.")) await loadMem();
    else await loadMaint();
  }
}

// 自检条。整页的信任前提:下面每一块面板都建立在这些路径读得到之上,
// 所以它排在最前面,而且读不到时说的是「读不到」,不是一个更小的绿数字。

const toneOf = verdict => verdict && verdict.tone || "warn";
