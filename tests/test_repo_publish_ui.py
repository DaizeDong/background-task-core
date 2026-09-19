"""Exercise the actual browser module with a synthetic DOM and transport."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

SOURCE = (Path(__file__).resolve().parents[1] /
          'scripts/task_console/static/panels/repositories.js').read_text('utf-8')


def run_ui(plans, results, *, batches=None, cancel=False):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node required for browser module checks')
    fixture = json.dumps(dict(plans=plans, results=results,
                              batches=batches or [list(plans)], cancel=cancel))
    program = r'''
const vm=require('node:vm');
const f=FIXTURE, trace=[], dialogs=[], toasts=[], elements={rpout:{textContent:''}};
let writes=0;
function element(tag){
  const e={tag,children:[],textContent:'',value:'',handlers:{},
    appendChild(x){this.children.push(x);return x;},setAttribute(){},
    addEventListener(k,fn){this.handlers[k]=fn;},remove(){},
    set innerHTML(v){writes++; throw Error('untrusted HTML write');},
    showModal(){
      dialogs.push(this.children.find(c=>c.tag==='pre').textContent);
      trace.push({kind:'review'});
      const text=f.cancel?'取消':'确认发布';
      this.children.find(c=>c.textContent===text).handlers.click();
    }};
  return e;
}
const document={createElement:element,body:element('body')};
const saved={};
const context=vm.createContext({document,
  sessionStorage:{getItem:k=>saved[k]||null,setItem:(k,v)=>saved[k]=v},
  $:id=>elements[id],toast:(text,tone)=>toasts.push({text,tone}),alert:()=>{},
  api:async(path,options)=>{
    const body=JSON.parse(options.body);
    if(path==='/api/repo/plan'){
      trace.push({kind:'plan',name:body.name});
      return f.plans[body.name];
    }
    const arg=JSON.parse(body.arg);
    trace.push({kind:'act',name:body.name,arg});
    const result=f.results.shift();
    if(result.throw) throw Error(result.throw);
    return result;
  }});
vm.runInContext(SOURCE,context);
vm.runInContext('loadRepos=async()=>{};',context);
(async()=>{
  for(const names of f.batches){
    await vm.runInContext('repoCommitPushBatch('+JSON.stringify(names)+')',context);
  }
  console.log(JSON.stringify({trace,dialogs,toasts,saved,writes,out:elements.rpout.textContent}));
})().catch(e=>{console.error(e);process.exitCode=1;});
'''.replace('FIXTURE', fixture).replace('vm.runInContext(SOURCE,context);',
                                      'vm.runInContext(' + json.dumps(SOURCE) + ',context);')
    result = subprocess.run([node, '-'], input=program, encoding='utf-8',
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def plan(name='a', **overrides):
    return dict(ok=True, files=['reviewed.txt'], fileCount=1, ahead=[], aheadCount=0,
                branch='main', upstream='origin/main', diff='+ <script>untrusted()</script>',
                expect={'snapshot': {'head': name * 40, 'tree': name * 40},
                        'target': {'ref': 'refs/heads/main', 'url': 'synthetic'}},
                **overrides)


def test_batch_collects_and_displays_all_plans_before_approval_or_action():
    plans = {'a': plan(), 'b': plan('b')}
    result = run_ui(plans, [{'ok': True, 'state': 'pushed'}] * 2)
    assert [x['kind'] for x in result['trace']] == ['plan', 'plan', 'review', 'act', 'act']
    actions = [x for x in result['trace'] if x['kind'] == 'act']
    assert actions[0]['arg']['expect'] == plans['a']['expect']
    assert actions[1]['arg']['expect'] == plans['b']['expect']
    assert '<script>untrusted()</script>' in result['dialogs'][0]
    assert result['writes'] == 0


@pytest.mark.parametrize('override', [{'blocked': ['scope changed']},
                                     {'filesTruncated': True}, {'diffTruncated': True}])
def test_incomplete_or_blocked_review_cannot_publish(override):
    result = run_ui({'a': plan(), 'b': plan('b', **override)}, [])
    assert [x['kind'] for x in result['trace']] == ['plan', 'plan']
    assert not result['dialogs']


def test_cancel_after_review_performs_no_action():
    result = run_ui({'a': plan()}, [], cancel=True)
    assert [x['kind'] for x in result['trace']] == ['plan', 'review']


@pytest.mark.parametrize('state', ['push_failed', 'unknown'])
def test_failed_delivery_retains_receipt_and_retries_only_existing_commit(state):
    receipt = {'retry': {'commit': 'c' * 40, 'tree': 'd' * 40},
               'target': {'ref': 'refs/heads/main', 'url': 'synthetic'}}
    result = run_ui({'a': plan()}, [
        {'ok': False, 'state': state, 'commit': 'c' * 40, 'expect': receipt},
        {'ok': True, 'state': 'pushed'}], batches=[['a'], ['a']])
    assert [x['kind'] for x in result['trace']] == ['plan', 'review', 'act', 'review', 'act']
    assert result['trace'][-1]['arg']['expect'] == receipt
    assert result['trace'][-1]['arg']['push'] is True
    assert '仅重试推送' in result['dialogs'][1]
    assert result['toasts'][0]['tone'] == 'bad'
    assert json.loads(result['saved']['repo-publish-retries-v1']) == []


def test_no_error_field_does_not_imply_success_and_batch_stops():
    result = run_ui({'a': plan(), 'b': plan('b')}, [{'ok': False, 'state': 'unknown'}])
    assert len([x for x in result['trace'] if x['kind'] == 'act']) == 1
    assert '结果尚未确认' in result['out']
    assert '尚未执行: 1' in result['out']
    assert result['toasts'][-1]['tone'] == 'bad'


def test_lost_response_does_not_replay_and_reports_uncertainty():
    result = run_ui({'a': plan()}, [{'throw': 'connection closed'}])
    assert len([x for x in result['trace'] if x['kind'] == 'act']) == 1
    assert '需要先核查本地提交和远端状态' in result['out']
