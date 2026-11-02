# Debian and Proxmox testing status

**Evidence reconciled through 2026-11-02 UTC.** Selected live workflows cover Debian
12/13 guests and a physical Proxmox host; they do not establish a pass on every
machine type. Status applies only to the workflow described in each row; see
the [legend](README.md#status-legend).
“Component” includes native probes or controlled fixtures; “Automated” is
repository test coverage. Consult the
[detailed coverage map](../plans/testing-coverage.md) for exact observations
and the [release matrix](../release-qualification.md#stable-qualification-matrix)
for candidate-specific status.
The [dated evidence summaries](evidence.md) identify tested revisions where known;
older rows retain their earlier scope. The owner's newly reported setup rerun
has no supplied revision and indicates basic functionality, with features not
exhaustively tested. The current Shares & Data qualification below is separate
agent-run evidence.

Debian is the project's officially supported distribution on physical machines,
VMs and unprivileged Proxmox LXC where each profile permits. Selected runs
covered `server_lite`, `server_web`, `server_proxmox` and `agent_vm`, not every
profile on every machine type. The owner additionally reports basic setup reruns
of `server_lite`, `server_web`, `server_proxmox`, `agent_code_vm` and
`server_github_runner`; this does not extend the earlier lab results to every
selected option. The [platform index](features.md) distinguishes availability
from test status.

## Debian guests and services

| Feature | Status | Evidence | Major remaining boundary |
| --- | --- | --- | --- |
| Owner-reported server and coding-VM reruns | 🟡 **Partial** | 2026-11-01 report: selected web, storage/service, `agent_code_vm` and runner setup commands; starting an initially stopped coding VM explicitly succeeded | Basic setup only; individual tools/services, registration/jobs and recovery were not separately qualified; see [selections](evidence.md#owner-reported-stack-setup-rerun) |
| Debian server and agent setup | 🟢 **Labbed** | Fresh setup, rerun and reboot | Other profiles, option combinations and exact candidate |
| Shared XFCE/XRDP desktop | 🟢 **Labbed** | Setup, concurrent starts, input, clipboard and reconnect | Additional clients, crash/interruption and release matrix |
| Browser installation | 🟡 **Partial** | Debian package failure, repair and retry; automated selection | Graphical startup and other installers/platforms |
| Firewall, jails and initramfs | 🟢 **Labbed** | Selected setup and component failure/retry | Runtime failures, power loss and hibernation |
| AppArmor | 🟡 **Partial** | Packaged service failures, repair and policy preservation | Full setup, boot transitions and interrupted reload |
| Compressed swap | 🟡 **Partial** | Real zram devices and component failure/recovery | Native zswap controls, reboot and pressure |
| Security event retention | 🟡 **Partial** | Logs, rotation, gaps and live monitor components | Continuous daemon behavior and power loss |
| Agent tools and tasks | 🟢 **Labbed** | Selected enrollment, managed skills, task/goal flows | Other providers and longer interrupted runs |
| Approval and admin jobs | 🟢 **Labbed** | Selected approval/refresh; real leaf-package upgrade, separately approved no-op, native index failure/lock wait and task/service preservation. 2026-11-02 owned signed package adds real APT/dpkg conffile conflict, daemon restart and no-op without another restart; see [scope](evidence.md#admin-service-restart-and-conffile-conflict) | Distribution service packages, kernel updates, failing maintainer hooks and uncertain/power-loss boundaries |
| mDNS and automatic updates | 🟡 **Partial** | Packaged services and timer handoff failure/retry components | Full setup, remote discovery and scheduled upgrades |
| Sync and PAR2 | 🟢 **Labbed** | Selected mirror, mount guards and repair; controlled failures | Hard-mounted network storage and power loss |
| Btrfs | 🟢 **Labbed** | Single-device setup, rerun, reboot and read-only native scrub | Diagnostic integration and scheduled native scrub; multi-device/subvolume support needs design, and single-copy data cannot self-repair |
| Samba and Syncthing | 🟢 **Labbed** | SMB3 access/reconnect and two-peer synchronization | Other client OSs, hard mounts and relay/discovery |
| Shares & Data management | 🟢 **Labbed** | 2026-11-01: installed HTTPS, independent approvals and root workers; account/share and mirror lifecycles, catalog scope/pagination/drafts, verify/restore, successful missing-file repair with adequate parity, acceptance and independent scan, race refusal, setup exclusion and guest reboot persistence. Selected installed Chromium/Firefox navigation/forms/layout pass with isolated CA trust; Unicode search truncation fixed. 2026-11-02 UTC: selected full `agent_vm` setup, public refresh, reconciled populated controller rerun and reboot preserve approved declarations, controls, identities, auth and bytes. See [revision boundaries](evidence.md#shares-and-data) | Shared preview trust, native Firefox ESR, other full-profile combinations and interruption/power-loss boundaries remain open |
| Gogs and mirrors | 🟢 **Labbed** | Git/LFS, rerun, reboot and same-host restore | GitHub transport and off-host recovery |
| HomeBox | 🟢 **Labbed** | Loopback API, upgrade, restore, reboot and re-enable | Browser, public TLS, replacement host and rollback |
| Web deployment | 🟢 **Labbed** | Selected Go, Node and SPA/API workflows, rollback and same-host recovery | Public tunnel/ACME, off-host restore and power loss |
| Selected builder | 🟢 **Labbed** | Enrollment, delivery, refresh and multi-application workflows | Public ingress, more failure boundaries and unsupported interpreter services |
| Builder retention/recovery | 🟡 **Partial** | Component and automated settlement, pruning and lost-acknowledgement guards | Further crash/power-loss boundaries; no automatic collection |
| GitHub runner | 🟢 **Labbed** | Earlier tokenless setup, rerun and reboot; owner also reports a basic setup rerun selecting `--ci-register-with-gh` | Registered state was not separately reported; end-to-end registration, real jobs and cancellation remain open |
| Combined build/CI host | 🔵 **In progress** | Separate roles implemented | Composition and shared capacity not yet implemented or qualified |
| Web panels | 🟢 **Labbed** | Selected setup/auth and component lifecycle/recovery; browser fixtures | Full removal/interruption, additional browsers and native console |
| Managed user rename | 🟢 **Labbed** | Selected identity, home and recovery cases | More interruption points and application-specific migration |
| Resource-constrained development | 🟢 **Labbed** | Selected 1 vCPU / 1 GiB Debian workload | Full suite, browser and other platforms at that floor |
| Repository checks | 🟡 **Partial** | [Proxmox-fix revision: 9,243 tests, six skips, 82% branch-aware coverage](evidence.md#automated-validation), 2026-10-31; full Debian and Node compatibility CI passed | Historical revision only; browser and physical hardware are separate |
| Desktop development and visual checks | 🟡 **Partial** | Debian 13 software GLX, small Blender CPU render and XRDP edit/save fixture | Native GPU/display, audio, Wayland and full project images |
| T3 Code and provider workflows | 🟡 **Partial** | Selected enrollment and task checks; additional T3 components | Exact-candidate install, pairing, migration and complete live workflow |
| Game publishing and external alerts | 🟡 **Partial** | Automated checks or selected observations | Full publishing and external-provider paths |

## Proxmox host and guest operations

These host features are separate from Debian guest profile results. Earlier
detailed host checks cover one physical node; the owner also reports two basic
`server_proxmox` setup reruns. Neither establishes cluster qualification.

| Feature | Status | Evidence | Major remaining boundary |
| --- | --- | --- | --- |
| Owner-reported host setup reruns | 🟡 **Partial** | Two commands with LAN allowlists and restart/wait selections, including panel/TLS and webhook variants | Basic setup only; panel features, actual restart occurrence, preservation and failure recovery were not separately reported |
| Setup repair and management CLI | 🟢 **Labbed** | Saved public patch completed 21 steps and recovered an interrupted panel operation; installed `basaltw --help`, update preview and busy-lock refusal passed | Fresh complete host installation, package-changing upgrade and interrupted host upgrade remain unqualified |
| Proxmox panel identity and authentication | 🟢 **Labbed** | Automated root payload regression; native isolated Nginx accepted generated root auth and rejected another user/anonymous access; actual panel accepted the operator credential over verified TLS on 2026-11-01 | Isolated auth is component evidence; public password-replacement flow and interrupted auth replacement still need native qualification |
| Proxmox cached inventory and exporter | 🟢 **Labbed** | 2026-11-01: native inventory comparison, two unattended exports with future rearm, host-local verified HTTPS/filter/auth checks and actual worker namespace isolation. Owned current-component units qualify interruption, retained snapshot, recovery and removal; see [scope](evidence.md#proxmox-inventory-and-exporter) | Intermittent controller partial-body timeouts reproduced with TCP retransmissions remain unresolved; interactive browser/native console, multi-node and public full-host setup/teardown remain unqualified |
| Guarded host maintenance | 🟡 **Partial** | Installed updater and cleanup succeeded with no package upgrades available; update preview and maintenance-lock exclusion passed | No package-changing transaction or Admin-triggered power action was exercised |
| VM lifecycle | 🟢 **Labbed** | Provision, clone, snapshot, backup and same-host restore | Off-host recovery, multi-node and EFI/TPM variants |
| LXC suspend | 🔴 **Failed** | Live CRIU attempt failed | Suspend remains unqualified |
| Storage and networking guards | 🟢 **Labbed** | Selected capacity, mount and access checks; automated tests | HA/Ceph, management isolation and interrupted upgrades |
| USB SSD test-pool activation | 🟢 **Labbed** | 2026-11-01: existing ext4 mounted by UUID, pool enabled with mount-point guard, temporary 1 MiB fsynced write/read hash passed; guest configurations/states and existing disk retained | New mount declaration has not been reboot-qualified; no endurance, physical disconnect or power-loss qualification |
| Local browser kiosk | 🟡 **Partial** | Debian VM lifecycle and browser/input component checks | Full Proxmox profile and physical display/input |

## Next checks

- Follow the [bounded continuation procedure](continuing.md). Selected Shares &
  Data HTTP/worker, recovery and installed-browser checks are recorded. A real
  leaf-package Admin update and separately approved no-op now pass
  on another disposable target. Keep remaining client, package breadth,
  other profile combinations and interruption boundaries separate.
- On disposable targets, qualify off-host restore and interrupted setup/recovery;
  repeat relevant cases on the exact release candidate.
- Exercise registered GitHub runner jobs and cancellation, then implement and
  qualify combined build/CI host composition.
- Complete public tunnel/certificate issuance and native browser cases; keep
  live provider credentials and raw evidence private.
- Investigate the failed LXC suspend and run the separate Proxmox cluster and
  physical-kiosk cases when appropriate fixtures are available.
