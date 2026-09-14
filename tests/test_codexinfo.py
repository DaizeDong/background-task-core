"""codexinfo 的用例。

重点全部在「缺失不能被读成值」这一类上,因为这个模块产出的每一个数字都会被画成
一条体积。一个悄悄跳过了一半的总量,和一个真的只有那么大的总量,在屏幕上长得一样。

每条断言都配一个能让它失败的对照:没设环境变量与设了但目录不在是两条不同的路径,
空目录与读不动的目录是两个不同的结论,数全了与没数全在输出上必须分得开。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "scripts", "task_console"))

import codexinfo  # noqa: E402


@pytest.fixture
def codex_root(tmp_path, monkeypatch):
    root = tmp_path / "codex"
    root.mkdir()
    monkeypatch.setenv("TASK_CONSOLE_CODEX", str(root))
    return root


def test_unset_env_is_unchecked_not_empty(monkeypatch):
    monkeypatch.delenv("TASK_CONSOLE_CODEX", raising=False)
    out = codexinfo.read()
    assert out["available"] is False
    assert "TASK_CONSOLE_CODEX" in out["reason"]
    # 未检查绝不能带着一个 0 出去:一个 0 会被画成一条空条,读起来是「什么都没有」。
    assert "bytes" not in out


def test_missing_dir_says_missing_not_unset(tmp_path, monkeypatch):
    """「没设」和「设了但那条路径不在」必须是两句不同的话。

    合并之后,目录被移走时页面言之凿凿地说环境变量没设,而它设了 ——
    人会照着这句去检查一个没问题的地方。
    """
    monkeypatch.setenv("TASK_CONSOLE_CODEX", str(tmp_path / "nope"))
    out = codexinfo.read()
    assert out["available"] is False
    assert "TASK_CONSOLE_CODEX" not in out["reason"]
    assert "nope" in out["reason"]


def test_empty_root_is_available_with_all_items_absent(codex_root):
    out = codexinfo.read()
    assert out["available"] is True
    assert out["bytes"] == 0
    for name in ("AGENTS.md", "config.toml", "sessions", "log"):
        item = out["items"][name]
        # 不在 != 读不了。两者都不是失败,但要做的事不同。
        assert item["available"] is True
        assert item["exists"] is False
    assert out["incomplete"] == []
    assert out["unread"] == []


def test_named_file_reports_size_and_mtime(codex_root):
    (codex_root / "AGENTS.md").write_text("x" * 512, encoding="utf-8")
    out = codexinfo.read()
    item = out["items"]["AGENTS.md"]
    assert item["exists"] is True
    assert item["bytes"] == 512
    assert item["mtime"] > 0
    assert out["bytes"] == 512


def test_session_count_walks_the_real_nested_layout(codex_root):
    """场数必须递归地数转录文件,而不是数顶层子项。

    ⚠ 这条用例的**目录形状是照生产抄的**:sessions/YYYY/MM/DD/*.jsonl。
    第一版 fixture 是平铺的,于是「数顶层子项」这个错实现全绿通过,
    而在真机上它把 2380 份转录、3.1G 报成了「2 场」。
    自己造的输入会让测试全绿,而闸门在生产里失效 —— 所以这里钉住嵌套。
    """
    sess = codex_root / "sessions"
    for day, n in (("2025/10/30", 2), ("2025/11/01", 1), ("2026/01/02", 3)):
        d = sess / day
        d.mkdir(parents=True)
        for i in range(n):
            (d / f"rollout-{i}.jsonl").write_text("x", encoding="utf-8")

    item = codexinfo.read()["items"]["sessions"]
    assert item["count"] == 6       # 场数:递归到的转录份数
    # 负对照:顶层子项只有两个年份目录。这两个数必须不相等,
    # 否则一个「数顶层」的实现照样能过这条用例。
    assert len(list(os.scandir(sess))) == 2
    assert item["count"] != len(list(os.scandir(sess)))


def test_non_transcript_files_count_as_files_but_not_sessions(codex_root):
    """会话目录里不是转录的东西不算「场」。"""
    d = codex_root / "sessions" / "2026" / "01" / "02"
    d.mkdir(parents=True)
    (d / "rollout-0.jsonl").write_text("x", encoding="utf-8")
    (d / "notes.txt").write_text("yy", encoding="utf-8")
    item = codexinfo.read()["items"]["sessions"]
    assert item["count"] == 1
    assert item["files"] == 2
    assert item["bytes"] == 3


def test_archived_sessions_counted_separately_from_live(codex_root):
    """归档的能不能删,和活跃的能不能删,是两个问题。合并成一个总量就答不了。"""
    (codex_root / "sessions" / "2026" / "01").mkdir(parents=True)
    (codex_root / "sessions" / "2026" / "01" / "live.jsonl").write_text("1234", encoding="utf-8")
    (codex_root / "archived_sessions").mkdir()
    (codex_root / "archived_sessions" / "old.jsonl").write_text("1", encoding="utf-8")
    items = codexinfo.read()["items"]
    assert items["sessions"]["bytes"] == 4
    assert items["archived_sessions"]["bytes"] == 1


def test_unreadable_subdir_marks_incomplete(codex_root, monkeypatch):
    """扫不动的条目必须让总量自报偏小。

    这是本模块的核心用例:没有它,`except OSError: continue` 会让体积安静地变小,
    而调用方拿到一个看起来正常的数。
    """
    log = codex_root / "log"
    log.mkdir()
    (log / "ok.txt").write_text("abcd", encoding="utf-8")
    deep = log / "locked"
    deep.mkdir()

    real_scandir = os.scandir

    def fake_scandir(p):
        if Path(p) == deep:
            raise PermissionError("locked")
        return real_scandir(p)

    monkeypatch.setattr(codexinfo.os, "scandir", fake_scandir)
    out = codexinfo.read()
    assert out["items"]["log"]["errors"] == 1
    assert out["incomplete"] == ["log"]
    # 负对照:能数到的那部分仍然要数出来,不能因为有一处失败就整项作废。
    assert out["items"]["log"]["bytes"] == 4


def test_clean_tree_reports_no_incomplete(codex_root):
    """上一条的负对照。没有它,那条断言在「永远 incomplete」的实现下也会通过。"""
    log = codex_root / "log"
    log.mkdir()
    (log / "ok.txt").write_text("abcd", encoding="utf-8")
    out = codexinfo.read()
    assert out["items"]["log"]["errors"] == 0
    assert out["incomplete"] == []


def test_file_where_dir_expected_is_unread_not_absent(codex_root):
    """`log` 是个文件而不是目录 —— 这是坏了,不是「没有」。"""
    (codex_root / "log").write_text("not a dir", encoding="utf-8")
    out = codexinfo.read()
    item = out["items"]["log"]
    assert item["available"] is False
    assert "不是目录" in item["reason"]
    assert out["unread"] == ["log"]


def test_total_excludes_unread_items(codex_root):
    """读不了的那一项不能给总量贡献 0 之后就没声了 —— 它要出现在 unread 里。"""
    (codex_root / "config.toml").write_text("ab", encoding="utf-8")
    (codex_root / "cache").write_text("x", encoding="utf-8")   # 文件冒充目录
    out = codexinfo.read()
    assert out["bytes"] == 2
    assert out["unread"] == ["cache"]
