"""Behavior checks for owner-issued todo actions; all records are generated."""
import json
from pathlib import Path

from test_operations_ui import run
from test_panel_parity import module_source
from tools.make_fixtures import work_action_case


def action_script():
    path = Path(__file__).resolve().parents[1] / 'scripts/task_console/static/work-actions.js'
    assert path.is_file(), 'Todo action module has not been implemented'
    return ''  # Loaded through the same ordered module list as the browser.


def render(item, setup=''):
    return run('workActionButtons(item)', action_script() + '\nconst item=' + json.dumps(item) + ';' + setup)


def test_todo_displays_owner_offer_without_an_inferred_action():
    item = work_action_case()
    html = render(item)
    assert '整理报告' in html and 'data-work-action="agent"' in html
    item.pop('actions')
    assert 'data-work-action' not in render(item)


def test_owner_action_label_is_escaped_and_readonly_disables_execution():
    item = work_action_case()
    item['actions']['offers'][0]['label'] = '<script>unsafe</script>'
    html = render(item, "document.querySelector=()=>({content:'true'});")
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert 'disabled' in html


def test_queued_action_shows_work_link_without_claiming_completion():
    item = work_action_case()
    item['actions']['offers'] = []
    item['actions']['current'] = {'id': 'acme-request', 'kind': 'agent', 'state': 'queued', 'work_item_id': 'acme-work'}
    html = render(item)
    assert '排队' in html and '已完成' not in html
    assert 'acme-work' in html


def test_action_unavailable_is_explained_without_an_enabled_placeholder():
    item = work_action_case()
    item['actions'] = {'available': False, 'reason': '执行接口尚未连接', 'offers': [], 'current': None}
    html = render(item)
    assert '执行接口尚未连接' in html
    assert 'data-work-action' not in html


def test_uncertain_click_reuses_exact_intent_and_ack_clears_it():
    setup = 'const sample=' + json.dumps(work_action_case()) + ';' + '''
WORK={available:true,items:[sample]};renderWorkPlatform=()=>{};loadWork=async()=>{};toast=()=>{};
globalThis.crypto={randomUUID:()=> 'synthetic-request-unique'};
const stored={};globalThis.sessionStorage={getItem:key=>stored[key],setItem:(key,value)=>stored[key]=value,removeItem:key=>delete stored[key]};
let sent=[],attempt=0;api=async(path,options)=>{sent.push(JSON.parse(options.body));if(!attempt++) throw Error('lost reply');return {ok:true,status:'queued'};};
'''
    result = run('''(async()=>{await submitWorkAction(sample.id,'agent');sample.actions.revision='sha256:changed';
await submitWorkAction(sample.id,'agent');return {sent,pending:WORK_ACTION_PENDING.size,intents:WORK_ACTION_INTENTS.size,stored:Object.keys(stored).length};})()''',setup)
    assert result['sent'][0] == result['sent'][1]
    assert result['pending'] == result['intents'] == result['stored'] == 0
