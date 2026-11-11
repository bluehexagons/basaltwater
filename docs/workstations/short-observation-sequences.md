# One inspected input and two delayed observations

Use this Linux recipe for a brief, already-inspected transition when reasoning
between tool calls would miss useful frames. It sends one input and attempts two
captures, stops on the first failure and never retries input. It is not continuous
video or a guarantee of catching a combat transition. Project scripts own the
capture delays and interpretation. Keep one desktop controller.

## Inspect, then run once

On CachyOS, capture the consent-selected monitor and retain its JSON receipt:

```bash
basaltw desktop --native screenshot --compact > /absolute/private/frame.json
```

Read the receipt and inspect the PNG named by `output`. Confirm the application,
focus and target. Replace the geometry, observation ID, coordinates and generation
below with that inspected frame's values. Mapping a resized view uses
`map-point --capture frame.json`; do not combine it with `--geometry` or `--crop`.
Mapping takes a JSON receipt, not the PNG. Do not use an expired/consumed token.

Save this JSON as a private `observe-pair.json` outside Git:

```json
[
  {"action": "input", "kind": "click", "geometry": [2400, 1600],
   "observation_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "x": 400, "y": 200, "button": 1},
  {"action": "screenshot", "after_ms": 150, "next_frame": true},
  {"action": "screenshot", "after_ms": 400, "next_frame": true}
]
```

Omitting `output` generates a different private PNG path for each capture. An
explicit `output` must be a new absolute `.png` path under an existing writable
parent. All capture paths and delay options are checked before input; checks do
not reserve paths, so a later filesystem change can still cause partial failure.
Run once, promptly after inspection:

```bash
basaltw desktop --native sequence /absolute/private/observe-pair.json --generation GEN --save-output
```

The returned `artifact` names a private JSON result, not an image or directory.
Read it even on nonzero exit. `results[0]` retains the input receipt; later results
contain compact capture receipts with PNG `output` paths. `completed` counts only
successful actions; `failed_action_index` is zero-based and `failed_action` retains
the failure receipt. If the second capture fails, the completed input and first
capture remain in `results`. A failed sequence is never a reason to replay its
input. Inspect retained evidence, then capture again separately if permitted.
Unknown input delivery remains unknown; an acknowledged input does not prove
its application effect. `--save-output` preserves the full result before emitting
a short preview, including the latest recorder headroom and any recording error.
Omit the flag for direct JSON; sequences have no `--json` flag.

`after_ms` waits **after the preceding action's response**, not at a fixed offset
from input or compositor presentation. Each delay is 0–5000 ms, with at most
5000 ms across the sequence, within the existing 20-second sequence deadline.
Initial and per-action status requests share that controller deadline, as do
capture socket reads on both Linux backends. A timeout does not prove that an
in-flight helper capture stopped; inspect its status and output before recapture.
Capture acquisition and status checks add time. `timings` retains requested and
actual delay, action start/request/finish monotonic times and elapsed milliseconds,
including the failed action. Retain these alongside helper input/frame timestamps;
stream PTS and controller receipt times are not input-to-photon measurements.
The client rechecks generation, permission and control revision after each delay;
human intervention stops subsequent steps, even if agents are enabled again.

On Debian XFCE/XRDP omit `--native` and remove both `next_frame` fields. Captures
use scrot and carry no claim about compositor frame freshness. Explicit Debian
paths cannot contain `%`, `$` or backslash; these are rejected before input.
The same bounded delays, completed-prefix receipts and no-replay rule apply.

Native Windows has a separate `desktop input sequence` keyboard contract; it does
not accept this Linux JSON recipe. A project-owned supervised PowerShell script
can issue one inspected, observation-bound Windows input and two screenshots,
with a `Stopwatch`, bounded delays, exit checks and retained receipts for each
command. Abort on input uncertainty or human pause; do not retry input in a catch
block. Windows post-input receipt/stage parity still requires separate work.
See [Windows observation selectors](desktop-observations.md#capture-and-bind-one-action).

## Disposable native qualification

The [observation probe](../../scripts/desktop_observation_probe.py) is a small
cross-platform Tk application. Run with an existing Python that has Tk; it installs
nothing. It logs only its synthetic button/phase events to a new private JSONL
file and never collects global keys. In a permitted desktop session, launch:

```bash
python3 scripts/desktop_observation_probe.py --log /absolute/private/probe.jsonl
```

For small-control targeting qualification, add `--small-controls`. This places
adjacent **Advance** and **Decoy** buttons below the phase display. Advance keeps
the same one-shot transition and `button` event. Decoy changes the visible label
and logs `decoy`, without advancing phases or creating a blocked output. Use
fresh independent probes for reduced-view and crop tests. Retain any decoy event
as a visible miss, then reinspect and explain the corrected mapping before
recovery. Button sizes depend on desktop theme/scaling; inspect actual geometry.

Inspect a fresh capture, target **Advance once** (or **Advance** in small-control
mode), and use the recipe above.
The probe shows an immediate amber phase, a blue phase after 300 ms and a green
phase after 1200 ms. Compare PNGs, probe logs and actual receipt timings; slow
capture may miss a phase. Reinspect before a second independent input.

For a deliberate partial-capture failure, start a fresh probe with
`--block-output /absolute/private/blocked.png`, and set only the second screenshot's
`output` to that new path. On the first button event the probe creates that file,
after the sequence's preflight but before capture. The first PNG and completed
input must survive the second capture's refusal. The probe log must show exactly
one button event. Preserve the blocked fixture and failed receipt; do not rerun
the input to obtain a clean record.

During recording, follow the complete [rotation procedure](desktop-evaluation.md#rotate-a-recording-at-a-safe-checkpoint).
Opening [the human control window](desktop-control.md#handoff-is-not-a-permission-change)
must leave permission unchanged, including while blocked. A human-selected override
must invalidate subsequent sequence work. Never simulate human approval in a real
account merely to qualify a recipe. Close only the disposable probe normally;
preserve unrelated windows, the desktop and recorder evidence.

## Development qualification, 2026-11-11

A disposable Debian 13 Xvfb/xfwm4 session exercised the real Tk probe, xdotool
input, scrot capture and the real local Unix-socket request client. Only the
desktop declaration and private account-state paths were adapted to the fixture;
no shared desktop, real account permission or system installation was changed.

- One inspected click completed with two PNGs showing the immediate and
  transition phases. Requested 150/400 ms delays recorded about 150/400 ms;
  capture/status work added further time, retained in the receipt.
- The deliberate second-capture path collision returned nonzero with the input
  receipt and first PNG intact. Each independent probe logged exactly one click.
  Verification retained the failed capture as image-less and unhealthy.
- The real Recorder was connected to that fixture's request adapter. Real
  process-status observations brought the first segment to 450 events, exposing
  50 remaining and a rotation warning. Its three PNGs verified, and the finished
  segment and its successor linked in the same original run ledger. This tested
  Recorder/ledger behavior, not the KDE helper's live recording hook.
- The real Debian Tk handoff window opened with isolated fixture permission
  disabled without changing it. A fixture UI takeover then blocked sequence
  input as `not-sent`; closing the window preserved human override. Follow-up
  review exercised the CLI's dedicated handoff RPC while disabled and under
  human override. Its UI child used the same isolated declaration/account
  bootstrap; opening and normal close preserved permission. X11 window readiness
  did not guarantee that every captured frame showed a painted window.

Native KDE routing, guard changes, old-helper refusal and parser examples have
offline regression coverage. Live KDE portal/PipeWire timing and native Windows
remain unqualified. The Debian fixture lacks the GTK 3 typelib for the native KDE
control window; the Debian handoff observation does not qualify that UI. CachyOS-only
task finalization refused the Debian host as designed; the frozen first
segment remains `finalized: false`, with its verification and original evidence
retained. This exercise is not an XRDP login/reconnect or physical-display test.
