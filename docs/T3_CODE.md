# T3 Code server

This guide covers T3 Code's server-side web interface on managed Debian
profiles. The retired desktop AppImage installation is not managed.

For an existing CachyOS KDE workstation, use the limited local profile described
in the [CachyOS guide](CACHYOS.md). Choose the native `t3code-bin` package with
`--t3code-desktop` using the [desktop installer example](CACHYOS.md#install-with-t3-code-desktop),
or the [managed web service](CACHYOS.md#t3-code-web-service-host-locally-or-on-a-trusted-lan)
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

T3 Code sessions can expose collaborative preview tools through the client.
In those sessions, use preview first: status, then open if no capable tab is
attached. Fallback requires absent preview tools, an explicit user request for
another browser, or an explicit unsupported/unavailable response from open;
navigation and certificate failures alone do not permit switching browsers.
For independent SSH or terminal sessions, request VM-local Chromium explicitly:

```bash
basaltw setup agent_code_vm vm.example agent \
  --browser-automation playwright
```

The collaborative preview uses the connected client's routes and certificate
store. Use the [browser workflow](BROWSER_AUTOMATION.md#collaborative-preview-and-private-networks)
for tab attachment, verified input, recordings, and bounded recovery. If only
client-origin testing fails, continue with healthy VM-local Playwright when
session policy permits fallback, or
non-browser checks. For an explicit `ERR_CERT_AUTHORITY_INVALID`, optional
[client CA enrollment](CLIENT_CA_TRUST.md) can restore private-origin access;
a timeout or unreachable address needs network diagnosis. Never bypass TLS.

Before navigating to a remote VM's loopback development port, run
`basaltwater-web preview resolve --port PORT --json`. It returns a verified
owned gateway URL for `preview_navigate`, or an exact forward creation command
with actionable failure fields. See [Internal HTTPS previews](INTERNAL_WEB.md#forward-an-existing-loopback-service).
The browser guide also covers navigation/component attribution, uncertain
input acknowledgments, and [offline-cache freshness](BROWSER_AUTOMATION.md#offline-cache-freshness).

Agent-enabled T3 setups install T3-only preview guidance, or the combined
Playwright/T3 skill when both capabilities are selected, plus focused T3 Code
and HTTPS-gateway guidance. See
[Managed agent workflow skills](AGENT_SKILLS.md) for the installation matrix.
Selected Claude Code receives the catalog under `~/.claude/skills`; Codex and
OpenCode share `~/.agents/skills`. Bundled references ship with the entrypoint,
so host-side migration instructions remain available outside this repository.
Finish the current turn and restart the agent session after setup refreshes
its skills.

When Codex is selected, root-owned Codex defaults make direct CLI sessions use
auto-reviewed workspace access. They do not restrict explicit user or client
choices, so T3 can retain its upstream full-access default and users can select
other Codex permission modes. `--harden-agent` adds system requirements that
enforce the workspace boundary without an approval escalation path. This is a
Codex policy and does not change another provider CLI's native permission model
or remove T3's collaborative preview. See
[Agentic coding security](AGENT_SECURITY.md).
The workspace skill uses the safe local lifecycle command:

```bash
basaltw agent workspace create ~/repos/PROJECT TASK --json
basaltw agent workspace remove WORKTREE --dry-run --json
```

The removal path rejects dirty, untracked, or unmerged work and cannot remove
the primary checkout. For a shareable diagnostic snapshot that omits log and
credential contents, use `basaltw agent support-bundle`.

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
presentation. One recognizable stale state returns successful background
navigation or DOM results while `preview_open` produces no visible sidebar or
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
Basaltwater does not automatically restart a healthy T3 service on a normal
setup rerun or maintenance schedule because that could terminate the agent
session performing the setup; refresh/update paths already restart when the
managed runtime or service configuration actually changes.

When T3 Code is selected, every Basaltwater setup run checks the upstream
service for a newer release. A healthy service is restarted only when the
runtime changes or its managed configuration needs it, so routine reruns do
not interrupt an unchanged session. The T3 client can also offer an explicit
**Update server** action for this background service; prefer that action after
active agent work and terminal commands finish. Keep the client open while the
launcher downloads, installs, restarts, and reconnects. For a host-side update,
set `T3_RELEASE` to the exact version required by the connected client. Use
`latest` only when the client is also on the latest stable release; npm's
`latest` does not select a newer nightly or preview. Before the first
orchestration V2 update, follow [the thread migration guidance](#orchestration-v2-thread-migration):

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
basaltw agent doctor --capability t3code --fix
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

T3 v0.0.35 also invokes `loginctl enable-linger` without a username. That can
fail in the sessionless `runuser` environment used by remote setup even after
Basaltwater has enabled lingering as root. During the upstream update only,
Basaltwater places a short-lived `loginctl` compatibility shim first in PATH;
it confirms lingering is already enabled, otherwise adds the validated target
username to that exact no-argument request, and delegates every other
invocation unchanged. The shim is removed immediately after the updater exits.

Older npm-backed T3 runtimes need a local `node-pty` build, so Basaltwater also
selects the `gcc` and `g++` provided by `build-essential` for setup-time and
service-initiated updates. This prevents a stale inherited `CC` or `CXX` value
from selecting a missing versioned compiler. Basaltwater validates the native
module, rebuilds an incomplete npm-backed active runtime, and waits for several
consecutive healthy service and HTTP checks before setup succeeds. The same
active-runtime repair is available after setup:

```bash
basaltw agent doctor --capability t3code --fix
```

As of 2026-09-01, Basaltwater's service and collaborative-preview checks had
passed with T3 Code v0.0.37, including preview open, snapshot, semantic input,
scrolling, viewport and appearance emulation, and client-side recording.
Keyboard helpers dispatch after semantic pointer focus; programmatic typing can
set DOM focus before page input is pointer-activated, so retry a no-op key once
after clicking its intended target and verify the outcome.

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
and retain the `node-pty`/`msgpackr-extract` repair allowlist. Host npm must not
rebuild a standalone archive against a different Node runtime. A failed archive
probe is reported without stopping an active service; restore the damaged
version from its matching upstream release archive and rerun setup. Basaltwater
applies the supported 50 MiB request-body limit to T3's managed HTTPS route while leaving
the pairing route at its deliberately small limit. See the upstream
[background-service documentation](https://github.com/pingdotgg/t3code/blob/main/docs/user/background-service.md),
[update documentation](https://github.com/pingdotgg/t3code/blob/main/docs/user/updating.md),
and [release process](https://github.com/pingdotgg/t3code/blob/main/docs/operations/release.md).

Compatibility reviewed on 2026-10-02 against the stable
[v0.0.45 release](https://github.com/pingdotgg/t3code/releases/tag/v0.0.45).
The v0.0.44 → v0.0.45 comparison leaves the service-state protocol (3), runtime
layout, pairing scopes, and database migration list unchanged. Existing v0.0.44
installations need the normal upstream update, with no Basaltwater data move or
pairing reset. Earlier npm-backed installations migrate through the current
`t3 service install` command on a saved setup rerun; application data remains
under `~/.t3/userdata`. Basaltwater does not edit T3's database or schema versions.

### Orchestration V2 thread migration

Compatibility reviewed again on 2026-10-03 against
[v0.0.46-nightly.20261003.2623](https://github.com/pingdotgg/t3code/releases/tag/v0.0.46-nightly.20261003.2623)
(`fed41fa88bb27cb4325cb208d571393850bc63c2`). Stable GitHub and npm releases
still report v0.0.45. The nightly keeps service-state protocol 3, the standalone
runtime layout, and all eight administrative pairing scopes. Existing
Basaltwater runtime selection, embedded-Node native checks, and pairing work
without a new launcher shim or pairing reset. Stable setup reruns retain their
stable release policy; use the matching exact release for a deliberate nightly
forward upgrade. Downgrading is not a supported workflow.

The application wire protocol changes from 1 to 2. T3 blocks mismatched clients
and servers and names the side to update; update that side and reconnect.
Service-state protocol 3 is a separate launcher contract. A healthy Basaltwater
doctor result does not establish client wire compatibility.

Finish active turns and terminal commands, check host disk headroom, and
schedule the server interruption. Before the first V2 update, stop the managed
service and copy the complete `~/.t3/userdata` directory, including SQLite
sidecars and attachments, into private storage outside T3's home. Preserve
restricted permissions because it includes conversations and authentication
state. Custom homes use `<home>/userdata`. Reserve space for both that recovery
copy and the V2 database copy plus migration growth. Do not run any server
against the recovery copy or attach it to support reports.

On first launch, T3 snapshots `state.sqlite` into `statev2.sqlite` and applies
its migrations to the copy, through migration 56 in this reviewed build.
Threads appear automatically; full transcripts import as needed or in the
background. Later launches use the existing V2 database. T3 also reconciles
the migration IDs used by earlier V2 previews. Leave both databases and
sidecars intact; Basaltwater does not import, rewrite migration IDs, or clean
either database. Settings, attachments, and workspace files remain shared.

Thread metadata, user/assistant messages, and supported attachments carry
over. Old provider sessions, run records, checkpoints/diffs, tool activity,
approvals, and proposed plans are not recreated. The first new message starts
a fresh provider session using a budgeted conversation handoff. Read recent
history and repeat important older requirements before continuing; agents can
retrieve omitted saved text through T3's thread-reading tools when available.

After the update, record `basaltw agent doctor --capability t3code --capability
host --record`, then verify representative older threads in the matching
client. Missing history needs investigation with the private recovery copy
opened read-only, rather than database deletion or a pairing reset. See the
shipped [agent migration reference](../common/agent_skills/basaltwater-t3code/references/thread-migration.md)
and upstream [migration](https://github.com/pingdotgg/t3code/blob/v0.0.46-nightly.20261003.2623/docs/user/thread-migration.md)
and [handoff](https://github.com/pingdotgg/t3code/blob/v0.0.46-nightly.20261003.2623/docs/user/portable-handoffs.md)
guides.

The published Linux x64 archive's SHA-256 was verified before isolated
native-addon, fresh-server, and administrative-pairing checks. A temporary
v0.0.45 database containing a pinned conversation passed the forward import,
migration-56 and transcript checks; the original database remained unchanged,
and a V2 restart retained history and pairing readiness. These checks do not
exercise a deployed systemd launcher or a connected desktop client.

Older basaltwater installations used a root-owned
`basaltwater-t3code.service` and a separate npm runtime. A subsequent setup
stops that service, starts and validates the upstream user service, and only
then disables and removes the old unit and clears its retained failed state.
The old service is restarted if the migration fails.

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

The protected device-pairing broker uses port 3774 by default. Its Basic Auth
credential is staged for setup and is not written to the saved setup command.
Prefer the HTTPS endpoints printed during setup.
Opening the protected HTTPS endpoint completes browser pairing and opens T3
automatically. Its `/devices` page retains manual enrollment and T3 Connect
controls. Use the primary T3 HTTPS URL after pairing; if a saved browser session
stops working, reopen the protected entry to obtain a fresh native session.
See [device pairing](DEVICE_PAIRING.md) for origin and cookie diagnostics.

## Git and provider behavior

### Features available in v0.0.45

After installing or updating Basaltwater's managed skills, use **Restart agent
session** from T3's command palette (`Ctrl+K` on Linux/Windows, `Cmd+K` on
macOS). The next message resumes the conversation in a fresh provider process
that reloads skills, plugins, and MCP servers. Finish the current turn first.
This provides a per-thread way to load configuration changes while keeping
the background T3 service running.

**Settings → Providers → Update all** updates supported outdated providers on
every connected environment. Hover the control to inspect its targets. Use an
individual provider control or `basaltw agent update HOST USER --tool TOOL` for
a narrower update. Manual-only methods are excluded from T3's bulk action.
After a T3 provider update, run Basaltwater's doctor and record readiness;
Basaltwater's updater records and rollback backups are only created by its own
update flow. See the upstream
[provider update guide](https://github.com/pingdotgg/t3code/blob/v0.0.45/docs/user/updating.md#update-providers).

**No project** threads use individual folders under `~/.t3/scratch` on the
managed Debian service. Deleting a thread keeps its files. The host doctor
includes their size as `agent_storage.size_bytes.t3_scratch`; automatic cache
maintenance preserves them. CachyOS web installations use the `scratch` folder
under their isolated T3 data directory. For repository changes, choose a
project and an isolated worktree. T3 hides projectless threads when its data
directory is inside a Git checkout. See the upstream
[projectless thread guide](https://github.com/pingdotgg/t3code/blob/v0.0.45/docs/user/thread-sidebar.md#start-without-a-project).

The managed T3 skill also uses native `link_pull_request` and
`list_thread_pull_requests` tools when available, so PRs created through `gh`
appear in T3's linked-PR panel, including all layers of a stack.

### Features available in the reviewed V2 nightly

Portable handoffs transfer a bounded selection of saved conversation text
when providers change or migrated threads continue. Agents can retrieve
omitted saved history using T3's thread-reading tools when exposed; repeat
important older requirements before continuing a migrated conversation.

The [ACP Registry](https://github.com/pingdotgg/t3code/blob/v0.0.46-nightly.20261003.2623/docs/user/providers-acp.md)
adds agents through **Settings → Providers → Add provider** on the environment
hosting T3. T3 owns their installation, sign-in, and provider configuration.
Adding one does not select a Basaltwater terminal-agent capability, install its
managed skill catalog, or apply Codex's policy to that agent. Keep each agent's
native sandbox and account settings explicit. Basaltwater setup continues to
provision only the selected Codex, Claude Code, and OpenCode tools.

### Optional mobile device workflows

T3 also offers a Device panel and `device_*` tools for native mobile testing.
Basaltwater does not provision their SDKs, emulators, or simulator hosts and
does not enable agent device access during setup. Existing `--device-pairing`
enrolls T3 clients; it does not grant simulator control. Android implementation
is deferred, and iOS through a separate Mac is an unscheduled proposal. See
the [mobile support plan](plans/MOBILE_AGENT_SUPPORT.md) and the
[upstream device workflow](https://github.com/pingdotgg/t3code/blob/v0.0.45/docs/user/devices.md)
for ownership and prerequisites. Responsive browser presets remain browser
coverage rather than native-device verification.

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

### Codex provider status timeout

T3 v0.0.42 limits its Codex app-server status probe to 10 seconds. The message
`Timed out while checking Codex app-server provider status.` means that probe
did not complete; it does not establish that the credentials are invalid.
See the upstream [probe implementation](https://github.com/pingdotgg/t3code/blob/v0.0.42/apps/server/src/provider/Layers/CodexProvider.ts)
and [timeout constant](https://github.com/pingdotgg/t3code/blob/v0.0.42/apps/server/src/provider/providerSnapshot.ts).

An [upstream report](https://github.com/pingdotgg/t3code/issues/7230) describes
timeouts being cached as provider errors, including across restarts. After
setup finishes and the host is idle, open **Settings → Providers** in T3 and
refresh the Codex provider status. Then try a prompt in T3. Successful direct
CLI prompts alone do not validate T3's separate app-server probe.

Basaltwater's completion access summary includes this recovery hint when T3
and Codex are selected. Refresh remains a client action: the T3 v0.0.42 CLI
does not expose a provider-refresh command. Setup does not restart the service
or edit its provider cache to force a refresh.

If refreshing still times out, record the T3 server and Codex versions, collect
`basaltw agent doctor --capability t3code --capability host`, and inspect the
service log around the refresh time. Remove sensitive content before sharing
logs. Host contention is a possible trigger, not a diagnosis from this message
alone. Do not replace credentials, delete T3 state, or restart active sessions
solely because this status probe timed out. Basaltwater's service readiness
check does not clear T3's provider cache or fix this upstream timeout behavior.

### Doctor checks and repairs

The doctor validates the upstream service-state protocol and selected immutable
runtime, required native terminal module, active and boot-enabled user service,
endpoint, pairing helper, Git identity, and managed agent skill. Add `--fix` to
rebuild an incomplete npm-backed native runtime, repair GitHub's credential
helper, enable the service for future boots, or restart an inactive user service. The separate
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

- [Device pairing](DEVICE_PAIRING.md)
- [Agent browser automation](BROWSER_AUTOMATION.md)
- [Client CA trust](CLIENT_CA_TRUST.md)
- [Managed agent workflow skills](AGENT_SKILLS.md)
- [Agentic coding security](AGENT_SECURITY.md)
- [Command-line reference](COMMAND_LINE.md)
- [Workstation and agent profiles](WORKSTATIONS.md)
