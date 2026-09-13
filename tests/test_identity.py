#!/usr/bin/env python3
"""账号归属判定:四态必须互相区分得开。

这块的全部价值在于「我没查」和「查了,对得上」是两个答案。一个把未配置渲染成 ✓ 的面板,
比没有这一栏更糟:它让人以为已经查过了。

每个用例都在临时目录里现建一个真的 git 仓,身份表也是现写的合成内容。读这台机器上的真表
会让测试既是一次披露,又会因为今天这台机器碰巧怎么配而红或绿。
"""
import io
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "task_console"))

import identity as I  # noqa: E402

ACME = "acme-login"
ACME_EMAIL = "1234567+acme-login@users.noreply.example.com"
OTHER_EMAIL = "7654321+other-login@users.noreply.example.com"


def git(repo, *a):
    r = subprocess.run(["git", "-C", str(repo)] + list(a), capture_output=True, text=True)
    assert r.returncode == 0, (a, r.stderr)
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "r"
    d.mkdir()
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    git(d, "config", "user.name", "Acme Person")
    git(d, "config", "user.email", ACME_EMAIL)
    return d


@pytest.fixture
def table(tmp_path, monkeypatch):
    p = tmp_path / "identities.conf"
    io.open(p, "w", encoding="utf-8").write(
        "# synthetic\n"
        "%s|Acme Person|%s\n"
        "other-login|Other Person|%s\n" % (ACME, ACME_EMAIL, OTHER_EMAIL))
    monkeypatch.setenv("TASK_CONSOLE_IDENTITIES", str(p))
    t, why = I.load_table()
    assert why is None and len(t) == 2
    return t


def test_matching_identity_is_ok(repo, table):
    r = I.judge(repo, ACME, table, None)
    assert r["state"] == I.OK
    assert r["expect"] == ACME


def test_wrong_identity_is_a_mismatch_and_says_why(repo, table):
    git(repo, "config", "user.email", OTHER_EMAIL)
    r = I.judge(repo, ACME, table, None)
    assert r["state"] == I.MISMATCH
    assert r["why"] and "作者行" in r["why"], "mismatch 必须说清楚后果"


def test_an_owner_not_in_the_table_is_foreign_not_a_mismatch(repo, table):
    """第三方仓不是配错。把 foreign 判成 mismatch 会造出一个天天亮的红灯。"""
    r = I.judge(repo, "somebody-else", table, None)
    assert r["state"] == I.FOREIGN
    assert "不是配错" in (r["why"] or "")


def test_no_table_is_unchecked_never_ok(repo, monkeypatch):
    """负对照,也是这整块存在的理由:没配 ≠ 匹配。"""
    monkeypatch.delenv("TASK_CONSOLE_IDENTITIES", raising=False)
    t, why = I.load_table()
    r = I.judge(repo, ACME, t, why)
    assert r["state"] == I.UNCHECKED
    assert r["state"] != I.OK


def test_an_unreadable_table_is_unchecked_and_carries_the_reason(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("TASK_CONSOLE_IDENTITIES", str(tmp_path / "missing.conf"))
    t, why = I.load_table()
    assert why, "读不到却没有给出原因,页面就只能显示一个没有解释的空白"
    r = I.judge(repo, ACME, t, why)
    assert r["state"] == I.UNCHECKED


def test_a_table_of_only_comments_is_unchecked_not_foreign(repo, tmp_path, monkeypatch):
    """空表会让每个仓都变成 foreign —— 那和一台没登记任何账号的机器长得一样。"""
    p = tmp_path / "empty.conf"
    io.open(p, "w", encoding="utf-8").write("# nothing here\n\n")
    monkeypatch.setenv("TASK_CONSOLE_IDENTITIES", str(p))
    t, why = I.load_table()
    assert why and not t
    assert I.judge(repo, ACME, t, why)["state"] == I.UNCHECKED


def test_a_repo_override_wins_over_the_table(repo, table):
    """仓自己声明 guard.expected* 时,那就是期望值。

    钩子认这条。判定不认的话,每一个**正确配置**的例外都会被报成缺陷,
    而一个对着正确配置报警的面板不会被读第二次。
    """
    git(repo, "config", "user.email", OTHER_EMAIL)
    git(repo, "config", "guard.expectedEmail", OTHER_EMAIL)
    r = I.judge(repo, ACME, table, None)
    assert r["state"] == I.OK
    assert r.get("source") == "repo"


def test_the_override_can_also_fail(repo, table):
    """正对照:声明了期望值而实际对不上,照样是 mismatch。"""
    git(repo, "config", "guard.expectedEmail", OTHER_EMAIL)
    r = I.judge(repo, ACME, table, None)
    assert r["state"] == I.MISMATCH


def test_local_and_inherited_identity_are_distinguished(repo, table, tmp_path):
    """本仓显式配过,和继承全局默认,是两件事 —— 都可能对,但只有前者是有人决定过的。"""
    assert I.actual(repo)["scope"] == "local"
    bare = tmp_path / "b"
    bare.mkdir()
    subprocess.run(["git", "init", "-q", str(bare)], check=True)
    assert I.actual(bare)["scope"] == "inherited"


def test_no_email_address_is_returned_anywhere(repo, table):
    """这一栏回答的是「哪个账号」和「对不对得上」,不需要把地址画到屏幕上。"""
    for owner in (ACME, "somebody-else"):
        r = I.judge(repo, owner, table, None)
        blob = repr(r)
        assert "@" not in blob, "判定结果里出现了邮箱地址: %s" % blob


def test_a_repo_with_no_owner_says_so(repo, table):
    r = I.judge(repo, None, table, None)
    assert r["state"] == I.FOREIGN
    assert "没有 remote" in (r["why"] or "")


def test_a_table_of_only_malformed_lines_is_unchecked_and_says_so(repo, tmp_path, monkeypatch):
    """全是畸形行的表,和一个没配的表,和一个空表,都必须落到「未检查」并给出原因。

    这条单独写出来是因为它有自己的分支:行数不为零但可用行为零。悄悄返回空表会让每个仓
    变成 foreign,而 foreign 在屏幕上读起来像「第三方仓,正常」。
    """
    p = tmp_path / "junk.conf"
    io.open(p, "w", encoding="utf-8").write("no-delimiters-here\nalso|missing-third\n|\n")
    monkeypatch.setenv("TASK_CONSOLE_IDENTITIES", str(p))
    t, why = I.load_table()
    assert not t
    assert why, "全是畸形行却没有给出原因,页面只能显示一个没有解释的「未检查」"
    assert "畸形" in why
    assert I.judge(repo, ACME, t, why)["state"] == I.UNCHECKED
