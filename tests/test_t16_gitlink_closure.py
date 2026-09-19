"""Changed gitlinks are unsupported; rejection preserves synthetic Git state."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from test_repo_commit_push import R, Refused, _run, repo as base_repo  # noqa: F401


@pytest.fixture
def repo(base_repo, monkeypatch):
    # The sandbox cannot launch Git's MSYS file-transport child. Observe the
    # actual local bare ref directly; do not enable the older ext helper.
    def local_observer(path, *args, **kwargs):
        if args[0] == "ls-remote":
            target = Path(args[args.index("--") + 1]).resolve()
            assert target.is_relative_to(base_repo.parent.parent)
            ref = args[-1]
            result = subprocess.run(["git", "-C", str(target), "show-ref", "--verify", "--hash", ref],
                                    capture_output=True, text=True)
            if result.returncode == 0:
                return 0, result.stdout.strip() + "\t" + ref + "\n"
            return 2, ""
        return R.git_ops.run_git(path, *args, **kwargs)
    monkeypatch.setattr(R, "_git", local_observer)
    return base_repo


def evidence(repo):
    tree = _run(repo, "write-tree").strip()  # Populate Git's cache before byte capture.
    return {
        "index": (repo / ".git/index").read_bytes(),
        "head": _run(repo, "rev-parse", "HEAD").strip(),
        "tree": tree,
        "files": {p.relative_to(repo).as_posix(): p.read_bytes()
                  for p in repo.rglob("*") if p.is_file() and ".git" not in p.relative_to(repo).parts},
    }


def gitlink(repo, change, present=False):
    first = _run(repo, "rev-parse", "HEAD").strip()
    _run(repo, "update-index", "--add", "--cacheinfo", "160000," + first + ",vendor")
    if change != "new":
        _run(repo, "commit", "-m", "synthetic initial gitlink")
        if change == "deleted":
            _run(repo, "update-index", "--force-remove", "vendor")
        elif change == "updated":
            second = _run(repo, "rev-parse", "HEAD").strip()
            _run(repo, "update-index", "--cacheinfo", "160000," + second + ",vendor")
    if present:
        (repo / "vendor").mkdir()
    _run(repo, "config", "diff.ignoreSubmodules", "all")
    _run(repo, "config", "diff.submodule", "diff")
    (repo / "pending.txt").write_bytes(b"synthetic pending\n")


@pytest.mark.parametrize("change", ["new", "updated", "deleted"])
@pytest.mark.parametrize("present", [False, True], ids=["absent", "directory"])
def test_plan_refuses_changed_gitlink_without_mutation(repo, change, present):
    gitlink(repo, change, present)
    before = evidence(repo)
    plan = R.commit_push_plan("demo")
    assert evidence(repo) == before
    assert not plan["ok"], plan
    assert "expect" not in plan
    assert plan["code"] == "unsupported_submodule", plan
    assert "submodule" in plan["error"].lower(), plan
    print(json.dumps({"change": change, "present": present, "code": plan["code"],
                      "head": before["head"], "tree": before["tree"],
                      "index_sha256": hashlib.sha256(before["index"]).hexdigest()}))


def test_status_cannot_hide_staged_pointer_update(repo):
    gitlink(repo, "updated", True)
    paths, _ = R.git_ops.status_paths(repo)
    assert "vendor" in paths


@pytest.mark.parametrize("change", ["content", "untracked", "head"])
def test_ignore_settings_cannot_hide_dirty_submodule(repo, change):
    sub = repo / "vendor"
    sub.mkdir()
    _run(sub, "init", "-b", "main")
    _run(sub, "config", "user.name", "Example User")
    _run(sub, "config", "user.email", "user1@example.com")
    (sub / "inner.txt").write_bytes(b"synthetic original\n")
    _run(sub, "add", "inner.txt")
    _run(sub, "commit", "-m", "synthetic submodule")
    oid = _run(sub, "rev-parse", "HEAD").strip()
    _run(repo, "update-index", "--add", "--cacheinfo", "160000," + oid + ",vendor")
    _run(repo, "commit", "-m", "synthetic gitlink")
    _run(repo, "config", "diff.ignoreSubmodules", "all")
    _run(repo, "config", "submodule.vendor.ignore", "all")
    if change == "head":
        _run(sub, "commit", "--allow-empty", "-m", "synthetic advance")
    else:
        (sub / ("inner.txt" if change == "content" else "new.txt")).write_bytes(b"changed\n")
    (repo / "pending.txt").write_bytes(b"ordinary pending\n")
    before = evidence(repo)
    sub_before = evidence(sub)
    paths, _ = R.git_ops.status_paths(repo)
    assert "vendor" in paths
    plan = R.commit_push_plan("demo")
    assert not plan["ok"] and plan["code"] == "unsupported_submodule", plan
    assert "expect" not in plan
    assert evidence(repo) == before
    assert evidence(sub) == sub_before


def test_unrelated_scope_cannot_carry_hidden_staged_gitlink(repo):
    gitlink(repo, "updated", True)
    before = evidence(repo)
    with pytest.raises(R.git_ops.GitError) as error:
        R.git_ops.snapshot(repo, ["pending.txt"])
    assert error.value.code == "unsupported_submodule"
    assert evidence(repo) == before


def test_absent_allowed_gitlink_cannot_be_erased_by_add(repo):
    gitlink(repo, "new")
    before = evidence(repo)
    with pytest.raises(R.git_ops.GitError) as error:
        R.git_ops.snapshot(repo, ["vendor", "pending.txt"])
    assert error.value.code == "unsupported_submodule"
    assert evidence(repo) == before


def test_submodule_added_after_approval_refuses_commit(repo):
    (repo / "pending.txt").write_bytes(b"approved bytes\n")
    plan = R.commit_push_plan("demo")
    assert plan["ok"], plan
    gitlink(repo, "new")
    before = evidence(repo)
    with pytest.raises(Refused):
        R.commit_push("demo", "synthetic approved", plan["expect"])
    assert evidence(repo) == before


def test_clean_gitlink_preserved_in_ordinary_commit(repo):
    gitlink(repo, "clean", True)
    before = _run(repo, "ls-tree", "HEAD", "vendor")
    plan = R.commit_push_plan("demo")
    assert plan["ok"], plan
    assert "Subproject commit " in plan["diff"]  # Already committed outgoing history.
    result = R.commit_push("demo", "synthetic ordinary file", plan["expect"], push=False)
    assert result["state"] == "committed"
    assert _run(repo, "ls-tree", "HEAD", "vendor") == before
    assert _run(repo, "show", "HEAD:pending.txt") == "synthetic pending\n"


def test_history_digest_and_remote_base_still_bind_approval(repo):
    from test_repo_commit_push import copy_git_objects

    (repo / "outgoing.txt").write_bytes(b"SYNTHETIC_HISTORY_BYTES\n")
    _run(repo, "add", "outgoing.txt")
    _run(repo, "commit", "-m", "synthetic outgoing")
    _run(repo, "rm", "outgoing.txt")
    _run(repo, "commit", "-m", "synthetic revert")
    (repo / "pending.txt").write_bytes(b"reviewed pending\n")
    plan = R.commit_push_plan("demo")
    assert plan["ok"] and plan["historyCount"] == 2, plan
    assert "SYNTHETIC_HISTORY_BYTES" in plan["diff"]
    history = plan["expect"]["history"]
    R.git_ops.verify_history(repo, history, plan["expect"]["target"], runner=R._git)
    tampered = dict(history, digest="0" * 64)
    with pytest.raises(R.git_ops.GitError):
        R.git_ops.verify_history(repo, tampered, plan["expect"]["target"], runner=R._git)
    bare = repo.parent.parent / "origin.git"
    copy_git_objects(repo / ".git/objects", bare / "objects")
    _run(bare, "update-ref", "refs/heads/main", _run(repo, "rev-parse", "HEAD").strip())
    before = evidence(repo)
    with pytest.raises(Refused):
        R.commit_push("demo", "synthetic stale approval", plan["expect"])
    assert evidence(repo) == before
    print(json.dumps({"history_digest": history["digest"], "outgoing": history["commits"],
                      "base": history["base"], "remote_drift_refused": True}))
