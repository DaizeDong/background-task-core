"""Generated Git repositories exercise the real source-owned writer boundary."""
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/task_console"))
import console_store as store


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def private(tmp_path, monkeypatch):
    generator = load("console_guard_fixtures", ROOT / "guards/tools/make_fixtures.py")
    layout = generator.make_storage_contract_fixture(tmp_path, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    for key in list(os.environ):
        if key.startswith(("GIT_", "TASK_CONSOLE_")):
            monkeypatch.delenv(key)
    for key, value in layout["companion"].env.items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)
    api = store._load_storage()
    actual = api.authorize_artifact_write
    monkeypatch.setattr(api, "authorize_artifact_write",
                        lambda *a, **kw: actual(*a, **kw, visibility_map=layout["receipt"]))
    root = layout["companion"].root
    monkeypatch.setenv("TASK_CONSOLE_CONFIG", str(root))
    layout["db"] = root / "data/task-console/console.sqlite3"
    return layout


def test_writer_accepts_private_declared_database_and_preserves_read_only_discovery(private):
    path, state = store.resolve_db()
    assert state is None and path == private["db"]
    assert not path.parent.exists()
    path, state = store.resolve_db(create_parent=True)
    assert state is None and path.parent.is_dir() and not path.exists()


@pytest.mark.parametrize("route", ["public", "unknown"])
def test_override_rejects_unproven_push_destination(private, monkeypatch, route):
    repo = private["companion"]
    repo.git("config", "remote.origin.pushurl", "https://github.com/example-owner/synthetic-" + route + ".git")
    monkeypatch.setenv("TASK_CONSOLE_DB", str(private["db"]))
    path, state = store.resolve_db(create_parent=True)
    assert path is None and state.code == "STORAGE_REFUSED"
    assert not private["db"].parent.exists()


@pytest.mark.parametrize("kind", ["loose", "source", "undeclared", "ignored"])
def test_override_cannot_bypass_storage_admission(private, monkeypatch, tmp_path, kind):
    path = {"loose": tmp_path / "loose/console.sqlite3", "source": ROOT / "data/console.sqlite3",
            "undeclared": private["companion"].root / "other.sqlite3", "ignored": private["db"]}[kind]
    if kind == "ignored":
        (private["companion"].root / ".gitignore").write_text("data/task-console/console.sqlite3\n")
    monkeypatch.setenv("TASK_CONSOLE_DB", str(path))
    resolved, state = store.resolve_db(create_parent=True)
    assert resolved is None and state is not None
    assert not path.exists()


@pytest.mark.parametrize("cached", [None, False])
def test_missing_pinned_resolver_refuses_even_override(private, monkeypatch, tmp_path, cached):
    monkeypatch.setattr(store, "SOURCE_ROOT", tmp_path / "missing-payload")
    monkeypatch.setattr(store, "_datadir", cached)
    monkeypatch.setenv("TASK_CONSOLE_DB", str(private["db"]))
    assert store.resolve_db(create_parent=True)[1].code == "NO_RESOLVER"
    assert not private["db"].parent.exists()


def test_config_alias_precedence_and_missing_explicit_root(private, monkeypatch, tmp_path):
    chosen = tmp_path / "absent-a"
    monkeypatch.setenv("TASK_CONSOLE_CONFIG", str(chosen))
    monkeypatch.setenv("TASK_CONSOLE_CONFIG_DIR", str(private["companion"].root))
    assert store.companion_root() == chosen
    assert store.resolve_db()[1] is not None
    monkeypatch.delenv("TASK_CONSOLE_CONFIG")
    assert store.companion_root() == private["companion"].root


def test_sidecars_have_exact_transient_permission_and_export_is_versionable(private):
    root = private["companion"].root
    (root / ".gitignore").write_text("*-wal\n*-shm\n*-journal\n", encoding="utf-8")
    assert store.resolve_db(create_parent=True)[1] is None
    export = private["db"].parent / "run-events.jsonl"
    store.authorize_write(export, artifact_id="run-events")
    (root / ".gitignore").write_text("*.jsonl\n", encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError)):
        store.authorize_write(export, artifact_id="run-events")


def test_native_initializer_preserves_existing_settings_and_does_not_create_db(private):
    initializer = load("console_native_init", ROOT / "tools/init_config.py")
    root = private["companion"].root
    path, created = initializer.initialize(root)
    assert created and "TASK_CONSOLE_READ_ONLY" in path.read_text(encoding="utf-8")
    before = path.read_bytes()
    assert initializer.initialize(root) == (path, False)
    assert path.read_bytes() == before and not private["db"].exists()
    doctor = load("console_native_doctor", ROOT / "tools/verify_config.py")
    result = doctor.doctor()
    assert not result["ready"] and any("TASK_CONSOLE_DB" in problem for problem in result["problems"])


def test_native_settings_resolve_each_companion_with_identical_template_bytes(private, tmp_path):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell is required to consume native settings")
    generator = load("console_second_guard_fixture", ROOT / "guards/tools/make_fixtures.py")
    second = generator.make_storage_contract_fixture(
        tmp_path / "second", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    initializer = load("console_portable_init", ROOT / "tools/init_config.py")
    roots = [private["companion"].root, second["companion"].root]
    templates = []
    for root in roots:
        path, created = initializer.initialize(root)
        assert created
        templates.append(path.read_bytes())
        literal = "'" + str(path).replace("'", "''") + "'"
        command = (". " + literal + "; [pscustomobject]@{config=$env:TASK_CONSOLE_CONFIG; "
                   "database=$env:TASK_CONSOLE_DB} | ConvertTo-Json -Compress")
        execution = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", command],
                                   capture_output=True, text=True, check=True, timeout=30)
        selected = json.loads(execution.stdout)
        assert Path(selected["config"]) == root
        assert Path(selected["database"]) == root / "data/task-console/console.sqlite3"
    assert templates[0] == templates[1]


@pytest.mark.parametrize("defect", [None, "missing-table", "missing-column", "version"])
def test_native_doctor_checks_the_complete_read_model_schema(private, monkeypatch, defect):
    generator = load("console_schema_fixture", ROOT / "tools/make_fixtures.py")
    generator.configuration_database(private["db"], defect)
    doctor = load("console_schema_doctor", ROOT / "tools/verify_config.py")
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: object())
    before = private["db"].read_bytes()
    result = doctor.doctor()
    assert result["ready"] is (defect is None)
    assert result["checks"].get("database_schema", False) is (defect is None)
    assert private["db"].read_bytes() == before
    assert not any(private["db"].parent.glob("*.sqlite3-*"))
