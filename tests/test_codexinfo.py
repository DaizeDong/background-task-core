"""codexinfo 的用例。

重点全部在「缺失不能被读成值」这一类上,因为这个模块产出的每一个数字都会被画成
一条体积。一个悄悄跳过了一半的总量,和一个真的只有那么大的总量,在屏幕上长得一样。

每条断言都配一个能让它失败的对照:没设环境变量与设了但目录不在是两条不同的路径,
空目录与读不动的目录是两个不同的结论,数全了与没数全在输出上必须分得开。
"""

from __future__ import annotations

import os
import subprocess
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


# ---------- 逐份转录的清单与删除 ----------

def _mk(root, rel, size=1):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x" * size, encoding="utf-8")
    return p


def test_list_is_sorted_by_size_desc(codex_root):
    _mk(codex_root, "sessions/2026/01/01/small.jsonl", 10)
    _mk(codex_root, "sessions/2026/01/02/big.jsonl", 900)
    _mk(codex_root, "sessions/2026/01/02/mid.jsonl", 100)
    out = codexinfo.list_transcripts("sessions")
    assert [i["name"] for i in out["items"]] == ["big.jsonl", "mid.jsonl", "small.jsonl"]
    assert out["count"] == 3
    assert out["bytes"] == 1010
    # 相对路径,不是绝对路径:一个交出绝对路径的列表接口,迟早会被配上一个
    # 接受绝对路径的删除接口。
    assert all(not os.path.isabs(i["rel"]) for i in out["items"])
    assert out["items"][0]["rel"] == "sessions/2026/01/02/big.jsonl"


def test_list_carries_mtime_so_it_can_be_sorted_by_time(codex_root):
    p = _mk(codex_root, "sessions/2026/01/01/a.jsonl", 5)
    os.utime(p, (1700000000, 1700000000))
    out = codexinfo.list_transcripts("sessions")
    assert out["items"][0]["mtime"] == pytest.approx(1700000000, abs=2)


def test_list_ignores_non_transcripts(codex_root):
    _mk(codex_root, "sessions/2026/01/01/a.jsonl", 5)
    _mk(codex_root, "sessions/2026/01/01/notes.txt", 500)
    out = codexinfo.list_transcripts("sessions")
    assert [i["name"] for i in out["items"]] == ["a.jsonl"]
    assert out["bytes"] == 5


def test_list_of_missing_dir_is_empty_not_unavailable(codex_root):
    """会话目录还没建出来 != 读不了。"""
    out = codexinfo.list_transcripts("sessions")
    assert out["available"] is True
    assert out["exists"] is False
    assert out["items"] == []


def test_list_rejects_other_directories(codex_root):
    out = codexinfo.list_transcripts("cache")
    assert out["available"] is False


def test_delete_removes_only_the_named_ones(codex_root):
    _mk(codex_root, "sessions/2026/01/01/a.jsonl", 10)
    keep = _mk(codex_root, "sessions/2026/01/01/b.jsonl", 20)
    out = codexinfo.delete_transcripts(["sessions/2026/01/01/a.jsonl"])
    assert out["ok"] is True
    assert out["deleted"] == 1
    assert out["freed"] == 10
    assert keep.is_file(), "没点名的那份必须还在"


@pytest.mark.parametrize("bad", [
    "../outside.jsonl",
    "sessions/../../escape.jsonl",
    "sessions/2026/01/01/a.txt",          # 不是转录
    "/abs/path.jsonl",
    "sessions\2026\a.jsonl",            # 反斜杠
    "sessions/./a.jsonl",
])
def test_delete_refuses_bad_paths(codex_root, bad):
    _mk(codex_root, "sessions/2026/01/01/a.jsonl", 10)
    with pytest.raises(Exception) as e:
        codexinfo.delete_transcripts([bad])
    assert getattr(e.value, "code", "") in ("bad_path", "bad_args"), bad
    # 负对照:一条都不许删。没有这句,一个「先删再校验」的实现照样抛异常。
    assert (codex_root / "sessions/2026/01/01/a.jsonl").is_file()


def test_delete_refuses_an_existing_non_transcript_inside_the_session_dirs(codex_root):
    """后缀闸同样要用一个**真实存在**的目标。

    ⚠ 和上一条同一个病:参数化里那条 `a.txt` 指的文件根本不存在,于是它是被
    「不是普通文件」拦下的,跟后缀毫无关系。实测:把后缀闸整个去掉,27 条照样全绿。
    会话目录里除了转录还有别的东西,而这个删除接口只该动转录。
    """
    victim = _mk(codex_root, "sessions/2026/01/01/notes.txt", 10)
    with pytest.raises(Exception) as e:
        codexinfo.delete_transcripts(["sessions/2026/01/01/notes.txt"])
    assert getattr(e.value, "code", "") == "bad_path"
    assert victim.is_file(), "会话目录里不是转录的东西也不许删"


def test_delete_refuses_an_existing_file_outside_the_session_dirs(codex_root):
    """这一条必须用一个**真实存在**的目标。

    ⚠ 它原来混在上面那组参数化里,写成 `cache/x.jsonl` —— 而那个文件根本不存在,
    于是它是被最后那道「不是普通文件」拦下的,跟目录白名单毫无关系。
    实测:把 `segs[0] not in SESSION_DIRS` 那道闸整个去掉,27 条用例照样全绿。
    一条因为别的原因通过的用例,和一条根本不存在的用例,效果一样。
    """
    victim = _mk(codex_root, "cache/precious.jsonl", 10)
    with pytest.raises(Exception) as e:
        codexinfo.delete_transcripts(["cache/precious.jsonl"])
    assert getattr(e.value, "code", "") == "bad_path"
    assert victim.is_file(), "会话目录以外的文件一个都不许删"


def test_delete_refuses_whole_batch_if_any_path_is_bad(codex_root):
    """部分成功的删除最难收拾:人不知道到底少了哪些。"""
    a = _mk(codex_root, "sessions/2026/01/01/a.jsonl", 10)
    b = _mk(codex_root, "sessions/2026/01/01/b.jsonl", 10)
    with pytest.raises(Exception) as e:
        codexinfo.delete_transcripts(["sessions/2026/01/01/a.jsonl", "../evil.jsonl"])
    assert getattr(e.value, "code", "") == "bad_path"
    assert a.is_file() and b.is_file()


def test_delete_refuses_empty_list(codex_root):
    with pytest.raises(Exception) as e:
        codexinfo.delete_transcripts([])
    assert getattr(e.value, "code", "") == "bad_args"


def test_delete_refuses_a_file_that_is_not_there(codex_root):
    (codex_root / "sessions").mkdir()
    with pytest.raises(Exception) as e:
        codexinfo.delete_transcripts(["sessions/nope.jsonl"])
    assert getattr(e.value, "code", "") == "bad_path"


def test_delete_refuses_a_junction_that_escapes_the_tree(codex_root, tmp_path):
    """归属闸(resolve 之后 relative_to)唯一真正独当一面的那种输入。

    ⚠ 这条用例的形状是量出来的,不是想出来的。先把六道闸逐个投毒,发现**所有**
    现成用例的输入都被多道闸同时拦住 —— 去掉任何一道都不会红。一个每道闸都可以
    被单独删掉而不报警的套件,挡不住纵深被一层层磨掉。

    Windows 的目录联接正好穿过其余每一道:路径里没有 `..`、没有反斜杠、没有冒号,
    不是绝对路径,首段是 sessions,后缀是 .jsonl,`p.is_file()` 为真,
    而且 **`p.is_symlink()` 为假**(联接在 Python 眼里不是符号链接)。
    只有 resolve() 之后的归属检查看得见它落在树外。

    这不是假想形状:这套东西本来就用联接部署目录。
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "precious.jsonl"
    victim.write_text("不在那棵树里", encoding="utf-8")

    (codex_root / "sessions").mkdir(exist_ok=True)
    # ⚠ 「这个平台没有联接」和「这一次没建成」必须分开。
    # 第一版无条件 skip,实测在一次全量里静默跳过了一次、重跑又过,
    # 一个偶尔跳过的用例比一个失败的更糟:它不报错地把覆盖降下来,
    # 而这条用例守的是一个不可逆删除接口唯一独当一面的闸。
    # 非 Windows 才是真的做不到;在 Windows 上 mklink /J 不需要管理员,
    # 失败就是出了别的事,要带着原文喊出来。
    if os.name != "nt":
        pytest.skip("目录联接是 Windows 的东西")
    made = subprocess.run(["cmd", "/c", "mklink", "/J",
                           str(codex_root / "sessions" / "junc"), str(outside)],
                          capture_output=True, text=True)
    assert made.returncode == 0, (
        "建目录联接失败,而这条用例是那道归属闸唯一的覆盖 —— "
        "不要把它改回 skip,先看这里的原文:"
        + (made.stdout or "") + (made.stderr or ""))

    target = codex_root / "sessions" / "junc" / "precious.jsonl"
    # 先证明它确实穿过了其余每一道闸,否则这条用例又会变成「被别的原因拦下」。
    assert target.is_file() and not target.is_symlink()

    with pytest.raises(Exception) as e:
        codexinfo.delete_transcripts(["sessions/junc/precious.jsonl"])
    assert getattr(e.value, "code", "") == "bad_path"
    assert victim.is_file(), "树外的文件必须原封不动"


def test_delete_refuses_an_absolute_path_to_a_real_transcript(codex_root):
    """绝对路径闸同样要指向一个真实存在的目标,否则它也是被「文件不存在」拦下的。

    ⚠ 这一条**不是**单闸用例:Windows 的绝对路径同时带反斜杠和冒号,
    形状闸与段闸都会拦它。留着它是因为它是真实的误用形状,
    但别把它当成「绝对路径闸有覆盖」的证据 —— 那道闸没有独当一面的用例。
    """
    real = _mk(codex_root, "sessions/2026/01/01/a.jsonl", 10)
    with pytest.raises(Exception) as e:
        codexinfo.delete_transcripts([str(real)])
    assert getattr(e.value, "code", "") == "bad_path"
    assert real.is_file()


def test_delete_refuses_more_than_the_cap_without_deleting_any(codex_root):
    """上限闸:超过一条就整批拒绝,而且一条都不许先删掉。"""
    made = [_mk(codex_root, f"sessions/2026/01/01/f{i}.jsonl", 1)
            for i in range(3)]
    rels = [f"sessions/2026/01/01/f{i}.jsonl" for i in range(3)]
    # 真实路径凑到上限之上;重复同一条即可,闸在长度上,不在内容上。
    over = rels * (codexinfo.LIST_CAP // 3 + 2)
    assert len(over) > codexinfo.LIST_CAP
    with pytest.raises(Exception) as e:
        codexinfo.delete_transcripts(over)
    assert getattr(e.value, "code", "") == "bad_args"
    assert all(p.is_file() for p in made), "拒绝整批时一条都不许删"


def test_delete_without_config_refuses(monkeypatch):
    """没配根目录时拒绝。这条分支原来一点覆盖都没有。"""
    monkeypatch.delenv("TASK_CONSOLE_CODEX", raising=False)
    with pytest.raises(Exception) as e:
        codexinfo.delete_transcripts(["sessions/a.jsonl"])
    assert getattr(e.value, "code", "") == "no_config"
