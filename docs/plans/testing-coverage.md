# Test surface and lab coverage

Updated 2026-10-24. This is a living inventory of development testing, including
the single-node Debian/Proxmox lab. It records selected workflows and remaining
gaps; it does not pass the [release qualification gate](../release-qualification.md).
Results on an older revision remain historical evidence until the affected
workflow is repeated. A passed CI run is separate from a live result.

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
| `server_lite` | Live slice: Debian 13 QEMU and unprivileged LXC, setup/reruns/reboots, declared disks and selected tools | Other feature combinations, distro versions and failure points |
| `server_web` | Live slice: Debian 13 QEMU, separate application data disks, Gogs and HomeBox | Public certificate lifecycle, other applications, complete browser/update recovery |
| `server_proxmox` | Live slice: single physical PVE 9 node, storage, setup reruns, panel and selected maintenance checks | Multi-node locking, HA/Ceph, interrupted upgrades and guest evacuation |
| `workstation_desktop` | Not recorded | Desktop install, hardware/session/input/recovery |
| `pc_dev` | Not recorded | Complete developer workstation setup |
| `workstation_dev` | Not recorded | Desktop and development-tool composition |
| `agent_workstation` | Not recorded | Complete agent and desktop lifecycle |
| `agent_cachyos` | Not recorded | Native CachyOS installation, desktop consent and recovery |
| `agent_code_vm` | Not recorded | Complete profile and provider/browser enrollment |
| `control_plane` | Not recorded | Controller bootstrap, credentials and fleet operations |
| `agent_vm` | Not recorded | Fresh profile, provider authentication, privileged executor |
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
| PVE-SNAP | Snapshots | Live slice: QEMU/LXC and native thin linked-clone rollback, content and identity checks; snapshot removal | E1, E2, E7 |
| PVE-BACKUP | VM archives and restore | Partial: QEMU/LXC snapshot/zstd backup and selected same-host native restores checked; no Basaltwater restore CLI, off-host copy or retention qualification | E1, E2, E8 |
| PVE-ORPHAN | Orphan deletion safety | Partial: actual referenced local files/LVs/storage aliases refused; selected disposable orphan removed; remote-node/RBD/VirtioFS cases have mocked safeguards | E3 |
| PVE-TEMPLATE | Vendor templates and clones | Partial: immutable never-booted Debian template, full and linked native thin clones, unique identities and stable reboot; no production template/clone CLI or retirement test | E4, E7 |
| PVE-HOST | Host storage and maintenance | Partial: storage reboot persistence, passive thin-capacity audit, services and maintenance guards; no interrupted host upgrade or reboot with running guests | E1, E3, E8 |
| NET-ACL | Guest firewall and trust | Partial: source allowlists, node excluded/controller allowed, custom web port, strict SSH and private CA; selected rejection paths checked | E5, E6 |
| NET-ISOLATION | Lab management isolation | Not run as an isolated-network qualification; the physical bridge/subnet permits guest access to management ports | E1, E2 |
| APP-GOGS | Git hosting | Live slice: HTTPS/SSH clone/push, LFS digest, server ACL rejection, forced-command restrictions, populated rerun and reboot; no Gogs application restore | E6 |
| APP-HOMEBOX | Asset management | Partial: login/API, entity/attachment digest, native backup/restore, traversal rejection, disable/re-enable, mounted data and reboot; browser/WebSocket and upgrade rollback remain open | E5 |
| DEV-GIT | Managed Git client | Live slice: workspace with spaces, scoped CA/credentials, read/write server ACL, password rotation, removal/re-enable and reboot | E6 |
| DEV-TOOLS | Optional CLI tools | Partial: `--debug-tools`, `--container-tools`, Git LFS, busybox-static and selected extra packages; GDB/strace/Valgrind/Ninja runtime checks, ccache statistics only | E5, E6, E7 |
| DEV-CONTAINER | Rootless container lifecycle | Live slice: native image/build cleanup, keep/reference protection, active-work deferral, local-engine restriction, default pasta DNS/HTTP, loopback port publication and failed-build cleanup | E9, E7 |
| DEV-SKILLS | Skill delivery and output capture | Live slice: idempotence, interrupted atomic replacement, cross-version retry, edited-file protection; bounded output, exit status and process-group cleanup | E9 |
| PANEL-SETUP | Proxmox panel lifecycle | Live slice: setup/rerun/removal/reinstall, private CA/auth persistence, native PVE isolation and firewall preservation | E10 |
| PANEL-UI | Panel browser behavior | Partial: Chromium/Firefox first-party and synthetic Guacamole suite; selected real-host pages; no native PVE console or Firefox ESR qualification | E10 |
| PANEL-JOBS | Real timers and journals | Live slice: failure then recovery, filtering/search/escaping/redaction, entry/byte bounds, partial results and historical views after removal | E11 |
| STO-JOBS | Path mirrors and PAR2 recovery | Live slice: small-file `--sync`/`--backup` plus `--scrub` parity, detected damage preserved by mirrors, targeted repair/restore/accept, bad restore and symlink/hardlink rejection, unreadable-findings fallback and corrupt-cadence refusal; data/parity/schedule retained through setup rerun and reboot; missing mounts and real power loss remain open | E12 |
| STO-SMB | Samba server/client | Not run: authenticated SMB3, service-user access, mount ordering/failure and reruns | — |
| STO-SYNCTHING | Syncthing | Not run: two-device exchange, identity, removal/re-enable and disconnected recovery | — |
| SYS-USER | Target-user rename | Not run: separate administrator, UID/home/units, interruption/resume | — |
| SYS-PRIV | Privileged executor | Not run end to end on a disposable target: review/TLS lifecycle, lost response, worker interruption and recovery | — |
| DEPLOY | Application deployment | Not run: signed webhook, healthy delivery and unhealthy activation rollback | — |
| INTEGRATIONS | External services | Partial: Proxmox notifications tested on loopback; external alert receivers, public DNS/ACME/Cloudflare, provider sign-in and publishing accounts remain open | E2 |
| DESKTOP | XRDP and desktop consent | Not run: full native desktop/client/session/interruption/permission lifecycle | — |
| PLATFORMS | CachyOS/WSL/Windows | Not run on their native platforms; Debian mocked tests do not qualify them | — |
| RUNNER | GitHub runners | Not run: external registration, actual job and removal on Debian or Windows | — |
| AGENT | Complete agent profile | Not run: clean install, login, browser/pairing, updates/reboot and broker lifecycle as one workflow | — |
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

Test missing/nested storage mounts, then Samba and Syncthing with independent
disposable endpoints. Additional valuable cases are
managed-user rename, a local signed deployment/rollback, and fresh agent-profile
setup without account-bound credentials. Off-host restore, external notification
delivery and native desktop qualification need their own explicit test boundary.
Multi-node migration/HA/Ceph need additional infrastructure. Hardware stress,
media endurance tests and disk-wide scans are excluded from this lab plan.
