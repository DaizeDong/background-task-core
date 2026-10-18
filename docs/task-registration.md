# Task registration producer contract

The concrete Windows runtime is documented in
[task-registration-runtime.md](task-registration-runtime.md). This page records the
transaction engine's contract. T11's `compiler.plan` remains read-only.

## API and private inputs

`registration.build_plan(runtime, task_ids, operation="apply", migrate=False,
approve_enable=False, reason="")` reads inputs and validates an explicit mutation
plan. Operations are apply, enable, disable and retire. Full task IDs are opaque;
installation namespaces and structured identities retain the T11 contract.
`apply(plan, expected_revision, runtime=...)` checks that exact plan again under
locks. `retire(task_id, expected_revision, reason=..., runtime=...)` and
`recover(transaction_id, runtime=...)` share the same engine. Recovery compensates
an incomplete transaction; it does not restore a retired task to active service.

The Runtime load callback supplies a private bundle with these fields:

- `request`: the T11 request envelope, now with the current migrated task IDs and
  authority epoch in machine. T12 checks authority before compiling a copy with
  an empty migrated set through T11. This does not grant authority to T11 output.
- `ownership`: full task ID to `{name, task_path, epoch, writer, identity}`.
  TaskPath must be the root path. Writer is `legacy` or `declarations`, determined
  by the migrated set. Identity must match the fresh Scheduler observation; an
  explicitly approved absent target uses null. Ownership evidence comes from a
  reviewed initial import or the previous committed receipt, not guessed names.
- `paths`: exact adapter keys for bindings, machine, task-health.json,
  TaskNames.ps1, categories.json and tombstones. All six must exist and parse.
  Bindings/machine are the published generation; only selected bindings change.
- `file_ownership`: resource key to the fingerprint of its last approved image,
  including identity and content hash. Drift refuses publication.
- `active_xml`: required for retirement, full task ID to an exported active XML
  resource key, or explicit null for a verified absence of an active export.
  Exported files need file ownership proof too. Retirement removes these files
  transactionally; the before-image stays behind the secure storage boundary.
- `linked_work_items`: task ID to caller-supplied linked work-item descriptors.
  The engine reports these verbatim and has no cancellation operation.

The load callback must be a strict, bounded reader of the *pinned desired input
generation*. Its revision covers declarations, namespaces, ownership and paths.
Publication targets must be distinct from that pinned input generation. It must
remain readable throughout recovery. The controller advances its input pointer
only after the journal is terminal, loading the resulting machine/bindings and
ownership receipts for the next operation. Pointing load at files being rewritten
by this same transaction causes `input_changed` and a conflict, intentionally.

An optional private task binding `description` stores category display text;
`machine.overrides[full_task_id].description` can override it. It is a string
(including empty or multiline text, excluding NUL), never an executable field.
Public component task manifests do not accept it. Compilation retains it in
TaskSpec and emits `categories[].taskDesc[OS_task_name]`, without changing argv,
Scheduler parity fields, task IDs or installation namespaces. Old bindings may
omit it. During initial import, the controller must explicitly map the approved
legacy taskDesc entries into these private bindings; the compiler does not adopt
baseline text as authority. T12 updates/removes only selected descriptions and
membership, retaining unselected taskDesc entries, category metadata and top-level
metadata. Omission clears a selected task's projected description; retirement
removes it. Existing category descriptor changes still require a separate reviewed
category migration.

No bundle is discovered from a user home or repository fallback. Real bundles,
journals, before-images and tombstones belong in controller-selected private
storage outside this public repository. The producer does not add a DATA writer
with a default repository path.

## Required transport guarantees

Runtime accepts `load, files, scheduler, journal, vault, locks, render, authority,
linked_work_items` plus an optional checkpoint callback. The adapters guarantee:

| Adapter | Required operations and guarantees |
| --- | --- |
| files / scheduler | `read(key)` returns exactly an absent observation or a present observation with stable identity and value. Unknown/error/missing replies are never absence. File values are bytes. Scheduler values retain XML, enabled, running and normalized spec with argv as an array. Running is a volatile observation, excluded from configuration comparison/fingerprints; it must still be checked separately and a running target is busy. |
| files / scheduler | `prepare(key, value, token)` stages an image and returns its *eventual published identity* without touching the active object. Null value means removal. Tokens are transaction-scoped and discoverable after interrupted preparation. |
| files / scheduler | `publish(key, expected, desired)` conditionally publishes only if the object still matches identity plus content/XML. New objects use OS no-replace creation. Running Scheduler objects refuse changes. A lost reply may follow a completed operation; recovery inspects the prepared identity. |
| files / scheduler | `cleanup(token)` is idempotent, checks token ownership, removes only private staging, and never removes the active object. Restore preparation uses token plus `:undo`. |
| journal | `create(id, record)` durably creates without replacement; `save` durably replaces; `load` validates/bounds the record; `pending(lock_keys)` enumerates all unresolved or uncleaned overlapping transactions. Missing enumeration is a hard error, never an empty result. Storage must enforce ownership/ACLs and reject arbitrary journal redirection. |
| vault | `put(key, snapshot)` durably stores through the existing encrypted boundary and returns an opaque reference; `get(ref)` verifies and returns it. All before/after/undo images use this adapter, including all XML. No plaintext fallback or new encryption format is implemented. Vault references are retained for recovery and retirement evidence. |
| locks | `hold(sorted_keys)` owns cross-process, crash-releasing authority and resource locks. All declaration writers and legacy dispatch share the authority lock. A currently held lock is busy; timestamps alone cannot establish a dead writer. |
| render | `render(spec, old_snapshot, enabled)` prepares the disabled Scheduler value using existing registration/XML and launcher behavior. It must be pure with respect to active resources and retain principal, power, timezone and exit propagation. Current core does not publish renderer-created launcher files. |

WindowsScheduler validates the structured query/prepare/publish/cleanup transport,
pins TaskPath to root, rejects wildcard/path-bearing TaskNames, retains argv arrays,
and verifies readback. It contains no shell interpolator or default subprocess
runner. A Python
check followed by replacement is not an atomic compare-and-swap against an
uncooperative writer. Existing-file and Scheduler replacement require the shared
lock protocol or a controller-enforced quiescent window; do not claim stronger
guarantees from this producer.

The ordinary journal contains phases, references, safe error codes and private
resource metadata, never snapshot XML or file bytes. Stage intent is persisted
before preparation; publish intent is persisted before mutation. All resources
are staged before publication. Scheduler registration is disabled, generated
files and binding/machine generations publish, readback runs, then explicit
enabled bindings enable. Each activation validates the complete expected file
generation, including relevant unchanged resources, and final readback repeats
that check before the commit marker. The same lock/quiescence boundary must cover
publication, readback and activation; readback cannot prevent an arbitrary editor
from changing a file immediately afterward. Retirement verifies disabled state before removing
monitoring/export membership and keeps a tombstone plus secure XML reference.
An approved absent task remains absent during retirement: no render, stage or
Scheduler publication occurs for it. Its projections/export membership are still
removed and a tombstone records the secure absent before-image.

Unfinished or uncleaned journals block overlapping apply and legacy dispatch
inside the authority lock. Compensation checks every selected Scheduler object
before restoring supporting files. If an object is enabled, running, unknown or
unreadable and cannot be safely undone, supporting restoration is deferred and
the journal retained. A disabled but still running task is also busy. Recovery
can proceed when execution ends; a durable identity/XML/configuration change
remains a conflict. Re-enabling an original Scheduler configuration requires
the complete supporting before-generation to pass readback, including unchanged
files. Journals retain secure references for all Scheduler/file baselines.
Cleanup after either terminal status is independently repeatable.

`concurrency.gate_budget` optionally supplies positive integer wait_seconds,
execution_seconds and cleanup_seconds plus held_by_payload=true. Their sum must
fit a finite effective timeout. No gate is inserted, acquired or run by validation.
The existing payload owns gate acquisition; absence of this optional metadata
does not prove an existing payload's internal budgets.

## CLI

The installed/source package exposes:

```text
background-task-core apply --request PRIVATE_PLAN.json --expected-revision REVISION
background-task-core retire --task-id FULL_ID --expected-revision REVISION --reason TEXT
background-task-core recover --transaction-id TRANSACTION_ID
```

An embedding calls `main(argv, runtime=runtime)` or explicitly configures the
registration Runtime in that process. Exit 2 is an input/contract refusal. Apply
or retire returns 0 only for committed; a compensated/conflicted result returns
3. Recover returns 0 for committed or rolled_back, 3 for conflict. T11 plan CLI
semantics are unchanged. There is no runtime plugin loader from untrusted JSON.

## Creation check

`background-task-core creation-check --task-id ID` takes the same four runtime/configuration roots as
`registration-plan` and returns `reuse`, `update` or `create` with the matching task IDs, their
enabled/running state and an inventory revision. It registers, runs and enables nothing. Two tasks
are equivalent when their executable, argument contents, working directory and principal match;
frequency is excluded, so a cadence change updates the existing definition. It covers declared
tasks only, not every unmanaged Scheduler entry.

Creating a missing task rechecks the declared inventory under the authority lock and raises
`equivalent_task_exists` before publication when an equivalent action is already declared, so a
renamed declaration cannot register the same command again. An unreadable inventory refuses
creation.
