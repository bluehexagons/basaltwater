# Testing status

The baseline below summarizes selected platform results through 2026-11-08 UTC.
For newer, scoped follow-ups, see [Headscale qualification](headscale.md),
[Debian desktop observation](../plans/game-testing.md#debian-refreshed-fixture-follow-up-2026-11-16),
and the [current release candidate](../release-candidate-current.md).
The [stable qualification matrix](../release-stable-matrix.md) governs publication.

| Scope | Baseline |
| --- | --- |
| [Owner-reported stack setup](evidence-workstations.md#owner-reported-stack-setup-rerun) | 🟡 **Partial** basic setup, with exact revisions unknown |
| [Debian and Proxmox](debian.md) | 🟢 **Labbed** selected server and host workflows. 🔴 The recorded live LXC suspend attempt failed. |
| [CachyOS](cachyos.md) | 🟡 **Partial** overall. Selected T3/Codex, Godot, SDL/OpenGL, and display recovery passed. Corrected T3 refresh relaunch awaits a live check. |
| [Windows and WSL](windows.md) | 🟡 **Partial** overall. Selected diagnostics, tasks, desktop control, builds, and T3/Codex passed. Fresh install, physical QA, and WSL integration remain open. |
| [Combined build and CI host](evidence-build-ci.md#shared-debian-build-and-ci-host) | 🟢 **Labbed** selected Actions, SSH/webhook builds, nested scheduling, maintenance, rerun/reboot, process-loss recovery, and bounded scratch ENOSPC. |

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

| Task | Reference |
| --- | --- |
| Compare feature and platform scope | [Feature availability](features.md), [coverage map](../plans/testing-coverage.md) |
| Find a tested procedure or choose a test level | [Tested workflows](workflows.md), [methodology](methodology.md) |
| Review dated results and their limits | [Testing evidence by topic](evidence.md) |
| Resume a bounded Proxmox test batch | [Continuing qualification](continuing.md) |

Off-host recovery, interrupted setup and power loss, wider GitHub event and
fault coverage, public HTTPS flows, and additional native platforms remain
open. Preserve evidence dates for scopes that have yet to be retested. Keep raw
logs and private operational details outside Git.
