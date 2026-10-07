# Changelog

All notable changes to this project are documented here (Keep a Changelog style).

## [Unreleased]

### Changed

- Set a 64 MiB aggregate storage review threshold while preserving current
  database, transaction files and recovery protection.
- Make storage declarations relative to the complete private companion repository.
  Protect exact database and recovery files, and distinguish current state from
  legacy development copies and helpers awaiting source reconciliation.

- **docs: unify repo structure (Skill Repo Spec v1).** The README was restructured to the house section order with philosophy first, a Chinese counterpart was added section for section, and the mandatory `ROADMAP.md` and `CHANGELOG.md` were written. No functional version bump: nothing about the console changed.

  The repository declared no version number anywhere before this, so `0.1.0` was chosen and the choice is argued in `docs/2026-09-22-spec-adaptation.md` rather than presented as a fact discovered in the code.

  Several requirements of that spec are deliberately not met, every one of them because meeting it would claim something untrue of a local server: no `.claude-plugin/plugin.json`, no Claude Code Skill badge, no `SKILL.md` and therefore no L0 or L1 documentation layer, no load budget workflow, and five of the nine fingerprint topics refused. All of them are recorded in the same adaptation document rather than left as silent gaps.

### Added

- **Task declarations, registration and observations** (`codex/infra-20260917`): component `.console.json` declarations compiled by a read-only planner, a registration transaction that records authority and recovery evidence before changing a task definition, periodic observations, and guarded task control through `task_control` instead of `act.ps1`. See `docs/task-registration.md`.
- **Work platform and console UX** (from `codex/console-ux-20260922`): an integrations registry (`/api/integrations`) with bounded shared source reads, creation preflight (`creation-check`), a deletion flow, manual completion of human todos, optional panels and a preloading script loader.
- **Conversation chain view.** Turn-by-turn chains of saved transcripts with compaction points, real branches and subagents; export any node as Markdown or fork a session at any node into a new transcript file. The transcript logic is the pinned library `convo-chain==0.1.0`; the console keeps routes, token checks and the panel.
- **CI installs the real dependencies.** `tests.yml` installs `convo-chain`, `fleet-guards` and the private `llmcall` at pinned commits (the latter through a read-only deploy key), plus `skill-smith`, `PyYAML`, `pywin32` and the console package itself, instead of letting collection fail on a missing import. Tests that bind a private owner source are marked `owner_integration` from the fixtures they request and deselected on the hosted runner with a notice; locally they still fail loudly when the source is unset.
- **`.github/workflows/dash-guard.yml`.** The style submodule was pinned and its gate had never been wired to anything, so the house rule that published prose carries no en or em dash had never been enforced here. The first tree scan reported 179 lines across 39 files, which is what a gate nobody ran looks like.

### Fixed

- **Task declarations have unique storage ownership.** Legacy development artifacts now use explicit directory namespaces instead of a catch-all date pattern, preserving the combined 16 MiB review budget. Unknown paths still require review, and retirement still requires exact paths and the existing recovery checks.
- **The operation panel no longer covers the conversation chain card.** It was pinned under the header at a hard coded 54px for as long as it had any record, so after a fork its finished record sat over the top of the chain card (58px at desktop width, 137px at phone width, where the header is not sticky at all and page content showed through the gap above it). It is now pinned only while an operation is pending; finished records stay in the page flow. The header and panel heights are measured into `--bar-stick` and `--sticky-stack`, which place the panel under the header and set `scroll-padding-top`, so the chain card and other scrolled-to targets land below whatever is actually pinned. The diagnostics and repository side panels use the same measured offset instead of a fixed 66px.
- **Conversation chain reads after a console restart are pinned by tests.** A restart mints a new page token. The chain, node, Markdown export (the Blob download) and fork requests all go through `api()`, whose one-time `bad token` refresh swaps the token and replays the request; a real browser on a restarted test server showed 403, then `/`, then 200 for the first chain read with no reload. New regression tests drive each path against a fake server that accepts only the fresh token, plus a check that the panel never calls `fetch` directly.
- **179 en and em dashes in prose, across 39 files.** Mostly full width dashes inside Chinese comments and documents, rewritten as commas by `style/tools/dash_guard.py --fix`. The suite was run before and after and reported the same 711 passed and 1 skipped, so the rewrite touched prose only.

## [0.1.0] - 2026-09-11

### Added

- Initial extraction into its own repository: a loopback only console for one Windows machine, covering scheduled tasks, artifact freshness, git repositories, skills, the memory pool, plugins, disk, saved conversations and the LLM call ledger, with maintenance verbs in one closed table and a self check that counts unconfigured sources in its own denominator.
