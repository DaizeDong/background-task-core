// Classic script module; loaded in app.js dependency order.
let CONVOS=null, CV_HUMAN_ONLY=false, CV_OPEN={}, CV_QUERY="";
// 排序维度。后端按最近活动发,这里只重排,不重扫 ——
// 换个顺序而已,没有理由再走一遍上千份转录。
let CV_SORT="new";
// 临时目录折叠块的展开键。用一个不可能是路径的字符串,免得和真目录撞。
const CV_EPH_KEY="::eph::";
// 标题从哪来。改过名的单独一种颜色:那是唯一一个人明确说过「这场对话叫这个」的地方,
// 别的都是推断出来的,看的人有权知道自己在看哪一种。
const CV_TAG={rename:"名","ai-title":"AI",summary:"概","first-message":"首",slug:"代",id:"?"};
const CV_SRC={rename:"你用 /rename 起的名字","ai-title":"自动生成的窗口标题",
  summary:"压缩时写的概括","first-message":"第一条消息",slug:"自动代号",id:"只有会话号"};

async function loadConvos(){
  $("cvnote").textContent="扫描中";
  try{ CONVOS=await api("/api/convos"); $("cvnote").textContent=""; }
  catch(e){ $("cvnote").textContent="失败:"+e.message; return; }
  renderConvos();
}

function cvAge(h){ return h<24 ? h.toFixed(0)+"h" : (h/24).toFixed(0)+"d"; }

function renderConvos(){
  if(!CONVOS) return;
  if(!CONVOS.available){
    $("cvhead").innerHTML=`<span style="color:var(--warn)">${esc(CONVOS.reason)}</span>`;
    $("cvgroups").innerHTML=""; return;
  }
  const S=CONVOS.summary;
  $("cvhead").innerHTML=`<span>共 <b>${S.files}</b> 场</span>`
    +`<span><b style="color:var(--cyan)">${S.humanish}</b> 场真人多轮</span>`
    +`<span>${S.groups} 个目录 · ${kb(S.bytes)}</span>`
    +`<label>排序 <select id="cvsort">
        <option value="new"${CV_SORT==="new"?" selected":""}>最近活动</option>
        <option value="size"${CV_SORT==="size"?" selected":""}>体积</option>
        <option value="count"${CV_SORT==="count"?" selected":""}>场次</option>
        <option value="old"${CV_SORT==="old"?" selected":""}>最旧在前</option>
      </select></label>`
    +`<label><input type="checkbox" id="cvonly"${CV_HUMAN_ONLY?" checked":""}> 只看真人</label>`
    // 读不动的必须报出来:悄悄跳过会让「没有这些对话」和「我没读到」变成同一个数字。
    +(S.unreadable?`<span style="color:var(--bad)">读不动 ${S.unreadable}</span>`:"");

  const query=CV_QUERY.trim().toLowerCase();
  const matches=(g,r)=>(!CV_HUMAN_ONLY || r.humanSeen>=2) && (!query ||
    [g.cwd,r.title,r.preview,r.file].join(" ").toLowerCase().includes(query));
  const groups=CONVOS.groups.filter(g=>(!CV_HUMAN_ONLY||g.humanish) && (!query || g.shown.some(r=>matches(g,r))));
  const delivered=CONVOS.groups.reduce((n,g)=>n+g.shown.length,0);
  const matching=groups.reduce((n,g)=>n+g.shown.filter(r=>matches(g,r)).length,0);
  $("cv-match").textContent=`匹配 ${matching}/${delivered} 条已加载记录 · 另有 ${Math.max(0,S.files-delivered)} 条未载入`;

  // 体积条的基准。取对数是因为跨度有四个数量级(几百字节到几个 G):线性标度下
  // 除了最大的两行,其余全部宽度归零,那等于把这一列画成空的。
  // ⚠ 但对数**从 0 起算**又走到另一个极端:实测最小的一行也有 56% 宽,
  // 三十多行挤在 56%-100% 之间,肉眼分不出谁比谁大 —— 一根几乎人人等长的条,
  // 和没有条一样没用。所以把 [最小, 最大] 整段铺开到 [3%, 100%]。
  const vals=groups.map(g=>g.bytes||0).filter(b=>b>0);
  const maxB=Math.max(1,...vals), minB=vals.length?Math.min(...vals):1;
  const lo=Math.log10(1+minB), hi=Math.log10(1+maxB), span=hi-lo;
  const barW=b=>{
    if(b<=0) return 0;                       // 真的 0 走斜纹,不是一根 3% 的条
    if(span<=0) return 100;                  // 只有一组,或全部一样大
    return Math.max(3,Math.round(3+97*(Math.log10(1+b)-lo)/span));
  };

  // 一次性无头运行留下的临时目录。它们占掉三分之一的行,而每行携带的信息
  // 只有「这里有过一次无头运行」。三个谓词缺一不可,而且**不许放宽成「凡 1 场的都折」**:
  // humanish 保证折进去的没有真人多轮,count<=2 保证一个意外长起来的目录不会被藏掉,
  // 名字正则保证只吃一次性目录。任一条不满足就留在外面单独成行。
  const EPH=/[\\/]Temp[\\/](astab|astra|ccstab|ccagentic)_[a-z0-9]+$/i;
  const isEph=g=>EPH.test(g.cwd)&&!g.humanish&&g.count<=2;
  const eph=groups.filter(isEph), rest=groups.filter(g=>!isEph(g));

  // ⚠ 只排 rest。临时目录那一坨永远留在最后,不参与排序:
  // 它们是被折起来的一整块,让它按体积浮到第一行会把「这是被折叠的噪音」
  // 这个含义弄丢。
  if(CV_SORT==="size") rest.sort((a,b)=>(b.bytes||0)-(a.bytes||0));
  else if(CV_SORT==="count") rest.sort((a,b)=>(b.count||0)-(a.count||0));
  else if(CV_SORT==="old") rest.sort((a,b)=>(a.newest||0)-(b.newest||0));
  else rest.sort((a,b)=>(b.newest||0)-(a.newest||0));

  const cvGroup=g=>{
    const rows=g.shown.filter(r=>matches(g,r));
    // 这一屏唯一告诉人「你没看全」的地方,以前在最需要它准确的那个模式下报旧口径的数。
    // 现在把三个来源分开数:被筛掉的、后端就没送来的、以及**送来的那批没经过筛**这件事。
    // 最后一句不是啰嗦:后端截断在筛选之前发生,所以没列出的那些里有几场是真人对话,
    // 前端根本不知道 —— 编一个筛过的数出来会比报旧口径更糟。
    const cut=g.shown.length-rows.length;
    const unlisted=g.count-g.shown.length;
    const open=CV_OPEN[g.cwd]!==false?" open":"";
    const cut2=g.cwd.search(/[\\/][^\\/]*$/);
    const head=cut2>0?g.cwd.slice(0,cut2+1):"", tail=cut2>0?g.cwd.slice(cut2+1):g.cwd;
    const b=g.bytes||0;
    // 年龄这一列是排序说明书:后端按 newest 倒序发,而前端从来没把 newest 画出来,
    // 于是一个有序列表看起来像乱序的。newest 是 epoch **秒**。
    const ageH=g.newest?(Date.now()/1000-g.newest)/3600:null;
    return `<div class="cv-g${open}" data-cv="${esc(g.cwd)}">
      <button class="cv-gh" aria-expanded="${!!open}">
        <span class="caret">${open?"▼":"▶"}</span>
        <span class="dir" title="${esc(g.cwd)}"><span class="dp">${esc(head)}</span><span
          class="dn">${esc(tail)}</span></span>
        <span class="cv-bar${b?"":" zero"}" title="${kb(b)}"><i style="width:${barW(b)}%"></i></span>
        <span class="cv-hm">${g.humanish?"👤"+g.humanish:""}</span>
        <span class="ag" title="最近一场的时间">${ageH==null?"–":cvAge(ageH)}</span>
        <span class="n">${CV_HUMAN_ONLY?rows.length+"/"+g.count:g.count} 场 · ${kb(b)}</span>
      </button>
      <div class="cv-list">${rows.map(r=>`
        <div class="cv-r${r.humanSeen>=2?" human":""}" data-cvfile="${esc(r.file||"")}"
             title="点一下复制转录路径">
          <button class="t cv-copy" title="${esc(r.title)}">${esc(r.title)}</button>
          <span class="src ${esc(r.titleFrom)}" title="${esc(CV_SRC[r.titleFrom]||r.titleFrom)}">${
            CV_TAG[r.titleFrom]||"?"}</span>
          <span class="m">${r.humanSeen?"👤"+r.humanSeen+(r.partial?"+":""):""}</span>
          <span class="m">${cvAge(r.ageHours)} · ${kb(r.bytes)}</span>
          ${(r.preview&&r.preview!==r.title)?`<span class="pv" title="${esc(r.preview)}">${esc(r.preview)}</span>`:""}
        </div>`).join("")}
        ${cut?`<div class="cv-more">另有 ${cut} 场被当前条件筛掉</div>`:""}
        ${g.truncated?`<div class="cv-more">这个目录还有 ${unlisted} 场没列出（未参与筛选）</div>`:""}
      </div></div>`;
  };

  // 折叠块本身要把总场数和总体积打在标题上:「这里有东西」不能因为收起来就消失,
  // 消失的只是 23 份逐字相同的路径前缀。
  const ephOpen=CV_OPEN[CV_EPH_KEY]!==false?" open":"";
  const ephN=eph.reduce((a,g)=>a+g.count,0), ephB=eph.reduce((a,g)=>a+(g.bytes||0),0);
  const ephHtml=eph.length?`<div class="cv-g${ephOpen}" data-cv="${esc(CV_EPH_KEY)}">
      <button class="cv-gh" aria-expanded="${!!ephOpen}">
        <span class="caret">${ephOpen?"▼":"▶"}</span>
        <span class="dir" title="一次性无头运行留下的临时目录,每个至多 2 场且没有真人多轮"
          ><span class="dn">临时会话目录 ${eph.length} 个</span></span>
        <span class="cv-bar${ephB?"":" zero"}" title="${kb(ephB)}"><i
          style="width:${barW(ephB)}%"></i></span>
        <span class="cv-hm"></span><span class="ag"></span>
        <span class="n">${ephN} 场 · ${kb(ephB)}</span>
      </button>
      <div class="cv-list">${eph.map(cvGroup).join("")}</div></div>`:"";

  $("cvgroups").innerHTML=(rest.map(cvGroup).join("")+ephHtml) || '<p class="review-empty">没有匹配的已加载会话</p>';
}

// ================= 外壳:分区切换与徽章 ==============================================
// ── 调用屏 ──────────────────────────────────────────────────────────────────
// llmcall 的账本在 ~/.llmcall/ 下,十一万条,二十兆。在这一屏之前唯一的读法是一条
// 打印**终生均值**的命令行 —— 于是「今天降级了八十二次」和「这一年平均很健康」
// 会打印出同一个绿色数字。这一屏存在的理由就是把那两件事分开。
