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

## Deleting a task

Delete means gone for good: absent from the Scheduler, from the console's category map, and,
through the private follow-up hook, from the health watch list, the backup and the migration
plan. `task_delete.py` implements it behind two authenticated POST routes; `maint` `task.retire`
and `/api/retire/plan` keep working unchanged for their existing callers.

`POST /api/task/delete/plan {name, reason}` is read-only. The name passes the same
`scheduler_windows.task_name` gate as the Controller (spaces allowed, paths, wildcards and control
characters refused), must appear in a fresh `collect.ps1` enumeration (root path, vendor tasks
excluded), and the task must not be running. A non-empty reason is required; it goes into the
Controller's tombstone or the archive receipt. The task's actions (execute, arguments, working
directory) and its exported XML digest are captured now, before anything is unregistered.

- **Managed** (the Controller's compiled declarations name the task, compared case-insensitively
  as dispatch does): the preview is the Controller's own `retire_plan` with the real reason, and
  its `plan_revision` is kept. Apply is `controller.action(name, 'retire', reason)`; the step is
  done only when the transaction is committed and a fresh root enumeration no longer lists the
  task. Whatever the Controller returns, including an exception, a timeout or `ok: false`, the
  root enumeration decides whether the task is gone: a transaction that committed but could not
  clean its staging files (`status: committed`, `cleanup_error`) is a deletion with a failed
  `authority.cleanup` step that names the transaction for `recover`, and any other Controller
  failure after which the task is absent is a failed `authority.retire` step, but still a deletion,
  so the category edit and the hook still run and the result is `partial`. The journal is read back
  separately to collect the files the transaction wrote or removed; those files, plus the
  transaction's own `current.json`, `journals/<tx>.json` and `receipts/<tx>.json` under the state
  root (written as `../` paths relative to the private root), are what the hook receives in
  `authority.files`. An unreadable journal is its own failed step. A declared task whose ownership
  proof is missing is refused, never downgraded to the unmanaged path. Authority that is configured
  but broken refuses. A console with none of the four authority settings also refuses
  (`authority_not_configured`), as `retire.apply` does, unless
  `TASK_CONSOLE_DELETE_WITHOUT_AUTHORITY=1` explicitly allows treating every task as unmanaged; the
  preview then says so in its notes.
- **Unmanaged**: requires `TASK_CONSOLE_DELETED_ARCHIVE`, an existing absolute directory outside
  every git worktree (and so outside the public repo and the backup repo). Unset or unusable is a
  blocking reason in the preview, so no token is issued. Apply writes `<safe name>-<UTC stamp>.xml`
  (UTF-16, as the export declares) and a JSON receipt (name, reason, original actions, XML digest),
  both create-no-replace and read back byte for byte, then unregisters at the root path. The name
  travels as `TC_NAME` in the environment, never inside the command text, and the same PowerShell
  call re-exports the task and refuses if its digest differs from the preview. Absence is then
  verified by a separate root enumeration that throws rather than guesses. That enumeration also
  runs when the unregister call fails or times out: a task that is gone anyway is a deletion
  (a failed `scheduler.unregister` step, result `partial`), not "still there".

The console's own category map (`TASK_CONSOLE_CATEGORIES`, the file the page reads) loses the
name from every category's `tasks`, `taskDesc` and `taskInfo`, compared case-insensitively as the
Controller and the hook compare it. Only a map that parsed and does not list the task counts as
"nothing to edit"; a missing path or file is a `skipped` step and an unreadable file, invalid UTF-8
JSON or a document without a `categories` array is a `blocked` step, each with a note in the preview
and the result, so the result can never be `ok`. Before editing, the untouched
document must re-serialise to the original bytes under some combination of BOM, `ensure_ascii`,
indent, separators, line ending and trailing newline; if none reproduces it, the automatic edit is
refused, because a reformat would bury the one real change. A map that lists the task and cannot be
rewritten is a blocking reason, so no token is issued: once the Scheduler step has run the task can
no longer be previewed, and the hook's apply refuses while the live map still lists it, so letting
the delete through would strand every later cleanup with no console path back. The reason asks for
the entry to be removed by hand and the delete to be previewed again. The write is atomic and read
back. It runs only after the Scheduler step succeeded.

The preview carries every step with its target and status, the hook's steps, `blocking` reasons,
`notes` for things a person must handle, and `warnings` (the pipeline list in `pipelines.js` could
not be read; the category map's verdict is not `remove`; linked todos exist; or linked todos were
not checked at all, because the task is unmanaged or the Controller's linked reader was unavailable,
which is never shown as zero). A task listed on the pipeline page (`PIPELINE_DEFS` in
`pipelines.js`, read from the page itself) is a blocking reason: those tasks are the backup itself,
and the page offers no delete button for them on any tab. When nothing blocks, it also returns a single-use token valid for 300 seconds. The
token is bound to a fingerprint of the XML digest, the managed flag, the Controller plan revision,
the category file's byte digest, the hook's plan digest and the archive binding.

`POST /api/task/delete/apply {token, name}` pops the token (one use, even when refused), requires
the same name, recomputes the whole preview, and refuses with `followup_blocked`, `blocked` or
`plan_changed` before anything irreversible. Only then does the Scheduler step run. If a fresh
enumeration still lists the task, nothing else runs and the status is `failed` (HTTP 500). If the
enumeration cannot be read after the attempt, the status is `unknown` (HTTP 500): the task may be
gone, so the result asks for a refresh and never says "not deleted". Once the task is confirmed
absent the category edit and the hook's apply run; the status is `ok` only when every step and the hook report `ok`, otherwise
`partial` (HTTP 200) with a `remaining` list. A missing hook binding is `partial`, never `ok`. One
delete runs at a time; a second concurrent apply is refused with `busy`.

`remaining` names what a person still has to do. For a hook that reported `failed` it lists only the
hook steps that are `failed`, `blocked` or still `planned`, each with the hook's detail; a `skipped`
step is one the hook judged not to apply and is never listed. A hook that blocks in apply mode
(exit 2) has refused after the task was already deleted, so the step and the hook message say the
task is gone and nothing was cleaned, never "nothing changed"; `remaining` lists the hook's
`blocking` reasons. Only in that case the result also carries `followupRetry`:
`{hook, request, command, howto}`, where `request` is the exact apply request the hook was given and
`command` runs the hook on it from cmd or Git Bash (`"<python>" -B -I "<hook>" < "<request file>"`,
naming `python.exe` even when the console runs under `pythonw.exe`). The delete dialog cannot preview
a task that is gone, and the hook's apply preflight requires the task to be gone and re-checks
everything, refusing with exit 2 and no change, so feeding it the same request again is the
supported re-run once its reasons are dealt with. A hook that failed part-way (exit 1) or whose
reply was unreadable gets no retry: it may have left changes that a blind re-run would trip over,
so a person reviews first.

The page (`static/task-operations.js`) asks for the reason before it calls `/plan`, and discards a
preview whose reason was edited afterwards. Its confirm button is enabled only while the preview is
applicable, carries a token that has not expired, lists no blocking reason, and the typed name
equals the task name exactly. It sends the token once: any reply, including a refusal, sends the
person back to a new preview. It waits up to 20 minutes for `/apply` instead of the page's usual
360 seconds, because the Controller and the hook may legitimately take that long; if the wait
still runs out, it says the result is unconfirmed. Afterwards it re-reads the task list whatever
the status was.

### Follow-up hook protocol

`TASK_CONSOLE_DELETE_FOLLOWUP` holds the absolute path of a private Python script. The console runs
`[sys.executable, '-B', '-I', script]` with `PYTHONDONTWRITEBYTECODE=1`, the script's directory as
the working directory, one UTF-8 JSON document on stdin, and a timeout of 60 seconds in plan mode
and 600 seconds in apply mode. `-I` ignores `PYTHONIOENCODING`, so the hook should read
`sys.stdin.buffer` and write `sys.stdout.buffer` as UTF-8. Exit 0 means ok, 1 failed or partial,
2 blocked by preflight with nothing changed.

```json
{"schema": 1, "mode": "plan|apply",
 "task": {"name": "AcmeSync", "managed": true,
          "actions": [{"execute": "C:\\Acme\\sync.exe", "arguments": "--daily", "workingDirectory": "C:\\Acme"}]},
 "reason": "replaced by AcmeSync2",
 "categories": {"path": "C:\\Acme\\categories.json", "edited": true},
 "authority": {"transaction_id": "<id or null>", "files": ["paths relative to the private root"]}}
```

`authority` is `null` in plan mode. In plan mode `categories.edited` says whether the console will
edit the file; in apply mode, whether it did. The console never issues a token while the map lists
the task and cannot be edited, so a plan request with `edited: false` means the console found no
entry to remove (or could not read the map, which is its own blocked step); `edited: false` in apply
mode can still happen when the write itself failed. The reply on stdout (at most 1 MB):

```json
{"schema": 1, "ok": true,
 "steps": [{"id": "health", "title": "移出健康监控清单", "target": "task-health.json",
            "status": "planned|ok|failed|skipped|blocked", "detail": "…"}],
 "blocking": [], "notes": []}
```

The console distinguishes five outcomes: `not_configured`, `ok`, `blocked`, `failed` and
`unreadable`. Bad JSON, a wrong schema, an unknown step status, oversize output, a timeout, an exit
code outside 0-2, or an exit code that contradicts the reply (0 with `ok: false`, 2 without
`blocking`) are all `unreadable`, never success. A configured hook whose plan is `failed` or
`unreadable` blocks the delete. The hook's plan output should be deterministic for an unchanged
machine: it is part of the token's fingerprint.

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
