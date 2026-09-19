# Shared Git publication API

`task_console.git_ops` owns Git status parsing, review snapshots, commit execution
and push outcome handling. It imports no server, UI, allowlist or private bindings.
Repository selection, approval, identity, credential environment, messages and
durable storage of receipts belong to the caller. A snapshot is evidence, not an
authentication token; callers must preserve their existing approval boundary.

```python
from task_console import git_ops

# Obtain these literal leaf paths from the caller's already authorized scope.
review = git_ops.snapshot(repo, ["artifacts/summary.json"], env=identity_env)
target = git_ops.push_target(repo, env=identity_env)
history = git_ops.review_history(repo, review["head"], target=target, env=identity_env)
if history["truncated"]:
    raise RuntimeError("Complete history review is required")
# Review history["diff"], the prospective byte diff, and the exact target.
# The caller owns approval and must block if required evidence is unavailable.
history.pop("diff")  # Preserve the complete metadata/digest, after displaying it.
git_ops.verify_history(repo, history, target, env=identity_env)
local = git_ops.commit(repo, review, message, env=identity_env)
if local["state"] == "committed":
    # Persist local + target before attempting delivery.
    created = {"commit": local["commit"], "tree": local["tree"], "message": message.strip()}
    delivery = git_ops.publish(repo, local["commit"], local["tree"], target,
                               history=history, created=created, env=identity_env)
```

`snapshot(repo, allowed_paths)` records HEAD, branch, HEAD tree, index tree, a
SHA-256 of the actual index, hashes of tracked and visible untracked file bytes,
NUL-delimited status, the explicit allowed paths, and the prospective tree ID.
It refuses dirty paths outside that set. Paths are literal leaves, never globs,
directories, traversal or Git metadata. Changed submodules are refused. A clean
tracked file becoming dirty, an unrelated staged/untracked file, changed index
bytes, or HEAD drift invalidates the review.

Submodule support is limited to preserving unchanged gitlinks and reviewing
already committed outgoing gitlink history. Staged gitlink additions, pointer
updates, deletions and type changes are unsupported, whether or not the path
exists on disk. Dirty submodule worktrees (including untracked files and changed
checkout HEADs) also block snapshots, even for an unrelated ordinary-file scope.
Status explicitly overrides `diff.ignoreSubmodules` and per-submodule ignore
settings. Gitlink modes are read from both HEAD and the real index, so a deleted
entry or absent checkout cannot disappear through temporary-index `git add`.
Explicitly selecting even a clean gitlink is unsupported. These cases raise
`unsupported_submodule`; console plans are unavailable and contain no `expect`.
Rejection preserves real index bytes, worktree and refs. No submodule publisher
or recursive push is provided. The gitlink path set is also bound into snapshot
state; approval issued before this contract change requires a fresh review.

Snapshot construction uses a temporary index and may create unreachable Git
objects. It preserves the real index bytes, worktree and refs. The tree is checked
against a newly built snapshot before commit. Commit acquires the real index lock,
loads the reviewed tree into a private index, rechecks observed state and runs
normal `git commit` with hooks enabled. On success it installs the committed index
under that lock without rewriting the worktree. The temporary index path is
resolved through Git, including for linked worktrees.

`push_target` resolves the configured upstream branch and its single push URL. It
does not guess a first remote or first branch. `publish(repo, expected_commit,
expected_tree, target, history=history, created=created)` requires those exact local IDs, an unchanged target and complete history approval,
then pushes only `expected_commit:target_ref`. It permits only fast-forward updates or creation of an absent ref, disables
mirror/tag and recursive-submodule expansion, and uses the exact-base comparison
described below. It does not stage files, create
another commit, invoke business work or retry automatically.

| State | Meaning |
| --- | --- |
| `noop` | The reviewed worktree produces the current HEAD tree; no commit was made. |
| `committed` | A local commit with the reviewed tree and expected parent is verified. |
| `pushed` | Git returned success, or a follow-up observation confirmed the exact remote ref/commit. |
| `push_failed` | Git explicitly rejected the requested ref, or a recognized pre-send authentication, configuration or connection error proves it was not updated. The local commit remains. |
| `unknown` | Commit evidence or delivery evidence is inconclusive. No automatic replay occurs. |

Precondition and ordinary commit failures raise `GitError` with a stable `code`.
Results include `commit`, `tree`, `out` and `pushed`; commit results also include
`committed`, which can be `None` for uncertain commit evidence. A lost commit
response can still become `committed` when its tree and parent are verified. A
lost push response becomes `pushed` only if a bounded `ls-remote` sees the exact
requested commit. An unavailable or different remote observation remains
`unknown`. Never interpret an unchanged cached tracking ref as remote evidence.

Child diagnostics cross a separate confidentiality boundary before becoming a
`GitError`, plan error, or receipt `out`/`error`. The shared
`fleet_guards.secrets` scanner checks them. Credential findings, URI userinfo, or
authorization headers cause the entire diagnostic to be replaced with a visible
omission marker, so repeated unlabelled values cannot remain in a receipt.
Unavailable, failed or malformed scans visibly report `secret scan unavailable`
and omit the diagnostic. Safe diagnostic text is retained. No diagnostic is
truncated before checking it, and raw child output remains local only for
exit/ref classification. Sanitization does not change `push_failed` versus
`unknown`, the stable error code, or the retained publication evidence.

Successful history and prospective diffs are review content and bypass diagnostic
sanitization. Complete history bytes determine the history digest; the prospective
diff remains bound to the snapshot tree. A failed prospective diff is never
appended to that content. Consumers may display and retain diagnostic fields
without retaining the raw child streams themselves.

After `push_failed` or uncertain delivery, persist the known commit/tree/target, `history`, `created` and `publication`
evidence and retry **only** `publish` with them. Later worktree changes remain untouched. HEAD
drift or target drift requires intervention. An unknown commit with an unexpected
tree or parent is not a publishable receipt.

The injectable runner has signature `runner(repo, *argv, timeout=..., env=...)`
and returns `(exit_code_or_None, output)`. `None` means interruption or missing
exit evidence. The default uses installed `llmcall.process` for operations that
can launch hooks or transports and `subprocess.run` for simple local Git reads.
It never invokes a model. Packaging must provide that installed process module;
there is no fallback process-tree implementation or embedded retry ladder.
Identity and credential environment overlays are per call. Repository/index/object
storage redirection via the caller environment is rejected.

## Console integration

The endpoints and action names stay unchanged. `repos.commit_push_plan` retains
`files` for display and adds `expect`, `diff`, `diffTruncated` and `pushTarget`.
Both publish flows must display the review and send `expect: p.expect`, replacing
`expect: p.files`. The action dispatcher already passes this JSON field through.
Path-only requests now fail closed with `no_plan`, including clean-worktree pushes.

The UI must respect `blocked`, display the diff safely as text, and retain the
returned `expect` on `push_failed`/delivery `unknown` for an explicit push-only
retry. Such a retry still requires `push: true`. Commit uncertainty caused by an
unexpected tree/parent does not receive a retry payload. `ok: false` is not proof
that no commit happened; inspect `state`, `commit`, `tree` and `committed`.

The UI collects complete plans before its approval dialog and sends the frozen
`expect`. It keeps explicit push-only retry receipts. The backup consumer still
needs its own integration; accepting filenames cannot identify which of two
reviews of the same paths was approved.

### Complete history and unavailable plans

The console observes `ls-remote --refs --exit-code -- <push-url> <full-ref>`
through the existing Git runner. This read-only transport does not fetch objects,
update tracking refs or write FETCH_HEAD. A missing advertised commit in the local
object store returns `refresh_required`: explicitly fetch from the **selected push
URL/ref**, reconcile locally as needed, then request a new review. The existing
ordinary fetch action may read a different fetch URL and cannot satisfy this
prerequisite by itself. No new endpoint or hidden fetch is introduced.

For an existing ref, the advertised commit must be a proven local ancestor of the
snapshot HEAD. Every commit in `base..HEAD` is reviewed with full messages, root
and per-parent merge byte diffs, binary patches, and subsequently reverted content.
Already-published ancestors are excluded. A repository with thousands of old
remote commits and one outgoing commit therefore reviews one commit. For an
absent ref, the review covers the complete HEAD ancestry. Failure to observe an
existing remote is never interpreted as an absent ref.

History explicitly uses `--diff-merges=separate`, overriding `log.diffMerges`
settings such as `off`, `first-parent` and `combined`. History and prospective
diffs both pin `--ignore-submodules=none --submodule=short`, so committed gitlink
changes include their complete object IDs even when local configuration hides
submodules or requests a different display format. External diff and textconv
remain disabled. Reverification uses the same explicit representation; an old
approval that omitted content fails naturally because its review digest differs.

`aheadCount` and `historyCount` count the same complete outgoing set relative to
that exact observation; the repository overview's cached-upstream counters are
unchanged. `historyScope` is `exact_remote_outgoing` or `new_ref_ancestry`.
`remoteBase` is the observed commit ID, or null for a confirmed absent ref.
`expect.history` binds target, base, pinned HEAD, all outgoing commit IDs,
summaries and a digest of the full messages/diffs. An unknown count is null, not
zero. The prospective pending byte diff and user-entered commit message are
approved separately; only one verified direct child with exactly that tree and
message may be added. Hook changes to a message or tree return `unknown` without
publication approval.

Approval is revalidated before committing and before publishing. Remote
advancement, reset, deletion, target changes or newly discovered history require
new review; retries cannot silently expand the set. `publication` records the
base, target, complete final outgoing commit IDs, their digest and the original
review digest. Failed delivery retains that evidence, `history`, and the optional
`created` commit/tree/message for a publication-only retry. An exact remote tip
already equal to the retained commit reconciles delivery without pushing again.
Legacy receipts without exact-target history require a new review.

Shallow, grafted or replacement-ref repositories are refused. Replacement objects
and lazy fetching are disabled in the Git environment, including hooks/transports.
Missing required commits, trees or blobs block review rather than fetching.
URL rewrite configuration (`insteadOf`/`pushInsteadOf`) and URLs naming another
remote are refused: Git may interpret them differently for a read and a push.
Configure a direct selected URL before review. Separate fetch and push URLs are
supported and only the push URL supplies approval evidence.

The console limits review to 1,000 commits, 200 pending paths, and 128,000 UTF-8
bytes across history and pending diffs. Counts are computed before truncation.
Exceeding a limit sets `historyTruncated`, `aheadTruncated`, `filesTruncated`, or
`diffTruncated` as applicable, returns a blocked plan, and omits `expect`.
There is no silent 50-row cutoff. A history read/count failure or timeout also
blocks approval, preserving the error code and safe diagnostic. Consumers must respect all
completeness flags; a preview is not approval evidence.

| Plan state | Contract |
| --- | --- |
| `ready` | `ok: true`; complete `expect` is present. |
| `blocked` | `ok: false`; explicit scope/size reasons in `blocked`; no `expect`. |
| `unavailable` | `ok: false`; safe `error` and stable `code`, `blocked` reasons, and any available display evidence; no `expect`. |

A snapshot timeout is an unavailable plan, not successful empty evidence.
Callers must stop at that plan. Existing drift tests require a successfully
created review, then expect `plan_stale` after mutation. A missing, null or
path-only approval still returns `no_plan`: the independent review's suggestion
that falling back to a filename list would produce `plan_stale` does not match
the existing API. No test assertion or timeout is relaxed to hide this distinction.

For push outcomes, a nonzero exit or an arbitrary `fatal:` line alone does not
prove failure. Connection resets, broken pipes, remote hangs and interruptions
can happen after acceptance and remain `unknown` unless exact-ref observation
confirms the requested commit. Recognized pre-send errors require a known exit
and no conflicting porcelain ref result. Unknown/localized messages fail toward
uncertainty. Failure and uncertainty both retain the same explicit retry receipt.

## Exact-base comparison and the no-force policy

A fresh preflight observation alone is insufficient: a remote reset just before
push could make a normal fast-forward publish previously excluded ancestors.
Every push therefore includes an explicit
`--force-with-lease=<full-ref>:<observed-base>`, alongside `--no-force`,
`--no-mirror`, `--no-follow-tags` and `--recurse-submodules=no`, and one literal
`<verified-commit>:<full-ref>` refspec. An absent ref uses the empty expected value.
No implicit tracking-based lease, `--force`, `+` refspec or force-if-includes
fallback is used.

**Git leases normally permit forced updates, even with `--no-force`.** This API
cannot rely on that flag alone. Independently, it proves the exact leased base is
an ancestor of the approved HEAD, and permits only that HEAD or its verified
single child as the source. Thus when the lease matches, the update is necessarily
a fast-forward (or an absent-ref creation). Divergent ancestry is refused before
Git push. The lease supplies comparison, never permission to rewrite history.

For cooperative Git receive-pack implementations, a changed advertisement fails
the lease; a change after advertisement fails the server's old-OID ref update.
Tests use real Git native pipe transport with synthetic local bare repositories
for matching-base success and reset/advance/new-ref collision rejection. This is
a practical exact-ref contract, not a global or permanent server transaction:
a writer can change the ref after success, ABA changes returning to the same OID
are indistinguishable, and a malicious server/transport or trusted hook can act
outside this protocol. No guarantee is made that a hostile endpoint retains its
advertised history or that a later writer cannot reset it. Other refs and
already-existing remote objects are outside the single-ref approval boundary.

## Concurrency and integration limits

The index lock excludes cooperating Git index writers; it does not freeze editors
or arbitrary programs that bypass Git locks. Observation checks are not an OS-wide
worktree transaction. The immutable index prevents late ordinary worktree edits
from being silently staged. Hooks are trusted executable code and remain enabled;
if a hook changes the committed tree, or a non-cooperating writer changes history,
post-commit verification returns `unknown` and blocks automatic publication.
This API does not claim that arbitrary hooks or raw ref writers cannot create a
local commit after the last observation. Callers needing a fully quiescent source
must supply that operational boundary.

Repositories need an existing HEAD. Directory/submodule changes, multiple push
URLs, and ambiguous upstreams require separate handling. Real authenticated
transport, production guard hooks, signing and deployment are not validated by the
synthetic tests. The backup owner must retain its existing scheduling/order,
managed-path policy, credentials, and durable receipts, and must not rerun
curation or capture in response to a push failure.
