# CachyOS Godot playtest evidence

[Topic index](cachyos.md)

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
the later [SDL/OpenGL follow-up](cachyos-desktop-evidence.md#native-sdlopengl-application-follow-up-2026-11-01)
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
