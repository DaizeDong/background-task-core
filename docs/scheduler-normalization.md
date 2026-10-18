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

The journal must use `scheduler.same_candidate` in addition to its exact
Scheduler comparison. That covers a crash after native publication but before
readback or a receipt: the durable prepared candidate can recognize only the
equivalent object. An engine, action, trigger, principal, unknown-field or data
edit cannot be adopted. Receipts continue binding the observed identity, and
effective-input documents remain protected separately from Scheduler output.
