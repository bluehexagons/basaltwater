# CachyOS conditional execution qualification

On 2026-11-05, after an owner-selected system refresh, bounded disposable-app
checks exercised the delivered native adapter on CachyOS KDE Wayland. Installed
CLI, managed source and newly started worker provenance matched clean commit
`744205cc6f1f718276a518309f5a023596e4beb4`. The existing enabled control state and
remembered portal grant were preserved. A sleeping display was woken before
capture; startup restored access and a fresh capture confirmed availability.
This is application qualification for one fixed profile and monitor geometry,
separate from [offline acceptance](../plans/game-testing-handoffs/acceptance.md)
and game acceptance.

## Fixture and observations

A task-owned Qt application painted a red/blue clickable panel, a separate
green anchor and an independently visible click counter. Its mouse-release
handler wrote a private event log. Fixture setup changed scenes separately
from mouse events, so setup commands could not inflate the input count.
Monitor captures used a bounded crop with two 64-by-64 fixed regions. Templates
came from calibration captures; the profile and template hashes were frozen
before collecting separate held-out runs. Error and ambiguity thresholds were
0.01 and 0.1 respectively.

Three held-out captures covered red, blue and an occluded scene panel. Both
fields had zero false-confident predictions; scene abstained on occlusion and
the anchor remained recognized. This tiny fixture establishes those cases only,
not a general recognition accuracy or latency threshold.

| Boundary | Observed result |
| --- | --- |
| One click through the installed conditional CLI | Durable intent, completed helper delivery, subsequent red-to-blue capture and verified effect; independent counter advanced once |
| Two composed steps through the native Python adapter | Both transitions verified, with distinct fresh observations before each dispatch; counter advanced twice |
| Occlusion before the first action | Unknown scene returned control without a dispatch or mouse event |
| Occlusion between steps | First effect verified; next capture abstained and refused the second action; exactly one mouse event |
| Observation superseded before dispatch | Real helper refused the old token with `not-sent`; counter unchanged |
| Post-capture failure injected into the adapter | Completed real input retained with unknown effect and issue receipt; counter advanced once; no replay |
| Capture-only frame limit | Four captures, three ring evictions, one retained frame/metadata pair; no dispatch or mouse event |
| Capture-only predicate wait | Scene change from fixture setup satisfied the predicate; earlier frame evicted; no input |
| Capture-only deadline | Returned control with retained evidence and explicit deadline; no input or game-loss assertion |

Wait receipts included measured start-to-start gaps, including capture latency.
Configured cadence remained the delay between captures.
Five mouse events across the input cases matched the fixture's final visible
counter and event log. Captures, profile/templates, pinned record bundles,
held-out report, plans, stage receipts, logs and fixture source remain private
outside Git. The disposable application exited successfully after qualification;
the running helper and enabled control state were left available.

## Remaining boundaries

The adapter failure above was deliberately injected; it does not qualify real
transport interruption or compositor capture loss. Human takeover, expiry during
input holds, physical input release under interruption, app movement/resize,
different display scaling/monitor layouts, animation/focus changes and real
game semantics remain unqualified. Mocked acceptance covers guard and failure
contracts; it does not establish those desktop behaviors. Run the
[native recipe](../workstations/conditional-execution.md#disposable-application-qualification-after-refresh)
for each additional boundary and retain its own evidence.

Consuming projects still own labelled real-profile held-out data, risk
thresholds, adapters, strategy, outcome mapping and paired adoption revisions.
No game trial, consuming-project change or other-platform acceptance is claimed.
