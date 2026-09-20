"""Evidence projection must not turn a preview, missing receipt or unrelated output green."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.make_fixtures import review_pipeline_case, catalog_snapshot
from test_panel_parity import node, module_source
import component_status
import selfcheck


def project(expression, data=None):
    program = """
const vm=require('node:vm');const elements={};
const document={querySelector:()=>({content:'synthetic'}),createElement:()=>({}),
getElementById:id=>elements[id] ||= {innerHTML:'',textContent:'',addEventListener:()=>{},appendChild:()=>{}}};
const context=vm.createContext({document});
"""
    for name in ('api.js', 'panels/skills.js', 'panels/pipelines.js', 'panels/review.js'):
        program += f"vm.runInContext({json.dumps(module_source(name))},context);\n"
    program += "vm.runInContext(" + json.dumps("COMPONENTS=" + json.dumps(data) + ";") + ",context);\n"
    program += "console.log(JSON.stringify(vm.runInContext(" + json.dumps(expression) + ",context)));"
    return node(program)


def test_zero_difference_does_not_claim_full_capability_parity():
    steps = project("pipelineSteps('sync',COMPONENTS)", review_pipeline_case())
    assert steps[1]['label'] == '文件已对齐'
    assert steps[2]['tone'] == 'warn'
    assert steps[3]['tone'] == 'warn'
    assert steps[2]['findings'][0]['name'] == 'synthetic-skill'


@pytest.mark.parametrize('field,value', [('mode', 'plan'), ('status', 'failed'), ('finished_at', None), ('remaining_changes', None)])
def test_incomplete_or_preview_receipt_never_proves_alignment(field, value):
    data = review_pipeline_case()
    data['tasks'][0]['last_run_v1'][field] = value
    assert project("pipelineSteps('sync',COMPONENTS)[1].tone", data) != 'ok'


def test_absent_or_ambiguous_task_is_unknown():
    assert all(step['tone'] == 'idle' for step in project("pipelineSteps('sync',COMPONENTS)", None))
    data = review_pipeline_case()
    data['tasks'].append(data['tasks'][0])
    assert project("pipelineTask('sync',COMPONENTS)", data) is None


def test_backup_artifacts_do_not_prove_copy_push_or_model_execution():
    steps = project("pipelineSteps('backup',COMPONENTS)", review_pipeline_case())
    assert steps[0]['tone'] == 'idle'
    assert all(step['label'] == '产物检查正常' for step in steps[1:])
    assert '尚未按运行标识关联' in steps[1]['detail']


def test_review_groups_same_task_and_preserves_distinct_check_evidence():
    expression = """renderReviewQueue([
      {v:'tasks',src:'任务',task:'synthetic',nm:'synthetic',why:'failed',sev:3},
      {v:'tasks',src:'产物',task:'synthetic',nm:'synthetic · output',why:'missing',sev:2}
    ],false);document.getElementById('todod').innerHTML"""
    html = project(expression)
    assert html.count('class="review-row"') == 1
    assert 'failed' in html and 'output：missing' in html
    assert 'data-fix' not in html and 'data-task="synthetic"' in html


def test_catalog_search_keeps_unknown_auth_and_escapes_untrusted_names():
    catalog = component_status.catalog_view(catalog_snapshot())
    catalog['records'][0]['registry_key'] = '<script>synthetic</script>'
    result = project("renderCatalog();document.getElementById('catalog-results').innerHTML", {'catalog':catalog})
    assert '<script>synthetic</script>' not in result
    assert '&lt;script&gt;synthetic&lt;/script&gt;' in result
    assert '<dt>认证</dt><dd>未检查</dd>' in result
    result = project("CATALOG_QUERY='no-such-synthetic-source';renderCatalog();document.getElementById('catalog-count').textContent", {'catalog':catalog})
    assert result == '显示 0/0'


def test_selfcheck_and_components_agree_on_embedded_catalog(tmp_path):
    path = tmp_path/'snapshot.json'
    path.write_text(json.dumps({'schemaVersion':1, 'tasks':[], 'catalog':catalog_snapshot()}),encoding='utf-8')
    env = {'TASK_CONSOLE_STATUS_SNAPSHOT':str(path)}
    assert component_status.read_configured(env=env)['catalog']['available']
    row = next(row for row in selfcheck.run(env=env)['rows'] if row['key']=='source_catalog')
    assert row['state'] == 'ok'
    env['TASK_CONSOLE_CATALOG_SNAPSHOT'] = str(tmp_path/'missing.json')
    assert not component_status.read_configured(env=env)['catalog']['available']
    row = next(row for row in selfcheck.run(env=env)['rows'] if row['key']=='source_catalog')
    assert row['state'] == 'missing'
