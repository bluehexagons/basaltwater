# CachyOS agentic desktop capabilities

Status: initial native setup and T3 access slice implemented; live qualification
pending. Portable contracts and read-only diagnostics can be implemented before
qualification. Machine-use feature release remains gated on the P0 live pass.
The development host is Debian, which cannot supply CachyOS/Plasma acceptance
evidence. Do not mark this project complete from mocked or headless tests.

## Objective

Expand the existing `agent_cachyos` profile into a useful agentic workstation
for modern bare-metal CachyOS installations while preserving the user's KDE
desktop and account ownership. The profile should make selected tools easy to
install, update, observe, and recover, and should give an agent bounded access
to browser and desktop workflows when the user explicitly enables those
capabilities.

The current profile covers native package installation for the coding, creative,
media, gaming, remote desktop, and sysadmin bundles, user-managed Codex/OpenCode
updates, workspaces, and an optional T3 Code service with loopback access,
private-LAN pairing, and documented T3 Connect setup. The next work should add
capability contracts and safe integrations around that foundation rather than
replace it with a second desktop or provisioning system.

## Boundaries and non-goals

- Keep the existing human user, KDE Plasma session, GPU/driver setup, network
  policy, firewall, login manager, and OS update policy under user control.
- Install system dependencies only from enabled CachyOS/Arch repositories. Do not
  add AUR, Flatpak, opaque vendor installers, or an automatic full-system
  upgrade to this profile. Existing user-managed coding agents and T3 retain
  their documented upstream installation paths. The selected Playwright runtime
  is a separate, explicit exception: pin its package and matching upstream
  Chromium revision under a private user-owned prefix. Never run Playwright's
  Debian-oriented `install-deps` or `--with-deps` on CachyOS.
- Keep machine-use features disabled unless selected during setup. No default
  screen capture, input injection, clipboard export, remote bind, credential
  copying, or unattended background agent.
- Treat the T3 Code desktop app as a client and collaboration surface. Do not
  build a competing custom desktop controller into it; local browser and
  desktop capabilities should report status and artifacts through existing
  interfaces.
- The existing `infra-tools desktop` command remains the XRDP/X11 workflow
  until a separate Wayland implementation is qualified. Do not silently route
  CachyOS sessions through X11-only tools such as `xdotool`.

## Capability decisions to make first

The implementation must distinguish three layers:

1. **Browser automation** for web applications, using a user-scoped,
   version-matched Playwright runtime and isolated browser contexts.
2. **Semantic desktop access** through AT-SPI for application trees, actions,
   text, and state. This is the preferred native-app interface when an
   application exposes a usable accessibility tree.
3. **Wayland session access** through XDG Desktop Portals for user-approved
   screenshots and pointer/keyboard input. Portal access is the Wayland path;
   it must not be emulated with a privileged global input daemon.

Pixel or uinput fallbacks are a final, separately reviewed option. `wtype` may
be considered for text-only entry after KWin testing. `ydotool` should not be
offered by default because its broad input path would weaken the session
boundary. Existing X11 helpers can remain available only to the desktop
profile that owns them.

## Delivery sequence

### P0 — compatibility and capability contract

The initial native setup, T3 access, and read-only doctor slices are implemented.
Before releasing further setup mutations or machine-use capabilities, qualify a
disposable CachyOS x86_64 KDE Wayland machine and record the following. A VM may
exercise diagnostic and session helpers, but the existing setup profile
intentionally rejects VMs; full setup acceptance requires disposable bare metal.
Do not bypass the hardware check or interpret VM results as GPU/hardware
qualification.

- Plasma, KWin, Wayland, PipeWire, portal backend, AT-SPI, Python GObject, and
  browser package versions.
- Session environment and user-systemd behavior after login, logout, reboot,
  and a second login.
- GTK and Qt accessibility behavior, including KDE utilities and representative
  applications such as Blender, GIMP, Remmina, and a text editor.
- GPU/compositor behavior for screenshots, browser rendering, video playback,
  Sunshine, and Moonlight.
- Failure behavior when DNS, package mirrors, portal permissions, or the
  graphical session is unavailable.

Define a versioned capability result before wiring it into setup or the web
panel. Each result should include a capability name, `available`, `deferred`, or
`failed` state, a user-facing reason, the owning user/session, an origin such as
`vm-local`, `portal`, or `t3-preview`, whether interaction is required, and a
sensitivity marker. Keep this separate from package installation success so a
healthy agent update is not reported as an unhealthy desktop.

Record observation time separately from successful live verification. A
package or bus-name check proves only that prerequisite, never usable desktop
control. Unknown selection and permission state must remain unknown rather
than being inferred from installed packages. Query only already-owned bus
names; do not activate portal or accessibility services as a doctor side effect.
Do not publish environment values, raw journal output, window titles, device
names, or personal mount paths in the default support record.

**Gate:** a live test must show that probing the machine does not alter the
desktop, start a remote listener, or capture data. Unsupported capabilities
must produce an actionable result rather than a generic setup failure.

### P1 — browser automation as the first machine-use feature

Add an opt-in CachyOS flag such as `--browser-automation playwright`. Keep the
runtime and browser under a managed, user-owned path with provenance and an
atomic update marker. Pin the Playwright package and browser pair; do not
silently substitute an arbitrary distro browser when the versions do not
match. Reconcile the runtime on setup reruns and retain the previous known-good
pair until a readiness check passes.

The browser capability should provide:

- isolated temporary contexts per task, with no reuse of the user's cookies,
  password store, extensions, or personal browser profile;
- explicit navigation and download/upload origin policy, bounded artifact size,
  private artifact ownership, and retention cleanup;
- a readiness check for the runtime, browser launch, loopback HTTPS policy, and
  the session's network origin;
- clear handling for missing mirrors, DNS failures, first-run browser prompts,
  and an already-running user browser;
- a doctor result that distinguishes a missing optional feature from a broken
  selected feature.

Use the existing VM Playwright design as the reference for isolation and
artifacts, while documenting the CachyOS-specific user-session and package
policy. The [Playwright browser lifecycle](https://playwright.dev/docs/browsers)
and [browser automation guide](../BROWSER_AUTOMATION.md) describe the version
matching and artifact constraints that this work must preserve.

**Acceptance:** a local test page can be opened, interacted with, and captured
from a fresh context; setup reruns are idempotent; an interrupted update leaves
the old pair usable; personal credentials and cookies are never imported,
transient task profiles are private and removed after the task; and a
failed browser launch identifies the missing dependency or session condition.

### P1 — read-only desktop and host observability

Extend the structured CachyOS doctor, which already reports the initial
read-only prerequisites, without changing state:

- session type, active graphical user, display/socket variables, compositor,
  PipeWire, portal backend, AT-SPI bus, and user-systemd health;
- native agent versions and provenance, selected application packages, T3 Code
  service state, and workspace health;
- GPU/rendering information, audio devices, mounted data paths, capacity, and
  recent setup/update failures;
- whether a selected capability is waiting for a human permission dialog.

Use native diagnostics (`systemctl --user`, `journalctl --user`, `wpctl`,
`wayland-info`, `vulkaninfo`/`glxinfo`, and `pacman`) behind bounded subprocess
wrappers. Return redacted, size-limited records suitable for the web panel and
support snapshots. Observability must not imply control or silently grant
additional permissions.

For single screenshots, qualify the
[Screenshot portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Screenshot.html).
For continuous capture, qualify the
[XDG ScreenCast portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html)
with the KDE backend and PipeWire. The first capture should require the normal
user consent flow; saved images must be private, bounded, and removable.

### P2 — semantic native-app workflows

Reuse the bounded AT-SPI concepts in `desktop/accessibility.py`, but give the
CachyOS implementation its own user-session lifecycle instead of depending on
XRDP or X11. Provision `at-spi2-core` and `python-gobject` only when this
capability is selected, then expose a small contract for:

- launching a named, allowlisted application;
- listing windows and application identity;
- inspecting a bounded accessibility tree with stable, task-local references;
- focusing an element, setting text, invoking an action, and waiting for a
  state change;
- verifying an expected file or application state after a mutation.

Start with a tested matrix of GTK and Qt applications. Include a text editor,
KDE file picker, browser, and one representative application from the creative
bundles. Expand to Blender, GIMP, Krita, Inkscape, Scribus, Audacity, LMMS,
Kdenlive, Shotcut, FreeCAD, KiCad, Remmina, and virt-manager only when their
accessibility trees and dialogs are reliable.

Every operation needs bounded tree depth/count, output size, helper time,
stale-reference rejection, password/secret redaction, and a clear
`interactive_required` result when the user must decide. The
[AT-SPI2 API](https://docs.gtk.org/atspi2/) is the source of truth for exposed
roles, actions, text, and state.

AT-SPI access is not a portal permission boundary: a same-user process may
observe other accessible applications. Enforce task/window scope in the helper,
keep it opt-in, and disclose this limitation. Do not claim that a managed
helper can sandbox arbitrary code already executing as the desktop user.

**Acceptance:** a controlled editor workflow can create and save a file; a
dialog wait survives a slow application; an element from another window cannot
be targeted; stale references fail safely; and a human can pause or take over
without restarting the session.

### P2 — user-approved Wayland control

Implement portal-backed capture and input only after the read-only and semantic
layers are stable. Use the KDE backend's
[RemoteDesktop portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)
and its EIS input path where available. Scope each session to the active user,
selected devices, and an explicit lifetime. Make permission prompts and revoke
behavior visible in the UI.

The control lease must support pause, expiration, human handoff, and explicit
cleanup. Clipboard operations should be opt-in and bounded; consider the
[portal clipboard interface](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Clipboard.html)
separate from input permissions. Never require root, a setuid helper, or a
machine-wide input daemon. If the portal backend or graphical session is
missing, return a capability failure with remediation rather than falling back
to unrestricted input.

**Acceptance:** after user consent, an agent can capture a selected screen and
perform a bounded pointer/key action; revocation prevents further actions;
logout and lease expiry clean up the session; and no control is possible while
the human pause is active.

### P3 — application bundles and agent workflows

Keep application installation declarative and native while adding only the
workflow metadata needed for a useful agentic desktop:

- creative/media: Blender, GIMP, Krita, Inkscape, Scribus, Audacity, Ardour,
  LMMS, Kdenlive, and Shotcut;
- engineering/admin: FreeCAD, KiCad, Remmina, virt-manager, Wireshark, nmap,
  tcpdump, and BIND utilities;
- gaming/streaming: Steam or the current CachyOS gaming bundle, Sunshine,
  Moonlight, and OBS.

Each bundle should declare native package names, readiness probes, expected
desktop categories, optional GPU/audio requirements, and whether a human setup
step is needed. Do not encode vendor-specific UI automation into setup. The
agent can launch an application or report its readiness only after the bundle's
contract passes.

Add optional workflows for project capture/export, local media conversion,
remote desktop troubleshooting, VM inspection, and game/streaming diagnostics.
These workflows should produce reviewable artifacts and commands, with a dry
run and a user confirmation before destructive or externally visible actions.

### P3 — maintenance, recovery, and collaboration

Make infrequently used desktops recoverable on setup reruns without disturbing
an active session:

- reconcile selected native packages, user-managed agents, browser runtimes,
  workspace records, and T3 Code state;
- prune only exact managed caches, stale browser versions, and expired artifacts
  after a readiness check; never remove user application data;
- preserve provenance, previous-known-good versions, and a recovery marker for
  every managed runtime;
- expose maintenance age and pending cleanup in the doctor and web panel;
- publish capability status and artifacts to the existing T3 Code/web-panel
  surfaces without adding a second remote-control channel.

Updates must be atomic, retryable, and safe when the graphical session is in use.
No setup step should restart Plasma, KWin, the user's browser, or a running
creative application as a side effect.

## Proposed configuration and API surface

Start with narrow, explicit options rather than a broad `--agentic` switch:

- `--browser-automation playwright` / `--no-browser-automation`;
- `--desktop-automation portal` / `--no-desktop-automation`;
- a separate accessibility capability selection if AT-SPI proves useful before
  portal input is ready;
- existing application bundle flags and `--web-interface t3code` remain
  independent; T3 Connect authorization remains an interactive, post-setup CLI
  flow using the managed runtime.

The CachyOS profile currently bypasses controller-side setup persistence.
Introduce a private, versioned user-local selection record at the target-side
setup boundary; do not assume the generic saved-host cache exists. Separate
requested selection from the last successful reconciliation and preserve the
last known-good runtime on failure. Validate schema, ownership, and symlinks;
serialize no credentials. Omission stops management without deleting data.

Add doctor fields for selection (nullable until known), state, observation
time, and last successful live verification. Every command and web-panel
record should carry the capability origin, owning user, sensitivity, and
whether human interaction is pending (nullable when unknown). Keep error text
actionable and avoid treating optional capability failures as failures of
unrelated agent updates. Initially expose read-only diagnostics through
`infra-tools local cachyos-doctor --json`; this command must also explain an
unsupported host without probing its unrelated desktop.

## Security and privacy requirements

- Run under the existing user account and active session; package installation
  is the only operation that may request `sudo`, and only for selected native
  dependencies.
- Bind local services to loopback by default. Direct T3 private-LAN binding is
  an explicit pairing option; T3 Connect uses the loopback origin expected by
  its managed relay. Do not expose portal or browser-control endpoints directly
  on the network.
- Treat page text, accessibility labels, files, and screenshots as untrusted
  input. Do not let UI content change setup policy, package sources, credentials,
  or control scope.
- Never copy credentials into browser profiles or workspace artifacts. Redact
  password fields, tokens, private keys, and protected clipboard content.
- Use bounded leases, process identity checks, task-local references, artifact
  quotas, timeouts, and cleanup markers. A crash must release control and leave
  the human session usable.
- Document how self-signed local HTTPS is trusted for testing and retain a
  strict verification option for deployments that require it.

## Verification strategy

Unit tests should mock system calls and cover configuration parsing,
serialization, package selection, capability states, provenance, update
rollback, doctor aggregation, portal errors, accessibility limits, lease
expiry, and artifact cleanup. Keep these tests independent of a graphical
session.

Live qualification should use a disposable CachyOS VM or bare-metal test
machine with Plasma Wayland and cover:

1. fresh setup, rerun, interrupted setup, and recovery;
2. package/DNS/mirror failure and a missing optional dependency;
3. browser cold start, isolated context, local HTTPS, upload/download, and
   cleanup;
4. portal consent, screenshot, input, clipboard opt-in, revoke, logout, and
   pause/handoff;
5. AT-SPI inspection and a save workflow in both GTK and Qt applications;
6. GPU, audio, Sunshine/Moonlight, OBS, T3 Code local/LAN pairing, and T3
   Connect readiness;
7. concurrent human use, a second login, reboot, and active application update;
8. support snapshot redaction and web-panel rendering of unavailable,
   deferred, and pending-interaction states.

CI should continue to validate the portable contracts on the existing runners.
Wayland, GPU, portal, and hardware behavior belongs in a clearly labeled
manual or dedicated CachyOS qualification job; a headless CI pass must not
claim that desktop control works.

## Rollout and completion criteria

Ship each capability behind an opt-in flag and a feature marker. Setup should
install or reconcile only selected features, leave unselected existing tools
alone, and make disabling a feature stop future management without deleting
user data. Provide a doctor command, recovery instructions, and a removal path
for every managed runtime.

This plan is complete when the capability contract, operator documentation,
skill guidance, setup persistence, doctor output, security review, focused unit
tests, and live CachyOS qualification matrix are all present; browser and
portal/AT-SPI features pass their acceptance cases; updates recover cleanly;
and the T3 Code desktop app remains an optional client rather than a second
desktop-control implementation.

## Implementation record

- Implemented the versioned capability metadata contract and
  `infra-tools local cachyos-doctor [--json]`, with fixed read-only probes,
  bounded streaming, local bus addressing, and no service activation.
- Added mocked contract, parser, privacy, ownership, and probe-bound tests,
  operator documentation, and workstation skill guidance.
- Live P0 qualification is pending; no CachyOS machine has been supplied.
  Use the [qualification checklist](CACHYOS_AGENTIC_DESKTOP_QUALIFICATION.md)
  to record evidence. No machine-use flag has been enabled.
- The initial native package, workspace, user-service, private-LAN pairing, and
  T3 Connect guidance is implemented; live service and hardware qualification
  remain open.
- Browser runtime/isolation, selection persistence, full host/application
  diagnostics, AT-SPI operations, portal leases/input/capture, application
  workflows, recovery, and web-panel integration remain unimplemented. This
  milestone is the P0 portable foundation, not completion of P0 or the project.

## Open decisions and issue slices

Resolve these in order and record the result in the relevant issue or plan:

1. Use pinned Playwright-managed Chromium as the explicit user-runtime
   exception above; qualify CachyOS dependencies before enabling setup.
2. Portal permission lifetime and whether restored permission tokens are ever
   enabled by default. Default to no restored permission tokens; consent and
   a fresh lease are required for each control session.
3. The minimum reliable GTK/Qt application matrix for AT-SPI.
4. Whether any uinput fallback can meet the security boundary; default answer is
   no until demonstrated otherwise.
5. Whether T3 Code needs status/artifact links only or a narrowly scoped client
   action API.
6. Supported CachyOS CPU variants and GPU/compositor combinations.

Suggested issue slices are: compatibility gate; capability/result schema;
browser runtime and isolation; read-only doctor; ScreenCast portal; AT-SPI
session runtime; RemoteDesktop portal and leases; application bundle readiness;
maintenance/recovery; web-panel/T3 status integration; security review; and
live qualification.
