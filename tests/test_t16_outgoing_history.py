"""Exact-target publication tests; all objects, hooks and remotes are synthetic."""
from pathlib import Path
import subprocess

import pytest

from test_repo_commit_push import R, Refused, _run, copy_git_objects, native_local_transport, repo  # noqa: F401


def remote_tip(repo, oid=None, bare=None):
    bare = bare or repo.parent.parent / "origin.git"
    copy_git_objects(repo / ".git/objects", bare / "objects")
    _run(bare, "update-ref", "refs/heads/main", oid or _run(repo, "rev-parse", "HEAD").strip())
    return bare


def add_commits(repo, count):
    """Generate a long synthetic chain in one Git process, without models."""
    parent = _run(repo, "rev-parse", "HEAD").strip()
    stream = []
    for number in range(count):
        message = f"synthetic historical change {number}\n"
        stream.append(f"commit refs/heads/main\nmark :{number+1}\ncommitter Example User <user1@example.com> {1700000000+number} +0000\ndata {len(message)}\n{message}from {parent}\n\n")
        parent = f":{number+1}"
    result = subprocess.run(["git", "fast-import", "--quiet"], cwd=repo,
                            input="".join(stream).encode("utf-8"), capture_output=True)
    assert result.returncode == 0, result.stderr


def test_mature_remote_has_only_one_outgoing_commit(repo):
    add_commits(repo, 1005)
    base = _run(repo, "rev-parse", "HEAD").strip()
    remote_tip(repo, base)
    # Tracking is intentionally stale at the initial commit.
    (repo / "small.txt").write_text("one small change\n", encoding="utf-8")
    _run(repo, "add", "small.txt")
    _run(repo, "commit", "-m", "single outgoing commit")
    review = R.commit_push_plan("demo")
    assert review["ok"], review
    assert review["historyCount"] == review["aheadCount"] == 1
    assert len(review["ahead"]) == 1
    assert review["expect"]["history"]["base"] == base
    assert "single outgoing commit" in review["diff"]
    assert "synthetic historical change" not in review["diff"]
    assert not review["historyTruncated"] and not review["diffTruncated"]


def test_sixty_actual_outgoing_commits_are_all_visible(repo):
    remote_tip(repo)
    add_commits(repo, 60)
    review = R.commit_push_plan("demo")
    assert review["ok"], review
    assert review["historyCount"] == review["aheadCount"] == len(review["ahead"]) == 60
    for number in range(60):
        assert f"synthetic historical change {number}\n" in review["diff"]


@pytest.mark.parametrize("change", ["advance", "reset", "delete"])
def test_changed_remote_requires_new_review_before_commit(repo, change):
    add_commits(repo, 2)
    base = _run(repo, "rev-parse", "HEAD").strip()
    bare = remote_tip(repo)
    (repo / "pending.txt").write_text("approved bytes", encoding="utf-8")
    review = R.commit_push_plan("demo")
    assert review["ok"], review
    if change == "delete":
        _run(bare, "update-ref", "-d", "refs/heads/main")
    else:
        tip = _run(repo, "rev-parse", "HEAD^").strip()
        if change == "advance":
            # A descendant already stored locally, without moving the review HEAD.
            tip = subprocess.run(["git", "commit-tree", "HEAD^{tree}", "-p", base,
                                  "-m", "remote advance"], cwd=repo,
                                 capture_output=True, text=True, check=True).stdout.strip()
        remote_tip(repo, tip)
    with pytest.raises(Refused) as error:
        R.commit_push("demo", "approved message", review["expect"])
    assert error.value.code == "plan_stale"
    assert _run(repo, "rev-parse", "HEAD").strip() == base


@pytest.mark.parametrize("fault", ["shallow", "graft", "replace"])
def test_ancestry_overrides_refuse_review(repo, fault):
    remote_tip(repo)
    add_commits(repo, 1)
    if fault == "shallow":
        (repo / ".git/shallow").write_text(_run(repo, "rev-parse", "HEAD"), encoding="utf-8")
    elif fault == "graft":
        (repo / ".git/info/grafts").write_text(_run(repo, "rev-parse", "HEAD"), encoding="utf-8")
    else:
        _run(repo, "replace", "HEAD", "HEAD^")
    review = R.commit_push_plan("demo")
    assert not review["ok"] and "expect" not in review
    assert review["code"] == "history_unavailable"


def test_missing_local_remote_base_requires_refresh_without_fetch(repo, monkeypatch):
    real = R._git
    calls = []
    def missing(path, *args, **kwargs):
        calls.append(args[0])
        if args[0] == "ls-remote":
            return 0, "1" * 40 + "\trefs/heads/main\n"
        return real(path, *args, **kwargs)
    monkeypatch.setattr(R, "_git", missing)
    review = R.commit_push_plan("demo")
    assert not review["ok"] and review["code"] == "refresh_required"
    assert "expect" not in review and "fetch" not in calls


def test_new_ref_requires_full_history_review(repo):
    bare = repo.parent.parent / "origin.git"
    _run(bare, "update-ref", "-d", "refs/heads/main")
    add_commits(repo, 2)
    review = R.commit_push_plan("demo")
    assert review["ok"], review
    assert review["historyScope"] == "new_ref_ancestry"
    assert review["historyCount"] == 3
    assert review["expect"]["history"]["base"] is None


@pytest.mark.parametrize("rc,output", [(None, "timeout"), (128, "failed"),
                                       (0, ""), (0, "not-an-oid\trefs/heads/main\n"),
                                       (0, "1" * 40 + "\trefs/heads/other\n")])
def test_failed_or_ambiguous_observation_never_approves(repo, monkeypatch, rc, output):
    real = R._git
    def failed(path, *args, **kwargs):
        if args[0] == "ls-remote":
            return rc, output
        return real(path, *args, **kwargs)
    monkeypatch.setattr(R, "_git", failed)
    review = R.commit_push_plan("demo")
    assert not review["ok"] and review["code"] == "remote_unavailable"
    assert review["aheadCount"] is None and "expect" not in review


def test_push_url_not_fetch_url_or_tracking_defines_outgoing(repo):
    other = repo.parent.parent / "other.git"
    _run(repo, "init", "--bare", "-b", "main", str(other))
    base = _run(repo, "rev-parse", "HEAD").strip()
    remote_tip(repo, base, other)
    add_commits(repo, 3)
    remote_tip(repo)
    _run(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _run(repo, "remote", "set-url", "--push", "origin", str(other))
    review = R.commit_push_plan("demo")
    assert review["ok"] and review["aheadCount"] == 3
    assert review["expect"]["history"]["base"] == base
    assert review["expect"]["history"]["target"]["url"] == str(other)


def test_missing_outgoing_blob_cannot_hide_reverted_content(repo):
    (repo / "transient.txt").write_text("synthetic secret removed later", encoding="utf-8")
    _run(repo, "add", "transient.txt")
    _run(repo, "commit", "-m", "temporary content")
    blob = _run(repo, "rev-parse", "HEAD:transient.txt").strip()
    _run(repo, "rm", "transient.txt")
    _run(repo, "commit", "-m", "remove temporary content")
    missing = repo / ".git/objects" / blob[:2] / blob[2:]
    missing.chmod(0o666)  # Git marks immutable objects read-only on Windows.
    missing.unlink()
    review = R.commit_push_plan("demo")
    assert not review["ok"] and "expect" not in review
    assert "failed" in review["error"]


def test_reverted_bytes_and_full_message_body_are_bound(repo):
    (repo / "transient.txt").write_text("synthetic secret\n", encoding="utf-8")
    _run(repo, "add", "transient.txt")
    _run(repo, "commit", "-m", "temporary change", "-m", "synthetic message body scope")
    _run(repo, "rm", "transient.txt")
    _run(repo, "commit", "-m", "revert content")
    review = R.commit_push_plan("demo")
    assert review["ok"] and review["historyCount"] == 2
    assert "+synthetic secret" in review["diff"] and "-synthetic secret" in review["diff"]
    assert "synthetic message body scope" in review["diff"]
    assert len(review["expect"]["history"]["commits"]) == 2


def test_divergent_base_is_rejected_without_force(repo, monkeypatch):
    base = _run(repo, "rev-parse", "HEAD").strip()
    add_commits(repo, 1)
    sibling = subprocess.run(["git", "commit-tree", "HEAD^{tree}", "-p", base,
                              "-m", "sibling"], cwd=repo, text=True,
                             capture_output=True, check=True).stdout.strip()
    remote_tip(repo, sibling)
    review = R.commit_push_plan("demo")
    assert not review["ok"] and review["code"] == "non_fast_forward"
    assert "expect" not in review


@pytest.mark.parametrize("race", ["none", "reset", "advance", "new_ref"])
def test_real_git_cas_never_broadens_approved_set(repo, monkeypatch, race):
    add_commits(repo, 2)
    base = _run(repo, "rev-parse", "HEAD").strip()
    older = _run(repo, "rev-parse", "HEAD^").strip()
    bare = remote_tip(repo)
    if race == "new_ref":
        _run(bare, "update-ref", "-d", "refs/heads/main")
    (repo / "pending.txt").write_text("reviewed bytes\n", encoding="utf-8")
    monkeypatch.setattr(R, "_git", native_local_transport)
    review = R.commit_push_plan("demo")
    assert review["ok"], review
    calls = []
    def racing(path, *args, **kwargs):
        if args[0] == "push":
            calls.append(args)
            assert "--no-force" in args and "--no-mirror" in args
            assert "--no-verify" not in args and "--force" not in args
            assert all(not arg.startswith("+") for arg in args)
            expected = "" if race == "new_ref" else base
            assert f"--force-with-lease=refs/heads/main:{expected}" in args
            if race == "reset":
                remote_tip(repo, older)
            elif race == "advance":
                advanced = subprocess.run(["git", "commit-tree", "HEAD^{tree}", "-p", base,
                                           "-m", "remote advance"], cwd=repo, text=True,
                                          capture_output=True, check=True).stdout.strip()
                remote_tip(repo, advanced)
            elif race == "new_ref":
                remote_tip(repo, base)
        return native_local_transport(path, *args, **kwargs)
    monkeypatch.setattr(R, "_git", racing)
    result = R.commit_push("demo", "approved message", review["expect"])
    assert len(calls) == 1
    assert result["state"] == ("pushed" if race == "none" else "push_failed"), result
    assert result["publication"]["base"] == review["expect"]["history"]["base"]
    assert result["publication"]["commits"] == [result["commit"], *review["expect"]["history"]["commits"]]
    actual = _run(bare, "rev-parse", "refs/heads/main").strip()
    assert (actual == result["commit"]) == (race == "none")


def test_changed_target_after_plan_requires_new_review(repo):
    review = R.commit_push_plan("demo")
    _run(repo, "remote", "set-url", "--push", "origin", str(repo.parent.parent / "other.git"))
    with pytest.raises(Refused) as error:
        R.commit_push("demo", "approved", review["expect"])
    assert error.value.code == "plan_stale"


def test_unresolved_url_rewrites_refuse_ambiguous_transport(repo):
    _run(repo, "config", "url.synthetic.pushInsteadOf", "unmatched:")
    review = R.commit_push_plan("demo")
    assert not review["ok"] and "expect" not in review
    assert any("insteadOf" in reason for reason in review["blocked"])


def test_retry_binds_history_and_remote_base(repo, monkeypatch):
    (repo / "pending.txt").write_text("reviewed bytes\n", encoding="utf-8")
    review = R.commit_push_plan("demo")
    real = R._git
    def rejected(path, *args, **kwargs):
        if args[0] == "push":
            return 1, "!\tlocal:refs/heads/main\t[rejected] (policy)\n"
        return real(path, *args, **kwargs)
    monkeypatch.setattr(R, "_git", rejected)
    result = R.commit_push("demo", "approved", review["expect"])
    assert result["state"] == "push_failed"
    assert result["expect"]["history"] == review["expect"]["history"]
    assert result["publication"]["commits"] == [result["commit"]]
    _run(repo.parent.parent / "origin.git", "update-ref", "-d", "refs/heads/main")
    with pytest.raises(Refused) as error:
        R.commit_push("demo", "retry", result["expect"])
    assert error.value.code == "plan_stale"
    assert _run(repo, "rev-parse", "HEAD").strip() == result["commit"]


def test_commit_message_hook_mutation_cannot_publish(repo, monkeypatch):
    import sys
    hooks = Path(_run(repo, "config", "core.hooksPath").strip())
    hook = hooks / "commit-msg"
    hook.write_text(f"#!{sys.executable}\nimport pathlib,sys\npathlib.Path(sys.argv[1]).write_text('synthetic altered message\\n')\n",
                    encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    (repo / "pending.txt").write_text("reviewed bytes", encoding="utf-8")
    review = R.commit_push_plan("demo")
    result = R.commit_push("demo", "approved message", review["expect"])
    assert result["state"] == "unknown" and "expect" not in result
    assert _run(repo, "log", "-1", "--format=%B").strip() == "synthetic altered message"


def test_real_git_rejects_reset_after_advertisement(repo, monkeypatch):
    import sys
    add_commits(repo, 2)
    older = _run(repo, "rev-parse", "HEAD^").strip()
    bare = remote_tip(repo)
    hooks = Path(_run(repo, "config", "core.hooksPath").strip())
    hook = hooks / "pre-push"
    hook.write_text(f"#!{sys.executable}\nimport subprocess\nsubprocess.run({['git', '--git-dir', str(bare), 'update-ref', 'refs/heads/main', older]!r},check=True)\n",
                    encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    monkeypatch.setattr(R, "_git", native_local_transport)
    (repo / "pending.txt").write_text("reviewed", encoding="utf-8")
    review = R.commit_push_plan("demo")
    result = R.commit_push("demo", "approved", review["expect"])
    assert result["state"] == "push_failed", result
    assert _run(bare, "rev-parse", "refs/heads/main").strip() == older
    assert "expect" in result


def test_default_runner_preserves_absent_ref_exit_code(repo):
    bare = repo.parent.parent / "origin.git"
    rc, output = R.git_ops.run_git(repo, "ls-remote", "--refs", "--exit-code", "--",
                                  "ext::git %s " + bare.as_posix(), "refs/heads/absent",
                                  env={"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "protocol.ext.allow",
                                       "GIT_CONFIG_VALUE_0": "always"})
    assert rc == 2 and output == ""


def test_default_runner_disables_lazy_fetch_and_replacement(repo, monkeypatch):
    from types import SimpleNamespace
    def capture(*args, **kwargs):
        assert kwargs["env"]["GIT_NO_LAZY_FETCH"] == "1"
        assert kwargs["env"]["GIT_NO_REPLACE_OBJECTS"] == "1"
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    monkeypatch.setattr(R.git_ops.subprocess, "run", capture)
    assert R.git_ops.run_git(repo, "rev-parse", "HEAD") == (0, "")


def test_mature_remote_with_only_one_pending_file(repo):
    add_commits(repo, 1005)
    remote_tip(repo)
    (repo / "small.txt").write_text("one pending small change\n", encoding="utf-8")
    review = R.commit_push_plan("demo")
    assert review["ok"] and review["aheadCount"] == review["historyCount"] == 0
    assert review["fileCount"] == 1 and not review["nothing"]
    assert "one pending small change" in review["diff"]
    assert "synthetic historical change" not in review["diff"]


def test_ui_labels_exact_outgoing_count_and_observed_base():
    from test_repo_publish_ui import plan, run_ui
    review = plan()
    review.update(aheadCount=60, historyCount=60, remoteBase="a" * 40,
                  pushTarget={"ref": "refs/heads/main"},
                  ahead=[f"synthetic outgoing {number}" for number in range(60)])
    result = run_ui({"a": review}, [], cancel=True)
    text = result["dialogs"][0]
    assert "refs/heads/main" in text and "a" * 40 in text
    assert "60" in text and "synthetic outgoing 59" in text
    assert "相对本地 upstream" not in text
    assert [event["kind"] for event in result["trace"]] == ["plan", "review"]


def test_history_digest_ignores_log_presentation_configuration(repo):
    add_commits(repo, 1)
    review = R.commit_push_plan("demo")
    assert review["ok"]
    _run(repo, "config", "log.date", "relative")
    _run(repo, "config", "color.ui", "always")
    _run(repo, "config", "log.showSignature", "true")
    R.git_ops.verify_history(repo, review["expect"]["history"], review["expect"]["target"], runner=R._git)
