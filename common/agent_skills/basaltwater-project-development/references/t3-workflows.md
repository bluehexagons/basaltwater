# T3 project actions and visual replies

Use these integrations when the active T3 session exposes the relevant tools.
Follow its injected instructions and live schemas. This reference follows
[stable 0.1.8](https://github.com/pingdotgg/t3code/releases/tag/v0.1.8).

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

## Shared 0.1.6 client workflows

These workflows apply to compatible web/desktop clients on Debian, CachyOS,
native Windows, and Ubuntu WSL connections. They use the selected environment's
repository, provider, credentials, and installed skills.

- Start a side question with `/side` or `/btw`, or **Side chat** in the right
  panel's **+** menu. It inherits context and shares the thread's folder while
  the main thread continues. In 0.1.8 it inherits the parent's composer model,
  effort and permissions from the first reply; later parent changes do not
  change an existing side chat. Inspect its own composer. Use bound worktrees for
  concurrent edits. **Bring back** includes the discussion with the next
  main-thread message while that thread is idle. It does not create a delegated
  task receipt or merge Git changes.
- Pin projects in pickers or **Settings → Projects**. Archive/unarchive in
  that settings list; archive hides a project from pickers and retains its
  files, settings, and threads. All checkouts must be connected for a project
  spanning machines. Inspect running work and schedules separately.
- Choose **Commits** in the Diff pane source picker for the newest 100 commits
  in the branch comparison. Merge commit diffs use the first parent.
- Builds enabling `T3CODE_FEATURE_SECRETS` at build time expose **Settings →
  Secrets** for 1Password-backed provider variables and source-control
  credentials. The environment running the provider needs a working `op` CLI
  and account access. A Windows installation/login does not establish Ubuntu
  access. Setup does not enroll 1Password; setting a service environment
  variable does not enable the compiled UI. Continue using `request_secret`
  for private agent input with one-use references or file delivery.

Contracts: [side chats](https://github.com/pingdotgg/t3code/blob/v0.1.8/docs/user/thread-sidebar.md#side-chats),
[project organization](https://github.com/pingdotgg/t3code/blob/v0.1.6/docs/user/project-settings.md#pin-and-archive-projects),
[commit review](https://github.com/pingdotgg/t3code/blob/v0.1.6/docs/user/source-control.md),
and [1Password resolution](https://github.com/pingdotgg/t3code/blob/v0.1.6/packages/secret-provider-onepassword/src/server/driver.ts).

## Shared 0.1.7 client workflows

- **Child approvals:** the parent conversation shows the child's name and
  requested action. The owner can approve/reject there; this does not give the
  parent agent additional approval authority. For a linked child, check the
  owning environment. Questions and permission approvals remain separate flows.
- **Thread links:** use **Copy → Thread**, or **Copy thread** on mobile, to
  share a link that opens the conversation. A copied reference can prefer its
  PR. Recipients still need access to the machine; copying a link does not
  publish the thread. Preserve tool-returned `link` values verbatim in agent replies.
- **Usage filters:** `usageModelFilter` in the server's `userdata/settings.json`
  supports `include`/`exclude` lists of `{provider, model}` selectors; omit
  `provider` to match a model across providers. Use the IDs Usage shows before
  **Map to**: filtering precedes that mapping, and each environment has its own
  filter. Invalid filters are ignored and can disappear on the next settings
  save. Change these settings only when requested. Filtered API-equivalent cost
  estimates are not a subscription bill or complete accounting of all work.
- **Model history:** conversation markers show model or reasoning-effort changes.
  Use them to locate a change when reviewing behavior; read the current thread
  configuration before reusing settings for another task or schedule.
- **Tagged-thread hiding:** the server settings `hiddenThreadTags` and
  `hideTaggedThreads` can hide matching threads from lists for recording or
  presentation. Hiding does not stop work, revoke access, or redact conversation
  contents. Inspect the actual recording surface for private material.

One-time follow-ups use T3's new `once` schedule; the subagent skill's
[scheduling reference](https://github.com/bluehexagons/basaltwater/blob/main/common/agent_skills/basaltwater-subagents/references/t3-scheduling.md)
owns the schema, timezones, and dispatch checks. Machine-wide managed policy can
cap modes or disable providers, remote access, and updates; inspect installed
versus loaded policy with `t3 policy show` on the environment host. Windows
policy does not configure a server inside WSL and disables desktop-managed WSL
connections. Read the [platform boundaries](t3-platforms.md) for that restriction,
desktop CLI installation, and Debian's default systemd-path requirement.
The T3 operations skill's
[update reference](https://github.com/bluehexagons/basaltwater/blob/main/common/agent_skills/basaltwater-t3code/references/updates.md#release-tracks-and-archive-layout)
owns policy diagnostics. Setup preserves the owner's feature settings.

Contracts: [approvals](https://github.com/pingdotgg/t3code/blob/v0.1.7/docs/user/permission-modes.md),
[thread links](https://github.com/pingdotgg/t3code/blob/v0.1.7/docs/user/prompt-links.md#share-a-thread),
[usage filters](https://github.com/pingdotgg/t3code/blob/v0.1.7/docs/user/usage.md#show-only-some-models),
[model history](https://github.com/pingdotgg/t3code/blob/v0.1.7/apps/web/src/components/chat/V2LifecycleRow.tsx),
and [tag settings](https://github.com/pingdotgg/t3code/blob/v0.1.7/packages/contracts/src/settings.ts).

## Shared 0.1.8 client workflows

- **Context reset:** send `/clear` alone to start a fresh provider session in
  the same thread. Earlier conversation and diffs stay visible, but are not
  supplied to the new session. Save a durable checkpoint before a deliberate
  reset. Compaction retains a summary; **Restart agent session** reloads the
  session's guidance and preserves conversation context. Do not substitute
  `/clear` for a skill reload or assume it erases retained history.
- **Private secrets:** call `request_secret` for the private card. Use its
  one-use `secretRef` with a supported T3 tool, or `delivery: "file"` for a
  command. Read that owner-only file inside the command without printing the
  value; it expires after about 24 hours. A configured secret provider can
  answer the card with a reference. The request card works even when the
  preview **Settings → Secrets** page is absent. Headless 1Password access
  needs service-account or Connect authentication on the server, not an
  unattended desktop approval. Private sudo entry does not expand the task's
  authority or replace Basaltwater's privilege approval route.
- **Optional text generators:** Settings can select a direct OpenAI, Anthropic,
  OpenRouter or compatible API route for titles, branch names, commit/PR text
  and transcript cleanup. It needs separate API credentials/billing and can
  send the staged diff to that endpoint. A coding subscription does not cover
  those requests automatically. Preserve the existing agent route unless the
  owner chooses a generator; PR review guides still use a coding agent.
- **Thread state:** default automatic settlement includes seven days of
  inactivity. Inspect **Settings → Threads**, per-thread **Auto-settle
  behavior**, project settle actions and worktree retention before relying on
  a thread staying active. Pending work/input blocks automatic settlement;
  snoozing an existing question leaves it unanswered. Pinning does not prevent
  settlement, and an open PR protects against inactivity only when configured.

Fresh-session schedules use the [scheduling reference](https://github.com/bluehexagons/basaltwater/blob/main/common/agent_skills/basaltwater-subagents/references/t3-scheduling.md).
Preserve explicit model selections as discovery refreshes; new catalog entries
do not override the shared Sol/Opus cost guidance.

Contracts: [composer](https://github.com/pingdotgg/t3code/blob/v0.1.8/docs/user/composer.md),
[secrets](https://github.com/pingdotgg/t3code/blob/v0.1.8/docs/user/secrets.md),
[text generation](https://github.com/pingdotgg/t3code/blob/v0.1.8/docs/user/text-generation.md),
and [thread state](https://github.com/pingdotgg/t3code/blob/v0.1.8/docs/user/thread-sidebar.md).

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

`runOnSettle` can execute on every settlement of a thread with its own
worktree. Use it only for an intended, repeatable project operation; do not
attach broad cleanup, publication, or deployment as an incidental setup step.
Settling is separate from merging Git changes and may make a worktree eligible
for configured automatic cleanup. A script's `previewUrl` is a desktop-build
feature; use the browser skill's server-tab workflow for remote automation.

In 0.1.8, `stopOnSettle: true` can stop an action's terminal and child processes
when its thread settles. It applies to commands started through that action;
provider shell commands retain their provider's lifecycle. Inspect this setting
alongside `runOnSettle`, including automatic settlement, before starting a
long-lived command. Do not add it to existing actions incidentally.

Contract: [project file schema](https://github.com/pingdotgg/t3code/blob/v0.1.8/packages/contracts/src/t3ProjectFile.ts),
[settle actions](https://github.com/pingdotgg/t3code/blob/v0.1.8/docs/user/thread-sidebar.md),
and [settings and cleanup](https://github.com/pingdotgg/t3code/blob/v0.1.8/docs/user/project-settings.md).

## Visual replies

For a chart, comparison, diagram, or interactive explanation that communicates
the result better, build self-contained HTML with inline styles and scripts.
Call `html_preview`, inspect the screenshot and console output, fix relevant
errors, then publish with `html_render` using the measured content height and
the live tool's theme/layout rules. Check a narrow viewport when relevant.
Follow the session's instruction for the accompanying final reply.

Stable 0.1.8 also accepts `path` with an absolute `.html` file path in both
tools. Follow the live schema and retain the file when an export is useful;
a file on disk alone does not publish an inline result.

Local image paths are embedded when published; remote URLs remain remote
dependencies. Keep needed images local and exclude private material from the
page. The sandboxed page is a thread artifact: it does not deploy a site,
provide a backend, or verify a real application's behavior. For requested
product changes, implement and test the actual project. For exportable or
publication-quality plots, retain the normal standalone artifact too.

`html_preview` uses T3's own headless Chrome, shared with its server browser.
An initial browser download is preparation, not an unavailable viewing client;
follow the returned retry guidance. Missing host libraries, fonts, or sandbox
support need the browser skill's host-setup route. Do not install another
automation stack or disable the sandbox to repair an HTML preview. If preview
is unavailable, report that validation gap; publishing itself does not require
Chrome. Independent terminal tasks do not inherit these tools or rendered pages.

Contracts: [visual replies](https://github.com/pingdotgg/t3code/blob/v0.1.8/docs/user/html-renders.md)
and [HTML tools](https://github.com/pingdotgg/t3code/blob/v0.1.8/apps/server/src/mcp/toolkits/html/tools.ts).
