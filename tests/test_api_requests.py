"""Exercise request concurrency and error feedback without touching live services."""
import json

from test_panel_parity import module_source, node


def run_api(body):
    return node("""
const vm=require('node:vm'), calls=[], waiting=[];
const context=vm.createContext({
  document:{querySelector:()=>({content:'synthetic-token'})},
  setTimeout,clearTimeout,AbortController,Date,
  fetch:(path,options)=>new Promise(resolve=>{calls.push({path,options});waiting.push(resolve);})
});
const reply=(index,data={},status=200)=>waiting[index]({ok:status<400,status,json:async()=>data});
""" + "vm.runInContext(" + json.dumps(module_source("api.js")) + ",context);\n"
        + "(async()=>{" + body + "})().then(result=>console.log(JSON.stringify(result)));" )


def test_identical_pending_reads_share_one_request_but_later_reads_are_fresh():
    result = run_api("""
const a=vm.runInContext("api('/api/tasks')",context);
const b=vm.runInContext("api('/api/tasks')",context);
await new Promise(resolve=>setImmediate(resolve));
const pending=calls.length;waiting.forEach((_,i)=>reply(i,{version:1}));
const first=await Promise.all([a,b]);
const c=vm.runInContext("api('/api/tasks')",context);
await new Promise(resolve=>setImmediate(resolve));reply(calls.length-1,{version:2});
return {pending,first,next:await c,requests:calls.length};
""")
    assert result == {"pending": 1, "first": [{"version": 1}, {"version": 1}],
                      "next": {"version": 2}, "requests": 2}


def test_identical_pending_writes_are_not_submitted_twice():
    result = run_api("""
const action="api('/api/maint/act',{method:'POST',body:JSON.stringify({action:'skill.archive',name:'acme'})})";
const a=vm.runInContext(action,context), b=vm.runInContext(action,context);
await new Promise(resolve=>setImmediate(resolve));
const pending=calls.length;waiting.forEach((_,i)=>reply(i,{ok:true}));
await Promise.all([a,b]);return {pending};
""")
    assert result == {"pending": 1}


def test_structured_control_error_retains_code_and_readable_explanation():
    result = run_api("""
const pending=vm.runInContext("api('/api/act',{method:'POST',body:'{}'}).catch(error=>({message:error.message,code:error.payload?.error?.code}))",context);
await new Promise(resolve=>setImmediate(resolve));
reply(0,{ok:false,message:'Task registration changed',error:{code:'scheduler_ownership_changed',field:'ownership'}},500);
return await pending;
""")
    assert "Task registration changed" in result["message"]
    assert "[object Object]" not in result["message"]
    assert result["code"] == "scheduler_ownership_changed"


def test_read_after_a_write_does_not_reuse_the_pre_action_request():
    result = run_api("""
const old=vm.runInContext("api('/api/tasks')",context);
const action=vm.runInContext("api('/api/act',{method:'POST',body:'{}'})",context);
await new Promise(resolve=>setImmediate(resolve));reply(1,{ok:true});await action;
const fresh=vm.runInContext("api('/api/tasks')",context);
await new Promise(resolve=>setImmediate(resolve));
const requests=calls.length;reply(2,{version:2});const newest=await fresh;
reply(0,{version:1});await old;
return {requests,newest};
""")
    assert result == {"requests": 3, "newest": {"version": 2}}


def test_request_failure_can_be_retried_without_reusing_failed_promise():
    result = run_api("""
const a=vm.runInContext("api('/api/tasks').catch(error=>error.message)",context);
await new Promise(resolve=>setImmediate(resolve));reply(0,{error:'unavailable'},503);await a;
const b=vm.runInContext("api('/api/tasks')",context);
await new Promise(resolve=>setImmediate(resolve));reply(1,{version:2});
return {requests:calls.length,next:await b};
""")
    assert result == {"requests": 2, "next": {"version": 2}}


def test_synchronous_fetch_failure_releases_the_pending_entry():
    result = run_api("""
context.fetch=()=>{throw new Error('synthetic fetch failure')};
return await vm.runInContext("api('/api/tasks').catch(error=>error.message)",context);
""")
    assert result == "synthetic fetch failure"


def test_old_read_resolves_to_post_action_data_even_when_it_finishes_last():
    result = run_api("""
const old=vm.runInContext("api('/api/tasks')",context);
const action=vm.runInContext("api('/api/act',{method:'POST',body:'{}'})",context);
await new Promise(resolve=>setImmediate(resolve));reply(1,{ok:true});await action;
const fresh=vm.runInContext("api('/api/tasks')",context);
await new Promise(resolve=>setImmediate(resolve));reply(0,{version:1});
await new Promise(resolve=>setImmediate(resolve));reply(2,{version:2});
return {old:await old,fresh:await fresh,requests:calls.length};
""")
    assert result == {"old": {"version": 2}, "fresh": {"version": 2}, "requests": 3}


def test_stale_token_refreshes_once_and_replays_the_identical_payload():
    result = run_api("""
context.DOMParser=class {parseFromString(text){return {querySelector:()=>({content:text})};}};
context.fetch=async(path,options)=>{
  calls.push({path,options});
  if(path==='/') return {ok:true,text:async()=> 'fresh-synthetic-token'};
  const ok=options.headers['X-Console-Token']==='fresh-synthetic-token';
  return {ok,status:ok?200:403,json:async()=>ok?{ok:true}:{error:'bad token'}};
};
const reply=await vm.runInContext("api('/api/work/action',{method:'POST',body:JSON.stringify({request_id:'synthetic'})})",context);
return {reply,paths:calls.map(c=>c.path),sameBody:calls[0].options.body===calls[2]?.options.body};
""")
    assert result == {"reply": {"ok": True}, "paths": ["/api/work/action", "/", "/api/work/action"], "sameBody": True}


def test_concurrent_expired_requests_share_one_token_refresh():
    result = run_api("""
context.DOMParser=class {parseFromString(text){return {querySelector:()=>({content:text})};}};
context.fetch=async(path,options)=>{
  calls.push({path,options});
  if(path==='/') {await new Promise(resolve=>setImmediate(resolve));return {ok:true,text:async()=> 'fresh'};}
  const ok=options.headers['X-Console-Token']==='fresh';
  return {ok,status:ok?200:403,json:async()=>ok?{ok:true}:{error:'bad token'}};
};
await Promise.all(['/api/tasks','/api/work'].map(path=>vm.runInContext(`api('${path}')`,context)));
return {refreshes:calls.filter(c=>c.path==='/').length,requests:calls.length};
""")
    assert result == {"refreshes": 1, "requests": 5}


def test_repeated_bad_token_is_reported_as_refusal_without_a_retry_loop():
    result = run_api("""
context.DOMParser=class {parseFromString(text){return {querySelector:()=>({content:text})};}};
context.fetch=async(path,options)=>{
  calls.push({path,options});
  return path==='/'?{ok:true,text:async()=> 'fresh'}:{ok:false,status:403,json:async()=>({error:'bad token'})};
};
const error=await vm.runInContext("api('/api/work/action',{method:'POST',body:'{}'}).catch(e=>({message:e.message,rejected:e.requestRejected}))",context);
return {error,requests:calls.length};
""")
    assert result["requests"] == 3
    assert result["error"]["rejected"] is True
    assert "刷新" in result["error"]["message"]


def test_other_http_errors_and_network_loss_never_replay_a_write():
    result = run_api("""
context.fetch=async(path,options)=>{calls.push({path,options});return {ok:false,status:503,json:async()=>({error:'unavailable'})};};
await vm.runInContext("api('/api/act',{method:'POST',body:'{}'}).catch(()=>{})",context);
context.fetch=async(path,options)=>{calls.push({path,options});throw Error('lost reply');};
await vm.runInContext("api('/api/act',{method:'POST',body:'{}'}).catch(()=>{})",context);
return {requests:calls.length};
""")
    assert result == {"requests": 2}
