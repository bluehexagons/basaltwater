# Agent browser automation

Basaltwater can provision a browser that Codex and OpenCode use directly from
their agent sessions. This is separate from the workstation browser selected
with `--browser`: agent automation uses a pinned Playwright MCP package and its
matching Chromium build, while `--browser` installs an interactive desktop or
terminal browser with a separate profile and interaction workflow.

## Provisioning

Select at least one compatible agent and request the provider explicitly:

```bash
basaltw setup workstation_dev 192.168.0.41 agent \
  --provision-on ts1 --name agent-1 \
  --image-storage ts1-storage \
  --memory 4G --balloon-min 1G --storage root ts1-storage 32G \
  --agent-tool opencode,gh,codex \
  --browser-automation playwright \
  --node --go --rdp --password "$RDP_PASSWORD" \
  --rdp-source 192.168.0.0/24 --rdp-source 10.0.0.0/8
```

The `--password` option passes its value in a command-line argument. A shell
variable keeps the literal value out of shell history, but process inspectors
may still see the expanded argument. Run setup from a trusted controller and
never put literal credentials in a saved or committed command.

`--browser-automation playwright` currently requires `--agent-tool codex`
or `--agent-tool opencode`. It registers the managed
`basaltwater-playwright` MCP server
only for the compatible tools selected by that setup. GitHub CLI, Claude Code,
and T3 Code do not receive a browser integration. The browser capability owns
its root-owned Node.js dependency, so `--node` is not required unless projects on
the VM also need the user-managed Node.js development environment.

The interactive setup flow offers the same choice after agent selection. A
saved setup command retains the provider choice, so a later full setup restores
the browser capability without retaining any browser or website credentials.
An agent setup that omits the option also refreshes launchers for a complete,
current managed browser installation; this keeps safety defaults current when
the controller updates the target source independently of optional capability
selection. An explicit `--no-browser-automation` suppresses that reconciliation.
An older installation reports that migration is required. Rerun the saved setup
with `--browser-automation playwright` to provision both runtimes and Node.
For an explicit `--steps` setup, include `install_browser_automation` after
installing the selected compatible agent; it is available as a registered
custom step as well.

Compatible agent setups install `basaltwater-playwright-testing` when this is
the only browser capability. A VM that also provisions T3 Code receives the
combined `basaltwater-browser-testing` skill instead. Setup removes a stale
Basaltwater-managed browser variant when that capability combination changes;
see [Managed agent workflow skills](agent-skills.md).

## Installed components and agent configuration

Setup installs two independently verified packages:

- Stable `@playwright/test` 1.64.1 under `/opt/basaltwater-playwright-stable`
  for engine doctors and Basaltwater's application suite (`--context managed`).
- `@playwright/mcp` 0.0.83 under `/opt/basaltwater-playwright` for MCP clients
  (`--context managed-mcp`). Its exact Playwright dependency is
  `1.64.0-alpha-1790635538000`, an explicit upstream prerelease exception.

Setup verifies package versions and registry integrity metadata, installs native
browser dependencies, and downloads each runtime's matching Chromium build into
the target user's Playwright cache. Browser garbage collection is disabled to
preserve the other runtime's payloads. npm lifecycle scripts are disabled during
the root-owned package installs, which use isolated npm configuration.
Both use Node 24.22.1 from `/opt/basaltwater-node/v24.22.1`, downloaded from the
official release with pinned SHA-256 checksums for Linux x86_64 and aarch64.
Setup installs the Debian runtime libraries before executing that interpreter.
Setup verifies root ownership, permissions and the interpreter version before
installing or reusing browser packages. The distro interpreter is left to the
distribution; writable user NVM installations never run these privileged tasks.
The setup marker is written only after the resolved pinned Chromium executable is
present and executable, so a stale marker cannot suppress a required browser
download.

The managed launchers are:

- `/usr/local/bin/basaltwater-playwright-mcp` for MCP clients;
- `/usr/local/bin/basaltwater-playwright-doctor` for a local smoke test.

Codex registration is performed through `codex mcp`; OpenCode's existing JSON
or JSONC configuration is merged atomically. JSONC comments and formatting are
normalized during the rewrite, while configuration values are preserved. If
`--agent-config active` copied a config,
the browser registration is applied afterward, so the explicit setup choice
owns the `basaltwater-playwright` entry while preserving unrelated settings and
MCP servers.
Malformed target configuration is rejected. Symlinked, hard-linked, or
non-regular target files are rejected before their contents are read or merged.
Unterminated block comments and comments splitting a JSON token are treated as
malformed; registration leaves that file unchanged instead of repairing it
into a potentially different configuration.

The MCP launcher resolves the Chromium executable from the same pinned
Playwright package used to provision the user browser cache and passes that
path explicitly to MCP. It does not rely on MCP's default Chrome channel. The
launcher always starts Chromium in headless, isolated mode and enables
Playwright's bounded vision capability. Each MCP
connection receives a temporary browser profile; cookies and login state do not
persist between sessions. This avoids profile locking between simultaneous
agents and prevents one task from silently inheriting another task's browser
session. Use the normal desktop browser over RDP when a durable, human-managed
browser profile is required.

The managed MCP configuration at `/opt/basaltwater-playwright/mcp-config.json`
and the direct smoke test disable Chromium's `NativeNotifications` feature.
Headless browser startup therefore avoids activating the display-dependent
XFCE notification daemon on the user's service bus. This does not disable
notifications in the interactive desktop browser or modify the desktop session.
Rerunning setup refreshes the configuration and launchers; existing MCP sessions
pick it up when they next restart.

Generated snapshots, console captures, screenshots, and other MCP evidence
default to the private
`~/.local/state/basaltwater/playwright-mcp` directory instead of the current
project. The launcher applies a 256 MiB output ceiling so older evidence is
evicted without dirtying or filling active Git worktrees. An agent may still
name an explicit output file when the evidence belongs in a requested
deliverable. Omit `filename` for routine evidence: Playwright resolves unnamed
artifacts below the private output directory and returns their paths, while an
explicit name deliberately resolves in the active workspace.

## What agents can do

Once provisioned, Codex or OpenCode can ask the `basaltwater-playwright` MCP server to open
pages, inspect rendered content, click controls, fill forms, and capture
screenshots. The vision tools include safe viewport-coordinate mouse input for
canvas applications that expose no internal DOM controls, avoiding unrestricted
code evaluation for ordinary canvas clicks. Browser traffic originates from
the VM and is subject to its DNS,
routing, firewall, proxy, and destination-site controls. The provisioning smoke
test does not require internet access: it launches Chromium, interacts with a
local in-memory page, verifies rendering, and closes the browser. The doctor
allows slow browser operations up to two minutes, with a three-minute hard
process limit, so a cold browser can complete while a small ballooned VM is
temporarily swapping. Exceeding that limit reports likely memory, swap, or
storage pressure instead of an ambiguous locator timeout.

Website authentication is deliberately not part of Basaltwater credential
copying. Supply site-specific credentials through the application or a scoped
secret workflow appropriate to the task. Never type literal passwords or
session tokens into setup commands, repository files, prompts intended for
logging, or agent configuration. Even when a setup command uses an environment
variable, its expanded value may be visible to local process inspectors; use a
trusted controller. Isolated mode discards any cookies created during the
session.

## Choosing Playwright or collaborative preview

Agents choose a ready, configured browser that covers the task, subject to the
active session's browser policy. Installed guidance does not imply that every
option below is available to that agent session.

| Option | Useful for | Origin and limits |
| --- | --- | --- |
| T3 collaborative preview | Live participation, shared evidence, and environment-local automation | Stable 0.1.3 automates server tabs; listed desktop tabs require separate desktop-capable tools |
| Managed Playwright | Repeatable DOM, console, network, fixture, canvas/WebGL, and VM loopback checks | VM; isolated profile and session-exposed MCP tools |
| Existing project Playwright tests | Project-specific browser assertions with its documented test command | Selected project runtime; existing dependencies and engine payload required |
| Configured desktop browser | Native dialogs, desktop integration, visual interaction, or available fallback | VM desktop; separate profile, scoped input, and active control permission |

T3 sessions may require collaborative preview first: call `preview_status`, then
`preview_open` when no automation-capable tab is attached. Under that policy,
switch when preview tools are absent, the user explicitly requests another
browser, or open returns an explicit unsupported/unavailable host error.
Inspect actionable errors and retry with
corrected arguments. If the active policy permits fallback after two failures
at the same step, retain the raw error and switch to a configured alternative.
A status without a tab can be repaired by opening once and must not be treated
as a disconnect.

### Automatic continuation after client disconnection

Once fallback is permitted, continue automatically with a ready alternative
within the authorized browser test. Do not wait for the user to reopen T3,
restart its service to restore automation, or request approval merely to select
another configured browser. The server browser can remain available after a
viewing client closes; check its tools before treating client disconnection as
browser unavailability.
Playwright and VM desktop browsers do not depend on an open T3 application.

Discover browser contexts with `basaltw agent manifest REPOSITORY --capability
browser --json`; use the full manifest to discover desktop applications.
For managed Playwright, run `basaltw agent doctor --capability browser --json`
and verify its tools are exposed to the session. Missing MCP tools do not make
other configured contexts unavailable: inspect existing project tests and
desktop readiness before declaring browser coverage blocked. Project tests
use their own dependencies and documented commands; do not load managed
packages directly or bypass the managed MCP launcher.

For a configured XRDP desktop browser, read the installed desktop skill, check
`basaltw desktop status` and `basaltw desktop doctor`, and use the managed
`desktop start`/`exec` commands within the test task. Use scoped window/PID
inspection and fresh captures for input, honor human pause, and preserve
unrelated applications. Native KDE uses its separate skill and portal grant.
No fallback implicitly authorizes installing a browser stack, changing access
policy, restarting an agent session, or copying cookies between profiles.

If T3 disconnects during interaction, make one bounded status/open availability
check when policy requires it, then select a ready alternative. Inspect
application state before retrying a toggle or submission with uncertain
acknowledgment; profiles and login state are separate. Keep testing in the
selected browser rather than polling T3 before every action. Recheck T3 when
the client returns or the remaining task requires client-origin evidence.

For asynchronous review, share selected screenshot artifacts and observed
inputs/results. State the browser, engine where known, network origin, and
checks actually exercised. A VM-origin pass does not verify a disconnected
client's routing, trust, or presentation; pixel-only desktop evidence does not
establish DOM/network assertions. If no ready alternative covers the remaining
work, report that specific gap and continue build, unit, and HTTP checks.

## File import, download, and reopen

Browser filesystem access follows the selected runtime. Managed Playwright can
upload fixture paths from the VM through `browser_file_upload` or
`browser_drop`; T3's server browser uses the server filesystem, while a VM path
is not automatically available to a desktop-hosted tab. When session policy
permits both surfaces, use the surface with access to the fixture for file round trips and the shared
preview for client-visible review. Keep source fixtures disposable and use the
application's normal import, edit, and save controls.

A successful Save click or an updated in-memory view does not prove that an
export reached disk. For download-based editors, observe the actual download,
verify it completed, and reopen that downloaded file through the import flow.
If download handling requires `browser_run_code_unsafe`, use a bounded,
agent-authored Playwright sequence: register `page.waitForEvent('download')`
before clicking Save, await the download, and inspect `download.failure()`
before retaining it with `download.saveAs()`. Keep routine files in the private
evidence directory and use explicit workspace paths only for task deliverables.
Use distinct filenames for different outcomes so a repeated suggested download
name does not overwrite the comparison evidence.

Refresh the snapshot after edits: accessible names can change, such as Save
becoming Save *. Verify the exported format with its proper parser, including
JSONC comments when applicable, compare unrelated data with the original, and
run the consuming project's validator or runtime check when it is in scope.
Report import/export/reopen coverage separately from visual review and native
filesystem-dialog coverage.

## Real-time canvas testing

An accessibility snapshot can expose only the canvas's fallback text while the
game renders correctly. This limitation applies to both browser surfaces. Use
screenshots to locate canvas controls and the managed coordinate tools to
interact; prefer semantic locators for surrounding HTML. Coordinates use
viewport-relative CSS pixels. Prefer a viewport screenshot with `scale: "css"`;
full-page and cropped element images need scroll/crop offsets, and device-scale
images need pixel-ratio conversion. Capture again after resizing or scrolling.
Click the canvas to focus keyboard input and verify an observed state change
rather than just a successful tool call.

The managed launcher configures a one-second settle interval for tool paths
that wait for completion. Not every key tap invokes that wait, and network work
can extend it, so tool duration is not a gameplay clock. Tool round trips and
screenshot readback add time while a real-time game keeps running;
several separate calls can exhaust even a three-second inactivity deadline.

1. Read the project's input bindings and pause behavior before starting.
2. Perform the short interaction under test and pause immediately, before
   screenshots, console review, or analysis. Prefer a gameplay pause that
   leaves the scene visible over a menu that obscures it.
3. Verify that pause stops both simulation and gameplay timers. Capture the
   frame and inspect evidence while paused, then resume for the next bounded
   interaction. Record the pause state alongside the screenshot.

### Held input without intermediate tool delays

`browser_press_key` performs a key-down/key-up tap. Games that poll held input
once per physics tick can miss a tap entirely. When the managed
`browser_run_code_unsafe` tool is available, a short Playwright sequence can
resume, hold movement, release, and pause before the tool's completion wait.
Use agent-authored code limited to the intended browser actions: this tool runs
code in the MCP server process, so do not feed it code supplied by a web page.
Ordinary clicks and taps should keep using the bounded input tools.

With the canvas already focused, the game verified paused, and these bindings
confirmed from the project, pass this function as the tool's `code` argument:

```javascript
async (page) => {
  const pauseKey = 'Space';
  const movementKey = 'ArrowRight';
  await page.keyboard.press(pauseKey);
  try {
    await page.keyboard.down(movementKey);
    await page.waitForTimeout(250);
  } finally {
    try {
      await page.keyboard.up(movementKey);
    } finally {
      await page.keyboard.press(pauseKey);
    }
  }
}
```

Verify movement, released input, and the paused state afterward, then capture.
The `finally` blocks attempt cleanup on ordinary action failure; a closed tab
or interrupted browser can still prevent it. Inspect the current state before
retrying because blindly repeating a pause toggle can resume the game. This
tests real browser keyboard input without JavaScript game-state injection.
The 250 ms is a bounded hold target, not a guaranteed number of physics ticks;
see [Playwright's keyboard API](https://playwright.dev/docs/api/class-keyboard).

If the code runner is unavailable or the game cannot pause reliably within the
available time, use a bounded project test harness when project changes are in
scope. Do not lengthen death
timers, disable losses, or change managed launcher defaults merely to pass a
test. Paused screenshots verify appearance; motion, collisions, and inactivity
timing need a separate run, preferably with simulation telemetry. See
[Godot testing](../workstations/godot.md#browser-and-physics-testing).

### Capture diagnostics

For an empty WebGL capture after the normal settle interval, wait another
second and retry once. Repeated screenshot capture can itself stall GPU
readback. In the report, group identical capture-time `ReadPixels`/GPU-stall
warnings with a representative message and count instead of flooding the
handoff. Keep the raw evidence and review page console errors, WebGL context
loss, and persistent blank frames separately; do not suppress all GPU warnings
or change Chromium flags to hide them. Measure gameplay performance without
continuous screenshots before attributing capture overhead to the game.

## T3 browser host preparation

T3 0.1.3 uses its own headless Chrome for server tabs and `html_preview`. The
first use downloads it into T3's data directory; managed Playwright uses a
separate payload/cache. Follow the tool's installation/retry message before
declaring the host unavailable. A healthy T3 HTTP endpoint or Playwright doctor
does not establish that this Chrome can launch or render text.

For missing libraries, fonts, or AppArmor sandbox diagnostics, run
`t3 browser setup` as the service account without sudo to inspect what is needed.
Use the server's `T3CODE_HOME` or `--base-dir` for a nondefault data directory,
including CachyOS web's isolated home.
Before Chrome is downloaded it cannot inspect that executable's libraries, so
repeat inspection if the first launch reports missing dependencies. The elevated
command can install Debian/Ubuntu packages/fonts and an AppArmor profile; use
the exact command printed by the active runtime through the platform's authorized
privileged workflow. CachyOS uses its native package workflow for missing
libraries/fonts. Keep the server under its existing nonroot account and Chrome's
sandbox enabled. Verify the failed operation after repair; a readable screenshot
is needed to catch missing fonts. See the [upstream setup procedure](https://github.com/pingdotgg/t3code/blob/v0.1.3/docs/user/remote-access.md#browser-host-setup).

T3's inline HTML pages are thread artifacts, distinct from product browser tests
or gateway publication. The project-development skill covers
[project actions and visual replies](../../common/agent_skills/basaltwater-project-development/references/t3-workflows.md).

## Collaborative preview and private networks

T3 Code 0.1.3's server tools automate server-hosted tabs. Requests use the environment
host's network and certificate trust; clients view the streamed tab. A
desktop-hosted tab instead uses that desktop's routes and trust. Managed
Playwright uses the agent VM. Identify the runtime from the active tool/host
metadata or a controlled loopback check before attributing a failure to the
client. A disconnected viewer does not establish that the server browser failed.

Before reusing a tab ID from status or `t3_preview_list`, check its `runtime`.
Only `runtime: "server"` entries can be passed to this environment's
`preview_*` tools. An entry with desktop or missing runtime belongs to the user's
local app: report that the server cannot access it, then call `preview_open`
without that ID to open a server tab. Do not retry the desktop ID or diagnose
networking, TLS, or stale presentation from that rejection. Desktop automation
instructions below apply only when the active tools explicitly support it.
See the [upstream tab-list contract](https://github.com/pingdotgg/t3code/blob/v0.1.3/apps/server/src/mcp/toolkits/previewControls/tools.ts).

For a development server in the current environment, prefer the native target
with the retained tab ID:

```json
{"tabId":"TAB_ID","target":{"kind":"environment-port","port":5173,"path":"/"}}
```

Pass exactly one of `url` or `target`. The target defaults to HTTP and accepts
`protocol: "https"` for TLS. The server browser resolves `environment-port`
directly to `localhost:PORT`, so VM loopback needs no gateway forward. See the
[server resolver and preferred-host registration](https://github.com/pingdotgg/t3code/blob/v0.1.3/apps/server/src/preview/ServerBrowser.ts).
Desktop-hosted tabs use the client resolver, which rewrites the environment's
connection host and may reject public relay targets; it does not tunnel to VM
loopback. See the
[desktop resolver](https://github.com/pingdotgg/t3code/blob/v0.1.3/apps/web/src/browser/browserTargetResolver.ts).

When a desktop browser needs a reachable URL for a VM loopback service, resolve
the existing HTTPS route:

```bash
basaltwater-web preview resolve --port 8080 --json
```

Open the returned `navigation.url` in the desktop browser, using
`preview_navigate` only when its tools support that runtime. If `issue` is
`forward_missing`, run the exact returned `command` when
client-visible preview is in scope, then resolve again. The read-only resolver
checks owned HTTPS forwards without changing bindings or access policy.
Failures identify the upstream or gateway, distinguish refused connections,
timeouts, HTTP errors and certificate failures, and include recovery advice.
Use `--host ::1` for IPv6 or `--profile godot` for cross-origin isolation.
See [preview resolution](../services/internal-web.md#forward-an-existing-loopback-service).
VM-side success does not verify the client's network or certificate trust.

An internal gateway URL can pass VM checks yet fail on a desktop with no LAN
route, an excluded source address, or missing CA trust. Attribute that boundary
only after establishing the browser host. Keep development servers on loopback
and preserve the saved access policy.

When the task selects collaborative preview, agents should call preview status
first and open a preview once when no automation-capable tab is attached. A tab
with `visible: false` has no active viewer; server-browser navigation, input,
and capture can still work. With tools that support desktop automation,
desktop-hosted capture can depend on the client window. Attempt one snapshot
after navigation. If capture fails or times out while the tab remains available
and invisible, do not repeatedly
retry or diagnose the application, WebGL, TLS, or network from that result.
Use the healthy VM-local fallback when session policy permits it and its network origin is
appropriate, or finish with non-browser checks and report the coverage gap.
When collaborative evidence is important, restoring T3 should make the
existing tab capturable; recheck status and retry that tab rather than opening
duplicates.

Preview availability and tab attachment are separate states. A status response
with `available: true` and `tabId: null` confirms an automation host but means
the current agent session has no selected tab. Open once, retain the returned
tab ID, and use it explicitly after an agent-session boundary. Do not describe
that state as a detached or unavailable preview.

Once a tab is attached, use a snapshot-first workflow. Server hosts list every
tab in the thread, including tabs opened by the user. After verifying a supported
runtime, read the requested tab by `tabId`; act on your own or an unclaimed tab
only while no human controls it.
Snapshot output provides visible text, accessibility data, semantic locators, console and
network diagnostics, a screenshot, and recent action history. Prefer role/name
locators from that output. Treat a successful type, press, click, or scroll
response as proof that T3 accepted the helper, not that the page received an
event or changed: verify the expected text, URL, or rendered state with a
bounded wait or follow-up snapshot. Programmatic typing can establish DOM focus
before the connected preview has pointer-activated page input. If a subsequent
keyboard helper produces no expected event or application change, semantically
click its intended target and retry the key once. If it still has no effect, use
the equivalent semantic click when the test allows it or record the keyboard
path as unverified. Do not inject mutations through page evaluation merely to
turn an unverified interaction green.

A failed input call can also leave a changed page. Before retrying a press,
click, or submission, inspect the expected state. If the change occurred,
report successful input with uncertain acknowledgment; retain the tool error
separately. If state cannot be inspected, report an unverified outcome.
Distinguish automation transport failure from a deadline timeout using the
actual error details; do not infer either from a generic failure alone.

Tag diagnostic evidence with tab ID, requested URL, navigation time or action
ID, and component: preview host (including Electron/preload), gateway/network,
or application. Snapshots can retain errors from a previous `about:blank`
navigation. Keep those historical host errors separate from current page
errors, and preserve uncertain attribution when no URL or timestamp proves
which navigation produced a message. Basaltwater provides this reporting
workflow; native tool error schemas and snapshot tagging are owned by T3.

Viewport presets change CSS layout dimensions while preserving T3's desktop
browser user agent; they are breakpoint checks, not full mobile-device
emulation. Re-snapshot after resizing or scrolling before using coordinates,
and account for a scroll position that persists across viewport changes. Light,
dark, and system appearance emulation changes `prefers-color-scheme`; restore
the system setting after a bounded comparison unless the user asked to leave an
override.

The current `preview_recording_stop` tool transfers a compressed collaborative
recording once, up to 50 MiB, into an evidence file in the agent environment.
After successful transfer, check that the returned local path is readable and
link it for review. Keep the recording short enough for the transfer limit;
do not claim local evidence when transfer fails. Older tools can return
client-only artifacts, so follow the attached tool's description and metadata
rather than assuming every returned path is local. Start recording only when
video adds useful evidence, stop it promptly, and avoid credentials or unrelated
user data. A snapshot's action timeline can show the snapshot currently being
constructed as `running`.
When the response itself has returned, that self-entry is expected; use prior
completed actions and the returned page state to assess success.

With tools that support desktop automation, a narrower failure is possible
when `preview_open` returns a usable background tab but the desktop application
never mounts a sidebar or floating preview: status remains `visible: false`,
lightweight DOM operations can succeed, and
snapshot capture fails or times out. This is stale T3 preview-presentation
state, not proof of an application, route, certificate, or VM browser failure.
Do not create more tabs. Restarting the desktop client may preserve the stale
tab registry in the VM-side service. Use managed Playwright when session policy
permits fallback. If collaborative coverage is required, warn that every active T3
session will be interrupted and, only with the user's approval, restart the
server as the target user:

```bash
systemctl --user restart t3code.service
```

Reconnect the desktop client and retry status/open once. Do not use a full VM
reboot as the first recovery step. Explicit setup refreshes restart a healthy
managed service unless the updater already started a fresh invocation; finish
active work before a setup refresh. Internal non-refresh checks reuse a healthy
service.

For WebAssembly and WebGL applications, successful browser navigation can
precede runtime initialization. A snapshot containing only a splash screen or
progress bar confirms document rendering but not application readiness. Wait
one bounded startup interval and capture the expected application frame once;
do not turn ordinary runtime startup into an unbounded polling loop.

For private-URL failures, verify the exact URL and a non-sensitive artifact
from the VM with normal TLS verification, then inspect the attached preview's
snapshot and network error. Use the observed failure to choose the next step:

| Observation | Next step |
| --- | --- |
| Listed tab has desktop or unspecified runtime | Report that server tools cannot access it and open a server tab without its ID; desktop automation needs explicit tool support. |
| No selected automation tab | Call open once and retain its tab ID; status alone does not prove host unavailability. |
| Open explicitly reports a disconnected/unavailable host, or preview tools are absent | Automatically select a ready configured alternative within session policy; report the missing coverage for that browser host. |
| Unavailable capture with an attached tab | Follow bounded capture diagnostics; use an alternative when session policy permits it. |
| Explicit `ERR_CERT_AUTHORITY_INVALID` | Inspect trust on the browser host. For a desktop-hosted tab, offer `basaltwater-web ca` and [Client CA trust](../services/client-ca-trust.md) when client access is wanted. A server-browser error needs host-side trust diagnosis. |
| Timeout, refused connection, or unreachable address | Check routing from the browser host and any gateway source policy; CA enrollment will not repair connectivity. |
| Desktop-hosted background DOM works but no visible surface or snapshot | Follow the bounded stale-preview recovery above. |

Before using provisioned Playwright as the fallback, run
`basaltw agent doctor --capability browser --json`. If the capability is
absent or unhealthy, inspect configured project tests or desktop readiness
before declaring browser coverage blocked. If no usable alternative remains,
continue non-browser checks and report the missing coverage. Do not install
another browser stack merely to fill that gap.

Report which network origin passed and which failed. A client-only failure
does not establish that the hosted site is down. Preserve TLS verification,
loopback bindings, and the intended access policy; client CA enrollment is
optional and does not block unrelated work.

## Offline-cache freshness

When an updated game appears unchanged, distinguish the copied publication
from the browser's offline cache. First compare a served artifact with the
build as described in [Internal HTTPS sites](../services/internal-web.md). Then pass this
read-only expression to `preview_evaluate` in the same tab:

```javascript
(async () => {
  if (!('serviceWorker' in navigator)) return {supported: false};
  const worker = value => value
    ? {scriptURL: value.scriptURL, state: value.state} : null;
  const registration = await navigator.serviceWorker.getRegistration();
  return {
    supported: true,
    pageURL: location.href,
    controller: worker(navigator.serviceWorker.controller),
    registration: registration ? {
      scope: registration.scope,
      active: worker(registration.active),
      installing: worker(registration.installing),
      waiting: worker(registration.waiting),
      updateWaiting: Boolean(registration.waiting)
    } : null
  };
})()
```

`controller` identifies the worker controlling this page; `getRegistration()`
selects the registration matching its URL rather than unrelated applications
on the shared gateway origin. A waiting worker is an installed update that
has not activated. These fields expose cache context, not the contents or age
of cached responses. See the [controller API](https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerContainer/controller)
and [waiting worker API](https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerRegistration/waiting).

Use the app's normal update flow or reload once, then capture the worker state
and expected changed content again. Avoid awaiting `serviceWorker.ready`, which
can remain pending when no worker activates. Do not unregister workers, delete
caches, or force `skipWaiting` merely to diagnose freshness. Report a pending
update or unresolved freshness check instead of treating it as a failed build.

## Verification

For the panel, pairing, approval, and disposable Guacamole application checks,
use the developer-machine `make browser-test` workflow in
[browser qualification](browser-testing.md). It records each engine separately
and keeps intensive tests out of ordinary CI. Native Debian Firefox ESR has a
separate release checklist there; neither runtime doctor establishes that
coverage. Projects retain their own [browser policy](project-tooling.md#project-browser-tests).

### Engine and context coverage

Use small, read-only discovery before selecting engine coverage:

```bash
basaltw agent manifest /home/agent/repos/example --capability browser --json
```

The `browser.contexts` entries distinguish stable managed headless Chromium and
optional Firefox, the separate managed MCP context, project-installed Playwright Firefox, interactive desktop
Firefox/Firefox ESR, and the connected client's collaborative preview.
Discovery reports runtime presence and leaves launch/rendering readiness
unverified; browser-cache directories do not prove that an engine works.
Inventory separates `discovered` (the runtime or desktop installation),
`launchable` (unknown until a launch check), `mcp_registered` (configuration,
not session tool exposure), and `application_tested`. Desktop discovery probes
native Firefox/Firefox ESR and `flatpak info org.mozilla.firefox`, including
Flatpak-only installs, and reports versions when available. A connected preview
has engine `unknown` until the session establishes it. Never infer Firefox
coverage from a viewport preset or an unknown preview.
The ordinary browser capability doctor below qualifies managed Chromium and
its MCP integration only. Its result includes the engine, context, and
network origin explicitly, also retained in redacted readiness and support
records. Older records without those fields leave the context unknown.

When Firefox coverage is requested, check the selected existing runtime:

```bash
basaltw agent browser doctor --engine firefox --context project \
  --project /home/agent/repos/example --json
```

If that runtime reports `browser_payload_missing`, explicitly install its
matching Firefox payload and repeat the check:

```bash
basaltw agent browser install --engine firefox --context project \
  --project /home/agent/repos/example --json
```

These commands use the existing package under that directory's `node_modules`
(`playwright`, `@playwright/test`, or `playwright-core`), including pnpm package
links, and the project's supported Node executable selected by `basaltw node`.
Use the owning package directory
for a monorepo. They do not run npm/npx, add packages, change a lockfile, upgrade
Playwright, or install OS dependencies. Project `PLAYWRIGHT_BROWSERS_PATH` and
custom CA settings are retained; Node preload/module overrides are omitted.
The managed MCP and smoke launchers omit those overrides too, while retaining
custom CA settings, so their Node environment agrees with the engine doctor.
Optional downloads disable browser garbage collection so other projects'
payloads remain available. Missing project dependencies require the project's
normal lockfile installation first.

On a Debian VM with managed Playwright, `--context managed` uses the pinned
stable package and its fixed user cache instead. `--context managed-mcp` checks
MCP's separate alpha package and matching payload. Optional Firefox provisioning
leaves the default MCP registration on Chromium. Project context also works on
CachyOS and Ubuntu WSL without assuming a Debian managed browser stack.
An unexpected managed package version reports `runtime_version_mismatch`
before installing or launching a payload; rerun setup to restore the pin.
The platform must already provide the selected engine's OS dependencies;
a failed download or smoke test returns a nonzero status with `issue` and
`remediation`. Use the host's authorized dependency setup when needed.
System Firefox is a separate desktop context: [Playwright uses patched Firefox](https://playwright.dev/docs/browsers#firefox),
so an installed desktop browser does not satisfy its payload requirements.

Each explicit engine doctor launches an isolated, in-memory page, checks a
button interaction and screenshot, and closes the browser. Successful results
record Playwright and browser versions and verify local rendering; application
behavior, desktop integration, and connected-client coverage still need their
own tests. Session browser policy continues to govern application automation;
these commands do not expose additional browser tools to an agent session.

### Opt-in managed Firefox tools

After updating the managed launchers through the saved setup, run as the setup
user (without sudo):

```bash
basaltw agent browser install --engine firefox --context managed-mcp --json
basaltw agent browser register --engine firefox --agent-tool codex --json
# Or select --agent-tool opencode; repeat to register both clients.
basaltw agent browser doctor --engine firefox --context managed-mcp --json
```

Installation downloads only the pinned Firefox payload. It never installs OS
dependencies; missing libraries require the host's explicit dependency setup.
Registration first verifies startup and rendering and checks the managed
launchers, then adds the separate `basaltwater-playwright-firefox` server. It
preserves `basaltwater-playwright` as Chromium and unrelated settings. Start a
new agent session to discover the Firefox tools; registration alone does not
expose tools to a running session.

Both MCP servers use the same pinned alpha Playwright package, headless isolated profiles,
explicit matching executables, certificate verification, and bounded private
evidence. Firefox evidence lives under `playwright-mcp/firefox`; its separate
configuration omits Chromium launch flags. Engine doctors report runtime
launch/render results and registrations separately and always leave
`application_tested` false. Use application tests to establish that coverage.
Registration always qualifies `managed-mcp`, even when the default context is
omitted. A stable engine doctor does not qualify an MCP browser payload or its
registration. An unavailable configuration parser leaves registration
unknown. If registration fails after a successful runtime check, the command
returns nonzero with `browser_registration_failed` and retains the engine,
version, and rendering results for diagnosis.
The general capability doctor reports `registration_unverified` with
`use_python_with_toml_support` when its Python lacks `tomllib`; run it with
Python 3.11 or newer to inspect Codex configuration.

### Managed Chromium integration

Run the browser check on the configured VM as the setup user:

```bash
basaltw agent doctor --capability browser --json
```

This checks the browser capability without requiring unrelated terminal tools.
Add `--tool codex` or `--tool opencode` when that client must also be healthy;
each explicit tool is required, even when absent.

The capability is healthy only when both managed
launchers are executable, root-owned regular files without group or world write
access; explicit managed-Chromium selection and current private, bounded
evidence, safe-coordinate, and one-second-settle defaults are present; every
installed compatible agent has the managed MCP registration; exactly one
Basaltwater-managed Playwright-capable browser workflow skill is installed as
a bounded, user-owned file without group or world write access;
active managed MCP processes use those same safe defaults; and the local
interaction/rendering smoke test passes. A stale launcher is unhealthy even
when its smoke test passes. Inspect an unsafe path, then rerun the saved setup
to reconcile it. A stale active process instead requires
restarting the agent session that owns it.

In an agent sandbox, Linux may show an unmapped host owner as the overflow UID
(normally `65534`/`nobody`). The doctor checks `/proc/self/uid_map` and the kernel's
configured overflow UID before classifying ownership. If root is not mapped and
ownership is ambiguous, it reports `launchers_unverified`,
`launchers_secure: null`, and `verify_launchers_from_host`. Unreadable security
metadata also leaves security unverified. A visible non-root owner, a linked or
non-regular launcher, or group/world write permission remains unsafe.

The doctor skips its launcher smoke test when launcher security is unsafe or
unverified; `smoke_test` and `launchable` remain `null`, without a
`smoke_test_failed` issue. Readiness still returns nonzero until verification
succeeds. Verify numeric ownership and modes from an authorized host context;
an unmapped owner alone is not grounds to change ownership, permissions, or
reinstall. Readiness records and support bundles preserve the unknown security
value and the verification recommendation. A smoke-test failure after verified
launcher security describes the current invocation; sandbox restrictions can
still prevent browser startup and do not establish a host installation defect.

JSON results include a stable `issues` list and one primary `remediation` code.
`mcp_browser_selection_missing` identifies a launcher that can pass the direct
Chromium smoke test while MCP still defaults to an unavailable Chrome channel;
`rerun_saved_setup` refreshes stale managed defaults;
`rerun_setup_with_browser_automation` restores missing launchers or agent
registration; `inspect_launcher_security_then_rerun_saved_setup` requires
reviewing an unsafe managed path before reconciliation;
`workflow_skill_missing_or_stale` identifies absent, incompatible, or multiple
managed browser workflow variants and is repaired by the saved setup;
`restart_agent_sessions` identifies MCP processes that were started before a
launcher or trust-store update and must be restarted rather than reconfigured;
and `inspect_browser_runtime` identifies an installation whose local Chromium
smoke test failed. These fields are also retained in redacted readiness records
and support bundles.

For lower-level checks:

```bash
/usr/local/bin/basaltwater-playwright-doctor
codex mcp list
```

Inspect `~/.config/opencode/opencode.json` or `opencode.jsonc` for OpenCode's
`mcp.basaltwater-playwright` entry. The managed OpenCode entry uses a 30-second tool
discovery timeout to accommodate cold VM startup. Do not print agent auth files
while troubleshooting.

## Security and operating limits

Browser automation is not a security boundary. A page can contain untrusted
content designed to influence an agent, and a browser-enabled agent can act
with the network access, repository access, and application credentials
available to its Unix user. Prefer scoped repository and website credentials,
restrict VM egress where appropriate, and require human review for sensitive
or irreversible actions.

The managed server uses local stdio rather than an exposed network listener.
Chromium retains its browser sandbox; VM targets are recommended. Browser
namespaces and sandboxes may not work in every container policy, so use a VM
when browser automation must be reproducible. Rerunning setup reconciles the
managed registration and package version. Version changes are delivered through
a Basaltwater update and should be reviewed like other executable dependency
updates.
