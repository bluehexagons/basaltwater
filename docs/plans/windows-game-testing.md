# Windows game testing facilities

Status: input fidelity and an initial opt-in test-session facility are implemented;
remaining facilities are planned. Basaltwater owns OS facilities and lifecycle/evidence collection.
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
input and partial submission. Native delivery in SDL and WinForms remains an
acceptance check after refresh:

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
   down/up taps need not span a game frame. Held input remains out of scope.

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
