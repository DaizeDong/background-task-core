"""Which account is this repository committed under, and is that the right one.

WHY THE PANEL NEEDS THIS. A repo panel that lists repositories without saying whose account each one
belongs to cannot answer the question people actually have in front of a list of forty-eight repos.
Worse, this machine commits under more than one GitHub account on purpose, and committing to one
account's repository under the other account's identity is a mistake that leaves no trace anywhere
the console currently looks: the commit succeeds, the push succeeds, and the wrong name is on the
author line forever.

FOUR STATES, NOT A BOOLEAN. "I could not check" must never render as "matches", because the whole
point of the panel is that an unchecked thing and a passing thing look different:

    ok         the owner is in the table and the local identity agrees with it
    mismatch   the owner is in the table and the local identity does NOT agree
    foreign    the owner is not in the table, which is normal for a fork or someone else's project
    unchecked  no table configured, or the table could not be read

MIRROR THE HOOK, DO NOT INVENT. The pre-commit hook is the authority on what "expected" means, and
it honours a per-repo override: a repository that deliberately commits under something other than
its owner's identity declares `guard.expectedEmail` / `guard.expectedName` in its own config. A
judgement that ignored that override would mark every such repo as a mismatch, and a panel that
cries wolf on repositories that are correctly configured is one nobody reads.

The table lives outside every repository on purpose. It states that two GitHub accounts belong to
one person, and vendoring it into a public repo would publish exactly that. So this module reads it
through TASK_CONSOLE_IDENTITIES and treats its absence as "unchecked", never as "fine".

NO ADDRESSES ARE RETURNED. The panel answers "which account" and "does it agree", and neither needs
an email address on screen. This page gets screenshotted.
"""
from __future__ import annotations

import io
import os
import subprocess
from pathlib import Path

OK, MISMATCH, FOREIGN, UNCHECKED = "ok", "mismatch", "foreign", "unchecked"

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _git(repo: Path, *args: str, timeout: int = 10) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", str(repo)] + list(args),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        return r.returncode, (r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def load_table() -> tuple[dict, str | None]:
    """Read the owner -> expected identity table. Returns (table, reason_it_is_empty).

    Same shape as the visibility loader next door, and for the same reason: a parse failure that
    silently produces an empty table makes every repo read as `foreign`, which looks exactly like a
    machine where nothing is registered. The reason travels with the data so the panel can say it.
    """
    p = os.environ.get("TASK_CONSOLE_IDENTITIES")
    if not p:
        return {}, None                       # not configured is not a failure
    path = Path(os.path.expanduser(p))
    try:
        raw = io.open(path, encoding="utf-8", errors="replace").read()
    except OSError as e:
        return {}, f"身份表读不到({e.__class__.__name__}),账号匹配一栏全部显示未检查"
    out = {}
    bad = 0
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = [x.strip() for x in s.split("|")]
        if len(parts) < 3 or not parts[0] or not parts[2]:
            bad += 1
            continue
        out[parts[0].lower()] = {"login": parts[0], "name": parts[1], "email": parts[2]}
    if not out:
        return {}, (f"身份表里没有可用的行(跳过 {bad} 行畸形的),账号匹配一栏全部显示未检查"
                    if bad else "身份表是空的,账号匹配一栏全部显示未检查")
    return out, None


def actual(repo: Path) -> dict:
    """The identity git would actually sign a commit with, here.

    `git var` rather than `git config`, because an environment override such as GIT_AUTHOR_EMAIL is
    invisible to config and is exactly the shape of a mistake worth catching. `scope` separates a
    repo that set its own identity from one inheriting the global default: both can be correct, but
    only the first is a decision somebody made about this repo.
    """
    rc, ident = _git(repo, "var", "GIT_AUTHOR_IDENT")
    name = email = None
    if rc == 0 and "<" in ident:
        name = ident.split("<", 1)[0].strip()
        email = ident.split("<", 1)[1].split(">", 1)[0].strip()
    rc_l, local_email = _git(repo, "config", "--local", "user.email")
    return {"name": name, "email": email,
            "scope": "local" if (rc_l == 0 and local_email) else "inherited"}


def expected(repo: Path, owner: str | None, table: dict) -> tuple[dict | None, str]:
    """What this repo SHOULD commit as, and where that expectation came from.

    The per-repo override comes first and is not a loophole: it is how a fork or an organisation
    repo declares that its owner's identity is deliberately not the right one here. The hook honours
    it, so this must too, or every correctly configured exception shows up as a defect.
    """
    rc_e, exp_email = _git(repo, "config", "--local", "guard.expectedEmail")
    rc_n, exp_name = _git(repo, "config", "--local", "guard.expectedName")
    if rc_e == 0 and exp_email:
        return {"login": None, "name": (exp_name if rc_n == 0 else None), "email": exp_email}, "repo"
    if owner and table:
        row = table.get(owner.lower())
        if row:
            return row, "table"
    return None, "none"


def judge(repo: Path, owner: str | None, table: dict, table_reason: str | None) -> dict:
    """Answer the panel's question, in four states plus a sentence a person can act on.

    No email address is placed in the result. `login` is an account name, which is the thing being
    asked about; the address is the mechanism and stays out of the UI.
    """
    if table_reason:
        return {"state": UNCHECKED, "owner": owner, "expect": None, "scope": None,
                "why": table_reason}
    if not table:
        return {"state": UNCHECKED, "owner": owner, "expect": None, "scope": None,
                "why": "没有设 TASK_CONSOLE_IDENTITIES,账号匹配是「未检查」,不是「匹配」。"}

    got = actual(repo)
    exp, source = expected(repo, owner, table)
    if exp is None:
        return {"state": FOREIGN, "owner": owner, "expect": None, "scope": got["scope"],
                "why": (f"owner「{owner}」不在身份表里。第三方仓(fork、别人的项目)本来就不该在,"
                        f"这不是配错。" if owner else "这个仓没有 remote,没有 owner 可以比对。")}

    same_email = bool(got["email"]) and got["email"].lower() == exp["email"].lower()
    # 名字不一致但邮箱一致仍算匹配:GitHub 按邮箱归属提交,名字只是显示。
    if same_email:
        return {"state": OK, "owner": owner, "expect": exp.get("login"), "scope": got["scope"],
                "source": source, "why": None}
    return {"state": MISMATCH, "owner": owner, "expect": exp.get("login"), "scope": got["scope"],
            "source": source,
            "why": ("这个仓会用一个和它 owner 不对应的身份提交。提交一旦推上去,作者行就永远是那个名字,"
                    "而任何文件扫描都看不见作者行。"
                    + (f"期望的是「{exp['login']}」。" if exp.get("login") else
                       "期望值来自这个仓自己的 guard.expected* 声明。"))}
