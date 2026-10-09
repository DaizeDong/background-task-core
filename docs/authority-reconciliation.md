# Reviewed file reconciliation

Ordinary registration refuses a managed file whose identity or content differs
from its authority receipt. `reconcile-plan` provides a separate, explicit review
path when an external change should become the accepted file baseline.

The request is private JSON with `schemaVersion: 1`, a nonempty `reason`, a
nonempty unique list of owned `task_ids` in the maintenance scope, and `files`.
Each file entry contains its exact managed `path`,
`expected_receipt_fingerprint`, `reviewed_fingerprint`, and a nonempty `reason`.
Both fingerprints come from observations reviewed by the operator. Unknown,
duplicate, absent, unchanged and reparse paths are refused. Every other managed
file must still match its receipt; every selected task must still match its stable
Scheduler definition. Nonselected task drift is reported in
`baseline.unselected_scheduler_drift` and remains unaccepted: its original task
proof is preserved verbatim and ordinary operations on that task still refuse
drift. All tasks' current definitions remain bound to the plan, so any concurrent
definition change invalidates approval. Task execution state can change without altering that
definition, so a running task does not need to stop for this operation.

Run `reconcile-plan --request <private-review.json>` with the four explicit runtime
arguments. It only reads and returns a plan whose `approval_revision` binds the
review, existing receipt and pointer, parsed and raw runtime configuration,
desired file, effective inputs, managed files and Scheduler observations.
Review the complete result, then pass it to
`reconcile-apply --request <private-plan.json> --approval-revision <digest>`.
The CLI does not infer approval from the review's reasons.

Apply takes the existing authority and file locks and rechecks the approved
observations. It saves the actual current before-images in the protected vault,
then publishes a new immutable receipt and its conditional generation pointer.
The original effective-input contents, authority epoch, task proofs and all
existing managed files remain unchanged. It makes no Scheduler writes, stages no
launcher, and neither recreates nor enables a task. Any subsequent declaration
change requires an ordinary registration plan and its separate apply.

If interrupted, `runtime-status` and ordinary `recover` identify
`reconcile-resume`. Resume requires the transaction ID and the exact original
approval revision. It verifies the pinned evidence and derives the only allowed
completion receipt before continuing. It never invokes ordinary compensation:
the recorded file images are the reviewed external edits, not earlier contents
that may be restored. A completed transaction can be queried again without
requiring later legitimate file changes to revert.

These guarantees use the same cooperative directory and locking assumptions as
the registration runtime. Protected objects may remain if a process dies after
a vault write but before its journal update; recovery does not delete objects
whose ownership was never journaled. Plans, receipts and diagnostic output
belong in private runtime storage and must not be added to the tool repository.
