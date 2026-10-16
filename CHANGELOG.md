# Changelog

All notable changes to this project are documented here (Keep a Changelog style).

## [Unreleased]

### Guards from the local checkout
- Privacy hooks run from the local fleet-guards checkout named by `git config fleet.guards`, the dash check from `fleet.style`; the `guards/` and `style/` submodules and the pii-guard, dash-guard and fleet-sync workflows are removed. `console_store.py` loads the installed `fleet_guards.runtime` resolver for source checkouts and wheels alike, with no path load from `guards/fleet_guards`.

### Variable references in task declarations
- `argv`, `cwd` and `source_root` may name paths through `%NAME%` environment references; `argv[0]`, `cwd` and `source_root` may start with one instead of an absolute path. The hidden launcher quotes every argument holding a reference and sets a referencing `cwd` through `ExpandEnvironmentStrings`; WScript.Shell.Run expands the command line. A `%` outside a well-formed reference still refuses.
- The runtime configuration accepts an optional `launcher_root_reference`, the private root spelled through an environment variable. Scheduled actions then call `wscript.exe //B //Nologo <reference>\<launcher>`, so neither the action nor the launcher names a machine path, and a reference that resolves to nothing exits 1 instead of opening a modal box. Configurations without it render exactly as before.
- A hidden launcher that expands a reference now exits 1 when the expanded directory or program cannot be used. Before, `shell.Run` raised "file not found" for an unset variable or a missing interpreter, `wscript //B` swallowed the error and exited 0, and the Scheduler recorded the run as a success; the earlier description of variable references (that a run with an unset variable fails on a path that does not exist) held only for failures inside a payload that did start. The launcher now sets a referencing working directory and calls Run under `On Error Resume Next`, quits 1 if either raised, and otherwise quits with the payload's exit code. Launchers without references render byte for byte as before.

### Test suite removed
- Delete `tests/` and the `tests.yml` workflow; CI no longer runs tests. The pii-guard and dash-guard workflows are unchanged.

### Work path slimmed
- An agent work action no longer runs through the task Controller. After the owner accepts it, the console asks the scheduler to run the drainer named by `TASK_CONSOLE_AGENT_TASK_ID` (its name part, for example `agentcenterworktick`) once (`schtasks /run`, on a background thread, no window, failures ignored); the tick's own trigger picks the work up anyway. This removes about seven PowerShell launches from every agent click and every repair submit. Linked task offers still run through the Controller.
- The agent offer needs only `TASK_CONSOLE_AGENT_TASK_ID`; the Controller runtime settings now gate only linked task offers.
- Remove the conversation-context feature: `work_context.py`, `GET /api/work/context`, the 原对话 row button and its detail section. No producer ever wrote the session link it read, so every agent click paid for a full `work-feed` to submit an empty context. Agent work is submitted with no context. The per-process feed cache added for it is gone too.
- The page polls the work feed only while something is moving (a todo action preparing, queued or running, or Agent work queued or running), and stops once the page has seen no change in its state or progress for 30 minutes; the cap is measured client-side from the last observed change, not from the action receipt's timestamp. States that wait for a person (reconcile, task_requested, review_unavailable) are never polled. The repairs view no longer polls at all.
- A repair submit reads the work feed once and passes it to the dispatch; a new order costs one more read after `ensure`. The repairs map and the repair preview use the shared `work` read.
- Shared reads only join an in-flight read of the same generation. Every authenticated POST bumps the generation once when it finishes, whatever its outcome, so the re-read after any write (task, skill, plugin, memory, conversation, repository, work action or repair) never joins a read that started before it; the page's own `loadWork` / `loadRepairs` follow the same rule. A read is no longer discarded and retried up to three times.
- The badge prefetch no longer reads `/api/repos` (a git scan of every repository) or `/api/tasks`; a badge whose source was never requested is hidden instead of showing a permanent 「…」.

### Fixed
- `storage_retention.py` checks its DATA root through the console's one own-repo check (`console_store.outside_source`, which resolves junctions) instead of two inline copies.

### Conversations
- Rename a session in place. Each list row has a pencil after its title, and the title of an open conversation chain is itself the rename control. The inline box takes Enter to save and Esc to cancel; a click outside closes it only when the name is unchanged. Saving reuses the existing rename request and updates the row and chain header immediately.
- AI 起名 in the inline box and in the rename dialog asks the new read only route `POST /api/convo/suggest-title` for one short Chinese name. The route digests only what the person typed (the transcript is streamed once, and only the first and last six typed messages are kept), asks `llmcall` in judge mode, validates and normalises the answer, and writes nothing. The suggestion fills the box for the owner to confirm.
- The delete dialog now shows the session's working directory, short ID and last activity, and warns when the loaded list holds sessions with the same title or the same fork base, naming how many and that only this one will be deleted. Forks are titled `<original> (fork @ <8 chars>)`, so deleting one used to look like a failed delete when its look alike stayed in the list.

### Storage: runtime PRIVATE proof removed
- Delete the runtime storage proof. `console_store.authorize_write` (Guards `authorize_artifact_write`, which ran the PRIVATE visibility proof with dozens of Git and GitHub subprocesses before every database open, event export, conversation cache write, task archive write and settings template) is gone with all its callers, and the tools' `private_data_root` proof (`prove_private_companion`, called up to four times per scheduled backup) is now `data_root`, a pure path check. The category-map and llmcall chain writes added with direct companion storage (`authorize_private_write`, which also called `prove_private_companion`) use `console_store.check_write_path` instead: Guards lexical and link checks plus the source-tree refusal, with no Git. Resolution keeps the companion discovery order, the NO_COMPANION state and the refusal of any destination inside this payload's own source tree. Keeping DATA out of public repositories stays with the commit and push hooks and CI.
- The wheel no longer bundles `storage.contract.json`; nothing reads it at runtime. The contract file and its CI checks are unchanged.

### Configuration and storage admission
- Keep generated PowerShell settings examples byte-identical on Windows checkouts, account for native configuration overrides in self-check, and align scheduled-backup unit fixtures with the current authorization boundary.
- Raise the aggregate storage review threshold from the earlier 64 MiB development threshold to a 256 MiB operating cap, with explicit database, event, recovery and registration allowances. Retained historical artifacts still count; current databases, transaction files and recovery state remain protected, and core history is not truncated to meet the cap.
- Require this source payload's pinned resolver and source-owned PRIVATE versioned admission for database overrides and durable event exports. Remove ancestor and vendored resolver fallback.
- Declare SQLite coordination sidecars as exact transient leaves while retaining their transaction recovery obligations; the main database remains versionable.
- Add portable native environment initialization and a read-only local doctor that verifies the declared database version and table columns; align English/Chinese usage and completed UI split documentation.
- Preserve artifact admission in installed wheels through `fleet-guards>=0.2.1` and a build-time copy of the sole source storage contract. Missing package policy fails visibly.


### Changed

- Move the CI skill-smith pin from ff47b5f (0.1.4) to 7ec8280 (0.2.0) and the convo-chain pin from e4e0d2f to 36be94d (both 0.3.0), the versions the runtime generation ships beside the console, so CI tests what production runs.

- Move the CI llmcall pin from 98af777 (0.3.0) to a7b8d8f (0.3.1, current llmcall main).
  `llmcall.process` is byte-identical between the two, so `llmcall>=0.3.0` stays the
  bound; 0.3.1 also declares `fleet-guards>=0.2.1`, which the CI fleet-guards pin satisfies.

- Require `llmcall>=0.3.0` and move the CI pin from 39dd746 (0.2.0) to 98af777
  (0.3.0, Windows Job Object ownership of every client process tree). The console
  uses only `llmcall.process` (`execution_scope`, `current_control`,
  `remaining_timeout`, `run`, `resolve_context`, `CallContext`), whose signatures
  are unchanged between the two, so a 0.2.0 install no longer satisfies the bound.

- Make storage declarations relative to the complete private companion repository.
  Protect exact database and recovery files, and distinguish current state from
  legacy development copies and helpers awaiting source reconciliation.

- **docs: unify repo structure (Skill Repo Spec v1).** The README was restructured to the house section order with philosophy first, a Chinese counterpart was added section for section, and the mandatory `ROADMAP.md` and `CHANGELOG.md` were written. No functional version bump: nothing about the console changed.

  The repository declared no version number anywhere before this, so `0.1.0` was chosen and the choice is argued in `docs/2026-09-22-spec-adaptation.md` rather than presented as a fact discovered in the code.

  Several requirements of that spec are deliberately not met, every one of them because meeting it would claim something untrue of a local server: no `.claude-plugin/plugin.json`, no Claude Code Skill badge, no root `SKILL.md` or root L0/L1 documentation layer (the auxiliary automation-management skill is documented separately), no load budget workflow, and five of the nine fingerprint topics refused. All of them are recorded in the same adaptation document rather than left as silent gaps.

### Added

- **Task declarations, registration and observations** (`codex/infra-20260917`): component `.console.json` declarations compiled by a read-only planner, a registration transaction that records authority and recovery evidence before changing a task definition, periodic observations, and guarded task control through `task_control` instead of `act.ps1`. See `docs/task-registration.md`.
- **Work platform and console UX** (from `codex/console-ux-20260922`): an integrations registry (`/api/integrations`) with bounded shared source reads, creation preflight (`creation-check`), a deletion flow, manual completion of human todos, optional panels and a preloading script loader.
- **Conversation chain view.** Turn-by-turn chains of saved transcripts with compaction points, real branches and subagents; export any node as Markdown or fork a session at any node into a new transcript file. The transcript logic is the pinned library `convo-chain` at the revision declared by the current dependency contract; the console keeps routes, token checks and the panel.
- **CI installs the real dependencies.** `tests.yml` installs `convo-chain`, `fleet-guards` and the private `llmcall` at pinned commits (the latter through a read-only deploy key), plus `skill-smith`, `PyYAML`, `pywin32` and the console package itself, instead of letting collection fail on a missing import. Tests that bind a private owner source are marked `owner_integration` from the fixtures they request and deselected on the hosted runner with a notice; locally they still fail loudly when the source is unset.
- **`.github/workflows/dash-guard.yml`.** The style submodule was pinned and its gate had never been wired to anything, so the house rule that published prose carries no en or em dash had never been enforced here. The first tree scan reported 179 lines across 39 files, which is what a gate nobody ran looks like.

### Fixed

- Use the packaged Guards resolver for observation bindings so the installed worker imports successfully and rejects output inside its source or package directory.
- **Task declarations have unique storage ownership.** Legacy development artifacts now use explicit directory namespaces instead of a catch-all date pattern, preserving the combined 16 MiB review budget. Unknown paths still require review, and retirement still requires exact paths and the existing recovery checks.
- **The operation panel no longer covers the conversation chain card.** It was pinned under the header at a hard coded 54px for as long as it had any record, so after a fork its finished record sat over the top of the chain card (58px at desktop width, 137px at phone width, where the header is not sticky at all and page content showed through the gap above it). It is now pinned only while an operation is pending; finished records stay in the page flow. The header and panel heights are measured into `--bar-stick` and `--sticky-stack`, which place the panel under the header and set `scroll-padding-top`, so the chain card and other scrolled-to targets land below whatever is actually pinned. The diagnostics and repository side panels use the same measured offset instead of a fixed 66px.
- **Conversation chain reads after a console restart are pinned by tests.** A restart mints a new page token. The chain, node, Markdown export (the Blob download) and fork requests all go through `api()`, whose one-time `bad token` refresh swaps the token and replays the request; a real browser on a restarted test server showed 403, then `/`, then 200 for the first chain read with no reload. New regression tests drive each path against a fake server that accepts only the fresh token, plus a check that the panel never calls `fetch` directly.
- **179 en and em dashes in prose, across 39 files.** Mostly full width dashes inside Chinese comments and documents, rewritten as commas by `style/tools/dash_guard.py --fix`. The suite was run before and after and reported the same 711 passed and 1 skipped, so the rewrite touched prose only.

## [0.1.0] - 2026-09-11

### Added

- Initial extraction into its own repository: a loopback only console for one Windows machine, covering scheduled tasks, artifact freshness, git repositories, skills, the memory pool, plugins, disk, saved conversations and the LLM call ledger, with maintenance verbs in one closed table and a self check that counts unconfigured sources in its own denominator.
