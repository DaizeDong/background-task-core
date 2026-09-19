"""T16 regressions using generated synthetic objects, hooks and local Git peers."""
import json
from pathlib import Path
import sys

import pytest

from test_repo_commit_push import R, Refused, _run, native_local_transport, repo  # noqa: F401


FAKE = "SYNTHETIC_CREDENTIAL_NOT_REAL"
DIAGNOSTIC = ("fatal: Authentication failed for 'https://user1:" + FAKE
              + "@example.com/repo'\nAuthorization: Basic " + FAKE
              + "\nAuthorization: Bearer " + FAKE + "\ntoken=" + FAKE)


def merge_only_tree(repo, mode):
    base = _run(repo, "rev-parse", "HEAD").strip()
    tree = _run(repo, "rev-parse", "HEAD^{tree}").strip()
    first = _run(repo, "commit-tree", tree, "-p", base, "-m", "synthetic first").strip()
    side = _run(repo, "commit-tree", tree, "-p", base, "-m", "synthetic side").strip()
    (repo / "merge-only.txt").write_text("SYNTHETIC_MERGE_ONLY_CONTENT\n", encoding="utf-8")
    _run(repo, "add", "merge-only.txt")
    _run(repo, "update-index", "--add", "--cacheinfo", "160000," + base + ",vendor")
    merged_tree = _run(repo, "write-tree").strip()
    merge = _run(repo, "commit-tree", merged_tree, "-p", first, "-p", side,
                 "-m", "synthetic merge").strip()
    _run(repo, "update-ref", "refs/heads/main", merge)
    _run(repo, "config", "log.diffMerges", mode)
    _run(repo, "config", "diff.ignoreSubmodules", "all")
    _run(repo, "config", "diff.submodule", "log")
    return base


@pytest.mark.parametrize("mode", ["off", "first-parent", "combined"])
def test_merge_only_bytes_and_gitlink_survive_review_and_native_publication(repo, monkeypatch, mode):
    base = merge_only_tree(repo, mode)
    (repo / "pending.txt").write_text("synthetic pending\n", encoding="utf-8")
    calls = []

    def traced(path, *args, **kwargs):
        calls.append(args)
        return native_local_transport(path, *args, **kwargs)

    monkeypatch.setattr(R, "_git", traced)
    plan = R.commit_push_plan("demo")
    assert plan["ok"] and not plan["diffTruncated"], plan
    assert plan["diff"].count("diff --git a/merge-only.txt") == 2
    assert plan["diff"].count("Subproject commit " + base) == 2
    assert "synthetic pending" in plan["diff"]
    R.git_ops.verify_history(repo, plan["expect"]["history"], plan["expect"]["target"], runner=traced)
    result = R.commit_push("demo", "synthetic approved", plan["expect"])
    assert result["state"] == "pushed"
    bare = repo.parent.parent / "origin.git"
    assert _run(bare, "show", "main:merge-only.txt") == "SYNTHETIC_MERGE_ONLY_CONTENT\n"
    assert _run(bare, "ls-tree", "main", "vendor").startswith("160000 commit " + base)
    assert _run(bare, "show", "main:pending.txt") == "synthetic pending\n"
    diffs = [args for args in calls if args[0] in {"log", "diff"} and "--binary" in args]
    assert any(args[0] == "diff" for args in diffs)
    assert all("--ignore-submodules=none" in args and "--submodule=short" in args for args in diffs)
    assert all("--diff-merges=separate" in args for args in diffs if args[0] == "log")


def test_old_approval_with_hidden_merge_is_invalidated_by_digest(repo):
    merge_only_tree(repo, "off")

    def legacy(path, *args, **kwargs):
        args = [arg for arg in args if arg not in {"--ignore-submodules=none", "--submodule=short"}]
        args = ["-m" if arg == "--diff-merges=separate" else arg for arg in args]
        return native_local_transport(path, *args, **kwargs)

    target = R.git_ops.push_target(repo)
    head = _run(repo, "rev-parse", "HEAD").strip()
    old = R.git_ops.review_history(repo, head, target=target, runner=legacy)
    assert "SYNTHETIC_MERGE_ONLY_CONTENT" not in old.pop("diff")
    current = R.git_ops.review_history(repo, head, target=target, runner=native_local_transport)
    assert current["digest"] != old["digest"]
    with pytest.raises(R.git_ops.GitError) as error:
        R.git_ops.publish(repo, head, _run(repo, "rev-parse", "HEAD^{tree}").strip(),
                          target, history=old, runner=native_local_transport)
    assert error.value.code == "plan_stale"


@pytest.mark.parametrize("command", ["ls-remote", "log", "diff"])
def test_failed_child_diagnostic_cannot_enter_plan_or_review_diff(repo, monkeypatch, command):
    real = R._git

    def failed(path, *args, **kwargs):
        if args[0] == command:
            return 128, DIAGNOSTIC
        return real(path, *args, **kwargs)

    monkeypatch.setattr(R, "_git", failed)
    plan = R.commit_push_plan("demo")
    assert not plan["ok"] and "expect" not in plan
    assert FAKE not in json.dumps(plan)
    assert "omitted" in plan["error"]


@pytest.mark.parametrize("diagnostic", [
    "Authorization: Basic " + FAKE,
    "Authorization: Bearer " + FAKE,
    '{"Authorization": "Basic ' + FAKE + '"}',
    "fatal: https://" + FAKE + "@example.com/repo failed",
    "fatal: ssh://user1:" + FAKE + "@example.com/repo failed",
    "token=" + FAKE,
])
def test_git_error_omits_credential_bearing_diagnostics(diagnostic):
    error = R.git_ops.GitError(diagnostic, "synthetic_failure")
    assert FAKE not in str(error) and "omitted" in str(error)
    assert error.code == "synthetic_failure"


@pytest.mark.parametrize("failure", ["failed", "exception", "malformed"])
def test_scanner_failure_visibly_omits_diagnostics(repo, monkeypatch, failure):
    from fleet_guards import secrets

    def unavailable(*args, **kwargs):
        if failure == "exception":
            raise RuntimeError(FAKE)
        return {"state": "failed"} if failure == "failed" else None

    monkeypatch.setattr(secrets, "scan", unavailable)
    real = R._git

    def failed(path, *args, **kwargs):
        if args[0] == "ls-remote":
            return 128, "synthetic transport detail " + FAKE
        return real(path, *args, **kwargs)

    monkeypatch.setattr(R, "_git", failed)
    plan = R.commit_push_plan("demo")
    assert not plan["ok"] and "expect" not in plan
    assert FAKE not in json.dumps(plan)
    assert "scan unavailable" in plan["error"]


def test_safe_diagnostic_is_preserved():
    message = "fatal: unable to create index.lock: File exists"
    assert str(R.git_ops.GitError(message)) == message


@pytest.mark.parametrize("hook_name,exit_code", [("pre-commit", 1), ("post-commit", 0), ("pre-push", 1)])
def test_real_hook_diagnostics_never_escape_receipts_or_retry(repo, monkeypatch, hook_name, exit_code):
    hooks = Path(_run(repo, "config", "core.hooksPath").strip())
    hook = hooks / hook_name
    hook.write_text(f"#!{sys.executable}\nimport sys\nprint({DIAGNOSTIC!r})\nsys.exit({exit_code})\n",
                    encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    calls = []

    def traced(path, *args, **kwargs):
        calls.append(args[0])
        return native_local_transport(path, *args, **kwargs)

    monkeypatch.setattr(R, "_git", traced)
    (repo / "pending.txt").write_text("synthetic pending\n", encoding="utf-8")
    plan = R.commit_push_plan("demo")
    assert plan["ok"], plan
    if hook_name == "pre-commit":
        with pytest.raises(Refused) as error:
            R.commit_push("demo", "synthetic approved", plan["expect"])
        assert FAKE not in str(error.value)
        assert error.value.code == "commit_failed"
        return
    result = R.commit_push("demo", "synthetic approved", plan["expect"], push=hook_name == "pre-push")
    assert FAKE not in json.dumps(result)
    assert "omitted" in result["out"]
    assert result["state"] == ("push_failed" if hook_name == "pre-push" else "committed")
    if hook_name == "pre-push":
        hook.unlink()
        (repo / "later.txt").write_text("synthetic later business edit\n", encoding="utf-8")
        calls.clear()
        retried = R.commit_push("demo", "synthetic retry", result["expect"])
        assert retried["state"] == "pushed" and retried["commit"] == result["commit"]
        assert "add" not in calls and "commit" not in calls
        assert "later.txt" in _run(repo, "status", "--porcelain")


@pytest.mark.parametrize("returncode,state", [(128, "push_failed"), (None, "unknown")])
def test_diagnostic_suppression_preserves_known_exit_and_uncertainty(repo, monkeypatch, returncode, state):
    (repo / "pending.txt").write_text("synthetic pending\n", encoding="utf-8")
    plan = R.commit_push_plan("demo")
    real = R._git

    def failed(path, *args, **kwargs):
        if args[0] == "push":
            return returncode, DIAGNOSTIC
        return real(path, *args, **kwargs)

    monkeypatch.setattr(R, "_git", failed)
    result = R.commit_push("demo", "synthetic approved", plan["expect"])
    assert result["state"] == state and "expect" in result
    assert FAKE not in json.dumps(result)


def test_successful_review_bytes_are_not_diagnostic_redacted(repo):
    (repo / "synthetic.txt").write_text("token=" + FAKE + "\n", encoding="utf-8")
    _run(repo, "add", "synthetic.txt")
    _run(repo, "commit", "-m", "synthetic credential fixture")
    plan = R.commit_push_plan("demo")
    assert plan["ok"] and "token=" + FAKE in plan["diff"]
    R.git_ops.verify_history(repo, plan["expect"]["history"], plan["expect"]["target"], runner=R._git)


@pytest.mark.parametrize("returncode,state", [(128, "push_failed"), (None, "unknown")])
def test_actual_process_adapter_output_is_sanitized_after_classification(repo, monkeypatch, returncode, state):
    from types import SimpleNamespace
    from llmcall import process

    real_process = process.run

    def injected(argv, *args, **kwargs):
        if argv[3] == "push":
            return SimpleNamespace(returncode=returncode, stdout=DIAGNOSTIC,
                                   stderr="\n" + FAKE, error=FAKE)
        return real_process(argv, *args, **kwargs)

    def runner(path, *args, **kwargs):
        if args[0] == "ls-remote":
            return native_local_transport(path, *args, **kwargs)
        return R.git_ops.run_git(path, *args, **kwargs)

    monkeypatch.setattr(process, "run", injected)
    monkeypatch.setattr(R, "_git", runner)
    (repo / "pending.txt").write_text("synthetic pending\n", encoding="utf-8")
    plan = R.commit_push_plan("demo")
    assert plan["ok"], plan
    result = R.commit_push("demo", "synthetic approved", plan["expect"])
    assert result["state"] == state and "expect" in result
    assert FAKE not in json.dumps(result)


def test_scanner_failure_omits_receipt_without_claiming_delivery_failure(repo, monkeypatch):
    from fleet_guards import secrets

    (repo / "pending.txt").write_text("synthetic pending\n", encoding="utf-8")
    plan = R.commit_push_plan("demo")
    real = R._git

    def interrupted(path, *args, **kwargs):
        if args[0] == "push":
            return None, "synthetic unclassified diagnostic " + FAKE
        return real(path, *args, **kwargs)

    monkeypatch.setattr(R, "_git", interrupted)
    monkeypatch.setattr(secrets, "scan", lambda *args, **kwargs: {"state": "failed"})
    result = R.commit_push("demo", "synthetic approved", plan["expect"])
    assert result["state"] == "unknown" and "expect" in result
    assert FAKE not in json.dumps(result)
    assert "secret scan unavailable" in result["out"]
