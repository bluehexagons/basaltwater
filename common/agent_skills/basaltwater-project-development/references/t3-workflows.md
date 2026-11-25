# T3 project actions and visual replies

Use these integrations when the active T3 session exposes the relevant tools.
Follow its injected instructions and live schemas. This reference follows
[T3 Code 0.1.12](https://github.com/pingdotgg/t3code/releases/tag/v0.1.12).

## Establish the execution environment

The client window's operating system does not determine where commands run.
Before diagnosing tools, skills or paths, identify the selected connection,
provider account, shell and repository from the active session. Compare the
terminal's current directory and Git checkout with T3's bound workspace.
A shell `cd` changes that shell only; it does not rebind the thread or its tools.

| Provider environment | Host integration |
| --- | --- |
| Debian | Per-user `t3code.service`; use `basaltwater-t3code` for service operations. |
| CachyOS desktop | Packaged `t3code-bin` and desktop account; use `basaltwater-cachyos-t3code`. |
| CachyOS web | `basaltwater-cachyos-t3.service`, with separate data from the desktop app. |
| Native Windows | Local Windows connection, PowerShell and Windows paths; use `basaltwater-windows-workspace`. |
| Ubuntu WSL | Selected distribution's Linux account, paths, provider and skills; use `basaltwater-wsl-workspace`. Windows desktop updates and Ubuntu skill updates are separate. |

Installed bundle completeness, installed revision, session-loaded guidance,
provider authentication and a working project are separate observations.
Use the platform's skill inventory for files, and the active agent's skill list
for access; neither establishes that a long-running thread loaded a new revision.
After setup finishes, use T3's **Restart agent session** for an authorized thread
reload when needed. Do not restart a whole server to refresh one thread.

For an authorized setup/refresh, retain its job handle, private log and final
completion status. Dispatch acceptance, HTTP readiness or a desktop process does
not verify a provider turn. After completion, check the intended connection with
a harmless provider turn and terminal command, then run relevant project checks.
Do not repeat a refresh while its completion is pending or uncertain. Preserve
the current job's evidence and inspect it through the platform's operations guide.

For verbose project checks run directly in a T3 terminal, use the shared
[`capture_output.py`](../scripts/capture_output.py) helper with the selected
environment's Python interpreter. `--output-directory NEW_PATH` chooses a new
directory under an existing private parent outside Git. Retain that path before
starting the check: `summary.json` records its initial `in_progress` status and
then its final status, exit code, duration and log byte counts without recording
command arguments or environment values. The final JSON is saved before it is
emitted to the terminal, so lost terminal output does not lose that result.
A stale `in_progress` record is an uncertain outcome; inspect the owned run
instead of launching a duplicate. Capture does not keep a process alive after
terminal/service shutdown. For a submitted native development task, keep its
supervisor's handle and result; wrapping submission only measures dispatch.

## Project commands and worktree setup

Keep the project's native build, test, and development commands canonical.
When adding T3 actions is useful to the requested work, point them at those
commands instead of maintaining a second build system. Read existing actions
with `t3_project_read`; inspect environment preferences with
`t3_environment_read` before changing settings. A saved action or accepted
launch is not evidence that its command finished successfully.
Actions run in the selected environment's shell. Use its project-documented
commands and interpreter; a Windows action must not assume GNU Make or Bash,
and an Ubuntu action must not assume Windows tools, paths or credentials.

Repositories can share actions in `t3.json`, imported through the project's
Actions settings. Preserve existing project overrides: for file-backed settings,
the order is project override, environment setting, `t3.json`, then built-in
default. Do not change environment-wide defaults just to configure one project.

For worktree preparation, `runOnWorktreeCreate: true` runs a setup action.
Its `async` default is true, so the agent can start before setup finishes. Use
`async: false` when dependencies must be ready before the first agent turn;
check the setup result before launching dependent checks. New worktrees start
from committed files and share host ports and services. Allocate distinct ports
and keep secrets in the project's existing private configuration.

In 0.1.11, `worktreeSparsePatternsPath` in `t3.json` can point to a
repository-relative non-cone sparse-checkout pattern file. T3 reads both
files from the branch being checked out before running setup. Ensure required
setup inputs are included. A missing pattern file aborts creation. This needs
Git 2.35+ and enables `extensions.worktreeConfig` for the repository.
See [worktree settings](https://github.com/pingdotgg/t3code/blob/v0.1.11/docs/user/project-settings.md#defaults-and-inheritance).
When reviewing a sparse checkout, run `basaltw manifest check-t3-worktree
--ref REF --require-file PATH` against the intended commit. Repeat
`--require-file` for the setup script and inputs it depends on. Try setup and
the project's checks in a disposable T3 worktree before relying on the rules.

### Multi-repository projects

In 0.1.12 a project can discover repositories under its folder and track
additional paths on that environment. Check **Settings → Projects →
Repositories** and the thread's actual checkout before editing. In a worktree
thread, verify the selected repositories and their base commits independently:
T3 uses the thread's base branch where it exists and each repository's default
branch otherwise. Files outside the repositories in a plain project folder are
shared links to the originals. Review Git changes and publish per repository.

T3 reads the project folder's `t3.json` for project actions and custom
`worktreeLayout`. The sparse preflight checks one repository's committed rules
at a time. For a custom layout, review the scripts and verify the actual
checkouts and setup result. T3 records owner approval of each exact layout
command per project and environment. Review script changes too, since approval
of the command text persists when a referenced script changes. Agent-initiated
layout execution also needs Full access within the applicable host policy.
[Multi-repository projects](https://github.com/pingdotgg/t3code/blob/v0.1.12/docs/user/multi-repo-projects.md)
· [Managed policy](https://github.com/pingdotgg/t3code/blob/v0.1.12/docs/user/managed-policy.md#other-capabilities)

### Action lifecycle

`runOnSettle` can execute on every settlement of a thread with its own
worktree. Use it only for an intended, repeatable project operation; do not
attach broad cleanup, publication, or deployment as an incidental setup step.
Settling is separate from merging Git changes and may make a worktree eligible
for configured automatic cleanup.

In 0.1.9, an action with `autoOpenPreview: true` and a `previewUrl` can open
Preview in web clients as well as desktop clients when Preview is available for
the selected environment. Opening a tab does not establish application readiness
or make a desktop-local tab automatable by server tools. For requested browser
work, use the browser skill's server-tab workflow and inspect the tab's runtime.

In 0.1.8, `stopOnSettle: true` can stop an action's terminal and child processes
when its thread settles. It applies to commands started through that action;
provider shell commands retain their provider's lifecycle. Inspect this setting
alongside `runOnSettle`, including automatic settlement, before starting a
long-lived command. Do not add it to existing actions incidentally.

Contract: [project file schema](https://github.com/pingdotgg/t3code/blob/v0.1.9/packages/contracts/src/t3ProjectFile.ts),
[action preview availability](https://github.com/pingdotgg/t3code/blob/v0.1.9/apps/web/src/components/chat/useChatTerminalActions.ts),
[settle actions](https://github.com/pingdotgg/t3code/blob/v0.1.8/docs/user/thread-sidebar.md),
and [settings and cleanup](https://github.com/pingdotgg/t3code/blob/v0.1.8/docs/user/project-settings.md).

## Choose a topic

- [T3 issue-to-PR workflow](t3-issue-to-pr.md) Track an issue, run project checks, and follow the resulting PR from the thread.
- [Schedules and workflows](https://github.com/bluehexagons/basaltwater/blob/main/common/agent_skills/basaltwater-subagents/references/t3-scheduling.md) Choose a native task or workflow and recover interrupted runs.

<a id="shared-016-client-workflows"></a>
<a id="shared-017-client-workflows"></a>
<a id="shared-018-client-workflows"></a>
<a id="shared-019-client-workflows"></a>
<a id="shared-0110-client-workflows"></a>

- [T3 client features by release](t3-client-feature-history.md) Check which client workflows were introduced in T3 Code 0.1.6 through 0.1.10.

<a id="visual-replies"></a>

- [T3 visual replies](t3-visual-replies.md) Return artifacts using the paths supported by the active client.
