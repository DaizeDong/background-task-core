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
import convos
import task_delete


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


@pytest.fixture
def user_state(private, tmp_path, monkeypatch):
    generator = load("console_user_state_fixture", ROOT / "tools/make_fixtures.py")
    case = generator.user_state_storage_case(tmp_path / "inputs")
    base = private["companion"].root / "data/user-state"
    case.update(cache_path=base / "convo-cache.json", archive_path=base / "deleted-tasks")

    def unexpected_scheduler_call(*args, **kwargs):
        pytest.fail("Storage admission tests must not call Task Scheduler")

    for name in ("_describe", "_unregister", "_presence"):
        monkeypatch.setattr(task_delete, name, unexpected_scheduler_call)
    return case


def tree_contents(root):
    """Include directories so refused writes cannot hide newly created containers."""
    return {path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
            for path in root.rglob("*")}


def flip_private_on_admission(private, monkeypatch, number):
    """Change real Git publication state immediately before a selected admission."""
    actual = store.authorize_write
    calls = []

    def authorize(*args, **kwargs):
        calls.append(kwargs["artifact_id"])
        if len(calls) == number:
            private["companion"].git("config", "remote.origin.pushurl",
                                     "https://github.com/example-owner/synthetic-public.git")
        return actual(*args, **kwargs)

    monkeypatch.setattr(store, "authorize_write", authorize)
    return calls


def test_conversation_cache_writes_only_the_declared_private_leaf(private, user_state):
    path = user_state["cache_path"]
    convos._write_cache_atomically(path, user_state["cache"])
    assert json.loads(path.read_text(encoding="utf-8")) == user_state["cache"]
    assert list(path.parent.iterdir()) == [path]
    assert store.authorize_write(path, artifact_id="conversation-cache").path == path


@pytest.mark.parametrize("kind", ["public", "unknown", "loose", "undeclared", "ignored"])
def test_conversation_cache_refusal_preserves_existing_bytes(private, user_state, tmp_path, kind):
    path = user_state["cache_path"]
    repo = private["companion"]
    if kind in {"public", "unknown"}:
        repo.git("config", "remote.origin.pushurl",
                 "https://github.com/example-owner/synthetic-" + kind + ".git")
    elif kind == "loose":
        path = tmp_path / "loose/convo-cache.json"
    elif kind == "undeclared":
        path = path.with_name("convo-cache-copy.json")
    else:
        (repo.root / ".gitignore").write_text("data/user-state/convo-cache.json\n", encoding="utf-8")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(user_state["cache"]), encoding="utf-8")
    before = tree_contents(path.parent)
    with pytest.raises((OSError, ValueError, RuntimeError)):
        convos._write_cache_atomically(path, user_state["cache"])
    assert tree_contents(path.parent) == before


def test_scan_returns_transcripts_and_an_explicit_cache_refusal(private, user_state):
    private["companion"].git("config", "remote.origin.pushurl",
                             "https://github.com/example-owner/synthetic-public.git")
    result = convos.scan(root=str(user_state["sessions"]), cache=str(user_state["cache_path"]))
    assert result["available"] and result["summary"]["files"] == 1
    assert not result["summary"]["cached"] and result["summary"]["cacheError"]
    assert result["groups"][0]["shown"][0]["id"] == user_state["session_id"]
    assert not user_state["cache_path"].parent.exists()


@pytest.mark.parametrize("failure", ["malformed-json", "top-list", "top-null", "entry-list",
                                     "entry-null", "read-error"])
def test_scan_preserves_invalid_or_unreadable_cache_evidence(private, user_state, monkeypatch, failure):
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


@pytest.mark.parametrize("admission", [3, 4, 5])
def test_cache_rechecks_private_before_creation_and_publication(private, user_state, monkeypatch, admission):
    path = user_state["cache_path"]
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(user_state["cache"]), encoding="utf-8")
    previous = path.read_bytes()
    calls = flip_private_on_admission(private, monkeypatch, admission)
    with pytest.raises((OSError, ValueError, RuntimeError)):
        convos._write_cache_atomically(path, user_state["cache"])
    assert len(calls) == admission and path.read_bytes() == previous
    staging = list(path.parent.glob("convo-cache.json.tmp-*"))
    assert len(staging) == (0 if admission == 3 else 1)
    if staging:
        assert json.loads(staging[0].read_text(encoding="utf-8")) == user_state["cache"]


def test_private_task_archive_keeps_recoverable_xml_and_paired_receipt(private, user_state):
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


@pytest.mark.parametrize("kind", ["public", "unknown", "loose", "undeclared", "ignored"])
def test_task_archive_refuses_unproven_or_unversionable_storage(private, user_state, tmp_path, kind):
    directory, task = user_state["archive_path"], user_state["task"]
    repo = private["companion"]
    if kind in {"public", "unknown"}:
        repo.git("config", "remote.origin.pushurl",
                 "https://github.com/example-owner/synthetic-" + kind + ".git")
    elif kind == "loose":
        directory = tmp_path / "loose/deleted-tasks"
    elif kind == "undeclared":
        directory = directory.with_name("deleted-tasks-copy")
    else:
        (repo.root / ".gitignore").write_text("data/user-state/deleted-tasks/*.json\n", encoding="utf-8")
    directory.mkdir(parents=True)
    selected, error = task_delete._archive_dir({task_delete.ARCHIVE_ENV: str(directory)})
    if kind == "ignored":
        assert selected == directory and error is None
    else:
        assert selected is None and error
    before = tree_contents(directory)
    ok, error, _ = task_delete._archive(directory, task["name"], user_state["reason"], task)
    assert not ok and error
    assert tree_contents(directory) == before


@pytest.mark.parametrize("admission,retained", [(3, set()), (4, {"xml"}),
                                               (5, {"xml", "json"})])
def test_task_archive_rechecks_private_before_writes_and_readback(
        private, user_state, monkeypatch, admission, retained):
    directory, task = user_state["archive_path"], user_state["task"]
    directory.mkdir(parents=True)
    calls = flip_private_on_admission(private, monkeypatch, admission)
    ok, error, files = task_delete._archive(directory, task["name"], user_state["reason"], task)
    assert not ok and error and len(calls) == admission
    assert {path.suffix[1:] for path in directory.iterdir()} == retained
    if "xml" in retained:
        assert Path(files["xml"]).read_bytes() == task["xml"].encode("utf-16")
    if "json" in retained:
        assert json.loads(Path(files["receipt"]).read_text(encoding="utf-8"))["xmlSha256"] == task["sha256"]


@pytest.mark.parametrize("writer", ["cache", "archive"])
def test_user_state_writers_require_the_source_contract(private, user_state, monkeypatch, tmp_path, writer):
    api, resolver = store._load_storage(), store._load_datadir()
    source = tmp_path / "source-without-contract"
    source.mkdir()
    monkeypatch.setattr(store, "SOURCE_ROOT", source)
    monkeypatch.setattr(store, "_storage", api)
    monkeypatch.setattr(store, "_storage_root", source)
    monkeypatch.setattr(store, "_datadir", resolver)
    if writer == "cache":
        with pytest.raises((OSError, ValueError, RuntimeError)):
            convos._write_cache_atomically(user_state["cache_path"], user_state["cache"])
        assert not user_state["cache_path"].parent.exists()
    else:
        directory, task = user_state["archive_path"], user_state["task"]
        directory.mkdir(parents=True)
        selected, error = task_delete._archive_dir({task_delete.ARCHIVE_ENV: str(directory)})
        assert selected is None and error
        ok, error, _ = task_delete._archive(directory, task["name"], user_state["reason"], task)
        assert not ok and error and not list(directory.iterdir())


def test_task_archive_container_never_authorizes_unknown_children(private, user_state):
    directory = user_state["archive_path"]
    directory.mkdir(parents=True)
    store.authorize_write(directory, artifact_id="task-delete-container", directory=True)
    for path in (directory / "notes.json", directory / "AcmeSync.xml",
                 directory / "nested/AcmeSync-20991231T235959Z.xml"):
        with pytest.raises((ValueError, RuntimeError)):
            store.authorize_write(path)
    assert not list(directory.iterdir())
