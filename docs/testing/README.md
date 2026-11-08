# Testing status

**Snapshot: 2026-11-08 UTC.** Includes selected native storage triage/filesystem
and shared build/CI host qualification, plus earlier retained evidence. The owner's
stack setup rerun
indicates **basic setup functionality only: not all features were tested**. It covers selected
Debian/Proxmox server and coding-VM commands, a partial CachyOS refresh and basic
native Windows setup with Godot and T3 Code. Exact tested revisions were not
supplied for this report. Subsequent native CachyOS checks confirm a running
T3 desktop/Codex session after manual recovery and read-only backend discovery;
the corrected automatic relaunch still needs a live run. See the
[rerun summary](evidence.md#owner-reported-stack-setup-rerun) and
[T3 recovery record](evidence.md#cachyos-t3-desktop-recovery) for scope and limits.
Native Windows checks on 2026-11-01 additionally confirm installed diagnostics,
skill freshness and real supervised task behavior at `5f549204`; see the
[Windows evidence](evidence.md#native-windows-workstation-and-tasks). Historical
project builds/exports retain their original dates and do not qualify fresh setup.
The [Windows follow-up](windows.md#follow-up-native-qualification-2026-11-01)
adds disposable desktop control, supervised npm/MSVC/Godot workloads and real
timeout/cancel cleanup, including reproduced restore and completion-order fixes.
Native CachyOS SDL/OpenGL application checks on the same date also qualify
selected Intel Arc Wayland rendering, scripted UI interaction, software capture
comparisons and display-wake recovery; see the
[application evidence](evidence.md#cachyos-native-game-and-capture-workflow).

| At a glance | Status |
| --- | --- |
| Owner-reported stack setup rerun | 🟡 **Partial** — basic setup success reported; selected options are not individual feature passes |
| [Debian and Proxmox](debian.md) | 🟢 **Labbed** — selected server and host workflows passed; options and recovery gaps remain |
| [CachyOS](cachyos.md) | 🟡 **Partial** overall; 🟢 selected T3/Codex, Godot and SDL/OpenGL application workflows and display-wake recovery labbed; corrected T3 refresh relaunch awaits a live check |
| [Windows and WSL](windows.md) | 🟡 **Partial** overall; 🟢 selected diagnostics/tasks, scoped desktop control, native toolchain workloads and T3/Codex session labbed; fresh installation, physical QA and WSL integration remain open |
| Combined build/CI host | 🟢 **Labbed** — selected real Actions, SSH/webhook builds, nested scheduling, maintenance, rerun/reboot, process-loss recovery and bounded scratch ENOSPC; see [scope and limits](evidence.md#shared-debian-build-and-ci-host) |
| LXC suspend | 🔴 **Failed** — the recorded live attempt failed |
| Production use and broad human QA | 🟣 **Deployed** / ✅ **Verified** — no status claimed without the relevant evidence |
| `2.0.0rc1` stable release | ⚪ **Untested** — required [release qualification](../release-qualification.md) remains open |

## Status legend

Labels describe the **specific scope in each row**, not every configuration
of a feature. The accompanying evidence and limits still matter.

| Label | Meaning |
| --- | --- |
| ⚪ **Planning only** | Proposed work; implementation has not started. |
| 🔵 **In progress** | Implementation or qualification is underway. |
| ⚪ **Untested** | No qualifying test is recorded for the stated scope. |
| 🟡 **Partial** | Automated, fixture or limited component checks passed; the stated full workflow has not been labbed. |
| 🟢 **Labbed** | Agents exercised the stated workflow on a real test system; this does not establish production reliability. |
| 🔴 **Failed** | A recorded attempt failed; the affected scope remains unqualified. |
| 🟣 **Deployed** | The owner reports using the stated scope on a real production or internal server. |
| ✅ **Verified** | Long-term consistent reliability or broader human QA supports dependability for the stated scope. |

Statuses are not a strict progression: a deployed feature can still have a
failed test or untested configuration. Record the date, scope and evidence;
do not infer deployed or verified status from lab or automated results.

## Documents

| Document | Purpose |
| --- | --- |
| [Feature availability by platform](features.md) | Compare platform scope and open a detailed status page |
| [Debian and Proxmox](debian.md) · [CachyOS](cachyos.md) · [Windows and WSL](windows.md) | Feature status, evidence, limits and next checks for each environment |
| [Tested workflows](workflows.md) | Examples and procedures corresponding to selected live checks |
| [Methodology and environments](methodology.md) | Test levels, CI, anonymized platforms and intended use cases |
| [Dated evidence summaries](evidence.md) | Owner-reported setup rerun and retained host, storage, Shares & Data and automated results; tested revisions where known |
| [Continue testing on Proxmox](continuing.md) | First bounded test batch, acceptance criteria and handoff requirements |
| [Detailed coverage map](../plans/testing-coverage.md) | Supporting observations, narrower boundaries and next checks |
| [Release qualification](../release-qualification.md) | Exact-candidate cases and publication gate |

Important open areas include off-host recovery, broader interrupted-setup and
power-loss cases, broader GitHub event/organization and shared-host fault cases,
public tunnel and certificate issuance, and native platform
qualification. Platform pages distinguish available, planned and unqualified
work. Historical failures, including LXC suspend, are not passes.

On 2026-11-01, the documentation/privacy cleanup passed 33 focused agent and
documentation tests, followed by 868 tests across affected Python modules.
These runs qualified those changes only; native Windows tests were not run.
The later Windows catalog pass separately ran native validation and installed
component/workflow checks; its [dated evidence](evidence.md#native-windows-workstation-and-tasks)
does not extend those earlier Python runs to Windows.
Keep per-run revisions, exact commands and private evidence outside this page.

Earlier full validation of the Proxmox fixes passed 9,243 tests with six skips
and 82% branch-aware aggregate coverage. That is automated evidence for its
revision, separate from the live host checks and the later documentation work.
Shares & Data's current per-task UI and recovery guards now have installed
HTTPS/approval/worker coverage, including missing-file repair with adequate parity,
accepted content with independent verification, race refusal and owned-guest reboot
persistence. Selected installed Chromium/Firefox interactions also pass with
isolated CA trust, including a fixed Unicode search bug. Shared preview trust and
native ESR remain open; see the
[dated scope and limits](evidence.md#shares-and-data).
The [full-profile follow-up](evidence.md#shares-and-data-full-profile-refresh)
qualifies selected `agent_vm` setup, actual public refresh, reconciled populated
controller rerun and guest reboot, preserving independently approved read-only
shares, paused routines, identities, credentials and exact bytes. It fixes lost
browser opt-outs and refresh's incorrect requirement for saved Samba passwords;
other profiles and interruption/power-loss cases remain separate.

The [storage follow-up](evidence.md#storage-triage-and-native-filesystems)
qualifies selected verify/quarantine/delete batches, durable history and mirror
protection, real ext4/Btrfs/XFS observations and guest reboot persistence. It fixes
failed staged recovery recording the wrong file identity, then verifies a separately
approved restoration. Native XFS online scrub remains unavailable on the test
kernel; these HTTP/worker checks do not add browser qualification.

A separate fresh Debian guest at `176caeff62ad` also passed a real approved
leaf-package upgrade, separately approved no-op, native APT index failure and
lock waiting, with exact task, service-identity, credential and data preservation.
See the [Admin evidence](evidence.md#package-changing-admin-update).
The [owned-package follow-up](evidence.md#admin-service-restart-and-conffile-conflict)
also passes modified-conffile preservation and an actual daemon restart, followed
by a separately approved no-op without another restart. Distribution service
packages, kernel updates and interruption/power-loss remain open. The subsequent
[failed-hook batch](evidence.md#admin-failed-maintainer-hook-and-recovery) qualifies
an owned failed post-install hook, retained half-configured package, independently
approved configuration recovery and no-op, preserving other packages/services/data.

CI was repaired at `785c3ffd` by regenerating the stale Shares & Data specimen.
Full local validation passed 9,196 tests with six skips and 82% coverage;
hosted full Debian and Node compatibility checks passed. See the
[automated evidence](evidence.md#automated-validation) for the separate CI scope.
The shared-host implementation at `550e8f7f` subsequently passed 9,316 local
tests with six skips and 82% coverage, all remaining `check-ci` checks and hosted
CI. Its [native qualification](evidence.md#shared-debian-build-and-ci-host) is
recorded separately from those automated results.

The [Proxmox inventory batch](evidence.md#proxmox-inventory-and-exporter) adds
native inventory comparison, unattended timer refresh, verified host-local HTTPS
and worker isolation. Separately owned component units passed interruption,
snapshot preservation, recovery and removal. Intermittent controller partial-page
timeouts reproduced with server TCP retransmissions and remain unresolved.
Investigation was deferred by the owner on 2026-11-02, with evidence retained for
paired captures; unrelated testing continues. Full controller/browser and
production teardown qualification remain open.

Update the affected status and its evidence date when new checks complete;
preserve older dates for areas not retested. Keep raw logs, personal addresses,
account paths and private infrastructure inventories outside Git.
