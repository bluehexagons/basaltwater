# Agent-assisted game testing

Use this procedure for manual playtests through Basaltwater, starting with
the [CachyOS native desktop](cachyos-desktop.md). The game project owns its
rules, saves, acceptance criteria and testing brief. Basaltwater supplies
capture/input and private evidence collection. Installation or a previous
successful match does not establish readiness for the next run.

## Prepare a bounded brief

Record these fields in the game project's private task tracker before play:

| Field | Required decision |
| --- | --- |
| Identity | Run/match ID, game build, Basaltwater revision and dirty state, platform, controller, model and effort when applicable |
| Purpose | Gameplay feedback, platform qualification, or model comparison; define the observations needed for that purpose |
| Completion | Natural completion unit, such as one match through its result screen; specify checkpoints and terminal evidence |
| Budget | Separate setup/qualification allowance, play allowance and hard wall-clock cap; define who may extend the same match |
| Waits | Game-specific transition waits, combat observation cadence, maximum time without an observation and stuck-state threshold |
| Interaction | Allowed player controls, disposable save/profile, reversible qualification action, and permitted recovery |
| Evidence | Private project path, recorder/artifact ownership, checkpoint location, and reporting destination |
| Handoff | One active desktop controller, transfer point, current objective and strategy/history that may be shared |

Choose waits and budgets from the game's expected round/match duration and
observed tool/reasoning overhead. State actual values in each brief; a short
generic pilot cap is not a substitute for a completion unit. At a hard cap,
save the current state and report unfinished unless an authorized extension
arrives. An extension continues the same match ID and history.

## Refresh the baseline after changes

Begin a new game with discovery questions about its controls, configuration,
natural completion unit and timing. Mark estimates provisional; choose the next
bounded experiment from observations rather than requiring a win or full match
before the analysis is useful.

Before later runs, compare the recorded baseline with the current game build,
Basaltwater/backend, OS/display/input setup, model/effort/team and procedure.
Refresh affected assumptions only. For each changed premise, record old/new
versions or unknown, the source, checks needed and their state: reviewed,
live check pending, verified, or blocked. A documentation update is no live retest.

Retest a known finding under its original trigger when safe, then check nearby
normal behavior. Declare repetition/time limits first and report reproduction
as X/Y. If the trigger is unreachable, conditions differ or visual media is
missing, keep the result inconclusive. One quiet attempt does not establish an
intermittent fix. Preserve the original history; do not reset saves, downgrade
builds or mix changed conditions into an unlabeled comparison.

## Qualify the controller on fresh state

Check `basaltw desktop --native status --json` for the current helper and agent
permission. Follow the [native control reference](../../common/agent_skills/basaltwater-cachyos-desktop/references/native-control.md)
for consent, sleeping displays and recovery. Keep one controller at a time.
Start `evidence start --project /absolute/project --json` before qualification
when it belongs in the run record.

Each new worker, including one taking over a match, must:

1. Take and inspect a fresh capture. Identify the game, focus, mode/scene and
   visible cues supporting that scene. Mark uncertain/transitional scenes
   unknown and observe again before gameplay input.
2. Record full-monitor `geometry`, PNG `image_geometry` and `image_origin`,
   and the dimensions of the image actually presented to that worker. Map
   image-local targets with [the coordinate aid](#map-a-presented-image).
   A coordinator's old scale factor or exact target coordinates cannot qualify
   a new worker's independent targeting.
3. Select a bounded action allowed in the current scene. Predict its visible
   effect, execute it through normal player controls, then inspect fresh
   evidence of the effect. For a shop/board game, require the intended card,
   a matching gold decrease, the card entering hand, and then placement on the
   intended board slot. Use the project's equivalent checks for another game.
4. Have the coordinator independently review the scene, mapped target and
   resulting state. Record pass/fail, assistance supplied and evidence references.
   A corrected action can pass recovery while independent targeting remains
   unqualified. If qualification fails, correct and repeat before scoring play.

Repeat after monitor/layout changes or a targeting failure. A new capture is
required after a stale-observation rejection. Monitor/scaling/resolution changes
also require refreshing the portal session under the native control procedure.
Successful input delivery alone does not establish the intended game effect.

## Map a presented image

`map-point` performs a read-only calculation with explicit metadata. It needs
no running helper, sends no input and does not refresh a capture's age. For a
fictional 2400 × 1600 monitor image presented at 1920 × 1280:

```fish
basaltw desktop --native map-point --geometry 2400 1600 \
  --view-geometry 1920 1280 --x 800 --y 600 --json
```

The result reports per-axis scale `[1.25, 1.25]`, the image origin and dimensions,
and `input_args: "--x 1000 --y 750"`. Review that monitor position against the
intended target, then use those arguments with `input`, the original capture's
generation, and full-monitor geometry. Copy the returned arguments literally;
do not apply the scale again. The backend applies portal logical scaling later.

For a screenshot made with `--crop 100 200 800 600`, presented at 400 × 300,
map a drag within that image as follows:

```fish
basaltw desktop --native map-point --geometry 2400 1600 \
  --crop 100 200 800 600 --view-geometry 400 300 \
  --x 100 --y 100 --to-x 300 --to-y 200 --json
```

This returns `--x 300 --y 400 --to-x 700 --to-y 600`. Both endpoints must be
inside the presented image; the crop must be inside the monitor. Conversion
uses `origin + floor(view_point * image_size / view_size)` independently per
axis. Bounds and types are validated, including paired drag endpoints.

Use coordinates relative to the entire displayed PNG, excluding viewer borders
and letterboxing. The view dimensions describe that PNG's presented content,
not the chat pane or the viewer window. If dimensions are unknown or the viewer
has zoomed/clipped the image, obtain a known full view or take a bounded native
crop and inspect it at a known size. The aid cannot infer viewer transforms,
verify scene/focus, select a target or validate freshness. Image presentation
resizing and desktop logical scaling are separate transforms.

## Observe, act, verify during play

Before every shop action or other scene-dependent interaction, confirm current
scene cues, resources, target identity and expected effect from a fresh image.
Use bounded actions followed by observation; do not queue speculative gestures
across scene transitions. If evidence contradicts a worker's scene description,
correct the note and inspect current state before continuing.

`input --observe --settle-ms N` accepts **0–5000 ms** (default 120). For longer
combat or animation waits, use later `screenshot --next-frame --compact`
observations at the brief's cadence. Do not request an unsupported longer settle
or send meaningless input to spend time. A transitional first frame calls for
another observation at the declared cadence; repeated equal pixels alone do
not establish a frozen capture. Apply the stuck-state threshold before recovery.

Input rejects expired captures (older than 60 seconds), wrong geometry and
changed generations. Recapture and re-plan after rejection. When input completed
but its follow-up capture failed, retain the completion result and capture again;
never replay an uncertain purchase or placement blindly.

At a handoff, stop issuing actions and record the current match ID, objective,
last verified scene/resources/board, learned strategy, pending/uncertain inputs,
helper generation, evidence path and budget remaining. The receiver takes a
fresh capture and qualifies before acting. Historical status is context,
not live desktop authority. Use the [shared handoff conventions](../agents/handoffs.md).

## Give observers immutable packets

Use a sole controller plus a bounded observer/advisor when useful. The controller
alone captures and acts; helpers read saved artifacts and return conditional
advice. Follow active orchestration instructions for models, deadlines,
completion and cancellation. Separate simultaneous live matches need separate
qualified desktop resources.

Keep each packet outside Git with these fields:

```text
packet ID; private run/phase/match/session record; recorder artifact
capture path, UTC, artifact_sha256; scene/turn; recent action and observed result
monitor geometry; PNG size/origin; presentation size; coordinate convention
one question; expected deliverable; validity conditions; decision deadline
remaining shared budget; requested/actual model and effort; advice access policy
```

Verify that the actual worker can open the image and has visual input. An
absolute local path does not establish access from another environment. Preserve
originals and packet IDs; use separate crops/redactions as derivatives. Require
the reply to identify the packet, observations, uncertainty, coordinate space,
conditional recommendation and expiry. The controller rechecks current scene
and target after any reorder, purchase or transition; old advice can be stale
before the backend's 60-second limit. Disregard late optional advice.

Record material assistance, corrections and advice used/rejected in the private
session record linked to the recorder artifact. The recorder does not infer
run IDs, model settings or strategy assistance. Freeze these conditions for
team comparisons; quarantine optional advice until the outcome for solo tests.
All workers share the original clocks and attempt limits. Report elapsed wall
time once and aggregate worker time separately when available.

## Retain and verify evidence

Choose durable private storage outside Git for each run, with enough space and
a declared retention window. Record actual locations privately. `--save-output`
observations use temporary storage; copy needed JSON/media to durable storage,
verify bytes/hashes, reopen important captures and update the private index
before retiring scratch files. A temporary path is no durability guarantee.

New native captures include `artifact_sha256` for PNG file bytes as well as
`pixel_sha256` for visible RGBA pixels. Compact responses and the evidence
journal retain both. Check referenced originals before citing them and after
any reviewed storage consolidation:

```fish
basaltw desktop --native evidence verify /absolute/private/record.json --json
```

This command reads the existing journal and capture files without contacting
the desktop, finalizing, moving, deleting or repairing evidence. It reports
verified, missing, mismatched and unverified counts, with up to ten problem
details and an explicit flag when further problems are omitted. Unsafe or
oversized files, absent file hashes on older records, and uncertain captures
remain unverified. Truncated journals and records with no captures cannot return
a successful verification. A nonzero status calls for investigation, never
blind input replay. An active record can be checked; finalization is reported
separately. File equality does not verify image meaning, outcome, task logs,
omitted events or video/audio. Reopen images to check the claim they support.

Keep original paths usable when retaining recorder references. If evidence must
move, keep the original until a verified private relocation index explains the
new location; this verifier does not rewrite journals or follow a relocation
index. Hard-linked retained copies are immutable: crops and annotations must
be new files. Age alone is not disposal authority, and referenced media stays
retained while an unresolved finding needs it.

## Close out and report limits

Inspect the result screen or other declared terminal state independently of
worker notes. Finish the evidence recorder and inspect its finalization,
truncation, capture paths/hashes, task snapshots and errors. Finalization does
not stop applications or prove tasks completed. Preserve original artifacts
privately. Close task-owned game/test processes after saving the outcome unless
the brief requests continuation; verify exit through their launch/task handles.
Leave helper/agent control in the owner's existing state and record any active
task or pending input. Remove only disposable, unreferenced artifacts under the
project's retention policy; keep evidence needed to reproduce findings. Share
sanitized findings only.

Use a private closeout inventory for each owned launch, supervised task, helper
job, recorder, temporary setting/device and artifact group. Mark each as exited,
finalized, restored, retained with reason, or unresolved, with current evidence.
Recorder finalization snapshots are historical; recheck live handles when
closing processes. A launcher exiting does not prove its game child exited,
and an absent accessibility window does not prove process exit. Preserve shared
launchers and desktop services. Never use broad process-name kills for cleanup.
Record reviewed artifact dispositions, retained-copy verification and measured
storage reclaimed. If a close action may lose progress, preserve state and
report partial cleanup rather than silently force termination.

The journal caps events at 500 and task attachments at 50. Plan recording
boundaries at safe checkpoints for long sessions: finish and inspect the
existing recording, then start the next and link both to the same run/match
without resetting clocks. Retain any truncation as a coverage gap. Recording
boundaries do not close the game or authorize another attempt.

Separate intended action, observed input delivery, verified game effect,
targeting/scene mistakes, rejected calls and unresolved platform questions.
Attribute a backend defect only with reproducible evidence. Missing renderer,
desktop-scale, video/audio, latency, token or cost observations remain unknown.
Frame receipt and stream PTS do not measure input-to-photon or model latency.
Report actual stage timings only when measured; wall-clock duration includes
game waits, reasoning, tool and coordination overhead.

For model comparisons, require each worker's qualification and comparable game
build/state, rules, strategy assistance, waits, budgets and completion criteria.
Record model/effort from the active orchestration system. Targeting failures,
scene errors or unequal handoff assistance confound play quality: report the
run as exploratory and repeat qualification before drawing model rankings.
This procedure and the coordinate aid do not themselves qualify a game, a fresh
worker, another platform route, or any unmeasured telemetry.

## Controller capability boundaries

The native CLI currently provides keyboard, text, pointer and drag input.
Gamepad events are not implemented. Package/device presence, keyboard remapping
and a physical pad are separate conditions and do not qualify a virtual-pad
route. Record Steam Input or other remapping separately from game-native input.
The [game-testing capability plan](../plans/game-testing.md) defines the remaining
adapter, failure-path and game-recognition acceptance work. Do not use an
independent input script to bypass human override or disabled control.

## Coordinate aid verification scope

The coordinate calculation has offline contract coverage for resized full
images, resized crops, drag endpoints, rounding, bounds and invalid inputs.
CLI tests verify that mapping makes no backend status/input requests or
artifact allocations. Existing native freshness and control guards remain
covered by the desktop suite.

A native CachyOS KDE Wayland check on 2026-11-02 used a disposable test canvas:
a target selected from a resized full-monitor image received the mapped click,
and a cropped-image drag reached the intended destination. The canvas event
log and post-input image both confirmed one click and one completed drag.
The fixture exited successfully afterward. Raw evidence remains private.
This checks the aid with native input; fresh-worker game scene recognition
and purchase/placement qualification still require the next game's run brief.
