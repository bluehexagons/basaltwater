# CachyOS testing status

**Evidence includes native T3 checks and automated validation on 2026-11-01,
an owner report received that day, and native Godot playtesting on 2026-10-31.**
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
| Blender and visual workflows | 🟡 **Partial** | Small native Blender CPU render | Full project scenes, image fidelity and hardware rendering |
| Supervised development tasks | 🟡 **Partial** | Disposable tasks plus repeated native Godot game launches; task IDs, logs, successful graceful exits and scoped stop used during playtesting | Completion-wait command, interruption/disconnection, long-running descendants and recovery need separate qualification |
| T3 web runtime | 🟡 **Partial** | 0.1.6 archive, PTY, disposable HTTP startup, SQLite and generated unit checks | Live unit activation/restart, pressure and complete client/provider workflows |
| KDE desktop capture and input | 🟢 **Labbed** | Owner-approved selected-monitor capture, 120 ms keyboard/pointer holds and repeated observe/act/verify loops drove real Godot menus and gameplay on 2026-10-31; earlier native checks exercised saved-grant restoration, stale-generation rejection and renewal | New timed-observation/evidence features, live takeover/revocation, monitor/scaling changes, logout/reboot and other application identities |
| Optional Sunshine streaming | ⚪ **Untested** | No end-to-end stream pass recorded in these summaries | Service, pairing, video/audio/input and reconnect |
| Creative/game tools and publishing | 🟡 **Partial** | Broad tool selections included in the owner-reported refresh; application QA remains untested | Launch, project-specific build/export, input and publishing |
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
3. Extend the established desktop pass to timed observations, manual-evidence
   collection, completion waits and export diagnostics; separately qualify
   takeover/revocation, monitor/scaling changes and logout/reboot grant reuse.
4. If selected, test T3 web-service restart and Sunshine video, audio, input and
   reconnect. Record private evidence and publish only sanitized outcomes.
