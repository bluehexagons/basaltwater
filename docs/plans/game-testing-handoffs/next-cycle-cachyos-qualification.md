# CachyOS handoff qualification record

[Topic index](next-cycle.md)

## CachyOS qualification, 2026-11-11

After the previous controller's completed run and resource handoff were reconciled,
the owner activated this Basaltwater-only qualification. Installed CLI and running
helper startup source were updated to `b96c6f434b836903a723e7038b8105a0051c7900`.
Managed skills were refreshed with the existing bundle installer. Preflight
observed matching clean invoked/managed/startup sources and nine matching skill
bundles, with no unknown bundles. This does not attest immutable loaded code or
session-loaded skill identity. Only documentation changed in the checkout during
the probes; recorder provenance retains that dirty state.

The helper was stopped/started normally without an active recorder; saved portal
access restored and human permission remained unchanged. Full workstation setup,
OS/provider upgrades and T3 reload were unnecessary. The system Python initially
failed to import Tk because the optional runtime was absent; that owned launch
exited before opening the probe. The successful probes used a private Tk 8.6
runtime extracted from an Arch archive package whose detached signature verified
against the installed distribution keyring. Its library/script paths applied only
to those launches. No system package was installed. The recipe now checks the Tk
import before starting a recording.

| Case | Observed acceptance / boundary |
| --- | --- |
| Reduced full view | Controller inspected an explicitly half-size derivative, selected Advance visually and mapped through the saved full-frame receipt; exactly one Advance event |
| Bounded crop and wrong target | Independent probe retained a deliberate neighboring Decoy selection and visible red state; fresh crop inspection and a written changed-target explanation preceded one Advance |
| Crop drag boundary | Offline mapper refused an endpoint outside the presented crop, with nonzero exit; no live drag attempted |
| Normal observation pair | Both successful sequences retained completed input and two distinct PNG paths; reduced-view captures visibly showed Transition then Settled |
| Second-capture collision | Independent probe created the collision after input; sequence exited nonzero at index 2 with completed input and first PNG intact; exactly one Advance, no replay |
| Headroom and rotation | Live replies exposed remaining capacity; two separately finalized segments and their closeouts were linked in one fixture ledger with an explicit gap. Native low-headroom warning was not exercised by artificially filling a journal |
| Human control change | No real human takeover occurred; this native case remains unqualified. Permission was never simulated or changed |

All three successful probe instances logged one Advance and one normal close;
their owned launch receipts reported exit zero. Segment A verified 11 PNGs and
was healthy. Segment B verified three PNGs but retained one deliberate image-less,
uncertain screenshot failure, so verification correctly exited nonzero and stayed
unhealthy. Neither segment was truncated or had missing/mismatched saved PNGs.
Closeouts retained both results; the fixture ledger was finished. No recorder or
owned probe remained active. Shared applications, helper, enabled control and saved
grant were preserved. Initial black capture and successful wake/recapture remain
separate evidence rather than a discarded setup failure.

Requested 150/400 ms sequence delays measured approximately those waits, while
native capture/status acquisition added time. Helper input/frame timestamps,
controller times and gaps remain in private receipts; callback logs alone were
not used to assign visual phases. No inference-time, input-to-photon or provider
usage claim follows. `make check-fast SUITE=desktop` passed all 849 desktop tests,
compilation, documentation/link and metadata checks on the tested source.

The private adoption receipt records the consuming revision and its current
authority/tasks without modifying that repository. Its targeting, exposure and
prospective evaluation tasks still need consuming-project delivery. This check
activates no game match and qualifies no consuming-game transition, native Windows
route or gamepad. Real takeover and native low-headroom warning remain explicit
platform gaps; broader adoption remains pending rather than an aggregate pass.

Copy this adoption receipt into private state and fill it from observations.
Pending fields are not a compatibility pass. Keep the tested source revision
separate from whichever HEAD happens to be present during handoff.

```text
Producing Basaltwater revision and clean/dirty state:
Consuming project revision and clean/dirty state (pending until reviewed there):
Probe/recipe fixture paths and SHA-256 digests:
Invoked CLI source; managed source; running-helper startup source:
Installed skills; new context's reported exposure; unavailable provenance:
Changed contracts: optional standalone probe flag; no evidence schema change
Offline commands, tested revision, exits and private evidence:
Disposable native cases: passed / failed / unavailable, with platform and evidence
Consuming-game cases: scene / trigger / resolution / terminal, separately qualified
Current run disposition; owned resources; cleanup responsibility:
Open objections, adoption changes and exact next action:
Explicit next-cycle activation source, or not activated:
```

Review the consuming project's current authority, tasks, controller instructions
and result format against NC-01–08 together; record each adopted recipe and each
remaining difference. The initial source-only delivery established no companion
revision or installed/runtime compatibility; the dated CachyOS check above adds
only its observed scope. Future controllers must not fill those fields from this
repository's HEAD or from a version string alone.
