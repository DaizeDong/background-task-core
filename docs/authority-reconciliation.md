# Reviewed authority reconciliation

Ordinary registration refuses a managed file whose identity or content differs
from its authority receipt. `reconcile-plan` provides a separate, explicit review
path when an external change should become the accepted file baseline.

The request is private JSON with `schemaVersion: 1`, a nonempty `reason`, a
nonempty unique list of owned `task_ids` in the maintenance scope, and `files`.
Each file entry contains its exact managed `path`,
`expected_receipt_fingerprint`, `reviewed_fingerprint`, and a nonempty `reason`.
Both fingerprints come from observations reviewed by the operator. Unknown,
duplicate, absent, unchanged and reparse paths are refused. Every other managed
file must still match its receipt. Without the explicit Scheduler review described
below, every selected task must still match its stable Scheduler definition.
Nonselected task drift is reported in
`baseline.unselected_scheduler_drift` and remains unaccepted: its original task
proof is preserved verbatim and ordinary operations on that task still refuse
drift. All tasks' current definitions remain bound to the plan, so any concurrent
definition change invalidates approval. Task execution state can change without altering that
definition, so file-only reconciliation does not require a running task to stop.

An optional `scheduler_reviews` list can explicitly accept exactly one selected
task's current definition. Its entry contains `task_id`, `name`,
`expected_receipt_identity`, `reviewed_identity`, `reviewed_fingerprint`, and a
nonempty `reason`. The task must be selected, nonhistorical, present and idle. Its
literal name and previous definition identity must match the receipt, and its
enabled value must remain unchanged. The receipt must already record an observed
root task definition. The reviewed identity and complete stable snapshot
fingerprint must both match the current observation. Its specification and
execution fields must agree with the current effective desired declaration.
Omitting this review does not authorize accepting a changed selected definition.

When effective declarations have removed an initially adopted task, the optional
`historical_task_ids` list must name exactly the ownership proofs left without a
declaration. These IDs must be unselected and their current Scheduler objects must
be absent. Supply an independently reviewed `historical_receipt_chain` in current
to initial-adoption order, with each generation's `generation`, `receipt_digest`
and `journal_fingerprint`. The sequence must match exactly. Its records, protected
inputs and predecessor before-images must agree, and each historical proof must
match its original adoption identity and name, except for the authority epoch.
The original adoption approval and protected publication images provide separate
evidence for that origin. Duplicate names, missing history and declarations
without ownership proofs are refused. The supplied sequence is an explicit review
input; the tool cannot prove that editable predecessor links enumerate every past
transaction. This review preserves the original proofs and creates no retirement
record.

Run `reconcile-plan --request <private-review.json>` with the four explicit runtime
arguments. It only reads and returns a plan whose `approval_revision` binds the
review, existing receipt and pointer, parsed and raw runtime configuration,
desired file, effective inputs, managed files and Scheduler observations.
Historical review also binds the ancestry files and original protected inputs.
Review the complete result, then pass it to
`reconcile-apply --request <private-plan.json> --approval-revision <digest>`.
The CLI does not infer approval from the review's reasons.

Apply takes the existing authority and file locks, plus the explicitly reviewed
task's lock when present, and rechecks the approved
observations. It saves the actual current before-images in the protected vault,
then publishes a new immutable receipt and its conditional generation pointer.
The original effective-input contents, authority epoch and all existing managed
files remain unchanged. File-only reconciliation preserves every task proof.
An explicit Scheduler review updates only that task's definition identity in the
new receipt; all other fields and all unselected and historical proofs remain
unchanged. The reviewed task's protected before-image includes its full idle
observation, and the journal binds that exact evidence reference. Apply rechecks
the review at each completion checkpoint. It makes no Scheduler writes, stages no
launcher, and neither recreates nor enables a task. Any subsequent declaration
change requires an ordinary registration plan and its separate apply.

If interrupted, `runtime-status` and ordinary `recover` identify
`reconcile-resume`. Resume requires the transaction ID and the exact original
approval revision. It verifies the pinned evidence and derives the only allowed
completion receipt before continuing. It checks the complete ownership map and
the reviewed task's exact before-image reference when that review is present.
It never invokes ordinary compensation:
the recorded file images are the reviewed external edits, not earlier contents
that may be restored. A completed transaction can be queried again without
requiring later legitimate file changes to revert.

These guarantees use the same cooperative directory and locking assumptions as
the registration runtime. Protected objects may remain if a process dies after
a vault write but before its journal update; recovery does not delete objects
whose ownership was never journaled. Plans, receipts and diagnostic output
belong in private runtime storage and must not be added to the tool repository.
