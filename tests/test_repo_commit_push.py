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
    _run(work, "push", "-u", "origin", "main")

    monkeypatch.setenv("TASK_CONSOLE_REPOS", str(root))
    return work


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
        R.commit_push("demo", "msg", plan["files"], push=False)
    assert e.value.code == "plan_stale"
    assert "b.txt" in str(e.value)
    # 负对照:什么都没提交。
    assert "b.txt" in _run(repo, "status", "--porcelain=v1")


def test_commit_succeeds_when_plan_matches(repo):
    (repo / "a.txt").write_text("a", encoding="utf-8")
    plan = R.commit_push_plan("demo")
    out = R.commit_push("demo", "加一个文件", plan["files"], push=False)
    assert out["committed"] is True
    assert out["pushed"] is False
    assert _run(repo, "status", "--porcelain=v1").strip() == ""


def test_push_reaches_origin(repo):
    (repo / "a.txt").write_text("a", encoding="utf-8")
    plan = R.commit_push_plan("demo")
    R.commit_push("demo", "加一个文件", plan["files"], push=True)
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
        R.commit_push("demo", bad, plan["files"], push=False)
    assert e.value.code == "bad_message"


def test_good_commit_message_is_accepted(repo):
    """上一条的负对照:一个正常的提交信息必须过得去,

    否则一个「什么都拒绝」的实现会让那六个参数化用例全绿。
    """
    (repo / "a.txt").write_text("a", encoding="utf-8")
    plan = R.commit_push_plan("demo")
    assert R.commit_push("demo", "正常的一行提交信息", plan["files"], push=False)["ok"]


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
    R.commit_push("demo", "正常提交", plan["files"], push=True)

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
    R.commit_push("demo", "带空格的文件名", plan["files"], push=False)
    assert _run(repo, "status", "--porcelain=v1").strip() == ""


def test_nothing_to_do_is_reported(repo):
    plan = R.commit_push_plan("demo")
    assert plan["nothing"] is True
    assert plan["fileCount"] == 0
    assert plan["aheadCount"] == 0
