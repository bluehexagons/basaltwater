# CachyOS testing status

**Evidence includes native T3 and SDL/OpenGL application checks and automated
validation on 2026-11-01, an owner report received that day, and native Godot
playtesting on 2026-10-31.**
Exact execution dates and revisions for the earlier setup report and other
earlier native checks were not supplied in the public summary.
The experimental `agent_cachyos` profile adds tools to an existing, updated
x86-64 KDE workstation. Server profiles and
CachyOS VM/container provisioning are outside its scope. See the
[setup guide](../workstations/cachyos.md) and [status legend](README.md#status-legend).

| Available feature | Status | Evidence | Next qualification boundary |
| --- | --- | --- | --- |
| Workstation setup and tool discovery | 🟡 **Partial** | Owner-reported partial local setup via `basaltw refresh`; native read-only doctor completed with 89 available, 15 deferred and no failed observations on 2026-11-01 | Fresh full setup, individual tool behavior, reboot and recovery; inventory is not application QA |
| T3 Code desktop setup | 🟡 **Partial** | Selected in the owner-reported refresh; installed `t3code-bin` 0.1.6-1 and running packaged desktop/backend observed natively | Fresh installation, actual AUR update, KDE login autostart and pairing |
| T3 Code desktop session with Codex | 🟢 **Labbed** | Owner's manual reopen after waiting succeeded; subsequent live Codex conversation, shell-tool execution, repository edits, tests, commit and push worked through T3 | Other providers, T3's interactive terminal UI, pairing/Connect and broader GUI workflows |
| T3 Code desktop refresh/relaunch | 🟡 **Partial** | Native logs confirmed the old backend still owned the T3 home during premature reopen. Fix `bf0eb670` passed automated shutdown/timeout regressions; read-only native discovery captured the live embedded server | Corrected worker has not been exercised through a live shutdown/relaunch; repeat refresh, slow-backend exit, timeout, reboot and interruption remain open |
| Graphics discovery and reference contexts | 🟡 **Partial** | Accelerated GLX and isolated llvmpipe contexts | Other devices/backends; these probes alone do not qualify application rendering |
| Native Godot rendering and gameplay | 🟢 **Labbed** | Godot 4.7.2 on KDE Wayland with NVIDIA hardware: Vulkan Forward+ and explicit OpenGL Compatibility launches, visible card scenes, gameplay and renderer settings exercised on 2026-10-31 | Other GPU vendors, HDR, performance acceptance, exported-game renderer restart and target devices |
| Native SDL/OpenGL rendering and scripted UI | 🟢 **Labbed** | Antistatic on KDE Wayland with Mesa Intel Arc OpenGL 4.6: controls-menu scrolling, pause preference toggle, reviewed application/monitor captures and clean shutdown on 2026-11-01; four fixed-frame llvmpipe effect comparisons also matched | Physical controllers, audible output, HDR, display scaling/pacing, other devices and package qualification; software comparisons do not qualify hardware rendering |
| Blender and visual workflows | 🟡 **Partial** | Small native Blender CPU render | Full project scenes, image fidelity and hardware rendering |
| Supervised development tasks | 🟡 **Partial** | Native game and GPU-recipe completion through `develop wait`; a wait timeout left the game running, intentional exit 7 remained a failure, stop preview preserved a running task and scoped stop reached `stopped` on 2026-11-01 | Interruption/disconnection, long-running descendants, unavailable user manager and logout/reboot recovery |
| T3 web runtime | 🟡 **Partial** | 0.1.6 archive, PTY, disposable HTTP startup, SQLite and generated unit checks | Live unit activation/restart, pressure and complete client/provider workflows |
| KDE desktop capture and input | 🟢 **Labbed** | Owner-approved selected-monitor capture, 120 ms keyboard/pointer holds and real Godot menus/gameplay on 2026-10-31; saved access restored without a new prompt on 2026-11-01. Earlier native checks exercised stale-generation rejection and renewal | Live takeover/revocation, monitor/scaling changes, logout/reboot and other application identities |
| Timed native observations | 🟢 **Labbed** | `input --observe` and `screenshot --next-frame --compact` returned reviewed game captures, ordered input/frame timestamps, pixel hashes and change metadata on 2026-11-01; native help correctly hides unavailable window capture | Stalled/static streams, cancellation, capture-failure recovery and latency qualification |
| Sleeping-display capture recovery | 🟢 **Labbed** | A black monitor capture recovered after `basaltw desktop --native wake` with agent control enabled; a fresh capture showed the desktop and later game presentation on 2026-11-01 | Locked, disconnected or disabled outputs, repeated sleep/wake cycles and capture-stream recovery beyond this case |
| Manual native evidence records | 🟢 **Labbed** | A 48-event playtest record attached both game and GPU-recipe tasks automatically, retained clean Git provenance and successful exits; private modes and capture paths verified. Offline finalization after helper shutdown and repeated finalization also worked | Interrupted writes/collection, truncation, unavailable task inventory and other application identities |
| Godot export prerequisite diagnosis | 🟢 **Labbed** | `develop doctor --export-check` observed Godot 4.7.2 and correctly rejected installed 4.7.1 stable and 4.7 beta4 templates as mismatched on 2026-11-01 | Matching-template positive case, template contents/provenance and actual export remain unverified |
| Optional Sunshine streaming | ⚪ **Untested** | No end-to-end stream pass recorded in these summaries | Service, pairing, video/audio/input and reconnect |
| Creative/game tools and publishing | 🟡 **Partial** | Broad tool selections included in the owner-reported refresh; Godot application QA is recorded separately above | Other selected applications, project-specific build/export, input and publishing |
| Desktop/browser CA enrollment | ⚪ **Untested** | Arch userland probes ran as container root, not on the CachyOS desktop | Full desktop and browser trust with the intended client |
| Managed Playwright | ⚪ **Planning only** | Deferred | Implementation and native qualification |

## Native Godot playtest

The 2026-10-31 application pass used the existing unlocked KDE Wayland session
and approved monitor capture/input. Native interaction covered game startup,
pause/resume, return to menu, several table environments, keyboard and pointer
actions, wins/losses/pushes, split hands and hand summaries. Menu checks covered
control rebinding and restart persistence, save creation/rename/load/deletion,
statistics, achievements, credits, window modes, frame limits, enlarged text and
live language changes. Source game fixes were verified through repeatable
project-owned regressions and native interaction.

The project quick gate passed 3,754 assertions across 74 headless routes/suites
plus affected tooling checks. Focused final graphical owners passed 535 gameplay,
387 UI-polish, 75 display/renderer and 6 viewport-material assertions. An earlier
full graphical matrix passed 6,376 assertions across 14 routes/suites; it was not
repeated after the focused follow-up fixes. These are application checks, not
Basaltwater unit-test counts or general hardware certification.

Actual Vulkan Forward+ and OpenGL Compatibility use was confirmed in game logs
and reviewed monitor captures, separately from installed-tool or GLX discovery.
OpenGL still reports unsupported static-resource feature warnings; the game's
fog-shader error and unreadable card colours were fixed. Vulkan shutdown retains
the project's documented engine/resource-cleanup diagnostics. Steam integration
and cloud sync remained disabled. Physical controllers, audible output, a live
screen reader and Sunshine streaming were not exercised.

Fresh export and automatic exported-game renderer switching remain unverified:
the Godot 4.7.2 executable had only 4.7.1 stable and 4.7 beta4 export templates.
Raw captures, task logs and player data remain private; this page records only
sanitized outcomes.

### Native workflow follow-up, 2026-11-01

The installed Basaltwater launcher reported clean source revision `5184cef0`
for this pass, including features introduced in `22baf35d` and `9fad24dc`.
The live desktop helper was restarted before testing those features. The
existing saved grant restored without another owner prompt, and native doctor
reported timestamped display power as on. That pass did not exercise wake;
the later [SDL/OpenGL follow-up](#native-sdlopengl-application-follow-up-2026-11-01)
records a sleeping-display recovery.

A project-declared GPU smoke recipe passed 75 display/renderer and 2 viewport
readback assertions. Its report observed native Wayland, Vulkan Forward+ and
NVIDIA hardware. `develop wait` retained command timing and the successful exit;
the project report supplied the rendering assertions. Additional Main Table and
Open Sky gameplay exercised held keyboard/pointer input, hand summaries,
ace scoring, a loss and a dealer-bust win through timed observations. No new
application or Basaltwater runtime defect was confirmed in this pass.

Post-input captures skipped the initial stream sample and recorded a later
receipt than input completion. A capture during scene loading still required a
later application-readiness check. These timestamps are observation evidence,
not input-to-photon latency measurements. Compact screenshots retained the
expected path, dimensions, generation, timing, hash and change fields.

The playtest evidence record retained 48 events without truncation, linked both
successful development tasks without explicit attachment, and preserved the
same clean project revision at start/end. Capture paths existed; record and
parent permissions were 0600/0700. Finishing and repeated finalization preserved
the journal and renderer annotation. A second recording survived helper stop;
offline finalization collected the intended failed/stopped task outcomes. Two
initial disposable probes had invocation quoting errors; corrected probes
established exit-7 propagation and actual stop behavior. All owned tasks ended,
and approved desktop control was restored and left available.

T3 refresh/relaunch, setup/reboot, streaming, physical audio/controllers and
Steam/cloud remain outside this follow-up. The missing-template diagnosis is
an expected prerequisite failure, not a successful export.

## Native SDL/OpenGL application follow-up, 2026-11-01

Antistatic source checks and screenshot smoke passed through `af23811f2`.
Supervised native tasks used the existing approved KDE Wayland session and
reported Mesa Intel Arc hardware. Project-protocol actions exercised controls
scrolling and a pause-menu preference change, with state assertions, reviewed
captures and successful owned-process shutdown. This is scripted application
coverage; physical gamepads, audible output and display pacing were not tested.

Four deterministic effect captures matched byte-for-byte under isolated
X11/llvmpipe. Strict batch comparison rejected incomplete metadata; the retained
per-image records separately verified actual frame, requested/applied graphics,
camera and GL identity. Earlier randomized battle captures were not treated as
whole-image equality passes. See the
[dated evidence and limits](evidence.md#cachyos-native-game-and-capture-workflow)
and [project workflow lessons](../integrations/antistatic/cachyos.md#scripted-desktop-checks).
Game packaging was intentionally left to CI and Debian under project policy.

## Setup and T3 evidence

The owner supplied a partial `agent_cachyos` command including development,
Godot, AV/GL, creative/audio, gaming/Sunshine, LAN access and webhook options.
The refresh is an indicator of basic setup functionality; not all selected
features were tested. Sunshine streaming and application workflows retain their
separate qualification gaps. See the
[sanitized selections and limits](evidence.md#owner-reported-stack-setup-rerun).

The later [T3 recovery and validation record](evidence.md#cachyos-t3-desktop-recovery)
qualifies the narrow running desktop/Codex session and discovery component.
It records the failed automatic reopen separately from the successful manual
recovery. At `bf0eb670`, all 2,107 agent-suite tests passed, including 42 focused
desktop tests, alongside compilation, documentation and package metadata checks.
Those mocked regressions do not establish a successful live refresh with the fix.

Sources: [native workstation observations](../plans/workstation-feedback-debian.md#cross-platform-findings),
[T3 component record](../agents/t3-code.md), and the
[detailed coverage map](../plans/testing-coverage.md). An isolated component
pass does not qualify a fresh workstation installation or another graphics backend.

## Next checks

1. Exercise the corrected T3 completion worker through a real shutdown/relaunch,
   then verify a provider turn and the interactive terminal; preserve the
   previous failure evidence and record the exact installed revision.
2. On a suitable disposable or explicitly authorized workstation, exercise
   fresh full setup, repeat refresh, reboot and recovery with recorded tool and
   OS versions.
3. Extend timed observations and evidence collection to stalled streams,
   cancellation and interrupted writes; extend the single sleeping-display
   recovery check to repeated cycles and unavailable outputs. Separately qualify
   takeover/revocation, monitor/scaling changes and logout/reboot grant reuse.
   Verify a matching template installation and a real export on the appropriate
   build host.
4. If selected, test T3 web-service restart and Sunshine video, audio, input and
   reconnect. Record private evidence and publish only sanitized outcomes.
