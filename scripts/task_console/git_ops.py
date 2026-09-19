"""Git snapshots and publication, independent of UI and authorization policy.

Callers own repository access, approval, identity/credential environment, message
policy and durable receipts. Snapshots leave the worktree, real index and refs
unchanged; constructing the reviewed tree can add unreachable Git objects.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile


def sanitize_diagnostic(text: str) -> str:
    """Return displayable diagnostics, never a partially redacted credential.

    Review bytes bypass this boundary. Raw child output stays local for exit/ref
    classification; errors and receipts pass here before callers can retain them.
    Omit the entire diagnostic when unsafe, including unlabelled repetitions of a
    detected value. The shared scanner owns credential shape detection.
    """
    if not text:
        return ""
    try:
        from fleet_guards.secrets import scan

        result = scan(text)
        if not isinstance(result, dict) or result.get("state") not in {"clean", "findings"}:
            return "[Git diagnostic omitted: secret scan unavailable]"
        if result["state"] == "findings":
            return "[Git diagnostic omitted: credential-bearing output]"
        if result.get("findings") != []:
            return "[Git diagnostic omitted: secret scan unavailable]"
    except Exception:
        # A missing or broken scanner cannot authorize disclosure of its input.
        return "[Git diagnostic omitted: secret scan unavailable]"
    # Conservatively omit URI userinfo and all authorization schemes, including
    # username-only tokens and Basic headers outside the scanner's shape policy.
    if ("://" in text and "@" in text) or "authorization" in text.casefold():
        return "[Git diagnostic omitted: credential-bearing output]"
    return text


class GitError(RuntimeError):
    def __init__(self, message: str, code: str = "git_failed"):
        super().__init__(sanitize_diagnostic(message))
        self.code = code


_COMMANDS = {"status", "log", "remote", "rev-parse", "rev-list", "symbolic-ref", "config",
             "ls-files", "ls-tree", "diff", "diff-tree", "read-tree", "write-tree", "add",
             "commit", "push", "fetch", "ls-remote", "check-ref-format", "merge-base",
             "for-each-ref"}
_TOPOLOGY_ENV = {"GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                 "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                 "GIT_SHALLOW_FILE", "GIT_GRAFT_FILE", "GIT_REPLACE_REF_BASE"}
_STATE_KEYS = ("head", "branch", "index", "status", "content", "changed_paths", "gitlinks")


def _caller_env(env):
    if any(key.upper() in _TOPOLOGY_ENV for key in (env or {})):
        raise GitError("Caller environment cannot redirect repository storage", "bad_environment")
    return dict(env or {})


def run_git(repo: Path, *args: str, timeout: int = 20, env=None) -> tuple[int | None, str]:
    """Trusted argv adapter; None means interruption, not a confirmed Git exit.

    Mutating commands and transports use installed llmcall.process containment.
    No model is called. Ordinary local reads retain a simple subprocess runner.
    """
    if not args or args[0] not in _COMMANDS or any("\x00" in arg for arg in args):
        raise GitError("Unsupported Git command", "bad_command")
    values = {key: value for key, value in os.environ.items() if key.upper() not in _TOPOLOGY_ENV}
    values.update(env or {})
    values.update(GIT_OPTIONAL_LOCKS="0", GIT_LITERAL_PATHSPECS="1", GIT_TERMINAL_PROMPT="0",
                  GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1")
    cmd = ("git", "-C", str(repo), *args)
    if args[0] in {"add", "commit", "push", "fetch", "ls-remote"}:
        from llmcall import process
        result = process.run(cmd, "", timeout, context=process.CallContext(str(repo), values))
        output = (result.stdout or "") + (result.stderr or "")
        # A confirmed ls-remote exit 2 with empty streams means "ref absent".
        # Do not replace Git's empty output with a synthetic "exit 2" message.
        return result.returncode, output if result.returncode is not None else (output or result.error or "")
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout, env=values,
                                stdin=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        output = result.stdout.decode("utf-8")
        if result.returncode:
            output += result.stderr.decode("utf-8", errors="replace")
        return result.returncode, output
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _checked(repo, *args, runner, env=None, timeout=30):
    rc, out = runner(repo, *args, timeout=timeout, env=env)
    if rc != 0:
        raise GitError(f"git {args[0]} failed: {out}")
    return out


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _json_digest(value):
    return _digest(json.dumps(value, sort_keys=True, ensure_ascii=True).encode())


def _path_list(paths):
    if not isinstance(paths, (list, tuple)):
        raise GitError("Explicit allowed paths are required", "bad_paths")
    for path in paths:
        if (not isinstance(path, str) or not path or "\\" in path or ":" in path
                or "\x00" in path or "\ufffd" in path or path.startswith("/")
                or any(part in {"", ".", ".."} or part.lower() == ".git" for part in path.split("/"))):
            raise GitError("Only literal repository-relative leaf paths are allowed", "bad_paths")
    return sorted(set(paths))


def _git_path(repo, name, runner, env):
    path = Path(_checked(repo, "rev-parse", "--git-path", name, runner=runner, env=env).strip())
    return path if path.is_absolute() else repo / path


def _index_bytes(repo, runner, env):
    path = _git_path(repo, "index", runner, env)
    return path.read_bytes() if path.exists() else b""


def _repository_root(repo, runner, env):
    repo = Path(repo).resolve()
    actual = _checked(repo, "rev-parse", "--show-toplevel", runner=runner, env=env).strip()
    if Path(actual).resolve() != repo:
        raise GitError("Expected the repository root, not a nested directory", "bad_repo")
    return repo


def status_paths(repo, *, runner=run_git, env=None):
    """NUL-delimited, rename-disabled status keeps every path literal."""
    out = _checked(repo, "status", "--porcelain=v1", "-z", "--no-renames",
                   "--untracked-files=all", "--ignore-submodules=none",
                   runner=runner, env=env, timeout=60)
    records = [record for record in out.split("\x00") if record]
    if any(len(record) < 4 or record[2] != " " for record in records):
        raise GitError("Unrecognized Git status", "status_failed")
    if any("U" in record[:2] or record[:2] in {"AA", "DD"} for record in records):
        raise GitError("Unmerged index cannot be published", "conflict")
    return _path_list([record[3:] for record in records]), out


def _file_evidence(repo, path):
    relative = PurePosixPath(path)
    full = repo.joinpath(*relative.parts)
    for parent in full.parents:
        if parent == repo:
            break
        if parent.is_symlink() or parent.is_junction():
            raise GitError("Reparse parent in reviewed paths", "bad_paths")
    try:
        info = full.lstat()
    except FileNotFoundError:
        return [path, "missing"]
    if stat.S_ISLNK(info.st_mode):
        return [path, "symlink", os.readlink(full)]
    if stat.S_ISDIR(info.st_mode):
        return [path, "directory"]
    if not stat.S_ISREG(info.st_mode):
        raise GitError("Unsupported file type", "bad_paths")
    with full.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return [path, stat.S_IMODE(info.st_mode), digest]


def _gitlink_paths(repo, head, runner, env):
    # HEAD finds staged deletions/type changes; the index finds new gitlinks.
    # Filesystem directory checks cannot identify an absent submodule.
    index = _checked(repo, "ls-files", "--stage", "-z", runner=runner, env=env)
    tree = _checked(repo, "ls-tree", "-r", "--full-tree", "-z", head, runner=runner, env=env)
    return _path_list([record.split("\t", 1)[1] for record in (index + tree).split("\x00")
                       if record.startswith("160000 ")])


def _reject_gitlinks(paths, gitlinks):
    if any(path == link or path.startswith(link + "/")
           for path in paths for link in gitlinks):
        raise GitError("Submodule changes are unsupported; preserve and handle them separately",
                       "unsupported_submodule")


def _state(repo, runner, env):
    head = _checked(repo, "rev-parse", "--verify", "HEAD", runner=runner, env=env).strip()
    branch = _checked(repo, "rev-parse", "--abbrev-ref", "HEAD", runner=runner, env=env).strip()
    index = _digest(_index_bytes(repo, runner, env))
    paths, status = status_paths(repo, runner=runner, env=env)
    gitlinks = _gitlink_paths(repo, head, runner, env)
    _reject_gitlinks(paths, gitlinks)
    listed = _checked(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                      runner=runner, env=env)
    files = _path_list([path for path in listed.split("\x00") if path])
    content = _json_digest([_file_evidence(repo, path) for path in files])
    return {"head": head, "branch": branch, "index": index, "status": status,
            "content": content, "changed_paths": paths, "gitlinks": gitlinks}


@contextmanager
def _temporary_index(repo, runner, env, initial=None):
    directory = _git_path(repo, "index", runner, env).parent
    fd, name = tempfile.mkstemp(prefix="publish-index-", dir=directory)
    os.close(fd)
    path = Path(name)
    if initial:
        path.write_bytes(initial)
    else:
        path.unlink()
    try:
        yield path, dict(env or {}, GIT_INDEX_FILE=str(path))
    finally:
        path.unlink(missing_ok=True)
        Path(str(path) + ".lock").unlink(missing_ok=True)


def _scope(repo, paths, changed, gitlinks):
    _reject_gitlinks(paths, gitlinks)
    unexpected = sorted(set(changed) - set(paths))
    if unexpected:
        raise GitError("Changes outside allowed paths: " + ", ".join(unexpected[:5]), "plan_stale")
    if any((repo / path).is_dir() for path in paths):
        raise GitError("Directory and submodule changes need a separate publisher", "bad_paths")


def snapshot(repo, allowed_paths, *, runner=run_git, env=None):
    """Capture review evidence and the exact prospective commit tree.

    Includes raw worktree bytes, real index bytes, HEAD and all visible changes.
    Changed gitlinks and dirty paths outside the explicit allowed set are refused.
    """
    env = _caller_env(env)
    repo = _repository_root(repo, runner, env)
    paths = _path_list(allowed_paths)
    before = _state(repo, runner, env)
    _scope(repo, paths, before["changed_paths"], before["gitlinks"])
    head_tree = _checked(repo, "rev-parse", "HEAD^{tree}", runner=runner, env=env).strip()
    with _temporary_index(repo, runner, env, _index_bytes(repo, runner, env)) as (_, stage_env):
        if not _index_bytes(repo, runner, env):
            _checked(repo, "read-tree", before["head"], runner=runner, env=stage_env)
        index_tree = _checked(repo, "write-tree", runner=runner, env=stage_env).strip()
        if paths:
            _checked(repo, "add", "--", *paths, runner=runner, env=stage_env, timeout=120)
        tree = _checked(repo, "write-tree", runner=runner, env=stage_env).strip()
    if _state(repo, runner, env) != before:
        raise GitError("Repository changed while preparing review", "plan_stale")
    return {"schemaVersion": 1, "repo": str(repo), **before, "paths": paths,
            "head_tree": head_tree, "index_tree": index_tree, "tree": tree}


def _receipt(state, oid, tree, output="", **extra):
    if "error" in extra:
        extra["error"] = sanitize_diagnostic(extra["error"])
    return {"state": state, "commit": oid, "tree": tree, "out": sanitize_diagnostic(output),
            "pushed": state == "pushed", **extra}


def _exact_oid(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value)


def _observe_target(repo, target, runner, env):
    """An exact ref advertisement, never a tracking-ref or hidden fetch."""
    rc, output = runner(repo, "ls-remote", "--refs", "--exit-code", "--",
                        target["url"], target["ref"], timeout=30, env=env)
    if rc == 2 and not output.strip():
        return None
    fields = output.strip().split()
    if rc == 0 and len(fields) == 2 and _exact_oid(fields[0]) and fields[1] == target["ref"]:
        return fields[0]
    raise GitError("Cannot observe exact push ref: " + output, "remote_unavailable")


def _review_at_base(repo, head, target, base, max_commits, max_bytes, runner, env):
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
        raise GitError("Exact reviewed HEAD required", "no_plan")
    shallow = _checked(repo, "rev-parse", "--is-shallow-repository", runner=runner, env=env).strip()
    replaced = _checked(repo, "for-each-ref", "--format=%(refname)", "refs/replace/",
                        runner=runner, env=env).strip()
    if shallow != "false" or replaced or _git_path(repo, "info/grafts", runner, env).exists():
        raise GitError("Complete history unavailable in a shallow, replaced or grafted repository",
                       "history_unavailable")
    if base is not None:
        if not _exact_oid(base):
            raise GitError("Invalid observed base", "no_plan")
        rc, value = runner(repo, "rev-parse", "--verify", base + "^{commit}", timeout=30, env=env)
        if rc != 0 or value.strip() != base:
            raise GitError("Exact push base is missing locally; explicitly refresh from the selected "
                           "push URL/ref and request a new review. " + value, "refresh_required")
        rc, value = runner(repo, "merge-base", "--is-ancestor", base, head, timeout=30, env=env)
        if rc != 0:
            raise GitError("Push base is not a proven ancestor of reviewed HEAD; reconcile locally "
                           "and request a new review. " + value, "non_fast_forward")
    revision = f"{base}..{head}" if base else head
    raw_count = _checked(repo, "rev-list", "--count", revision, "--", runner=runner, env=env).strip()
    if not raw_count.isdigit():
        raise GitError("Invalid history count", "history_unavailable")
    count = int(raw_count)
    listed = _checked(repo, "log", f"--max-count={max_commits}", "--format=%H %s",
                      "--no-decorate", "--no-color", "--no-show-signature", revision, "--",
                      runner=runner, env=env).splitlines()
    commits = [line.split(" ", 1)[0] for line in listed]
    if len(commits) != min(count, max_commits) or not all(_exact_oid(oid) for oid in commits):
        raise GitError("Incomplete outgoing commit list", "history_unavailable")
    diff = _checked(repo, "log", f"--max-count={max_commits}", "--format=fuller",
                    "--no-decorate", "--no-notes", "--no-color", "--no-show-signature",
                    "--date=iso-strict", "--root", "--diff-merges=separate", "--binary", "--full-index",
                    "--ignore-submodules=none", "--submodule=short",
                    "--no-renames", "--no-ext-diff", "--no-textconv", revision, "--",
                    runner=runner, env=env)
    encoded = diff.encode("utf-8")
    truncated = count > max_commits or len(encoded) > max_bytes
    return {"head": head, "target": target, "base": base,
            "scope": "exact_remote_outgoing" if base else "new_ref_ancestry", "count": count,
            "commits": commits, "summaries": listed,
            "truncated": truncated,
            "digest": _json_digest([target, base, commits, _digest(encoded)]),
            "diff": encoded[:max_bytes].decode("utf-8", errors="ignore")}


def review_history(repo, head, *, target, max_commits=1000, max_bytes=128000,
                   runner=run_git, env=None):
    """Review complete outgoing history against an observed exact push ref.

    Only a verified local ancestor may bound the review. A missing advertised
    base requires an explicit refresh; absence of the remote ref requires root
    ancestry review. No fetch or tracking-ref assumption is made.
    """
    env = _caller_env(env)
    repo = Path(repo).resolve()
    if push_target(repo, runner=runner, env=env) != target:
        raise GitError("Push target changed", "plan_stale")
    base = _observe_target(repo, target, runner, env)
    return _review_at_base(repo, head, target, base, max_commits, max_bytes, runner, env)


def verify_history(repo, history, target, *, runner=run_git, env=None, observe=True):
    """Revalidate frozen approval; never broaden it to match a changed remote."""
    env = _caller_env(env)
    repo = Path(repo).resolve()
    if not isinstance(history, dict) or history.get("target") != target or "base" not in history:
        raise GitError("Exact-target history approval is required", "no_plan")
    if push_target(repo, runner=runner, env=env) != target:
        raise GitError("Push target changed", "plan_stale")
    if observe and _observe_target(repo, target, runner, env) != history["base"]:
        raise GitError("Remote tip changed; request a new review", "plan_stale")
    current = _review_at_base(repo, history.get("head"), target, history["base"],
                              1000, 128000, runner, env)
    current.pop("diff")
    if current["truncated"] or current != history:
        raise GitError("History review changed or is incomplete", "plan_stale")


def commit(repo, expected, message, *, runner=run_git, env=None):
    """Commit a reviewed snapshot with normal hooks; never retry a commit.

    The real index lock excludes cooperating Git writers. A private index holds
    the immutable reviewed tree. Hooks remain enabled. Unexpected post-commit
    evidence is reported unknown and is never automatically published.
    """
    repo = Path(repo).resolve()
    env = _caller_env(env)
    if not isinstance(expected, dict) or expected.get("schemaVersion") != 1:
        raise GitError("A reviewed snapshot is required", "no_plan")
    try:
        current = snapshot(repo, expected["paths"], runner=runner, env=env)
    except (KeyError, OSError) as exc:
        raise GitError("Invalid or unreadable reviewed snapshot", "plan_stale") from exc
    if current != expected:
        raise GitError("Repository changed after review", "plan_stale")
    if expected["tree"] == expected["head_tree"]:
        return _receipt("noop", expected["head"], expected["tree"], committed=False)
    index_path = _git_path(repo, "index", runner, env)
    lock_path = Path(str(index_path) + ".lock")
    try:
        lock = lock_path.open("xb")
    except FileExistsError as exc:
        raise GitError("Git index is busy", "busy") from exc
    try:
        with _temporary_index(repo, runner, env) as (index, stage_env):
            _checked(repo, "read-tree", expected["tree"], runner=runner, env=stage_env)
            if _state(repo, runner, env) != {key: expected[key] for key in _STATE_KEYS}:
                raise GitError("Repository changed before commit", "plan_stale")
            rc, output = runner(repo, "commit", "-m", message, timeout=300, env=stage_env)
            try:
                oid = _checked(repo, "rev-parse", "HEAD", runner=runner, env=env).strip()
                tree = _checked(repo, "rev-parse", "HEAD^{tree}", runner=runner, env=env).strip()
                parents = _checked(repo, "log", "-1", "--no-show-signature", "--format=%P",
                                   runner=runner, env=env).strip()
                actual_message = _checked(repo, "log", "-1", "--no-show-signature", "--format=%B",
                                          runner=runner, env=env).strip()
            except GitError:
                return _receipt("unknown", None, expected["tree"], output, committed=None)
            if oid == expected["head"]:
                if rc is None:
                    return _receipt("unknown", None, expected["tree"], output, committed=None)
                raise GitError(f"git commit failed: {output}", "commit_failed")
            if tree != expected["tree"] or parents != expected["head"] or actual_message != message.strip():
                return _receipt("unknown", oid, tree, output, committed=None)
            if _digest(_index_bytes(repo, runner, env)) != expected["index"]:
                return _receipt("unknown", oid, tree, output, committed=True)
            # Publish the committed index under the lock; worktree edits are not touched.
            try:
                lock.write(index.read_bytes())
                lock.flush()
                os.fsync(lock.fileno())
                lock.close()
                os.replace(lock_path, index_path)
            except OSError as exc:
                return _receipt("unknown", oid, tree, output, committed=True,
                                reason="index_update_failed", error=str(exc))
            return _receipt("committed", oid, tree, output, committed=True)
    finally:
        lock.close()
        lock_path.unlink(missing_ok=True)


def push_target(repo, *, runner=run_git, env=None):
    """Resolve the configured upstream and its sole push URL without networking."""
    env = _caller_env(env)
    repo = _repository_root(repo, runner, env)
    branch = _checked(repo, "symbolic-ref", "-q", "HEAD", runner=runner, env=env).strip()
    short = branch.removeprefix("refs/heads/")
    remote = _checked(repo, "config", "--get", f"branch.{short}.remote", runner=runner, env=env).strip()
    ref = _checked(repo, "config", "--get", f"branch.{short}.merge", runner=runner, env=env).strip()
    if remote.startswith("-") or remote == "." or not ref.startswith("refs/heads/"):
        raise GitError("Unsupported push target", "no_upstream")
    _checked(repo, "check-ref-format", ref, runner=runner, env=env)
    urls = _checked(repo, "remote", "get-url", "--push", "--all", remote,
                    runner=runner, env=env).splitlines()
    if len(urls) != 1 or not urls[0] or urls[0].startswith("-"):
        raise GitError("Publication requires exactly one push URL", "bad_remote")
    # Explicit URLs still undergo Git's transport-specific rewrite rules. Until
    # they can be resolved identically for both commands, do not claim that a
    # read advertisement and a push using the same string address the same repo.
    rc, rewrites = runner(repo, "config", "--get-regexp", r"^url\..*\.(insteadof|pushinsteadof)$",
                          timeout=20, env=env)
    if rc != 1:
        raise GitError("Exact-target publication requires URLs without insteadOf/pushInsteadOf "
                       "rules; configure a direct URL before review. " + rewrites, "bad_remote")
    names = _checked(repo, "remote", runner=runner, env=env).splitlines()
    if urls[0] in names:
        raise GitError("Push URL must be a direct location, not another remote name", "bad_remote")
    return {"branch": branch, "remote": remote, "ref": ref, "url": urls[0]}


def publication_evidence(history, commit):
    """Summarize an already verified approval plus its optional new commit."""
    outgoing = list(history["commits"])
    if commit != history["head"]:
        outgoing.insert(0, commit)
    return {"target": history["target"], "base": history["base"], "commits": outgoing,
            "digest": _json_digest([history["target"], history["base"], outgoing]),
            "reviewDigest": history["digest"]}


def publish(repo, expected_commit, expected_tree, target, *, history=None, created=None,
            runner=run_git, env=None):
    """Push only the expected commit/ref. Retry this API, never upstream work.

    A lost response is reconciled only by observing that exact remote ref at the
    expected commit. Failed/unavailable reconciliation remains unknown.
    """
    repo = Path(repo).resolve()
    env = _caller_env(env)
    if not all(isinstance(oid, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid)
               for oid in (expected_commit, expected_tree)):
        raise GitError("Exact commit and tree IDs required", "no_plan")
    head = _checked(repo, "rev-parse", "HEAD", runner=runner, env=env).strip()
    tree = _checked(repo, "rev-parse", "HEAD^{tree}", runner=runner, env=env).strip()
    if head != expected_commit or tree != expected_tree or push_target(repo, runner=runner, env=env) != target:
        raise GitError("HEAD, tree or push target changed", "plan_stale")
    verify_history(repo, history, target, runner=runner, env=env, observe=False)
    if head != history["head"]:
        if (not isinstance(created, dict) or created.get("commit") != head
                or created.get("tree") != tree or not isinstance(created.get("message"), str)):
            raise GitError("New commit must be bound to the reviewed tree and message", "no_plan")
        parents = _checked(repo, "log", "-1", "--no-show-signature", "--format=%P", head,
                           runner=runner, env=env).strip()
        message = _checked(repo, "log", "-1", "--no-show-signature", "--format=%B", head,
                           runner=runner, env=env).strip()
        if parents != history["head"] or message != created["message"]:
            raise GitError("Publication commit differs from approved parent/message", "plan_stale")
    elif created is not None:
        raise GitError("Unexpected new-commit receipt", "plan_stale")
    # The reviewed base is an ancestor of history.head; the only permitted new
    # commit is its direct child. Thus a successful exact-base lease can only
    # fast-forward (or create an absent ref), despite Git leases allowing force
    # in general. Never use a tracking-based/implicit lease or a '+' refspec.
    publication = publication_evidence(history, head)

    def receipt(state, output="", **extra):
        return _receipt(state, head, tree, output, history=history, created=created,
                        publication=publication, **extra)

    observed = _observe_target(repo, target, runner, env)
    if observed == head:
        return receipt("pushed", reconciled=True)
    if observed != history["base"]:
        raise GitError("Remote tip changed; request a new review", "plan_stale")
    lease = "--force-with-lease=" + target["ref"] + ":" + (history["base"] or "")
    rc, output = runner(repo, "push", "--porcelain", "--no-force", "--no-mirror",
                        "--no-follow-tags", "--recurse-submodules=no", lease, "--", target["url"],
                        expected_commit + ":" + target["ref"], timeout=600, env=env)
    if rc == 0:
        return receipt("pushed", output)
    if rc is not None and _push_rejected_before_update(output, target["ref"]):
        return receipt("push_failed", output)
    try:
        if _observe_target(repo, target, runner, env) == head:
            return receipt("pushed", output, reconciled=True)
    except GitError:
        pass  # Failed reconciliation cannot turn interrupted delivery into failure.
    return receipt("unknown", output, reconciled=False)


def _push_rejected_before_update(output, ref):
    """Only recognized pre-send errors or an exact-ref rejection prove failure.

    An arbitrary fatal error, a broken connection, or a missing porcelain result
    can follow an accepted update. Those require reconciliation instead.
    """
    rows = [line.split("\t") for line in output.splitlines()
            if re.match(r"^[ =*+!\-]\t", line)]
    if rows:
        return (len(rows) == 1 and len(rows[0]) == 3 and rows[0][0] == "!"
                and rows[0][1].endswith(":" + ref)
                and rows[0][2].startswith(("[rejected]", "[remote rejected]")))
    if re.search(r"(?m)^(?:remote:|Writing objects:|Counting objects:|Enumerating objects:"
                 r"|Compressing objects:|send-pack:|error: RPC failed)", output):
        return False
    patterns = (
        r"^fatal: Authentication failed(?: for .*)?$",
        r"^.*: Permission denied \(publickey(?:,[^)]*)?\)\.?$",
        r"^fatal: '.+' does not appear to be a git repository$",
        r"^fatal: repository '.+' not found$",
        r"^fatal: unable to access .+: Could not resolve (?:host|proxy): .+$",
        r"^fatal: unable to access .+: Failed to connect to .+ port \d+.*$",
        r"^fatal: unable to access .+: The requested URL returned error: (?:401|403|404)$",
        r"^fatal: could not read (?:Username|Password) for .+: terminal prompts disabled$",
        r"^error: src refspec .+ does not match any$",
        r"^fatal: invalid refspec .+$",
        r"^fatal: transport '.+' not allowed$",
    )
    return any(re.fullmatch(pattern, line) for line in output.splitlines() for pattern in patterns)
