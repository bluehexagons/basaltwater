# Native workstation feedback review, 2026-11-02

Feedback describes selected Windows and CachyOS application runs, sometimes from
older checkouts. It does not establish installed behavior after a source change.
Raw application/state receipts remain private. This review does not refresh,
reboot, install language engines or change physical control/routing preferences.

Scope correction: the owner subsequently identified Windows feedback as an
accidental inclusion. Further implementation here focuses on CachyOS. Existing
Windows commits `d6f3f191` and `ae5fe136` are retained for the separate Windows
agent to review/qualify; Windows rows below are handoff items, not this
workstream's remaining implementation scope.

## Changes and existing coverage

- Windows raw editing keys now use mapped scan codes and extended-key flags;
  Home/End/PageUp/PageDown/F11 and bounded CTRL/SHIFT chords are available.
  Submission is one batch; partial failure attempts reverse-order release and
  reports unconfirmed release separately. Existing identity, generation,
  foreground, physical-modifier and human-control guards remain. Leaf input
  help lists keys/text limits. Queue acceptance still requires app verification.
- Native compact captures already expose age/remaining validity and recovery
  guidance (`59ce468b`); expired input still fails. Inspect refreshed scenes
  before reusing coordinates. No freshness timeout was relaxed.
- [Windows task guidance](../workstations/windows-desktop.md#supervised-project-tasks)
  now records the initial ID/receipt and provenance before waiting, reattaches
  to the same job and retains final cleanup/log/truncation evidence.
  [Optional Ninja analysis](../workstations/windows-desktop.md#optional-ninja-analysis-build)
  documents a separate task-local compile-database build and SDK validation.
  This recipe has not yet been tested natively.
- [Native input QA](../workstations/native-input-qa.md) defines multilingual
  environment records, OS composition/clipboard/device checks, application key
  verification, private derivative provenance, observer packets and closeout.
  It also documents read-only timezone-data diagnosis. The separate reported
  regulatory-domain failure did not reproduce on the inspected workstation.

## Follow-up priorities and acceptance gates

| Priority | Work remaining | Acceptance before advertising support |
| --- | --- | --- |
| First | Native Windows scan-code qualification | Run offline desktop/CLI contracts and full native suite; disposable SDL and WinForms fixtures verify navigation, selection, F11, modifier release, partial-send/release failure, foreground change and physical takeover. Include ordinary and non-BMP/spaced paths; test long paths without changing policy. |
| Implemented, scoped | Consolidated installed/worker/source/skill provenance | [Manifest context](../workstations/cachyos-provenance.md) compares invoked/managed source and worker startup evidence with conservative drift flags and delivered skill bundle digests. Existing delivery inventories do not record commit revisions; those and session-loaded revisions explicitly remain unknown. |
| Implemented, scoped | Read-only input environment report | Doctor includes KDE layout snapshots and already-running Fcitx current-group metadata without service activation. IBus ownership is discovery only; other engine/framework routes remain unknown. Physical composition still needs manual qualification. |
| Next | Expected native foreground identity | Use a trustworthy compositor/backend identity immediately before injection; reject changed focus. AT-SPI inventory alone is insufficient on Wayland. Preserve generation/age/control guards. |
| Implemented, scoped | Evidence crop/excerpt/report exporter | [Private QA tools](../workstations/native-qa-evidence.md) supply literal PNG crops, bounded WAV excerpts, captioned Markdown, source hashes and explicit reviewed portable export. Video clip trimming remains future work. |
| Later | Tool/model/phase telemetry and specialist packets | Monotonic tool durations and captured timestamps; distinguish handoff overhead; actual model/effort/usage only when exposed. Absolute paths, read acknowledgements, immutable inputs, advice deadlines and exclusive controller ownership. |
| Later | Virtual gamepad adapter | Opt-in owned device, bounded buttons/axes, control-state integration, neutralization/watchdog recovery and verified removal. Qualify in an event inspector before a game. Prerequisite discovery is not qualification. |
| Implemented, scoped | Application audio and analysis | Protocol-verified native PipeWire stereo capture adds only owned recording links, checks existing playback, and records bounded format/timing/cleanup receipts. Offline FFmpeg analysis supplies peaks/loudness/silence, level windows and suspect excerpts. PulseAudio bridges, actual game capture and physical audibility remain unqualified. OpenGL probes remain future work. |
| Implemented, scoped | Accessibility events and shared timeline | PID/start-identity scoped AT-SPI recording, common Linux boot/monotonic clocks, conservative timeline joins, and an optional isolated Orca qualification recipe. No automatic speech success or sample-accurate audio alignment claim. |
| Later | Automated run closeout | Track owned tasks/launches/recorders/workers and retained artifacts; report verified exits and uncertain resources, preserving shared services and indexed evidence. Manual procedure is available now. |

The Windows changes have added offline test cases for scan/extended flags,
chord order, malformed chords, unmapped keys, partial submission and release
failure. They were not executed on this CachyOS host, which has no Windows
PowerShell/.NET Framework test runtime. The desktop Python suite passed 675
tests; that is Linux/offline evidence, not an SDL or WinForms pass.
Refresh/reboot, restored desktop grants, physical input/audio, IME composition,
clean-target packages and streaming remain separate acceptance work.

The subsequent CachyOS input-report change passed 48 focused tests and a live
read-only KDE layout query. The wider agent/desktop run initially exposed two
fixtures inheriting the host's `XDG_CONFIG_HOME`; those fixtures now use temporary
configuration roots. The corrected combined run passed all 2,808 tests. Fcitx
engine/group responses have mocked contract coverage; no framework was started
to claim a live IME pass.

The subsequent QA batch passed 2,998 agent/desktop and related CLI/configuration
tests plus the static checks. Synthetic clean/clipped/silent WAV analysis,
two-second native PipeWire capture to an owned receiver, exact PNG crop bytes,
reviewed portable export, and a six-event GTK name-change recording passed
locally. The capture retained existing playback links and confirmed owned-node
removal. A joined audio/accessibility timeline retained nine observed events;
audio sample-zero alignment remained explicitly unknown. Review regressions
cover failed partial accessibility timelines and recorder teardown reconciliation.
Orca, Speech Dispatcher and the speech engine were absent; no packages, reader,
desktop preferences, normal routing or refresh were changed. The optional Orca
recipe is a qualification workflow, not installed or spoken-output support.
