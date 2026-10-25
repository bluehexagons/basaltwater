# Test surface and lab coverage

Updated 2026-10-25. This is a living inventory of development testing, including
the single-node Debian/Proxmox lab. It records selected workflows and remaining
gaps; it does not pass the [release qualification gate](../release-qualification.md).
Results on an older revision remain historical evidence until the affected
workflow is repeated. A passed CI run is separate from a live result.

Current lab limitation: ordinary I/O exposed HDD media read errors on
2026-10-25. With operator acceptance of complete lab data loss, the HDD pool
is available for deliberate disposable failure tests. The affected agent guest
still failed basic execution after a controlled resume and is paused again.
Ordinary feature qualification needs healthy storage; historical results below
do not establish the failed drive's current readability (E23, E24).

## Size and counting rules

At `9d39e008`, the declared surface contains **15 setup profiles, 49 top-level
commands, 301 command paths and 522 distinct long-option spellings**. Regenerate
the complete profile, command, alias and option inventory from any checkout:

```bash
.venv/bin/python scripts/inventory_test_surface.py > /tmp/basaltwater-surface.json
```

Command paths include groups and leaves, exclude the root, and collapse command
aliases. Long options are deduplicated by spelling across all parsers, including
option aliases; their meaning can differ between commands. Profile and option
choices, positional values, shell actions, interactive prompts, HTTP routes,
external integrations and combinations add further behavior. These counts are
an inventory denominator, not a percentage of behavior tested.

Test independent options separately and select meaningful interactions: profile
and machine capability, fresh setup and populated rerun, enable/change/disable,
reboot, invalid input, permissions, interrupted work, rollback, and uncertain
remote completion. Do not treat every profile/option Cartesian product as either
supported or necessary. Record the exact tested values for each selected case.

## Setup profile coverage

“Live slice” means the named workflow ran on a real disposable target. It does
not qualify every default or optional feature of that profile. “Not recorded”
means this lab has no retained live evidence; it does not imply absent unit tests
or that the feature has never been used elsewhere.

| Profile | Retained lab coverage | Remaining boundary |
| --- | --- | --- |
| `server_lite` | Live slice: Debian 13 QEMU and unprivileged LXC, setup/reruns/reboots, declared disks, selected tools and a retained app-server target | Other feature combinations, distro versions and failure points |
| `server_web` | Live slice: Debian 13 QEMU, separate application data disks, Gogs, HomeBox, CI build/app roles and manifest service deployment | Public certificate lifecycle, other applications, complete browser/update recovery |
| `server_proxmox` | Live slice: single physical PVE 9 node, storage, setup reruns, panel and selected maintenance checks | Multi-node locking, HA/Ceph, interrupted upgrades and guest evacuation |
| `workstation_desktop` | Not recorded | Desktop install, hardware/session/input/recovery |
| `pc_dev` | Not recorded | Complete developer workstation setup |
| `workstation_dev` | Not recorded | Desktop and development-tool composition |
| `agent_workstation` | Not recorded | Complete agent and desktop lifecycle |
| `agent_cachyos` | Not recorded | Native CachyOS installation, desktop consent and recovery |
| `agent_code_vm` | Not recorded | Complete profile and provider/browser enrollment |
| `control_plane` | Not recorded | Controller bootstrap, credentials and fleet operations |
| `agent_vm` | Live slice: clean Debian 13 QEMU setup, selected Python/container/debug/LFS tools, independent privilege approvals, populated reruns and reboot; subsequent separate Codex enrollment, agent panel and bounded Luna tasks | Other providers, browser/pairing enrollment, maintenance enrollment and other optional combinations |
| `server_wsl` | Not recorded | Native Windows/WSL lifecycle |
| `server_dev` | Not recorded | Complete profile, services and tool composition |
| `server_github_runner` | Not recorded | Registration, real jobs, removal and failure recovery |
| `custom_steps` | Not recorded as a complete profile | Selected-step composition and inline failure behavior |

Machine selection is another dimension: QEMU `vm`, Proxmox `hardware` and
non-nested `unprivileged` LXC have selected live evidence. This is not coverage
of all seven accepted `--machine` values, every `auto` detection branch, or all
capability skips.

## Workflow ledger

**Live slice** records checked success only within its description. **Partial**
also calls out a known limitation or missing end-to-end boundary. **Not run** is
an explicit remaining lab case. Evidence keys identify private operator records
in the evidence index below; raw logs and host inventories stay outside Git.

| ID | Surface | Retained result and limits | Evidence |
| --- | --- | --- | --- |
| PVE-BOOT | QEMU provision and identity | Live slice: pinned Debian image, static networking, QGA/SSH, locked passwords, cloud-init guard before snippet removal; identity stable on reboot | E1, E7 |
| PVE-LXC | Unprivileged containers | Partial: provision, stopped recovery, grow, rollback and replacement restore passed; CRIU suspend failed with nested UTS namespace and remains unqualified | E2 |
| PVE-CTL | VM control and inspection | Live slice: start/shutdown/reboot, pause/resume, autostart/delays, status/stats/health and JSON/error output | E1, E6, E7 |
| PVE-DISK | Disk and resource flags | Live slice: root/data growth, ext4/XFS, SSD/discard/backup flags, CPU, balloon minimum/shares, additive disk and swap reruns; block and directory-backed import | E1, E4, E8 |
| PVE-COLD | [HDD parking](../operations/proxmox-hdd-parking.md) | Partial: verified stopped-guest backup, full-clone root/cloud-init move from thin SSD to directory-backed HDD, native task outcomes, boot/identity/file checks, saved storage adoption and unchanged-setup wake from stopped; stale SSD declarations rejected. Reverse moves, linked clones, snapshot history and EFI/TPM layouts remain open | E15 |
| PVE-SNAP | Snapshots | Live slice: QEMU/LXC and native thin linked-clone rollback, content and identity checks; snapshot removal | E1, E2, E7 |
| PVE-BACKUP | VM archives and restore | Partial: QEMU/LXC snapshot/zstd backup and selected same-host native restores checked; gracefully stopped Git-service archive restored to an unused VMID on HDD with NIC disconnected before first boot, followed by application and reboot checks. No Basaltwater restore CLI, off-host copy or retention qualification | E1, E2, E8, E16 |
| PVE-ORPHAN | Orphan deletion safety | Partial: actual referenced local files/LVs/storage aliases refused; selected disposable orphan removed; remote-node/RBD/VirtioFS cases have mocked safeguards | E3 |
| PVE-TEMPLATE | Vendor templates and clones | Partial: immutable never-booted Debian template, full and linked native thin clones, unique identities and stable reboot; no production template/clone CLI or retirement test | E4, E7 |
| PVE-HOST | Host storage and maintenance | Partial: storage reboot persistence, passive thin-capacity audit, services and maintenance guards; no interrupted host upgrade or reboot with running guests | E1, E3, E8 |
| NET-ACL | Guest firewall and trust | Partial: source allowlists, node excluded/controller allowed, custom web port, strict SSH and private CA; selected rejection paths checked | E5, E6 |
| NET-ISOLATION | Lab management isolation | Not run as an isolated-network qualification; the physical bridge/subnet permits guest access to management ports | E1, E2 |
| APP-GOGS | Git hosting | Live slice: HTTPS/SSH clone/push, LFS digest, server ACL rejection, forced-command restrictions, populated rerun and reboot; same-host HDD recovery of SQLite, private repository and LFS, authenticated HTTPS/fresh clone/download/hash before and after reboot. Native probes reject missing/empty databases, an unbound direct HTTP port and HTTP-unready binary recovery. Off-host recovery, migration rollback, restored SSH transfers and attachments remain open | E6, E15, E16 |
| APP-HOMEBOX | Asset management | Partial: login/API, entity/attachment digest, native backup/restore, traversal rejection, disable/re-enable, mounted data and reboot; browser/WebSocket and upgrade rollback remain open | E5 |
| DEV-GIT | Managed Git client | Live slice: workspace with spaces, scoped CA/credentials, read/write server ACL, password rotation, removal/re-enable and reboot; mirror configuration dry-run preservation and clean reruns, multiple fetch/push URL repair with fetch rules preserved, inherited-destination guards and conflicting LFS-setting refusal. Combined/separate pushes to authenticated Gogs and a local bare primary, cold-cache clones from both and LFS payload hashes passed. Controlled unavailable endpoints exposed partial Git updates; observed refs converged after deliberate recovery. Actual GitHub transport and permission behavior remain open | E6, E21, E22 |
| DEV-TOOLS | Optional CLI tools | Partial: `--debug-tools`, `--container-tools`, Git LFS, busybox-static and selected extra packages; GDB/strace/Valgrind/Ninja runtime checks, ccache statistics only; credential-free agent CLI versions, offline uv/Python environment and rootless Podman initialization checked | E5, E6, E7, E19 |
| DEV-CONTAINER | Rootless container lifecycle | Live slice: native image/build cleanup, keep/reference protection, active-work deferral, local-engine restriction, default pasta DNS/HTTP, loopback port publication and failed-build cleanup; cold namespace-helper denial under maintenance restrictions reproduced, with a specific security deferral and conditional retry after normal user initialization | E9, E7, E15 |
| DEV-SKILLS | Skill delivery and output capture | Live slice: idempotence, interrupted atomic replacement, cross-version retry, edited-file protection; bounded output, exit status and process-group cleanup | E9 |
| PANEL-SETUP | Proxmox panel lifecycle | Live slice: setup/rerun/removal/reinstall, private CA/auth persistence, native PVE isolation and firewall preservation | E10 |
| PANEL-UI | Panel browser behavior | Partial: Chromium/Firefox first-party and synthetic Guacamole suite; real agent dashboard/forms, readiness, history/receipts, inbox archive/restore, CSRF rejection and narrow layout in Chromium with strict private-CA trust. Anonymous Basic Auth challenges no longer count toward bans; submitted invalid credentials still do. Native PVE console, Firefox ESR and the collaborative preview client's private-CA trust remain unqualified | E10, E20 |
| PANEL-JOBS | Real timers and journals | Live slice: failure then recovery, filtering/search/escaping/redaction, entry/byte bounds, partial results and historical views after removal | E11 |
| PANEL-PVE | Proxmox inventory snapshot | Partial: real cached API reads, sanitized export under the collector's systemd restrictions, panel-group read permissions and production rendering; primary-group IPC failure reproduced and fixed. The new collector's installed timer, full HTTP/browser path and interrupted teardown remain separate qualification cases | E14 |
| STO-JOBS | Path mirrors and PAR2 recovery | Live slice: small-file mirrors/PAR2, damage preservation and targeted recovery, unsafe-input rejection, populated rerun/reboot; missing endpoints block dependent jobs while independent jobs continue; absent nested mounts preserve backup subtrees; private mount views survive controlled source/destination detachment at endpoints and nested paths; native QEMU/LXC rsync and denied-namespace refusal checked; worker-discovered exclusions reach warning summaries and hold completion cadence until recovery. An unavailable CIFS source at launch hit a shortened test deadline without losing existing mirror files and synchronized after recovery. Real power loss and mid-transfer network-filesystem failure remain open | E12, E13, E14 |
| STO-SMB | Samba server/client | Live slice: encrypted authenticated SMB3.1.1 read/write/rename/delete, readonly/user/anonymous rejection, fast access change/removal preserving data, TCP445-only reconciliation, headless CIFS helpers, failed-automount repair, service-user access, idle reactivation and reboot; bounded server-service outage and native reconnect without manual remount. Windows/macOS clients, silent network blackholes and interrupted writes remain open | E13, E14 |
| STO-SYNCTHING | Syncthing | Live slice: direct two-device exchange, disconnected edits, concurrent edit conflicts retaining both versions, remote replacement/deletion and archived-version restoration to both peers; populated reruns, reboot and removal/re-enable preserving identity and folders; private-CA HTTPS/API authentication and credential rotation; injected generation/unit/policy/interruption failures restore the prior login and active service; runtime GUI folders in temporary paths are refused while approved storage works; unsafe-root and inaccessible-ancestor rejection. Browser GUI and relays/discovery remain open; staggered history coalesces versions within retention intervals | E13, E14 |
| SYS-USER | Target-user rename | Partial: separate/default administrator, overlapping login names, moved/retained home, UID/GID/groups/subordinate IDs, managed units, linger/cron, strict SSH/sudo and cache reconciliation; native partial-cutover recovery and final-verification retry without replay, followed by reboot and populated setup rerun checks. Initialized rootless storage blocks home moves; retained-home rename preserves Podman health. Later-phase identity drift and overlapping home-path retries have mocked regressions. Arbitrary application-state migration, all interruption points and native SMB mount migration remain unqualified | E17 |
| SYS-PRIV | Privileged executor | Partial: native independent HTTPS authentication/decisions, exact once-only commands, safe failure reporting, denial/cancellation, stale decision and policy-change rejection; lost-response inspection, pending expiry and interrupted execution without replay; loaded-unit shutdown despite missing fragments; populated rerun, credential rotation, removal/re-enable, approved service restart and observed reboot. Injected publication/activation/readiness/interruption failures restore files, credentials, permissions and prior service state. Strict-CA Chromium separately approves registered and exact-command service prerequisites before task launch; Block cancels an unclaimed request. All crash/concurrency points and manual recovery from retained guards remain open | E19, E21 |
| DEPLOY | Application deployment | Partial: signed loopback webhook through Nginx, strict HTTPS Git, separate build/app guests and SSH artifact publication; signature/body/branch/delete/ping checks, body replay suppression, failed-build and unreachable-commit preservation, invalid-configuration queue retention/recovery. Direct manifest service activation/update, unhealthy-release rollback, build failure without service interruption, persistent SQLite data and private backup integrity/retention, populated role-preserving reruns and separate guest reboots passed. Native queue-selector publication recovery and acceptance order checked; filesystem I/O error paths have mocked regressions. Public sender/Tunnel/ACME, browser behavior, Node/Godot/multi-component publication, database migration recovery and power-loss boundaries remain open | E18 |
| INTEGRATIONS | External services | Partial: Proxmox notifications on loopback and account-holder-approved, separate target Codex device enrollment with live Luna calls; external alert receivers, public DNS/ACME/Cloudflare, other provider sign-ins and publishing accounts remain open | E2, E20 |
| DESKTOP | XRDP and desktop consent | Not run: full native desktop/client/session/interruption/permission lifecycle | — |
| PLATFORMS | CachyOS/WSL/Windows | Not run on their native platforms; Debian mocked tests do not qualify them | — |
| RUNNER | GitHub runners | Not run: external registration, actual job and removal on Debian or Windows | — |
| AGENT | Complete agent profile | Partial: clean `agent_vm`, strict SSH, no direct sudo, private decision credentials, unrelated-UID rejection, managed skills, selected runtime tools, setup reruns and broker lifecycle through reboot; subsequent separate Codex enrollment, main panel installation/rerun and retained authentication after another reboot. Browser/pairing enrollment, long administration jobs, updates and other agent profiles remain open | E19, E20 |
| AGENT-TASK | Independent Codex tasks | Partial: live Luna/high inspection, Luna/xhigh persistent worktree repair and exact conversation resume, per-run usage accounting, post-run token-budget pause, read-only and out-of-scope write denial, explicit additional write grant, observed child-process cancellation, prerequisite blocking, event deduplication, future one-shot pause, read-only MCP tools and idle service/VM restart persistence. Native CLI/panel guards reject HOME/protected aliases; old unsafe tasks remain visible and fail before provider launch. Draft repair works; established source bindings require replacement tasks. Killing the active worker removes its observed child; restart records interruption and unknown usage without replay. An inert Luna follow-up waits for two exact service approvals, launches one reviewed child despite repeated submission, and verifies counters without repeating the actions; another proposal is blocked and its pending request cancelled. Automated failure-injection checks additionally cover interrupted decision publication, concurrent approval/block, same-child recovery without service replay, and refusal to recreate removed or reclaimed children. Those new paths have not been repeated live. Other crash/claim races, long goals and models remain open | E20, E21, E23 |
| PUBLISH | Godot and game publishing | Not run: export, browser rendering, upload and account-bound operations | — |
| RELEASE | Installer and candidate | Not run for the exact release candidate; source/wheel CI is recorded separately | — |

## Evidence index and maintenance

The operator's private project note `lab-testing-coverage` maps these stable keys
to report paths, exact revisions, environment, commands/results and artifacts.
It is account-local; contributors without that store should treat these as
reviewed historical summaries and retain their own evidence for new runs:

```bash
basaltw agent memory get lab-testing-coverage --project . --json
```

| Key | Date | Evidence scope / observed revision |
| --- | --- | --- |
| E1 | 2026-10-23 | Initial physical storage and QEMU lifecycle, through `ea501c01` |
| E2 | 2026-10-23 | LXC, confinement and loopback notification, `c72519f0` |
| E3 | 2026-10-23 | Local orphan-deletion guards and passive audit, through `de15726b` |
| E4 | 2026-10-23/24 | Additive disks/swap and initial template experiment, through `fa8c8b4d` |
| E5 | 2026-10-24 | Native HomeBox application and flags, `bbeece82` |
| E6 | 2026-10-24 | Gogs/client credentials/lifecycle/JSON, `04c92899` and `f1ab5e5c` |
| E7 | 2026-10-24 | Templates, tool/network/runtime flags and global workspace, through `5265074b` |
| E8 | 2026-10-24 | Resize failure/uncertainty and file-backed import/restore, `53051640` and `9c132597` |
| E9 | 2026-10-24 | Native rootless cleanup, skill/output lifecycle and root CI fixture, `371efe99` |
| E10 | 2026-10-23 | Panel install/auth/TLS and browser checks, through `35c50a8b` |
| E11 | 2026-10-23 | Real job/journal qualification, `189fbeaa` |
| E12 | 2026-10-24 | Small-file mirrors/PAR2 and security APT-lock wait, `f9cd844d` |
| E13 | 2026-10-24 | Samba, Syncthing and native rsync mount lifetime; final populated reruns at `b01be24a` and mount-retry checks through `e24658a4`; reboot checks at `d1b15ff7` plus `bbdbf163` rsync |
| E14 | 2026-10-24 | CIFS service-outage deadline/reconnect, Syncthing concurrent edits and archived-version restoration at `945b4012`; recovery/sandbox fixes, credential rotation, populated reruns and separate endpoint reboots at `00127f88`; incoming Proxmox panel inventory native collection/export/render and IPC-group regression |
| E15 | 2026-10-24 | Native HDD parking, storage-declaration verification/adoption, stopped-guest setup wake and retained Git/LFS regression checks; controller source `78789105`; cold Podman maintenance diagnostics through `e1340e95` |
| E16 | 2026-10-24 | Consistent stopped-guest Gogs backup from controller `477e4619`, isolated same-host HDD restore preserving the E6 runtime, fresh HTTPS Git/LFS recovery and reboot; exact health/recovery functions from `9a8ca7f7` exercised in memory against native services, SQLite and controlled port fixtures |
| E17 | 2026-10-24 | Disposable Debian user-rename qualification from `5a13c802`; native isolated-Python startup, account/session race and partial-home recovery through `504eb459`, container-store move rejection at `06938d21`, authenticated polling at `b126b3e3`, retained-home rename and reboot. Home-rewrite idempotence and pre-activation identity validation fixed at `88baf054`; both-username storage expansion and malformed authenticated-status regressions fixed at `11e17aef`. Final stopped-guest setup wake, retained-home rename, identity/container checks and reboot passed at that revision; combined CI recorded separately |
| E18 | 2026-10-24 | Disposable Debian CI/build/app and direct manifest service checks from `d737b241`; native rerun ownership damage, lost role flags, private-state traversal, deploy SSH group exclusion and systemd broker capability loss reproduced. Production patches through `883418f0` preserve data, roles, secret privacy and unrelated ACL restrictions; signed static delivery/failure/recovery and separate guest reboots passed. Queue recovery selector uses real SQLite/files with controlled publication failure; remaining lookup/read error paths are mocked |
| E19 | 2026-10-24 | Clean credential-free Debian `agent_vm` repeat at `483378a1`, with installed-source hashes, populated reruns, tool runtimes, native approval/rotation/removal/service restart and reboot. Pinned baseline reproduces missing-fragment authority and failed-rerun shutdown; lifecycle fixes at `57ca120b` pass native injected failures. CIDR origin/serialization and first-install shared-parent rollback fixed through `483378a1`; the shared-parent case has mocked regression evidence. Initial prototype setup excluded from exact-revision qualification; its unchanged request runtime has scoped lost-response/interrupted-worker evidence |
| E20 | 2026-10-24 | Separate target Codex device enrollment and bounded Luna/high/xhigh execution; strict-CA Chromium agent panel checks. Anonymous challenge ban regression fixed at `ff6916ce`, production rerun and native log/jail checks at `b95491ab`. Workspace boundary guards at `04f3023f`, with migration guidance corrected at `788d280c`; installed runtime hashes, negative CLI/panel submissions, legacy prelaunch refusal/draft repair, actual allowed/denied writes and reboot persistence checked at that combined revision. Automated suite/CI results and fixture corrections are recorded separately from live evidence |
| E21 | 2026-10-24 | Independent worker SIGKILL during an observed disposable Luna command, child cleanup, native restart and interrupted/unknown-usage pause without replay; inert live Luna proposals and strict-CA Chromium operator review, registered/exact-command restart approvals, counter checks, one-attempt launch deduplication and pending-request cancellation. Ambiguous broker responses retain the retry guard after `cde7dcc6`. A synthetic receipt with one real accepted request and a simulated generic socket response blocked both retry controls and task launch; the known original was separately cancelled. Native preacceptance rejection persisted no request. Response identity checks at `74a6cbe9` reject missing, malformed and mismatched request IDs before follow-up creation; temporary socket/context probes passed after normal populated refresh at `bbe80ed6`, with matching native status and installed hashes. Supplemental local mirror configuration probes exposed a Git LFS status write during dry-run; removal at `896737fd` passed native dry-run, push-mode, rerun and fresh-local-clone endpoint checks after populated refresh at `9031892f`. No remote mirror transfer was attempted. Actual database-commit and media failures remain mocked |
| E22 | 2026-10-25 | Exact repository helper through `0a4cd415` executed in memory on the retained Debian client, with its other installed modules unchanged. Native Git LFS 3.6.1 checks repaired stale multi-value default/custom mirror push URLs and origin/mirror fetch URLs while preserving fetch refspecs, refused conflicting download/upload settings without file changes or value disclosure, and preserved matching settings, dry-run and clean reruns. Inherited fetch/push destinations and redirected config writes fail before actions; matching global, included and per-worktree remote definitions configure successfully. Combined/separate pushes at `56cdc0fa` and final-helper transfer/clone checks verified small LFS payload hashes through strict-CA authenticated Gogs. The primary used a repository-local URL rewrite to a local bare repository; no GitHub transport was attempted. Controlled LFS/primary/secondary endpoint failures at baseline `b2321cfa` checked both refs before recovery; a secondary failure left only the primary advanced, and explicit retries converged. Config-query error paths have mocked regression coverage |
| E23 | 2026-10-25 | Agent expiry/recovery preflight at `052352e2` encountered actual HDD read failures before new task execution. Kernel media errors and unreadable guest executables were observed; passive SMART attributes were collected without self-tests or scans. The affected guest was paused in RAM and the pool disabled for new operations; backups on that same drive remain unverified. Automated broker tests cover pending/approved expiry on restart, preserved successful outcomes without replay, and approved requests reaching their deadline before execution. Temporary task stores with mocked execution reproduced Block succeeding over an already queued child after a decision-write failure. Durable submission intent at `3ce3e565` makes recovery use the matching child and prevents false blocking; `0f6c2828` refuses recreation after completed-child deletion or automatic reclamation. Concurrency, inert draft recovery and missing-child uncertainty have automated regressions. Panel recovery at `2f258e8f` offers Finish saved submission for both current and previously reviewed legacy proposals, checked through rendering and the HTTP handler with temporary state. New live expiry/concurrency qualification remains deferred |
| E24 | 2026-10-25 | Operator accepts loss of all lab-drive data and permits continued use of the known-failing HDD for disposable experiments. Native storage enable and affected-guest resume succeeded; shell execution still returned I/O error and SSH reset, so the guest was paused again. Passive pending/reallocated/uncorrectable/CRC counters were unchanged across the retry. A separate disposable 1 MiB host file passed write, flush and hash-checked readback and was removed. That one allocation does not establish readability elsewhere. This is storage-failure evidence, not a successful agent-feature test or device-wide health qualification |

For each new batch, update affected rows and the private evidence mapping.
Record preconditions, exact source/runtime revisions, expected/observed behavior,
failure injection, cleanup or retained assets, and unresolved gaps. Mark stale
results explicitly when a relevant implementation changes. Never infer a flag
passed because parsing accepted it or its package was installed. Keep secrets,
private hostnames, addresses and raw logs outside this document.

Automated ownership is discoverable through `python3 run_tests.py --list-suites`;
use the contributor guide's [checks](../agents/contributing/README.md#standard-checks)
for affected suites and `make check-ci` for broad validation. Browser runs are
separate. Record exact CI revisions and skipped cases in private evidence; unit
branch coverage is not a live-feature coverage percentage.

## Next useful batches

Next valuable cases are additional worker crash/claim and receipt-publication
boundaries, privileged follow-up expiry and concurrency, agent-profile options
and long administration jobs, additional deployment manifests and interruption
points, and actual GitHub transport and permissions for the mirror workflow.
Extend file-service testing to interrupted writes and silent network blackholes.
Extend rename testing to additional interruption points and software-specific
state; moving an account home is not a general application migration.
Off-host restore, external notification
delivery and native desktop qualification need their own explicit test boundary.
Multi-node migration/HA/Ceph need additional infrastructure. Hardware stress,
media endurance tests and disk-wide scans are excluded from this lab plan.
