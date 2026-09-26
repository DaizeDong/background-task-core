"""Deletion acceptance uses generated disposable trees and a simulated plugin CLI."""
import json
import http.client
import os
import subprocess

import pytest

import deletion as D
import maint
from test_panel_parity import synthetic_server
from tools.make_fixtures import skill_delete_case, plugin_delete_case


@pytest.fixture
def skill(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    target = skill_delete_case(root)
    monkeypatch.setenv("TASK_CONSOLE_SKILLS", str(root))
    monkeypatch.setenv("TASK_CONSOLE_SKILL_ARCHIVE", str(tmp_path / "archive"))
    return target


def test_preview_has_exact_scope_and_execution_requires_issued_token(skill):
    with pytest.raises(maint.Refused):
        D.apply("not-a-plan")
    plan = D.plan("skill", skill.name, "live")
    assert plan["files"] == 2 and plan["bytes"] > 0 and plan["path"] == str(skill)
    assert skill.exists()
    assert D.apply(plan["token"])["ok"] is True
    assert not skill.exists()
    with pytest.raises(maint.Refused):
        D.apply(plan["token"])


def test_changed_or_replaced_target_refuses_stale_preview(skill):
    plan = D.plan("skill", skill.name, "live")
    (skill / "guide.txt").write_text("Changed synthetic content", encoding="utf-8")
    with pytest.raises(maint.Refused, match="变化"):
        D.apply(plan["token"])
    assert skill.exists()


@pytest.mark.parametrize("name", ["..", "../acme", "C:/Acme", "a/b", "", "acme."])
def test_bad_names_are_refused(skill, name):
    with pytest.raises(maint.Refused):
        D.plan("skill", name, "live")


def test_archive_selection_and_nested_junction_preserve_external_target(skill, tmp_path):
    outside = skill_delete_case(tmp_path / "outside")
    link = skill / "linked"
    if os.name == "nt":
        # Native PowerShell creates a disposable junction. Deletion remains in Python.
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        f"New-Item -ItemType Junction -Path {quote(link)} -Target {quote(outside)} | Out-Null"], check=True)
    else:
        link.symlink_to(outside, target_is_directory=True)
    plan = D.plan("skill", skill.name, "live")
    assert plan["files"] == 2 and plan["links"] == 1
    assert D.apply(plan["token"])["ok"]
    assert (outside / "guide.txt").is_file()


def test_top_level_junction_only_removes_registration(skill, tmp_path):
    outside = skill.parent / "acme-link"
    if os.name == "nt":
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        f"New-Item -ItemType Junction -Path {quote(outside)} -Target {quote(skill)} | Out-Null"], check=True)
    else:
        outside.symlink_to(skill, target_is_directory=True)
    plan = D.plan("skill", outside.name, "live")
    assert plan["linked"] is True and plan["files"] == 0
    assert D.apply(plan["token"])["ok"]
    assert not os.path.lexists(outside) and (skill / "guide.txt").exists()


def test_partial_failure_is_explicit_and_plan_cannot_be_replayed(skill, monkeypatch):
    plan = D.plan("skill", skill.name, "live")
    def fail(path):
        (path / "guide.txt").unlink()
        raise PermissionError("synthetic file is busy")
    monkeypatch.setattr(D.shutil, "rmtree", fail)
    result = D.apply(plan["token"])
    assert not result["ok"] and result["partial"] and "synthetic" in result["error"]
    with pytest.raises(maint.Refused):
        D.apply(plan["token"])


def test_plugin_uninstall_is_scoped_keeps_data_and_checks_result(monkeypatch):
    rows = [plugin_delete_case()]
    monkeypatch.setattr(maint, "read_plugins", lambda: {"available": True, "plugins": rows.copy()})
    monkeypatch.setattr(maint, "_claude", lambda: "synthetic-cli")
    calls = []
    def execute(args, **kwargs):
        calls.append(args)
        rows.clear()
        return subprocess.CompletedProcess(args, 0, "{}", "")
    monkeypatch.setattr(D.subprocess, "run", execute)
    plan = D.plan("plugin", "acme@example", "user")
    assert D.apply(plan["token"])["ok"]
    assert calls == [["synthetic-cli", "plugin", "uninstall", "acme@example", "--scope", "user", "--keep-data", "--json"]]


def test_plugin_project_scope_and_changed_registration_are_refused(monkeypatch):
    rows = [plugin_delete_case()]
    monkeypatch.setattr(maint, "read_plugins", lambda: {"available": True, "plugins": rows.copy()})
    with pytest.raises(maint.Refused):
        D.plan("plugin", "acme@example", "project")
    plan = D.plan("plugin", "acme@example", "user")
    rows[0] = dict(rows[0], version="2.0")
    with pytest.raises(maint.Refused, match="变化"):
        D.apply(plan["token"])


def test_directory_plugin_cannot_be_sent_to_cli_uninstall(monkeypatch):
    plugin = plugin_delete_case()
    plugin['name'] = 'acme@skills-dir'
    monkeypatch.setattr(D.maint, 'read_plugins', lambda: {'available': True, 'plugins': [plugin]})
    with pytest.raises(maint.Refused, match='技能'):
        D.plan('plugin', plugin['name'], 'user')


def test_expired_plan_is_refused(skill, monkeypatch):
    plan = D.plan("skill", skill.name, "live")
    monkeypatch.setattr(D.time, "monotonic", lambda: float("inf"))
    with pytest.raises(maint.Refused, match="过期"):
        D.apply(plan["token"])
    assert skill.exists()


def test_deletion_routes_auth_readonly_and_preview_apply(synthetic_server, skill, monkeypatch):
    port = synthetic_server[0]
    def post(path, body, token="synthetic-browser-token", host=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("POST", path, json.dumps(body), {"X-Console-Token": token, "Host": host or f"127.0.0.1:{port}"})
        response = connection.getresponse()
        status, data = response.status, json.loads(response.read())
        connection.close()
        return status, data
    body = {"kind": "skill", "name": skill.name, "location": "live"}
    assert post("/api/maintenance/plan", body, token="wrong")[0] == 403
    assert post("/api/maintenance/plan", body, host="example.invalid")[0] == 400
    monkeypatch.setenv("TASK_CONSOLE_READ_ONLY", "1")
    assert post("/api/maintenance/plan", body)[0] == 403
    monkeypatch.delenv("TASK_CONSOLE_READ_ONLY")
    assert post("/api/maintenance/plan", [body])[0] == 400
    status, plan = post("/api/maintenance/plan", body)
    assert status == 200 and skill.exists()
    assert post("/api/maintenance/delete", {"token": plan["token"]}, token="wrong")[0] == 403
    assert post("/api/maintenance/delete", {"token": plan["token"]})[1]["ok"]
    assert not skill.exists()
