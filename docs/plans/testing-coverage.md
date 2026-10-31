# Test coverage and next checks

Updated 2026-10-31. This is a current testing map, not a run log or the
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
paths and 537 distinct long-option spellings**, with aliases collapsed. These
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
| Shared Debian desktop | Complete clean Debian 13 `server_lite --desktop xfce --rdp` installation, populated rerun and reboot pass. Human-first and simultaneous RDP/two-peer starts share one Xorg/desktop. Certificate-pinned FreeRDP 3.15 keyboard input, bidirectional Unicode clipboard, resize and reconnect pass. Frontend stop/start and SSH-tunnel loss preserve session/window identity, exact application text and an independent service; normal logout preserves the user manager. Native capture/window checks cover client-list fallback, visibility/active/absence waits, identity guards and normal operations. First launch/wait succeeds from an unknown baseline without classifying matches as new; default-app opening and timeout receipts pass | With no application windows, native XFCE can omit both client lists: coverage remains unavailable and absence stays unknown. Further race timing, additional clients/desktops, desktop crash recovery, interrupted setup, legacy-session blocking, administrator rollback and the full release matrix remain unqualified |
| Setup security and resume recovery | Automated firewall capability and desktop/jail ordering checks. Complete clean Debian desktop setup activates SSH/XRDP jails; native probes also reject missing logs before restarting a running service. Real initramfs hook failures cover resume addition/replacement/removal, unchanged published images on failure, matching retries, private pending intent and subsequent no-ops | Runtime jail failures, process/power-loss recovery of boot images and hibernation remain separate qualification |
| Security event retention | Automated known/unknown boundary, error, state-write and restart checks. Native Debian 12/13 audit/journal fixtures cover rotated history, C-locale dates, removed files and durable gaps despite failed delivery. Packaged Fail2ban logrotate policies cover numeric/gzip history, rotation during collection, exact counts and corrupt archives. A live Debian monitor reports first-observation gaps, then succeeds and advances its cursor while retaining private gap history. Real SSH journal collection excludes successful public-key logins; automated cases retain explicit authentication failures | Rotation probes use fixture logs, journal import and logrotate without forcing real authentication failures or daemon rotation. Continuous timer/daemon behavior, interior loss, custom rotation policies and power-loss recovery remain separate qualification |
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
| Selected-builder delivery | Managed enrollment and hardened typed SSH; native static/Go and ready-SPA/Go-API flows, plus six unchanged homelab manifests with Node 24/26, shell builds, service arguments/environment, private SQLite backups and real STUN/UDP. HTTPS routes, the extra foodguide route and Cloudflare/SSL HTTP origins passed with target-to-builder access prohibited. A normal saved six-site deploy and actual target refresh with the builder powered off preserve SQLite state/backups; SSH-only trusted-source migration keeps web ingress public. Shared-domain neighbors, saved revision updates, retained reconciliation and idle reboot work without target compilers. Controlled truncated uploads and unhealthy candidates retain releases and recovery identities; selected APT-lock and child-stdout checks | Python static examples have automated checks. Public Tunnel/ACME, browser rendering, power loss and further activation/restart boundaries remain unqualified. Interpreter services and runtime secret bindings remain unsupported |
| Builder capacity and retention | Cold protected-copy capacity inspection preserves installed code/caches and retained evidence. Selected native/process checks cover settlement, root-local source/log pruning, successful-bundle release, exact-revision resumption and one-time byte credit. Same-job shared readers exclude deletion. Automated checks cover noninitializing job evidence, staged settlement recovery and lifecycle ledger headroom | No automatic collection, orphan release or replay-history retirement. Input/history slots remain occupied; physical space is not reserved. Further crash points and power loss remain open; persistent jobs are mutually trusted |
| Controller build recovery and history | Offline inspection distinguishes saved acknowledgements from unobserved remote outcomes. A pipe-synchronized SIGKILL after durable target completion but before controller acknowledgement preserves IDs/evidence: reentry refuses mutations, while identical target activation replay returns its cache without a callback. Explicit archival of a real completed six-site attempt preserves all 1,814 existing files and receipts, frees one active controller slot and supports exact-revision retry. Automated checks cover success proof, unfinished guards, lock ordering and normalized host identity | Transport and activation effects are mocked in that crash test. Archives still consume disk and free no remote history slots. No automatic resume/reset, power-loss or complete crash-boundary qualification |
| Combined build/CI host | On-demand builder, webhook executor and dedicated runner implementations exist separately | Role composition, shared capacity/draining, webhook artifact reuse, existing-runner conversion and mixed-role native/GitHub qualification are not implemented or qualified |
| Constrained development | Prepared 1 vCPU / 1 GiB Debian x86-64 guest without swap: offline checkout and installed-wheel version/help, installed Bash completion, the unchanged 513-test smoke selection and 168/28/13-test validation/network/atomic selections, with no skips. Automated nano search/edit/save of a 10 KiB file overlaps a focused run and verifies the saved bytes. A separate run retains 256 MiB while repeating focused tests, completion and editing. Selected latency, sampled memory/PSI and scratch budgets pass | OS/dependency preparation is excluded. Application HOME/cache starts empty; OS/Python read caches remain uncontrolled. Guest headroom and filesystem growth are sampled at 50 ms and can miss short peaks. Heavy/full-suite/browser workloads, larger concurrency fixtures and CachyOS/WSL/Windows resource floors remain separate qualification |
| Panels | Setup/auth/TLS, journals/timers, Chromium agent forms, synthetic Chromium/Firefox Guacamole checks and native cached Proxmox inventory export. Automated checks cover installation-aware navigation, diagnostics and optional-route guards | Native PVE console, Firefox ESR, inventory timer/full HTTP lifecycle, additional application browser flows and collaborative preview CA trust |
| Client CA enrollment | Native Arch userland runs generated HTTP/local-file scripts through POSIX sh and fish with real sudo and trust extraction: initially untrusted CA becomes trusted, identical reruns succeed, wrong fingerprints preserve trust before/after installation, local inputs survive and temporary files are removed. Automated tests cover generated platform variants and the script picker | Native probes run as container root, without interactive sudo or browser trust stores. Full CachyOS desktop, other native client platforms, TLS hostname/chain behavior and application-specific enrollment remain separate qualification |
| User rename | Identity/groups, moved or retained home, selected interruption recovery, rerun/reboot and initialized-container-store guards | More interruption points, software-specific state and native SMB mount migration; home moves are not general application migration |
| Other surfaces | Selected loopback notifications and external Codex enrollment | Other desktop profiles, kiosk/CachyOS/WSL/Windows, GitHub runner jobs, game publishing, external alerts/public DNS and exact-candidate installer qualification |

## Next useful checks

1. **Combined build/CI host:** implement role composition and shared capacity,
   then qualify existing-runner conversion, real allowed/denied GitHub jobs,
   webhook/on-demand concurrency, nested builds, draining, rerun and reboot.
   Use the [shared-host acceptance](cicd-manifest-reuse.md); do not infer this
   coverage from the qualified on-demand path.
2. **Build recovery:** target remaining worker/child teardown, storage exhaustion
   and lost-acknowledgement boundaries as shared adapters land. Preserve guards
   and evidence; automatic retention and generic resume are separate policy.
3. **Longer agent runs:** exercise longer provider-backed goals and remaining
   worker crash/restart boundaries while preserving usage limits and recovery.
4. **Agent administration:** test package-changing upgrades on a disposable target;
   extend interruption checks to other applying jobs.
5. **Storage failure handling:** check hard-mounted network storage, then Btrfs
   diagnostic integration and native scrub scheduling.
6. **Recovery and developer workflows:** restore backups onto another physical
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
