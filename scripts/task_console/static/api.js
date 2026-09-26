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
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const $ = id => document.getElementById(id);

// Presentation only: callers supply the owner's label and its display tone.
const componentTone=state=>({healthy:'ok',unhealthy:'bad',degraded:'warn',failed:'bad',success:'ok',ok:'ok',running:'active',completed:'ok'}[state] || 'idle');
function statusBadge(label,tone='idle',symbol,extraClass=''){
  const symbols={ok:'✓',bad:'×',warn:'!',active:'▶',pending:'◷',idle:'?',muted:'—'};
  if(!Object.hasOwn(symbols,tone)) tone='idle';
  return `<span class="status-chip ${tone} ${esc(extraClass)}"><span class="status-symbol" aria-hidden="true">${esc(symbol || symbols[tone])}</span><span>${esc(label)}</span></span>`;
}

function toast(m,k){const d=document.createElement("div");d.className=k||"";d.textContent=m;
  $("toast").appendChild(d);setTimeout(()=>d.remove(),k==="bad"?9000:4200);}

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
  if(!read && typeof ConsoleActions!=='undefined' && ConsoleActions.readOnly) return Promise.reject(new Error(ConsoleActions.reason));
  const epoch=API_READ_EPOCH, identity=JSON.stringify([p,{...options,method}]);
  const key=JSON.stringify([read?epoch:null,identity]);
  if(!options.signal && API_PENDING.has(key)) return API_PENDING.get(key);
  if(!read) API_READ_EPOCH++;
  const sequence=++API_SEQUENCE;
  const record=state=>{if(read && (API_READS.get(p)?.sequence || 0)<=sequence)
    API_READS.set(p,{path:p,sequence,observedAt:new Date().toISOString(),...state});};
  record({pending:true});
  const operation=!read && typeof ConsoleActions!=='undefined' ? ConsoleActions.begin?.(p,options) : null;
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
      if(!read) API_READ_EPOCH++;
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

const kb = n => n==null ? "-" : n<1024 ? n+"B" : n<1048576 ? (n/1024).toFixed(0)+"K"
                                                          : (n/1048576).toFixed(1)+"M";

function mtUnset(el,reason){ el.innerHTML=`<div class="mt-note">${esc(reason)}</div>`; }

function ibtn(sym, label, extra, cls){
  const text=({'i-copy':'复制路径','i-folder':'打开目录','i-web':'打开网页','i-diff':'查看改动','i-fetch':'获取远程更新','i-push':'提交并推送','i-archive':'归档','i-restore':'恢复','i-off':'停用','i-on':'启用'})[sym];
  return `<button class="mini ib ${text?'with-label ':''}${cls||""}" ${extra||""} title="${esc(label)}"
    aria-label="${esc(label)}"><svg class="ic" aria-hidden="true"><use href="#${sym}"/></svg>${text?`<span>${text}</span>`:''}</button>`;
}

async function maintAct(action,name){
  const label=action.split(".")[1];
  try{
    const r=await api("/api/maint/act",{method:"POST",body:JSON.stringify({action,name})});
    if(r.error || r.ok===false) throw new Error(apiError(r,500));
    // ⚠ 这句原来的兜底是「启用」:凡是不认识的动作后半段一律说「已启用」,
    // 于是 repo.fetch 报「已启用」、clean.tempgit 也报「已启用」——
    // **一个报告了它没做的事的成功提示,比不报告更糟**:人会据此以为某个东西被打开了。
    // 现在只对认识的动作说具体做了什么,不认识的就说动作名本身。
    const DONE = {archive: "归档", restore: "还原", disable: "禁用", enable: "启用"};
    const what = DONE[label];
    toast(r.message || (what ? `${name} 已${what}` : `${name}: ${action} 完成`));
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
