# CachyOS agentic desktop capabilities

Status: proposed, unscheduled P3 follow-on. This plan depends on the P0/P1
reliability and state contracts, and on a live qualification pass on a real
CachyOS Plasma session. It is a delivery plan for future work, not a request to
enable desktop control on existing installations.

## Objective

Expand the existing `agent_cachyos` profile into a useful agentic workstation
for modern bare-metal CachyOS installations while preserving the user's KDE
desktop and account ownership. The profile should make selected tools easy to
install, update, observe, and recover, and should give an agent bounded access
to browser and desktop workflows when the user explicitly enables those
capabilities.

The current profile already covers native package installation for the coding,
creative, media, gaming, remote desktop, and sysadmin bundles, user-managed
Codex/OpenCode updates, workspaces, and an optional loopback T3 Code service.
The next work should add capability contracts and safe integrations around that
foundation rather than replace it with a second desktop or provisioning
system.

## Boundaries and non-goals

- Keep the existing human user, KDE Plasma session, GPU/driver setup, network
  policy, firewall, login manager, and OS update policy under user control.
- Install only current packages from enabled CachyOS/Arch repositories. Do not
  add AUR, Flatpak, opaque vendor installers, or an automatic full-system
  upgrade to this profile.
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

Before adding setup mutations, qualify a disposable CachyOS x86_64 KDE
Wayland machine and record:

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
the old pair usable; credentials and cookies never enter managed paths; and a
failed browser launch identifies the missing dependency or session condition.

### P1 — read-only desktop and host observability

Add a structured CachyOS doctor that can report, without changing state:

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

For screenshots, qualify the
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
  independent.

Persist selected capabilities and versions in the existing setup state with
schema validation and provenance. Add doctor fields for selected, available,
  deferred, failed, and last-verified. Every command and web-panel record
  should carry the capability origin, owning user, sensitivity, and whether a
  human interaction is pending. Keep error text actionable and avoid treating
  optional capability failures as failures of unrelated agent updates.

## Security and privacy requirements

- Run under the existing user account and active session; package installation
  is the only operation that may request `sudo`, and only for selected native
  dependencies.
- Bind local services to loopback unless an existing, separately reviewed
  gateway explicitly publishes them. Do not expose portal or browser control
  endpoints directly on the network.
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
6. GPU, audio, Sunshine/Moonlight, OBS, and T3 Code readiness;
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

## Open decisions and issue slices

Resolve these in order and record the result in the relevant issue or plan:

1. Pinned Playwright-managed Chromium versus a distro browser with an explicit
   compatibility check.
2. Portal permission lifetime and whether restored permission tokens are ever
   enabled by default.
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
