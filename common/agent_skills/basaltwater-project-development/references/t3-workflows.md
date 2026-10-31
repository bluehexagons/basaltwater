# T3 project actions and visual replies

Use these integrations when the active T3 session exposes the relevant tools.
Follow its injected instructions and live schemas. This reference follows
[stable 0.1.6](https://github.com/pingdotgg/t3code/releases/tag/v0.1.6).

## Shared 0.1.6 client workflows

These workflows apply to compatible web/desktop clients on Debian, CachyOS,
native Windows, and Ubuntu WSL connections. They use the selected environment's
repository, provider, credentials, and installed skills.

- Start a side question with `/side` or `/btw`, or **Side chat** in the right
  panel's **+** menu. It inherits context and shares the thread's folder while
  the main thread continues. Its first reply asks before commands or edits;
  later replies use its composer permission mode. Use bound worktrees for
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

Contracts: [side chats](https://github.com/pingdotgg/t3code/blob/v0.1.6/docs/user/thread-sidebar.md#side-chats),
[project organization](https://github.com/pingdotgg/t3code/blob/v0.1.6/docs/user/project-settings.md#pin-and-archive-projects),
[commit review](https://github.com/pingdotgg/t3code/blob/v0.1.6/docs/user/source-control.md),
and [1Password resolution](https://github.com/pingdotgg/t3code/blob/v0.1.6/packages/secret-provider-onepassword/src/server/driver.ts).

## Project commands and worktree setup

Keep the project's native build, test, and development commands canonical.
When adding T3 actions is useful to the requested work, point them at those
commands instead of maintaining a second build system. Read existing actions
with `t3_project_read`; inspect environment preferences with
`t3_environment_read` before changing settings. A saved action or accepted
launch is not evidence that its command finished successfully.

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

Contract: [project file schema](https://github.com/pingdotgg/t3code/blob/v0.1.6/packages/contracts/src/t3ProjectFile.ts)
and [settings and cleanup](https://github.com/pingdotgg/t3code/blob/v0.1.6/docs/user/project-settings.md).

## Visual replies

For a chart, comparison, diagram, or interactive explanation that communicates
the result better, build self-contained HTML with inline styles and scripts.
Call `html_preview`, inspect the screenshot and console output, fix relevant
errors, then publish with `html_render` using the measured content height and
the live tool's theme/layout rules. Check a narrow viewport when relevant.
Follow the session's instruction for the accompanying final reply.

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

Contracts: [visual replies](https://github.com/pingdotgg/t3code/blob/v0.1.6/docs/user/html-renders.md)
and [HTML tools](https://github.com/pingdotgg/t3code/blob/v0.1.6/apps/server/src/mcp/toolkits/html/tools.ts).
