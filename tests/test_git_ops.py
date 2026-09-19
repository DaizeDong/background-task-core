"""Shared publishing contract; real synthetic Git with injected transport faults."""
import importlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "task_console"))
from test_repo_commit_push import R, _run, repo  # noqa: F401,E402


def reviewed_history(repo, ops):
    history = ops.review_history(repo, _run(repo, "rev-parse", "HEAD").strip(),
                                 target=ops.push_target(repo), runner=R._git)
    history.pop("diff")
    return history


@pytest.fixture
def ops():
    return importlib.import_module("git_ops")


def test_snapshot_preserves_real_index_and_pins_tree(repo, ops):
    (repo / "a.txt").write_bytes(b"reviewed\x00bytes")
    before = (repo / ".git" / "index").read_bytes()
    expected = ops.snapshot(repo, ["a.txt"])
    assert (repo / ".git" / "index").read_bytes() == before
    assert _run(repo, "show", expected["tree"] + ":a.txt") == "reviewed\x00bytes"
    result = ops.commit(repo, expected, "snapshot")
    assert result["state"] == "committed"
    assert result["tree"] == expected["tree"]
    assert _run(repo, "status", "--porcelain").strip() == ""


@pytest.mark.parametrize("drift", ["content", "head", "index", "staged", "untracked"])
def test_primitive_rejects_drift_without_changing_index(repo, ops, drift):
    (repo / "a.txt").write_text("reviewed", encoding="utf-8")
    expected = ops.snapshot(repo, ["a.txt"])
    if drift == "content":
        (repo / "a.txt").write_text("different", encoding="utf-8")
    elif drift == "head":
        _run(repo, "commit", "--allow-empty", "-m", "interloper")
    elif drift == "index":
        _run(repo, "add", "a.txt")
    else:
        (repo / "other.txt").write_text("unexpected", encoding="utf-8")
        if drift == "staged":
            _run(repo, "add", "other.txt")
    head = _run(repo, "rev-parse", "HEAD")
    index = (repo / ".git" / "index").read_bytes()
    with pytest.raises(ops.GitError) as error:
        ops.commit(repo, expected, "snapshot")
    assert error.value.code == "plan_stale"
    assert _run(repo, "rev-parse", "HEAD") == head
    assert (repo / ".git" / "index").read_bytes() == index


def test_scope_rejects_unrelated_staged_changes(repo, ops):
    (repo / "managed.txt").write_text("managed", encoding="utf-8")
    (repo / "user.txt").write_text("user", encoding="utf-8")
    _run(repo, "add", "user.txt")
    with pytest.raises(ops.GitError, match="allowed"):
        ops.snapshot(repo, ["managed.txt"])
    assert "user.txt" in _run(repo, "diff", "--cached", "--name-only")


@pytest.mark.parametrize("path", [".", "../outside", "/absolute", ":(glob)*", ".git/config", "dir/"])
def test_scope_requires_literal_leaf_paths(repo, ops, path):
    with pytest.raises(ops.GitError):
        ops.snapshot(repo, [path])


def test_no_changes_returns_noop_without_commit(repo, ops):
    head = _run(repo, "rev-parse", "HEAD")
    assert ops.commit(repo, ops.snapshot(repo, []), "unused")["state"] == "noop"
    assert _run(repo, "rev-parse", "HEAD") == head


def test_tree_tampering_rejected(repo, ops):
    (repo / "a.txt").write_text("reviewed", encoding="utf-8")
    expected = ops.snapshot(repo, ["a.txt"])
    expected["tree"] = expected["head_tree"]
    with pytest.raises(ops.GitError):
        ops.commit(repo, expected, "tampered")


def test_push_failure_retains_commit_and_retry_only_pushes(repo, ops):
    (repo / "a.txt").write_text("reviewed", encoding="utf-8")
    committed = ops.commit(repo, ops.snapshot(repo, ["a.txt"]), "snapshot")
    target = ops.push_target(repo)
    history = reviewed_history(repo, ops)
    calls = []

    def rejected(path, *args, **kwargs):
        calls.append(args)
        if args[0] == "push":
            return 1, "!\tlocal:refs/heads/main\t[rejected] (non-fast-forward)\n"
        return R._git(path, *args, **kwargs)

    failed = ops.publish(repo, committed["commit"], committed["tree"], target,
                         history=history, runner=rejected)
    assert failed["state"] == "push_failed"
    assert failed["commit"] == committed["commit"]
    assert _run(repo, "rev-parse", "HEAD").strip() == committed["commit"]

    def accepted(path, *args, **kwargs):
        calls.append(args)
        if args[0] == "push":
            assert committed["commit"] + ":refs/heads/main" in args
            return 0, "accepted"
        return R._git(path, *args, **kwargs)

    # A later business edit must remain uncommitted during push-only retry.
    (repo / "later.txt").write_text("later", encoding="utf-8")
    assert ops.publish(repo, committed["commit"], committed["tree"], target,
                       history=history, runner=accepted)["state"] == "pushed"
    assert not any(args[0] in {"commit", "add"} for args in calls)
    assert "later.txt" in _run(repo, "status", "--porcelain")


@pytest.mark.parametrize("probe", ["unavailable", "same", "different"])
def test_lost_push_response_is_reconciled_or_unknown(repo, ops, probe):
    _run(repo, "commit", "--allow-empty", "-m", "outgoing")
    head = _run(repo, "rev-parse", "HEAD").strip()
    tree = _run(repo, "rev-parse", "HEAD^{tree}").strip()
    target = ops.push_target(repo)
    history = reviewed_history(repo, ops)
    sent = False

    def transport(path, *args, **kwargs):
        nonlocal sent
        if args[0] == "push":
            sent = True
            return None, "response lost after send"
        if args[0] == "ls-remote" and sent:
            if probe == "unavailable":
                return None, "probe timed out"
            return 0, (head if probe == "same" else "0" * 40) + "\trefs/heads/main\n"
        return R._git(path, *args, **kwargs)

    result = ops.publish(repo, head, tree, target, history=history, runner=transport)
    assert result["state"] == ("pushed" if probe == "same" else "unknown")
    assert result["commit"] == head


def test_retry_rejects_head_tree_and_target_drift(repo, ops):
    head = _run(repo, "rev-parse", "HEAD").strip()
    tree = _run(repo, "rev-parse", "HEAD^{tree}").strip()
    target = ops.push_target(repo)
    with pytest.raises(ops.GitError):
        ops.publish(repo, head, "0" * 40, target)
    _run(repo, "remote", "set-url", "origin", str(repo.parent / "different.git"))
    with pytest.raises(ops.GitError):
        ops.publish(repo, head, tree, target)
    _run(repo, "commit", "--allow-empty", "-m", "new head")
    with pytest.raises(ops.GitError):
        ops.publish(repo, head, tree, target)


def test_commit_failure_preserves_index(repo, ops):
    (repo / "a.txt").write_text("reviewed", encoding="utf-8")
    expected = ops.snapshot(repo, ["a.txt"])
    index = (repo / ".git" / "index").read_bytes()

    def rejected(path, *args, **kwargs):
        if args[0] == "commit":
            return 1, "synthetic hook rejected"
        return ops.run_git(path, *args, **kwargs)

    with pytest.raises(ops.GitError, match="synthetic hook rejected"):
        ops.commit(repo, expected, "snapshot", runner=rejected)
    assert (repo / ".git" / "index").read_bytes() == index


def test_lost_commit_response_reports_commit_without_repeating(repo, ops):
    (repo / "a.txt").write_text("reviewed", encoding="utf-8")
    expected = ops.snapshot(repo, ["a.txt"])
    commits = []

    def lost(path, *args, **kwargs):
        if args[0] == "commit":
            commits.append(args)
            assert ops.run_git(path, *args, **kwargs)[0] == 0
            return None, "response lost"
        return ops.run_git(path, *args, **kwargs)

    result = ops.commit(repo, expected, "snapshot", runner=lost)
    assert result["state"] == "committed"
    assert result["tree"] == expected["tree"]
    assert len(commits) == 1


@pytest.mark.parametrize("drift", ["content", "head", "index"])
def test_drift_during_commit_preparation_is_refused(repo, ops, drift):
    if drift == "head":
        _run(repo, "commit", "--allow-empty", "-m", "preparation")
        previous = _run(repo, "rev-parse", "HEAD^").strip()
    (repo / "a.txt").write_text("reviewed", encoding="utf-8")
    expected = ops.snapshot(repo, ["a.txt"])
    commits = []

    def race(path, *args, **kwargs):
        result = ops.run_git(path, *args, **kwargs)
        if args[0] == "read-tree":
            if drift == "content":
                (repo / "a.txt").write_text("raced", encoding="utf-8")
            elif drift == "head":
                _run(repo, "update-ref", "HEAD", previous)
            else:
                # Simulate a non-cooperating writer; Git itself honors index.lock.
                index = repo / ".git" / "index"
                index.write_bytes(index.read_bytes() + b"unexpected")
        if args[0] == "commit":
            commits.append(args)
        return result

    with pytest.raises(ops.GitError):
        ops.commit(repo, expected, "snapshot", runner=race)
    assert not commits


def test_existing_index_lock_is_preserved(repo, ops):
    (repo / "a.txt").write_text("reviewed", encoding="utf-8")
    expected = ops.snapshot(repo, ["a.txt"])
    lock = repo / ".git" / "index.lock"
    lock.write_text("another writer", encoding="utf-8")
    with pytest.raises(ops.GitError) as error:
        ops.commit(repo, expected, "snapshot")
    assert error.value.code == "busy"
    assert lock.read_text(encoding="utf-8") == "another writer"


def test_linked_worktree_uses_its_own_index(repo, ops):
    linked = repo.parent / "linked"
    _run(repo, "worktree", "add", "-b", "side", str(linked))
    original_head = _run(repo, "rev-parse", "HEAD")
    original_index = (repo / ".git" / "index").read_bytes()
    (linked / "a.txt").write_text("linked", encoding="utf-8")
    result = ops.commit(linked, ops.snapshot(linked, ["a.txt"]), "linked commit")
    assert result["state"] == "committed"
    assert _run(repo, "rev-parse", "HEAD") == original_head
    assert (repo / ".git" / "index").read_bytes() == original_index
    assert _run(linked, "status", "--porcelain").strip() == ""


def test_caller_environment_cannot_redirect_index(repo, ops):
    with pytest.raises(ops.GitError) as error:
        ops.snapshot(repo, [], env={"GIT_INDEX_FILE": str(repo.parent / "outside-index")})
    assert error.value.code == "bad_environment"


def test_snapshot_requires_the_repository_root(repo, ops):
    nested = repo / "nested"
    nested.mkdir()
    with pytest.raises(ops.GitError) as error:
        ops.snapshot(nested, [])
    assert error.value.code == "bad_repo"
