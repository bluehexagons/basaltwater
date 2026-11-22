# CachyOS handoff execution and acceptance

[Topic index](next-cycle.md)

## Preparation while another controller owns the desktop

Source review and offline checks can proceed during the existing run. Keep that
controller responsible for game input, recordings and cleanup. A running thread,
enabled agent control or an absent game process alone does not establish that
ownership has been released. Reconcile its private checkpoint and outstanding
resources before using the shared desktop for disposable qualification.

Check the checkout and installed launcher separately. From the repository root,
with the existing development environment available:

```bash
git status -sb
git rev-parse HEAD
basaltw agent manifest . --capability workstation --json
.venv/bin/python basaltwater.py desktop --native preflight --repository "$PWD" \
  --instruction "$PWD/AGENTS.md" \
  --instruction "$PWD/docs/plans/game-testing-handoffs/next-cycle.md" \
  --output /absolute/private/source-preflight --json
PATH="$PWD/.venv/bin:$PATH" make check-fast SUITE=desktop
```

Use a new private output directory and retain full receipts outside Git. The
explicit Python invocation inspects this checkout even when `basaltw` resolves to
an older managed installation. Preflight's generated continuation commands use
`basaltw`; if retaining the source route, use the same explicit Python invocation
with the returned arguments and digest. The two selected instruction files are
an initial inventory, not the complete required reading closure.

Compare delivered skills against the intended source, rather than accepting an
installed manifest's comparison against its own older source as current. Preserve
invoked, managed and worker startup identities independently. Successful preflight
exit means observations were collected; inspect each check and its unknowns.
Offline test success qualifies the checkout only. Neither result qualifies the
installed helper or authorizes a refresh. At the maintenance gate, identify which
mismatches affect the native cases before selecting updates, then repeat preflight
and qualification against the actual installed components.

## CachyOS agent's execution order

1. Inspect source revision/dirty state, installed CLI and skill delivery, running
   helper provenance and private current-run checkpoint using read-only preflight.
   Record mismatches/unavailable checks. Confirm active-run disposition; this
   handoff carries no assertion that a previous game or cleanup finished.
2. At a separately authorized maintenance point, refresh only components shown
   stale and needed for qualification. Follow the existing safe refresh procedure;
   never weaken permission/freshness or restart during a live match.
3. When disposable native testing is authorized, run the probe matrix below.
   Retain exact private artifacts, command exits and timing gaps. Close only owned
   probe resources normally. No game launch is needed for these checks.
4. Reconcile companion-project instructions and publish a private adoption receipt:
   producing/consuming revisions, fixture digests, invoked/installed/running
   provenance, skill exposure, contracts reused, checks and unresolved objections.
5. Return implementation proposals for demonstrated gaps with exact writable files,
   compatibility impact and synthetic acceptance cases. Keep schema/CLI edits with
   one owner. Run `make check-fast SUITE=desktop`; CLI/packaging changes also need
   `make artifact-check`. Review final diff and `git diff --check` before commit.

| Native probe case | Required evidence / failure behavior |
| --- | --- |
| Reduced full view and bounded crop | Fresh controller activates the intended small control through each established transform; verify visible effect and exactly one event per independent probe |
| Numerically valid wrong target | Identify a neighboring control/incorrect transform before input; after any observed miss, fresh inspection and changed mapping explanation; no blind retry |
| Crop drag boundary | Offline rejection of endpoint outside crop; do not attempt it live |
| Normal observation pair | Exactly one intended input, two unique PNG paths; actual helper/frame/controller times and gaps retained |
| Deliberate second-capture collision | Nonzero receipt retains completed input and first PNG; no replay; exactly one probe event |
| Headroom and rotation | Installed helper reports remaining events/warning; link separate segments and retain gap; do not fill a live journal merely for a clean pass |
| Human control change | Real permitted takeover cancels subsequent work; never simulate account approval; unavailable case stays unqualified |

Offline Debian fixtures do not qualify KDE portal/PipeWire timing. Existing
Debian probe observations remain valid only within their documented boundary.
Native Windows, gamepad and consuming-game transitions remain separate.

## Source acceptance and follow-up scope

On source delivery `f5f3aa671e4f71aceeeb7412c65b17444478c4f4`,
`PATH="$PWD/.venv/bin:$PATH" make check-fast SUITE=desktop`
passed: 848 tests, maintained-Python compilation, CLI documentation, local links
and package metadata checks. `git diff --check` passed. The system Python lacked
development extras; checks used the existing project virtual environment. The
probe behavior was checked with mocked Tk and private temporary files, including
default-mode compatibility, decoy event isolation and blocked-output creation.
No graphical session was opened for that initial delivery. Full CI, native KDE
qualification and consuming-project adoption were not performed at that revision.

The successor-recipe follow-up passed the same fast checks with 849 tests. Its
real-parser/offline-ledger regression verifies frozen parent bytes, distinct
segment clocks, same-match/context references and unavailable elapsed after a
boot change, without contacting either desktop backend. Published successor
commands are also included in the parser-tested recipes. These checks exercise
the existing ledger API; manual parent-chain checks and original-deadline
enforcement still belong to the controller.

Local review on source `18f43def0b0c6b09223a88c8cbec28a3aa2a87e4` subsequently
passed `make check-ci` in the project virtual environment: 9650 tests run, six
skipped, 82% reported aggregate coverage, plus compilation, documentation, brand,
package metadata and wheel build/install smoke checks. This is a local Debian
result, without a claim about hosted CI or native KDE behavior.

The same source passed the [real Tk small-control development check](../../workstations/short-observation-sequences.md#small-control-development-check-2026-11-11)
in an isolated Xvfb display. Instrumented widget geometry supplied the numeric
targets; the test does not qualify a fresh controller's visual interpretation.
Default, Decoy recovery and blocked-output behavior passed with one Advance each
and normal probe close. An initial Settled log/Transition PNG mismatch remains
retained separately from follow-up rendered-frame evidence. Phase logs alone
cannot establish what a capture shows. These disposable fixtures did not touch
the shared desktop, account permission, helper or game.

For the CachyOS follow-up, expected writable source scope is
`scripts/desktop_observation_probe.py`, `tests/test_desktop_recipes.py`,
`tests/test_desktop_native_visuals.py`, `tests/test_desktop_qa_media.py`,
this handoff and the [crop-first targeting](../../workstations/desktop-observations.md#small-consequential-controls)
and [short observation sequence](../../workstations/short-observation-sequences.md)
recipes. Inspect actual failures before extending that scope to
`desktop/coordinates.py`, `desktop/client.py` or native helper code; add focused
regressions and compatibility notes for any demonstrated defect. Do not edit
the companion repository as part of a Basaltwater-only assignment. Keep captures,
runtime provenance, exact evidence paths and operational checkpoints outside Git.
No live helper, shared desktop, account or game state was changed for this
delivery. All disposable probe runs, Xvfb wrappers and local test commands
completed. Previous-run resources remain the prior controller's responsibility
until its private checkpoint is reconciled.
