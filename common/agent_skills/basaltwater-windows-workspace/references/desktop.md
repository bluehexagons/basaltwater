# Windows native desktop and tasks

Use the intended Windows account and interactive session. Discover the current
native command surface with `basaltw desktop --help`. `desktop doctor` checks
session/API readiness without capture, input or app launch; `desktop status`
also reports persistent human control state. A locked or secure desktop, other
accounts/sessions and UAC prompts are outside the adapter.

Run these commands from the prepared PowerShell 7 development shell. Doctor's
`worker: WindowsPowerShell-5.1` describes a hidden, bounded UI Automation helper
using Windows' .NET Framework assemblies; it does not describe your shell or
the PowerShell 7 guardians used for project tasks. Basaltwater isolates the
helper's module paths without changing your shell's `PSModulePath`. Keep using
the public `basaltw` commands rather than dot-sourcing its internal worker code.
Use `$PSVersionTable.PSVersion` to check your shell. Run project scripts with
`pwsh.exe -NoProfile -File SCRIPT.ps1` in PowerShell 7; `powershell.exe` selects
Windows PowerShell 5.1.

Use `desktop windows` for handles, identities, generation and physical geometry.
`desktop inspect --pid PID` returns bounded UI Automation controls and references.
Only explicit screenshot commands capture pixels; choose a new PNG path and
observed window selectors or an explicit monitor device. PrintWindow may not
capture hardware-rendered content correctly. Inspect the artifact before claiming
visual success; an API or process exit alone does not prove rendering/audio/input.

Screenshots return `result.observation_id`, `[width,height]` geometry, explicit
coordinate space/origins and monotonic `capture_freshness`. For stable, untimed
scenes, `--max-age-seconds 180` allows more thinking time (1–600; default 60).
Use `input ... --observation ID` for one discrete action with the matching window
selectors and capture dimensions. New captures, mutations and control/session
changes invalidate the token; status/windows never renew it. Monitor captures
cannot authorize window input. Token-bound holds and post-input `--observe` are
pending on Windows. See the shared [observation contract](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/desktop-observations.md).
During an active or uncertain keyboard lease, captures return a null observation
ID with `observation_unavailable_reason: keyboard-lease-active-or-uncertain`.
Use those images for monitoring; take a fresh capture after confirmed cleanup
before binding more input, even if the lease settled during capture.

Window actions require `--window HWND --identity IDENTITY --generation GENERATION`.
Element actions use `--ref REF --generation GENERATION`. References expire after
60 seconds or a mutation; observe again before another action. Text/focus and
window geometry actions verify their outcomes, while invoke/raw input require
task-specific reinspection. Raw input uses observed window width/height and
window-relative coordinates and requires that window to remain foreground.
Do not blindly repeat an action after timeout or an uncertain outcome.
Use `desktop input --help` to list operations and `desktop input key --help`
for scan-code editing keys and text limits.
`input chord --key CTRL+A` or `SHIFT+LEFT` submits bounded CTRL/SHIFT chords;
partial failure attempts reverse-order key release. Queue acceptance does not
verify application state; failed release requires inspection before continuing.

For frame-polled continuous keyboard state, `input hold --key W --milliseconds
600` uses the same fresh window selectors and a finite 50–5000 ms lease.
`--repeat-ms 100` opts into explicit repeated downs. The independent watchdog
stops on focus/geometry/identity/control loss, cancellation or expiry and releases
owned synthetic keys. Other mutations remain blocked during the lease;
observation and human handoff remain available. Discover its ID in
`status.input_lease`; use `input release --lease ID` to request cancellation and
`input lease --lease ID` to inspect cleanup. A physical-owner or uncertain release
requires inspection, not replay. See the [keyboard lease contract](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/windows-held-input.md).

Human override/disabled control blocks mutations and artifact/terminal handoff.
Observation remains available. Only an explicit human decision authorizes a
`desktop control ... --human-approved` transition; the task alone is not approval
to change it. While enabled, perform requested work without asking for a lease
or repeated approval. There is one input controller at a time. Browser operations
must still follow the active session's browser-tool policy.

`desktop open PATH [--reveal]` opens a local artifact or selects it in Explorer.
`desktop terminal --project PATH [--msvc]` opens the prepared PowerShell 7 shell,
optionally with the supported x64 Visual Studio environment only in that child.
Use these handoff commands when the human needs the output or a project terminal.

Use `desktop develop --help` for task options and examples.
`desktop develop doctor --project PATH` discovers project tools. `develop exec
--project PATH -- EXECUTABLE ARGV...` preserves literal arguments. `develop node
NPM_SCRIPT --project PATH [-- NPM_ARGS...]` runs a named `package.json` script,
selecting an installed fnm numeric pin and matching npm;
it rejects unsupported package-manager pins instead of using incompatible tools.
For a JavaScript file, use `develop exec --project PATH -- node.exe script.js`;
explicit execution uses that executable without selecting a project runtime.
Add `--track-children` to exec/node/run for sampled owned-child identities,
including replacement workers and observed exits. `develop diagnose ID --tree`
lists bounded retained identities and counters; `--member PID:FILETIME` selects
an exact retained token. Default diagnostics select the native root. Sampling
can miss short-lived workers; terminal cleanup does not invent their exits.
See [diagnostics](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/windows-diagnostics.md).
Godot `develop editor|run|import --project PATH` honors `GODOT_BIN`, then the managed
engine. Project pins, templates and build commands remain authoritative.

For Electron tests, read the shared
[platform testing guide](https://github.com/bluehexagons/basaltwater/blob/main/common/agent_skills/basaltwater-project-development/references/electron-testing.md#native-windows).
Use native Windows commands and graphics evidence; Xvfb and WSL results do not
qualify the Windows executable. Preserve the sandbox and distinguish a rendered
application frame from a successful launch or screenshot API call.

Tasks return IDs; use `develop status ID`, `logs ID` and `cancel ID`. `--dry-run`
does not create state. `--msvc`, `--env NAME=VALUE` and `--timeout SECONDS` apply
only to the task. Real executables are accepted directly; scripts/batch launchers
need an explicit executable wrapper. Scheduled guardians survive T3 closing,
contain child processes and settle cleanup before recording completion. Private
environment snapshots are removed afterward. Logs are bounded and require review
before sharing. No upload/signing credentials are implied by local tasks.
Save the ID, initial receipt, selected executable/runtime and worker source
revision in private notes before waiting. Reattach to that same ID after a
terminal/turn interruption; do not launch a replacement because a handle was
lost. Record installed setup, selected checkout and delivered skill provenance
separately; the session-loaded skill revision can remain unknown.

`develop wait ID [--timeout SECONDS]` waits for confirmed completion (default 60,
range 1–86400), returning JSON with `WaitState`, `TimedOut` and `Receipt`.
Exit 0 means completed success, 1 means failure or attention required, and 124
means caller wait timeout. Wait timeout/Ctrl+C leaves the task running; task
timeout remains separate in `Receipt.State`. Collect output with `logs`.

Opt-in `develop exec|node|run --test-session` creates a disposable profile and
retains provenance/logs/artifacts with the task receipt. The project must honor
`BASALTWATER_TEST_PROFILE` and atomically publish its readiness token; this does
not sandbox an app that ignores the profile. `--ready-timeout` bounds readiness
to 1–300 seconds (default 30). `develop ready ID [--timeout SECONDS]` waits for
live readiness: exit 0 ready, 1 ended/attention, 124 caller timeout. It does not
replace `wait` for confirmed terminal cleanup. Conflicting supervised work for
the same project is refused until the session settles; uncertain cleanup retains
the reservation and profile. See the [session contract](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/windows-test-sessions.md)
before adapting a project. Sessions retain a bounded host GPU/driver/display
snapshot. `desktop environment --json` collects the same read-only inventory
independently, without pixels or input/control-state changes. Partial/unavailable
sections remain explicit; mode refresh and scale do not establish per-window
DPI, HDR, renderer GPU selection or actual frame pacing. See the
[environment report](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/windows-environment.md).
Recording remains separate.

Status includes UTC execution boundaries and monotonic elapsed seconds from gate
release preparation through confirmed cleanup, excluding queue time. Unknown or
legacy timing is not reconstructed. Settled stream counters distinguish the
4 MiB retention cap from `logs` tail omission at 64 KiB; review both before sharing.
New logs retain the first 2 MiB plus a rolling last 2 MiB, with `RetentionPolicy`,
`HeadBytes` and `TailBytes` identifying the policy. Discarded bytes are the missing
middle; raw logs join head and tail without a gap marker. Older prefix logs stay
unchanged.
Project doctor only discovers paths, conventional runtime requirements and Git
revision/dirty state; it does not execute project shims, GUI tools or
recipes. Missing Git metadata stays unknown. Use host doctor for version health
and project-owned commands for verification. Keep one current checkpoint and
return evidence through the shared
[checkpoint and handoff guidance](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/handoffs.md).

Active or uncertain tasks block setup/reboot; cancellation is a request, so verify
terminal status before retrying maintenance. Tasks temporarily prevent idle sleep
and release the request when finished; they do not change the power plan or lock
policy. A GUI process remains subject to the real display/session state.

See the [Windows desktop guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/windows-desktop.md)
for command examples and qualification limits.
