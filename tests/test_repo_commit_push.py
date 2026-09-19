"""提交并推送:两步流程的闸门。

这是这块面板上唯一一个把东西送出这台机器的动作,所以每一条断言都配一个能让它
失败的对照。重点不在「能不能提交成功」,而在**该拒的时候拒不拒**:

  - 计划是只读的(跑完之后工作树逐字不变)
  - 执行必须带着计划里那份清单回来,对不上就整个拒绝
  - 提交信息里的换行 / 反引号 / $ 一律拒绝
  - 不许出现 --no-verify,不许出现 `git add -A`

最后两条用的是**看实际发出的 git 参数**,不是看代码里有没有那个字符串:
一个被拼进变量再传进去的 `-A` 用读源码的办法抓不到。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "scripts", "task_console"))

import repos as R  # noqa: E402
from maint import Refused  # noqa: E402


def _run(cwd, *args):
    r = subprocess.run(("git",) + args, cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, f"{args}: {r.stdout}{r.stderr}"
    return r.stdout


def copy_git_objects(source, destination):
    # Git objects are immutable and read-only on Windows; copy only missing ones.
    def copy_missing(src, dst):
        if not os.path.exists(dst):
            shutil.copy2(src, dst)
        return dst
    shutil.copytree(source, destination, dirs_exist_ok=True, copy_function=copy_missing)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """一个带 origin 的真仓。origin 是本地裸仓,所以 push 不出这台机器。"""
    root = tmp_path / "repos"
    root.mkdir()
    bare = tmp_path / "origin.git"
    _run(tmp_path, "init", "--bare", "-b", "main", str(bare))

    work = root / "demo"
    work.mkdir()
    _run(work, "init", "-b", "main")
    _run(work, "config", "user.email", "user1@example.com")
    _run(work, "config", "user.name", "Example User")
    # 钩子路径指向一个空目录:这几条用例测的是这个模块的闸,不是机器上那套钩子。
    hooks = tmp_path / "nohooks"
    hooks.mkdir()
    _run(work, "config", "core.hooksPath", str(hooks))
    (work / "seed.txt").write_text("seed\n", encoding="utf-8")
    _run(work, "add", "seed.txt")
    _run(work, "commit", "-m", "seed")
    _run(work, "remote", "add", "origin", str(bare))
    _run(work, "update-ref", "refs/remotes/origin/main", "HEAD")
    _run(work, "config", "branch.main.remote", "origin")
    _run(work, "config", "branch.main.merge", "refs/heads/main")
    # Tracking state alone is not a remote fact. Seed the actual synthetic ref.
    copy_git_objects(work / ".git" / "objects", bare / "objects")
    _run(bare, "update-ref", "refs/heads/main", _run(work, "rev-parse", "HEAD").strip())

    # Fake only transport: this sandbox cannot launch Git's MSYS local-push shell.
    # Objects, commits and refs remain real, confined to these synthetic repos.
    real_git = R._git

    def local_transport(repo_path, *args, **kwargs):
        if args and args[0] == "ls-remote":
            return native_local_transport(repo_path, *args, **kwargs)
        if args and args[0] == "push":
            oid = _run(work, "rev-parse", "HEAD").strip()
            copy_git_objects(work / ".git" / "objects", bare / "objects")
            _run(bare, "update-ref", "refs/heads/main", oid)
            _run(work, "update-ref", "refs/remotes/origin/main", oid)
            return 0, "synthetic local transport accepted"
        return real_git(repo_path, *args, **kwargs)

    monkeypatch.setattr(R, "_git", local_transport)

    monkeypatch.setenv("TASK_CONSOLE_REPOS", str(root))
    return work


def native_local_transport(repo_path, *args, **kwargs):
    """Native Git pipe transport avoids this sandbox's MSYS shared-memory failure.

    This adapter is only for synthetic local fixtures. Both Git protocol peers
    remain real; no listener, daemon, credentials or network are involved.
    """
    args = list(args)
    if args[0] in {"ls-remote", "push"}:
        position = args.index("--") + 1
        from pathlib import Path
        location = Path(args[position]).resolve()
        assert location.is_relative_to(Path(repo_path).resolve().parent.parent)
        escaped = str(location).replace("\\", "/").replace("%", "%%").replace(" ", "% ")
        args[position] = "ext::git %s " + escaped
        env = dict(os.environ, **kwargs.get("env", {}))
        result = subprocess.run(["git", "-c", "protocol.ext.allow=always", *args],
                                cwd=repo_path, env=env, capture_output=True,
                                timeout=kwargs.get("timeout", 30))
        return result.returncode, (result.stdout + result.stderr).decode("utf-8", errors="replace")
    return R.git_ops.run_git(repo_path, *args, **kwargs)


def test_plan_is_read_only(repo):
    """计划跑完,工作树和索引逐字不变。

    负对照在最后两行:如果计划顺手 add 了,status 就不再是 `??`。
    """
    (repo / "new.txt").write_text("x", encoding="utf-8")
    before = _run(repo, "status", "--porcelain=v1")
    plan = R.commit_push_plan("demo")
    after = _run(repo, "status", "--porcelain=v1")
    assert plan["files"] == ["new.txt"]
    assert before == after
    assert after.strip().startswith("??")


def test_plan_lists_branch_and_upstream(repo):
    plan = R.commit_push_plan("demo")
    assert plan["branch"] == "main"
    assert plan["upstream"] == "origin/main"
    assert plan["blocked"] == []


def test_no_upstream_is_blocked_not_guessed(repo):
    """没有 upstream 时不替人选推到哪里。"""
    _run(repo, "checkout", "-b", "side")
    plan = R.commit_push_plan("demo")
    assert plan["upstream"] is None
    assert any("upstream" in b for b in plan["blocked"])


def test_commit_refuses_when_worktree_changed_since_plan(repo):
    """计划之后工作树变了就整个拒绝,而不是顺手把新出现的也提交了。

    这条是整组用例的核心:这个仓已经因为「长任务中途工作树被另一自动化改掉」
    出过一次事。
    """
    (repo / "a.txt").write_text("a", encoding="utf-8")
    plan = R.commit_push_plan("demo")
    (repo / "b.txt").write_text("b", encoding="utf-8")     # 另一个写入方插进来
    with pytest.raises(Refused) as e:
        R.commit_push("demo", "msg", plan["expect"], push=False)
    assert e.value.code == "plan_stale"
    assert "b.txt" in str(e.value)
    # 负对照:什么都没提交。
    assert "b.txt" in _run(repo, "status", "--porcelain=v1")


def test_commit_succeeds_when_plan_matches(repo):
    (repo / "a.txt").write_text("a", encoding="utf-8")
    plan = R.commit_push_plan("demo")
    out = R.commit_push("demo", "加一个文件", plan["expect"], push=False)
    assert out["committed"] is True
    assert out["pushed"] is False
    assert _run(repo, "status", "--porcelain=v1").strip() == ""


def test_push_reaches_origin(repo):
    (repo / "a.txt").write_text("a", encoding="utf-8")
    plan = R.commit_push_plan("demo")
    R.commit_push("demo", "加一个文件", plan["expect"], push=True)
    local = _run(repo, "rev-parse", "HEAD").strip()
    remote = _run(repo, "rev-parse", "origin/main").strip()
    assert local == remote


def test_missing_plan_refuses(repo):
    """没带清单不许提交 —— 那等于回到 `git add -A`。"""
    (repo / "a.txt").write_text("a", encoding="utf-8")
    with pytest.raises(Refused) as e:
        R.commit_push("demo", "msg", None, push=False)
    assert e.value.code == "no_plan"


@pytest.mark.parametrize("bad", ["", "   ", "a" * 201, "两行\n第二行", "反引号`x`", "$HOME"])
def test_bad_commit_messages_refused(repo, bad):
    (repo / "a.txt").write_text("a", encoding="utf-8")
    plan = R.commit_push_plan("demo")
    with pytest.raises(Refused) as e:
        R.commit_push("demo", bad, plan["expect"], push=False)
    assert e.value.code == "bad_message"


def test_good_commit_message_is_accepted(repo):
    """上一条的负对照:一个正常的提交信息必须过得去,

    否则一个「什么都拒绝」的实现会让那六个参数化用例全绿。
    """
    (repo / "a.txt").write_text("a", encoding="utf-8")
    plan = R.commit_push_plan("demo")
    assert R.commit_push("demo", "正常的一行提交信息", plan["expect"], push=False)["ok"]


def test_git_is_never_called_with_no_verify_or_add_all(repo, monkeypatch):
    """看实际发出的 git 参数,不看源码里有没有那个字符串。

    一个被拼进变量再传进去的 `-A`,用读源码的办法抓不到。
    """
    seen = []
    real = R._git

    def spy(repo_path, *args, **kw):
        seen.append(args)
        return real(repo_path, *args, **kw)

    monkeypatch.setattr(R, "_git", spy)
    (repo / "a.txt").write_text("a", encoding="utf-8")
    plan = R.commit_push_plan("demo")
    R.commit_push("demo", "正常提交", plan["expect"], push=True)

    flat = [a for args in seen for a in args]
    assert "--no-verify" not in flat
    assert "-n" not in flat
    adds = [args for args in seen if args and args[0] == "add"]
    assert adds, "根本没调用 git add,这条用例就没在测它该测的东西"
    for args in adds:
        assert "-A" not in args and "--all" not in args and "-u" not in args
        assert "--" in args, "add 必须用 -- 把路径和参数分开"


def test_paths_with_spaces_survive(repo):
    """文件名里有空格时,路径不能被按空格切开。"""
    (repo / "two words.txt").write_text("x", encoding="utf-8")
    plan = R.commit_push_plan("demo")
    assert plan["files"] == ["two words.txt"]
    R.commit_push("demo", "带空格的文件名", plan["expect"], push=False)
    assert _run(repo, "status", "--porcelain=v1").strip() == ""


def test_nothing_to_do_is_reported(repo):
    plan = R.commit_push_plan("demo")
    assert plan["nothing"] is True
    assert plan["fileCount"] == 0
    assert plan["aheadCount"] == 0


@pytest.mark.parametrize("drift", ["content", "head", "index", "untracked", "staged"])
def test_review_rejects_drift_before_commit(repo, drift):
    """Approval of paths alone must not authorize different bytes or history."""
    target = repo / "a.txt"
    target.write_text("reviewed\n", encoding="utf-8")
    plan = R.commit_push_plan("demo")
    if drift == "content":
        target.write_text("unreviewed\n", encoding="utf-8")
    elif drift == "head":
        _run(repo, "commit", "--allow-empty", "-m", "concurrent commit")
    elif drift == "index":
        _run(repo, "add", "a.txt")
    else:
        (repo / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
        if drift == "staged":
            _run(repo, "add", "unrelated.txt")
    head = _run(repo, "rev-parse", "HEAD")
    index = (repo / ".git" / "index").read_bytes()
    with pytest.raises(Refused) as error:
        R.commit_push("demo", "reviewed change", plan.get("expect", plan["files"]), push=False)
    assert error.value.code == "plan_stale"
    assert _run(repo, "rev-parse", "HEAD") == head
    assert (repo / ".git" / "index").read_bytes() == index


def test_path_only_plan_is_rejected_even_for_push_only(repo):
    with pytest.raises(Refused) as error:
        R.commit_push("demo", "publish", [], push=True)
    assert error.value.code == "no_plan"


def test_retry_respects_push_false(repo):
    head = _run(repo, "rev-parse", "HEAD").strip()
    tree = _run(repo, "rev-parse", "HEAD^{tree}").strip()
    target = R.commit_push_plan("demo")["expect"]["target"]
    with pytest.raises(Refused):
        R.commit_push("demo", "retry", {"retry": {"commit": head, "tree": tree},
                                        "target": target}, push=False)


def test_unknown_unreviewed_commit_does_not_issue_retry_approval(repo, monkeypatch):
    (repo / "a.txt").write_text("reviewed", encoding="utf-8")
    plan = R.commit_push_plan("demo")
    real = R._git

    def changed_by_hook(path, *args, **kwargs):
        if args[0] == "commit":
            (repo / "a.txt").write_text("hook changed content", encoding="utf-8")
            assert real(path, "add", "--", "a.txt", **kwargs)[0] == 0
        return real(path, *args, **kwargs)

    monkeypatch.setattr(R, "_git", changed_by_hook)
    result = R.commit_push("demo", "snapshot", plan["expect"], push=True)
    assert result["state"] == "unknown"
    assert not result["pushed"]
    assert "expect" not in result


def test_ui_push_failure_has_receipt_and_retries_without_committing(repo, monkeypatch):
    (repo / "a.txt").write_text("reviewed", encoding="utf-8")
    plan = R.commit_push_plan("demo")
    real = R._git

    def reject(path, *args, **kwargs):
        if args[0] == "push":
            return 1, "!\tlocal:refs/heads/main\t[rejected] (policy)\n"
        return real(path, *args, **kwargs)

    monkeypatch.setattr(R, "_git", reject)
    result = R.commit_push("demo", "snapshot", plan["expect"], push=True)
    assert result["state"] == "push_failed"
    assert result["committed"] is True
    (repo / "later.txt").write_text("later", encoding="utf-8")
    monkeypatch.setattr(R, "_git", real)
    retry = R.commit_push("demo", "retry", result["expect"], push=True)
    assert retry["state"] == "pushed"
    assert retry["commit"] == result["commit"]
    assert retry["committed"] is False
    assert "later.txt" in _run(repo, "status", "--porcelain")
