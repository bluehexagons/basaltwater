# Windows game testing facilities

Status: input fidelity, opt-in test sessions, owned-child diagnostics and bounded
keyboard holds are implemented; capture, pacing and audio facilities remain
planned. Basaltwater owns OS facilities and lifecycle/evidence collection.
Projects own readiness signals, assertions, deterministic replays and capture
recipes. This plan does not introduce commands for unimplemented features.

## Input fidelity

The native adapter now maps named keys through the target thread's keyboard
layout with scan-code and E0 extended flags. It supports Home/End, F1–F12,
Ctrl/Shift chords and sequences of up to 32 chords in one SendInput call.
It retains current-account/session, observed identity/geometry, foreground,
physical-modifier and human-control guards. Partial delivery is an error with
best-effort releases and no replay. Unicode text remains a separate path.

Offline construction and transport tests cover flags, ordering, bounds, invalid
input and partial submission. The [native readiness and acceptance record](../workstations/windows-testing.md)
documents installed-host/session qualification and the unprefixed-navigation
mapping defect found in SDL. Corrected source explicitly marks dedicated
navigation keys extended. Refreshed installed `58206546` passed ten real SDL
input cases and 27 Antistatic editing, selection, resize and fullscreen
assertions without a private input helper. The older installed `b16d1ca0`
requires an update. WinForms and other widgets remain separate acceptance checks:

1. Use a disposable text fixture in each toolkit. Record the loaded Basaltwater
   revision, keyboard layout and toolkit version in private evidence.
2. Observe and focus its window through the public CLI. Put the caret inside a
   known line, then reobserve before each input. Check LEFT/RIGHT, HOME/END,
   CTRL+HOME/END and SHIFT+HOME/END against both caret/selection state and the
   toolkit's key-event log. Check Ctrl+Shift selection and F11 event delivery.
3. Check `--keys 'HOME,SHIFT+END'` selects the same line. Test F11 separately
   when it changes fullscreen or geometry; a batch cannot wait for that change.
4. Check stale identities, changed geometry, another foreground window and
   human override reject further input. Releasing a physically held modifier
   must be an operator action, never synthetic cleanup of unrelated input.
5. Distinguish key-event delivery from frame-polled game state: immediate
   down/up taps need not span a game frame. Qualify
   [bounded keyboard holds](../workstations/windows-held-input.md) separately.

## Isolated sessions: initial implementation

[`--test-session`](../workstations/windows-test-sessions.md) now adds disposable
profiles, token-based readiness, exact-project admission and retained provenance,
logs and artifacts to supervised exec/node/run tasks. `develop ready` waits for
live readiness; normal wait/cancel confirm completion. Cleanup uncertainty keeps
the reservation and remaining profile. Revision/dirty state, Windows version and
selected executable/file versions are recorded without environment/arguments.
The bounded [environment report](../workstations/windows-environment.md) records
GPU/driver and display geometry, mode refresh and API scaling. DPI/HDR and
automatic captures/recordings remain deferred. Project
adapters must prove profile use and perform their own readiness assertions.

The broader session design and acceptance targets remain:

Build on the existing guardian, Job Object and terminal cleanup receipt rather
than creating a second process supervisor. A session should reserve its project
before build/start and retain the reservation through confirmed client cleanup.
Conflicting builds/runs must fail with an inspectable owner; no process-name
killing, forced rebuild over a live executable, or silent restart.

Create a new private session directory containing a disposable profile and a
separate retained evidence directory. Pass the profile path explicitly to a
project-owned command: changing APPDATA alone is not proof of isolation.
Readiness must be a bounded, project-defined observation tied to the contained
process, with distinct launch, ready, failed, cancelled and cleanup-uncertain
states. A launched PID or surviving window is insufficient proof of readiness.
Do not automatically execute newly discovered project manifests.

The evidence index should record UTC and monotonic event times, Basaltwater and
project revisions/dirty flags, selected runtime/toolchain, Windows version,
GPU/driver, display geometry/DPI/refresh/HDR availability, task IDs, bounded
logs with loss accounting, captures and the final cleanup receipt. Missing
metadata must say unavailable, never imply a default. Keep raw operational data
outside Git. Cancellation, readiness timeout and failed capture must retain
evidence and settle owned children before profile deletion; uncertain cleanup
must retain the profile and block maintenance. Evidence survives profile removal.

Acceptance requires temporary-directory/mock tests for conflicts, readiness
failure, stale PID identities, cancellation, early parent exit, guardian loss,
evidence failure and cleanup failure, then native Scheduler qualification with
an unrelated client surviving. Prove the consuming game's saves stay untouched.

## Subsequent increments

Native [task diagnostics](../workstations/windows-diagnostics.md) now retain
actual executable identity/exit evidence and offer bounded owned-root resource
snapshots before cancellation. This does not yet provide crash dumps or hang
stack traces.

Priority 1 now has opt-in `--track-children` on exec/node/run, with bounded
`diagnose --tree` and exact retained `--member PID:FILETIME` selection. The
guardian samples owned identities and retains observed unsigned exit codes
through worker replacement. Coverage explicitly remains partial for missed
short-lived members, tracking overflow and unavailable observations.

Priority 2 adds finite keyboard `input hold` leases with optional explicit
repeat, QPC send/lateness records and an independent Scheduled Task release
watchdog. Focus/control/identity changes stop input; physical ownership and
uncertain release remain explicit. Mouse holds and virtual pads are separate.

### Delivery order from consuming-game testing

Use the following order for new scoped increments. These are proposed
capabilities; priorities 1–2 are implemented as described above. Retain per-platform
capability discovery and explicit unsupported results; Linux and Windows need
equivalent evidence contracts, not identical implementation mechanisms.

| Priority | Capability | Additional game behavior it enables |
| --- | --- | --- |
| 1 | Owned-child identity and diagnostic selection | Attribute CPU, memory, handles and exit evidence to the game worker behind a Node/npm adapter, including replacement workers. |
| 2 | Leased held input on Windows | Exercise continuous keyboard movement, held attacks, key repeat and focus-loss release through the real OS input path. |
| 3 | Timestamped GPU-window capture and project event journal | Compare rendered game markers with captured desktop frames across scene changes, resize and fullscreen, without requiring an engine screenshot hook. |
| 4 | Process-scoped frame-pacing traces | Correlate cold stage boundaries and focus/fullscreen events with observed presentation timing. |
| 5 | Process audio and operator-assisted recovery checkpoints | Detect silence/discontinuities and correlate physical controller/audio-device recovery with game observations. |

**Owned children:** keep the existing task guardian and Job Object. Resolve
targets only inside that task, validate account/session, creation identity and
membership on every sample, and retain parent/child identities through worker
replacement. Refuse stale, escaped and unrelated targets. Distinguish native
root, selected child and bounded tree samples, including partial/unavailable
coverage. This directly addresses wrappers whose root remains idle while the
game is busy. Acceptance needs a real wrapped fixture, child replacement, PID
reuse rejection, cancellation and terminal retained exit evidence. Do not hold
query handles in a way that prolongs the kill-on-close Job's lifetime.

**Held input:** bring the useful bounded hold behavior of the
[CachyOS native adapter](../workstations/cachyos-desktop.md) to Windows, with
monotonic scheduled state changes and actual send-time/lateness records. Each
request owns a finite lease and only its synthetic pressed keys/buttons.
Foreground/identity/geometry loss, human override, cancellation and watchdog
expiry must stop the sequence and release owned state. Do not release unrelated
physical input or replay after partial delivery. Qualify both SDL events and
frame-polled held state across multiple application ticks; immediate down/up
events alone are insufficient. Keep deterministic game-frame timelines in the
project protocol. Virtual-pad support remains a separate
[controller-adapter increment](game-testing.md), not proof of physical HID,
GameInput/XInput, USB or reconnect behavior.

Bundle an optional fresh post-action observation/capture with the receipt to
reduce control round trips, as the native Linux adapter already allows. Keep
queue acceptance separate from the project's outcome assertions. Measure
end-to-end latency before choosing a persistent worker, and retain lease expiry
and control checks if a worker is reused; cached references must not bypass
freshness or geometry checks.

**Shared evidence:** align capture, input, diagnostics and optional project
phase/frame markers using explicit clock domains, UTC/QPC anchors, sequence
numbers and loss accounting. Accept project events only through an opt-in data
contract; do not execute discovered commands or interpret game-specific state
inside Basaltwater. Preserve output/presentation dimensions separately from
internal render size and scale. A project screenshot does not observe compositor
delivery, and a captured desktop frame does not prove physical scanout.

The small enabling documentation increment is a project-neutral Node session
adapter example: explicitly apply the supplied profile to children, publish
readiness after project checks, use file URLs for Windows preloads, forward exit
failure and close all children. Demonstrate with a disposable fixture and keep
project commands usable without Basaltwater. The adapter contract must explain
which paths survive a project-owned worker/profile reset.

### Capture, timing and recovery acceptance

- **GPU-window capture:** add an explicit Windows Graphics Capture backend,
  preserving PrintWindow as a named alternative. Record client-area geometry,
  frame timestamp, pixel/color format, dimensions and dropped-frame accounting.
  Bound duration, frame count, memory and disk usage; handle resize, close,
  minimize, unsupported capture and HDR explicitly. Qualify actual GPU content
  and short video synchronization. Microsoft's [screen capture guide](https://learn.microsoft.com/en-us/windows/apps/develop/media-authoring-processing/screen-capture)
  describes frame acquisition and capability checks; current PNG creation is
  not evidence of GPU capture support.
- **Frame pacing:** opt-in, versioned PresentMon/ETW collection tied to the
  session's process identity. Keep raw traces plus reproducible hitch summaries
  and timestamped project stage, fullscreen and focus annotations. Record event
  loss and unavailable metrics. PresentMon documents [OpenGL/Vulkan limitations](https://github.com/GameTechDev/PresentMon#analyzing-opengl-and-vulkan-applications);
  do not infer render/present latency from unsupported metrics or confuse
  tracing failure with a smooth run. Confirm privileges and overhead separately.
- **Display/device recovery:** snapshot DPI/refresh/HDR, then exercise only
  owned-window resize, monitor movement, minimize/restore and project-owned
  fullscreen transitions. Controller/audio disconnects are operator-assisted
  checkpoints with timestamped device events and project recovery observations.
  Do not change workstation-wide display settings automatically.
- **Process audio:** capture the owned game process tree, with bounded WAV data,
  silence/peak summaries, endpoint events and explicit format/timing metadata.
  Microsoft's [process loopback sample](https://learn.microsoft.com/en-us/samples/microsoft/windows-classic-samples/applicationloopbackaudio-sample/)
  demonstrates process-tree inclusion independently of an endpoint. Unsupported
  systems must fail explicitly rather than fall back to all-system audio.
  Qualify an unrelated audible app remaining absent from the captured output.

Each increment needs failure/cleanup coverage and native qualification before
being advertised as available. Host doctor checks remain discovery, separate
from game input, rendering, pacing and audible recovery assertions.
