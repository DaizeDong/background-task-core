"""仓库面板:一眼看完一整片 git 仓的分支、脏文件、有没有没推上去的提交。

为什么值得单独一块:「有没有未推送的提交」这件事没有任何东西会主动告诉你。它不会让
任何检查变红,不会让任何任务失败,只会在某天换机器或者清目录的时候变成永久损失。

三条判据上的讲究:

**没有 upstream 时 ahead/behind 是 None,不是 0。** 一个从没设过上游的分支,和一个
和上游完全同步的分支,在「0/0」这个显示下长得一模一样,而前者的提交其实一个都没推出去。

**`.git` 是文件而不是目录,说明这是 submodule 或 worktree。** 这个区分是承重的:
向上找 `.git` 的代码会停在 submodule 上,把子模块当成独立仓来判断。

**读不了的仓要报错,不能跳过。** 一个悄悄跳过三个仓的扫描器,和一个扫完发现三个仓
都没问题的扫描器,输出的绿色一模一样。

网络动作有两个,都只由人显式触发:fetch 只读;push 是这个模块里唯一把东西送出这台机器的,
所以它是两步的 —— 先出只读计划,执行时必须带回计划里那份文件清单,对不上就整个拒绝。
「push 绝不自动」这句仍然成立,而且现在由 commit_push 那段注释和它的用例守着;
这里不再复述它的规则,免得同一条约束有两份会各自漂的说法。
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import subprocess
import time
from pathlib import Path

# 在 pythonw(GUI 子系统)下,每个控制台子程序都要新分配一个控制台。那次分配很慢,
# 而且并发时根本不成立:实测同一条 git 命令,普通 python 下几毫秒,pythonw 下单次
# 4.5 秒,四个并发全部 15 秒超时。加上这个标志之后单次降到 0.08 秒。
#
# 这个坑只在生产形态下出现,而开发期测试都是用普通 python 跑的:探针必须复现真实的
# 调用形状,否则测的是另一个程序。
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


CLEAN, DIRTY, UNPUSHED, DETACHED, ERROR = "clean", "dirty", "unpushed", "detached", "error"

# 仓库的类型。**全部从仓库自身的形状观察出来,没有任何一张仓名表。**
# 这不是洁癖:一份「哪个仓是什么、谁跟谁有关系」的清单就是舰队地图,
# 而这个文件在公开仓里。判据只认形状,于是它在任何人的机器上都成立,
# 也不会随着增删仓库而过期。
KIND_SKILL = "skill"          # 有 SKILL.md(自己的或 skills/*/ 下的)
KIND_COMPANION = "companion"  # origin 名是 <宿主>-config,而宿主也在这次扫描里
KIND_SHARED = "shared"        # 被这次扫描里别的仓当 submodule 引用
KIND_OTHER = "other"

# 组的显示顺序。伴生仓不单独成组:它跟在自己的宿主下面。
KIND_ORDER = (KIND_SKILL, KIND_SHARED, KIND_OTHER)


# 远程跟踪引用多久之内算数。超过这个岁数,behind 一律画成「未知」而不是它缓存里那个数。
# 24 小时:有每日任务在动的仓,一天之内的缓存基本跟得上;
# 再久就说不准了,而说不准的时候必须说「不知道」,不能把缓存里的 0 当成结论。
BEHIND_TRUST_HOURS = 24.0


def _repo_name_from_url(url: str | None) -> str | None:
    """从 remote URL 取出裸仓名。

    形式有两种:`https://host/owner/name.git` 和 `git@alias:owner/name.git`。
    这段归一化和 guards/tools/datadir.py 的 `_proves_companion` 是同一套 ——
    伴生关系的**权威判据在那里**(origin 名等于 `<skill>-config`,或有 .companion 标记),
    这里只是照它的形状去认。
    ⚠ 不要在这里发明第二套判据。这个面板回答的是「这次扫描里看得见什么关系」,
    不回答「伴生仓在哪、对不对」—— 后者是 datadir 和 data_boundary 的职责,
    而同一个问题有两个都自称权威的答案,正是这个控制台反复在修的那类缺陷。
    """
    if not url:
        return None
    base = url.rstrip("/").rsplit("/", 1)[-1]
    if base.endswith(".git"):
        base = base[:-4]
    if ":" in base:
        base = base.rsplit(":", 1)[-1]
    return base or None


def _submodule_parents(repo: Path) -> list[str]:
    """这个仓把哪些仓当 submodule 用。读 .gitmodules,取每个 url 的裸仓名。

    读文件而不是跑 `git submodule`:后者要求子模块已经 checkout,
    而「声明了但没 checkout」正是这套闸门栽过的那个坑(目录存在且为空,
    git 找不到钩子就什么都不跑、退出 0)。声明本身才是关系的事实。
    """
    p = repo / ".gitmodules"
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in txt.splitlines():
        line = line.strip()
        if line.startswith("url"):
            _, _, v = line.partition("=")
            n = _repo_name_from_url(v.strip())
            if n:
                out.append(n)
    return out


def _has_skill_manifest(repo: Path) -> bool:
    """SKILL.md 在根上,或者在 skills/<任意一个>/ 下。两种布局真实存在,都要认。"""
    if (repo / "SKILL.md").is_file():
        return True
    skills = repo / "skills"
    try:
        return any((d / "SKILL.md").is_file() for d in skills.iterdir() if d.is_dir())
    except OSError:
        return False

# 严重度序。unpushed 排在 dirty 前面:脏文件你自己知道,没推的提交没人会告诉你。
_SEV = {CLEAN: 0, DETACHED: 1, DIRTY: 2, UNPUSHED: 3, ERROR: 4}


def _git(repo: Path, *args: str, timeout: int = 20) -> tuple[int, str]:
    try:
        r = subprocess.run(("git", "-C", str(repo)) + args, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        # 失败时要带上 stderr:git 把成功的输出写 stdout,把**失败的原因**写 stderr。
        # 原来只取 stdout,于是仓库面板上一个 error 行只会显示「git status 退出 128」、
        # fetch 失败只会显示「fetch 退出 128:」后面什么都没有 :
        # 真正的原因(认证失败、dubious ownership、远端不存在)读不到,
        # 排查只能到命令行重跑一遍,而那正是这块面板想省掉的事。
        if r.returncode != 0:
            return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
        return r.returncode, (r.stdout or "")
    except (OSError, subprocess.SubprocessError) as e:
        return 127, f"{e.__class__.__name__}: {e}"


def _owner_repo(url: str | None) -> str | None:
    """从 remote URL 取出 `owner/repo`(小写)。可见性表就是按这个键存的。

    三种形式都要认:
        https://github.com/Owner/Name.git
        git@github.com:Owner/Name.git
        git@<ssh-alias>:Owner/Name.git      <- 本机用 alias,没有 github.com 这一段
    """
    if not url:
        return None
    u = url.strip().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    # scp 式:host-or-alias:owner/repo
    if "://" not in u and ":" in u:
        u = u.rsplit(":", 1)[-1]
    parts = [p for p in u.replace("\\", "/").split("/") if p]
    if len(parts) < 2:
        return None
    return (parts[-2] + "/" + parts[-1]).lower()


def _web_url(remote: str | None, table: dict) -> str | None:
    """浏览器里打开这个仓的地址,**只在能确定的时候才给**。

    不能从 remote 直接猜。本机的 remote 是 `git@<ssh-alias>:owner/repo.git` 这种形态,
    里面根本没有 `github.com` 这一段,而按「反正大家都用 GitHub」拼出来的链接在一个
    自建 remote 上会把人送到一个不存在的页面 —— 一个指错地方的链接比没有链接更糟,
    因为它看起来是工作的。

    判据用可见性表:那张表是拿 `gh` 对着 GitHub 查出来的,所以一个 `owner/repo` 键
    出现在里面,本身就是「这确实是 GitHub 上的仓」的证据,不是推测。查不到就返回 None,
    页面据此把按钮整个隐掉并说明原因。
    """
    key = _owner_repo(remote)
    if not key or not table:
        return None
    if key in table or any(isinstance(k, str) and k.lower() == key for k in table):
        return "https://github.com/" + key
    return None


def _visibility(remote: str | None, table: dict) -> str | None:
    """这个仓是公开还是私有。查不到就是 None,页面显示「未知」而不是猜 PRIVATE。

    ⚠ 这个函数以前拿仓库的**文件系统路径**去比表里的键,而表的键是 `owner/repo`
    (实测绝大多数是这个形状,含路径分隔符的一条都没有)。
    于是它把 `owner/name` 这种键当成一个相对路径 expanduser + abspath,
    结果永远匹配不上 —— **整个公开/私有视图从来没有工作过**:
    每一行都不显示 PUB/PRI 徽章,而 visibilityReason 是 None,页面一句话都不说。

    屏幕上「表里没登记这几个仓」和「匹配逻辑压根不对」长得一模一样,
    而这个模块自己的注释早就写过这句话:**一个静默变空的可见性视图,
    比没有这个视图更危险** —— 可见性正是判断一个仓能不能装真实数据的依据。
    所以现在除了修匹配,scan() 还会数「表里 N 条、匹配上 M 条」,M 为 0 时明说。
    """
    if not table:
        return None
    key = _owner_repo(remote)
    if not key:
        return None
    v = table.get(key)
    if v is None:
        for k, vv in table.items():
            if isinstance(k, str) and k.lower() == key:
                v = vv
                break
    if v is None:
        return None
    return v if isinstance(v, str) else (v or {}).get("visibility")


def scan_one(repo: Path, vis_table: dict, now: float) -> dict:
    d = {"name": repo.name, "path": str(repo)}
    dot = repo / ".git"
    d["nested"] = dot.is_file()          # submodule 或 worktree

    rc, out = _git(repo, "status", "--porcelain=v2", "--branch")
    if rc != 0:
        return dict(d, state=ERROR, why=out.strip()[:160] or f"git status 退出 {rc}")

    branch, ahead, behind, upstream, dirty = None, None, None, None, 0
    for line in out.splitlines():
        if line.startswith("# branch.head "):
            branch = line.split(" ", 2)[2]
        elif line.startswith("# branch.upstream "):
            upstream = line.split(" ", 2)[2]
        elif line.startswith("# branch.ab "):
            parts = line.split()
            try:
                ahead, behind = int(parts[2]), -int(parts[3])
            except (IndexError, ValueError):
                ahead = behind = None
        elif line and not line.startswith("#"):
            dirty += 1

    rc2, out2 = _git(repo, "log", "-1", "--format=%ct")
    last = None
    if rc2 == 0 and out2.strip().isdigit():
        last = int(out2.strip())

    rc3, out3 = _git(repo, "remote", "get-url", "origin")
    remote = out3.strip() if rc3 == 0 else None

    # 远程跟踪引用上一次刷新是什么时候。
    #
    # ⚠ 这个字段存在的理由:上面那个 behind 来自 `# branch.ab`,而那一行比的是
    # HEAD 和**本地缓存的那份 origin/xxx**,不是真正的远端。扫描过程不 fetch,
    # 所以一个从没 fetch 过的仓,behind 恒为 0 —— 而屏幕上它和「真的已同步」
    # 逐字一样。实测下来这不是边缘情况:一个只推不拉的仓永远不会写 FETCH_HEAD,
    # 而那种仓在任何以推送为主的工作流里都占多数,于是「落后 0」大面积失真。
    #
    # 判据用 FETCH_HEAD 的 mtime:它是 fetch 真的跑过才会被写的那个文件。
    # 不存在 = 从来没 fetch 过,那和「刚 fetch 过」是两件相反的事。
    fetched_at = None
    try:
        fh = repo / ".git" / "FETCH_HEAD"
        if not fh.exists():
            # submodule / worktree 的 .git 是文件不是目录,真正的 git 目录在别处。
            # 问 git 自己,不自己拼路径。
            rc4, out4 = _git(repo, "rev-parse", "--git-dir")
            if rc4 == 0 and out4.strip():
                cand = Path(out4.strip())
                if not cand.is_absolute():
                    cand = repo / cand
                fh = cand / "FETCH_HEAD"
        if fh.is_file():
            fetched_at = fh.stat().st_mtime
    except OSError:
        fetched_at = None

    if branch in (None, "(detached)"):
        state = DETACHED
    elif ahead:
        state = UNPUSHED
    elif dirty:
        state = DIRTY
    else:
        state = CLEAN

    return dict(d, state=state, branch=branch, upstream=upstream,
                # 形状事实。类型和关系在 scan() 里定,因为那需要看到全部仓;
                # 这里只报「我这个目录长什么样」。
                remoteName=_repo_name_from_url(remote),
                # owner 一直被算出来,但从前只喂给可见性查表然后就丢了。面板要回答
                # 「这个仓属于哪个账号」,而那个问题的答案就是它。
                owner=(_owner_repo(remote) or "").split("/")[0] or None,
                hasSkillManifest=_has_skill_manifest(repo),
                usesShared=_submodule_parents(repo),
                ahead=ahead, behind=behind, dirty=dirty, remote=remote,
                fetchedAt=fetched_at,
                fetchAgeHours=(round((now - fetched_at) / 3600.0, 1)
                               if fetched_at else None),
                # behind 可不可信,由这里说了算,不让前端各自去推。
                # ⚠ 没有 upstream 时 behind 本来就是 None(未知),那一档不受这条影响。
                # ⚠ 两边都要是小时。第一版拿 `now - fetched_at`(秒)直接比
                # BEHIND_TRUST_HOURS(24),于是任何超过 24 秒的缓存都被判成过期 ——
                # 于是每一个仓都会显示「落后未知」。这个错的方向是「安全」的,
                # 所以光读代码看不出来;而一个永远在喊的提示等于没有提示,
                # 它还会顺带把真正该看的那几个一起淹掉。
                # 抓到它的是那条负对照用例:刚 fetch 过就必须是可信的。
                behindKnown=(behind is not None and fetched_at is not None
                             and (now - fetched_at) / 3600.0 <= BEHIND_TRUST_HOURS),
                lastCommit=last,
                ageDays=round((now - last) / 86400.0, 1) if last else None,
                visibility=_visibility(remote, vis_table),
                webUrl=_web_url(remote, vis_table),
                # 没有 upstream 时 ahead 是 None,不是 0。页面必须画成「未知」。
                unpushedKnown=ahead is not None)


def _classify(rows: list[dict]) -> None:
    """就地给每个仓定 kind 和关系。只用这次扫描里看得见的事实。

    三条判据互不重叠,优先级 companion > shared > skill > other:
      * origin 名是 `<X>-config` **而且 X 也在这次扫描里** -> X 的伴生仓。
        宿主不在这次扫描里的(比如一个独立的配置备份仓,去掉后缀后并没有那个仓),
        不算伴生 —— 它只是名字长得像。**判据是配对成功,不是名字后缀。**
      * 被别的仓在 .gitmodules 里引用 -> 共享组件。
      * 有 SKILL.md -> skill 仓。

    `companionInScan` 刻意只说「这次扫描里」:一个仓的伴生仓完全可以在扫描根之外
    (真实存在的形态),那时这里是 False,而它**不表示缺口**。
    缺口由 data_boundary 判,这里不抢那个答案。
    """
    by_name: dict[str, dict] = {}
    for r in rows:
        for key in (r.get("remoteName"), r.get("name")):
            if key:
                by_name.setdefault(key, r)

    shared: set[str] = set()
    used_by: dict[str, int] = {}
    for r in rows:
        for parent in r.get("usesShared") or []:
            if parent in by_name:
                shared.add(parent)
                used_by[parent] = used_by.get(parent, 0) + 1

    for r in rows:
        rn = r.get("remoteName") or r.get("name") or ""
        host = None
        if rn.endswith("-config"):
            cand = rn[: -len("-config")]
            if cand in by_name and by_name[cand] is not r:
                host = by_name[cand]

        if host is not None:
            r["kind"] = KIND_COMPANION
            r["companionOf"] = host.get("name")
            host["companionInScan"] = r.get("name")
        elif rn in shared:
            r["kind"] = KIND_SHARED
        elif r.get("hasSkillManifest"):
            r["kind"] = KIND_SKILL
        else:
            r["kind"] = KIND_OTHER

    for r in rows:
        r.setdefault("companionInScan", None)
        # 共享组件被多少个仓用。这是关系的另一半 : 「谁被谁引用」在页面上原来只有
        # 一个类型标签在说,而「被 25 个仓用」和「被 1 个仓用」是完全不同的两件东西 ——
        # 前者改一行会波及整片,后者基本是私事。
        rn = r.get("remoteName") or r.get("name")
        r["usedBy"] = used_by.get(rn, 0) if r.get("kind") == KIND_SHARED else None


def _vis_match_reason(rows: list[dict], table: dict) -> str | None:
    """可见性表明明读到了,却一条都没匹配上 —— 说出来。

    没有这句话时,「表里没登记这几个仓」和「匹配逻辑坏了」在屏幕上是同一个样子:
    每一行都没有徽章,而且没有任何原因。前者是配置问题,后者是代码 bug,
    要做的事完全不同。
    """
    if not table:
        return None
    entries = sum(1 for k in table if isinstance(k, str) and not k.startswith("_"))
    if not entries or not rows:
        return None
    matched = sum(1 for r in rows if r.get("visibility"))
    if matched:
        return None
    return (f"可见性表读到了 {entries} 条,却和这 {len(rows)} 个仓一条都没对上 —— "
            f"这不是「没登记」,是对不上(remote 取不到,或者表的键换了形状)。"
            f"所有仓的公开/私有标记因此都不显示。")


def _identity_counts(rows: list[dict]) -> dict:
    """每种账号判定各有几个仓。

    页面用它做过滤器的角标,也用它回答「有没有配错的」这个问题而不必逐行看。
    刻意把四态都算出来,包括 unchecked:一个只数 mismatch 的计数器在没配身份表时是 0,
    而 0 在那里读起来像「没有配错的」。
    """
    out: dict[str, int] = {}
    for r in rows:
        st = (r.get("identity") or {}).get("state")
        if st:
            out[st] = out.get(st, 0) + 1
    return out


def _group_counts(rows: list[dict]) -> dict:
    """每个组标题下实际显示多少个仓。伴生仓归到它宿主所在的组。

    不变量:各组之和 == 仓总数。tests/test_repos.py 钉住了这一条。
    """
    host_kind = {r.get("name"): r.get("kind") for r in rows}
    counts = {k: 0 for k in KIND_ORDER}
    for r in rows:
        k = r.get("kind")
        if k == KIND_COMPANION:
            k = host_kind.get(r.get("companionOf")) or KIND_OTHER
        counts[k] = counts.get(k, 0) + 1
    return counts


def _load_visibility() -> tuple[dict, str | None]:
    """可见性表,外加「为什么没有」。

    原来解析失败被吞成空表,于是所有仓的 PUB/PRI 标记一起消失,而那和「表里没登记这几个仓」
    长得一模一样。在这套体系里可见性正是判断一个仓能不能装真实数据的依据,
    **一个静默变空的可见性视图,比没有这个视图更危险**。
    自检只能证明这个文件存在且非空,证明不了它解析得出来。
    """
    p = os.environ.get("TASK_CONSOLE_VISIBILITY")
    if not p:
        return {}, None                      # 没配 = 没启用,不是故障
    try:
        return json.loads(Path(os.path.expanduser(p)).read_text(encoding="utf-8-sig")), None
    except OSError as e:
        return {}, f"可见性表读不到({e.__class__.__name__}),所有仓的公开/私有标记都不显示"
    except ValueError as e:
        return {}, f"可见性表解析失败({e.__class__.__name__}),所有仓的公开/私有标记都不显示"


def scan(root: str | None = None, now: float | None = None, workers: int = 10) -> dict:
    """扫一整片仓。串行扫一整片仓要几十秒,所以并发;每个仓自己带超时,一个卡住的仓
    不能让整块面板转圈。"""
    now = time.time() if now is None else now
    raw = root if root is not None else os.environ.get("TASK_CONSOLE_REPOS")
    if not raw:
        return {"available": False,
                "reason": "没有设 TASK_CONSOLE_REPOS,仓库这一栏是「未检查」。"}
    base = Path(os.path.expanduser(raw))
    if not base.is_dir():
        return {"available": False, "reason": f"仓库根目录不存在: {base}"}

    repos = [d for d in sorted(base.iterdir())
             if d.is_dir() and (d / ".git").exists()]
    if not repos:
        # summary 的形状必须和正常分支一致。少给几个键不会报错,只会让前端把
        # `undefined` 拼进副标题(实测:「无上游 undefined」)——
        # 一个 undefined 印在屏幕上比一个说不出来的空更糟,因为它看起来像一个值。
        return {"available": True, "root": str(base), "repos": [],
                "summary": {"total": 0, "counts": {}, "attention": 0,
                            "unknownUpstream": 0,
                            # 没有仓时这三个都是 0,但键必须在 —— 见上面那段注释,
                            # 缺一个键的后果是前端把 undefined 拼进副标题。
                            "staleBehind": 0,
                            "neverFetched": 0,
                            "behindTrustHours": BEHIND_TRUST_HOURS,
                            "attentionStates": ["unpushed", "dirty", "error"],
                            "kinds": {k: 0 for k in (KIND_SKILL, KIND_SHARED,
                                                     KIND_COMPANION, KIND_OTHER)},
                            "kindOrder": list(KIND_ORDER),
                            "visibilityReason": None,
                            "identityReason": None,
                            "identityCounts": {}},
                "note": "这个根目录下没有 git 仓"}

    vis, vis_reason = _load_visibility()
    # 身份表和可见性表同一个形态:仓外文件、走环境变量、读不到时带着原因一起下发。
    # 读不到绝不能静默:那会让每一行都显示「未检查」而页面说不出为什么。
    import identity as _ident
    idt, idt_reason = _ident.load_table()
    out = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(scan_one, r, vis, now): r for r in repos}
        for f in cf.as_completed(futs):
            r = futs[f]
            try:
                out.append(f.result())
            except Exception as e:
                # 扫不动的仓要出现在列表里并且是红的。悄悄跳过等于把它算成没问题。
                out.append({"name": r.name, "path": str(r), "state": ERROR,
                            "why": f"{e.__class__.__name__}: {e}"})

    # 账号归属。放在分类之后、排序之前,因为它按仓逐个跑 git,而扫描那一步已经并发过了;
    # 这里再开一层并发只会和上面那批 git 抢同一批句柄。
    for _r in out:
        if _r.get("state") == ERROR:
            continue
        try:
            _r["identity"] = _ident.judge(Path(_r["path"]), _r.get("owner"), idt, idt_reason)
        except Exception as e:
            # 判定不了要说出来,不能让这一栏空着 —— 空着和「对得上」在屏幕上一样。
            _r["identity"] = {"state": _ident.UNCHECKED, "owner": _r.get("owner"),
                              "expect": None, "scope": None,
                              "why": "判定失败(%s)" % e.__class__.__name__}

    _classify(out)
    out.sort(key=lambda x: (-_SEV.get(x["state"], 0), x["name"]))
    counts: dict[str, int] = {}
    for x in out:
        counts[x["state"]] = counts.get(x["state"], 0) + 1
    return {
        "available": True, "root": str(base), "repos": out,
        "summary": {
            "total": len(out),
            "counts": counts,
            # 要人管的:没推的、脏的、扫不动的。detached 单独看,它常常是刻意的。
            "attention": counts.get(UNPUSHED, 0) + counts.get(DIRTY, 0) + counts.get(ERROR, 0),
            # 这个集合必须显式说出来,因为页面上有三处要用它。之前前端自己写了
            # `state !== "clean"`,于是一个游离 HEAD 的仓会让指标格显示绿色的 0、
            # 侧栏徽章不亮,而正下方的清单里有一行「仓库 · 游离 HEAD」 :
            # 后端明确决定「detached 不算要人管」,前端把这个决定推翻了一半。
            # 判定只能留一份,而这一份在这里。
            "attentionStates": [UNPUSHED, DIRTY, ERROR],
            # 可见性表读不出来时要说出来。原来它被吞成空表,于是所有仓的公开/私有标记
            # 一起消失,而那和「表里没登记这几个仓」长得一模一样。
            # 「读到了但一条都没匹配上」同样要说 —— 那是匹配逻辑坏了,不是没登记,
            # 而这两件事在屏幕上原本长得一样(都是没有徽章、没有原因)。
            "visibilityReason": vis_reason or _vis_match_reason(out, vis),
            # 和上面同一个道理:身份表读不出来时,页面要说得出为什么整栏是「未检查」。
            "identityReason": idt_reason,
            "identityCounts": _identity_counts(out),
            "unknownUpstream": sum(1 for x in out if x.get("unpushedKnown") is False),
            # 有多少个仓的「落后」其实不知道。这个数必须出现在汇总里,
            # 因为它说的是**整块面板有多少内容不可信**,而那不是某一行自己的事。
            # 从来没 fetch 过的单独数:它和「fetch 过但旧了」要做的事一样,
            # 但严重程度不同,合并之后就看不出有一批仓从来没连过远端。
            "staleBehind": sum(1 for x in out
                               if x.get("behind") is not None
                               and not x.get("behindKnown")),
            "neverFetched": sum(1 for x in out if x.get("fetchedAt") is None),
            "behindTrustHours": BEHIND_TRUST_HOURS,
            # 每组实际会显示多少个仓。页面用它画分组标题,而不是自己再数一遍 ——
            # 前端数一遍就是同一个事实的第二个来源。
            #
            # ⚠ 伴生仓算进**它宿主所在的那个组**,因为它就画在那里。
            # 按 kind 直接数的话,三个组标题加起来会比「共 N」少掉伴生仓的数目,
            # 而屏幕上没有任何一处解释那个差 —— 两个都自称权威的数字,
            # 读的人只能自己去猜哪个漏了什么。加起来等于总数,就不需要解释。
            "kinds": _group_counts(out),
            "kindOrder": list(KIND_ORDER),
        },
    }


def fetch(name: str) -> dict:
    """对一个仓跑 fetch。只读网络动作,不改工作树,不推任何东西。"""
    from maint import Refused, SAFE_NAME, _child   # 复用同一道参数闸,不另造一个
    raw = os.environ.get("TASK_CONSOLE_REPOS")
    if not raw:
        raise Refused("没有配 TASK_CONSOLE_REPOS", "no_config")
    if not SAFE_NAME.match(name or ""):
        raise Refused(f"仓名不合法: {name!r}", "bad_name")
    repo = _child(Path(os.path.expanduser(raw)), name)
    if not (repo / ".git").exists():
        raise Refused(f"不是 git 仓: {name}", "missing_src")
    rc, out = _git(repo, "fetch", "--prune", timeout=120)
    if rc != 0:
        raise Refused(f"fetch 退出 {rc}: {out.strip()[:200]}", "fetch_failed")
    return {"ok": True, "out": out.strip()[:400]}


def reveal(name: str) -> dict:
    """在资源管理器里定位到这个仓。

    只接受**仓名**,路径由这里解析 —— 一个接受路径的接口迟早会被喂进另一条路径,
    而这个接口会把参数交给外壳。复用 maint 那道参数闸,不另造一个。
    """
    from maint import Refused, SAFE_NAME, _child
    raw = os.environ.get("TASK_CONSOLE_REPOS")
    if not raw:
        raise Refused("没有配 TASK_CONSOLE_REPOS", "no_config")
    if not SAFE_NAME.match(name or ""):
        raise Refused(f"仓名不合法: {name!r}", "bad_name")
    repo = _child(Path(os.path.expanduser(raw)), name)
    if not repo.is_dir():
        raise Refused(f"目录不存在: {name}", "missing_src")
    if os.name != "nt":
        raise Refused("只在 Windows 上支持", "unsupported")
    # 不经 shell。explorer 的参数是一个已经解析好的绝对路径,不是拼出来的命令行。
    subprocess.Popen(["explorer", str(repo)], stdin=subprocess.DEVNULL,
                     creationflags=_NO_WINDOW)
    return {"ok": True, "opened": str(repo)}


def status(name: str) -> dict:
    """这个仓现在到底哪里脏了。

    面板上那个 `~12` 只说得出「有 12 个」,说不出是哪 12 个,而「别的自动化正在里面干活」
    和「我自己改了没提交」要靠文件名才分得开。只读。
    """
    from maint import Refused, SAFE_NAME, _child
    raw = os.environ.get("TASK_CONSOLE_REPOS")
    if not raw:
        raise Refused("没有配 TASK_CONSOLE_REPOS", "no_config")
    if not SAFE_NAME.match(name or ""):
        raise Refused(f"仓名不合法: {name!r}", "bad_name")
    repo = _child(Path(os.path.expanduser(raw)), name)
    if not (repo / ".git").exists():
        raise Refused(f"不是 git 仓: {name}", "missing_src")
    rc, out = _git(repo, "status", "--porcelain=v1", "-b", timeout=30)
    if rc != 0:
        raise Refused(f"git status 退出 {rc}: {out.strip()[:200]}", "status_failed")
    lines = [ln for ln in out.splitlines() if ln.strip()]
    branchline = lines[0] if lines and lines[0].startswith("##") else None
    files = [ln for ln in lines if not ln.startswith("##")]
    # 条数单独给:前端截断显示时,「只显示了前 N 条」和「一共就这么多」必须分得开。
    return {"ok": True, "branch": branchline, "count": len(files),
            "files": files[:200], "truncated": len(files) > 200}


# ---------------------------------------------------------------------------------------
# 提交并推送。这是这块面板上**唯一一个把东西送出这台机器**的动作。
#
# ⚠ 这个文件上面写过「push 永远不进动作表:它是对外动作,撤不回来,而一个能一键推送的
# 按钮迟早会在没人看的时候被点到」。那句话没有错,错的是把它读成「所以永远别做」。
# 真正要防的是**一次误击就把东西发出去**,而不是「人明确决定之后还要手工敲六条命令」。
# 所以它是两步的:先出一份只读计划,把要提交哪些文件、要推到哪个 ref、那个 remote 是
# 公开还是私有全部摆出来;执行那一步必须带着计划里那份文件清单回来,清单对不上就整个拒绝。
# 一次误击只会打开一份计划。
#
# 为什么值得做:伴生仓按设计天天在长数据,于是「有未提交改动」长期挂着十几条。
# 那十几条每一条的处理方式逐字相同,而它们占着「要人管的事」清单里最大的一块 ——
# 一张清单如果长期有一半是同一件琐事,人就会开始整张不看,连同真正要紧的那几条一起。

# 提交信息的字符闸。换行会让 `-m` 之后的内容变成另一段,反引号和 $ 在任何一层
# 被交给 shell 时都会求值 —— 这里不经 shell,但一个能塞进任意字节的提交信息
# 迟早会被别处读出来再执行。
_MSG_BAD = set('\r\n\x00`$')
_MSG_MAX = 200


def _repo_for(name: str):
    """把仓名解析成路径,并过同一道参数闸。三个动作共用,不各写一份。"""
    from maint import Refused, SAFE_NAME, _child
    raw = os.environ.get("TASK_CONSOLE_REPOS")
    if not raw:
        raise Refused("没有配 TASK_CONSOLE_REPOS", "no_config")
    if not SAFE_NAME.match(name or ""):
        raise Refused(f"仓名不合法: {name!r}", "bad_name")
    repo = _child(Path(os.path.expanduser(raw)), name)
    if not (repo / ".git").exists():
        raise Refused(f"不是 git 仓: {name}", "missing_src")
    return repo


def _porcelain_paths(repo: Path) -> tuple[list[str], list[str]]:
    """返回 (可提交的路径, 跳过的原文行)。

    用 `-z` 而不是按行切:文件名里可以有空格、引号、甚至换行,而按行切会把一个
    带换行的文件名读成两条记录,然后把其中半条当成路径传给 `git add`。
    """
    rc, out = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", timeout=60)
    if rc != 0:
        from maint import Refused
        raise Refused(f"git status 退出 {rc}: {out.strip()[:200]}", "status_failed")
    paths: list[str] = []
    skipped: list[str] = []
    parts = out.split("\x00")
    i = 0
    while i < len(parts):
        rec = parts[i]
        i += 1
        if not rec:
            continue
        code, _, path = rec[:2], rec[2:3], rec[3:]
        if not path:
            continue
        if code[0] == "R" or code[0] == "C":
            # 重命名/复制在 -z 下多占一条记录(原名紧跟其后)。两个名字都要提交,
            # 否则会留下一半的重命名。
            if i < len(parts):
                old = parts[i]
                i += 1
                if old:
                    paths.append(old)
        if code == "!!":
            skipped.append(rec)
            continue
        paths.append(path)
    return paths, skipped


def commit_push_plan(name: str) -> dict:
    """只读。把「点下去会发生什么」全部摆出来,一个字节都不写。"""
    repo = _repo_for(name)
    rc, br = _git(repo, "rev-parse", "--abbrev-ref", "HEAD", timeout=20)
    branch = br.strip() if rc == 0 else None
    rc, up = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", timeout=20)
    upstream = up.strip() if rc == 0 else None

    paths, skipped = _porcelain_paths(repo)

    ahead = []
    if upstream:
        rc, log = _git(repo, "log", "--oneline", "--no-decorate", f"{upstream}..HEAD", timeout=30)
        if rc == 0:
            ahead = [ln for ln in log.splitlines() if ln.strip()][:50]

    rc, rem = _git(repo, "remote", "get-url", "origin", timeout=20)
    remote = rem.strip() if rc == 0 else None
    # ⚠ _visibility 收的是 remote URL,不是 slug —— 它自己再算 owner/repo。
    # 传 slug 进去会让它二次解析、永远匹配不上,而那正是这个视图历史上
    # 「从来没有工作过」的原因,症状是每一行都安静地显示「未知」。
    vis = None
    try:
        vis = _visibility(remote, _load_visibility()[0]) if remote else None
    except OSError:
        vis = None

    # 拦下来的理由各自单独说。合并成一句「不能推」会让人不知道该去修哪一个。
    blocked = []
    if not branch or branch == "HEAD":
        blocked.append("现在是游离 HEAD,没有分支可推")
    if not upstream:
        blocked.append("没有 upstream。第一次推要人指定推到哪里,这个面板不替你选")
    if not remote:
        blocked.append("没有 origin")
    # ⚠ 公开仓不在这里放行也不在这里拦死:拦死会让一个合法的公开仓改不动,
    # 放行则等于替闸门做决定。把它摆出来,并说清接下来谁会检查。
    warn = []
    if vis and "pub" in str(vis).lower():
        warn.append("这是一个**公开**仓。真实运行产出绝不能进公开仓;"
                    "提交与推送都会过 pii_guard 与数据边界闸,由它们裁决。")
    if vis is None:
        warn.append("可见性问不出来。闸门对未知 remote 是按公开拦的,这里照样提醒。")
    if skipped:
        warn.append(f"{len(skipped)} 条被 .gitignore 忽略的路径不会提交。")

    return {"ok": True, "repo": name, "branch": branch, "upstream": upstream,
            "remote": remote, "visibility": vis,
            "files": paths[:200], "fileCount": len(paths), "filesTruncated": len(paths) > 200,
            "ahead": ahead, "aheadCount": len(ahead),
            "blocked": blocked, "warn": warn,
            "nothing": not paths and not ahead}


def commit_push(name: str, message: str, expect: list[str] | None = None,
                push: bool = True) -> dict:
    """执行。必须带着计划里那份文件清单回来。

    ⚠ 不用 `git add -A`。共享工作树里 -A 会捡走别的自动化做到一半的改动,
    而那种提交事后没人分得清是谁的。只 add 计划里逐条列出来的路径。
    ⚠ 不用 --no-verify,一次都不。钩子的输出原样回传 ——
    一个把闸门输出吞掉的按钮,和一个绕过闸门的按钮,后果一样。
    """
    from maint import Refused
    repo = _repo_for(name)
    msg = (message or "").strip()
    if not msg:
        raise Refused("提交信息不能为空", "bad_message")
    if len(msg) > _MSG_MAX:
        raise Refused(f"提交信息太长(上限 {_MSG_MAX})", "bad_message")
    if set(msg) & _MSG_BAD:
        raise Refused("提交信息里有不允许的字符(换行 / 反引号 / $)", "bad_message")

    plan_paths, _ = _porcelain_paths(repo)
    out: list[str] = []

    if plan_paths:
        if expect is None:
            raise Refused("没有带上计划里的文件清单,拒绝提交", "no_plan")
        # 钉住读到的那一版。工作树在你看计划和点确认之间被别的自动化改过时,
        # 这里必须整个拒绝而不是「顺手把新出现的也提交了」——
        # 这个仓已经因为「长任务中途工作树被另一自动化改掉」出过一次事。
        if sorted(expect) != sorted(plan_paths):
            added = sorted(set(plan_paths) - set(expect))
            gone = sorted(set(expect) - set(plan_paths))
            raise Refused(
                "工作树在你看计划之后变了,拒绝提交。重新出一份计划再确认。"
                + (f" 新增: {', '.join(added[:5])}" if added else "")
                + (f" 消失: {', '.join(gone[:5])}" if gone else ""),
                "plan_stale")
        rc, o = _git(repo, "add", "--", *plan_paths, timeout=120)
        out.append(o)
        if rc != 0:
            raise Refused(f"git add 退出 {rc}: {o.strip()[:300]}", "add_failed")
        rc, o = _git(repo, "commit", "-m", msg, timeout=300)
        out.append(o)
        if rc != 0:
            # 钩子挡下来是**正常结果**,不是这个按钮坏了。原文回传,一个字不删。
            raise Refused(f"git commit 退出 {rc}(钩子可能拦下了):\n{o.strip()[:2000]}",
                          "commit_failed")

    pushed = False
    if push:
        rc, o = _git(repo, "push", timeout=600)
        out.append(o)
        if rc != 0:
            raise Refused(f"git push 退出 {rc}:\n{o.strip()[:2000]}", "push_failed")
        pushed = True

    return {"ok": True, "repo": name, "committed": bool(plan_paths),
            "fileCount": len(plan_paths), "pushed": pushed,
            "out": "\n".join(x for x in out if x).strip()[:4000]}
