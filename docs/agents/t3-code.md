# T3 Code server

This guide covers T3 Code's server-side web interface on managed Debian
profiles. The retired desktop AppImage installation is not managed.

For an existing CachyOS KDE workstation, use the limited local profile described
in the [CachyOS guide](../workstations/cachyos.md). Choose the native `t3code-bin` package with
`--t3code-desktop` using the [desktop installer example](../workstations/cachyos.md#install-with-t3-code-desktop),
or the [managed web service](../workstations/cachyos.md#t3-code-web-service-host-locally-or-on-a-trusted-lan)
for loopback access, direct private-LAN pairing, and optional T3 Connect.
The two setup flags are mutually exclusive and can be switched on later runs;
each environment retains its own data.
The VM/server options below do not apply to `agent_cachyos`.

Use either the focused profile:

```bash
basaltw setup server_dev vm.example agent \
  --t3code-ready \
  --access-source 192.168.1.0/24
```

or select the interface and providers explicitly:

```bash
basaltw setup server_dev vm.example agent \
  --agent-tool gh \
  --agent-tool codex \
  --web-interface t3code \
  --device-pairing t3code \
  --git-access read-write \
  --access-source 192.168.1.0/24
```

The usual client is the separately installed T3 Code desktop or mobile app.
Generate a one-time URL from the control system:

```bash
basaltw agent web pair vm.example agent
```

T3 Code sessions can expose collaborative preview tools. Stable 0.1.4's server
tools automate tabs marked `runtime: "server"`; listed tabs with desktop or
missing runtime belong to the user's local app. Report that those tabs
are inaccessible to server automation and open a server tab without passing
their IDs. Desktop automation requires tools that explicitly support it.
In those sessions, use preview first: status, then open if no capable tab is
attached. Fallback is allowed for absent preview tools, an explicit user request
for another browser, or an explicit unsupported/unavailable response from open.
Follow the active session's additional bounded retry and fallback rules for
navigation, timeout, and certificate failures, including two failures on the same
step when specified; quote the raw error when switching.
For independent SSH or terminal sessions, request VM-local Chromium explicitly:

```bash
basaltw setup agent_code_vm vm.example agent \
  --browser-automation playwright
```

Stable 0.1.2 prefers its server browser, which uses the server's routes and
certificate trust and can keep working after a viewing client disconnects.
Desktop-hosted tabs use the desktop's network and trust instead. Use the
[browser workflow](browser-automation.md#collaborative-preview-and-private-networks)
for tab attachment, verified input, recordings, and bounded recovery. If only
client-origin testing fails, continue with healthy VM-local Playwright when
session policy permits fallback, or
non-browser checks. For a desktop-browser `ERR_CERT_AUTHORITY_INVALID`, optional
[client CA enrollment](../services/client-ca-trust.md) can restore private-origin access;
a timeout or unreachable address needs network diagnosis. Never bypass TLS.

For server-hosted preview, use `target: {kind: "environment-port", port: PORT}`
to reach a VM loopback development port directly. When a desktop-hosted browser
needs a reachable VM URL, run
`basaltwater-web preview resolve --port PORT --json`. It returns a verified
owned gateway URL for `preview_navigate`, or an exact forward creation command
with actionable failure fields. See [Internal HTTPS previews](../services/internal-web.md#forward-an-existing-loopback-service).
The browser guide also covers navigation/component attribution, uncertain
input acknowledgments, and [offline-cache freshness](browser-automation.md#offline-cache-freshness).
For T3's own Chrome download, missing libraries/fonts, or AppArmor errors, use
[T3 browser host preparation](browser-automation.md#t3-browser-host-preparation).
The ordinary T3 doctor checks service readiness, not a browser launch.

Agent-enabled T3 setups install T3-only preview guidance, or the combined
Playwright/T3 skill when both capabilities are selected, plus focused T3 Code
and HTTPS-gateway guidance. See
[Managed agent workflow skills](agent-skills.md) for the installation matrix.
Selected Claude Code receives the catalog under `~/.claude/skills`; Codex and
OpenCode share `~/.agents/skills`. Bundled references ship with the entrypoint,
so host-side migration instructions remain available outside this repository.
Successful setup queues selected T3 startup/reload after provider configuration,
skills, saved state and notifications. Debian's completion worker waits until
the setup process exits, then starts/reloads the shared service and checks
stable HTTP readiness. Its upstream updater can also start a newer runtime
during installation; the final reload picks up providers installed later.
Internal non-refresh service checks reuse a healthy running service. CachyOS
stages its web runtime without stopping the terminal's service, then queues
activation/readiness after setup. Its desktop worker gracefully reloads a
running app or starts a stopped app when a graphical session is available;
otherwise KDE login startup remains enabled. Native Windows uses a same-session
scheduled task after successful setup receipts and reports when manual reopening
is needed. See [Windows setup](../workstations/windows.md).

Debian `sudo basaltw refresh` runs in an independent system service, including
when invoked from a local T3 terminal or over SSH from another environment.
The caller's exit 0 confirms dispatch. Inspect the printed private refresh log,
refresh unit result and final T3 completion unit result before assuming success.
CachyOS keeps package and sudo prompts interactive until the completion handoff.
Queue acceptance does not prove app or provider readiness: inspect the printed
unit/status, then verify an authenticated provider turn and terminal. See
[refresh behavior](../reference/command-line.md#refresh-this-machine).

When Codex is selected, root-owned Codex defaults make direct CLI sessions use
auto-reviewed workspace access. They do not restrict explicit user or client
choices, so T3 can retain its upstream full-access default and users can select
other Codex permission modes. `--harden-agent` adds system requirements that
enforce the workspace boundary without an approval escalation path. This is a
Codex policy and does not change another provider CLI's native permission model
or remove T3's collaborative preview. See
[Agentic coding security](agent-security.md).
The workspace skill uses the safe local lifecycle command:

```bash
basaltw agent workspace create ~/repos/PROJECT TASK --json
basaltw agent workspace remove WORKTREE --dry-run --json
```

The removal path rejects dirty, untracked, or unmerged work and cannot remove
the primary checkout. For a shareable diagnostic snapshot that omits log and
credential contents, use `basaltw agent support-bundle`.

## Stable feature baseline

As checked on **2026-10-29**, GitHub's latest stable release and npm's `t3@latest`
are **0.1.5**, released October 29. It retains the stable Orchestrator V2
introduced in 0.1.0 and service-state protocol 3. Keep the
server and every connected desktop client on compatible V2 releases; mobile
requires the 2.0 app. See the [upstream release notes](https://github.com/pingdotgg/t3code/releases/tag/v0.1.5)
and [V2 migration notes](https://github.com/pingdotgg/t3code/releases/tag/v0.1.0).

The release's recommended provider versions are Codex **0.159+**, Claude Code
**2.1.280+**, Grok **1.0.13+**, OpenCode 2 **2.0.18+**, and Pi **1.0+**.
The [provider compatibility manifest](https://github.com/pingdotgg/t3code/blob/v0.1.5/apps/server/src/provider/model-manifest.json)
also marks Codex 0.156+ and Pi 0.80.5+ supported; distinguish recommended
versions from minimum support. Inspect **Settings → Providers** for the selected account,
binary, version, and readiness. Setup manages only selected Basaltwater
providers; T3's additional Pi, Muse Code, Cursor SDK, and ACP Registry choices
do not imply that Basaltwater installed, authenticated, or qualified them.
Antigravity now recommends **1.4.1**, with 1.3.0 and 1.1.1 also supported;
0.1.5 adds Claude/GPT model discovery and thinking controls to that integration.
This does not qualify it as a Basaltwater-managed provider.

The 0.1.5 integration review verified the unchanged service protocol, runtime
layout, and administrative permission contract against tagged source. Its
checksum-verified Linux x64 archive passed fresh-home and 0.1.4-to-0.1.5 upgrade
checks for setup/doctor runtime selection, managed wrapper execution, embedded
native-module loading, filtered HTTP startup, all 16 administrative pairing
scopes with bootstrap-session revocation, database integrity, host-side thread
listing, and Basaltwater's idle activity query. Seeded project metadata survived
the upgrade; both releases record 72 SQL migrations. The new preflight refused
a fixture with a newer migration without changing its database; see the
[preflight contract](https://github.com/pingdotgg/t3code/blob/v0.1.5/apps/server/src/cloud/servicePreflight.ts).
The executable resolved its bundled `lib/libatomic.so.1` through `$ORIGIN/lib`;
preserve the complete archive during recovery. No runtime compatibility patch
was needed.
The earlier 0.1.3-to-0.1.4 check covered the increase from 69 to 72 migrations.

Native Windows/CachyOS source review found compatible installer names,
executable paths, archive staging, and graceful completion handling. These
checks do not qualify systemd installation/reboot, authenticated provider turns,
linked-environment execution, native package installation or desktop/mobile
clients, Connect, or the complete live matrix below. Update the desktop client
itself for 0.1.5's Electron/Brotli security fix; a headless server update does not
patch a separately installed client.

Stable 0.1.3 defaults a background service without an explicit host to a
private-network listener. Basaltwater supplies `T3CODE_HOST` in its Debian
service drop-in and an explicit `--host` for CachyOS web, preserving the
configured bind and source allowlist. The new RPC/media WebSocket-ticket
distinction keeps the older ticket endpoint compatible; the managed HTTPS
gateway forwards paths, queries, and WebSocket upgrades without rewriting
those tickets. Connect's pinned relay client advances to cloudflared
2026.10.4; T3 owns that download and verification.

Stable 0.1.4 adds the opt-in **Linked environments** beta, remote delegation,
environment-scoped thread links, and retry keys for top-level launch. Enable
the beta on both machines only for a requested connection; Basaltwater setup
does not enroll peers or change the owner's Labs settings. Below full access,
a link can modify only its own threads and skips project setup scripts. Remote
delegation uses the peer project checkout and rejects worktree isolation.
See [link operations](../../common/agent_skills/basaltwater-t3code/references/operations.md#linked-environments)
and the [delegation contract](../../common/agent_skills/basaltwater-subagents/references/t3-orchestration.md#linked-environments).

On Linux, 0.1.4 adds `Delegate=yes` to T3's agent scopes so rootless containers
can manage nested cgroups. T3 owns this scope configuration; it does not require
a new Basaltwater service override or container installation. See the
[tagged scope implementation](https://github.com/pingdotgg/t3code/blob/v0.1.4/apps/server/src/process/agentScope.ts).

OpenCode 2 uses the separate `@opencode/cli` package and converts OpenCode's
shared database on first run. Do not install/run 1.x and 2.x side by side or
silently replace the managed OpenCode installation as part of a T3 update.
Plan that provider migration separately with a private recovery copy. OpenCode
1.x retains limited V2 support, but pending approvals/questions do not reappear
after a T3 restart; answer or stop them before updating. Basaltwater does not
claim OpenCode 2 qualification from installing the existing OpenCode tool.

Stable 0.1.5 also lets users choose rows in the thread details card through its
context menu or **Settings → Appearance → Thread details**. Enable **Usage**
there for the selected provider account's limit windows and thread context;
hidden rows retain their keyboard shortcuts. Setup preserves these UI choices.
See the [usage guide](https://github.com/pingdotgg/t3code/blob/v0.1.5/docs/user/usage.md).

### Agent workflow integration

The shared installed skills now route T3 agents to its native orchestration:

| Work | Integration |
| --- | --- |
| Delegated children | Discover provider/model IDs through `orchestrator_capabilities`; use native same-provider workers only when they support the chosen model and no options are requested/inherited, otherwise `delegate_task`. Retain task IDs, pass explicit context, and collect asynchronous results. Each independent review round is a fresh task. Read current questions/approvals through their appropriate flow; cancelling an old task also stops later work in its child thread. |
| Linked environments | Opt in on both machines under Labs. Discover with `t3_environment_links`; query the peer's catalog with `environmentId`. Remote `delegate_task` uses `target.environmentId` and optional `target.projectId`, runs in the peer checkout, and rejects worktree isolation. Retain `taskId` when no local `childThreadId` exists. Respect both caller and link ceilings. |
| Editing isolation | Select `delegate_task` worktree isolation or an explicit top-level thread workspace before starting work. T3 owns its worktree binding and cleanup; Basaltwater commands manage their own ownership records. A shell `cd` does not rebind a thread. |
| Conversation/context | Fork and merge-back transfer context separately from Git integration. Provider switches can carry command output, filenames, and plans as budgeted historical text; native tool state, reasoning, and attachments are not replayed. Read omitted history in bounded pages. |
| Queues and recovery | Choose queue versus steering explicitly and inspect pending messages before retrying. Persistent queues wait for user resume after restart; interrupted-turn auto-continuation and usage-reset resume have separate controls. |
| Recurring work | Choose T3 schedules for work returning to a T3 thread, or Basaltwater tasks for independent Codex execution and bounded goals. `schedule_task` updates by ID. 0.1.4 also exposes `run_scheduled_task_now` with `taskId` for authorized immediate runs; dispatch is not completion. Bound runs follow current thread settings; fresh-thread MCP tasks use remote `main` worktrees. Inspect stored model/workspace settings in the task UI. Filter/deduplicate webhooks before provider work. Use one scheduler per job. |
| Pull requests | Register PRs through `link_pull_request`. For requested monitoring, watch once, yield for wakeups, then unwatch before handoff. Watches persist across wakes. Read existing guides without starting a model run; generation is optional and uses project guide settings. Check head SHA/staleness alongside diff review and checks. |
| Project actions | Reuse canonical project commands through imported `t3.json` actions. Set worktree setup `async: false` when it must finish before the agent starts. Preserve project/environment overrides and inspect settle actions before cleanup. |
| Visual results | Use `html_preview` then `html_render` for useful interactive charts, diagrams, comparisons, or mockups. T3 embeds local images and publishes a sandboxed thread artifact; it does not deploy the project or verify its behavior. |

The shipped [T3 orchestration reference](../../common/agent_skills/basaltwater-subagents/references/t3-orchestration.md)
and [T3 scheduling reference](../../common/agent_skills/basaltwater-subagents/references/t3-scheduling.md)
are shared by Debian, CachyOS, and WSL. They cover retries, cancellation,
questions, workspace preparation, scheduler ownership, webhook signatures and
private secret entry. They use T3's injected tools rather than a second
Basaltwater orchestration service. Tool availability still depends on the active
provider session. Managed setup reloads these resources by refreshing T3.
For changes outside setup, finish the current turn and use **Restart agent
session** to reload one thread.

The project-development skill's [T3 workflow reference](../../common/agent_skills/basaltwater-project-development/references/t3-workflows.md)
provides the project-action and visual-reply routes. For deliberate outside-agent
access, use T3's OAuth `/mcp` integration described in the
[operations reference](../../common/agent_skills/basaltwater-t3code/references/operations.md#outside-agents).
It does not automatically connect independent Basaltwater tasks or give them a
thread's tools. Outside OAuth grants default to read only; `T3-Mode-Limit` may
narrow their modes, never widen them. Use returned thread `link` values verbatim:
current native links include environment and thread IDs, while outside OAuth
clients receive browser URLs. Stable 0.1.4's `t3_thread_launch` accepts a stable
`clientRequestId` for retries of one launch. Optional device-local
completion/input/approval alerts live in
**Settings → General → Notifications** and require an open client.

Inspect project overrides and environment settings before relying on tracked
`t3.json`: repository defaults have lower precedence than both. Preserve setup
actions, submodule policy, and branch naming when preparing a worktree. T3's
opt-in cleanup can discard ignored files depending on its local-file setting;
inspect retention before settling work or removing a checkout. Keep its cleanup
separate from Basaltwater's owned-worktree ancestry checks and cache maintenance.

### Stable feature qualification

These are required live checks, **not completed qualification evidence**. Run
them on disposable systems with the exact candidate commit and compatible stable
client/server. Record the provider executable/version and model, runtime and
permission modes, service versus desktop environment, checked result, and any
blocked step. Mocked tests and service HTTP readiness cannot qualify these paths.

| Case | Required evidence |
| --- | --- |
| Providers and migration | Fresh 0.1.5 setup, 0.1.4-to-0.1.5 upgrade, and V1-to-V2 copy migration with matching client, selected Codex/Claude combinations, private recovery copy, pairing permissions, authenticated provider turn, and terminal. Qualify OpenCode 2 separately before claiming support. |
| Project promotion and release tracks | Promote a stopped No project folder in place; retain its files, all associated threads and schedules, then initialize Git. Exercise a deliberate Stable/Nightly switch on disposable installations, including offline/unsupported machines, incompatible or unreadable databases, and an incomplete switch whose requested track differs from its running version. Preserve database compatibility refusals. |
| Delegation | Same-provider and cross-provider child results, explicit model/options, fresh review rounds, async wake, nested completion state, pending question, cancellation, and restart interruption without duplicated work. |
| Linked environments | On deliberately linked disposable peers, verify beta gates on both sides, server reachability/trust, read-only and narrower-mode denials, repository matching, missing/ambiguous project selection, remote result/restart/cancel, rejected worktree isolation/attachments, thread links, launch retry deduplication, expiry and peer revocation. Confirm forgetting a link does not claim to stop remote edits. |
| Workspace/context | Child worktree starts from committed local base; blocking setup completes before the first turn, while async setup is checked before dependent work. Uncommitted edits stay in parent. Explicit launch/handoff binding works, historical context stays distinguishable from native tool state, and cleanup/settle actions preserve unrelated work. |
| Queues and limits | Queue order and attachments survive restart, queue awaits user resume, steering reaches the intended turn, auto-continuation is controlled, and usage-limit/reset recovery does not duplicate edits. |
| Scheduling | Interval and server-time fixed schedules, current-thread and requested fresh-thread binding, safe model/options, unified update/pause/resume/delete, UI and MCP run-now results/permission denials, and restart recovery. Confirm bound runs follow current thread settings and MCP-created fresh runs use remote `main` plus stored task settings. For webhooks, signature rejection, private secret entry, filters, replay/coalescing and missing keys, stale deliveries, and caller/Connect URL eligibility. |
| PR workflow | Link all stack layers, receive checks/comment/conflict wakeups without polling, unwatch on handoff, and read review-guide results/staleness. Do not merge as a side effect of monitoring. |
| Client and gateway | Update/reboot readiness, direct/private and Connect access, gateway TLS, preview attachment and recording transfer, and optional offline webhook storage only when deliberately selected. |
| Visual and outside-agent workflows | T3 Chrome cold-start/host diagnostics, narrow/light/dark HTML preview and local image embedding, readable text, private file secret delivery/expiry, deliberate OAuth grant/revocation, and opt-in device alerts. Record any browser validation gap separately from successful HTML publication. |

Retain the overall status in [release qualification](../release-qualification.md).
Native mobile/device SDK provisioning remains a [deferred plan](../plans/mobile-agent-support.md).

## Service and update model

Basaltwater uses T3 Code's supported per-user background service. Upstream owns
the launcher, immutable version directories, service state, updates, and
rollback. Basaltwater adds a systemd drop-in for the configured workspace,
host, port, PATH, and GitHub CLI environment. The drop-in runs T3's current
upstream `ExecStart` through a small output filter, resolving that command from
the upstream unit on each start so launcher updates do not require a pinned
Basaltwater command. The filter decodes systemd's literal `%%` and `$$` markers
before executing that command, preserving percent signs and dollar signs in
paths and arguments. It removes terminal color codes and hyperlink metadata,
then redacts credential fields, complete authorization/cookie values, and
terminal QR rows from both output streams. It forwards the remaining
diagnostics to T3's normal service log. A pre-start step also sanitizes an
existing `boot-service.log` and its numbered rotations after the previous
service process has stopped.

The service unit is:

```text
~/.config/systemd/user/t3code.service
```

Its Basaltwater settings are:

```text
~/.config/systemd/user/t3code.service.d/basaltwater.conf
```

User lingering is enabled so the service starts at boot without an interactive
login. Inspect it as the target user:

```bash
systemctl --user status t3code.service
journalctl --user -u t3code.service -n 100 --no-pager
tail -n 100 ~/.t3/userdata/logs/boot-service.log
```

The upstream launcher writes application startup failures, including native
module load errors, to `~/.t3/userdata/logs/boot-service.log`. systemd's journal
primarily records the launcher lifecycle. The managed filter removes startup
tokens, pairing URLs, and their QR image before they reach persistent logs.
One-time pairing commands intentionally return a fresh URL to the authenticated
caller; do not copy that command output into shared logs or diagnostics.

Stable `0.1.0` splits administrative access into granular permissions and
reserves `review:write` for internal use. Basaltwater's pairing helper validates
the temporary CLI session's complete administrative grant and delegates the
matching permission set, while retaining the older grant for pre-V2 servers.
Incomplete grants are rejected and their temporary session is revoked. This
preserves provider management, settings, previews, filesystem access, and server
updates for newly paired clients. If an existing client reports legacy
permissions after upgrading, enroll it again through the protected pairing
entrypoint. See the
[upstream permission contract](https://github.com/pingdotgg/t3code/blob/v0.1.0/packages/contracts/src/auth.ts).

The helper sends its temporary administrative bearer only to an explicit
loopback address or `localhost`, with proxies and redirects disabled. Remote
and wildcard server addresses are rejected before a session is issued. The
client-facing base URL remains independently configurable, including IPv6;
it does not determine where the helper sends that bearer.

T3 also records non-fatal provider diagnostics. It can health-check optional
agent CLIs even when setup deliberately omitted them, so a missing-Claude or
similar warning is not a failed configured capability; compare it with the
doctor inventory's `required` fields. T3 polls pull-request status for open Git
repositories as well. An intentional local-only repository with no remote can
therefore log `SourceControlProviderError` with `provider: unknown` while local
Git remains healthy. Do not add a remote or rename a branch solely to silence
that PR-only warning. Repair the remote only when the repository is actually
intended to use a supported hosting provider.

T3's trace and provider-event logs are high-volume. Managed user-cache
maintenance preserves current files while pruning numbered rotations older
than 14 days or beyond a combined 256 MiB. The host doctor inventories the
whole T3 log tree and warns beyond 512 MiB. Use that warning and the maintenance
result to identify pressure; do not manually remove current files from a running
service. A preview wait that intentionally times out can also appear as an
application error in the log and is not, by itself, a readiness failure.

Service readiness does not exercise the connected desktop client's preview
presentation. With tools that explicitly support desktop automation, one
recognizable stale state returns successful background navigation or DOM results
while `preview_open` produces no visible sidebar or
floating surface, status remains `visible: false`, and snapshots fail. Do not
open replacement tabs or diagnose the application and network from that state.
Restarting only the desktop client may retain the VM-side tab registry. If the
user accepts interruption of all active T3 sessions, the bounded recovery is:

```bash
systemctl --user restart t3code.service
```

Reconnect the client and retry one status/open cycle. Prefer managed Playwright
when session policy permits fallback, or continue non-browser checks. Restart the service
before considering a whole-VM reboot so VM-side T3 state is isolated.
Setup queues a final service reload so refreshed provider configuration and
skills take effect, including when runtime and unit versions are unchanged.
It first waits for any upstream update's startup/migration to become ready;
the completion reload follows the remaining setup work.
Internal non-refresh service checks keep a healthy current service. Desktop and
standalone provider lifecycle behavior differs by profile; see the corresponding
setup guide.

Basaltwater targets stable T3 Code releases. When T3 Code is selected, every
setup run checks npm's `t3@latest` stable channel for a newer release.
A healthy service receives a final reload after successful setup, even when
runtime and managed configuration are unchanged; internal non-refresh service
checks reuse a healthy session. The T3 client can also offer an explicit
**Update server** action for this background service; prefer that action after
active agent work and terminal commands finish. Keep the client open while the
launcher downloads, installs, restarts, and reconnects. For a host-side update,
set `T3_RELEASE` to the exact stable version required by the stable client. Use
`latest` only when the client is also on the latest stable release; npm's
`latest` does not select a newer nightly or preview. Stable `0.1.0` introduces
orchestration V2 and requires a compatible client; a `0.0.45` client cannot
continue against that server. Read
[the migration procedure](#orchestration-v2-thread-migration) before the first
V2 upgrade and arrange the interruption and private recovery copy.

```bash
# As the target user, using T3 Code's documented updater:
T3_RELEASE=CLIENT_VERSION
T3_NPM_SHIM="$HOME/.local/share/basaltwater/t3-npm/bin"
env -u npm_config_dangerously_allow_all_scripts \
  -u NPM_CONFIG_DANGEROUSLY_ALLOW_ALL_SCRIPTS \
  -u npm_config_allow_scripts \
  -u NPM_CONFIG_ALLOW_SCRIPTS \
  PATH="$T3_NPM_SHIM:$PATH" \
  CC=gcc \
  CXX=g++ \
  npm_config_strict_allow_scripts=false \
  npm_config_foreground_scripts=true \
  npx --yes --package="t3@$T3_RELEASE" -c \
  'env -u npm_config_allow_scripts \
    -u NPM_CONFIG_ALLOW_SCRIPTS \
    -u npm_config_dangerously_allow_all_scripts \
    -u NPM_CONFIG_DANGEROUSLY_ALLOW_ALL_SCRIPTS \
    t3 service install'
basaltw agent doctor --capability t3code --fix --wait-for-t3-startup
```

The direct command and Basaltwater setup operate on the same upstream-managed
user service. To update through setup, rerun the saved command with its
existing options; `--refresh-packages` is only needed when APT packages should
also be refreshed.

During an automatic setup update, an upstream updater failure does not take
down a previously working installation. If the managed service file and active
runtime remain valid, Basaltwater health-checks that runtime, reports that it
was retained instead of updated, and continues setup. A first install,
a damaged runtime, or a failed readiness check remains fatal. Updater failures
include bounded diagnostics from both the beginning and end of npm's output so
an earlier npm error is not hidden by a later successful native-build message.
Even an updater that reports failure may already have started a new service
invocation; setup preserves that migration while validating the retained runtime.
Pending managed configuration changes survive interrupted reconciliation and
are applied on a later setup, without restarting an invocation that already
loaded them.
Setup records this checkpoint in the private
`~/.config/systemd/user/.basaltwater-t3-reconcile.json` file and removes it after
verified application. Its adjacent `.lock` file remains to serialize setup;
rerunning setup completes pending reconciliation automatically.

For older npm-backed runtimes, keep the npm settings scoped to the trusted T3
updater. npm 12 blocks native dependency scripts by default, but inherited `allow-scripts` or
`dangerously-allow-all-scripts` settings cannot be used by T3's nested
project-scoped install: npm rejects that combination with `EALLOWSCRIPTS`.
This also applies when npm reads `allow-scripts` from a user or global
`.npmrc`: the outer `npx` re-exports the setting before T3 starts. Basaltwater
removes only those policy variables inside the `npx` command boundary and
places a managed npm passthrough first in the T3 service PATH. The passthrough
recognizes only an exact versioned `t3` install targeting T3's immutable
`.staging-*` directory. For that call it creates a short-lived, project-scoped
npm policy allowing only `node-pty` and `msgpackr-extract`, then removes the
policy before T3 publishes the runtime. All other npm commands pass through
unchanged, and the target user's normal npm configuration remains unchanged.

If the client reports that its update needs a newer T3 Code service launcher,
use the matching release's reconciliation command above. For a stable client,
rerunning the same Basaltwater setup on that VM also updates the launcher and
runtime; the doctor then validates the selected runtime. If npm 12 already
produced an incomplete candidate and T3 rolled back, setup identifies the retained
`failed` or `rolled-back` npm-backed candidate from reviewed service state
and rebuilds its two trusted native dependencies without stopping the active
working version. Then retry **Update server** in the client. A refresh setup
performs the same repair before invoking the upstream updater.
If that retained candidate cannot be probed or repaired, including a command
timeout, setup warns and continues with the upstream updater and active-runtime
checks. An obsolete failed candidate does not block installing the current
release or retaining a healthy service;
failure to validate the selected active runtime still stops setup.
Setup bounds each native probe to 30 seconds and each native rebuild to
10 minutes.

Older npm-backed T3 runtimes need a local `node-pty` build, so Basaltwater also
selects the `gcc` and `g++` provided by `build-essential` for setup-time and
service-initiated updates. This prevents a stale inherited `CC` or `CXX` value
from selecting a missing versioned compiler. Basaltwater validates the native
module, rebuilds an incomplete npm-backed active runtime only after successfully
stopping the service, and waits for several consecutive healthy service and
HTTP 200 checks from the same systemd service invocation before setup succeeds.
A restart resets those checks. Readiness
allows up to 30 minutes, matching the upstream launcher's preparation window:
copying an existing database, migrating it, and optimizing SQLite can take
minutes. Setup prints progress every 30 seconds and fails promptly if systemd
reports a failed service. It does not restart a newly updated runtime a second
time while that runtime may be migrating. This also covers an updater that
restarts a repaired launcher without changing the version. Managed configuration
changes still restart the service when the updater leaves it running unchanged.
A readiness timeout alone does not prove a crash; inspect the boot log before
retrying or interrupting migration. The same active-runtime repair is available
after setup:

```bash
basaltw agent doctor --capability t3code --fix
```

Doctor results establish service readiness only; they do not establish
connected-client presentation, provider sign-in, or remote browser reachability.
Use `--wait-for-t3-startup` after an update to allow the same 30-minute startup
window; it checks for stable readiness without restarting an already active
migration. The web panel uses this mode after updating T3 and budgets time for
both the updater and startup. Ordinary doctor checks remain quick. A failed or
timed-out stop skips native repair and leaves readiness unhealthy with the stop
diagnostic.
After updates and reboots, record readiness as the target account with
`basaltw agent doctor --capability t3code --capability host --record`. No
automatic post-boot readiness audit runs.

T3's service-state protocol identifies the launcher and runtime contract, and
upstream has changed that contract as its executable layout evolved. Basaltwater
accepts protocol 2 and later for active runtime selection only when
`activeVersion` is valid SemVer and the installed executable matches a known
layout. This lets a protocol-only launcher update work without assuming that an
unknown runtime layout is executable. Setup, the doctor, and the stable `t3`
wrapper share the version and layout checks. Retained failed-update repair and
cache cleanup can alter or delete runtime data, so they remain limited to
explicitly reviewed protocols 2 and 3 and fail closed for newer protocols.
When upstream changes the active state fields or executable layout, update the
shared state/version/layout definitions and compatibility tests, then review
whether the new protocol is safe for repair and cleanup. Both the standalone
executable layout (`versions/<version>/t3`) and the older npm layout remain
supported. The published runtime archive includes its executable and native
packages. Basaltwater checks its `node-pty` with the executable's embedded Node
using a short-lived preload that exits before T3 opens application state. Host Node.js is not needed
for this check. npm-backed runtimes use the Node configured in the service PATH
and retain the `node-pty`/`msgpackr-extract` repair allowlist. Doctor places that
Node's directory first in the repair PATH so npm and its build scripts use the
same runtime as the service. Host npm must not rebuild a standalone archive
against a different Node runtime. A failed archive probe is reported without
stopping an active service; restore the damaged
version from its matching upstream release archive and rerun setup. Basaltwater
applies the supported 50 MiB request-body limit to T3's managed HTTPS route while leaving
the pairing route at its deliberately small limit. See the upstream
[background-service documentation](https://github.com/pingdotgg/t3code/blob/main/docs/user/background-service.md),
[update documentation](https://github.com/pingdotgg/t3code/blob/main/docs/user/updating.md),
and [release process](https://github.com/pingdotgg/t3code/blob/main/docs/operations/release.md).

Basaltwater currently targets stable T3 Code releases. Existing installations
use the normal upstream update path; application data stays under
`~/.t3/userdata`, and Basaltwater does not edit T3's database or schema versions.

### Orchestration V2 thread migration

Stable T3 `0.1.0` changes the client/server orchestration protocol from 1 to 2.
Update the desktop/mobile client as well as the server; an old client can
disconnect even while the server is healthy. This is separate from the
service-state protocol, which remains 3 and is already supported. The matching
desktop release is `0.1.0`; the mobile release is `2.0`. If the mobile update
has not reached your app store, use the server's web app in a mobile browser.
See the [release's client compatibility notes](https://github.com/pingdotgg/t3code/releases/tag/v0.1.0).

Before the first upgrade, stop the service after active work finishes and copy
the complete private `~/.t3/userdata` directory, including database sidecars and
attachments. The first V2 launch copies `state.sqlite` to `statev2.sqlite` and
migrates the copy. Leave both databases intact. Existing threads import
automatically; continuing one starts a fresh provider session with a budgeted
history handoff. The V2 desktop app also uses a separate browser profile, so
websites may need a fresh sign-in.

A healthy Basaltwater doctor result does not prove client protocol compatibility
or complete conversation migration. Follow the
[stable migration procedure](../../common/agent_skills/basaltwater-t3code/references/thread-migration.md)
and verify representative threads in the updated client. Do not delete state,
reset pairing, edit migration bookkeeping, or downgrade to resolve a mismatch.
The [upstream migration contract](https://github.com/pingdotgg/t3code/blob/v0.1.0/docs/user/thread-migration.md)
describes the retained history and its limitations.

Older basaltwater installations used a root-owned
`basaltwater-t3code.service` and a separate npm runtime. A subsequent setup
stops that service, starts and validates the upstream user service, and only
then disables and removes the old unit and clears its retained failed state.
A failed legacy stop aborts before updating or activating the replacement.
The old service is restarted if a later migration step fails.

Retiring the old AppImage installer did not delete an AppImage that an
older setup placed in a user's home. After confirming the files were not
replaced with user-managed content, that retired installation can be removed
manually:

```bash
rm -- "$HOME/.local/share/t3code/t3code.AppImage"
rm -- "$HOME/.local/bin/t3code"
rm -- "$HOME/.local/share/applications/t3code.desktop"
rmdir --ignore-fail-on-non-empty "$HOME/.local/share/t3code"
```

## Network behavior

The safe default is loopback:

```bash
--web-interface t3code
--web-interface-host 127.0.0.1
--web-interface-port 3773
```

Basaltwater publishes a managed HTTPS endpoint through its shared gateway. The
plain HTTP listener remains for local compatibility. For a non-loopback bind,
declare private source networks:

```bash
--web-interface-host 0.0.0.0
--web-interface-source 192.168.1.0/24
```

A non-loopback bind is rejected unless UFW is active and a private or
non-global allowlist is present. Basaltwater reconciles only its own labeled
UFW rules and refuses conflicting unmanaged rules on the managed ports.
T3 supports loopback literals, `localhost`, and the wildcard binds `0.0.0.0`
and `::`. Specific non-loopback addresses are rejected because readiness and
administrative pairing require a local loopback connection. Use a wildcard
with the source allowlist for LAN access. Local checks try both loopback
families for `localhost`; IPv6-only listeners use IPv6 loopback for readiness
and their managed HTTPS forwards, including the pairing route.

The protected device-pairing broker uses port 3774 by default. Its Basic Auth
credential is staged for setup and is not written to the saved setup command.
Prefer the HTTPS endpoints printed during setup.
Opening the protected HTTPS endpoint completes browser pairing and opens T3
automatically. Its `/devices` page retains manual enrollment and T3 Connect
controls. Use the primary T3 HTTPS URL after pairing; if a saved browser session
stops working, reopen the protected entry to obtain a fresh native session.
See [device pairing](../services/device-pairing.md) for origin and cookie diagnostics.

### Browser authentication diagnostics

An unpaired browser can report WebSocket authentication failures while showing
the native pairing form. A bare URL has not enrolled that browser; complete
the normal pairing flow before judging authenticated WebSocket access.

On T3 v0.0.45, cloud-account sign-in at a private HTTPS origin can return
HTTP 400 from `clerk.t3.codes` with a production-key domain error requiring
`t3.codes`. T3 loads its browser cloud-account provider from its bundled public
configuration; see the upstream [app entrypoint](https://github.com/pingdotgg/t3code/blob/v0.0.45/apps/web/src/main.tsx)
and [browser auth shell](https://github.com/pingdotgg/t3code/blob/v0.0.45/apps/web/src/components/clerk/BrowserManagedAuthShell.tsx).
This browser cloud-account flow is separate from native pairing and gateway
trust. Keep cloud-account sign-in unverified on that origin; a Clerk domain
rejection does not establish a TLS or native pairing failure. Preserve the
managed authentication policy and upstream assets.

## Git and provider behavior

### Current provider and thread workflows

Managed Debian headless setup exposes `~/.local/bin/t3`, linked to the stable
wrapper that follows the service's selected runtime after updates. Setup
preserves an existing command at that path; the managed wrapper is also
available as `~/.local/bin/basaltwater-t3code-pairing-provider`. Use
`t3 --version` to check which runtime the shell invokes.

Stable 0.1.2 adds `t3 thread` commands for scripts on the environment host.
`t3 thread list --json` and `t3 thread show THREAD_ID` inspect existing work;
`start` creates an ordinary thread and `send` queues a message without
interrupting a running turn. `--wait` exits 2 when an answer is needed in the
app. Use native `delegate_task` for child work inside a T3 session and keep
top-level thread creation within the user's requested scope. See the
[upstream host CLI guide](https://github.com/pingdotgg/t3code/blob/v0.1.2/docs/user/outside-agents.md#scripts-on-the-host).

Managed setup refreshes reload selected provider configuration and skills. On
CachyOS desktop, a detached user worker gracefully sends SIGTERM to a running
same-user `t3code-bin` app, waits for exit, then relaunches the packaged app
with its original environment and arguments. It does not force-kill; if the app
was stopped, it starts when a graphical session is available; otherwise KDE
login autostart remains available. Check
the worker's printed `journalctl --user` unit if it fails; queueing is not proof
of readiness. Verify an authenticated provider turn and terminal.

Saved T3 threads can continue on their next message. Automatic continuation of
an interrupted active turn depends on the existing **Continue threads after
server update** setting. Persistent queued turns still need user Resume, and
approvals may not survive. Standalone provider CLIs cannot be safely restarted
by setup: finish and exit them, then resume manually. For a single thread or
context-only override, use native **Restart agent session** or a launch override
without restarting the shared T3 service.

Use the **Update all** control at the bottom of T3's sidebar for a requested
bulk update across connected environments. It updates outdated providers,
servers, and the desktop app on their selected release tracks. Stable 0.1.5's
desktop **Settings → About → Update track** separately switches connected
installations between Stable and Nightly, with per-machine skips and database
compatibility checks. A requested track can remain recorded after an incomplete
switch; inspect the running version too. Preserve compatibility blocks rather
than forcing a downgrade or changing migration records. Basaltwater setup
continues to target stable. See the
[track-switch guide](https://github.com/pingdotgg/t3code/blob/v0.1.5/docs/user/updating.md#switch-between-stable-and-nightly).
For one provider on one VM, use that provider's
individual control in **Settings → Providers** or
`basaltw agent update HOST USER --tool TOOL`. After a T3 provider update, run
Basaltwater's doctor and record readiness; its updater records and rollback
backups are created only by its own update flow. See the upstream
[provider update guide](https://github.com/pingdotgg/t3code/blob/v0.1.0/docs/user/updating.md#update-providers)
and [release notes](https://github.com/pingdotgg/t3code/releases/tag/v0.1.0)
for the global update action.

**No project** threads use individual folders under `~/.t3/scratch` on the
managed Debian service. Deleting a thread keeps its files. The host doctor
includes their size as `agent_storage.size_bytes.t3_scratch`; automatic cache
maintenance preserves them. CachyOS web installations use the `scratch` folder
under their isolated T3 data directory. Stable 0.1.5 adds **Turn into project**
to a projectless thread's menu. Finish or stop all threads using that folder
first. Promotion registers the existing folder in place and moves its threads
and their scheduled tasks into the project; it does not move files or initialize
Git. Use **Initialize Git** before branch/worktree controls. A promoted folder
remains user project data even though it is still under `scratch`. T3 hides
projectless threads when its data directory is inside a Git checkout. See the
[projectless thread guide](https://github.com/pingdotgg/t3code/blob/v0.1.5/docs/user/thread-sidebar.md#start-without-a-project).

The managed T3 skill also uses native `link_pull_request` and
`list_thread_pull_requests` tools when available, so PRs created through `gh`
appear in T3's linked-PR panel, including all layers of a stack.

### Native mobile device workflows

T3 also offers a Device panel and `device_*` tools for native mobile testing.
Basaltwater does not provision their SDKs, emulators, or simulator hosts and
does not enable agent device access during setup. Existing `--device-pairing`
enrolls T3 clients; it does not grant simulator control. See the
[mobile support plan](../plans/mobile-agent-support.md) and the
[upstream device workflow](https://github.com/pingdotgg/t3code/blob/v0.1.4/docs/user/devices.md)
for ownership and prerequisites. The new Simlock pool integration is an opt-in
alpha; Basaltwater does not install its gateway/workers, create leases, or
provision agent tokens. Returning a lease wipes its device. Responsive browser
presets remain browser coverage rather than native-device verification.

### Server-side Git checks

T3 Code runs as the target user. Git identity, GitHub CLI credentials, provider
credentials, repositories, and the workspace therefore stay in that user's
home and configured workspace.

Useful checks:

```bash
basaltw agent doctor --capability t3code --capability host
gh auth status
git config --global --get user.name
git config --global --get user.email
git config --global --get init.defaultBranch
basaltw agent support-bundle
```

Basaltwater configures `main` as the default for newly initialized repositories
unless the target user already selected another global default. This matters
for T3's branch controls: an unborn repository has only a symbolic branch name
and no branch ref until its first commit. To correct an existing unborn
repository initialized as `master`, run `git branch -m main`. Using
`git branch main` invokes branch creation instead and fails because there is no
commit to reference. Additional branches can be created or selected normally
after the initial commit.

The doctor's `healthy` result covers T3 service readiness, not successful
provider requests. Missing Git author identity, GitHub authentication, or a
Git credential helper is reported separately as an integration warning; an
installed but unselected `gh` does not make T3 unhealthy. JSON retains these
observations in `checks` and identifies the mandatory service checks in
`required_checks`. The web panel still requires Git checks explicitly selected
by its setup manifest. Use `agent doctor --tool gh` to check GitHub credentials
directly. The doctor does not send a model prompt or establish that a previous
provider timeout was resolved by running diagnostics.

### Provider status timeouts

`Timed out while checking Codex app-server provider status.` means the T3
provider probe did not complete; it does not establish that credentials are
invalid. After setup finishes and the host is idle, refresh provider status in
**Settings → Providers** and retry in T3. A successful Codex CLI request does
not validate T3's separate app-server probe. If it continues to time out,
record T3 and Codex versions, collect the doctor result, and inspect a redacted
service-log excerpt. Host contention is one possible trigger, not a diagnosis.
Do not replace credentials, delete T3 state, or restart active sessions solely
because a provider status probe timed out.

### Doctor checks and repairs

The doctor validates the upstream service-state protocol and selected immutable
runtime, required native terminal module, active and boot-enabled user service,
HTTP 200 endpoint, pairing helper, Git identity, and managed agent skill. Its
local endpoint checks support IPv4 and IPv6 loopback binds; wildcard listeners
are checked through the corresponding loopback address. HTTP errors do not count
as successful startup checks. Add `--fix` to rebuild an incomplete npm-backed
native runtime, repair GitHub's credential helper, enable the service for future
boots, or restart an inactive user service. The separate
host capability reports memory, swap, filesystem and agent-storage headroom,
T3 cgroup usage, recurring maintenance state, and pending reboots. Capacity
warnings do not make an otherwise healthy service fail; critical disk pressure
and recorded maintenance failures do.

Swap occupancy alone is not a pressure warning: idle swapped pages can remain
while plenty of RAM is available. When swap is configured, the host doctor
samples Linux swap-in/out counters for one second and includes the page deltas
and sample duration in `memory.swap_activity`. It warns about observed swap
I/O, unavailable activity measurements when swap is in use, and available RAM
below 512 MiB (including zero). An idle sample is only a point-in-time result;
repeat the check under normal workload before deciding to increase VM memory.
The doctor does not clear swap or change kernel tuning.

## Related documentation

- [Device pairing](../services/device-pairing.md)
- [Agent browser automation](browser-automation.md)
- [Client CA trust](../services/client-ca-trust.md)
- [Managed agent workflow skills](agent-skills.md)
- [Agentic coding security](agent-security.md)
- [Command-line reference](../reference/command-line.md)
- [Workstation and agent profiles](../workstations/workstations.md)
