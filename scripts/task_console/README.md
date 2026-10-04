# task-console

A local, operable maintenance console for one machine. Scheduled tasks, artifact freshness, git
repositories, skills, the memory pool, plugins, disk, saved conversations, and the ledger of a
headless judgment primitive, on one screen, with a button on every row that needs one.

It exists because a report that only tells you something is dead makes you go and find it
yourself. This lets you act on what you just read.

Every panel obeys the same rule: **"nothing wrong" and "never checked" are different outputs.**
A source that cannot be read says so, in its own colour, instead of rendering an empty green
table. The self-check strip at the top exists to make that visible for the page as a whole:
it lists every path the console tried, whether it got there, and how fresh it was.

## Run it

```
python server.py                 # opens http://127.0.0.1:8787/ in your browser
python server.py --port 9000     # different port
python server.py --no-browser    # start the server, open it yourself
```

Windows only. It reads the Windows Task Scheduler; there is nothing to read anywhere else.

## What is on the page

| Panel | Reads | You can |
|---|---|---|
| Self-check | every configured path | see which sources are `ok` / `stale` / `missing` / `unset` |
| Artifact freshness | the health manifest plus each declared artifact | see five states, worst first |
| Repositories | every git repo under one root | spot unpushed commits and dirty trees; run `fetch` |
| Tasks | the Windows Task Scheduler | run, stop, enable, disable, retire; delete one after a bound preview; file a repair order that an agent diagnoses |
| Skills | a skills directory | archive one out of the way, or restore it |
| Memory pool | a memory directory | see index headroom and broken links; archive an entry |
| Plugins | `claude plugin list` | enable or disable one |
| Disk | a plugin cache and a session directory | delete abandoned clone staging directories |
| Conversations | Claude Code transcripts under `TASK_CONSOLE_SESSIONS` | search and page through sessions by storage location; rename, move or permanently delete a confirmed session; browse chains, export Markdown, and fork from a node |
| Calls | the LLM-call primitive's append-only ledger | see today's and this week's usage, which rung answered, how long the degraded stretches were, and reorder the fallback chain |

The chain logic (the transcript index and its cache, the id shape gate, chain, node, Markdown
export, fork and file operations) lives in the `convo-chain` library
([DaizeDong/convo-chain](https://github.com/DaizeDong/convo-chain)), which is a library
dependency, not an observed producer: `pyproject.toml` pins its version the way it
pins `llmcall`, and `server.py` imports it in process so its index cache lives in the console
process. The library reads no environment variable; the `/api/convo/*` routes read
`TASK_CONSOLE_SESSIONS` and pass it in as `root`, map the library's `ConvoChainError.code` to a
400 (or 409 for a busy/conflicting edit), and answer an unset root as NOT CHECKED on reads
and as a 400 `unavailable` on writes. Token, host and query checks stay in the console. The rule for which user lines a person
actually typed (`typed_text` / `looks_injected`) also lives there, and the session list imports it
rather than keeping a second copy; so does the work-context reader, which also resolves the session
file through the library's `locate`. The integrations view lists the library as an informational
row under library dependencies; it has no read endpoint of its own and is not a health check. The
dependency is hard: `server.py`, `convos.py` and `work_context.py` import it at module level, so a
missing library stops the console from starting rather than degrading one panel. Before a runtime
generation built from this version is installed, the runtime lock must carry the `convo-chain`
wheel row; the package is not on PyPI, so no other install path resolves it.

Project headers describe physical storage directories. Search runs before pagination, and an
expanded project loads the next page near its footer; a failed page keeps existing rows and offers
retry. Dragging onto a project or the destination tray performs the same move as the keyboard
accessible Move button. The move preserves transcript bytes and session IDs, carries the complete
sidecar directory, and updates both native indexes. Active writers, collisions and unsafe paths
are refused. Interrupted edits keep a recovery journal under the configured session root and are
recovered before another mutation. Fork requests retain a retry identity until the result is known.

The list exposes View and Delete; More actions holds Rename, Move and file metadata. Opening a
session replaces the list, and returning restores its scroll position without reloading it.
Delete first requests `/api/convo/delete-plan` and shows the file count, bytes and native index
entries. `/api/convo/delete` requires explicit confirmation, the unchanged preview fingerprint and
a retry request ID. Deletion permanently removes that transcript and its associated directory.
The library preserves unrelated sessions and refuses stale previews or active writers. Lost
responses retry the original request; `cleanup_pending` keeps the retry control visible. Read-only
mode refuses both deletion endpoints. Message content appears before collapsed technical metadata.

The conversation chain is read from the whole transcript, not from `parentUuid` alone. Parallel
tool calls make a node look like it has two children, and those are one reply, not a branch. A
compaction boundary has no parent, so the walk continues into the earlier history through the
boundary's recorded predecessor, and when it has to guess it says so. A message written after a
compaction can also hang directly on a message the compaction kept; the chain then shows the
boundary and summary at that step, so it agrees with what a fork would hand the model. A fork
writes a new session file next to the source, holding only the context the model had at that
node, and gives back a `claude --resume` command whose `cd` is the directory that owns the
project folder the file was written to (a session that changed directory midway would otherwise
resume in a folder that does not have the file). It never modifies the source file, never
overwrites an existing file, refuses to write inside a git worktree, and refuses a node whose
walk cannot reach the compaction summary rather than write a context the model never saw. Line numbers in these responses are physical
lines in the file (`lineIndex`, blank and unreadable lines included); byte positions are
`byteOffset` / `byteLength`.

On the page, the chain is a drill-down inside the conversations view, not a new sidebar entry:
clicking a session opens it below the list (the row still has a button that copies the path),
and `#convos/<session id>` links straight to it, with `/<subagent id>` and `/leaf=<uuid>` for a
subagent transcript or a non-default branch, so Back returns to the previous branch. Turns start
collapsed and a turn's steps are only rendered when it is expanded, because a long session runs
to tens of thousands of nodes. A compaction boundary is a divider row showing the token counts
before and after; clicking it expands the summary the model continued from. Where the file
branches, the node carries a ⑂ menu of the alternatives, and choosing one reloads the chain along
that branch. A subagent opens read-only with a link back to the node it was opened from; it can
be exported but not forked. The page's refresh button re-reads an open chain too, keeping the
selection, the expanded turns and the range. The chain list is a list box: with focus on it the arrow keys, Home and End
move between nodes and Enter or Space expands one; the start and end of the export range are set with their buttons. Esc
steps back one layer from anywhere on the page: an open branch menu first, then a subagent back to its main conversation,
then the chain back to the list. The 导出与新建会话 panel is open when a chain opens and can still be folded. Rename, move,
the file facts and 删除会话 sit in the 更多操作与详情 menu; read warnings stay visible outside it. 新建会话 asks in an
in-page confirmation first. With only a start
set, the range runs to the end of the chain. Export is a GET that returns the Markdown inside
JSON and the page saves it itself, because the token only travels in a request header; one
export is capped at 16 million characters and the file says where it was cut. The panel is optional (`static/panels/convchain.js`): if it fails to load, its card says
so, the rest of the console keeps working, and a session row falls back to copying the path.
In read-only preview the fork button is disabled with the preview's reason, since the server
refuses every POST there; export is a read and keeps working.

Five states, not two, for freshness. `LastTaskResult` is an HRESULT, not an exit code: one value
means "currently running" and another means "has never run". Treating non-zero as failure marks
healthy tasks red, so `up` / `grace` / `down` / `never` / `running` / `paused` / `unknown` stay
separate, and `unknown` is drawn so it can never be mistaken for `up`.

The call view answers "today" by the calendar day, not by a rolling 24 hours. Asked at eight in
the morning, a rolling window folds in yesterday afternoon, so the number is larger, looks
healthier, and matches nobody's idea of today.

Records written before the ledger carried timestamps cannot be placed in any window at all. They
are reported as excluded, with their count, rather than quietly counted as in-window or silently
dropped: "no calls today" and "a hundred thousand calls that cannot be dated" are different
findings and the page draws them differently.

Served, failed and skipped are three separate columns for one rung. A rung is skipped when that
call's chain did not contain it, which is neither a success nor a failure; before it was counted on
its own it was indistinguishable from a rung that simply never got its turn.

Everything this module returns ends up inside a JSON response, so none of it may hold a value
`json.dumps` cannot take. That is asserted for every public return at once rather than function by
function, because the one that got through was a `Path` on a resolution step that answered `None`
until the day the companion repo was configured: a check written per function only ever covers the
functions someone thought of, and this one was invisible on any machine where the thing worked out
to nothing. The test deliberately configures the companion first, since that is the only state in
which the bug exists.

Who made the call is recorded as a bare script name, never a full path. The ledger sits outside
every repository, but this page serves it over HTTP, and a full path carries a home directory for
no gain in what it tells you. Three states stay separate: a name, "could not be inferred" (an
embedded call with no main script), and "older than this field" (every record written before it
existed). The last two look identical once merged, and merging them turns "the feature just landed"
into "inference is failing".

Degradation is clustered, not spread out. An average dilutes one bad afternoon into a harmless
looking fraction, so the page also draws consecutive runs: how many calls in a row landed on the
same rung.

A fresh artifact does **not** clear a bad exit code by default. Log-shaped artifacts are usually
written on the crash path too, so a task can fail for days while its artifact stays fresh. Only an
artifact declared `artifact_written_only_on_success` earns that power.

## Feed the history first

The run-history panels read a database that nothing fills on its own. Until `console_ingest.py`
has run, those panels are empty, and they say so rather than drawing a flat line.

```
python console_ingest.py --days 60      # first time: pull in what the log still holds
python console_ingest.py --skip-runlog  # the cheap half: health observations only
python console_ingest.py                # the full pass, including run events
```

**Running it on a schedule is not tuning, it is the whole point.** The Windows Operational log is
a circular buffer: measured here at roughly 635 events an hour against 64 MB, which is about five
days before the oldest records are overwritten. Anything not ingested inside that window is gone,
not late, and no later run can recover it.

Drive the two halves separately. Folding them into one caller has been tried and it killed that
caller on every run for dozens of consecutive runs: the full pass hands `runlog.ps1` a 900 second
budget, so any caller whose own execution limit is lower than that will be cut off before the inner
budget is ever reachable, forever, without either number looking wrong on its own.

How often to run each half is a property of the machine and is deliberately not stated here: this
file has carried a wrong claim about it twice, in opposite directions. To find out whether ingest
is currently keeping up, read the ingest freshness the page computes from the data's own
timestamps, not a sentence anyone wrote down.

Reading that log costs around 109 seconds end to end, which is why it is paid once an hour by
something nobody is waiting on rather than on a page load. The ingester is the only writer, takes
`BEGIN IMMEDIATE`, and on a source it cannot read it records a failed run and exits non-zero
instead of writing a partial pass: a half-finished ingest and a successful one would otherwise
produce the same empty-looking chart weeks later.

## Configure it

Copy `categories.example.json` to `~/.task-console/categories.json` and replace the synthetic
names with your own tasks. Everything else is optional.

Each category has `name`, `tasks` (Windows task names), and an optional category `desc`.
`taskDesc` maps task names to legacy `title：summary` strings. The task row's `desc` still uses
that override first, then the Scheduler description, then `null`; overview and review keep
that contract.

An optional `taskInfo` object maps task names to structured descriptions:

| Field | Meaning |
| --- | --- |
| `title` | Short task title |
| `summary` | What the task does |
| `cadence` | Human-readable frequency; the actual Scheduler plan remains separately visible |
| `status` | Last reviewed situation, separate from the live execution state |
| `advice` | Suggested next action |
| `verdict` | `keep` 保留, `fix` 要修, `urgent` 急修, `adjust` 调整, `decide` 待定, `remove` 可删, `disabled` 保持停用 |
| `asOf` | Date of the status/advice review, conventionally `YYYY-MM-DD` |

All fields are optional strings. A broken entry and a missing entry are shown differently,
because they call for different fixes:

- A non-object entry, or an entry with any recognized field whose value is not a string, is ignored in full. Unless another category supplies a valid entry for it, the row gets `infoInvalid` set to what is broken (`整条不是一个对象`, or `这些字段不是文字:` and the fields), shows 说明写法不对, and repeats the reason in its detail, because the page warning that names the task sits on the diagnostics page, which the automation tabs never show. A non-object `taskInfo` map is reported by category name, since its tasks cannot be known.
- Unknown keys are dropped from the entry, and a page warning names the task and the keys, so
  a misspelt field such as `asof` does not silently read as an empty one.
- An unknown `verdict` is moved to `verdictUnrecognized` with its original text, the row shows
  建议无法识别 and counts under 无法识别, and a page warning names it. It never reads as 未评估.
  An empty or whitespace-only `verdict` counts as not written: 未评估, no warning.
- A `taskInfo` key that matches no Scheduler task is named in a page warning. Otherwise a misspelt task name would leave the real task quietly 未评估 while a misspelt field name is reported.

The page keeps its own copy of the verdict list. `tests/test_task_info.py` checks it against the server's, and a value only the server knows still shows 建议无法识别 rather than 未评估.

Strings are kept verbatim, including empty strings; dates are not parsed. Each task row
exposes the cleaned object as `info`, or `{}` when absent or ignored. If a task has metadata
in several categories, the last valid entry replaces the previous one in full. Category
membership still uses the first assignment and reports duplicates.

All three automation tabs, and the day timeline on the run tab, prefer a nonblank `info.title`, then the first clause of `desc`, then the task name. The summary prefers nonblank `info.summary`, then the rest of `desc`; when `info.title` is set and `desc` has no separator, the whole `desc` is the summary, so a description never disappears behind a title. The clause separator is the first `。`, or `：` / `:` not followed by a digit, slash or backslash, which keeps clock times, drive letters and URLs whole. Displayed titles and summaries are trimmed. The Windows task name remains visible, and on the timeline it moves into the tooltip.
Search includes the title, summary, advice and task name, plus legacy descriptions and categories; on the sync and backup tab it also matches the card titles.
Advice filters include counts after the other filters, with missing verdicts marked 未评估, broken ones 无法识别, and rows whose task data is still loading 建议未读取 (that option is listed only while such rows exist).
Category headers show counts for the whole category. A missing verdict never implies 保留 or 保持停用. Verdict chips have a dashed outline so a recommendation never looks like the live state chip next to it.
These editorial fields never change task actions, execution status or health judgments.

A disabled task shows 不会运行 as its next run (已停用，不会运行 in full). The Scheduler keeps computing a next time from the triggers of a disabled task, but it will not run, and the timeline already skips it. Disabled tasks sort after every task that has a next run.

The sync and backup tab reads its run records from a captured snapshot (`TASK_CONSOLE_STATUS_SNAPSHOT`), not from a live query. The toolbar says when the snapshot was captured and how long ago; once it is more than 24 hours old (both pipelines run at least daily) the time turns to the warning colour and a notice above the cards says the records stopped at that moment. Issue reason codes are shown in Chinese with the code in the tooltip, and stay searchable by either; a code without wording is shown as it is.

The run table defaults to task, state, schedule, next run and actions, plus selection checkboxes.
The 显示列 menu lists every other column in two groups: 常用 (the operational columns) and 保障配置 (补跑, 重试, 超时限制, 产物, 备份, 监控), each column ticked on its own; there is no separate safeguards toggle any more. Saved column choices persist locally, and a choice saved by the old toggle is merged into them once. The default table fits a desktop
panel and scrolls with the page, with its header staying under the top bar; only when many extra columns make it wider than the card does it scroll sideways inside the card. Below 768px each task becomes a stacked card (title, health chip, next run, actions) with no sideways scrolling.
Health comes first on all three automation tabs. Every task row on 任务开关, the pipeline cards and the 状态 column of 运行详情 show the same chip: × 失败 · 退出码 N, ! 有警告, ✓ 正常, Ⅱ 已停用 or ? 未知. Exit codes read in decimal; common Windows result codes are named instead (for example 正在运行 or 上一次运行还没结束，这次没有启动) and the raw hex code is only in the tooltip. The scheduler state (已启用, 正在运行) is a line of small text under the chip. A failing row has a red edge and comes first in its group on 任务开关. The run table sorts by severity by default (失败, 有警告, 正常, 停用, then title); the card header says 按严重程度：失败在前, or names the column the owner sorted by, with a 按严重程度 button to go back. The chosen sort is remembered in this browser. While the task list is being read the four counts show …, and a red ! with the reason in the tooltip if the read failed.
Each row has two labelled actions, 运行 and 停用 (or 启用, or 停止 while running), and a ⋯ menu with 启动方式, 修复 and, after a separator, 删除; 停用 and 删除 turn red only when hovered. Rows are about 56px tall, so ten fit on a 900px screen. Next runs read as relative times (2 小时后) with the full time in the tooltip, and whole-minute schedule times drop the seconds (每天 22:40). The verdict badge sits right after the title.
An open task detail starts with the reason when the task failed or has issues: a red (or amber, for warnings only) strip with 上次运行, the exit code and its meaning, and the first issue, next to 修复 and 运行一次. Then come 用途, 频率, 现状 and 建议, the run facts, and last a 技术细节 block with the command, working directory, identity and battery settings. Return code counts read 0 ×7755 · 1 ×50. On 任务开关, 详情 opens the same detail in place under the row and does not leave the tab or touch the run table's filters; Esc closes it and Enter in its search box opens the only remaining task. A link from another page (a lamp, a problem) still opens the run table, filtered to that task, with the highlight and the detail on the exact task even when other names contain it.
The timeline's 缩小 and 回到整天 are disabled at the full day (已是整天) and 放大 at the five-minute limit (已放到最大). Clicking a timeline track (moving less than 4px) opens that task in the run table; dragging still pans. Timeline rows are 20px tall.
Each 同步与备份 card starts with one combined verdict, such as × 任务上次运行失败 · 备份异常 · 记录停在 9 天前. Run modes and results read in Chinese (正式写入, 预览, 部分可用). The issue list groups its rows by reason under a header with the count, largest group first, and still lists every row; below 768px the steps table stacks.
The four counts above the run table (任务, 失败, 有警告, 停用) are filter buttons. Pressing 失败 keeps only the tasks whose last run failed, 有警告 the tasks with at least one warning, and 停用 the disabled tasks; the pressed one has a cyan outline, pressing it again (or 任务) shows every task, and the clear-filter button resets it too. Each number is the count of rows that button leaves, so 有警告 counts tasks rather than warning lines (the line total is in its tooltip). A button whose count is 0 is greyed out with the reason, and all four are grey until the task list has been read. Pressing 停用 unticks 隐藏停用, which would otherwise empty the table. The exported page data records the choice as `filters.status`.
The header checkbox selects or clears every task in the current list and shows a partial mark when only some are selected. The bulk bar (已选 N 个任务, with run, enable, disable and 取消选择) appears only on the run table; switching to another view hides it and keeps the selection. Each task title is a button, so Tab reaches a row and Enter opens or closes its detail like a click on the row; a click that ends a text selection in a row does not toggle it. An open detail, here and in the model calls table, has a 收起 button at its top right.

The console has no keyboard shortcuts. Two conventions apply on every page. Esc steps back exactly one layer of what is on screen: in a search box it first clears the text (the list re-filters) and a second Esc leaves the box; elsewhere it closes the open task detail and then clears the task selection on the run table, closes the detail opened in place on 任务开关, closes the open call on the model calls page, and backs out of the conversation chain as described above. When nothing is left to close on a view that was reached by a drill-down, Esc goes back to where the owner came from, once. It never acts on a view that is not shown, and open dialogs and menus handle their own Esc.

The frame says where the trouble is and how to get back. The sidebar uses one icon per section, and four sections carry a badge: 工作记录 counts model call chain failures in the last 7 days, 自动化 the tasks whose last run failed, 资源 the repositories that need attention plus storage near its limit, and 诊断 the objects in its problem list (the same number as the unfiltered list). The matching tabs (模型调用, 运行详情, 代码仓库, 存储清理) carry their own share. Red means failures and amber warnings; a badge is hidden at 0, shows … until its first read returns, and shows ? in a dashed outline when its source could not be read, with the reason in the tooltip. After the opened view has loaded, the badge sources that have not been read yet are read once. The page title names the tab, as in 自动化 · 运行详情. Clicking a section in the sidebar returns to the tab last used in it (remembered in this browser only), tabs are larger and wrap on a phone instead of being cut off. The header holds only 刷新, with 刚刚刷新 or N 分钟前刷新 next to it, and a ⋯ menu with 导出数据 and the 外观 choice; on a phone both buttons sit in the top brand row. While a refresh runs its icon spins and it is disabled. Cards no longer repeat the refresh button: the header one reruns every read of the view. Only the session storage card keeps 重新扫描, which rescans just the selected library. A view opened by a drill-down (a problem's 查看详情, a tile, a lamp, an overview link or an integration's 查看) shows ← 返回 <origin tab> at the right of its tab bar; views reached from the sidebar, a tab or the address bar do not. An open conversation shows ‹ 会话列表 / <conversation name> at the left of its card title; that link, Esc and the browser Back button all close it and put focus back on the row that opened it.

Each page puts what needs the owner first. 工作台 starts with a summary strip of technical trouble and a card for Agent work that failed, stalled or was blocked (both stay hidden until they have something to show), then 待办事项 and Agent 正在做 side by side, and Agent 完成记录 across the full width at the bottom; a phone shows one column in the same order. The 需要你确认 strip appears only when an approval source is connected; there is no permanent placeholder for it. 模型调用 shows 用量, 失败与回退 and 调用记录 first, and 服务统计 and the rarely changed 调用顺序 editor last. 能力与配置 has the single search described below and a text anchor bar 技能 · 插件 · 记忆 · 目录 instead of icon buttons, and its sections run 技能与插件, Claude 记忆与索引, 资源目录. 存储清理 shows 系统存储 (the disk) first, then Codex 存储, whose name appears once as the card title with the total after it (a trailing + means some items were not fully scanned), then the session storage cleanup.

Every dialog has the same shell: title on the left and ✕ at the top right in a header that stays put, the content, and a footer that stays put with the secondary button first and the primary last on the right. The primary is filled cyan, a destructive one filled red, and both fall back to the grey disabled style when they cannot act. Clicking the dimmed backdrop or pressing Esc closes a dialog, except while its request is in flight (nothing closes it then) and while a field holds text the owner typed (only Esc, ✕ and Cancel close it, so a stray click cannot discard the text; a text selection dragged out onto the backdrop does not close it either). Focus goes back to the control that opened it. Dialogs with a field are forms: Enter in the field presses the primary button, and only while that button is enabled. The skill and plugin delete dialog shows the exact name as a copyable chip and moves the cursor into the name field once the preview arrives; in the task delete dialog Enter in the reason runs 生成删除预览 (disabled until a reason is written), the name field stays disabled until a usable preview exists, and Enter there deletes only when the name matches. Blocking reasons and warnings sit right under the task name, and the footer keeps 确认删除 in view. Enter in the repair note files the order when 提交修复工单 is enabled; in the conversation move dialog Enter on the target list moves it; permanent conversation delete still needs a click. Each primary button and the line under its field say why it is waiting, for example 名称还差 3 个字符, 名称不一致, 先生成删除预览, 预览列出了不能删除的原因, 工单已提交给 Agent, 工作服务不可用 or 名称没有变化; a repair note locked to the earlier request says 备注沿用上次请求，不能修改. The publish review opens with focus on 取消, and Enter in the commit message publishes only after the owner has clicked into that field and only on a single, not held, key press.

There are no browser confirm() or alert() boxes. Stopping tasks (停用) asks in an in-page dialog that lists the task titles with the machine name in small text, and so do deleting session files (count, size and the first eight paths), starting a new session from a chain node, and the plugin cache cleanup, whose button reads 清理 N 个临时目录 and whose dialog says the backend picks the directories by name shape and age and that only the cache total is known. Esc, the backdrop and 取消 all answer no, and nothing is sent until the owner confirms. A partial session-file delete is reported in a status line on the cleanup card, and a multi-repository publish failure in the repository result panel, where both stay. Dropping a conversation on another project opens the move dialog with that project selected instead of moving at once. Each conversation row has one … menu (rename, move, copy the file path, the file facts, then a separate red 删除); the title opens the conversation, and rows no longer carry a trash or eye button. Menus close on an outside click or Esc, only one is open at a time, and an open one is reopened after the list repaints. Enter in a search box commits it: the model call and conversation searches run at once instead of after the typing pause; the run table, technical problem and work searches open the result when exactly one matches; the repository search selects the first matching repository; the subagent filter opens the first match. With no match, or several where one is required, Enter does nothing.

A control that cannot act right now is grey, half transparent, has a not-allowed pointer and does not change on hover, on every page and in both themes. Its tooltip names the action first and then the reason, as in 运行一次（不可用：请先启用）, so a row of icon buttons stays readable by hovering. In read-only preview every write button reads `<action>（不可用：只读预览，请用正式控制台）`; while a write is pending, the other write buttons are locked as before and read `<action>（不可用：等待「<current operation>」完成）`, then get their own tooltip back. Disabled and read-only form fields have a grey background too, so the repair note locked to an earlier request no longer looks editable. Viewing a repository's git status is a read: it creates no 最近操作 entry and does not lock the page, although read-only preview still cannot send it because that server refuses every POST. The clickable repair progress chip has a solid outline and a trailing ›; dashed chips are advice or unchecked states and do nothing.

Every list page has one clear-filter button that stays in place: grey with 当前没有生效的筛选 when the filters are at their defaults, and 清除筛选（N 项） when N filters are active. Clicking it resets the filters and moves focus to that page's search box. On 能力与配置 one search box at the top filters the skills, the plugins and the resource catalog together; its clear-filter button clears the whole page, while the buttons inside the 技能与插件 and 资源目录 cards clear only their own drop-downs, leave the shared search alone and move focus to their first drop-down. The model call order editor follows read-only preview too: the up and down buttons, dragging, 放弃修改 and 保存顺序 are all disabled there, and in live mode an 未保存 chip sits next to 调用顺序 while the draft differs from the saved order. In the session-file cleanup list, 清空选择 is disabled when nothing is selected and 全选 when every listed file already is.

Feedback toasts sit at the bottom right in four tones: success (green edge, gone after 3 seconds), information, warning, and failure (red, stays until dismissed). Clicking a toast or its ✕ closes it, except while text inside it is selected so an error can be copied; hovering or focusing it pauses its timer. At most three are shown, and a new one pushes out the oldest that is not a failure. Esc does not touch toasts. The 最近操作 panel collapses to one line, 最近操作 N · <last result>, five seconds after every operation has settled; clicking that line expands it again. It stays open while anything is pending or when the last result failed. Its ✕ hides it until the next operation starts and is disabled while an operation is pending.

Times use one style everywhere they adopt the shared formatter: 刚刚, N 分钟前/后, N 小时前/后 (whole hours, under 48), N 天前 within a month, otherwise MM-DD HH:mm with the year added when it is not this year; hovering shows YYYY-MM-DD HH:mm:ss. Numbers are grouped by thousands and sizes continue past M to G. Loading, unchecked, zero and broken have four fixed looks: a grey spinner with 正在读取…, a dash in a dashed box titled 未检查, a faint 0, and a red ! (or a red-edged block with <what>读取失败 and 重试) whose raw error code is only in the tooltip. A filtered-out empty list says so and offers 清除筛选. Text sizes are 20, 14, 13 and 12 px only; data tables share one header style and every status or tag chip is one 22px box. Icon buttons have one size. 停用 uses a switch-off icon, 修复 a first-aid kit, and the timeline zoom buttons are − and + icons.
Full descriptions, frequency, reviewed status/date and advice precede the technical detail fields.
Use only synthetic data in shared examples; keep actual descriptions in private machine config.
The checked-in categories example is generated by `tools/make_fixtures.py` and checked against
the generator by the fixture boundary gate. Regenerate it from the repository root with
`python -B tools/make_fixtures.py`.

| Variable | What it points at | If unset |
|---|---|---|
| `TASK_CONSOLE_CATEGORIES` | your category map | `~/.task-console/categories.json`; if that is missing, every task lands in one group called uncategorized, and the page says so |
| `TASK_CONSOLE_STATUS_SNAPSHOT` | captured version 1 component observations | no default; unset means component health is unchecked |
| `TASK_CONSOLE_REMINDER_CLI` | absolute path to the reminder owner's `reminder.py` supporting `work-feed` | no default; work records report unconnected; invoked with the console Python interpreter |
| `TASK_CONSOLE_REMINDER_DB` | absolute path to the existing reminder database | no default; work records and reviewed task links report unavailable; work-feed reads never initialize or migrate it |
| `TASK_CONSOLE_ACTION_WORKSPACE` | absolute private directory for agent workspaces | no default; automatic todo actions remain unavailable |
| `TASK_CONSOLE_AGENT_TASK_ID` | registered owner/task identifier for the agent queue tick | no default; the console cannot wake the agent queue |
| `TASK_CONSOLE_READ_ONLY` | set to `1` to disable mutations | unset leaves configured guarded actions available |
| `TASK_CONSOLE_RUNTIME_CONFIG` | absolute reviewed Controller configuration path | no default; task controls require configuration |
| `TASK_CONSOLE_PRIVATE_ROOT` | absolute private declaration directory | no default; task controls require configuration |
| `TASK_CONSOLE_STATE_ROOT` | absolute registration journal and receipt directory | no default; task controls require configuration |
| `TASK_CONSOLE_VAULT_ROOT` | absolute encrypted snapshot directory | no default; task controls require configuration |
| `TASK_CONSOLE_CATALOG_SNAPSHOT` | captured SMITH catalog schema_version 1 | no default; unset means catalog inventory is unchecked |
| `TASK_CONSOLE_HEALTH` | a health watch list (`task-health.json` shape) | no default; unset means the health-coverage column reads NOT CHECKED |
| `TASK_CONSOLE_ALLOWLIST` | a PowerShell file containing a `$TaskNames = @(...)` backup allow-list | no default; unset means the backup-coverage check reports NOT CHECKED rather than passing |
| `TASK_CONSOLE_HISTORY` | a poll-observation log | unset means the observation columns read NOT CHECKED |
| `TASK_CONSOLE_SKILLS` | a skills directory | unset means the skills panel reads NOT CHECKED |
| `TASK_CONSOLE_SKILL_ARCHIVE` | where archived skills go | unset means the archive button is not offered at all |
| `TASK_CONSOLE_MEMORY` | a memory directory | unset means the memory panel reads NOT CHECKED |
| `TASK_CONSOLE_MEMORY_ARCHIVER` | the existing archiver script | unset means the archive button is not offered |
| `TASK_CONSOLE_REPOS` | a directory holding git repos | unset means the repository panel reads NOT CHECKED |
| `TASK_CONSOLE_VISIBILITY` | a JSON object keyed by `owner/repo` (lowercase), each value `PUBLIC` or `PRIVATE`, or an object with a `visibility` field | unset means every repo's visibility reads unknown and no badge is drawn, which is deliberately hard to tell apart from a repo the table has no row for, so the panel also reports the table it loaded and how many rows matched |
| `TASK_CONSOLE_CODEX` | a second agent CLI's home directory, holding its instruction file, config, session store, logs and cache | no default; unset means that whole half of the maintenance view reads NOT CHECKED, which is deliberately not the same as reading zero bytes |
| `TASK_CONSOLE_PLUGIN_CACHE` | a plugin cache directory | unset means the cache row reads NOT CHECKED and nothing can be deleted |
| `TASK_CONSOLE_SESSIONS` | a session transcript directory; also the `root` the console passes to the `convo-chain` library for the conversation chain | unset means that row reads NOT CHECKED, the chain reads NOT CHECKED, and export and fork refuse with `unavailable` |
| `TASK_CONSOLE_CLAUDE` | the CLI used for plugin actions | falls back to PATH; not found means the plugin panel reads NOT CHECKED |
| `TASK_CONSOLE_ALLOWED_HOSTS` | extra Host header values to accept | only the three loopback spellings are accepted |
| `TASK_CONSOLE_IDENTITIES` | a table of `login\|display name\|commit email`, one per line, `#` for comments, saying which identity each repository owner should be committed under | unset means the account-match column reads NOT CHECKED for every repository, which is deliberately not the same as saying they match; no address from this file is ever rendered, only the account name and the verdict |
| `TASK_CONSOLE_CONVO_CACHE` | where the conversation index caches its scan | unset means the first scan of every page load walks every transcript again; correctness is unaffected, the page is just slower |
| `TASK_CONSOLE_DB` | the SQLite file the ingester writes and the page reads | falls back to the companion repo's `data/task-console/console.sqlite3`, and if no companion resolves it reports UNINITIALISED with setup instructions rather than falling back into this repo |
| `TASK_CONSOLE_LLMCALL_LEDGER` | the append-only JSONL ledger the LLM-call primitive writes, one line per call | falls back to `~/.llmcall/ledger.jsonl`; a missing file makes the call view read NOT CHECKED, which is deliberately not the same as reading zero calls |
| `TASK_CONSOLE_LLMCALL_CHAIN` | the file the call view writes a fallback-chain order into | falls back to `~/.llmcall/chain.txt`. This is the one path on this console that writes into another program's configuration, and it is shadowed by the `LLMCALL_CHAIN` environment variable: when that variable is set, the page says so in as many words instead of reporting a save that changes nothing |
| `TASK_CONSOLE_LLMCALL_BODIES` | the directory holding recorded prompt and reply bodies, one file per day | unset means the console mirrors whatever discovery order the call primitive itself uses to find its private companion directory, and adds a `bodies/` subdirectory to it. That order is defined by the primitive, not here, so it is not restated here either: two copies of an order drift, and the copy someone reads is not necessarily the copy that runs. The resolution steps actually walked, and their verdicts, are printed in the page when a body cannot be found. Body recording is off by default, and the three ways to have no bodies (never turned on, companion not initialised, entry past its retention) are reported as three different sentences rather than one empty box |
| `TASK_CONSOLE_DELETE_FOLLOWUP` | absolute path of the private Python follow-up hook that a task delete runs after the Scheduler step, to clean the health watch list, backup and migration plan (protocol in `docs/task-control.md`) | no default; unset still deletes from the Scheduler and the console's category map, but the preview and the result say, as their own state, that the watch list, backup and migration plan were not cleaned, and the result is `partial`, never `ok` |
| `TASK_CONSOLE_DELETE_WITHOUT_AUTHORITY` | `1` lets a console process that has none of the four Controller settings delete tasks, treating every task as unmanaged | unset; such a process refuses every delete with `authority_not_configured`, because it cannot tell which tasks the Controller owns |
| `TASK_CONSOLE_DELETED_ARCHIVE` | absolute private directory, outside every git worktree and outside the backup, where deleting a task the Controller does not own first writes its exported XML and a receipt | no default; unset blocks the delete of an unmanaged task in its preview; Controller-owned tasks keep their before-image in the Controller's vault and do not use it |
| `TASK_CONSOLE_POWERSHELL` | the powershell.exe that task commands run through | falls back to the pinned `System32\WindowsPowerShell\v1.0\powershell.exe`, and only to a bare `powershell.exe` off PATH when that file is not there |

The tool defaults only into its own namespace. Pointing it at whatever else a machine keeps its
watch-list and allow-list in is the launcher's job, and the launcher belongs on that machine.

That last row is the important one. **Unknown and pass are never rendered the same.** A console
that showed a green backup-coverage column because it had nothing to compare against would be
worse than one that showed nothing at all.

A task that appears in no category still shows up, in a group called uncategorized. It is never
dropped: the task nobody categorised is the one nobody is watching.

## What it will not do

It cannot create a task, and it cannot reconfigure one (triggers, actions, principal, settings).
Creating one correctly means naming it so the backup drift gate can see it, choosing its settings
deliberately, generating its launcher, and registering it in three places. A button that skipped
those steps would manufacture exactly the untracked task that procedure exists to prevent.

It can **delete** one, but only through a bound preview. `POST /api/task/delete/plan` lists every
step with its target (the Controller's retire transaction for a task it owns; for any other task,
an XML export into the private `TASK_CONSOLE_DELETED_ARCHIVE` followed by an unregister at the root
path), the edit to the category map, and what the private follow-up hook will clean (the health
watch list, the backup and the migration plan). It hands back a single-use token that lives 300
seconds and is bound to a fingerprint of everything the preview depended on; `/apply` re-checks
that fingerprint, asks the hook again, and refuses before anything irreversible if either moved.
The result is `ok` only when every step read back as done; a Scheduler delete whose later cleanup
did not finish, or whose hook is not configured, is `partial` and says what remains. Whether the task
is gone is always decided by re-reading the Scheduler, never by the Controller's or the unregister
call's own reply, and a re-read that fails is `unknown`, not "not deleted". A category map that
still lists the task but cannot be rewritten byte for byte blocks the preview, because after the
Scheduler step there is no console path back to it. When the hook refuses only after the task is
gone, the result says the task was deleted and nothing was cleaned, and hands back the exact
request and command to re-run just the follow-up. A console process
without the Controller's configuration refuses to delete unless
`TASK_CONSOLE_DELETE_WITHOUT_AUTHORITY=1` is set. The full
protocol is in `docs/task-control.md`.

It can also file a **repair order** for one task, on an explicit click only. The order is an
ordinary todo created through the reminder owner's `ensure` verb and dispatched through the
existing agent action; the agent diagnoses and proposes a fix on copies and never touches the live
task, its registration, launcher or backup. Nothing files a repair order because an observation
failed.

On the page, every task row on 任务开关 and every row of the 运行详情 table carries both a repair
(修复) and a delete (删除) item in its ⋯ menu, except the two 同步与备份 pipeline tasks, which carry repair
only on every tab, because those two tasks are the backup itself; the backend refuses to issue a
delete token for them too. Delete opens a dialog that asks for the reason, shows the preview step
by step with each target, puts any blocking reason and warning directly under the task name and keeps the confirm button disabled
while one exists, and enables it only once the exact task name is typed. The result names every
step's status; `partial`, `failed` and `unknown` are never shown as done, and the task list is
re-read either way. Repair opens a dialog with the facts and limits that will go into the order and an optional
one-line note; a submission is reported as accepted, with the result to appear on the work page and
in Discord, never as fixed. Each row then shows the order's progress (queued, running, awaiting
verification, proposal ready, failed, closed, or unknown) as a dashed chip that opens the order;
the page re-reads it every 15 seconds while an order is in flight and an automation tab is in view.
An unreadable order feed is stated in the toolbar instead of drawing every row as having no order: a
small ! whose tooltip says, in plain words, why progress cannot be seen (the reason code is kept in the
tooltip), and a red ! with 修复进度读取失败 when the read itself failed.
The retire button (停用并移出清单) is gone from the page: delete replaced it. `maint` `task.retire`
still works for callers of the API.

It can **retire** one, which is the opposite operation and is safe to automate precisely because
it is subtractive: disable, write the reason into the description, drop it from the backup
allow-list, drop it from the health manifest. All three, or none. A task that is only disabled is
worse than one that was never touched, because an exported task definition does not record the
disabled state: a backup that still lists it will faithfully reinstall it, enabled.

Pushing is the one thing here that leaves the machine, so it is deliberately **not** one click.
It is two steps: a read-only plan first (which files, which ref, whether that remote is public or
private), and the confirm step must hand back the very file list the plan showed, or the whole
thing is refused. A stray click opens a plan and nothing else. It never uses `--no-verify`, it
never stages with `git add -A`, and whatever the hooks say comes back verbatim.

The full list of what this page can do is the action table in `maint.py`; that table is the
authority and this file does not keep a second copy of it.

## Why it is locked down

It can change system state, so it has several controls, none of them decorative.

(This line used to say "three controls" while the list below had five. A count in prose next to
a list that anyone can append to is a fact with two homes and no reconciliation -- the list grew,
the number did not. So the number is gone: the list is the only place that says how many.)

1. **Binds 127.0.0.1 only.** Nothing off this machine can reach it.
2. **Every `/api/` call needs a token** minted fresh at startup and never written to disk. Without
   it, any web page you had open could POST to `http://127.0.0.1:<port>/api/act` and disable your
   backup task. Being on localhost does not prevent that; a token does.
3. **The verb list is closed** (`enable` / `disable` / `run` / `stop`), the task must be at the
   root path, and the server re-enumerates the live task list and checks membership before acting
   rather than trusting the name it received. The name reaches PowerShell through an environment
   variable, never interpolated into a command string.
4. **A Host header allowlist.** Binding to loopback stops the network; it does not stop DNS
   rebinding, and rebinding is the attack that matters here, because the token is substituted into
   the page at `/`: anything that can make a same-origin request to `/` simply reads it out of the
   HTML. Only the three loopback spellings are accepted, a missing Host is rejected, and `"*"` is a
   separate branch rather than an entry in the list, so no hostname can be spelled in a way that
   turns the check off. The class default is an empty set, so a handler that never went through
   startup refuses everything: forgetting to configure it and deliberately allowing everything must
   not be the same state.
5. **Maintenance actions live on a separate closed table** from the task verbs. One shared table
   would mean that adding a skill action silently widens the task surface, and nobody reviewing the
   diff would notice. Every argument passes a narrow character class, and any path must resolve to
   a direct child of its configured root; failing either refuses the whole action rather than
   sanitising the input, because sanitised input looks safe without anyone knowing what was removed.

Some tasks are owned by SYSTEM or registered at `RunLevel=Highest` and cannot be touched from a
normal user session. The console says so instead of reporting a bare access-denied.

## Files

| File | What it is |
|---|---|
| `server.py` | the HTTP layer: routing, auth, host allowlist, the two write endpoints |
| `console.html` | the whole page: one file, still no build step |
| `vendor/tabler/` | Tabler v1.5.0 (MIT), the dashboard shell. Vendored, not a CDN link |
| `collect.ps1` / `act.ps1` / `runlog.ps1` | the Windows side |
| `evtlog.py` | the fast event log reader (EvtQuery), with the PowerShell path as fallback |
| `freshness.py` | five-state artifact freshness, a pure function of timestamps |
| `selfcheck.py` | every configured source, probed and reported one by one |
| `repos.py` | git repository scan, concurrent, one timeout per repo |
| `maint.py` | the closed maintenance action table and its argument gate |
| `memops.py` | memory pool diagnosis; archiving is delegated, not reimplemented |
| `sysinfo.py` | disk, cache size, abandoned clone staging directories |
| `llmstats.py` | the LLM-call ledger: windowing, per-rung reconstruction, consecutive runs, and the chain config file |
| `retire.py` | the three-place deregistration, planned first and then written |
| `task_delete.py` | deleting a task: bound preview, Controller or archive-then-unregister, category map edit, follow-up hook |
| `task_repair.py` | repair orders: the task's known facts plus fixed limits, filed and dispatched through the reminder owner |
| `console_store.py` / `console_ingest.py` / `history.py` / `timeline.py` | the run history layer |

## Third-party assets

The page shell (sidebar, top bar, cards, badges) is [Tabler](https://github.com/tabler/tabler)
v1.5.0, MIT licensed. The CSS and JS are vendored under `vendor/tabler/` together with the
upstream `LICENSE`, and served by `server.py` from `/vendor/`.

They are vendored rather than loaded from a CDN on purpose: this console is the thing you open
when something is already broken, and that is the worst moment to depend on the network. The
cost is about 776 KB in the repository, paid once.

Tabler is calibrated for calm, low-density panels, which is the opposite of what a 38 by 19
task grid needs, so the density is pulled back through Tabler's own `--tblr-*` custom
properties (12px body text, halved table cell padding) rather than by overriding its rules.
Tuning through the documented variables is what keeps a version bump from silently undoing it.

The colour palette stays the one this page already had. Tabler's surface variables are pointed
at it, not the other way around: that palette was measured for contrast in both themes on a
dense table, and two palettes in one page always disagree somewhere with no way to tell which
one is right.

`/vendor/` is served without a token, because a `<link>` tag cannot send one and there is
nothing secret in there. That makes "cannot escape the vendor directory" the only control on
that route, so it is tested directly, including percent-encoded traversal.

## Data boundary

This directory ships in a public repo and holds no real state. No snapshot is cached to disk, the
category map is read from a path outside the repo, and `categories.example.json` contains only
synthetic names. Real task names are real-run data and belong in the private machine config.

The same rule covers identifiers, not just data. The name of a private repository, or a
conventional path under the operator's home that would reveal one, must not appear in this
directory either: that is a cross-repo link, and the PII gate blocks a commit carrying one. Note
that the verdict is not fixed by the text itself. The same line can be clean for months and become
a leak the day the private thing it names starts existing, so the question is never whether a
string looks sensitive, but whether it currently points at something real and private.

## Changing it

Read `../../docs/changing-this.md` before editing anything here. It is the invariants a change
must not break and the traps this repo has already fallen into, with the symptom each presents as.
There is no list of routes or modules in it on purpose: a list drifts, and a drifted list reads
exactly like an accurate one.

## Declaration producer (schemaVersion 1)

`task_console.compiler.plan(request)` is a pure function over JSON data. It accepts
`schemaVersion`, `components` (a list of manifests or private installation wrappers),
`bindings`, `machine`, and `baseline`.
The CLI accepts that envelope on stdin or through `plan --request FILE`. Alternatively,
pass all four of `--component FILE` (repeatable), `--bindings FILE`, `--machine FILE`, and
`--baseline FILE`. There is no implicit discovery, configuration path or output directory.

The generated examples in `examples/console` form a complete synthetic request. Their
generator is `tools/make_fixtures.py`; `--out DIRECTORY` writes the example files
directly to that directory. The data-boundary manifest registers each example as FIXTURE.
`installations.request.example.json` is a separate complete request for two installations
of the same public component in different scopes.

| Input | Required fields and meaning |
| --- | --- |
| Component manifest | `schemaVersion: 1`, stable `component`, logical read entrypoint `read`, and `tasks[]`. Each task declares local `id`, `kind` (`oneshot`, `daemon`, `dispatcher`), logical `entrypoint`, recommended `timeout_seconds`, `concurrency_key`, and `checks[]` containing unique `id` and boolean `required`. Optional `schedule_hint` is advice only. |
| Bindings | `schemaVersion: 1`, `tasks` keyed by `component/id`. Each binding explicitly supplies `name`, `enabled`, absolute-executable `argv[]`, absolute `cwd` and `source_root`, `timezone`, `trigger`, opaque nonempty `principal` and `power` objects, effective `timeout_seconds` (0 preserves no limit), `concurrency` with Scheduler `policy`, `backup`, category ID `category`, and `checks[]`. |
| Bound check | `id` plus exactly one of `legacy` (the original health-row fields, excluding identity fields) or `watched_elsewhere` (`component`, `check_id`, `observation_ref`). An omitted check binding remains visible as `unbound`. External observation references are reported, never fetched or treated as proof of health. |
| Machine | `schemaVersion: 1`, nonnegative `authority_epoch`, empty `migrated_tasks`, `overrides` keyed by `component/id`, and `categories[]` (`id`, `name`, optional `desc`). Overrides replace individual binding fields; nested objects/lists are replaced whole. Nonempty migrated sets are rejected in this producer phase. |
| Baseline | Optional `tasks` Scheduler snapshot array, `task_names` containing the literal PowerShell source, parsed `task_health`, and parsed `categories`. Missing/null sources remain NOT CHECKED. This must be an explicit snapshot; the compiler never reads the live Scheduler. |

For installed components, the caller supplies a private wrapper in `components`:

```json
{"namespace": "acme-user", "manifest": {"schemaVersion": 1, "component": "acme-maintenance", "read": "acme-status", "tasks": []}}
```

The manifest above illustrates the wrapper; use actual task declarations as in the
generated request. The public manifest is embedded unchanged. The same request supplies
this mapping alongside `bindings.tasks`:

```json
{"installations": {"acme-user": {"component": "acme-maintenance", "identity": {
  "marketplace": "acme-market", "scope": "user", "client": "acme-client",
  "source_id": "acme-source-user", "metadata": {"selected_version": "1.0"}
}}}}
```

`namespace` uses the same slug syntax as component IDs. Each namespace selects exactly
one wrapper and one mapping; their public `component` IDs must agree. The caller obtains
the identity descriptor from its catalog. `marketplace`, `scope`, `client`, and `source_id`
must be nonempty strings; other JSON metadata is preserved as structured data. No plugin
discovery, cache selection, identity inference or second plugin parser runs here.
The identity tuple excludes revision metadata: two mappings of the same identity fail
even if their selected versions differ. Missing, duplicate, unused or mismatched mappings,
or mixing wrapped and unwrapped declarations of the same component, fail closed.

Wrapped task IDs are `namespace/component/id`; use that exact key for `bindings.tasks`
and `machine.overrides`. There is no fallback to an unqualified binding. All check coverage,
projection ownership and Scheduler proposals use the full task ID. `TaskSpec.component`
retains the public ID, with separate `installation_namespace` and structured
`installation_identity` fields. These are null for legacy unwrapped inputs, whose
`component/id` keys remain supported. Identity metadata is not inserted into generated
legacy file content. The request wrapper, mapping and returned plan belong in private
storage; public manifest IDs never need an installation-specific edit.

Compiled `recommended_timeout_seconds` retains the public declaration's `timeout_seconds`.
Compiled `timeout_seconds` is the effective private binding/machine override, including 0
for unlimited. Changing a recommendation does not propose a Scheduler timeout change.

Supported triggers are `interval` (positive `minutes` or `seconds`), `daily` (`at`),
`weekly` (`at`, `days` using `mon` through `sun`), `logon`, or `xml` (`xml`, `owner`, `reason`).
A binding can supply a list of triggers; an empty list explicitly means no triggers.
Additional trigger fields are retained. XML is checked for well-formedness and retained
as supplied; DTD/entity declarations are rejected. Use optional binding `xml_passthrough`
with `xml`, `owner`, and `reason` for complete task settings that cannot be represented
losslessly. Supply the same object in the baseline for comparison. The optional
`credential_ref` is a reference only; callers must keep credentials out of requests.

Baseline Scheduler rows use `name`, `enabled`, `argv`, `cwd`, `timezone`, `trigger`,
`principal`, `power`, `timeout_seconds`, `concurrency`, and optional `xml_passthrough`.
Supply complete normalized snapshots of the current settings, including opaque fields.
`tasks: []` means the caller checked the complete Scheduler scope and observed no tasks.
A declared task absent from any supplied complete array produces `status: different`, an
`absent_tasks` count and a blocked `scheduler-proposal` with `operation: create`,
`before: null`, `after: TaskSpec`, and `reason_code: task_absent_requires_review`.
This is a read-only proposal: the producer always returns `applicable: false` and legacy
authority. Missing/null snapshots remain `not-checked` without create proposals; missing
fields on an existing row remain individually unchecked. `compared_tasks` counts existing
tasks with all comparison fields supplied, excluding absent tasks.
Unknown OS task names produce adoption proposals
without compiled bindings or create operations. A proposed false-to-true `enabled` change
is explicitly blocked for review. Disabled tasks remain in projections when their explicit
backup/category/check bindings require it.

The plan reports `baseline_revision` (SHA-256 of canonical baseline JSON), `input_revision`
(the complete canonical request), `task_specs`, `changes`, `parity`, `check_coverage`, and
`adopt_proposals`. Its `generated_files` contain UTF-8 content and SHA-256 digests of those
bytes, with ownership task IDs. These are **declared-task projections**, marked
`replacement_safe: false`; they can omit unmanaged legacy entries and must not replace
live files. The compiler writes no side files itself. Persist returned plans only in
private storage chosen by the caller.

Health parity compares all rows as a multiset and normalizes the two existing exit-code
key spellings through `freshness.declared_ok_codes`. Each generated row keeps its own
`task_id` and `check_id`. Coverage counts matching declarations; `evaluated: 0` explicitly
states that no health checks ran. Categories retain order because the legacy reader uses
first membership. Allowlist parity preserves the legacy empty/absent/malformed return
convention; an unreadable or empty list is never reported as a checked empty set.
The shared literal parser rejects direct assignments and mutations through ordinary or
braced TaskNames references, including scope prefixes, comments, backtick line
continuations and indexed updates. Unrelated names, comments and quoted text remain
excluded. Here-string closing markers at the start of a line may be followed by expression
code; scanning resumes after the marker. This is a literal-data reader, not execution or
a general PowerShell evaluator.

CLI exit 0 means a valid plan envelope, including drift and NOT CHECKED results. Exit 2
means invalid arguments, unreadable input, invalid JSON or a contract error. Diagnostics
identify fields without echoing input values. The package entrypoint exposes this producer;
the source-tree web server and retirement APIs keep their existing launch/import forms.
Registration, scheduler mutation, observation collection, runtime launchers and authority
cutover are outside this interface.

T12 adds a separate, explicitly injected registration transaction API and the
apply/retire/recover CLI entrypoints. It does not change the read-only plan above.
See [the registration contract](../../docs/task-registration.md) for required
private inputs, transport guarantees, recovery semantics and integration gaps.
No production runtime adapters or automatic authority cutover are installed.
