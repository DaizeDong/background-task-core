# Periodic observation collection

TaskHealthMonitor can refresh the existing console status snapshot on each of its
scheduled runs. `task_console.observations --periodic` performs one pass and exits.
It starts the bundled `collect.ps1 -CaptureOnly` through `llmcall.process`, then
uses the existing observation worker, health evaluator, publication lock and
atomic replacement. It creates no scheduler, service, task list or database.

## Runtime binding

The private collector binding retains schemaVersion 1 and the existing
`private_dir`, `snapshot`, `home`, `tasks`, `local_listeners`, `catalog_request`
and freshness fields. For periodic collection replace its `request` field with
`runtime`, an object containing exactly four absolute paths:

| Field | Owner |
| --- | --- |
| `runtime_config` | Existing task-console RuntimeConfig document |
| `private_root` | Existing runtime declaration resources |
| `state_root` | Existing runtime authority and journals |
| `vault_root` | Existing protected runtime inputs |

`request` and `runtime` are mutually exclusive. A frozen request remains available
for finite offline captures; periodic mode requires runtime authority. There is no
fallback from unreadable authority to an exported request or a global home path.
The snapshot must be inside the private observation directory and outside the
runtime roots. Provisioning and private-data policy remain with materialization
and the existing filesystem/data-directory guards.

Each pass reads current authority under its existing lock before capture, releases
that lock during collection, then reacquires it before publication. Pending
transactions, changes to generation/revision/effective request, changes to the
binding/runtime-config or mismatched worker provenance reject the pass. The previous snapshot
survives a rejected pass. Only `registration.compile_effective` compiles effective
inputs. No adoption, migration or task-control API is called.

Snapshots and publication receipts include `authority_generation`, `input_revision`,
`authority_epoch` and `effective_request_sha256`. The latter hashes sorted ASCII
JSON with standard separators and no newline, matching the finite-export encoding.
These fields describe the authority actually read; they are never fixed release
constants. The existing `tasks` map contains observation policies only. Task scope
comes from the effective declaration bundle.

## Monitor integration

Runtime materialization supplies `TASK_CONSOLE_OBSERVATION_BINDING` and
`TASK_CONSOLE_OBSERVATION_PYTHON`, or the equivalent monitor parameters
`-ObservationBinding` and `-ObservationPython`. The interpreter must be an absolute,
available interpreter with task-console, fleet-guards, llmcall and, when configured,
skill-smith installed. It runs with `-I -B -X utf8`; PYTHONPATH and source-checkout
fallbacks are intentionally unavailable. Resolve and verify this interpreter with
the existing installation resolver before passing it to the monitor.

The normal monitor publishes before its existing health/reporting work. It logs
the fixed publication receipt and returns a failure exit if collection failed.
Missing observation bindings/interpreters produce explicit failures. Notification,
translation, ingestion and watchdog behavior remain the monitor's existing work.

For collection-only verification invoke the same monitor with `-CollectionOnly`.
It publishes one snapshot and returns JSON/exit 0, or a fixed failure/exit 2, before
reading the watch list, creating logs or running any notification/reminder steps.
This mode still writes the bound snapshot: use disposable bindings for source
tests and the normal release authorization process for deployment verification.

The Python entrypoint is also available directly:

```text
<selected-python> -I -B -X utf8 -m task_console.observations --periodic --binding <private-binding> --powershell <absolute-powershell>
```

The shared execution budget is 95 seconds, including the bounded Scheduler pass
and observation worker. Captured input/output is limited to 16 MiB per stream.
Set `max_age_seconds` according to the existing monitor cadence plus tolerated
jitter; its compatibility default of 300 seconds is shorter than an hourly cadence.
`catalog_max_age_seconds` controls reuse of the catalog, not the freshness of
individual native discovery or authentication evidence.

## Catalog inputs

`catalog_request` is passed to the existing `skill_smith.catalog.discover` API.
No discovery implementation is duplicated. Optional `catalog_pins` maps absolute
input filenames to lowercase SHA-256 digests of exact bytes. Include the request
file and all private manifests/evidence files whose provenance must be fixed.
Pins are checked before collection, including cache hits, after discovery, and
before publication. Pin changes invalidate the cache identity. Content changes
without updated pins fail; they do not silently adopt a new source.

The release owner supplies the following existing producer inputs:

| Input | Existing API shape |
| --- | --- |
| `repo_roots` | Array of `{path, namespace?, approved_roots?}`; inspects direct child working copies, including `.git` files |
| `private_bindings` | Absolute JSON path containing `bindings: [{id, kind, server-or-connector, authenticated?}]`; kind is `mcp_binding` or `app_connector` |
| `plugin_descriptors` | Array of `{name, path}` selecting exact `plugin@marketplace` identities; registry coverage remains separate |
| `runtime_discovery` | Absolute JSON path containing `entrypoints: [{source_id, kind, name, client, scope, relative_path?, install_name?, discovered?, compatible?, observed_at?}]` |

`profile_catalog.startup_request` already accepts plugins, private bindings and
runtime evidence. Repository roots can be added to its request explicitly. Names
alone cannot connect native discovery to catalog identities. Match the native
resolved entrypoint path to a unique catalog entry, then retain its exact source
ID, client, scope, relative path and installation name. Preserve capture time.
An enabled entry returned by `skills/list` proves discovery, not skill execution,
compatibility, authentication, or success of any remote workload.

The shared skill-smith producer supports opt-in `native_discovery` with explicit
Codex executable, home and workspace paths. It owns the bounded `initialize` and
`skills/list` acquisition through llmcall.process. Configure its Codex skill roots
for exact returned-path matching, and retain unknown or ambiguous mappings.
The console only invokes the existing producer; it has no separate discovery code.

Set `runtime_discovery_policy.max_age_seconds` for supplied historical evidence.
The original capture time controls expiry; a new catalog envelope does not refresh
old discovery facts. Configure catalog cache age consistently with that policy.
See skill-smith's `docs/catalog-acquisition.md` for the full API. Omitted inputs
remain unchecked; empty arrays must not be used to simulate inspected sources.
