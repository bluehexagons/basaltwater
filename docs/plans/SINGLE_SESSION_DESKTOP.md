# One shared desktop session per machine

Status: implementation in progress, 2026-09-12. XRDP retained per user decision.
Runtime, CLI, setup wiring, migration guards, and managed skills are implemented;
disposable-VM qualification remains open. See [the operator guide](../XRDP.md)
for the implemented behavior and live smoke procedure. The acceptance criteria
below remain qualification targets, not a claim that live testing has passed.

## Objective and scope

Make every Basaltwater-managed desktop use one graphical session owned by
one configured non-root account. A human or an agent can start it, attach to
it, and use its applications. Disconnecting leaves it running; desktop logout
ends it. This replaces the current model across desktop setup, rather than
adding an agent-only desktop alongside existing sessions.

The primary acceptance target is a Debian agent VM with standard emulated
graphics and software rendering. Apply the same lifecycle to
`workstation_desktop`, `pc_dev`, `workstation_dev`, `agent_workstation`, and
`agent_code_vm`, and to desktop capability explicitly enabled on other
profiles. Keep `agent_vm` headless by default. Bare-metal desktop setup and
existing local desktops need an explicit migration path under the same
single-session contract.

One session means one desktop across the machine, not one session per remote
client or one per username. Other Unix accounts, SSH sessions, services, and
concurrent coding agents remain possible. Basaltwater does not support
creating additional managed graphical sessions. It does not attempt to
prevent an administrator from manually launching an unrelated X server.

## Original implementation and planned changes

| Original behavior before this project | Required change |
| --- | --- |
| `desktop/xrdp_steps.py` lets XRDP sesman create Xorg/xorgxrdp desktops; `MaxSessions` defaults to ten. | A single lifecycle owner creates the desktop; remote access attaches to it. Setting `MaxSessions=1` alone is insufficient. |
| `desktop/config/xrdp_xsession.template` starts the desktop and D-Bus from RDP login. | Move transport-independent startup and teardown into the shared session implementation. |
| `plugins/desktop.py` composes desktop installation and optional RDP; workstation profiles call it. | Make the shared session the base desktop capability, with remote transports as optional attachments. |
| `plugins/server.py` does not currently call the desktop capability builder. | Wire explicitly requested desktop capability through the server composition without installing it on all agent VMs. |
| Local workstation documentation preserves GNOME on the console and starts XFCE separately through RDP. | Retire the two-desktop model; migrate to one selected desktop or report that the existing installation cannot yet be adopted. |
| Desktop choices are XFCE, i3, Cinnamon, and LXQt; package installation can also install a display manager. | Validate each supported environment and ensure its display manager cannot independently create another user desktop. |
| Configuration, CLI, serialization, display output, and tests expose session counts and disconnect cleanup. | Replace these settings with the fixed lifecycle and migrate stored configurations. |
| Existing agent browser tools operate their own browser automation context. | Add explicit shared-desktop tools; do not assume browser tooling already controls native applications. |

See [XRDP](../XRDP.md), [workstations](../WORKSTATIONS.md), and the
[desktop maintenance audit](DESKTOP_AGENT_MAINTENANCE_AUDIT_2026-08-09.md)
for the current operator contract and prior reliability work. This plan owns
the new architecture; relevant outstanding desktop audit validation feeds
its acceptance suite.

## Fixed lifecycle contract

| Request or event | Required behavior |
| --- | --- |
| Authenticated start/connect with no session | Start the configured desktop account's session, wait for readiness, then attach. |
| Start/connect while starting | Join the in-progress operation; never spawn another desktop. |
| Start/connect while running | Return or attach to the existing session without restarting applications. |
| Viewer disconnect, network loss, or agent process exit | Leave the desktop running indefinitely. |
| Desktop logout | End the desktop, its display, and session-owned applications; invalidate its runtime metadata. |
| Logout canceled by an application | Keep the existing session and report that logout did not complete. |
| Start/connect during logout | Return a stopping/retry result; do not queue an implicit replacement session. |
| Desktop/display crash | Clean up the failed session, record the failure, and wait for a new explicit start. |
| Remote gateway crash/restart | Preserve the desktop; allow later attachment to the same session. |
| VM reboot or shutdown | End the session; do not claim to preserve live processes across reboot. |
| Boot after shutdown | Make the start/connect endpoint available; leave the desktop stopped until requested. |

The desktop must not have an unconditional restart policy. Agent input carries
a session generation. Native XRDP clients do not carry that agent generation:
a new authenticated login is an explicit start, including automatic client
reconnection. Operators must disable client auto-reconnect when logging out.

Use states `stopped`, `starting`, `running`, `stopping`, and `failed`, with a
machine-wide startup lock and a unique generation for each session. Treat
process and display readiness as evidence; a leftover PID or environment file
does not establish that a session is alive. Bound startup and teardown waits
and return actionable errors.

Desktop logout must not terminate independent SSH, tmux, T3 Code, or coding
agent services. Use session-specific process supervision rather than killing
every process belonging to the account. Verify applications launched through
D-Bus or user services are accounted for during teardown.

## Architecture and backend decision

XRDP sesman remains the single display/session owner. Both human RDP login and
local `xrdp-sesrun` use it. `MaxSessions=1`, reconnect policy `UB`, a dedicated
single-owner admission group, and a root-owned window-manager wrapper replace
the former per-user startup scripts. The wrapper supervises the chosen desktop
and a private control socket; no VNC backend or competing X server is added.

The Debian sesman unit is decoupled from frontend `BindsTo`/`StopWhenUnneeded`
behavior so frontend restarts do not deliberately tear down the session manager.
Existing xorgxrdp software-rendering, AppArmor, TLS, and resize fixes remain.
Agent startup uses Unix peer authentication without a password; human access
uses the account password. Password-protected keyrings are not automatically
unlocked by agent startup.

The console is a text/recovery surface on managed machines. Setup masks known
console display managers after all graphical sessions are logged out, retaining
the former alias for administrator rollback. This deliberately removes the
independent GNOME-console/XFCE-RDP model. Direct physical-console GUI attachment
is not implemented; do not convert a workstation that requires it. XFCE is the
primary VM target; i3, LXQt and Cinnamon remain selectable and require live
qualification before compatibility claims.

## Session implementation and human access

Implement the lifecycle in `desktop/`, exposed through the existing CLI and
plugin boundaries. Use systemd supervision appropriate to the machine and
account. Define how PAM/logind, the user runtime directory, D-Bus, and X
authentication are established without an interactive SSH or console login.
Validate required capabilities; unsupported hosts receive a clear setup
error before mutation. Never use `can_modify_kernel()` as a service/package
capability shortcut.

Keep display identity, generation, readiness, and authentication references
in private runtime state. Launch applications with the current session's
`DISPLAY`, `XAUTHORITY`, D-Bus address, and runtime directory. Do not export
stale display variables globally into shell profiles or unrelated services.
Resolve the actual account home/UID, validate paths and identities, and use
argument arrays rather than shell interpolation for application launch.

Human connection must work from a stopped desktop. Authentication precedes
session creation; an unauthenticated TCP connection must not start a desktop.
The connection entry point calls the same start-or-attach operation as agents.
Prototype this end to end; a VNC listener that exists only after an agent
starts the desktop does not satisfy the requirement.

The supported human entry point is native XRDP. Desktop profiles install a
loopback listener even without remote ingress; `--rdp` enables the configured
remote listener and firewall policy. No browser viewer or VNC transport is added.
Retain TLS, certificate health checks, channel policy and source restrictions.
The session runtime's same-UID socket requires the configured owner; it never
exposes a network automation endpoint.

Define a single desktop geometry and resize owner. Other viewers scale the
same framebuffer; competing clients must not continually resize it. Preserve
clipboard policy where supported. Inventory audio, drive redirection,
multi-monitor behavior, keyboard layouts, and lock/unlock support; explicitly
document any removed capabilities rather than carrying unsupported flags.
Locking preserves the session; reconnecting must not bypass the lock.

## Agent interface and shared control

Proposed command shapes, subject to the existing CLI conventions:

```text
basaltw desktop status --json
basaltw desktop start --json
basaltw desktop exec -- APPLICATION ARGUMENTS...
basaltw desktop screenshot --output PATH
basaltw desktop control pause
basaltw desktop control resume
basaltw desktop logout
```

These commands are implemented through the local CLI shell-tool adapter. Keep
`basaltw desktop ...` runtime operations distinct from the existing
`basaltw local desktop ENVIRONMENT` installation command. Default `exec`
and screenshot to requiring a running session; `start` is the explicit
creation operation. Provide an authenticated human connect action that
performs start-or-attach.

Expose equivalent bounded screenshot, pointer, keyboard, scroll, window, and
launch operations through a managed desktop tool adapter for agents. Return
the session generation and geometry with screenshots so input based on an old
session or resolution can be rejected. Scope control to the configured
account and active session. Register the tool only when desktop capability is
installed, and add a managed workflow skill using the existing skill delivery
pattern. Browser automation retains its separate lifecycle unless explicitly
launched into this desktop.

Provide human takeover by pausing agent desktop mutations while keeping
observation available. Serialize agent input sequences using a control lease
so multiple agents do not interleave typing and clicks. Human takeover revokes
the lease; resume is explicit. This is cooperative coordination within a
shared account, not an isolation boundary against arbitrary shell commands.
Do not promise automatic inference of human intent from mouse movement.

### Managed skills and choosing the right tool

Ship desktop guidance as part of the capability, not as optional follow-up
documentation. The managed desktop skill must describe the configured desktop
environment, available application/tool discovery, software-rendering limits,
the shared account/session, start and status operations, human connection and
takeover, and disconnect versus logout. Discover live state rather than
assuming a running desktop, a fixed display number, or installed applications.

Use this task-routing policy in the desktop skill and the existing browser
skills:

| Task | Preferred system |
| --- | --- |
| Routine browser testing, DOM assertions, console/network inspection, repeatable interactions, or VM-loopback access | Healthy managed VM-local Playwright. |
| Browser work the human should watch or participate in, or verification of the connected client's routes/trust | Available T3 Code collaborative browser. |
| Native GUI application work, desktop/window behavior, OS dialogs, or an explicit request to operate the human's shared desktop | Shared desktop tools. |
| File edits, builds, service checks, or tasks with a suitable CLI/API | Existing shell, API, or domain-specific tools. |

Browser testing should almost always use the available browser systems. Keep
the existing choice between T3 collaboration and Playwright based on the task;
do not introduce a mandatory T3-first probe when Playwright is the appropriate
tool. A running desktop or an installed graphical browser is not a reason to
switch routine web testing to desktop screenshots and clicks.

Use the desktop browser only when the task explicitly concerns that browser
session or desktop integration, the user requests it, or the preferred browser
systems are unavailable or cannot exercise the required behavior. Before a
fallback, perform the installed browser skill's bounded availability/health
checks and state the concrete reason for switching. If T3 is unavailable but
healthy Playwright fits the task, use Playwright. A failed application test is
not evidence that the browser tooling is unavailable. If no suitable browser
surface exists, report the coverage limit rather than treating a desktop
fallback as equivalent evidence.

Keep the browser contexts distinct: T3 preview runs in the connected client's
context; managed Playwright and the shared desktop browser run on the VM but
may have different profiles, cookies, and authentication. Do not transfer
profiles or credentials automatically. Report which surface produced test
evidence, especially when the network origin or user session matters.

Add the desktop skill under `common/agent_skills/` and reconcile its delivery
with desktop capability installation/removal. Update routing and cross-links
in `basaltwater-browser-testing`, `basaltwater-t3-preview-testing`, and
`basaltwater-playwright-testing`, plus the agent skill catalog and relevant
native-application guidance. Preserve each VM's capability-specific browser
skill selection; do not advertise unavailable desktop or browser tools.

## Configuration and migration

Define a versioned desktop configuration with one owner, selected environment,
remote access policy, and any qualified geometry/control options. There is no
session-count setting and no disconnect-triggered session destruction.

Update parsing, validation, saved-configuration loading, command generation,
remote forwarding, display summaries, completion, and tests together. Remove
`rdp_max_sessions`, `rdp_kill_disconnected`, and
`rdp_disconnected_timeout` from the active model. Decide whether the existing
idle timeout remains a viewer-disconnect option; it must never destroy the
desktop. Old command-line options should produce migration guidance rather
than be accepted and ignored.

Provide a narrow versioned loader migration for saved configurations:
translate old default session policy into the new fixed policy; surface
explicit nondefault multi-session/cleanup values as behavior changes in the
migration report. Do not preserve a legacy multi-session execution path.
Audit existing group-based RDP access: only the configured desktop owner may
start/own the desktop, with any human gateway identities explicitly authorized
to share that account's session.

Migration sequence:

1. Inspect installed packages, managed files, display managers, active
   sessions, saved configuration, remote access, and account ownership.
2. Produce a dry-run report showing the selected owner/environment, settings
   removed, service changes, access changes, and rollback material.
3. Stage and validate the new configuration without restarting the running
   desktop. Never auto-select and kill surplus sessions to satisfy the limit.
4. Require old graphical sessions to be logged out before cutover. If any
   remain, report the pending cutover and leave current access working.
5. Activate the new lifecycle and attachment service, disable old managed
   session-creation routes, and verify start/connect/logout behavior.
6. Remove obsolete managed units, templates, configuration, firewall rules,
   monitors, and packages only after establishing ownership and remaining
   dependencies. Preserve unrelated desktop files and application data.

Back up managed configuration and package state before cutover. Rollback
restores the previous configuration/access stack, not unsaved GUI processes.
Do not force logout for an automatic rollback after users have begun working
in the new session. Keep deployment markers sufficient to resume a failed
cutover. Explicit setup reruns now request normal managed-desktop logout before
package changes, per the operator decision on 2026-09-12. Run setup with work
saved; canceled logout aborts setup without force-killing applications. Automatic
maintenance retains its separate policy. Legacy sessions still require manual
logout. Setup leaves the desktop stopped until explicitly started again.

## Delivery sequence

| Phase | Deliverable | Exit gate |
| --- | --- | --- |
| 1. Compatibility prototype | Disposable standard-graphics VM; qualify retained XRDP, human-first startup, resize, and console conversion. | Record versions and live evidence; choose the default backend and supported transport/environment matrix. |
| 2. Shared runtime | Lifecycle owner, private runtime state, startup locking, readiness, launch, logout, crash cleanup, and JSON status. | Race/lifecycle tests pass; no automatic recreation after logout; independent agents survive. |
| 3. Human attachment | Authenticated start/connect and reconnect, lock behavior, geometry policy, and access restrictions. | Human and agent see the same application process across disconnects; transport restart preserves it. |
| 4. Agent control | Screenshot/input adapter, application launch, generation checks, takeover, managed tool/skill registration, and browser/desktop task routing. | Native application task completed while a human observes and takes over; routine browser tasks select T3 or Playwright when suitable and available. |
| 5. Setup and migration | All desktop profile builders, server opt-in, saved-config migration, old-stack cleanup, and rollback. | Fresh install, existing host migration, idempotent rerun, and interrupted cutover pass. |
| 6. Qualification and documentation | Supported environment/client matrix, maintenance checks, troubleshooting, and release notes. | Acceptance matrix below passes before changing desktop defaults. |

Land cohesive implementation changes in this order. Do not ship a temporary
agent desktop beside the legacy desktop as the final migration strategy.

## Validation and completion criteria

Unit and integration tests mock system calls and use temporary directories.
Prioritize state transitions, concurrent creation, canceled logout, stale
generation rejection, ownership checks, migration, and rollback over tests
that merely duplicate generated text. Run affected desktop, configuration,
validation, plugin, firewall, and agent-tool suites, then the relevant wider
suite. Use setup dry runs; ordinary tests must not alter the development host.

Retain a reproducible live smoke procedure on disposable machines:

| Scenario | Passing evidence |
| --- | --- |
| Agent starts first; human connects later | Same generation, display, application PID, and visible document. |
| Human connects first from stopped state | Authentication starts exactly one desktop; agent can discover and operate it. |
| Simultaneous starts/repeated connects | One graphical session and no duplicate desktop process tree. |
| Disconnect all viewers and end initiating agent | Applications remain alive and reconnect returns to them. |
| Logout and subsequent client login | Desktop ends; a new authenticated RDP login may start another generation. Test with automatic client reconnection disabled. |
| Unsaved-document logout prompt | Cancel leaves the same session usable; force termination is a separate explicit action. |
| Display crash, stale runtime state, or startup failure | Bounded cleanup, useful diagnostics, and successful later explicit start. |
| Gateway restart or network interruption | Desktop process identity remains unchanged. |
| Human takeover and multiple agents | Paused/stale input is rejected; input sequences do not interleave. |
| Browser testing with desktop and browser tools available | Agent chooses Playwright or T3 according to the task without starting/controlling the desktop merely to test a web page. |
| T3 unavailable, Playwright healthy | A suitable VM-origin browser task continues through Playwright; desktop is not the automatic fallback. |
| Native GUI task or justified desktop-browser exception | Desktop skill discovers the environment, explains the tool choice, and follows shared-session/takeover behavior. |
| Capability-specific skill installation/removal | Installed skills describe available tools and consistent routing; browser-only VMs are not instructed to use an absent desktop. |
| Resize, keyboard layout, clipboard, lock/unlock | Works for each claimed client; reproduce the previous XRDP resize failure case. |
| Standard emulated graphics | Browser, editor, terminal, file manager, and an applicable native GUI workflow operate without passthrough. Record responsiveness and resource usage. |
| Console and remote workstation access | Console graphical login is disabled; GUI access uses XRDP. Direct console GUI attachment remains outside the current implementation. |
| Migration with active legacy sessions | No application is killed; cutover reports why it is pending. |
| Headless profile and unrelated services | No implicit desktop install; logout leaves SSH, tmux, and agent services intact. |
| Reboot, maintenance, rollback, and setup rerun | Documented lifecycle holds; no unintended desktop restart or access expansion. |

Qualify supported Debian VM images and remote clients first, then the
bare-metal path and each retained desktop choice. Claim container support
only after equivalent capability and live-session checks. Do not substitute
mock-only success for the live resize/reconnect qualification.

Update `WORKSTATIONS.md`, `XRDP.md` (or its replacement transport guide),
`COMMAND_LINE.md`, `LOCAL_MAINTENANCE.md`, `MACHINE_TYPES.md`, `PROXMOX.md`,
agent guides/skills, authentication guidance, and maintenance documentation.
Describe the shared desktop as its own capability; do not revive the retired
T3 Code desktop AppImage integration.

The project is complete when every supported desktop setup uses the single
session lifecycle, either participant can initiate it, both operate the same
applications, disconnect preserves it, logout ends it without recreation,
and legacy session creation/configuration paths have been removed after a
tested migration. Multi-user desktops, persistent GUI processes across
reboots, and GPU passthrough are outside this project.

## Productivity refinements — 2026-09-12

The first productivity milestone builds on the shared XRDP runtime. CLI,
operator documentation and managed skills evolve together. The preference for
suitable shell/API tools and T3/Playwright browser testing remains unchanged.

| Refinement | Delivered scope and follow-on work |
| --- | --- |
| Window management | Focus/move/resize/maximize/minimize/restore and normal close, requiring generation and current window identity. Desktop/panel windows are excluded. Title/PID/class/X-ID fingerprints reduce stale targeting but cannot guarantee against ID reuse. |
| Semantic accessibility | Deferred: prototype bounded read-only AT-SPI role/name/state trees first; qualify GTK, Qt and office applications independently. Omit password values and cap depth, nodes and time. Add semantic actions only after identity, pause and verification contracts work. |
| Readiness waits | Present/visible/active/absent polling outside the supervisor and lease. Incomplete inventories cannot establish absence. Document/export readiness needs application adapters. |
| Launch results | Session-local launch token, PID, running/exited state, optional title wait and pre-existing window detection. Retain latest 128 records. Title matches do not prove process ownership or document readiness. |
| Visible human handoff | Application-menu control window with status and pause/resume; closing preserves pause. Permanent tray indicator and optional task labels deferred pending desktop-specific qualification. |
| Screenshot artifacts | Unique default paths in private Pictures/basaltwater storage, timestamps, window metadata and response-sharing guidance. Region selection, annotations and opt-in recordings deferred; retain original evidence alongside future annotations. |
| Coordinated sequences | 1–20 JSON actions under one revocable 30-second lease, partial results and release attempts on failure. No rollback, automatic retries or lease extension; slow waits stay outside sequences. |
| Diagnostics | Read-only doctor checks session, executables, handoff dependency and XRDP services. No automatic repairs or content capture. Human transport behavior remains unverified. |
| Document workflows | Open existing local documents using default applications and reveal their parent directory. Future export adapters must verify output content and canceled dialogs; prefer domain CLIs when suitable. |
| Repeatable qualification | Operator smoke matrix retained and expanded below. Automated opt-in disposable-VM harness and broader desktop/client matrix remain open. |

Setup adds wmctrl and python3-tk from normal distribution repositories and the
human control menu entry. Existing VMs need a setup rerun, which handles logout, for new
dependencies and the updated supervisor. Do not mix old and new runtime behavior
by replacing only parts of an active session.

Productivity qualification, using disposable documents and windows:

1. Run doctor before/after setup; missing dependencies must be reported without
   starting a session. Start explicitly; record generation and geometry.
2. Launch an editor, wait for visibility, compare PID/class/title and inspect
   launch status. Repeat with an application that reuses its existing process.
3. Exercise window operations and capture its client area. Rename its document;
   the previous identity must be rejected. Do not target unrelated windows.
4. Enter unsaved text, close normally, cancel the prompt and verify absence wait
   times out. Save and close, then observe absence.
5. Pause through Shared Desktop Control; verify mutation rejection and continued
   screenshots. Resume through its human button. Closing while paused must not
   resume agents.
6. Run successful and deliberately failing short sequences; verify partial
   results and released control. Human takeover between steps revokes mutations.
7. Open/reveal a local document and share an inspected application PNG in an
   agent response. Retain the linked artifact outside Git.

Mocked regressions cover stale targeting, normal close, canceled-close timeout,
incomplete inventory, launch failure, sequence release and artifact privacy.
The operator guide records live evidence separately. Human RDP reconnect/resize
and broader client qualification remain release gates, regardless of unit results.
