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

## Disposable-app limits

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
That fixture did not establish game acceptance, consuming-project changes or
other-platform acceptance. The subsequent pilot below has its own narrower scope.

## Owner-authorized Steam-game pilot

Later on 2026-11-05, an explicitly authorized exploratory session exercised a
Steam-installed game through the same native helper. The controller checkout was
clean `3e912e4e`; the installed source and running worker remained at the tested
`744205cc` runtime, with only test/documentation differences. Game identity,
installed build, run brief, process identities, profiles and captures remain in
the private run record. This was one controller's capability pilot, with separate
tutorial and easy bot-match checkpoints, not a full-match or model comparison.

Normal player controls verified menu navigation, keyboard continuation, card
hover, four purchases and four hand-to-board drags. The two bot-match purchases
each cost three: visible gold changed from six to three, then three to zero.
The intended cards appeared in hand and on the board; an on-play stat increase
was also visible. Tutorial combat returned to the shop with health changing
from 25 to 23. After the bot match's End Turn, a later capture showed the next
shop at 25 health and seven gold. No victory or complete match outcome is claimed.

A private two-field profile identified the tutorial match-menu heading and
Cancel button. Literal open/closed calibration regions, template hashes and
thresholds (0.015 maximum error, 0.05 ambiguity margin) were frozen before two
prospective captures checked the same episode's open and closed states. Through
`NativeController`, a one-click conditional plan dismissed that menu and verified
both fields changed to closed, preserving intent, completed delivery and effect
receipts. The subsequent frame was visually reviewed. A capture-only event wait
then returned `predicate-satisfied` on the already-closed state with no input.
This checks one menu/profile pair; those same-episode captures are not an
independent held-out split by complete game run, and the wait does not qualify
recognition of asynchronous combat transitions. Purchases and drags above were
controller-directed, not automatic strategy or conditional-plan actions.

Initial post-click images sometimes still showed the preceding scene. Later
observations established the transition without repeating the input. On leaving
the bot match, the main menu's saving indicator persisted for over 70 seconds
while background animation continued. Save completion remained unknown; no
Basaltwater defect was established by that observation. Normal Quit closed the
game. The original game PID was absent and an executable-path process inventory
found no remaining game instance; the Steam launcher's earlier exit alone was
not used as proof. Shared Steam and the enabled helper were preserved, with no
pending helper-injected presses.

The private recorder finalized with 91 events and no truncation. Byte verification
passed for all 54 recorded PNGs, with no missing, mismatched or unverified media.
That integrity result does not establish image meaning or save persistence.
Real-profile held-out acceptance, full-match outcome/strategy evaluation, save
persistence, interruption/takeover/release behavior and other display layouts or
platforms remain separate checks. No consuming repository was changed.

## Client-disconnect and widget-focus qualification

A further authorized disposable-app run on 2026-11-05 used controller checkout
`3e012e40` and the same `744205cc` running helper. The native Wayland Qt probe
logged its own press/release, auto-repeat and focus events with monotonic times.
It observed no other application's input. Fresh monitor captures supplied
single-use observations for each input; captured coordinates were mapped to
the current monitor before the baseline click. Control state, permission,
generation and display layout were preserved throughout.

For each disconnect case, a separate installed CLI client sent one operation.
After the probe logged its first press, a read-only helper status was saved and
only that client was terminated with SIGTERM. The probe and helper stayed alive.
Each case ran once; no interrupted input was replayed.

| Boundary | Observed result |
| --- | --- |
| Symbolic Shift+A, requested five-second hold | App received both presses, then reverse-order releases within one second of the first press; helper status went from two pending symbolic presses with Shift to zero |
| Physical evdev Shift+A, requested five-second hold | Same application press/release pairing; helper status went from two pending physical presses with Shift to zero |
| Left pointer button, requested five-second hold | App received one press and one release within one second of the press |
| Left-button drag, requested five-second duration | App received held motion followed by release before the endpoint; no completion or placement effect was claimed |
| Paced 60-character synthetic text | Three press/release pairs arrived before cancellation; no additional app events during the following half-second observation |
| Widget focus change during a one-second Shift+A hold | Presses reached the original widget; repeats and final releases reached the second widget; helper reported completed input |
| Recovery after interrupted operations and focus change | A fresh plain A had no modifiers and a fresh click had one press/release pair |

The widget-focus case demonstrates delivery following the application's current
focus, not automatic cancellation or a compositor focus guard. Consumers must
handle focus loss in their own pressed-state bookkeeping; successful helper
release does not prove the original widget received a release. Cross-application
focus loss, physical keyboard state, other input providers, human takeover,
session expiry, helper termination and portal/bus loss were not tested here.
Pointer release was established by app events, not keyboard-only helper counts.

The recorder finalized with 24 events and no truncation. All 12 recorded PNGs
passed byte verification, but verification correctly returned nonzero and
`healthy:false` for five intentionally uncertain input events. Their receipts
retained `input-dispatch` / `input_delivery:unknown` and client-disconnection
details. Independent app evidence established releases without rewriting those
receipts or promoting interrupted operations to completed delivery/effect.

The probe exited normally with return code zero and its PID disappeared. The
helper remained enabled with no pending injected keys or named modifiers and no
reported backend failure. Probe source, event log, captures, during/after status
and interrupted-client results remain private outside Git. No game was launched
and no Basaltwater runtime defect was established by this run. See the
[repeatable release-check procedure](../workstations/native-input-qa.md#client-disconnect-release-checks).
