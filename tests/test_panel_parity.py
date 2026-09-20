"""Behavioral static-route and module-loader tests; no live maintenance calls."""
import http.client
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "task_console"))
sys.path.insert(0, str(ROOT))
from tools.make_fixtures import health_case, catalog_snapshot
import component_status
import server


@pytest.fixture
def synthetic_server(monkeypatch):
    case = health_case()
    snapshot = {"schemaVersion": 1, "tasks": [case], "catalog": catalog_snapshot()}
    response = component_status.evaluate_snapshot(snapshot, case["now"])
    monkeypatch.setattr(component_status, "read_configured", lambda: response)

    class SyntheticHandler(server.Handler):
        token = "synthetic-browser-token"

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticHandler)
    port = httpd.server_address[1]
    SyntheticHandler.allowed_hosts = {f"127.0.0.1:{port}"}
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield port, response
    httpd.shutdown()
    thread.join()
    httpd.server_close()


def get(port, path, token=None, host=None):
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if token is not None:
        headers["X-Console-Token"] = token
    client.request("GET", path, headers=headers)
    response = client.getresponse()
    result = response.status, dict(response.getheaders()), response.read().decode("utf-8")
    client.close()
    return result


def test_static_modules_have_exact_mime_and_no_bootstrap_token(synthetic_server):
    port, _ = synthetic_server
    for name in server.STATIC_FILES:
        status, headers, body = get(port, "/static/" + name)
        assert status == 200
        assert headers["Content-Type"] == ("text/css; charset=utf-8" if name.endswith(".css") else "text/javascript; charset=utf-8")
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "synthetic-browser-token" not in body
        assert "__TOKEN__" not in body


@pytest.mark.parametrize("path", ["../server.py", "%2e%2e/server.py", "panels/../../server.py",
                                   "%2f%2fexample.invalid/share", "C:/example", "panels\\tasks.js",
                                   "styles.css/extra", "not-allowlisted.js", "%61pp.js"])
def test_static_traversal_and_unlisted_paths_rejected(synthetic_server, path):
    assert get(synthetic_server[0], "/static/" + path)[0] == 404


def test_host_guard_precedes_static_and_token_bootstrap(synthetic_server):
    port, _ = synthetic_server
    for path in ("/", "/static/app.js", "/static/styles.css"):
        status, _, body = get(port, path, host="example.invalid")
        assert status == 400
        assert "synthetic-browser-token" not in body
    status, _, html = get(port, "/")
    assert status == 200
    assert '<meta name="console-token" content="synthetic-browser-token">' in html
    assert "__TOKEN__" not in html


def test_components_auth_and_fixture_verdicts(synthetic_server):
    port, expected = synthetic_server
    assert get(port, "/api/components")[0] == 403
    assert get(port, "/api/components", token="wrong")[0] == 403
    status, _, body = get(port, "/api/components", token="synthetic-browser-token")
    assert status == 200
    assert json.loads(body) == expected


def node(program):
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node is not installed; module behavior pending")
    result = subprocess.run([executable, "-"], input=program, text=True, capture_output=True,
                            encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def module_source(name):
    return (server.STATIC / name).read_text(encoding="utf-8")


def test_catalog_panel_shows_sources_roles_auth_and_statistics():
    data = {"skills": {"available": False, "reason": "unchecked"},
            "plugins": {"available": False, "reason": "unchecked"},
            "components": {"available": False, "reason": "unchecked", "catalog": component_status.catalog_view(catalog_snapshot())}}
    sources = [module_source(name) for name in ("api.js", "panels/skills.js", "panels/plugins.js")]
    result = node("""
const vm=require('node:vm');
const elements={};
const document={querySelector:()=>({content:'synthetic-token'}),createElement:()=>({}),getElementById:id=>elements[id] ||= {innerHTML:'',addEventListener:()=>{},appendChild:()=>{}}};
const context=vm.createContext({document});
""" + "\n".join("vm.runInContext(" + json.dumps(source) + ",context);" for source in sources)
        + "vm.runInContext(" + json.dumps("MAINT=" + json.dumps(data) + ";renderMaint();") + ",context);"
        + "console.log(JSON.stringify(elements['mt-catalog'].innerHTML+elements['catalog-results'].innerHTML));")
    for expected in ("3 项", "角色模板", "shared", "codex", "<dt>认证</dt><dd>未检查</dd>", "MCP 连接 (1)", "部分检查"):
        assert expected in result


def test_loader_failure_is_visible_and_stops_initialization():
    result = node("""
const vm=require('node:vm');
const failure={hidden:true,textContent:''}, requested=[];
const document={getElementById:()=>failure,documentElement:{dataset:{}},createElement:()=>({}),
head:{appendChild:s=>{requested.push(s.src);s.onerror();}}};
const window={addEventListener:()=>{}};
""" + "vm.runInNewContext(" + json.dumps(module_source("app.js")) + ",{document,window});"
        + "setImmediate(()=>console.log(JSON.stringify({failure,requested,ready:document.documentElement.dataset.consoleReady||false}))); ")
    assert result["failure"]["hidden"] is False
    assert result["failure"]["textContent"].startswith("Console module failed:")
    assert result["requested"] == ["/static/api.js"]
    assert result["ready"] is False


def test_api_reads_bootstrap_and_sends_token_only_in_header():
    result = node("""
const vm=require('node:vm');let request;
const document={querySelector:()=>({content:'synthetic-token'})};
const fetch=async (path,options)=>{request={path,options};return {ok:true,json:async()=>({ok:true})};};
const context=vm.createContext({document,fetch});
""" + "vm.runInContext(" + json.dumps(module_source("api.js")) + ",context);"
        + "vm.runInContext(\"api('/api/components')\",context).then(()=>console.log(JSON.stringify(request)));")
    assert result["path"] == "/api/components"
    assert result["options"]["headers"]["X-Console-Token"] == "synthetic-token"


def test_all_modules_are_allowlisted_and_loaded_once():
    names = json.loads(re.search(r"const CONSOLE_MODULES = (\[.*?\]);", module_source("app.js"), re.S).group(1))
    assert len(names) == len(set(names))
    styles = set(re.findall(r'href="/static/([^\"]+\.css)"', server.PAGE.read_text(encoding="utf-8")))
    html = server.PAGE.read_text(encoding="utf-8")
    bootstrap = re.findall(r'src="/static/([^\"]+\.js)"', html)
    assert len(bootstrap) == len(set(bootstrap))
    assert not set(bootstrap) & set(names)
    assert set(names) | set(bootstrap) | styles == server.STATIC_FILES
    assert html.index('/static/theme.js') < html.index('/vendor/tabler/tabler.min.css')
    assert names[-1] == "events.js"


def test_panel_modules_initialize_in_one_shared_scope():
    names = json.loads(re.search(r"const CONSOLE_MODULES = (\[.*?\]);", module_source("app.js"), re.S).group(1))
    program = """
const vm=require('node:vm');
const document={querySelector:()=>({content:'synthetic-token'})};
const localStorage={getItem:()=>null};
const context=vm.createContext({document,localStorage});
"""
    for name in names[:-1]:
        program += "vm.runInContext(" + json.dumps(module_source(name)) + ",context);\n"
    program += "console.log(JSON.stringify(vm.runInContext('[typeof render,typeof renderMaint,typeof showView,typeof repoCommitPush,typeof renderLLM]',context)));"
    assert node(program) == ["function"] * 5


def test_static_modules_are_in_package_data():
    import tomllib
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = package["tool"]["setuptools"]["package-data"]["task_console"]
    included = {path.resolve() for pattern in patterns for path in server.HERE.glob(pattern)}
    assert all((server.STATIC / name).resolve() in included for name in server.STATIC_FILES)
