# Test coverage and next checks

Updated 2026-10-25. This is a current testing map, not a run log or the
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
paths and 527 distinct long-option spellings**, with aliases collapsed. These
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
| Agent tasks and goals | Live Luna inspection, workspace edits and conversation/checkpoint continuation; permissions, cancellation, usage and attempt/deadline limits. Controlled probes cover receipt recovery, unknown usage, surviving-child exclusion and failure isolation | Longer goals, additional crash/claim/publication boundaries and physical write failures |
| Approvals and follow-ups | Separate HTTPS approval, prerequisite expiry/retry and broker restart, selected competing decisions, cancellation and credential lifecycle. Synthetic proposals and injected write/transport failures cover child recovery and cancellation-only retries against the installed broker | Further task-creation/crash races, browser recovery controls and manual recovery from retained guards |
| Admin jobs | Approved web validation and setup dry run; failed validation preserves the running gateway. HTTP CSRF/replay checks and controlled status-publication races | Applying refresh, package upgrades, power actions, long-job interruption and browser interaction |
| Proxmox guests | Provisioning, identity/reboot, lifecycle controls, disk/resource flags, snapshots, full/linked clones, selected storage moves, backups and same-host restores | Off-host restore/retention, reverse storage moves, EFI/TPM layouts and template retirement. LXC CRIU suspend failed |
| Host storage and networking | Passive capacity audit, storage persistence, selected maintenance guards, local orphan protection, firewall allowlists and strict SSH/TLS | Management-network isolation, multi-node/HA/Ceph, interrupted host upgrades and live remote-node/RBD/VirtioFS deletion guards |
| Sync and PAR2 | Mirrors, mount-loss guards, interrupted replacement, unreadable-source preservation and logical-damage repair; supervised CIFS preflight/teardown and incomplete-run health. Enumeration I/O failures have automated regressions | Power loss, stalled scrub preflight and further network-filesystem variants. A failed mirror can still apply unrelated deletions; kernel I/O can delay teardown indefinitely |
| Btrfs | Single-device data mounts through a full `server_lite` setup, populated rerun/reboot, sync/PAR2 and read-only native scrub on USB-backed storage; UUID preservation, injected checksum detection and unsafe-mount guards | Diagnostic integration and automatic native scrub; multi-device/subvolume support needs separate design. Single-copy data cannot self-repair |
| Samba and Syncthing | SMB3 encryption/access control, automount repair/reconnect and selected soft-mounted Linux CIFS stopped-server/blackhole cases; two-peer exchange, conflicts, version restoration, credential rotation, rerun/reboot and removal/re-enable | Hard-mounted CIFS, other network filesystems, Windows/macOS SMB clients, Syncthing browser GUI and relays/discovery |
| Gogs and Git mirrors | HTTPS/SSH Git, LFS hashes, access control, rerun/reboot and same-host recovery; mirror routing/configuration guards and transfers against Gogs plus a local primary | Actual GitHub transport/permissions, off-host recovery, database migration rollback, restored SSH transfers and attachments |
| Deployment | Signed loopback webhooks, separate build/app hosts, SSH publication, service updates, rollback, private SQLite data and queue recovery | Public sender/Tunnel/ACME, Node/Godot/multiple components, database migration recovery and power loss |
| Panels | Setup/auth/TLS, journals/timers, Chromium agent forms, synthetic Chromium/Firefox Guacamole checks and native cached Proxmox inventory export | Native PVE console, Firefox ESR, inventory timer/full HTTP lifecycle, additional application browser flows and collaborative preview CA trust |
| User rename | Identity/groups, moved or retained home, selected interruption recovery, rerun/reboot and initialized-container-store guards | More interruption points, software-specific state and native SMB mount migration; home moves are not general application migration |
| Other surfaces | Selected loopback notifications and external Codex enrollment | Native desktop/kiosk/CachyOS/WSL/Windows, GitHub runner jobs, game publishing, external alerts/public DNS and exact-candidate installer qualification |

## Next useful checks

1. **Agent follow-up recovery:** test process crashes around child publication
   and additional competing decisions; require no duplicate privileged action or
   child task, and qualify the browser recovery controls.
2. **Longer agent runs:** exercise recurrence, checkpoint/history retention and
   remaining worker-restart boundaries while preserving usage limits and recovery.
3. **Agent administration:** qualify browser review/status, then bounded applying
   refresh and interrupted jobs on a disposable target.
4. **Storage failure handling:** check hard-mounted network storage and stalled
   scrub preflight, then Btrfs diagnostic integration and native scrub scheduling.
5. **Recovery and developer workflows:** restore off-host backups and verify
   application data; test real GitHub mirror permissions and additional deployment
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
