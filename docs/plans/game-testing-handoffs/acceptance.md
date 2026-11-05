# Basaltwater learning handoff acceptance

All four assignments in the [index](README.md) have source implementations and
offline acceptance. Native/game qualification is separate and has not run.
The [machine-readable compatibility receipt](../../examples/learning-records/learning-compatibility.json)
pins the integrated runtime producer, individual feature commits, contract
versions, synthetic fixture digests and actual CLI grammar. The initial
[records-only receipt](../../examples/learning-records/compatibility.json) is
preserved as historical evidence; its deferred list describes that earlier point.

## Delivered scope

| Assignment | Runtime / contract | Acceptance boundary |
| --- | --- | --- |
| BW-GL02A | `desktop/learning_records.py`, `record_bundles.py`; [records](../../workstations/learning-records.md) | Closed graphs, explicit unknowns, separate coach dispositions, stale/late rejection, pinned private copies, hostile/bounded inputs and unchanged originals |
| BW-GL06A | `desktop/corpus.py`, canonical corpus extension; [summaries](../../workstations/learning-corpus.md) | Setup-only/zero starts, continuation and duplicate deduplication, conflicting outcomes/conditions, missing sources, unknown/scoped telemetry and reconciled denominators |
| BW-GL04A | `desktop/perception.py`, `png_regions.py`, `perception_reports.py`; [profiles/evaluation](../../workstations/local-perception.md) | Fixed-region detector, literal crops, abstentions/ambiguity, drift, malformed PNGs, frozen run bindings and per-field false-confident/latency reports |
| BW-GL05A | `desktop/conditional.py`, `conditional_contract.py`, `conditional_native.py`; [executor/wait](../../workstations/conditional-execution.md) | Fresh target/token per action, effect verification, terminal return, geometry/control/token failures, unknown/partial delivery, post-capture failure, budgets/cancel, durable intents and wait retention |

Shared integration is in `desktop/qa_cli.py`, `lib/desktop_cli.py`, `run_tests.py`
and `scripts/check_wheel_artifact.py`. The explicit desktop suite registers all
four focused test modules. Tests mock native/managed status/request and process
launch boundaries so accidental real desktop access fails. The installed-wheel
check exercises records, corpus, recognition, held-out reports and offline plan
validation outside the source tree, and refuses execution without `--native`.
No new runtime dependency is needed for the bounded RGB template prototype.

Validation commands from the project environment:

```text
python -m unittest tests.test_desktop_learning_records tests.test_desktop_corpus tests.test_desktop_perception tests.test_desktop_conditional
make check-fast SUITE=desktop
make artifact-check
make check-ci
git diff --check
```

The full integration check includes syntax, CLI documentation, Markdown links,
generated brand assets, package metadata, isolated wheel installation/smoke and
the full covered suite. Expensive live tests remain opt-in; offline acceptance
does not claim those checks or platform/game behavior. Raw check logs remain
private. Source was delivered serially on `main`; feature commits in the receipt
identify each bounded milestone and its parent provides the feature's base.
No workers, watchers, live desktop jobs, model calls or helper/system changes
were created by these assignments.

The initial delivery's affected checks passed: **38 focused learning tests**, **810 desktop
tests** through `make check-fast SUITE=desktop`, and the installed-wheel check.
The full `make check-ci` run passed static/brand/package/wheel stages and ran
9482 tests with two skipped, but remained non-green with **2 failures and
15 errors** outside the new feature modules. A private source export of the
pre-feature commit `982a3cc4` reproduced all those failures in the five relevant
modules (121 tests): Python 3.14 `sys.flags`/`Path.lstat` mock assumptions in
builder tests and the node baseline fixture finding the workstation's installed
pnpm after its invalid fixture executable. These existing portability failures
were preserved, not changed by the learning implementation. The final wait
eviction regression also passed the affected fast/artifact checks after that
full run; no full-suite pass is claimed. The repository's full CI environment
remains the documented Debian environment in the contributor guide.

The follow-up review fixed exact held-out field-name handling (including spaces)
and incomplete/duplicate recognition fields incorrectly passing delta comparison.
The executor now checks this recognition completeness before dispatch as well as
after input; completed delivery remains separate from an unknown effect. Delta
source references are copied so later result mutation cannot alter the delta.
Regression acceptance passed **41 focused learning tests**, **813 desktop tests**,
and installed-wheel checks. No contract/fixture versions changed and no native
session was accessed. The earlier full-suite baseline failures remain separate;
the full suite was not repeated for these focused fixes.

## Consuming-project adoption

1. Pin the integrated producer and verify the five fixture hashes. Run the
   offline commands against new private outputs and record the consumer commit
   together with the tested Basaltwater commit. Retain both old and new evidence;
   the original record graph remains version 1 with unchanged fixtures.
2. Translate project state into the exact canonical observation graph. Keep
   game rules, profile labels, strategy, placement/outcome mapping, assistance
   policy and cost scope in the consumer. Null remains unknown. Do not treat
   record validation or matching byte hashes as verified image meaning.
3. Bind closed-corpus segments to pinned graphs. Declare inclusion/exclusion,
   original starts/windows, eventual outcomes, full condition IDs and missing
   evidence. Review conflicts explicitly. Summary fractions are review inputs,
   not an automatic policy ranking/release.
4. Freeze profile settings, template hashes, whole-run split and per-target risk
   thresholds before evaluating real held-out media. Synthetic red/blue fixtures
   only establish interoperability. Unrepresented animation, hover, occlusion,
   unreadable/unknown scenes and layout/version changes remain unqualified.
5. After an owner-selected refresh, follow the [disposable native recipe](../../workstations/conditional-execution.md#disposable-application-qualification-after-refresh).
   Start with one independently reviewed effect, then compose steps and waits.
   Keep one controller, actual helper authority and the original match clocks.
   Never replay a possibly sent step or interpret timeout as loss.
6. Record installation/helper parity and native/application/game acceptance as
   separate states. A game trial needs explicit scope and the consumer's outcome
   interpretation. Other platforms require their own authority adapter and
   acceptance; serialized packets grant no authority on any platform.

Coach dispatch and model scheduling stay controller-owned. Numeric OCR, dynamic
tracking, gamepad support and a strategy engine are not implicit extensions of
these four briefs. The delivered detector starts with the permitted stable
scene/anchor field class. The deferred native work has a concrete qualification
recipe rather than a claimed pass from mocked tests.
