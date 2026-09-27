# 2026-09-22: applying the house repository spec to a local console

This repository was brought in line with Skill Repo Spec v1, which was written for Claude Code skill repositories. This one is a local HTTP server for one Windows machine. Most of the spec applies as written, a few requirements need adapting, and a handful are refused outright because meeting them would mean claiming something untrue.

This file is the record of those decisions, so the next person auditing this repository against the spec finds a reasoned position rather than silence, and can argue with it. It is dated evidence, not a rule: the invariants live in `docs/changing-this.md`, the version history in `CHANGELOG.md`.

## Refused, because meeting them would be a false claim

**No `.claude-plugin/plugin.json`.** Chapter 1 makes it mandatory so that even a single skill repository stays installable with `/plugin install`. There is nothing here for that command to install: no `SKILL.md`, no skill entrypoint, no agent behaviour at all. A manifest would be advertising rather than metadata. What this repository ships is a server you start with a port and a set of environment variables, and the file that documents those is `scripts/task_console/README.md`, which already has a reconciliation test against the code.

**No Claude Code Skill badge.** Chapter 3 fixes the first badge as `Claude Code Skill` linking to the Claude Code docs. The slot is kept, because a reader should learn what kind of thing this is from the first line of badges, but its content is `Local Console`, linking to `server.py`. A second green badge carries `Platform Windows`, which is the fact a visitor most needs before cloning: the tool reads the Windows Task Scheduler and refuses to start anywhere else.

**No `claude-code`, `claude-plugin`, `claude-skill`, `claude` or `skill` topic.** Chapter 6 calls these part of a nine topic identity fingerprint. Five of the nine are false here. Nothing in this repository integrates with Claude, and `skill` on GitHub reads as "Agent Skill", which this is not. Putting them on would pollute the search results for the repositories where they are true. Of the remaining four, none is honest either: this console makes no model call and contains no prompt or model code. It reads one of its panels from an LLM call ledger written by a different tool, which makes it a reader of that tool's output and not an agent. So the base nine is read here as a base zero, and the domain topics do all the work: `windows`, `task-scheduler`, `powershell`, `dashboard`, `localhost`, `sysadmin`, `observability`, `python`. Topics are a remote setting and are not changed by this commit.

**No `SKILL.md`, and no L0 or L1 layer.** Chapter 11's first two layers are the frontmatter description and the per invocation preamble, both of which exist because a skill pays for them on every turn. A server is started, not invoked, so there is nothing to pay and nothing to budget. L2 through L5 apply and are implemented: `docs/changing-this.md` holds the invariants, the two READMEs are a tour, and `ROADMAP.md` plus `CHANGELOG.md` are the only places a version number appears.

**No load budget workflow.** `style/ci/load-budget` measures what a `SKILL.md` costs to load. With no `SKILL.md` it would report that there is nothing to measure, on every commit, forever. A check that cannot fail is worse than no check, because it teaches people that green means something.

## Adapted, because the intent survives and the letter does not

**No version existed, so one was chosen.** Chapter 7 asks for four copies of the version kept in step and treats the `ROADMAP.md` heading as the source of truth. This repository declared no version anywhere: not in the code, not in a manifest, not in a package file, because there is no package. It is started from a path. So `0.1.0` is a decision taken here, recorded in `CHANGELOG.md` and reflected in the two README badges and the ROADMAP heading, and it is not a number discovered in the source. There are three prose copies and no literal, which is the inverse of the shape the spec assumes, and nothing enforces their agreement yet.

**The `0.1.0` date is the extraction, not the first commit.** The git history here starts on 2026-06-25 under a different tool's name, because this code was carved out of another repository. Dating `0.1.0` from that first commit would claim the console existed three months before it did, so the entry is dated 2026-09-11, when the extraction landed and `.dataclass.json` was written.

**Sections 6, 7 and 8 of the README order are folded into a pointer table.** The spec asks for "Skills at a glance", "How to invoke" and "Example output". There are no skills and no trigger words, and a capability list in the root README is exactly the thing this repository refuses to keep: the verbs live in one closed table in `maint.py`, the environment variables in `scripts/task_console/README.md` with a two way reconciliation test, and a prose copy of either would drift silently while still rendering like an accurate one. So those three slots are one table of pointers, which says where each list lives and what question it answers.

## What the audit found, and how it is closed

**The style submodule was pinned and had never been wired to anything.** `style/` was present and its own gate worked, but no workflow called it and `core.hooksPath` points at the security suite, which is the only place it can point. The first tree scan reported **179 offending lines across 39 files**, nearly all of them full width dashes in Chinese comments and documents. `.github/workflows/dash-guard.yml` now runs `style/ci/dash-guard` on every push and pull request, and the 179 lines were rewritten by `style/tools/dash_guard.py --fix`.

That rewrite touches comments and documents in almost every source file, which is a wide diff for a style rule, so it was bounded by measurement rather than by intent: the suite reported 711 passed and 1 skipped before the fix and 711 passed and 1 skipped after it.

## What was measured, and where

| Claim | How it was checked |
| --- | --- |
| The guards are armed, not merely present | `guards/tools/pii_guard.py --tree` and `guards/tools/data_boundary.py` both run clean in the checkout, and `.dataclass.json` declares the console database with a run shape probe rather than an empty list |
| The style gate had never run | `style/` was pinned, `python style/tools/dash_guard.py --tree` exited 1 with 179 findings, and no workflow referenced it |
| The dash rewrite changed prose only | Full suite before and after: 711 passed, 1 skipped, both times |
| No version exists in the code | A tree wide search for a version literal outside the vendored Tabler bundle returned nothing but PowerShell's `Set-StrictMode -Version Latest` |
| The Chinese README matches the English one | Written section for section against `README.md`, with the badge text localised and url encoded and the anchor set to `#语言` |
