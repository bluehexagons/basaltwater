# CachyOS testing status

**Evidence includes native Godot environment/menu checks on 2026-11-02,
native T3 and SDL/OpenGL checks, menu/workflow audits and automated validation
on 2026-11-01, an owner report received that day, and native Godot playtesting
on 2026-10-31.**
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
| Native Godot rendering and gameplay | 🟢 **Labbed** | Godot 4.7.2 on KDE Wayland with NVIDIA hardware: Vulkan Forward+ and explicit OpenGL Compatibility launches, gameplay and renderer settings on 2026-10-31; extended menu audit on 2026-11-01; weather/time/idle, fixed section navigation, binding chords/mouse input and settings/audio controls with focused GPU regressions on 2026-11-02 | Other GPU vendors, HDR, performance acceptance, exported-game renderer restart and target devices |
| Native SDL/OpenGL rendering and scripted UI | 🟢 **Labbed** | Antistatic on KDE Wayland with Mesa Intel Arc OpenGL 4.6: controls-menu scrolling, pause preference toggle, reviewed application/monitor captures and clean shutdown on 2026-11-01; four fixed-frame llvmpipe effect comparisons also matched | Physical controllers, audible output, HDR, display scaling/pacing, other devices and package qualification; software comparisons do not qualify hardware rendering |
| Blender and visual workflows | 🟡 **Partial** | Small native Blender CPU render | Full project scenes, image fidelity and hardware rendering |
| Supervised development tasks | 🟡 **Partial** | Native game and GPU-recipe completion through `develop wait`; a wait timeout left the game running, intentional exit 7 remained a failure, stop preview preserved a running task and scoped stop reached `stopped` on 2026-11-01. Application regression failures and an engine abort also surfaced as failed tasks with retained logs | Interruption/disconnection, long-running descendants, unavailable user manager and logout/reboot recovery |
| Managed worktrees and shared checkpoints | 🟢 **Labbed** | Native CLI create/status, shared-memory revision-checked updates, isolated edits/checks, Git fast-forward integration and push, then clean/merged status, removal preview and removal on 2026-11-01; ignored audit evidence retained before cleanup | Dirty/unmerged removal refusal, revision conflicts, concurrent editors, alternate roots and T3 worktree binding |
| T3 web runtime | 🟡 **Partial** | 0.1.6 archive, PTY, disposable HTTP startup, SQLite and generated unit checks | Live unit activation/restart, pressure and complete client/provider workflows |
| KDE desktop capture and input | 🟢 **Labbed** | Owner-approved selected-monitor capture and real Godot menus/gameplay on 2026-10-31 and 2026-11-01; 120 ms held keys/buttons, wheel input, text entry and popup interaction. Saved access restored without a new prompt; stale-capture input rejection recovered through fresh capture. Earlier native checks exercised stale-generation rejection and renewal | Live takeover/revocation, monitor/scaling changes, logout/reboot and other application identities |
| Timed native observations | 🟢 **Labbed** | `input --observe` and `screenshot --next-frame --compact` returned reviewed game captures, ordered input/frame timestamps, pixel hashes and change metadata on 2026-11-01; native help correctly hides unavailable window capture | Stalled/static streams, cancellation, capture-failure recovery and latency qualification |
| Sleeping-display capture recovery | 🟢 **Labbed** | A black monitor capture recovered after `basaltw desktop --native wake` with agent control enabled; a fresh capture showed the desktop and later game presentation on 2026-11-01 | Locked, disconnected or disabled outputs, repeated sleep/wake cycles and capture-stream recovery beyond this case |
| Manual native evidence records | 🟢 **Labbed** | Automatic task attachment, Git provenance, exit outcomes, private modes/paths, offline and repeated finalization verified. Extended audit reported its 500-event cap; shorter follow-ups retained complete events. A long-session task inventory reported truncation on 2026-11-02, with owned task completion separately verified | Interrupted writes/collection, unavailable task inventory, recovery at caps and other application identities |
| Application audio routing observations | 🟡 **Partial** | Native Godot input changed saved bus gains, mute/reset and background attenuation; a read-only application observer sampled engine meters during gameplay on 2026-11-02 | Speaker/loopback output, loudness, clipping and transition quality; the desktop helper does not capture audio |
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

### Native menu audit and worktree workflow, 2026-11-01

An extended Godot application audit used the same installed clean `5184cef0`
launcher and existing authorized helper, with one owner for desktop input and
GPU checks. No new helper restart or permission grant was needed. Reviewed
captures and interaction covered startup/loading, main and pause menus, all
five settings tabs, dropdowns, save dialogs and validation, binding capture,
statistics/detail/filter/pager views, credits/licenses, summaries and quit.
A disposable local fixture also exercised missing-asset/loading-failure UI;
it did not establish recovery of a damaged installation.

The pass used fullscreen 2560×1440 and application windows at 1600×900 and
1280×720, text scaling up to 150%, English and longer translated labels.
These were application layout changes, not physical monitor or KDE scaling
changes. Held keyboard/pointer input, wheel scrolling and text entry exercised
real menus. Input attempts rejected because their capture was stale were
retried after a fresh screenshot. `input --observe` and subsequent compact
screenshots supplied visual evidence; loading still required a separate
readiness observation. No input-to-photon latency was measured.

Native review found application UI defects, and focused fixes were checked
through repeat interaction and project-owned tests. Supervision retained
application assertion failures and a layout-related engine abort during
development as failed tasks with logs; corrected normal launches completed
with exit 0. Final delivery checks passed 3,756 assertions across 74 headless
suites plus affected tooling checks. Focused graphical checks included 2,043
main-menu and 407 UI-polish assertions. These are application results, not
Basaltwater unit-test counts, and headless success did not substitute for
desktop interaction. No new Basaltwater runtime defect was confirmed.

The long audit record finalized at its 500-event cap with
`events_truncated: true`; it was not a complete interaction journal. Its clean
start/end Git revisions differed because a commit occurred during recording.
Three shorter follow-up records finalized with 98, 54 and 20 events, without
event or task-inventory truncation. Each attached its game task automatically,
recorded a successful exit and retained the same clean project revision at
start/end. Renderer annotations were operator observations. Separate capture
receipts and an index retained evidence beyond the capped journal; raw
screenshots, task logs, player data and record identifiers remain private.

The work also exercised managed worktree creation/status, shared-memory
checkpoints with successful revision checks, isolated edits and validation,
then Git commit, fast-forward integration and push. Clean/merged status and
removal preview preceded successful managed removal. Ignored audit evidence
was copied outside the worktree and verified by SHA-256 before cleanup, with
relocation metadata preserving the meaning of original receipt paths. This
qualifies the successful CLI lifecycle, not conflict handling, unsafe-removal
refusals, concurrent editing or T3 worktree binding.

This pass used Vulkan Forward+ with Godot 4.7.2 and NVIDIA hardware; it did not
repeat OpenGL qualification. Player data was isolated and Steam/cloud disabled
for normal audit runs. Physical controllers, audible output, a live screen
reader, HDR, streaming, export and reboot/recovery remain unverified. All owned
tasks ended, and the existing desktop control grant was left enabled.

### Native environment and navigation follow-up, 2026-11-02

Continued Godot QA through the existing approved KDE Wayland helper, using
supervised launches, project GPU recipes, fresh monitor captures and held
pointer/keyboard input. One worker owned desktop input and GPU checks. The
launcher manifest reported running source `5184cef0` with a dirty flag; these
observations do not qualify a clean Basaltwater revision or a fresh setup.

Native interaction checked an outdoor table correction, rainy noon, clear
midnight, haze, disabling weather dependencies, a fast day/night cycle,
pause/resume, normal restart and idle-camera return. A private read-only
application observer compared the exact hand/shoe and counters before and
after the menu and camera flows. It did not select weather or play the hands.
Night visibility was inspected, but the sampled night was naturally clear;
it does not establish rainy-night coverage or moving-effect performance.

Fixed section navigation was exercised from both the main menu and a paused
hand. The selector stayed available, Brightness exposed its monitor reference,
and tab switching preserved focus and reading position. Native rebind
cancellation revealed a further application focus issue. Project GPU checks
covered four window sizes, all seven locales and 150% text. Preliminary
assertion failures remained failed supervised tasks with retained logs;
corrected checks passed 2,162 UI assertions and 75 display/renderer assertions.
The table's graphical owner passed 553 assertions, and the delivery quick gate
passed 3,975 assertions across 75 suites plus translation/tooling checks.
These are application results, not Basaltwater unit tests or general hardware
certification. Source fixes were committed and pushed without an export.

Three focused native evidence records finalized with 215, 47 and 79 events,
without event or task-inventory truncation. Each attached its successful game
task and retained renderer annotations; private observer states, captures,
profiles and raw identifiers remain outside Git. Bounded wait timeouts were
followed on the same task handle rather than launching another instance.
Application shutdown retained its documented resource warnings, separate from
script/runtime errors. No new Basaltwater runtime defect was confirmed.

Steam integration/cloud were disabled and application account writes blocked.
Physical controllers, audible output, HDR, target-platform exports, latency,
Sunshine, setup/reboot and interruption/recovery boundaries remain unverified.
The helper stayed enabled; no desktop permission or display-layout change was
needed. Application-only window-size tests do not qualify KDE scaling changes.

### Native controls and audio follow-up, 2026-11-02

The same approved helper and supervised development workflow supported native
modifier-chord and mouse-button rebinding, cancellation, default restoration
and restart persistence. Plain-key input left the hand unchanged; the bound
chord dealt one card and the mouse binding stood once. Compact Russian menus
at 150% text exposed application label/badge overlap, which was corrected and
rechecked through real input and focused project GPU assertions. Final focused
UI checks passed 2,531 assertions plus 75 display preflight assertions; the
delivery gate passed 3,975 assertions across 75 headless suites and affected
tooling. These are project-owned checks, not Basaltwater unit-test counts.

Native slider changes reached distinct production audio bus gains and survived
restart. With Main at 50%, Background Volume 0% set Master to zero on focus
loss, while Background Volume 50% set it to 25%; refocusing restored 50%.
Individual bus gains were preserved. Unpaused gameplay produced Music and
Ambient meter activity. Music at zero reached the meter floor while Ambient
continued; restoring defaults returned gains to one and music activity resumed.
The hand/shoe and play counters stayed unchanged through the settings flows.

The observer was private, application-specific and read-only. It sampled engine
state; it was not desktop audio capture or a listening check. Gameplay music
was available, but the local presentation bundle contained no menu music tracks.
Audible mix, speakers, clipping, transitions and Steam-overlay attenuation
remain unverified. Source sessions used synthetic player data with Steam/cloud
disabled and the application's account-write guard. No export was attempted.

Two focused audio evidence records finalized with 74 and 100 events and no
event truncation. The later record reported `task_inventory_truncated: true`;
its owned successful task was present and separately checked through
`develop wait`. This does not establish a complete task inventory. Raw records,
captures, profiles and observer states remain private. Normal exits retained
the application's known shutdown warnings without runtime errors. Running
Basaltwater source still reported `5184cef0` with a dirty flag, so this is not
clean-release qualification.

Useful additions would be app-scoped loopback capture for audio acceptance and
paginated or time-scoped evidence task discovery for long sessions. Neither is
claimed as an available, qualified capability here.

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
