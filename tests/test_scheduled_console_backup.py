"""Scheduled recovery selects the bound source and never publishes unrelated work."""
from contextlib import closing
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import backup_console_db as recovery
import make_fixtures


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


@pytest.fixture
def companion(tmp_path, monkeypatch):
    repo, connection = make_fixtures.scheduled_console_backup_case(tmp_path)
    # Keep real source selection; this local bare fixture cannot prove PRIVATE admission.
    store = recovery.console_store()
    monkeypatch.setattr(store, "authorize_write", lambda *args, **kwargs: None)
    monkeypatch.setattr(recovery, "console_store", lambda: store)
    monkeypatch.setattr(recovery, "private_data_root", lambda path: Path(path))
    with closing(connection):
        yield repo, connection, repo / "data/task-console/console.sqlite3"


def test_bound_source_overrides_ambient_ui_db_without_changing_environment(companion, monkeypatch):
    repo, _, source = companion
    monkeypatch.setenv("TASK_CONSOLE_DB", str(repo / "stale.sqlite3"))
    assert recovery.resolve_scheduled_source(str(source)) == repo / "data"
    assert recovery.os.environ["TASK_CONSOLE_DB"] == str(repo / "stale.sqlite3")


@pytest.mark.parametrize("source", ["relative.sqlite3", "other.sqlite3"])
def test_unsupported_source_refuses_instead_of_backing_up_default(companion, source):
    repo, _, _ = companion
    value = source if source.startswith("relative") else str(repo / source)
    with pytest.raises(ValueError):
        recovery.scheduled_backup(value)
    assert not (repo / "data/task-console/recovery").exists()


def test_missing_selected_database_is_an_error(companion):
    repo, _, _ = companion
    with pytest.raises(FileNotFoundError):
        recovery.scheduled_backup(str(repo / "absent/task-console/console.sqlite3"))


def test_private_proof_failure_precedes_writes(companion, monkeypatch):
    repo, _, source = companion

    def refuse(path):
        raise ValueError("synthetic unproven destination")

    monkeypatch.setattr(recovery, "private_data_root", refuse)
    with pytest.raises(ValueError, match="unproven"):
        recovery.scheduled_backup(str(source))
    assert not (repo / "data/task-console/recovery").exists()


@pytest.mark.parametrize("ambient", [None, "stale.sqlite3"])
def test_resolver_admission_refusal_precedes_writes_and_restores_environment(companion, monkeypatch, ambient):
    repo, _, source = companion
    previous = str(repo / ambient) if ambient else None
    if previous is None:
        monkeypatch.delenv("TASK_CONSOLE_DB", raising=False)
    else:
        monkeypatch.setenv("TASK_CONSOLE_DB", previous)
    selected = []

    def refuse(destination, *, artifact_id=None):
        selected.append((Path(destination), artifact_id))
        raise ValueError("Synthetic storage admission refusal")

    monkeypatch.setattr(recovery.console_store(), "authorize_write", refuse)
    before = git(repo, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="resolver refused the launcher-selected source"):
        recovery.scheduled_backup(str(source))
    assert selected == [(source, "database")]
    assert recovery.os.environ.get("TASK_CONSOLE_DB") == previous
    assert not (repo / "data/task-console/recovery").exists()
    assert git(repo, "rev-parse", "HEAD") == before


def test_foreign_staged_changes_remain_untouched(companion):
    repo, _, source = companion
    (repo / "README.md").write_text("Synthetic unrelated edit.\n")
    git(repo, "add", "README.md")
    before = git(repo, "diff", "--cached")
    with pytest.raises(ValueError, match="Other staged"):
        recovery.scheduled_backup(str(source))
    assert git(repo, "diff", "--cached") == before
    assert not (repo / "data/task-console/recovery/current.zip").exists()


def test_publication_is_pair_only_and_second_unchanged_run_makes_no_commit(companion):
    repo, _, source = companion
    (repo / "README.md").write_text("Synthetic unrelated unstaged edit.\n")
    result = recovery.scheduled_backup(str(source))
    assert result["status"] == "published"
    changed = set(git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines())
    assert changed == {"data/task-console/recovery/current.zip", "data/task-console/recovery/current.json"}
    assert git(repo, "status", "--porcelain").strip() == "M README.md"
    hooks = (repo / ".git/fixture-hooks-ran").read_text()
    assert hooks.count("pre-commit") == 2 and hooks.count("pre-push") == 2
    second = recovery.scheduled_backup(str(source))
    assert second["status"] == "unchanged" and second["commit"] == result["commit"]
    assert (repo / ".git/fixture-hooks-ran").read_text() == hooks
    assert git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == result["commit"]


def test_failed_push_retains_commit_and_retry_does_not_recommit(companion):
    repo, _, source = companion
    before = git(repo, "rev-parse", "HEAD")
    hook = repo / ".git/hooks/pre-push"
    previous_hook = hook.read_text()
    hook.write_text("#!/bin/sh\nexit 1\n", newline="\n")
    with pytest.raises(RuntimeError, match="Git push failed"):
        recovery.scheduled_backup(str(source))
    pending = git(repo, "rev-parse", "HEAD")
    assert pending != before
    assert git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == before
    assert recovery.backup(repo / "data", verify_only=True)["database"]["quick_check"] == "ok"
    hook.write_text(previous_hook, newline="\n")
    result = recovery.scheduled_backup(str(source))
    assert result["status"] == "published" and result["commit"] == pending


def test_unpublished_unrelated_commit_is_not_pushed(companion):
    repo, _, source = companion
    (repo / "README.md").write_text("Synthetic unrelated commit.\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "Synthetic unrelated change")
    with pytest.raises(ValueError, match="Unpublished commits"):
        recovery.scheduled_backup(str(source))
    assert not (repo / "data/task-console/recovery/current.zip").exists()


def test_wrong_branch_and_alternate_index_refuse(companion, monkeypatch):
    repo, _, source = companion
    git(repo, "switch", "-c", "synthetic-work")
    with pytest.raises(ValueError, match="requires main"):
        recovery.scheduled_backup(str(source))
    git(repo, "switch", "main")
    monkeypatch.setenv("GIT_INDEX_FILE", str(repo / "alternate-index"))
    with pytest.raises(ValueError, match="overrides"):
        recovery.scheduled_backup(str(source))


def test_concurrent_foreign_staging_is_not_committed_or_pushed(companion, monkeypatch):
    repo, _, source = companion
    original = recovery.git
    before = git(repo, "rev-parse", "HEAD")

    def concurrent(repository, *args, **kwargs):
        if args[0] == "commit":
            (repo / "README.md").write_text("Synthetic concurrent staged edit.\n")
            git(repo, "add", "README.md")
        return original(repository, *args, **kwargs)

    monkeypatch.setattr(recovery, "git", concurrent)
    with pytest.raises(ValueError, match="Other staged"):
        recovery.scheduled_backup(str(source))
    assert git(repo, "diff", "--cached", "--name-only") == "README.md"
    assert "README.md" not in git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
    assert git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == before


def test_pair_replacement_before_commit_is_never_published_or_retried(companion, monkeypatch):
    repo, _, source = companion
    original = recovery.git
    before = git(repo, "rev-parse", "HEAD")
    receipt_path = repo / "data/task-console/recovery/current.json"

    def substitute(repository, *args, **kwargs):
        if args[0] == "commit":
            receipt = json.loads(receipt_path.read_text())
            receipt["captured_at_utc"] = "synthetic concurrent replacement"
            receipt_path.write_text(json.dumps(receipt))
        return original(repository, *args, **kwargs)

    monkeypatch.setattr(recovery, "git", substitute)
    with pytest.raises(ValueError, match="differs from the verified capture"):
        recovery.scheduled_backup(str(source))
    assert json.loads(receipt_path.read_text())["captured_at_utc"] == "synthetic concurrent replacement"
    assert git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == before
    monkeypatch.setattr(recovery, "git", original)
    with pytest.raises(ValueError, match="inconsistent receipt"):
        recovery.scheduled_backup(str(source))
    assert git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == before


def test_same_head_branch_switch_during_capture_refuses_before_staging(companion, monkeypatch):
    repo, _, source = companion
    original = recovery._backup_locked
    before = git(repo, "rev-parse", "HEAD")

    def switch(root):
        receipt = original(root)
        git(repo, "switch", "-c", "synthetic-work")
        return receipt

    monkeypatch.setattr(recovery, "_backup_locked", switch)
    with pytest.raises(ValueError, match="requires main"):
        recovery.scheduled_backup(str(source))
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(repo, "diff", "--cached", "--name-only") == ""


@pytest.mark.parametrize("multiple", [False, True])
def test_distinct_or_multiple_push_destinations_refuse_before_capture(companion, multiple):
    repo, _, source = companion
    if multiple:
        git(repo, "config", "--add", "remote.origin.pushurl", git(repo, "remote", "get-url", "origin"))
    git(repo, "config", "--add", "remote.origin.pushurl", str(repo.parent / "other-remote.git"))
    with pytest.raises(ValueError, match="one identical effective"):
        recovery.scheduled_backup(str(source))
    assert not (repo / "data/task-console/recovery/current.zip").exists()


def test_no_source_never_infers_an_initialized_database(companion, monkeypatch):
    _, _, source = companion
    monkeypatch.setenv("TASK_CONSOLE_DB", str(source))
    with pytest.raises(ValueError, match="requires the source DB"):
        recovery.scheduled_backup()


def test_only_unconfigured_absence_is_an_uninitialized_skip(monkeypatch):
    for key in list(recovery.os.environ):
        if key.startswith("TASK_CONSOLE_"):
            monkeypatch.delenv(key)
    state = SimpleNamespace(code="NO_COMPANION")
    monkeypatch.setattr(recovery, "console_store", lambda: SimpleNamespace(
        resolve_db=lambda **kwargs: (None, state)))
    assert recovery.scheduled_backup() == {"schema_version": 1, "status": "skipped", "reason": "uninitialized"}
    state.code = "RESOLVER_REFUSED"
    with pytest.raises(ValueError):
        recovery.scheduled_backup()
    state.code = "NO_COMPANION"
    monkeypatch.setenv("TASK_CONSOLE_RUNTIME_CONFIG", "synthetic-config")
    with pytest.raises(ValueError):
        recovery.scheduled_backup()
