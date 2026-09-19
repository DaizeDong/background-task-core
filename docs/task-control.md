# Task controller integration

The source HTTP actions, standalone CLI, and `act.ps1` compatibility shell share
`controller.load_runtime` and `runtime.dispatch_adapter`. This is source
integration with synthetic tests, not a deployment or live Scheduler acceptance.

## Explicit private authority

Supply all four CLI arguments, or all four environment variables. Partial CLI
arguments do not merge with environment settings. Paths must be absolute.

| CLI argument | Environment variable |
|---|---|
| `--runtime-config` | `TASK_CONSOLE_RUNTIME_CONFIG` |
| `--private-root` | `TASK_CONSOLE_PRIVATE_ROOT` |
| `--state-root` | `TASK_CONSOLE_STATE_ROOT` |
| `--vault-root` | `TASK_CONSOLE_VAULT_ROOT` |

The config schema is [the concrete runtime contract](task-registration-runtime.md).
It cannot select modules, providers or executable adapters. The caller establishes
the private roots and their access/backup policy. Missing configuration returns
`runtime_not_configured`; partial configuration returns
`incomplete_runtime_configuration`. Missing declarations, adoption, effective input
objects and corrupt/stale receipts refuse control. There is no home/repository
discovery, automatic adoption, cached authority, or public legacy write bypass.

Explicit recovery uses the same loader with `for_recovery=True`, then validates
the existing journal and protected input references. This preserves recovery when
the submitted desired file or authority pointer is missing; it does not select a
legacy writer. Config validation remains mandatory. Read/status paths do not
provision locks/state or register tasks.

## Entry points and outcomes

With the four authority settings provided:

```text
python -m task_console control --name AcmeSync --verb stop
python -m task_console retire-plan --name AcmeSync --reason maintenance
python -m task_console control --name AcmeSync --verb retire --reason maintenance
```

`control` also accepts a JSON object on stdin (or `--request FILE`):
`{"name":"AcmeSync","verb":"stop"}`. The optional `reason` is required for retire.
Names remain data, including quotes, spaces and shell punctuation. Paths,
wildcards and control characters are rejected. The root COM folder and literal
`GetTask` target are fixed. HTTP retains its loopback binding, Host allowlist,
token authentication and fresh live-name membership check before dispatch.

All control results include `schemaVersion`, `ok`, `name`, `verb`, `message`,
`before` and `after`. Errors contain a safe `error.code`/`error.field`; transport
output, XML and exception text are not exposed. CLI exits: 0 acknowledged/applied,
2 refused, 3 incomplete or uncertain. `act.ps1` preserves its old binary 0/1 exit
contract and `TASKCONSOLE_NAME`/`TASKCONSOLE_VERB` inputs. It forwards JSON on stdin
so PowerShell's native argv quoting cannot discard literal quotes or Unicode.
`TASK_CONSOLE_PYTHON` selects the caller-installed interpreter, outside runtime JSON.

| Operation | Meaning of success |
|---|---|
| enable/disable, migrated task | Committed, cleaned registration transaction; effective binding input generation advances |
| run, idle enabled task | Scheduler accepted the request; `status=run_requested`, `payload_success=null` |
| run, already running | No new run requested; `status=already_running` |
| run, disabled task | Refused; no automatic enabling |
| stop, idle task | `status=scheduler_idle`; no stop issued |
| stop, running task | Stop requested and Scheduler became idle; payload cleanup remains unverified |
| uncertain control reply/cleanup | `ok=false`, `status=cleanup_uncertain`; no automatic retry |

Stop never claims a WorkItem is cancelled or that detached payload descendants
are gone. It returns `payload_cleanup=unverified`. The fixed COM stop readback
wait is bounded at two seconds inside the existing bounded process transport.
Only an explicit `run` request can call COM `Run`; tests never start actual tasks.
The controller uses `llmcall.process.execution_scope`; it implements no process
supervisor, cipher, lock or argument parser.

## Authority and compatibility

Legacy and migrated enable/disable/retire use the existing registration transaction
engine. Legacy ownership stays legacy; full XML controls change only enabled state,
and retirement removes active projections with protected before-images. Verified
publication advances selected ownership receipts, so repeated controls remain
usable. Arbitrary definition-mutating legacy callbacks refuse with
`legacy_declarative_conversion_required`; built-in controls explicitly convert
their work into this transaction. Run/stop callbacks stay under authority/task
locks. Pending journals, changed ownership and malformed declarations refuse.
No ownership is inferred or refreshed from a matching task name.

`retire.apply` preserves its name/reason API but now always dispatches. The old
algorithm is a private callback tested separately. Migrated retirement preserves
`done`, `backups`, `reason` and exposes `recovery_transaction` for protected
before-images rather than creating plaintext XML backups. `retire_plan` preserves
`steps`, `changes`, and `blocked` and adds the exact registration proposal.
The legacy read-only preview without runtime configuration reports
`authority_status=unconfigured`; this does not authorize an apply.

Linked work items are queried through a fixed optional installed
`reminder_linked_items.read_linked_items` accessor and the explicit absolute
`TASK_CONSOLE_REMINDER_DB` path. Results distinguish available, unavailable and
error; unavailable/error carry null items. No store initialization, migration,
cancellation or task-name inference occurs. The reminder owner must deliver the
read-only accessor and attest its linkage schema. Trusted Python composition can
inject a reader or an explicit `load()['linked_work_items']` mapping.

## Export and restore

`python -P -m task_console export` queries the compiled backup scope under
authority/task/resource locks. It verifies projection ownership and returns exact
observed XML, enabled state, literal root identity and generation-bound receipts.
ABSENT and FAILED are separate; FAILED makes the batch INCOMPLETE. Retired and
backup-disabled tasks appear only in excluded, without executable XML. Export
does not register. The backup owner stages and publishes a completed snapshot.

`restore-plan --request FILE` accepts `{archive, task_ids, rebinding?}`. The task
selection is explicit and nonempty. It produces a private plan with operation
restore. `restore --request PLAN --approval-revision PLAN_REVISION` consumes that
exact reviewed plan; ordinary apply cannot bypass the restore approval. Legacy
XML is restored through the same staged Scheduler adapter and compensation engine.
Migrated tasks render reviewed declarations, preserving the full XML settings
through the existing renderer. Neither path silently enables disabled exports.
The output has COMPLETED per-task receipts or an incomplete transaction; it never
returns legacy XML for an installer to register after releasing locks.

Absent targets and changed principals require reviewed rebinding keyed by task ID:
`{task_id, source_revision, target_revision, target_principal, generation,
approved: true, reason}`. Target revision fingerprints the current Scheduler
configuration. Initial ownership/adoption must already be reviewed. Legacy
principal changes require declaration conversion; password principals remain
auth_pending until the credential owner provides the existing adapter prerequisite.

Interrupted restore uses `restore-recover --transaction-id ID --approval-revision
PLAN_REVISION`. The existing journal decides completion versus compensation.
Completed recovery can reproduce receipts; a rolled-back operation requires a
fresh export/restore plan and approval. Registration success does not assert
runtime_ready or payload success.

## Installer and source-owner responsibilities

Install the reviewed task-console, fleet_guards and llmcall artifacts into the
selected interpreter before replacing legacy scripts with these delegates.
Supply the same four authority settings to HTTP and standalone processes. The
public declarations, actual private task IDs/bindings, adoption proof and launcher
inventory must be supplied and reviewed by their source owners. No actual task
ownership is inferred by this integration.

CONFIG's registration adapters preview with an explicit task ID and optional
`-Migrate`; `-Apply` requires `-Request` and `-ExpectedRevision`. They no longer
install payload scripts, generate launchers themselves, or register via cmdlets.
The profile adapter refuses a legacy `-SyncRepo` override: source roots belong in
reviewed private bindings. The installer must deliver payloads before planning.
Only explicit reviewed enable control may enable an existing disabled task.

CONFIG's launcher and backup delegate share `task-console-binding.ps1`, which
uses T18's `install-runtime-artifacts --resolve` output for the installed Python,
entrypoints and optional reminder import root. Status/stop do not resolve or
install artifacts. Startup requires explicit reviewed roots, resolver and Python;
guessed source, home or working directories and PythonPath overrides are refused. Existing
snapshot bindings are preserved. Installer integration is supplied as a separate
owner patch: it consumes `restore-task-snapshot.ps1` receipts and removes outer
Scheduler registration. Applying that installer patch and shipping the optional
reminder accessor remain their owners' responsibilities.
