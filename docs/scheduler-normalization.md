# Scheduler preparation and readback

Windows has two serializers here: an in-memory `ITaskDefinition.XmlText` and the
registered task's `Xml`. The latter can omit schema defaults, reorder schema
fields, shorten durations, and add an action context. A prepared definition's
bytes are therefore not a prediction of the registered task's bytes.

The Windows adapter reparses each scoped readback through the same in-memory COM
interface used in preparation. This supplies defaults and schema ordering for
the XML version on the installed OS. There is no Python table of default setting
values and no second task-schema implementation. An explicit-input preservation
check runs before accepting this result: COM must retain supplied fields,
attributes, principal details, comments, unknown content, and ordered action and
trigger collections. Unsupported XML or discarded comments cause a conflict
before publication. The caller's XML is kept unchanged.

The remaining representation rules are deliberately narrow:

* Fixed day/hour/minute/second duration spellings compare by exact decimal
  seconds, only in known Scheduler duration fields. Months and years remain
  distinct, as do duration-looking strings in unknown fields. Long fractional
  values are never rounded.
* Missing `Actions.Context` resolves to the sole principal's explicit ID. An
  explicit context or an ambiguous principal collection is retained.
* XML formatting whitespace and CDATA spelling do not distinguish definitions.
  Leaf data, comments, processing instructions and unknown element order do.

`UseUnifiedSchedulingEngine` is a semantic setting. False and true never compare
equal. The observed Windows registration path changed this flag on a newly
rendered v1.4 definition whose in-memory COM default was false. New definitions
now explicitly request v1.4 with unified scheduling enabled. The registration
plan must display that choice. Existing and passthrough definitions retain their
version and engine setting. A later native non-equivalent change causes conflict;
it cannot be explained away as a missing default. In particular, this observation
does not establish a rule for all v1.2/v1.3 tasks or all Windows releases.

Prepared snapshots carry `prepared: true` and `candidate_identity`. Readback
computes the candidate identity independently through COM while retaining the
original raw XML and observed identity. Only comparison with a prepared snapshot
may use the candidate identity. Two observed snapshots still compare exactly.
The raw XML CAS inside the fixed COM publish operation remains in force. When a
prepared predecessor matches, the adapter supplies the exact freshly observed
bytes to that CAS; it never supplies an unchecked post-write hash.

The journal must use `runtime_xml.same_candidate` in addition to its exact
Scheduler comparison. That covers a crash after native publication but before
readback or a receipt: the durable prepared candidate can recognize only the
equivalent object. An engine, action, trigger, principal, unknown-field or data
edit cannot be adopted. Receipts continue binding the observed identity, and
effective-input documents remain protected separately from Scheduler output.

Unit tests never register tasks. The separate native canary script registers
only a fresh disabled UUID-named task, uses inherited same-user DPAPI, never
starts it, retires that exact name, and verifies absence. If the Scheduler root
folder is unavailable, the result must report registration was not attempted;
in-memory COM tests alone do not establish native registration success.

For a selected legacy binding whose `principal` and `power` are XML-only,
`task_console.export_restore.normalize_legacy_binding(binding, snapshot,
scheduler=...)` returns a deep copy with those two fields made explicit. It
reuses restore's `_principal` implementation. The snapshot must be present,
its definition identity must match the task name and raw XML, and the complete
passthrough and XML-only fields must agree with that observation. The enabled
flag must also match. All other binding fields and both input objects survive
unchanged. Retain the original snapshot as the raw CAS before-image.

The API performs no query or publication and loads no runtime, registry, or
authority. Supply the existing `WindowsScheduler(COMTransport())` for
unregistered preparation when XML omits defaults. Identity and logon type remain
explicit; omitted RunLevel uses restore's existing preparation-gated schema
rule. Missing power flags require explicit boolean values in the prepared XML;
absence never means false. Duplicate groups/settings, unsupported principal
features, malformed power booleans and changed preparation input are refused.
Password modes retain `auth_pending`. No credentials are requested or loaded.

This derives representation from caller-supplied evidence, not proof of freshness
or activation approval. The caller owns the fresh live binding check immediately
before activation and uses the existing registration transaction. A saved
snapshot's self-consistent digest alone cannot prove it is current. Ordinary
`render` still requires explicit principal/power fields and boolean power values.
For preservation checks, compare prepared XML using `runtime_xml.canonical`
after excluding only intended action and controller metadata edits; duration
spelling changes such as `PT7M` to `PT420S` have the existing canonical semantics.
