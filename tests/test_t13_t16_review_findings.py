"""Independent-review regressions using only synthetic repositories and UI data."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from test_repo_commit_push import R, Refused, _run, repo  # noqa: F401
from test_repo_publish_ui import plan, run_ui


def test_more_than_fifty_commits_are_reviewed(repo):
    for number in range(60):
        _run(repo, "commit", "--allow-empty", "-m", f"synthetic change {number}")
    review = R.commit_push_plan("demo")
    assert review["ok"], review
    assert review["aheadCount"] == len(review["ahead"]) == 60
    assert review["historyCount"] == 60
    assert "synthetic change 0" in review["diff"]
    assert "synthetic change 59" in review["diff"]


@pytest.mark.parametrize("rc", [1, None])
def test_history_failure_is_unavailable_not_zero(repo, monkeypatch, rc):
    real = R._git

    def fail(path, *args, **kwargs):
        if args[0] == "log":
            return rc, "synthetic history transport failure"
        return real(path, *args, **kwargs)

    monkeypatch.setattr(R, "_git", fail)
    review = R.commit_push_plan("demo")
    assert review["ok"] is False
    assert review["aheadKnown"] is False
    assert review["aheadCount"] is None
    assert review["nothing"] is False
    assert "expect" not in review
    assert "synthetic history transport failure" in review["error"]


def test_snapshot_failure_keeps_error_and_display_evidence(repo, monkeypatch):
    (repo / "reviewed.txt").write_text("synthetic", encoding="utf-8")

    def fail(*args, **kwargs):
        raise R.git_ops.GitError("synthetic snapshot timeout", "git_failed")

    monkeypatch.setattr(R.git_ops, "snapshot", fail)
    review = R.commit_push_plan("demo")
    assert review["ok"] is False
    assert review["state"] == "unavailable"
    assert review["code"] == "git_failed"
    assert review["files"] == ["reviewed.txt"]
    assert "synthetic snapshot timeout" in review["error"]
    assert "expect" not in review


@pytest.mark.parametrize("target_case", ["different_push_url", "missing_tracking", "rewound_tracking"])
def test_review_covers_all_ancestry_applicable_to_exact_target(repo, target_case):
    (repo / "transient.txt").write_text("synthetic historical payload", encoding="utf-8")
    _run(repo, "add", "transient.txt")
    _run(repo, "commit", "-m", "historical addition")
    added = _run(repo, "rev-parse", "HEAD").strip()
    _run(repo, "rm", "transient.txt")
    _run(repo, "commit", "-m", "historical removal")
    if target_case == "different_push_url":
        _run(repo, "init", "--bare", "-b", "main", str(repo.parent / "other.git"))
        _run(repo, "remote", "set-url", "--push", "origin", str(repo.parent / "other.git"))
    elif target_case == "missing_tracking":
        _run(repo, "update-ref", "-d", "refs/remotes/origin/main")
    else:
        # Cached tracking says synchronized; an actual target may have been rewound.
        _run(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    review = R.commit_push_plan("demo")
    assert review["ok"], review
    assert review["historyScope"] == ("new_ref_ancestry" if target_case == "different_push_url"
                                       else "exact_remote_outgoing")
    assert review["historyCount"] == (3 if target_case == "different_push_url" else 2)
    assert added in review["diff"]
    assert "+synthetic historical payload" in review["diff"]
    assert "-synthetic historical payload" in review["diff"]


def test_history_cap_blocks_approval_without_hiding_count(repo, monkeypatch):
    monkeypatch.setattr(R, "_REVIEW_MAX_COMMITS", 1, raising=False)
    _run(repo, "commit", "--allow-empty", "-m", "second commit")
    _run(repo, "commit", "--allow-empty", "-m", "third commit")
    review = R.commit_push_plan("demo")
    assert review["ok"] is False
    assert review["historyCount"] == 2
    assert review["historyTruncated"] is True
    assert "expect" not in review


@pytest.mark.parametrize("rc,output,state", [
    (128, "fatal: Authentication failed for 'https://example.invalid/repo.git'", "push_failed"),
    (128, "fatal: 'missing.git' does not appear to be a git repository", "push_failed"),
    (128, "git@example.invalid: Permission denied (publickey).", "push_failed"),
    (128, "fatal: unable to access URL: Could not resolve host: example.invalid", "push_failed"),
    (128, "fatal: the remote end hung up unexpectedly", "unknown"),
    (128, "fatal: unable to access URL: Connection reset by peer", "unknown"),
    (128, "fatal: unexpected synthetic error", "unknown"),
    (None, "fatal: Authentication failed", "unknown"),
    (128, " \tlocal:refs/heads/main\tupdated\nfatal: Authentication failed", "unknown"),
    (128, "Writing objects: 100%\nfatal: Authentication failed", "unknown"),
    (128, "remote: update accepted\nfatal: repository 'example' not found", "unknown"),
    (128, "!\tlocal:refs/heads/other\t[rejected] (policy)", "unknown"),
])
def test_push_classification_requires_known_pre_send_evidence(repo, rc, output, state):
    ops = R.git_ops
    _run(repo, "commit", "--allow-empty", "-m", "outgoing")
    head = _run(repo, "rev-parse", "HEAD").strip()
    tree = _run(repo, "rev-parse", "HEAD^{tree}").strip()
    calls = []
    history = R.commit_push_plan("demo")["expect"]["history"]
    sent = False

    def transport(path, *args, **kwargs):
        nonlocal sent
        calls.append(args[0])
        if args[0] == "push":
            sent = True
            return rc, output
        if args[0] == "ls-remote" and sent:
            return None, "synthetic unavailable observation"
        return R._git(path, *args, **kwargs)

    result = ops.publish(repo, head, tree, ops.push_target(repo), history=history, runner=transport)
    assert result["state"] == state
    assert result["commit"] == head
    assert "commit" not in calls and "add" not in calls


@pytest.mark.parametrize("override", [
    {"aheadTruncated": True}, {"aheadKnown": False},
    {"historyTruncated": True}, {"historyKnown": False},
    {"ok": False, "state": "unavailable"},
])
def test_ui_blocks_incomplete_history(override):
    review = plan()
    review.update(override)
    result = run_ui({"a": review}, [])
    assert not result["dialogs"]
    assert [step["kind"] for step in result["trace"]] == ["plan"]


@pytest.mark.parametrize("command", ["rev-list", "diff"])
def test_failed_count_or_pending_diff_preserves_underlying_error(repo, monkeypatch, command):
    real = R._git

    def fail(path, *args, **kwargs):
        if args[0] == command:
            return None, "synthetic timed-out read"
        return real(path, *args, **kwargs)

    monkeypatch.setattr(R, "_git", fail)
    review = R.commit_push_plan("demo")
    assert review["ok"] is False and review["state"] == "unavailable"
    assert "synthetic timed-out read" in review["error"]
    assert "expect" not in review


def test_binary_history_and_merge_parents_are_visible(repo):
    (repo / "binary.dat").write_bytes(bytes(range(256)))
    _run(repo, "add", "binary.dat")
    _run(repo, "commit", "-m", "binary ancestor")
    _run(repo, "checkout", "-b", "side")
    (repo / "side.txt").write_text("synthetic side content", encoding="utf-8")
    _run(repo, "add", "side.txt")
    _run(repo, "commit", "-m", "side ancestor")
    side = _run(repo, "rev-parse", "HEAD").strip()
    _run(repo, "checkout", "main")
    _run(repo, "commit", "--allow-empty", "-m", "main ancestor")
    _run(repo, "merge", "--no-ff", "side", "-m", "merge ancestor")
    review = R.commit_push_plan("demo")
    assert review["ok"], review
    assert review["historyCount"] == 4
    assert side in review["diff"]
    assert "GIT binary patch" in review["diff"]
    assert "+synthetic side content" in review["diff"]


def test_byte_cap_blocks_multibyte_pending_diff(repo):
    (repo / "large.txt").write_text("界" * 45000, encoding="utf-8")
    review = R.commit_push_plan("demo")
    assert review["ok"] is False and review["diffTruncated"] is True
    assert len(review["diff"].encode("utf-8")) <= 128000
    assert "expect" not in review


def test_incomplete_ancestry_is_blocked(repo):
    (repo / ".git/shallow").write_text(_run(repo, "rev-parse", "HEAD"), encoding="utf-8")
    review = R.commit_push_plan("demo")
    assert review["ok"] is False and review["historyKnown"] is False
    assert review["code"] == "history_unavailable"
    assert "expect" not in review


def test_approval_history_cannot_be_removed_before_publish(repo):
    review = R.commit_push_plan("demo")
    assert review["ok"], review
    review["expect"].pop("history")
    with pytest.raises(Refused) as error:
        R.commit_push("demo", "synthetic publish", review["expect"], push=True)
    assert error.value.code == "no_plan"


def render_components(components):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for UI fixture checks")
    root = Path(__file__).resolve().parents[1] / "scripts/task_console/static"
    program = "const element={innerHTML:''}; const document={querySelector:()=>({content:'synthetic'}),getElementById:()=>element};\n"
    program += (root / "api.js").read_text("utf-8")
    program += (root / "panels/skills.js").read_text("utf-8")
    program += "\nMAINT=" + json.dumps({"components": components}) + ";renderCatalog();console.log(element.innerHTML);"
    result = subprocess.run([node, "-"], input=program, encoding="utf-8", capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("checked,expected,state,tone", [
    (0, 0, "zero", "bad"), (1, 3, "partial", "warn"), (3, 3, "complete", "ok"),
    (3, 3, "complete", "bad"),  # Render the supplied tone even if counts look complete.
])
def test_collapsed_components_show_backend_tone_counts_and_state(checked, expected, state, tone):
    html = render_components({"available": True,
        "coverage": {"checked": checked, "expected": expected, "state": state, "tone": tone},
        "tasks": [{"task_id": "example-" + verdict, "verdict": verdict, "state": status}
                  for verdict, status in [("healthy", "up"), ("unhealthy", "down"), ("unknown", "unknown")]]})
    visible = html.split("<details>", 1)[0]
    assert f"var(--{tone})" in visible
    assert f"{checked}/{expected}" in visible
    assert "异常 1" in visible and "未检查 1" in visible
    assert "down" in html and "正常" in html
