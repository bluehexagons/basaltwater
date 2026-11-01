# Testing status

**Snapshot: 2026-11-01.** The feature assessment summarizes repository
coverage recorded through 2026-10-31; it does not establish that every check
was repeated on the current commit. The private test-environment inventory was
reviewed as of 2026-11-01. See each linked record for its scope and date.

Basaltwater has broad automated coverage and selected native checks across
Debian guests, a physical virtualization host, and development workstations.
Those checks cover useful setup, deployment, storage and agent workflows. They
are evidence for the cases exercised, not a blanket claim for every option,
platform or release candidate. The `2.0.0rc1` stable-release matrix remains
unqualified; see the [release gate](../release-qualification.md).

| Document | Purpose |
| --- | --- |
| [Feature status](features.md) | Skimmable coverage and gaps by feature |
| [Tested workflows](workflows.md) | Examples and procedures corresponding to selected live checks |
| [Methodology and environments](methodology.md) | Test levels, CI, anonymized platforms and intended use cases |
| [Detailed coverage map](../plans/testing-coverage.md) | Supporting observations, narrower boundaries and next checks, recorded through 2026-10-31 |
| [Release qualification](../release-qualification.md) | Exact-candidate cases and publication gate |

Important open areas include off-host recovery, broader interrupted-setup and
power-loss cases, real registered GitHub runner jobs, combined build/CI host
composition, public tunnel and certificate issuance, and native platform
qualification. The feature table distinguishes implemented-but-unqualified
and planned work. Historical failures, including LXC suspend, are not passes.

On 2026-11-01, the documentation/privacy cleanup passed 33 focused agent and
documentation tests, followed by 868 tests across affected Python modules.
These runs qualified those changes only; native Windows tests were not run.
Keep per-run revisions, exact commands and private evidence outside this page.

Update the affected status and its evidence date when new checks complete;
preserve older dates for areas not retested. Keep raw logs, personal addresses,
account paths and private infrastructure inventories outside Git.
