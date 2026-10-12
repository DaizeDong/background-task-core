"""Storage resolution is a path decision: companion selection plus the own-source-tree refusal."""
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
import convos
import task_delete


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def companion(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("TASK_CONSOLE_"):
            monkeypatch.delenv(key)
    root = tmp_path / "acme-console-config"
    root.mkdir()
    monkeypatch.setenv("TASK_CONSOLE_CONFIG", str(root))
    return {"root": root, "db": root / "data/task-console/console.sqlite3"}


def test_resolution_reads_without_creating_and_creates_only_the_parent_on_request(companion):
    path, state = store.resolve_db()
    assert state is None and path == companion["db"]
    assert not path.parent.exists()
    path, state = store.resolve_db(create_parent=True)
    assert state is None and path.parent.is_dir() and not path.exists()


def test_resolution_runs_no_git_or_network_subprocess(companion, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("storage resolution must not start a subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert store.resolve_db(create_parent=True)[1] is None


def test_override_inside_the_source_tree_is_refused(companion, monkeypatch):
    path = ROOT / "data/console.sqlite3"
    monkeypatch.setenv("TASK_CONSOLE_DB", str(path))
    resolved, state = store.resolve_db(create_parent=True)
    assert resolved is None and state.code == "STORAGE_REFUSED"
    assert not path.parent.exists()


def test_override_outside_the_source_tree_is_used_as_given(companion, monkeypatch, tmp_path):
    path = tmp_path / "elsewhere/console.sqlite3"
    monkeypatch.setenv("TASK_CONSOLE_DB", str(path))
    assert store.resolve_db(create_parent=True) == (path, None)


def test_no_companion_is_a_stated_condition(companion, monkeypatch):
    class Resolver:
        @staticmethod
        def resolve_companion_root(skill):
            return None

    monkeypatch.delenv("TASK_CONSOLE_CONFIG")
    monkeypatch.setattr(store, "_datadir", Resolver())
    path, state = store.resolve_db()
    assert path is None and state.code == "NO_COMPANION"


@pytest.mark.parametrize("cached", [None, False])
def test_missing_pinned_resolver_refuses_even_override(companion, monkeypatch, tmp_path, cached):
    monkeypatch.setattr(store, "SOURCE_ROOT", tmp_path / "missing-payload")
    monkeypatch.setattr(store, "_datadir", cached)
    monkeypatch.setenv("TASK_CONSOLE_DB", str(companion["db"]))
    assert store.resolve_db(create_parent=True)[1].code == "NO_RESOLVER"
    assert not companion["db"].parent.exists()


def test_config_alias_precedence_and_missing_explicit_root(companion, monkeypatch, tmp_path):
    chosen = tmp_path / "absent-a"
    monkeypatch.setenv("TASK_CONSOLE_CONFIG", str(chosen))
    monkeypatch.setenv("TASK_CONSOLE_CONFIG_DIR", str(companion["root"]))
    assert store.companion_root() == chosen
    monkeypatch.delenv("TASK_CONSOLE_CONFIG")
    assert store.companion_root() == companion["root"]


def test_native_initializer_preserves_existing_settings_and_does_not_create_db(companion):
    initializer = load("console_native_init", ROOT / "tools/init_config.py")
    root = companion["root"]
    path, created = initializer.initialize(root)
    assert created and "TASK_CONSOLE_READ_ONLY" in path.read_text(encoding="utf-8")
    before = path.read_bytes()
    assert initializer.initialize(root) == (path, False)
    assert path.read_bytes() == before and not companion["db"].exists()
    doctor = load("console_native_doctor", ROOT / "tools/verify_config.py")
    result = doctor.doctor()
    assert not result["ready"] and any("TASK_CONSOLE_DB" in problem for problem in result["problems"])


def test_native_initializer_refuses_the_source_tree():
    initializer = load("console_native_init_source", ROOT / "tools/init_config.py")
    with pytest.raises(RuntimeError):
        initializer.initialize(ROOT / "synthetic-settings")
    assert not (ROOT / "synthetic-settings").exists()


def test_native_settings_resolve_each_companion_with_identical_template_bytes(companion, tmp_path):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell is required to consume native settings")
    second = tmp_path / "second-config"
    second.mkdir()
    initializer = load("console_portable_init", ROOT / "tools/init_config.py")
    templates = []
    for root in (companion["root"], second):
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
def test_native_doctor_checks_the_complete_read_model_schema(companion, monkeypatch, defect):
    generator = load("console_schema_fixture", ROOT / "tools/make_fixtures.py")
    generator.configuration_database(companion["db"], defect)
    doctor = load("console_schema_doctor", ROOT / "tools/verify_config.py")
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: object())
    before = companion["db"].read_bytes()
    result = doctor.doctor()
    assert result["ready"] is (defect is None)
    assert result["checks"].get("database_schema", False) is (defect is None)
    assert companion["db"].read_bytes() == before
    assert not any(companion["db"].parent.glob("*.sqlite3-*"))


@pytest.fixture
def user_state(companion, tmp_path, monkeypatch):
    generator = load("console_user_state_fixture", ROOT / "tools/make_fixtures.py")
    case = generator.user_state_storage_case(tmp_path / "inputs")
    base = companion["root"] / "data/user-state"
    case.update(cache_path=base / "convo-cache.json", archive_path=base / "deleted-tasks")

    def unexpected_scheduler_call(*args, **kwargs):
        pytest.fail("Storage tests must not call Task Scheduler")

    for name in ("_describe", "_unregister", "_presence"):
        monkeypatch.setattr(task_delete, name, unexpected_scheduler_call)
    return case


def tree_contents(root):
    """Include directories so refused writes cannot hide newly created containers."""
    return {path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
            for path in root.rglob("*")}


def test_conversation_cache_publishes_one_complete_file(user_state):
    path = user_state["cache_path"]
    convos._write_cache_atomically(path, user_state["cache"])
    assert json.loads(path.read_text(encoding="utf-8")) == user_state["cache"]
    assert list(path.parent.iterdir()) == [path]


def test_scan_reports_a_cache_inside_the_source_tree_and_still_returns_transcripts(user_state):
    cache = ROOT / "synthetic-cache/convo-cache.json"
    result = convos.scan(root=str(user_state["sessions"]), cache=str(cache))
    assert result["available"] and result["summary"]["files"] == 1
    assert not result["summary"]["cached"] and result["summary"]["cacheError"]
    assert result["groups"][0]["shown"][0]["id"] == user_state["session_id"]
    assert not cache.parent.exists()


@pytest.mark.parametrize("failure", ["malformed-json", "top-list", "top-null", "entry-list",
                                     "entry-null", "read-error"])
def test_scan_preserves_invalid_or_unreadable_cache_evidence(user_state, monkeypatch, failure):
    path = user_state["cache_path"]
    path.parent.mkdir(parents=True)
    payload = (json.dumps(user_state["cache"]).encode("utf-8") if failure == "read-error"
               else user_state["invalid_cache"][failure])
    path.write_bytes(payload)
    before = tree_contents(path.parent)
    if failure == "read-error":
        original = Path.read_text

        def unreadable(selected, *args, **kwargs):
            if selected == path:
                raise OSError("synthetic cache read failure")
            return original(selected, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", unreadable)
    result = convos.scan(root=str(user_state["sessions"]), cache=str(path))
    assert result["available"] and result["summary"]["files"] == 1
    assert not result["summary"]["cached"] and result["summary"]["cacheError"]
    assert result["groups"][0]["shown"][0]["id"] == user_state["session_id"]
    assert tree_contents(path.parent) == before


def test_task_archive_keeps_recoverable_xml_and_paired_receipt(user_state):
    directory, task = user_state["archive_path"], user_state["task"]
    directory.mkdir(parents=True)
    assert task_delete._archive_dir({task_delete.ARCHIVE_ENV: str(directory)}) == (directory, None)
    ok, error, files = task_delete._archive(directory, task["name"], user_state["reason"], task)
    assert ok and not error
    xml, receipt_path = Path(files["xml"]), Path(files["receipt"])
    assert xml.read_bytes() == task["xml"].encode("utf-16")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["schema"] == 1 and receipt["name"] == task["name"]
    assert receipt["reason"] == user_state["reason"] and receipt["actions"] == task["actions"]
    assert receipt["xml"] == xml.name and receipt["xmlSha256"] == task["sha256"]
    assert xml.stem == receipt_path.stem == "Acme_Backup_Daily-" + receipt["exportedAt"]
    assert set(directory.iterdir()) == {xml, receipt_path}


def test_task_archive_directory_inside_the_source_tree_is_refused():
    selected, error = task_delete._archive_dir({task_delete.ARCHIVE_ENV: str(ROOT / "scripts")})
    assert selected is None and error
