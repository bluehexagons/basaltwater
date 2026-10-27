# Test coverage and next checks

Updated 2026-10-26. This is a current testing map, not a run log or the
[release qualification gate](../release-qualification.md). Coverage below is
partial: selected live workflows passed, with automated or controlled probes
where stated. CI success does not establish live coverage; repeat affected
checks when behavior changes.

Ordinary lab testing uses SSD-backed guests, including selected bounded checks
on a used USB SSD. This does not qualify drive reliability. The known-failing
HDD is reserved for disposable fault experiments. Disk-wide scans, SMART
self-tests and hardware stress/endurance tests are excluded.

## Surface

The declared surface has **15 setup profiles, 50 top-level commands, 307 command
paths and 535 distinct long-option spellings**, with aliases collapsed. These
counts describe breadth, not a percentage tested. Regenerate the inventory with:

```bash
.venv/bin/python scripts/inventory_test_surface.py
```

Selected live workflows cover `server_lite`, `server_web`, `server_proxmox` and
`agent_vm` on Debian QEMU, unprivileged LXC and a physical Proxmox node. Other
profiles, native platforms and machine types remain unqualified as complete
installations; optional combinations need their own checks.

## Current coverage

| Area | Selected coverage | Important gaps |
| --- | --- | --- |
| Agent setup and tools | Fresh setup, populated reruns, reboot, separate Codex enrollment, managed skills and selected developer/container tools | Other agent profiles/providers, browser/pairing enrollment and optional combinations |
| Agent tasks and goals | Live Luna inspection, workspace edits and conversation/checkpoint continuation, pruning and a token-budget stop. Permission/cancellation/limit guards; controlled synthetic-CLI recurrence, worker restarts and receipt recovery, including interrupted-run review stops; unknown usage, surviving-child exclusion and failure isolation | Longer provider-backed goals, additional crash/claim/publication boundaries and physical write failures. Token budgets are checked between invocations |
| Approvals and follow-ups | Separate HTTPS approval, prerequisite expiry/retry, broker restart and credential lifecycle. Controlled process crashes and independent-process decisions cover child publication with synthetic execution and no broker/provider actions; unresolved receipt/request-ID retention is bounded. Local Chromium covers saved-submission and cancellation controls with simulated broker/provider actions | Power loss, further crash boundaries, manual reconciliation of uncertain remote outcomes and remaining live follow-up browser flows |
| Admin jobs | Approved refresh and no-op APT updates preserve tasks; live Chromium approval controls and job status; Chromium/Firefox layouts and rejected power confirmations. Controlled metadata/fetch/status failures and synthetic-source crash/setup-exclusion probes | Package-changing upgrades, executed power actions, power loss and other interruption boundaries |
| Proxmox guests | Provisioning, identity/reboot, lifecycle controls, disk/resource flags, snapshots, full/linked clones, selected storage moves, backups and same-host restores | Off-host restore/retention, reverse storage moves, EFI/TPM layouts and template retirement. LXC CRIU suspend failed |
| Host storage and networking | Passive capacity audit, storage persistence, selected maintenance guards, local orphan protection, firewall allowlists and strict SSH/TLS | Management-network isolation, multi-node/HA/Ceph, interrupted host upgrades and live remote-node/RBD/VirtioFS deletion guards |
| Sync and PAR2 | Mirrors, mount-loss guards, interrupted replacement, unreadable-source preservation and logical-damage repair; supervised CIFS sync failure handling and incomplete-run health. Controlled scrub child-teardown checks; native PAR2 verification, missing-mount rejection and selected soft-CIFS preflight/work timeouts, successful retry and parity preservation; enumeration I/O regressions | Hard-mounted CIFS scrubs, other network filesystems, mount changes during scrubs and power loss. A failed mirror can still apply unrelated deletions; kernel I/O can delay teardown indefinitely |
| Btrfs | Single-device data mounts through a full `server_lite` setup, populated rerun/reboot, sync/PAR2 and read-only native scrub on USB-backed storage; UUID preservation, injected checksum detection and unsafe-mount guards | Diagnostic integration and automatic native scrub; multi-device/subvolume support needs separate design. Single-copy data cannot self-repair |
| Samba and Syncthing | SMB3 encryption/access control, automount repair/reconnect and selected soft-mounted Linux CIFS stopped-server/blackhole cases; two-peer exchange, conflicts, version restoration, credential rotation, rerun/reboot and removal/re-enable | Hard-mounted CIFS, other network filesystems, Windows/macOS SMB clients, Syncthing browser GUI and relays/discovery |
| Gogs and Git mirrors | HTTPS/SSH Git, LFS hashes, access control, rerun/reboot and same-host recovery; mirror routing/configuration guards and transfers against Gogs plus a local primary | Actual GitHub transport/permissions, off-host recovery, database migration rollback, restored SSH transfers and attachments |
| Deployment | Signed loopback webhooks, separate build/app hosts, SSH publication and queue recovery; real Go services, Node-built root/subpath sites and a same-origin React/Go/SQLite app. Manifest SPA reloads, populated reruns/reboots, selected Chromium flows, private SQLite backups and unhealthy-candidate rollback. Separate-guest restore on the same physical host preserves login, data and new writes over verified private HTTPS | Native SPA webhook publication, off-host recovery, public sender/Tunnel/ACME, Godot publishing, database migration recovery and power loss |
| Shared builders | Managed enrollment, rerun, key rotation, removal/re-enable and idle reboot; hardened typed SSH without supplementary groups or RDP access. Restricted static/Go jobs cover bounded logs, verified artifacts, active-work setup refusal and selected scratch/cancellation/output cleanup. Public selected-builder setup reserves both deployments before builds, stages both before activation and applies the guarded final profile. Fresh and repeat static/Go HTTPS, native child-output separation, target-only reconciliation and idle reboot pass without target compilers. Saved patch/redeploy preserves the selected revision, builder and sibling deployment; retained reconciliation preserves installed identities. Native ready-SPA/Go-API checks cover root/deep links, API prefix/query/error routing, a retained same-domain neighbor, revision replacement, reconciliation and idle reboot. Controlled truncated upload prevents activation; an unhealthy API candidate restores both prior exports and its unit while the neighbor remains available. Exact IDs and recovery guards remain, and guarded controller reentry makes no remote calls. Controlled enrollment APT-lock waiting passes. Root-local capacity inspection separates persisted quota and record headroom from filesystem availability; a check with a cold protected code copy preserves installed code and retained evidence without creating bytecode or initializing empty stores. Explicit terminal settlement reclaims logical quota; root-local source/log pruning survives controlled process interruption and same-revision resumption while preserving terminal results, bundles and same-ID SSH replay. Native shared-reader locks exclude same-job deletion until readers close. An offline SIGKILL after durable target activation and before controller acknowledgement preserves exact identities and guards, blocks new work on reentry and permits cached target replay; transport and activation effects are mocked. Automated checks also cover stale/cancelled inputs | Automatic artifact retention policy, power loss and additional crash/restart boundaries. Preview hostname resolution prevented browser rendering qualification. No untrusted-job isolation claim |
| Panels | Setup/auth/TLS, journals/timers, Chromium agent forms, synthetic Chromium/Firefox Guacamole checks and native cached Proxmox inventory export | Native PVE console, Firefox ESR, inventory timer/full HTTP lifecycle, additional application browser flows and collaborative preview CA trust |
| User rename | Identity/groups, moved or retained home, selected interruption recovery, rerun/reboot and initialized-container-store guards | More interruption points, software-specific state and native SMB mount migration; home moves are not general application migration |
| Other surfaces | Selected loopback notifications and external Codex enrollment | Native desktop/kiosk/CachyOS/WSL/Windows, GitHub runner jobs, game publishing, external alerts/public DNS and exact-candidate installer qualification |

## Next useful checks

1. **Shared build hosts:** extend crash/restart, capacity and retention-policy
   qualification before webhook or shared-runner adapters. Ready SPA/Go routing,
   revision replacement, retained reconciliation, idle reboot and controlled
   transfer/health failures have selected native coverage.
2. **Longer agent runs:** exercise longer provider-backed goals and remaining
   worker crash/restart boundaries while preserving usage limits and recovery.
3. **Agent administration:** test package-changing upgrades on a disposable target;
   extend interruption checks to other applying jobs.
4. **Storage failure handling:** check hard-mounted network storage, then Btrfs
   diagnostic integration and native scrub scheduling.
5. **Recovery and developer workflows:** restore backups onto another physical
   host; test real GitHub mirror permissions and additional deployment
   manifests. Account-bound tests need their own explicit scope.

## Keeping this useful

Update the affected coverage row and priorities in place. Keep detailed commands,
revisions, results, cleanup and artifacts in private evidence records; do not
append batch histories or fix chronologies here. The account-local project note
`lab-testing-coverage` points to those records:

```bash
basaltw agent memory get lab-testing-coverage --project . --json
```

Use `python3 run_tests.py --list-suites` and the
[contributor checks](../agents/contributing/README.md#standard-checks) to select
automated validation. Browser qualification is separate. Test meaningful option
interactions and failure/rollback paths; accepted parsing or installed packages
alone do not establish working features.
