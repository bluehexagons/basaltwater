# Testing status

**Snapshot: 2026-11-01.** The feature assessment summarizes repository
coverage recorded through 2026-10-31; it does not establish that every check
was repeated on the current commit. The private test-environment inventory was
reviewed as of 2026-11-01. See each linked record for its scope and date.

| At a glance | Status |
| --- | --- |
| [Debian and Proxmox](debian.md) | 🟢 **Labbed** — selected server and host workflows passed; options and recovery gaps remain |
| [CachyOS](cachyos.md) | 🟡 **Partial** — selected native discovery, graphics, task and T3 component checks |
| [Windows and WSL](windows.md) | 🟡 **Partial** — selected native Windows checks; full installations and WSL integration remain open |
| Combined build/CI host | 🔵 **In progress** — separate roles exist; composition is not implemented or qualified |
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
| [Detailed coverage map](../plans/testing-coverage.md) | Supporting observations, narrower boundaries and next checks, recorded through 2026-10-31 |
| [Release qualification](../release-qualification.md) | Exact-candidate cases and publication gate |

Important open areas include off-host recovery, broader interrupted-setup and
power-loss cases, real registered GitHub runner jobs, combined build/CI host
composition, public tunnel and certificate issuance, and native platform
qualification. Platform pages distinguish available, planned and unqualified
work. Historical failures, including LXC suspend, are not passes.

On 2026-11-01, the documentation/privacy cleanup passed 33 focused agent and
documentation tests, followed by 868 tests across affected Python modules.
These runs qualified those changes only; native Windows tests were not run.
Keep per-run revisions, exact commands and private evidence outside this page.

Update the affected status and its evidence date when new checks complete;
preserve older dates for areas not retested. Keep raw logs, personal addresses,
account paths and private infrastructure inventories outside Git.
