# Independent agent prompt tasks

[Documentation index](web-panel.md)

## Agent prompt tasks

Open **Agents → Independent tasks** for host operations or tasks that need
Basaltwater’s independent execution limits and receipts. Install
and authenticate Codex as the non-root account running the panel. Other
terminal agents are inventoried by diagnostics but do not execute prompt tasks
in this initial version. Root-managed panels use a locked service account
without a home and show diagnostics with prompt execution unavailable.

1. Select **New task** to write a custom prompt, **Templates** for a starting
   point under **Host checks and maintenance**, or
   **Guided tasks** for system and data tools. Each template shows its execution mode, suggested
   repeat interval, runtime cap, and additional permissions before selection.
2. Enter the working directory. Host inspection defaults to the account’s home;
   custom workspace tasks require a dedicated directory.
3. Choose **Inspect only** for Codex's read-only sandbox, or **Workspace
   changes** for a directory inside the account's home. Enable command network
   access for service sockets, endpoint checks or package downloads, and temporary writes when
   tests or package tools need `/tmp` or `TMPDIR`. Provider requests still use
   the network in either mode. Configured hooks and MCP integrations retain
   their own permissions.
4. Select a model and reasoning effort, or keep the configured defaults. The
   model dropdown reads Codex's local cache without contacting the provider;
   **Model and web search** accepts a custom model ID when needed. Model access
   and supported effort levels depend on the installed CLI and account.
5. Set **Maximum runtime** in whole minutes, from 1 minute to 7 days (default
   30 minutes). Waiting and sleeping count toward this wall-clock limit. This
   is a duration cap, not an exact cost or token budget.
6. Choose once, hourly, daily, weekly, or **Custom interval**. Custom intervals
   accept 1–43,200 whole minutes (30 days): 360 means every 6 hours; 20,160 means
   every 2 weeks. The custom field applies only when Custom interval is selected.
   **Start task** queues an immediate run and enables repetition when selected.
   **Create schedule** uses the first-run time when set; otherwise it starts
   after one interval. **Save draft** stores an inactive task, including a
   one-time prompt, without running it or enabling repetition. Before Codex is
   installed, drafts can be prepared through the task CLI; panel prompt forms
   appear once Codex is installed.
7. Open **Scheduling options** to set a first-run time, custom interval, or
   **Pause schedule after failures** from 1 to 10, or 0 to keep repeating.
   The default is 3. Failed, blocked, or interrupted runs count toward this
   limit; cancellation preserves the count, and progress or completion resets it.

Open **Context and workspace** to choose **Context persistence** independently of **Workspace**. A checkpoint
carries findings and next steps into a new conversation; conversation mode
resumes the exact saved Codex session. Managed directories begin empty; a
persistent worktree starts from the source repository's committed HEAD and
retains unfinished task changes. Managed workspaces require workspace mode.
Open **Goal and usage limits** to define completion criteria, continuation,
attempts, a deadline, reported-token budget, daily limits and evidence retention.
A goal needs at least one total limit. **Context and evidence** reads its saved
checkpoint and receipts; **Reset context** preserves workspace files and history.
See [persistent tasks](../agents/agent-tasks.md) for CLI/MCP controls and detailed limits.

The starting templates cover these tasks. Their prompts and settings remain
editable before submission; selecting a template does not run or schedule it.

| Template | Default repeat | Runtime cap | Scope |
| --- | --- | --- | --- |
| Host maintenance review | Once | 30 minutes | Workspace permissions; command network; private run directory |
| Check Debian update health | Once | 15 minutes | Workspace permissions; command network; private run directory |
| Check network and clock readiness | Once | 15 minutes | Workspace permissions; command network; private run directory |
| Check backup health | Once | 15 minutes | Workspace permissions; command network; private run directory |
| Review storage cleanup | Once | 15 minutes | Inspect only |
| Triage a service incident | Once | 20 minutes | Workspace permissions; command network; private run directory |
| Check certificate expiry | Once | 10 minutes | Workspace permissions; command network; private run directory |

Host check templates inspect existing evidence and recommend follow-up:
backup checks do not perform restores, storage reviews do not delete files,
and certificate checks do not renew certificates or change trust. Missing
configuration or unavailable evidence is reported explicitly.

Host service review templates select a private run directory and command network access;
the writable directory is empty scratch space. Their prompts prohibit host file
changes and service mutations. Inspect-only storage reviews need neither grant.
Saved tasks and reused receipts retain their original permissions: create a new
task from the refreshed template to replace a blocked inspection task. Merely
resuming it does not change its sandbox. Host reviews use explicit doctor
capabilities; project manifest discovery requires an actual Git checkout.

The catalog contains host checks and maintenance templates. Concrete repair requests can
be prepared through follow-up review or the custom form with exact targets,
actions, verification, and rollback. Existing saved tasks and receipts retain
their original prompts and permissions. Privileged actions require the
configured Admin approval workflow.

Runs can record a **Follow-up requested** proposal for checks or repairs that
need different access. **Review follow-up task** opens its actions and proposed
permissions in the normal task form, defaulting to Once and one attempt. Review
the original evidence, adjust scope, then save or submit. Opening it never
creates or runs work, changes the original task, or approves privileged actions.
Requests remain available in retained receipts after recent history is evicted.
For a service prerequisite, **Approve once** creates the missing broker request
and shows **Open service approval** in the form. Approve the exact operation
on the linked approval page, return, then **Check approval and launch**.
The panel preserves reviewed settings and checks status only on submission.
Restarts run before the task starts: a repair that must precede a restart needs
separate preparation and restart-verification proposals.

Additional templates appear when the panel's saved setup configuration or
commands installed for its account indicate a relevant integration. Each shows
the detection reason. These checks do not run commands or prove service health,
browser readiness, repository compatibility, or authentication.

The catalog and task form explain known conflicts with local Codex administrator
requirements, including forbidden sandbox/search modes and restricted permission
profiles. The runner checks again before provider launch and records a blocked
outcome with zero token usage when these requirements prevent execution. Drafts
remain available; policy is never relaxed or retried with broader access. This
preflight covers known local constraints, while Codex still enforces other
configuration layers and integrations retain their own permissions.

| Conditional template | Appears when | Default repeat / cap | Scope |
| --- | --- | --- | --- |
| Check T3 Code readiness | T3 Code is selected and its runtime or user service unit is installed | Once / 15 minutes | Workspace permissions; command network; private run directory |
| Review container health | `docker` or `podman` is installed | Once / 15 minutes | Workspace permissions; command network; private run directory |
| Review web hosting | Web server is configured, or `basaltwater-web` or `nginx` is installed | Once / 15 minutes | Workspace permissions; command network; private run directory |
| Check shared storage | Samba is configured, or `smbd`, `mount.cifs`, or `sshfs` is installed | Once / 15 minutes | Workspace permissions; command network; private run directory |
| Check remote desktop readiness | Remote desktop is configured or `xrdp` is installed | Once / 10 minutes | Workspace permissions; command network; private run directory |
| Check HomeBox data protection | HomeBox is configured | Once / 15 minutes | Workspace permissions; command network; private run directory |
| Check Gogs repository hosting | Gogs is configured or `gogs` is installed | Once / 15 minutes | Workspace permissions; command network; private run directory |
| Check Antistatic services | Antistatic lobby or DB is configured | Once / 15 minutes | Workspace permissions; command network; private run directory |
| Check notification delivery | Panel notification ingest is enabled | Once / 10 minutes | Workspace permissions; command network; private run directory |
| Check privilege approval readiness | Privilege approvals are configured | Once / 15 minutes | Workspace permissions; command network; private run directory |

Integration checks inspect existing state and recommend follow-up without
repairs or privileged actions. Availability filters only the starting catalog: saved tasks retain
their prompts and settings if an integration is later removed. A defined template
that is temporarily unavailable shows guidance and leaves the custom form usable.
Bookmarks for removed starters are no longer accepted; use **T3 Code** instead.

**Model and web search** controls Codex's web search independently of command network
access: disabled, cached results, or live search. Runs use ephemeral Codex
sessions by default; enable **Keep fresh Codex session history** to retain them
in Codex too. This does not resume them. Conversation persistence always retains
its session. Run evidence is retained with every choice.

Saved tasks use responsive cards with status, repetition, next run, latest
outcome, and working directory. Active and queued work appears first, followed
by tasks needing review. **View latest result** opens the retained report
directly; **View live run** opens a running task's current record. Use
**Pause**, **Resume**, **Edit**, **Run once**, or **Remove** to manage them.
Use **Start schedule** to activate a recurring draft. Running a draft manually
does not activate its repeat schedule. **Duplicate** opens a new form with a
saved task's settings so you can adjust its directory, model, or prompt. Run
history's **Reuse run settings** uses the prompt and execution settings recorded
for that specific run, even after the task is edited or removed. Reuse defaults
to Once to avoid enabling another recurring schedule inadvertently. Neither
link saves or executes work until you submit the form. **Task settings and more
actions** contains duplicate, context, removal, and reset controls, plus full
execution settings. Removing a task keeps its retained run history.

Schedules that reach their failure limit show **Auto-paused** and the failure
count. Inspect the last run, edit the task if needed, then use **Resume** to
reset the count and schedule its next interval. A successful manual recovery
run clears the count but leaves the schedule paused until resumed. Timeouts
and CLI startup errors count as failed runs. A completed CLI process does not
prove the requested task succeeded. Structured reports distinguish progress,
completion, waiting, blocked and failed work; blocked reports count toward
failure pausing even with a successful process exit. Review evidence before
resuming work.

The screen leads with task status and offers **New task** prominently. Workflow
links lead to templates, guided tasks, run history, and setup/diagnostics.
The task form keeps the prompt, directory, permissions, runtime, and repetition
visible; disclosures group model/search, context/workspace, scheduling, and
goal/usage settings. Relevant groups open for non-default settings and after
validation errors. Selecting a template, editing, duplicating, or reusing
settings puts the selected form before the saved-task list. All task controls
and disclosures work without JavaScript.
Pausing stops future work and clears a queued run; cancel an active run
separately. Cancellation stops processes, but does not undo completed edits.
Run history retains the prompt and settings used, timestamps, duration, exit
code when available, and bounded output. Summary cards show the active/queued
work, repeating schedules, drafts, automatically paused tasks, recent outcomes,
and tasks needing review. Run history shows recorded wall time for retained
finished runs and puts execution settings inside each report's disclosure.
Outcome badges distinguish blocked or failed work from a completed CLI process.
These are retained-history
totals, not lifetime usage or spending estimates. Active runs show elapsed time
and remaining runtime at page load. The safe activity summary updates run counts
and elapsed time in place. Open or refresh a report to inspect its latest
evidence; unsaved prompts and review forms remain as loaded.
Refreshing a selected run, task context, or receipt keeps that evidence open.
Unreadable task storage shows unavailable counts and a retry link on the task
list; it does not claim that the account has no tasks or run history. A submitted
prompt stays visible with instructions to copy its settings before reloading.
Execution controls remain disabled until storage recovers.

New non-root Debian setups with Codex selected install an independent task
worker. It runs without the panel or T3 Code, and the screen reports its live
lease separately from installed unit presence. Its system service receives
`systemd-journal` supplementary access for bounded log reviews, matching the
panel's journal access without adding that group to the login account. Rerun
Basaltwater setup to update an existing worker's unit; source updates alone do
not change a running service's groups. Older installations retain panel
scheduling until setup installs the worker. Tasks persist across panel and host restarts. After
downtime, each overdue schedule runs at most once, then advances to its next
interval. Deadlines crossed while a task is running are skipped, preserving its
cadence without immediately starting another run of that task. An explicit
Run once request remains available. Changing the elapsed interval resets the next
deadline to a full interval from saving; editing other settings keeps the existing
deadline. Paused tasks and drafts remain inactive after edits. These are elapsed
intervals rather than calendar times. Runs interrupted by a restart are labelled
for review. The runner executes serially across its shared CLI/panel controls;
select a managed task worktree when editing could overlap another agent session.
Removing the panel leaves independent tasks running; pause them explicitly.

Open **Agents → Readiness** for installation and sign-in checks.
**Check agent readiness** collects versions and local credential metadata for
selected, installed Codex, Claude Code, OpenCode, and GitHub CLI, plus
maintenance-hold state and the timestamp of any saved readiness record.
T3 Code is checked when selected and installed. Its workspace link leads to
T3’s native update controls; this panel no longer runs a T3 update worker.
Leftover unselected agent tools and T3 installations are not probed or offered
as starting templates. Each readiness request resolves current feature selections.
Credential file presence is not proof of provider authentication. Checks run
in the background and do not launch prompts or automatically repair the host.

Prompt execution uses the account's existing privileges. Host repairs that
need elevation use the configured [privilege approval workflow](../agents/privilege-approvals.md);
the panel does not grant root access. The templates prepare changes for review
and do not ask the agent to commit, push, or deploy.

Runs receive their saved execution settings in the prompt instructions and are
told to report blocked checks instead of bypassing sandbox permissions, starting
login, or waiting for an operator. Package and test tools may need a documented
per-command cache path inside the workspace or permitted temporary directory;
temporary writes do not grant access to caches elsewhere in the account's home.
Prompts must keep waits within the runtime cap and avoid detaching work into
another service. Cancellation does not undo changes or withdraw privileged
broker requests; external integrations can own work outside the local process
group. The [managed agent skills](../agents/agent-skills.md) include unattended task guidance.
