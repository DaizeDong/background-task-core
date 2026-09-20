// Classic script module; loaded in app.js dependency order.
const TOKEN = document.querySelector('meta[name="console-token"]').content;
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const $ = id => document.getElementById(id);

function toast(m,k){const d=document.createElement("div");d.className=k||"";d.textContent=m;
  $("toast").appendChild(d);setTimeout(()=>d.remove(),k==="bad"?9000:4200);}

let API_SEQUENCE=0;
const API_READS=new Map();
async function api(p,o){
  const read=!o?.method || o.method==='GET', sequence=++API_SEQUENCE;
  const record=state=>{if(read && (API_READS.get(p)?.sequence || 0)<=sequence)
    API_READS.set(p,{path:p,sequence,observedAt:new Date().toISOString(),...state});};
  record({pending:true});
  try{
    const r=await fetch(p,Object.assign({headers:{"X-Console-Token":TOKEN,"Content-Type":"application/json"}},o||{}));
    const j=await r.json();
    if(!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    record({pending:false,error:j.error || (j.available===false ? j.reason || '不可用' : null)});
    return j;
  }catch(error){record({pending:false,error:error.message});throw error;}
}

const kb = n => n==null ? "-" : n<1024 ? n+"B" : n<1048576 ? (n/1024).toFixed(0)+"K"
                                                          : (n/1048576).toFixed(1)+"M";

function mtUnset(el,reason){ el.innerHTML=`<div class="mt-note">${esc(reason)}</div>`; }

function ibtn(sym, label, extra, cls){
  return `<button class="mini ib ${cls||""}" ${extra||""} title="${esc(label)}"
    aria-label="${esc(label)}"><svg class="ic"><use href="#${sym}"/></svg></button>`;
}

async function maintAct(action,name){
  const label=action.split(".")[1];
  try{
    const r=await api("/api/maint/act",{method:"POST",body:JSON.stringify({action,name})});
    if(r.error){ toast(`${name}: ${r.error}`,"bad"); return; }
    // ⚠ 这句原来的兜底是「启用」:凡是不认识的动作后半段一律说「已启用」,
    // 于是 repo.fetch 报「已启用」、clean.tempgit 也报「已启用」——
    // **一个报告了它没做的事的成功提示,比不报告更糟**:人会据此以为某个东西被打开了。
    // 现在只对认识的动作说具体做了什么,不认识的就说动作名本身。
    const DONE = {archive: "归档", restore: "还原", disable: "禁用", enable: "启用"};
    const what = DONE[label];
    toast(what ? `${name} 已${what}` : `${name}: ${action} 完成`);
    // 插件与 skill 的改动都要下个会话才生效,所以这里只重读自己那一栏,不碰任务表。
    if(action.startsWith("repo.")) await loadRepos();
    else if(action.startsWith("clean.")) await loadSys();
    else if(action.startsWith("memory.")) await loadMem();
    else await loadMaint();
  }catch(e){ toast(`${name}: ${e.message}`,"bad"); }
}

// 自检条。整页的信任前提:下面每一块面板都建立在这些路径读得到之上,
// 所以它排在最前面,而且读不到时说的是「读不到」,不是一个更小的绿数字。

const toneOf = verdict => verdict && verdict.tone || "warn";
