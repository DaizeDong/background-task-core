# Changelog

All notable changes to this project are documented here (Keep a Changelog style).

## [Unreleased]

### Changed

- **docs: unify repo structure (Skill Repo Spec v1).** The README was restructured to the house section order with philosophy first, a Chinese counterpart was added section for section, and the mandatory `ROADMAP.md` and `CHANGELOG.md` were written. No functional version bump: nothing about the console changed.

  The repository declared no version number anywhere before this, so `0.1.0` was chosen and the choice is argued in `docs/2026-09-22-spec-adaptation.md` rather than presented as a fact discovered in the code.

  Several requirements of that spec are deliberately not met, every one of them because meeting it would claim something untrue of a local server: no `.claude-plugin/plugin.json`, no Claude Code Skill badge, no `SKILL.md` and therefore no L0 or L1 documentation layer, no load budget workflow, and five of the nine fingerprint topics refused. All of them are recorded in the same adaptation document rather than left as silent gaps.

### Added

- **`.github/workflows/dash-guard.yml`.** The style submodule was pinned and its gate had never been wired to anything, so the house rule that published prose carries no en or em dash had never been enforced here. The first tree scan reported 179 lines across 39 files, which is what a gate nobody ran looks like.

### Fixed

- **179 en and em dashes in prose, across 39 files.** Mostly full width dashes inside Chinese comments and documents, rewritten as commas by `style/tools/dash_guard.py --fix`. The suite was run before and after and reported the same 711 passed and 1 skipped, so the rewrite touched prose only.

## [0.1.0] - 2026-09-11

### Added

- Initial extraction into its own repository: a loopback only console for one Windows machine, covering scheduled tasks, artifact freshness, git repositories, skills, the memory pool, plugins, disk, saved conversations and the LLM call ledger, with maintenance verbs in one closed table and a self check that counts unconfigured sources in its own denominator.
