"""The conversation chain panel after a console restart, and the operation panel over its card.

A restart mints a new page token, so an already-open page holds a stale one. Every panel read
(chain, node, Markdown export that the page turns into a Blob download) and the fork POST must go
through api(), whose one-time 'bad token' refresh swaps the token and replays the request. A path
that calls fetch itself, or that api() stops recovering for, leaves the panel stuck on a 403 until
a hard reload. The fake server below accepts only the fresh token, so each path either recovers
or fails visibly.

The operation panel is sticky only while an operation is pending. Finished records stay in the page
flow instead of pinned over the chain card, which is what covered the card right after a fork.
All data is synthetic.
"""
import json

from test_convchain_ui import SID, chain_case, setup, uid
from test_operations_ui import run
from test_panel_parity import module_source

# The fake server: '/' serves a page carrying the fresh token; /api/* answer 403 'bad token'
# unless the request carries it. Records every request so the tests can see the refresh.
SERVER = """
let calls=[], stubChain=null;
TOKEN='stale-synthetic';
var AbortController=class{constructor(){this.signal={};}abort(){}};
var DOMParser=class{parseFromString(text){return {querySelector:()=>({content:text})};}};
fetch=async(path,options)=>{
  const token=(options||{}).headers?.['X-Console-Token'];
  calls.push({path,token,method:(options||{}).method||'GET'});
  if(path==='/') return {ok:true,status:200,text:async()=>'fresh-synthetic'};
  if(token!=='fresh-synthetic') return {ok:false,status:403,json:async()=>({error:'bad token'})};
  const base=path.split('?')[0];
  const body={'/api/convo/chain':()=>JSON.parse(JSON.stringify(stubChain)),
    '/api/convo/node':()=>({u:'%s',kind:'human',text:'synthetic node'}),
    '/api/convo/export':()=>({filename:'synthetic.md',text:'# synthetic',turns:1,nodes:2}),
    '/api/convo/fork':()=>({newId:'%s',command:'claude --resume x',emitted:1,lines:1})}[base];
  return {ok:true,status:200,json:async()=>body()};
};
CURVIEW='convos';var location={hash:''};var history={pushState(){},replaceState(){}};
$('chlist').querySelector=()=>({scrollIntoView(){},id:'x'});
$('chbox').scrollIntoView=()=>{};
""" % (uid(5), "0000000b-0000-4000-8000-000000000002")


def recover(expression, chain):
    extra = SERVER + "stubChain=" + json.dumps(chain) + ";"
    return run(expression, setup(chain, extra))


def test_first_chain_read_after_restart_refreshes_the_token_and_renders():
    chain = chain_case()
    got = recover("openConvoChain(CH.id,{force:true}).then(()=>({paths:calls.map(c=>c.path.split('?')[0]),"
                  "note:$('chnote').textContent,turns:CH&&CH.turns.length,token:TOKEN}))", chain)
    assert got["paths"] == ["/api/convo/chain", "/", "/api/convo/chain"]
    assert got["note"] == "", "the chain card must not be left on 读取失败 after a restart"
    assert got["turns"] == len(chain["turns"]) and got["token"] == "fresh-synthetic"


def test_node_read_after_restart_recovers_and_is_cached():
    chain = chain_case()
    got = recover(f"chLoadNode('{uid(5)}','|{uid(5)}').then(()=>({{paths:calls.map(c=>c.path.split('?')[0]),"
                  f"cached:CH_NODE.get('|{uid(5)}')}}))", chain)
    assert got["paths"] == ["/api/convo/node", "/", "/api/convo/node"]
    assert got["cached"] and got["cached"]["text"] == "synthetic node"


def test_markdown_export_after_restart_still_produces_the_blob_download():
    chain = chain_case()
    blob = ("let blobs=[],clicks=[];var URL={createObjectURL:b=>{blobs.push(b.parts.join(''));return 'blob:x';},revokeObjectURL(){}};"
            "var Blob=function(parts){this.parts=parts;};"
            "document.createElement=()=>({click(){clicks.push(this.download);},remove(){}});document.body={appendChild(){}};")
    got = run("CH_SEL='h:3';chExport().then(()=>({paths:calls.map(c=>c.path.split('?')[0]),blobs,clicks,"
              "note:$('chnote').textContent}))", setup(chain, SERVER + blob))
    assert got["paths"] == ["/api/convo/export", "/", "/api/convo/export"]
    assert got["blobs"] == ["# synthetic"] and got["clicks"] == ["synthetic.md"]
    assert got["note"] == ""


def test_fork_after_restart_replays_once_with_the_fresh_token():
    chain = chain_case()
    got = recover("askConfirm=async()=>true;CH_SEL='h:3';chFork().then(()=>({calls,fres:CH_FRES}))", chain)
    # After a successful fork the panel re-reads the conversation list; that read is not the fork.
    assert [c["path"] for c in got["calls"]][:3] == ["/api/convo/fork", "/", "/api/convo/fork"]
    assert [c["path"] for c in got["calls"]].count("/api/convo/fork") == 2
    assert [c["token"] for c in got["calls"] if c["path"] == "/api/convo/fork"] == ["stale-synthetic", "fresh-synthetic"]
    assert got["fres"]["newId"] == "0000000b-0000-4000-8000-000000000002"


def test_panel_reads_never_call_fetch_directly():
    """Only api() knows how to refresh the token. A panel fetch() would be the 403 dead end again."""
    source = module_source("panels/convchain.js")
    assert "fetch(" not in source
    assert '"/api/convo/chain?id="' in source and "j=await api(q)" in source
    for route in ("/api/convo/node", "/api/convo/export", "/api/convo/fork"):
        assert f'api("{route}' in source, route


# ---------- operation panel over the chain card ----------

PANEL = """
const classes=new Set();
$('operation-panel').classList={toggle:(name,on)=>{if(on) classes.add(name); else classes.delete(name);}};
"""


def test_operation_panel_is_pinned_only_while_an_operation_is_pending():
    got = run("const op=ConsoleActions.begin('/api/convo/fork',{body:'{}'});const pending=classes.has('settled');"
              "ConsoleActions.finish(op,{newId:'0000000b-0000-4000-8000-000000000002'});"
              "clearTimeout(ConsoleActions.timer);[pending,classes.has('settled'),$('operation-panel').hidden]",
              PANEL)
    assert got == [False, True, False], "a finished record must stop covering the page, but stay visible"


def test_sticky_offsets_follow_the_measured_bar_and_panel():
    measure = PANEL + """
const props={};document.documentElement={style:{setProperty:(k,v)=>props[k]=v}};
const pos={bar:'sticky','operation-panel':'sticky'};
var getComputedStyle=el=>({position:pos[el.id]});
$('bar').id='bar';$('bar').getBoundingClientRect=()=>({height:80.2});
$('operation-panel').id='operation-panel';$('operation-panel').getBoundingClientRect=()=>({height:90});
"""
    got = run("syncStickyOffsets();const wide={...props};pos.bar='static';pos['operation-panel']='static';"
              "syncStickyOffsets();[wide,{...props}]", measure)
    assert got[0] == {"--bar-stick": "81px", "--sticky-stack": "171px"}
    # Phone width: #bar is static, so the panel sits at the very top and nothing is reserved.
    assert got[1] == {"--bar-stick": "0px", "--sticky-stack": "0px"}


def test_operation_panel_css_uses_the_measured_offsets():
    css = module_source("styles.css")
    assert ".operation-panel.settled {position:static}" in css
    assert "top:var(--bar-stick,54px)" in css and "top:54px" not in css
    assert "scroll-padding-top:var(--sticky-stack" in css
