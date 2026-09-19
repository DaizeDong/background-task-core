# Concrete task runtime

The concrete runtime is implemented in source, not deployed. HTTP controls and
standalone/registration delegates share the [controller](task-control.md).
CONFIG install/export and other skill wrappers remain owner integrations. T11
planning is unchanged: applicable=false and replacement_safe=false.

## Trusted configuration

`RuntimeConfig.read(config_path, private_root=..., state_root=..., vault_root=...)`
resolves bounded configuration under a caller-selected private root.
`create_runtime(config)` uses the bundled COM transport and DPAPI protector.
JSON cannot select modules, protectors, providers, scripts or executables. Trusted
Python callers can inject a Scheduler transport/protector for synthetic testing.
Import/construction/status do not run tasks or provision locks/state.

The caller must establish private directory ownership, ACLs and backup policy.
No repository/home discovery or plaintext fallback exists. `vault_root` must be
the existing secrets directory captured by CONFIG secrets-vault.ps1 (`*.cred`).
State and vault roots must be disjoint; desired/adoption files and output paths
cannot alias runtime storage. Paths in this synthetic config resolve beneath the
supplied private root:

```json
{
  "schemaVersion": 1,
  "domain": "11111111111111111111111111111111",
  "desired": "desired.json",
  "adoption": "adoption.json",
  "paths": {
    "bindings": "generated/bindings.json",
    "machine": "generated/machine.json",
    "task-health.json": "generated/task-health.json",
    "TaskNames.ps1": "generated/TaskNames.ps1",
    "categories.json": "generated/categories.json",
    "tombstones": "generated/tombstones.json"
  },
  "active_xml": {"acme-maintenance/sync": null},
  "launchers": {"acme-maintenance/sync": "launchers/sync.vbs"}
}
```

Submitted desired input is the T11 request envelope, distinct from generated files
and immutable during compensation. All six main output files must already exist.
Launcher/export inventory is explicit; existing unmanaged launchers conflict.
`active_xml` must include an export path or explicit null for every retired task.

Adoption is reviewed ownership evidence, never inferred from matching names. Its
fields are schemaVersion=1, matching domain, 32-hex generation, authority with
authority_epoch/migrated_tasks, ownership, and file_ownership. Ownership maps full
task IDs to `{name, task_path, epoch, writer, identity}`. TaskPath is root; writer
is legacy/declarations. Identity is null only for verified absence, otherwise an
adapter `task-definition:<sha256>` observation. File ownership maps every resolved
absolute resource path to `registration.fingerprint` of its actual identity and
bytes, including approved absence of a new launcher. No implicit adoption command
exists. Unknown/lost evidence refuses mutation.

Each successful transaction or compensation writes an immutable receipt and
advances state/current.json under authority. Before publication, both effective
input generations are captured as immutable protected vault objects. Completion
references the resulting generation (the prior input on compensation), including
binding enabled flags and enabled machine overrides. Loading never derives desired
state from mutable generated files. Desired bytes never change. Subsequent source
edits are applied as changes against the previous submitted document, preserving
committed controls for fields the submission did not change. To change an enabled
flag whose submitted value is unchanged, use the explicit enable/disable control.
Current epoch/migrated membership comes from the receipt. Missing input objects,
or pointers after a terminal journal, fail closed; old completed receipts without
input generations require reviewed migration rather than guessing from projections.
The durable committing phase resumes receipt/pointer completion; it never
compensates across an ambiguously published authority pointer.

## CLI and wrapper contract

Every runtime command takes `--runtime-config CONFIG --private-root PRIVATE
--state-root STATE --vault-root SECRETS`. The following prefixes show command
arguments; append all four explicit root/config arguments:

```text
python -m task_console runtime-status
python -m task_console registration-plan --task-id FULL_ID --migrate
python -m task_console apply --request PLAN --expected-revision REVISION
python -m task_console retire --task-id FULL_ID --reason TEXT --expected-revision REVISION
python -m task_console recover --transaction-id ID
```

registration-plan supports --operation apply|enable|disable|retire,
--approve-enable and --reason. It selects one task; the Python API supports
several. Exit 2 means refusal; exit 3 means incomplete/conflicted. Success requires
a terminal cleaned journal. `main(argv, runtime=...)` remains available. Without
an explicit runtime, standalone mutations refuse. The installed environment must
provide the existing fleet_guards and llmcall packages; packaging remains controller-owned.

`runtime.dispatch_adapter(runtime)` returns
`dispatch(name, verb, *, legacy, reason='')`. HTTP/standalone callers must wrap the
entire legacy action in the callback. Authority remains locked throughout that
callback; pending journals block dispatch. Unknown names, missing proofs and
changed definitions never fall back. Migrated enable/disable/retire transact;
explicit run/stop use bounded literal COM control under authority/task locks.
See the controller outcome contract. Do not call control
and then perform an unlocked legacy action. Concrete runtime work-item integration
is empty; the injectable producer still reports caller-supplied linked items.

## Storage and recovery guarantees

runtime_storage reuses GUARDS bounded reads, path validation, no-replace creation,
conditional Windows detach, hard-link publication, atomic replacement and sync.
File identities are actual device/inode pairs. Updates detach the observed file
to a retained entry, then fill the vacancy with no-replace publication. Recovery
can restore a proven detached image after a process exits in that vacancy. An
intervening creator remains a conflict. POSIX existing-file updates refuse because
the current shared conditional-detach primitive supports Windows only.

Locks use Windows kernel byte locks or POSIX flock. Authority and ordered
task/resource locks cover mutation and recovery. Files are never stolen by age or
deleted on release. Journals support exclusive create, update, list/get and
pending-overlap detection. Malformed/unknown entries are errors, not empty lists.
Stage/publish intents precede their side effects; cleanup is independently resumable.
Undo intent also precedes preparation. Repeating a preparation token resumes only
when its resource, bytes and durable filesystem identity match. A created stage
whose identity was never recorded remains a conflict and is retained for review.

Ordinary journals contain references, phases, resource metadata and safe codes.
All before/after/undo images use per-object
task-console-<domain>-<object>.cred files in the existing secrets domain. The
serializer explicitly encodes binary values inside the protected envelope.
ConvertFrom/To-SecureString matches CONFIG's current format. References bind
domain, object, transaction owner and plaintext-envelope digest; CONFIG DPAPI
rewrapping preserves them. Loss, wrong-domain restore or cross-transaction reuse
fails closed. Objects are retained; no automatic GC or real credential reads are
implemented. SecureString's size limit applies and protection failure stops writes.

Task identity is an observation of root path plus complete normalized XML, not an
OS incarnation identifier. Preparation describes expected configuration; query
readback establishes what was published. Running/queued/instance state is checked
separately. Identical external delete/recreate cannot be detected by this API.
All writers must cooperate with locks, or the controller must enforce quiescence
through readback and activation. Scheduler has no compare-and-swap; a COM check
followed by registration/removal cannot exclude an uncooperative editor. GUARDS
also does not promise Windows directory-entry power-loss durability or protection
against hostile ancestor-directory swaps.

Unknown/busy/enable/owner failures retain supporting resources and journal
evidence. Re-enabling an old task requires complete restored-file readback.
Receipt completion verifies expected files and tasks before refreshing ownership.
Death between staging-object creation and recording its identity leaves ambiguous
evidence and cleanup_required; ownership is never guessed. Interrupted GUARDS
temporary journal files similarly block discovery for controller inspection.

## Windows XML and launchers

The fixed PowerShell source receives bounded JSON on stdin and returns bounded
JSON through private pipes. Names/XML/argv never become source code. COM uses root
GetFolder and literal GetTask, TASK_CREATE or TASK_UPDATE (not create-or-update),
and suppression of registration triggers. Observed owned XML and idle state are
checked immediately before mutation. Explicit run/stop control is described in
[task-control.md](task-control.md); registration never starts a payload. CLI errors
never include XML, environment, child stdout/stderr or transport exception text.

Process containment uses the installed `llmcall.process.WindowsJob` primitive,
including suspended creation and kill-on-close cleanup of descendants. Execution,
pipe completion and cleanup share one deadline; each output stream is capped at
8 MiB plus one overflow byte. Timeout, cancellation and uncertain cleanup refuse
success. The higher-level `llmcall.process.run(cmd, prompt, timeout, context=...)`
supports stdin and structured results, but currently lacks an output byte-limit
or bounded capture API and accumulates `communicate()` output without a cap.
Until that API exists, this boundary retains limited pipe capture while reusing
the shared Job Object implementation. There is no local tree-killer fallback.

Preparation validates an unregistered COM definition. COM may materialize
defaults/reorder fields; supplied attributes/values and action/trigger/principal
collections must survive. Settings.Enabled, the snapshot flag and embedded spec
are reconciled. Extra XML settings remain. Task.Data stores the compiled spec and
preserves previous application data as original_data; consumers that interpret
Task.Data directly need review before adoption.
Observation treats omitted Settings/Enabled as the Scheduler schema default true.
It accepts XSD true/false/1/0 with XML whitespace, rejects empty/invalid/mismatched
values, and preserves the original query XML bytes for conditional publication.
Legacy argument observation uses native CommandLineToArgvW through the Windows
boundary, including doubled literal quotes and empty arguments. Platforms without
that parser refuse conversion instead of approximating it.

Daily/weekly/interval/logon declarations, including lists, render for UTC.
Supported trigger dictionaries reject unrecognized fields before compilation or
rendering. Daily accepts type/at; weekly adds unique days; interval accepts exactly
one of seconds/minutes; logon accepts type. Extra Scheduler trigger semantics
require reviewed XML passthrough, which retains the complete trigger collection.
Non-UTC/DST schedules require reviewed full XML passthrough, retaining boundaries,
triggers, settings and principal details. Initial adoption without an embedded spec
also requires XML passthrough, preventing inferred schedule changes. Multiple
actions, unsupported principal shapes and ambiguous trigger forms refuse. Password
and InteractiveTokenOrPassword principals return auth_pending even when a reference
exists: a trusted credential-submission adapter is still needed. No account or
logon type is substituted and no password is read.

The pure launcher renderer reuses the helper's UTF-16LE BOM, hidden Run(...,0,True)
and WScript.Quit exit propagation, plus working directory. It handles disabled
tasks and never invokes the old helper. Launcher paths participate in locking,
staging, readback and compensation; retirement removes them only after idle and
disabled validation. Percent-sign arguments refuse because WScript.Shell.Run
expands environment tokens; newline/NUL arguments also refuse. Tests and
registration never execute a launcher or payload.

## Integration evidence and remaining release work

The runtime report records actual filesystem/process tests separately from injected
Scheduler mutations. The repair checks use synthetic storage, native argv decoding
and fixed PowerShell I/O probes; they perform no Scheduler or DPAPI operations.
Concrete registration live validation and the controller's read-only observation
recheck remain pending regardless of unit-test results.

Root integration still owns CONFIG install/export and other skill wrappers, private
adoption evidence, root ACLs and backup policy, package integration, password
credential submission, real Scheduler readback validation, fleet parity, and
install/export single-writer migration. No active bindings or private data changed.
