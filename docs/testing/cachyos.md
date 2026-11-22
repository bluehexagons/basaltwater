# CachyOS testing status

**Evidence includes a native workstation refresh with T3 desktop handoff on
2026-11-22, native Godot environment/menu checks on 2026-11-02,
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
| Workstation setup and tool discovery | 🟡 **Partial** | Refreshed setup at `4ec834cb` on 2026-11-22; the native doctor completed with 89 available, 16 deferred and no failed observations | Fresh full setup, individual tool behavior, reboot and recovery; inventory is not application QA |
| T3 Code desktop setup | 🟡 **Partial** | Selected in the owner-reported refresh; installed `t3code-bin` 0.1.6-1 and running packaged desktop/backend observed natively | Fresh installation, actual AUR update, KDE login autostart and pairing |
| T3 Code desktop session with Codex | 🟢 **Labbed** | Owner's manual reopen after waiting succeeded; subsequent live Codex conversation, shell-tool execution, repository edits, tests, commit and push worked through T3 | Other providers, T3's interactive terminal UI, pairing/Connect and broader GUI workflows |
| T3 Code desktop refresh/relaunch | 🟡 **Partial** | After refresh at `4ec834cb`, the completion journal recorded handoff and the same unit was running the packaged desktop. A Codex turn and shell tool calls worked through T3. Earlier logs captured the premature-reopen failure | Repeat refresh, interactive terminal UI, slow-backend exit, timeout, reboot and interruption |
| Graphics discovery and reference contexts | 🟡 **Partial** | Accelerated GLX and isolated llvmpipe contexts | Other devices/backends; these probes alone do not qualify application rendering |
| Native Godot rendering and gameplay | 🟢 **Labbed** | Godot 4.7.2 on KDE Wayland with NVIDIA hardware: Vulkan Forward+, explicit OpenGL Compatibility and Vulkan Mobile source launches; extended menus, weather/time/idle, binding chords/mouse input, settings/audio controls and focused GPU regressions through 2026-11-02 | Other GPU vendors, HDR, performance acceptance, exported-game renderer restart and target devices; desktop Mobile does not qualify a mobile device |
| Native SDL/OpenGL rendering and scripted UI | 🟢 **Labbed** | Antistatic on KDE Wayland with Mesa Intel Arc OpenGL 4.6: controls-menu scrolling, pause preference toggle, reviewed application/monitor captures and clean shutdown on 2026-11-01; four fixed-frame llvmpipe effect comparisons also matched | Physical controllers, audible output, HDR, display scaling/pacing, other devices and package qualification; software comparisons do not qualify hardware rendering |
| Blender and visual workflows | 🟡 **Partial** | Small native Blender CPU render | Full project scenes, image fidelity and hardware rendering |
| Supervised development tasks | 🟡 **Partial** | Native game and GPU-recipe completion through `develop wait`; a wait timeout left the game running, intentional exit 7 remained a failure, stop preview preserved a running task and scoped stop reached `stopped` on 2026-11-01. Application regression failures and an engine abort also surfaced as failed tasks with retained logs | Interruption/disconnection, long-running descendants, unavailable user manager and logout/reboot recovery |
| Managed worktrees and shared checkpoints | 🟢 **Labbed** | Native CLI create/status, shared-memory revision-checked updates, isolated edits/checks, Git fast-forward integration and push, then clean/merged status, removal preview and removal on 2026-11-01; ignored audit evidence retained before cleanup | Dirty/unmerged removal refusal, revision conflicts, concurrent editors, alternate roots and T3 worktree binding |
| T3 web runtime | 🟡 **Partial** | 0.1.6 archive, PTY, disposable HTTP startup, SQLite and generated unit checks | Live unit activation/restart, pressure and complete client/provider workflows |
| KDE desktop capture and input | 🟢 **Labbed** | Owner-approved selected-monitor capture and real Godot menus/gameplay on 2026-10-31 and 2026-11-01; 120 ms held keys/buttons, wheel input, text entry and popup interaction. Saved access restored without a new prompt; stale-capture input rejection recovered through fresh capture. Earlier native checks exercised stale-generation rejection and renewal | Live takeover/revocation, monitor/scaling changes, logout/reboot and other application identities |
| Physical keyboard injection and helper state | 🟢 **Labbed** | Evdev bracket presses, Ctrl+A, Shift+W and an unmodified following key reached disposable Godot 4.7.2 XWayland and Wayland fields on 2026-11-02; held Ctrl appeared in helper-only status and cleared after release | Godot's bracket physical codes differed by display backend; global hardware modifiers, lock state, other layouts/providers and live interruption remain unverified |
| Session-scoped task inventory | 🟢 **Labbed** | Time-filtered discovery recovered one task from a game session whose global listing was truncated; new evidence automatically attached its in-session probe and finalized without inventory truncation on 2026-11-02 | The separate 500-entry scan cap, more than 50 matching tasks and interruption remain qualification boundaries; mocked cap/timezone regressions are separate |
| Timed native observations | 🟢 **Labbed** | `input --observe` and `screenshot --next-frame --compact` returned reviewed game captures, ordered input/frame timestamps, pixel hashes and change metadata on 2026-11-01; native help correctly hides unavailable window capture | Stalled/static streams, cancellation, capture-failure recovery and latency qualification |
| Sleeping-display capture recovery | 🟢 **Labbed** | A black monitor capture recovered after `basaltw desktop --native wake` with agent control enabled; a fresh capture showed the desktop and later game presentation on 2026-11-01 | Locked, disconnected or disabled outputs, repeated sleep/wake cycles and capture-stream recovery beyond this case |
| Manual native evidence records | 🟢 **Labbed** | Automatic task attachment, Git provenance, exit outcomes, private modes/paths, offline and repeated finalization verified. Extended audit reported its 500-event cap; shorter follow-ups retained complete events. A long-session task inventory reported truncation on 2026-11-02, with owned task completion separately verified | Interrupted writes/collection, unavailable task inventory, recovery at caps and other application identities |
| Application audio routing observations | 🟡 **Partial** | Native Godot input changed saved bus gains, mute/reset and background attenuation; a read-only application observer sampled engine meters during gameplay on 2026-11-02 | Speaker/loopback output, loudness, clipping and transition quality; the desktop helper does not capture audio |
| Godot export prerequisite diagnosis | 🟢 **Labbed** | `develop doctor --export-check` observed Godot 4.7.2 and correctly rejected installed 4.7.1 stable and 4.7 beta4 templates as mismatched on 2026-11-01 | Matching-template positive case, template contents/provenance and actual export remain unverified |
| Optional Sunshine streaming | 🟡 **Partial** | After refresh at `4ec834cb`, the enabled service was active without restarts and its journal reported H.264 and HEVC NVENC detection | Moonlight pairing, video/audio/input and reconnect |
| Creative/game tools and publishing | 🟡 **Partial** | Broad tool selections included in the owner-reported refresh; Godot application QA is recorded separately above | Other selected applications, project-specific build/export, input and publishing |
| Desktop/browser CA enrollment | ⚪ **Untested** | Arch userland probes ran as container root, not on the CachyOS desktop | Full desktop and browser trust with the intended client |
| Managed Playwright | ⚪ **Planning only** | Deferred | Implementation and native qualification |

## Prepare a current native test

From the owning KDE account, use a reviewed checkout of the candidate revision
to inspect the installed state. Preview the saved setup with the installed CLI,
which has the managed upgrade channel.

```fish
git rev-parse HEAD
git status -sb
python3 -B ./basaltwater.py local cachyos-doctor --json
python3 -B ./basaltwater.py agent manifest . --installation-context --capability skills --json
basaltw refresh --dry-run
```

Confirm that the displayed channel will apply the candidate revision. The
`dev` channel follows `main`, and version or commit channels remain pinned.
Record checkout and installed revisions, failures, and deferred observations in
private evidence, including any pending T3 completion. Compare the T3 version
in use with the
[shared baseline](../agents/t3-qualification.md#stable-feature-baseline) and
the package available for the selected desktop or web mode. Use the
[refresh guide](../workstations/cachyos-refresh.md#upgrade-and-repeat-your-last-setup)
to apply the candidate, then retain the completion journal and repeat the
checks. Run an authenticated provider turn and terminal in the selected
environment before starting project-specific tests.

## Next checks

1. Verify T3's interactive terminal UI after a repeat refresh. Record the
   installed revision and worker journal, including any slow or interrupted
   relaunch.
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

## Choose a topic

<a id="native-godot-playtest"></a>
<a id="native-workflow-follow-up-2026-11-01"></a>
<a id="native-menu-audit-and-worktree-workflow-2026-11-01"></a>

- [CachyOS Godot playtest evidence](cachyos-godot-evidence.md) Native playtest, project gates, and initial menu and worktree follow-ups.

<a id="native-environment-and-navigation-follow-up-2026-11-02"></a>
<a id="native-controls-and-audio-follow-up-2026-11-02"></a>
<a id="native-translated-menus-and-input-limits-2026-11-02"></a>
<a id="native-renderer-and-guarded-menu-follow-up-2026-11-02"></a>
<a id="native-keyboard-and-task-inventory-improvements-2026-11-02"></a>
<a id="native-sdlopengl-application-follow-up-2026-11-01"></a>

- [CachyOS desktop and input evidence](cachyos-desktop-evidence.md) Later native Godot, keyboard, audio, renderer, and SDL/OpenGL checks.

<a id="setup-and-t3-evidence"></a>

- [CachyOS setup and T3 evidence](cachyos-setup-evidence.md) Reported refresh, native recovery, and established limits.
