# Prepare and qualify a game test

[Topic index](game-testing.md)

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

## Expect experiments during learning

In a declared learning phase, expect the controller to exercise a priority unknown
mechanic at a safe opportunity. The brief names a question, predicted visible
effect, expendable resources, maximum incremental spend/attempts and protected
assets. A small learning cost can be authorized without proving immediate strategic
benefit. Preservation of saves, accounts and shared services does not prohibit
ordinary roster changes or in-game spending inside that budget.

Record question, current state, prediction, bounded action, observed effect and
next adjustment in one compact entry. Separate planned, attempted without effect,
verified and blocked. After two misses, diagnose before repeating; a changed
hypothesis may justify another bounded attempt. Continue useful ordinary play when
the mechanic remains blocked. Never replay unknown delivery or manufacture a new
match for coverage. A win is not required for useful learning, and a written
rationale alone does not demonstrate a control.

Owner-authorized hints can amend an active learning match. Retain the original
packet/history and record actual advice processing time, coach model/prior context,
experiment budget, intervention limit, cooldown, reply validity and fallback.
Label play assisted from that boundary; sending a message does not establish when
it was read. Keep one desktop controller, original clocks and Start allowance.
Coaches read immutable artifacts; advice must be checked against fresh state.
Evaluation retains its declared assistance and performance conditions. A single
match before/after hints does not establish their causal benefit.

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
   image-local targets with [the coordinate aid](game-testing-controls.md#map-a-presented-image).
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
