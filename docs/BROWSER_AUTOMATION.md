# Agent browser automation

Basaltwater can provision a browser that Codex and OpenCode use directly from
their agent sessions. This is separate from the workstation browser selected
with `--browser`: agent automation uses a pinned Playwright MCP package and its
matching Chromium build, while `--browser` installs an interactive desktop or
terminal browser for a person.

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

`--browser-automation playwright` currently requires `--agent-tool codex`
or `--agent-tool opencode`. It registers the managed
`basaltwater-playwright` MCP server
only for the compatible tools selected by that setup. GitHub CLI, Claude Code,
and T3 Code do not receive a browser integration. The browser capability owns
its system Node.js dependency, so `--node` is not required unless projects on
the VM also need the user-managed Node.js development environment.

The interactive setup flow offers the same choice after agent selection. A
saved setup command retains the provider choice, so a later full setup restores
the browser capability without retaining any browser or website credentials.
An agent setup that omits the option also refreshes launchers for a complete
existing managed browser installation; this keeps safety defaults current when
the controller updates the target source independently of optional capability
selection. An explicit `--no-browser-automation` suppresses that reconciliation.
For an explicit `--steps` setup, include `install_browser_automation` after
installing the selected compatible agent; it is available as a registered
custom step as well.

Compatible agent setups install `basaltwater-playwright-testing` when this is
the only browser capability. A VM that also provisions T3 Code receives the
combined `basaltwater-browser-testing` skill instead. Setup removes a stale
Basaltwater-managed browser variant when that capability combination changes;
see [Managed agent workflow skills](AGENT_SKILLS.md).

## Installed components and agent configuration

Setup installs the exact Playwright MCP version declared by Basaltwater under
`/opt/basaltwater-playwright`, verifies the npm package version and registry
integrity metadata, installs native browser dependencies, and downloads the
matching Chromium build into the target user's Playwright cache. npm lifecycle
scripts are disabled during the root-owned package install.
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
secret workflow appropriate to the task; do not put passwords or session tokens
in setup commands, repository files, prompts intended for logging, or agent
configuration. Isolated mode discards any cookies created during the session.

## Choosing Playwright or collaborative preview

Playwright is not limited to emergency fallback use. Prefer it for repeatable
headless interactions, DOM/console/network inspection, canvas input, VM
loopback access, and browser-engine checks that do not need live collaboration.
It remains available when the connected T3 application is closed.

Prefer T3 Code's preview when the user should watch or participate, evidence
must appear in the collaborative UI, or the task specifically tests the
connected client's network or certificate trust. When either surface is
equally suitable, an already attached preview is useful; otherwise VM-local
Playwright is the dependable default. Agents should state which network origin
produced evidence when that affects the conclusion.

If the T3 client is known to be closed, start with healthy Playwright directly.
No preview probe or service restart is needed. For asynchronous review, provide
selected screenshot artifacts and a short record of inputs and observed states.
Live visibility is a reason to choose T3; it is not a prerequisite for complete
VM-origin rendering, input, console, and network checks.

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
[Godot testing](GODOT.md#browser-and-physics-testing).

### Capture diagnostics

For an empty WebGL capture after the normal settle interval, wait another
second and retry once. Repeated screenshot capture can itself stall GPU
readback. In the report, group identical capture-time `ReadPixels`/GPU-stall
warnings with a representative message and count instead of flooding the
handoff. Keep the raw evidence and review page console errors, WebGL context
loss, and persistent blank frames separately; do not suppress all GPU warnings
or change Chromium flags to hide them. Measure gameplay performance without
continuous screenshots before attributing capture overhead to the game.

## Collaborative preview and private networks

T3 Code's collaborative preview and the managed Playwright fallback do not use
the same network origin. The collaborative browser runs in the connected
client's context, while managed Playwright runs on the agent VM. Consequently,
an internal `basaltwater-web` URL such as `https://192.168.x.x:8443/...` can pass
`curl` and `basaltwater-web doctor` on the VM yet fail in the collaborative preview
when the client lacks a route to that LAN, is outside the gateway's allowed
source ranges, or has not enrolled the local CA.

An `environment-port` preview target is a host rewrite, not a tunnel from the
connected client to VM loopback. T3 maps the requested port onto the host in
the environment connection. If that connection is represented by localhost,
the collaborative browser still requests its own localhost; if it is
represented by a private VM address, a server bound only to VM loopback is not
listening on that address. A healthy loopback Vite server can therefore remain
unreachable through both `environment-port` and a direct loopback URL. Treat a
failed navigation that leaves the tab at `about:blank` with no network request
as a client/VM routing boundary after verifying the VM endpoint. Do not rebind
the development server or widen firewall policy solely for automation; use
the managed VM-local fallback, or use an explicit `basaltwater-web` publication when
client access is part of the task.

Collaborative preview is an opportunistic test surface. During normal agent
work, T3 may be minimized, its preview pane may be closed, or no preview
automation host may be attached. These are expected coverage states, not
application-health failures and not reasons to pause unrelated implementation
or non-browser verification.

When the task selects collaborative preview, agents should call preview status
first and open a preview once when no automation-capable tab is attached. A tab
with `visible: false` may still
navigate and accept input, but a minimized T3 window can make snapshots or
recordings fail. Attempt one snapshot after navigation. If capture fails or
times out while the tab remains available and invisible, do not repeatedly
retry or diagnose the application, WebGL, TLS, or network from that result.
Use the healthy VM-local fallback when its different network origin is
appropriate, or finish with non-browser checks and report the coverage gap.
When collaborative evidence is important, restoring T3 should make the
existing tab capturable; recheck status and retry that tab rather than opening
duplicates.

Preview availability and tab attachment are separate states. A status response
with `available: true` and `tabId: null` confirms an automation host but means
the current agent session has no selected tab. Open once, retain the returned
tab ID, and use it explicitly after an agent-session boundary. Do not describe
that state as a detached or unavailable preview.

Once a visible tab is attached, use a snapshot-first workflow. Snapshot output
provides visible text, accessibility data, semantic locators, console and
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

Viewport presets change CSS layout dimensions while preserving T3's desktop
browser user agent; they are breakpoint checks, not full mobile-device
emulation. Re-snapshot after resizing or scrolling before using coordinates,
and account for a scroll position that persists across viewport changes. Light,
dark, and system appearance emulation changes `prefers-color-scheme`; restore
the system setting after a bounded comparison unless the user asked to leave an
override.

Collaborative recordings are saved by the connected client. The returned path
can therefore name a client filesystem that is not mounted on the agent VM;
use T3's returned artifact metadata or link instead of trying to read or copy
that path from the VM. Start recording only when video adds useful evidence,
stop it promptly, and avoid credentials or unrelated user data. A snapshot's
action timeline can show the snapshot currently being constructed as `running`.
When the response itself has returned, that self-entry is expected; use prior
completed actions and the returned page state to assess success.

A narrower failure is possible when `preview_open` returns a usable background
tab but the desktop application never mounts a sidebar or floating preview:
status remains `visible: false`, lightweight DOM operations can succeed, and
snapshot capture fails or times out. This is stale T3 preview-presentation
state, not proof of an application, route, certificate, or VM browser failure.
Do not create more tabs. Restarting the desktop client may preserve the stale
tab registry in the VM-side service. Use managed Playwright when collaboration
is optional. If collaborative coverage is required, warn that every active T3
session will be interrupted and, only with the user's approval, restart the
server as the target user:

```bash
systemctl --user restart t3code.service
```

Reconnect the desktop client and retry status/open once. Do not use a full VM
reboot as the first recovery step. Normal setup reruns deliberately do not
restart an otherwise healthy T3 service because setup may itself be running
through an active T3 session.

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
| No automation host or unavailable capture | Use healthy VM-local Playwright when VM-origin coverage fits; otherwise report the browser gap. |
| Explicit `ERR_CERT_AUTHORITY_INVALID` | Offer `basaltwater-web ca` and [Client CA trust](CLIENT_CA_TRUST.md) only when the user wants client access restored. Otherwise skip that client-origin check. |
| Timeout, refused connection, or unreachable address | Check client routing and the gateway's source policy; CA enrollment will not repair connectivity. |
| Background DOM works but no visible surface or snapshot | Follow the bounded stale-preview recovery above. |

Before using provisioned Playwright as the fallback, run
`basaltw agent doctor --capability browser --json`. If the capability is
absent or unhealthy, continue non-browser checks and report the missing
coverage. Do not install another browser stack merely to fill that gap.

Report which network origin passed and which failed. A client-only failure
does not establish that the hosted site is down. Preserve TLS verification,
loopback bindings, and the intended access policy; client CA enrollment is
optional and does not block unrelated work.

## Verification

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
interaction/rendering smoke test passes. A stale or unsafe launcher is
unhealthy even when its smoke test passes; inspect an unsafe path, then rerun
the saved setup to reconcile it. A stale active process instead requires
restarting the agent session that owns it.

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
