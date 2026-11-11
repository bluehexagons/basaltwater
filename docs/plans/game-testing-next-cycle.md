# Game-testing improvement project: next run cycle

**Status: recipe delivery and offline checks; native adoption pending, November 2026.**
The [current implementation and CachyOS handoff](game-testing-handoffs/next-cycle.md)
provides NC-01 targeting and NC-03 read/exposure recipes first, with continuation,
review, closeout and prospective trial checklists for the remaining items. These
reuse existing contracts; optional generic APIs remain deferred pending measured
need. Source/offline delivery does not establish native or game acceptance.
Finish the currently authorized run and its cleanup, then stop game testing.
Prepare tooling and documentation updates before selecting a fresh set of runs.
This document does not activate another game, desktop fixture, helper refresh or
evaluation phase.

The next cycle should reduce control mistakes, missed observations and reporting
overhead while making learning experiments useful and comparisons interpretable.
A completed match or verified PNG inventory alone does not establish those goals.

Use the existing [capability plan](game-testing.md),
[learning contracts and responsibilities](game-testing-learning.md) and
[implementation handoffs](game-testing-handoffs/README.md). The work below is an
incremental follow-up, not a replacement framework or a general game-playing agent.

## Baseline: reuse what already exists

| Available capability | Remaining boundary for this project |
| --- | --- |
| [Frame-bound mapping](../workstations/desktop-observations.md) with crop origins, viewer dimensions, margins and single-use tokens | The caller can still supply the wrong presented dimensions. Correct arithmetic cannot establish that the target was read from the right coordinate space. |
| [One-input/two-observation sequences](../workstations/short-observation-sequences.md), unique default PNG paths, shared deadlines and partial receipts | Offline tests and the documented disposable Debian development fixture do not qualify live KDE portal/PipeWire timing or a particular game's transitions. |
| [Recorder headroom and rotation guidance](../workstations/desktop-evaluation.md#recorder-inventory-and-closeout) | Updated source warnings are not proof that a long-running installed helper exposes them. Rotation is manual and has a gap. |
| Native handoff routing, actionable parser errors and [parser-tested recipes](../../tests/test_desktop_recipes.py) | A human control window does not transfer logical agent ownership or change permission. Fresh controllers still need to use the right route. |
| Instruction paging, private preflight, run checkpoints, evidence verification and closeout | Emitted bytes do not prove comprehension; a finished ledger cannot resume; file integrity does not validate visual claims or prose. |
| [Learning records](../workstations/learning-records.md), [corpus summaries](../workstations/learning-corpus.md), optional perception and conditional execution | Project adoption and each real profile need their own acceptance. These tools neither choose strategy nor establish a win. |

Do not reopen these as missing-feature requests. Start with current source and
installed/runtime capability inspection, then identify an actual gap. Preserve
the separate qualification labels: source implemented, offline checked, disposable
native fixture checked, and consuming-game checked.

## Priorities and ownership

| ID | Priority | Deliverable | Owner |
| --- | --- | --- | --- |
| NC-01 | Before fresh gameplay | A targeting recipe that survives image presentation changes | Basaltwater docs/fixtures; consuming project controller brief |
| NC-02 | Before fresh gameplay | Adopt and qualify the existing bounded observation path | Basaltwater platform qualification; consuming project timing policy |
| NC-03 | Before fresh gameplay | Compact, explicit required-read and exposure manifests | Consuming project first; optional generic Basaltwater contract |
| NC-04 | Before fresh comparisons | Linked continuation and attempt accounting | Basaltwater offline contracts; consuming project match ledger |
| NC-05 | Before fresh comparisons | Evidence-backed result review and coverage labels | Consuming project, using existing Basaltwater records |
| NC-06 | Next iteration | Long-session evidence and cleanup summaries | Basaltwater reporting/recipes; consuming project resource ownership |
| NC-07 | Next iteration | Useful learning trials and a frozen evaluation protocol | Consuming project; no generic strategy engine |
| NC-08 | Optional, measured need | Timing and provider-usage integration | Basaltwater receipts plus the owning orchestration/provider integration |

### NC-01: make coordinate assumptions visible

Recent controller recovery found completed clicks outside the intended controls:
positions read from a reduced full-monitor image had been sent as native pixels.
A small native crop, inspected and mapped with its origin, produced the intended
navigation effect. This supports an operator mapping explanation; it does not
establish a game freeze or an input-delivery defect.

Document one preferred route for small consequential controls: capture a bounded
native region containing the complete control, inspect the actual presented
image, and map through its saved receipt. If the outer viewer's dimensions are
unknown, obtain a view whose transform can be established. An image API's
“original detail” option is not evidence of one-to-one presentation. A maximum
crop width may be chosen by the consuming harness; it is not a universal guarantee.

Retain the presented dimensions, image dimensions/origin and mapped point in the
private decision evidence. If useful, add an offline diagnostic preview showing
the proposed point on a derivative of the exact source frame. Such a preview
would send no input, confer no new authority and renew no observation token.
Do not add a second coordinate system or infer an unknown viewer transform.

Acceptance: synthetic full-view scaling, crop origins, margins, independent-axis
scaling and drag endpoints still pass existing contracts. A fresh controller
then identifies and activates a disposable small control from both a reduced
view and a bounded crop. Include a case where the numeric point is valid but
targets the wrong control. After a visible miss, require fresh inspection and a
changed mapping explanation. A crop cannot authorize a drag beyond its bounds.

### NC-02: qualify the existing observation sequence

Adopt the published sequence and saved-result format instead of hand-building a
new host-language capture loop for each run. Its delays are relative to preceding
responses, its cumulative delay and total duration are bounded, and capture work
adds time. Do not translate it into guaranteed fixed offsets from game input.
Do not increase those bounds merely to fit a historical game recipe.

First qualify the exact refreshed CachyOS CLI/helper and installed skills with
the existing disposable probe. Check normal capture, partial failure, unique
paths, headroom reporting and control-change cancellation. Preserve completed
input when a later capture fails. Never simulate human approval in a real account
or weaken freshness to obtain a clean result. If a human takeover case is not
available, retain it as unqualified rather than manufacture permission.

The consuming project then chooses one bounded game trial. Record separate
observations for a combat scene, an attack/trigger, fight resolution, and match
terminal. A lineup can satisfy a scene check without explaining a fight. Missing
a result is not evidence that combat never appeared. Use ordinary inspected
follow-up when needed; another sampling mechanism needs a separate measured case.

Acceptance: actual input/frame/controller timestamps and gaps are retained;
each independent probe receives exactly one intended input; a failed second
capture leaves the first PNG and input receipt intact; uncertain delivery is
never replayed. State precisely which platform and transition were exercised.

### NC-03: shorten required context without skipping authority

Growing historical plans and task lists consume setup time and encourage either
premature desktop actions or incomplete reads. Separate a current scope/authority
entry point, current tasks, compact standalone knowledge and optional history.
Any reduction in mandatory reading must be a prospective project instruction
change. Do not silently treat existing whole-file requirements as optional.

Consider a bounded generic read-set manifest built above current instruction
paging: explicit roles, dependency edges, source digests, required image IDs,
completed character ranges and outstanding requirements. Keep manifests and
session exposure private. Reject dependency cycles, missing sources, excessive
closure and changed bytes. Do not automatically crawl an entire repository.

Acceptance: a fresh context can resolve its complete required closure and report
missing or truncated ranges without recursive historical packet reads. Identical
hashes do not imply that a new context read the material. Record emission,
reported model exposure and comprehension limits separately. A digest change
invalidates the affected read evidence; an outer-tool truncation remains a gap.
Finish required preparation before changing settings or launching the game.

### NC-04: preserve identity through interruptions

Keep input intent, dispatch/delivery and observed application effect distinct.
A click labelled Start is not necessarily an observed match start. A completed
receipt does not prove it hit that control; an unknown receipt does not permit a
blind retry. Retain setup failures and missed inputs without counting them as
game losses or erasing their resource cost.

Existing nonterminal run contexts already preserve clocks. For recovery after a
ledger was finished too early, first document an explicit linked-successor recipe.
Consider an offline parent-reference contract only if that recipe remains costly:
same match identity, original root anchors, predecessor artifact/hash, continuation
reason, controller/model exposure and unknown intervals. Preserve immutable
terminal ledgers; do not reopen them or append fabricated historical events.
Reuse existing corpus grouping rather than create another match-results database.

Acceptance: setup-only failure, same-match continuation, context replacement,
changed model, reboot and unknown-delivery cases retain their conditions. Reject
cycles, incompatible identities and corrupt parents. Overlapping segments are
not summed as active work. Original elapsed time and a new segment's elapsed
time are both visible. No new Start allowance follows from creating a ledger.

### NC-05: review meaning as well as bytes

Use existing learning records for compact result fields with evidence references
and explicit uncertainty. Independently reread decisive terminal fields before
publishing: placement, combat totals, rounds, duration and relevant counters.
Retain the worker's original report and a dated correction when they disagree.
For example, a dropped duration digit must not become a clock-speed finding.

Projects own consistency rules and field interpretation. Do not impose a universal
relationship between rounds, fight counts, rewards or victory. Separate displayed
game duration, host elapsed time, declared active wall time and measured inference.
Stat changes, completed transactions and combat benefit require different evidence.

Acceptance: a correct PNG inventory paired with wrong prose produces a correction,
not a semantic pass. Unknown delivery, image-less failures, missing files and
truncation remain visible even when every existing PNG hashes correctly. A later
healthy segment does not erase an earlier unhealthy one. Any OCR assistance is
optional and separately qualified, with ambiguous digits left unresolved.

### NC-06: reduce long-session closeout work

Use the new headroom warnings and existing segment recipe before proposing
automatic rotation or larger journals. A compact offline session summary could
list linked recorders/closeouts, independent counts, gaps, uncertainty and remaining
owned resources. It should link canonical artifacts and reuse existing verification;
it must not mutate journals, delete media or claim aggregate cleanup success.

Track original temporary settings, responsible controller, intended restoration
and observed completion. Keep logical desktop ownership separate from permission
and portal grants. Normal quit, exact process absence, owned launcher completion
and visible synchronization are distinct facts; retain unavailable exit telemetry.

Acceptance: two valid segments plus one uncertain segment remain separately
visible; cross-recording `--previous` misuse stays rejected. A closeout summary
cannot turn selected process absence into normal exit. Completed games close
normally, temporary settings are restored or explicitly deferred, and shared
launchers/helpers/grants remain preserved. No automatic force termination.

### NC-07: make learning experiments prospective and discriminating

Keep game mechanics, budgets and strategy in the consuming project. Each learning
brief should name one question, trigger, resource reserve, bounded action,
observable effect, stop rule and concrete deferral condition. Lack of a guaranteed
next offer need not prohibit a small category-based search. Conversely, unused
currency alone does not prove a useful purchase existed. A successful qualification
trial should not accidentally prohibit later ordinary use of that mechanic.

After review, release a standalone successor packet and freeze it for a declared
evaluation block. Specify the number of starts and conditions before launching;
do not keep extending the block until a win appears. Count every authorized
started match and preserve setup failures separately. Report sequential learning,
coaching and operational recovery exposure; do not infer causal improvement from
a few wins under changing knowledge and account history.

Acceptance: the next learning trial can return a useful negative result without
a forced purchase or another match. Evaluation uses a reviewed fixed condition,
declared outcomes and prospective decision criteria. No new model-ranking,
opponent-difficulty, cross-platform or gamepad claim is implied.

### NC-08: measure overhead before adding more automation

Prefer an offline timing summary from existing receipts: instruction delivery,
setup, input completion, capture acquisition, controller gaps, match completion
and cleanup. Show unknown intervals instead of attributing them to the backend
or model. A long gap can include reasoning, tools, game time and context work.

Provider token, cost, actual model and service telemetry belong to their owning
integration. Basaltwater may consume explicit receipts with provenance; it must
not infer execution settings from requested settings or report unavailable usage
as zero. Preserve the distinction between host timing and input-to-photon latency.

Acceptance: summaries identify their source fields, units, clock scopes and
missing coverage. They deduplicate linked continuations and retain uncertainty.
Collect a small baseline before choosing a new scheduler, recognizer or recorder.

## Delivery sequence and restart gate

1. Finish and reconcile the current run, including normal cleanup and retained
   uncertainty. Stop further game testing. Documentation and isolated offline
   development can proceed without touching its active desktop or pinned inputs.
2. Choose bounded implementation assignments from this proposal. Deliver NC-01
   recipes and NC-03 context cleanup first; implement a new generic contract only
   where the existing recipe leaves a demonstrated gap. Keep schemas/versioning
   under one owner and use synthetic fixtures with mocked host operations.
3. Review both repositories together. Publish a compatibility/adoption receipt
   identifying producing and consuming revisions, installed CLI/skills, running
   helper provenance, changed contracts, checks and remaining unknowns. Reconcile
   stale planning statements against implementation and qualification evidence.
4. At a separately selected maintenance point, refresh and qualify only affected
   runtime components. Do not restart a healthy helper during a live match or
   infer loaded-code parity from the checkout. Run NC-01/02 disposable native
   checks before using the new path for gameplay.
5. Prepare a fresh bounded learning brief, then a frozen evaluation block only
   after its prerequisites are met. Record model/effort/service selection,
   knowledge/media closure, game/build/platform, account-history limits, settings,
   original clocks, ownership, start accounting and cleanup responsibility.
   Resume game testing only when the next cycle is explicitly activated.

The implementation handoff should identify writable files, compatibility changes,
focused checks, private evidence, unresolved objections and companion-project
adoption instructions. Source delivery, offline acceptance, native qualification
and game qualification remain separate deliverables. A failed required check
returns to its owning work item; it does not justify a reset or extra match.

## Scope exclusions

No game-specific strategy in Basaltwater, automatic model escalation, hidden
coaching, unrestricted continuous capture, new global input daemon, protection
bypass, silent input replay or automatic game restart. No publication of private
captures, prompts, account paths or machine inventories. Windows, gamepad, broad
perception and semantic gameplay execution retain their existing independent
qualification plans; they are not prerequisites for improving the working native
pointer/keyboard route.
