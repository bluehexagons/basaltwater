# Managed T3 service, pairing, and Git

Use the section relevant to the failing readiness check; do not collect every
log by default.

## Service and logs

For bounded redacted evidence, choose a new output file in an existing private
directory and keep event bodies in the artifact:

```bash
basaltw agent diagnostics --service t3code.service --window 1h \
  --summary --output /absolute/private/new-evidence.json
```

The summary retains collection issues and event counts, not service-health
proof. Inspect relevant artifact events when needed. If collection or saving
fails, read the issues and exit status; a save failure keeps events on stdout.

For missing readiness evidence or startup details, inspect the per-user service
without sudo:

```bash
systemctl --user status t3code.service
journalctl --user -u t3code.service -n 100 --no-pager
tail -n 100 ~/.t3/userdata/logs/boot-service.log
```

The upstream unit is `~/.config/systemd/user/t3code.service`;
`~/.config/systemd/user/t3code.service.d/basaltwater.conf` holds Basaltwater's
networking/workspace settings. On managed Debian services, startup tokens,
pairing URLs, and QR rows are filtered before logging. The first start with
the filter also scrubs those values from previous numbered rotations. Other
application/provider output is not fully sanitized: review and redact excerpts
before sharing. Pairing commands return credentials directly; keep their output
private.

Interpret warnings against the doctor inventory:

- Optional unselected executables may fail health checks. Do not install an
  unrequested provider to silence a warning.
- `SourceControlProviderError` with `provider: unknown` can be expected for an
  intentional local-only repository. Confirm no remote and working ordinary
  Git operations. Repair a remote only when the project should have one.
- A deliberate preview wait timeout is action evidence; it does not establish
  a service failure without other failed operations/readiness.

User-cache maintenance bounds numbered log rotations; host doctor reports log
usage. Do not delete current logs while T3 runs. For slow startup, inspect the
boot log before retrying; migration readiness can take 30 minutes. Follow
[updates](updates.md) for recovery without starting a second server or editing
migration state.

## Provider updates and records

Managed Debian headless setup exposes `t3` through `~/.local/bin/t3`; it follows
the selected service runtime. Setup preserves any existing command at that
path. Check `command -v t3` and `t3 --version` before using host CLI commands;
`~/.local/bin/basaltwater-t3code-pairing-provider` invokes the managed runtime
directly when another installation takes precedence.

Use **Update all** at the sidebar bottom for requested bulk updates across
connected environments. For one provider, use **Settings → Providers** or
`basaltw agent update HOST USER --tool TOOL`. Record readiness afterwards:

```bash
basaltw agent doctor --tool codex --capability t3code --capability host --record
```

Basaltwater's readiness/rollback history belongs to updates through
Basaltwater; T3 updates do not create these records automatically.
Stable 0.1.8 recognizes mise-managed provider installations and updates them
through mise. Preserve the owning installer rather than adding a second global
installation. Refresh discovery in **Settings → Providers** and inspect the
reported executable/version; a catalog entry alone does not establish readiness.
See the [upstream mise fix](https://github.com/pingdotgg/t3code/pull/21524).

## Additional providers

T3's built-in Codex provider can use its managed ChatGPT connection or a CLI
login on the selected host. Changing the CLI login leaves the managed connection
in place. The managed ChatGPT connection offers Standard speed. Model and tool
availability follow the selected instance's login, CLI version, and workspace
settings. For multiple accounts, check the configured Codex home. A shadow home
shares session history and configuration with the primary home while keeping its
own login. Separate homes keep sessions isolated. For native image generation,
check the selected instance's account, model, and client support. See the
[tagged Codex provider guide](https://github.com/pingdotgg/t3code/blob/v0.1.10/docs/user/providers-codex.md).

**Settings → Providers → Add provider → Show more** offers ACP Registry agents
and local ACP commands. They run on the T3 environment host. Registry agents
use their own authentication and sandbox controls. Local commands take an
executable and literal arguments. T3 negotiates ACP v2 preview or v1 according
to provider support, and model options and session import depend on capabilities
the provider reports. Basaltwater's setup manages its selected CLI providers.
Use the [tagged ACP guide](https://github.com/pingdotgg/t3code/blob/v0.1.10/docs/user/providers-acp.md)
for deliberately adding or removing a T3-managed provider.

## 0.1.6 conversation, project, and credential workflows

Use `/side` or `/btw` for an owner's side question, or **Side chat** in the right
panel's **+** menu. It carries the main thread's context and shares its folder
while that thread continues working. In 0.1.8 it starts with the parent's
composer model, effort and permissions, including the first reply. Existing
side chats keep their own settings when the parent changes. Keep concurrent edits
in explicitly bound worktrees. **Bring back** includes the discussion with the
next main-thread message while the main thread is idle. Side chats are not
delegated task receipts or a replacement for `delegate_task`.

Pin projects from a picker or **Settings → Projects**. Archive/unarchive in
that settings list; archiving retains files, settings, and threads and hides
the project from pickers. All of a project's checkouts must be connected.
Inspect running work and schedules separately before retiring a project.
For local commit review, select **Commits** in the Diff pane source picker;
it lists the newest 100 commits in the branch comparison and compares merges
against their first parent.

**Settings → Secrets** adds 1Password accounts as sources for provider variables
and source-control credentials in builds enabling `T3CODE_FEATURE_SECRETS` at
build time. An absent screen is not repaired by setting a service environment
variable. The environment host needs a working `op` CLI
and account access. Do not install/enroll it or replace existing authentication
incidentally. This configuration is separate from `request_secret`, which
remains the private agent input route with one-use references/file delivery.

For 0.1.8's private cards, headless secret providers, `/clear` and optional API
generators, read the shared
[client workflows](https://github.com/bluehexagons/basaltwater/blob/main/common/agent_skills/basaltwater-project-development/references/t3-workflows.md#shared-018-client-workflows).

Contracts: [side chats](https://github.com/pingdotgg/t3code/blob/v0.1.8/docs/user/thread-sidebar.md#side-chats),
[project organization](https://github.com/pingdotgg/t3code/blob/v0.1.6/docs/user/project-settings.md#pin-and-archive-projects),
[commit review](https://github.com/pingdotgg/t3code/blob/v0.1.6/docs/user/source-control.md),
and [1Password driver](https://github.com/pingdotgg/t3code/blob/v0.1.6/packages/secret-provider-onepassword/src/server/driver.ts).

## Attention alerts

For long-running work, the owner can opt into **Thread notifications** and
separate **In-app notifications** in **Settings → General → Notifications**.
Both default off. System alerts/sound cover completion, failure, input, and
approval on that device while T3 is open; focused-app toasts have their own
switch. Browser system alerts need HTTPS and notification permission, or the
desktop app. These are attention aids, not task wakeups, retained receipts, or
a guarantee of background push delivery. Do not change the owner's notification
preferences as a side effect of scheduling. See the
[notification controls](https://github.com/pingdotgg/t3code/blob/v0.1.4/apps/web/src/components/settings/NotificationSettings.tsx).

## Pairing and browser sessions

From the control system, request a one-time URL:

```bash
basaltw agent web pair HOST USER
```

Use the full returned URL; a bare URL showing a pairing-key form is expected.
Protected browser enrollment uses the HTTPS pairing endpoint and operator's
Basic Auth credentials. It automatically issues a one-time browser credential
and opens T3; `/devices` retains manual pairing and T3 Connect controls.
Use the primary T3 URL afterwards to reuse the native session.

With compatible 0.1.11 hosts and clients, directly paired device sessions
can renew with their device key while in use. Re-pair if a session expires
after inactivity or an older host cannot renew it. Check the active route's
permissions when switching between direct pairing and Connect. See
[direct pairing and routes](https://github.com/pingdotgg/t3code/blob/v0.1.11/docs/user/remote-access.md#pair-over-a-lan-or-private-network).

If pairing returns to the key form, check hostname/scheme changes and cookie
acceptance. Reopen the protected entry to recover a rejected session. Never
print cookies or credentials. An unpaired browser may log WebSocket auth
failures while showing the form.

For V2 browser-session diagnostics, inspect `permissions` from the authenticated
`/api/auth/session` response. Its `scopes` field contains only legacy-compatible
values; a shorter list there does not prove the granular grant is missing.
An explicitly empty `permissions` list grants nothing. Keep the response's
credentials/cookies private and compare the permission names only. See the
[response contract](https://github.com/pingdotgg/t3code/blob/v0.1.8/packages/contracts/src/auth.ts).

Older v0.0.45 clients may report a Clerk production-key domain error on private
HTTPS origins. Identify the failing origin and check native pairing separately;
rejected cloud requests do not establish a gateway, TLS, or local pairing
failure. Leave cloud sign-in unverified when it fails. Preserve managed
TLS/authentication policy and upstream assets.

## Connect device approval

T3 0.1.11 requires `relay:configure` for initial Connect linking and relay
configuration. Basaltwater-issued links carry the standard client grant,
including `relay:write` for existing relay preferences and unlinking.
For initial setup on managed Debian, run `t3 connect link --headless` as the
service account and arrange the restart described in
[Connect management](https://github.com/bluehexagons/basaltwater/blob/main/docs/services/device-pairing.md#t3-connect-management).

In 0.1.10 the owner can enable **Require approval for new devices** in the
host's **Settings → Sharing & access**. A new Connect device shows a code;
approve only the initiated device with a matching code from an already
connected device, or use the host's `t3 auth connect-device list` and
`t3 auth connect-device approve CODE`. Update older clients first and review
devices already trusted before enabling the setting. Revoke through the same
settings or `t3 auth connect-device revoke CODE`; with approval disabled that
device can connect again through the account. This is distinct from outside-agent
OAuth grants, direct-LAN pairing, and Basaltwater's root-action broker. Setup
does not change it. See [upstream device approval](https://github.com/pingdotgg/t3code/blob/v0.1.10/docs/user/remote-access.md#approve-new-devices).

## Outside agents

An independent agent can deliberately connect through T3's OAuth MCP endpoint
at the environment's `/mcp` route. In 0.1.9, **Use T3 Code in another app (MCP)** in
the command palette or **Settings → Sharing & access** guides app selection,
access level and URL setup. Over T3 Connect, sign in on the hosted T3 web app
with the account linked to that environment, inspect the agent/return address
and choose the required access level. No copied pairing code is needed.
**Use a sign-in code instead** opens the environment's own page. Older
environments use the wizard's one-use, five-minute code with its chosen grant.
Approve only a sign-in you initiated.
Use HTTPS or host-local loopback for sign-in; a cloud-hosted agent needs an
internet-reachable route such as T3 Connect. A private LAN/tailnet URL alone does
not supply that reachability. Revoke an unused grant through **Sharing & access**.

On the environment's own page, an existing browser session needs access-management
permission to approve without a code. Code-free approval starts at **Read only**;
inspect the displayed level on either approval route. Wizard codes retain
their selected grant. Another code can default to **Auto** or a lower policy
ceiling, so inspect the displayed access rather than assuming read only.
Supervised through full access
also permits starting, messaging, and stopping threads subject to the granted
mode ceiling. `T3-Mode-Limit: <runtimeMode>/<interactionMode>` can narrow an
OAuth request further; it cannot expand the grant and is ignored for injected
provider-session callers. See the
[mode-limit contract](https://github.com/pingdotgg/t3code/blob/v0.1.4/apps/server/src/mcp/McpInvocationContext.ts).

Use **Read only** for project, thread, and model discovery. Supply an explicit
`projectId` or `threadId` when a tool normally takes it from its caller, and use
`allProjects: true` for an environment-wide thread list. With an authorized
write grant, use `t3_thread_launch` and follow it with `t3_thread_wait` or
`t3_thread_read`. Delegation, interactive preview, secret requests, and device
tools require an agent running inside a T3 thread. Outside-agent OAuth is an
opt-in connection with its own grant. The
[tagged outside-agent guide](https://github.com/pingdotgg/t3code/blob/v0.1.10/docs/user/outside-agents.md)
covers setup from standalone Codex, Claude Code, ChatGPT, and other OAuth MCP clients.

## Guest links

For a requested one-thread collaboration, the owner can enable the **Guest
links** beta in **Settings → Labs** on the host connected to T3 Connect. The
thread's **Share with guest** link works in one browser for 24 hours. It exposes
recent messages, attachments, reasoning and command output. A guest's message
and files wait for the owner to send or dismiss. Use **Stop sharing** to revoke
the link. Review the thread before sharing it. See the
[tagged guest guide](https://github.com/pingdotgg/t3code/blob/v0.1.10/docs/user/guest-links.md).

## Linked environments

Stable 0.1.4 adds an opt-in **Linked environments** beta in **Settings → Labs**
on both machines. For a requested connection, use **Settings → Environments →
Linked environments → Link**, or the `t3_environment_link` approval card for a
machine the user named. Approve only the initiated link and needed access;
never copy pairing codes into chat. Basaltwater setup does not enable the beta
or create links. Keep existing TLS and source allowlists in place.

The initiating environment stores the peer credential in its secret store and
calls the peer's `/mcp` endpoint. The server must reach and trust that URL;
reachability from the viewing desktop alone is insufficient. Link access and
the calling agent's modes both bound remote operations. Below full access,
links can change only threads they started and skip project setup scripts.
Discovery, remote delegation, and workspace constraints are in the subagent
skill's [T3 reference](https://github.com/bluehexagons/basaltwater/blob/main/common/agent_skills/basaltwater-subagents/references/t3-orchestration.md#linked-environments).

On 0.1.11, links between compatible environments renew while agents use them.
After 30 days unused, or after 30 days with an older peer, link again using a
new code. **Forget** removes the local link and credential. Revocation in
the peer's **Settings → Sharing & access** ends its grant there. Turning the
beta off pauses link use and result delivery from that peer while existing
work continues. Do not treat
a forgotten link or unavailable result as proof that remote edits stopped.
See the [tagged link guide](https://github.com/pingdotgg/t3code/blob/v0.1.11/docs/user/remote-access.md#link-another-environment).

## Server-side Git

Inspect authentication and identity as the target user:

```bash
gh auth status
git config --global --get user.name
git config --global --get user.email
git config --global --get init.defaultBranch
```

Use HTTPS remotes with GitHub CLI's credential helper. Never print or copy
tokens from `~/.config/gh/hosts.yml`.

Basaltwater defaults new repositories to `main` while preserving a user's
chosen global default. Before the first commit, rename the symbolic branch
with `git branch -m main`; `git branch main` needs an existing commit. Create
the first commit before creating/switching to additional branches.
