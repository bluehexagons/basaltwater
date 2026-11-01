# Debian and Proxmox testing status

**Evidence recorded through 2026-10-31.** Selected live workflows cover Debian
12/13 guests and a physical Proxmox host; they do not establish a pass on every
machine type. Status applies only to the workflow described in each row; see
the [legend](README.md#status-legend).
“Component” includes native probes or controlled fixtures; “Automated” is
repository test coverage. Consult the
[detailed coverage map](../plans/testing-coverage.md) for exact observations
and the [release matrix](../release-qualification.md#stable-qualification-matrix)
for candidate-specific status.

Debian is the project's officially supported distribution on physical machines,
VMs and unprivileged Proxmox LXC where each profile permits. Selected runs
covered `server_lite`, `server_web`, `server_proxmox` and `agent_vm`, not every
profile on every machine type. The [platform index](features.md) distinguishes
availability from test status.

## Debian guests and services

| Feature | Status | Evidence | Major remaining boundary |
| --- | --- | --- | --- |
| Debian server and agent setup | 🟢 **Labbed** | Fresh setup, rerun and reboot | Other profiles, option combinations and exact candidate |
| Shared XFCE/XRDP desktop | 🟢 **Labbed** | Setup, concurrent starts, input, clipboard and reconnect | Additional clients, crash/interruption and release matrix |
| Browser installation | 🟡 **Partial** | Debian package failure, repair and retry; automated selection | Graphical startup and other installers/platforms |
| Firewall, jails and initramfs | 🟢 **Labbed** | Selected setup and component failure/retry | Runtime failures, power loss and hibernation |
| AppArmor | 🟡 **Partial** | Packaged service failures, repair and policy preservation | Full setup, boot transitions and interrupted reload |
| Compressed swap | 🟡 **Partial** | Real zram devices and component failure/recovery | Native zswap controls, reboot and pressure |
| Security event retention | 🟡 **Partial** | Logs, rotation, gaps and live monitor components | Continuous daemon behavior and power loss |
| Agent tools and tasks | 🟢 **Labbed** | Selected enrollment, managed skills, task/goal flows | Other providers and longer interrupted runs |
| Approval and admin jobs | 🟢 **Labbed** | Selected approval/refresh; controlled failure and browser fixtures | Package-changing upgrades and uncertain/power-loss boundaries |
| mDNS and automatic updates | 🟡 **Partial** | Packaged services and timer handoff failure/retry components | Full setup, remote discovery and scheduled upgrades |
| Sync and PAR2 | 🟢 **Labbed** | Selected mirror, mount guards and repair; controlled failures | Hard-mounted network storage and power loss |
| Btrfs | 🟢 **Labbed** | Single-device setup, rerun, reboot and scrub | Scheduled scrub, multi-device and self-repair |
| Samba and Syncthing | 🟢 **Labbed** | SMB3 access/reconnect and two-peer synchronization | Other client OSs, hard mounts and relay/discovery |
| Gogs and mirrors | 🟢 **Labbed** | Git/LFS, rerun, reboot and same-host restore | GitHub transport and off-host recovery |
| HomeBox | 🟢 **Labbed** | Loopback API, upgrade, restore, reboot and re-enable | Browser, public TLS, replacement host and rollback |
| Web deployment | 🟢 **Labbed** | Selected Go, Node and SPA/API workflows, rollback and same-host recovery | Public tunnel/ACME, off-host restore and power loss |
| Selected builder | 🟢 **Labbed** | Enrollment, delivery, refresh and multi-application workflows | Public ingress, more failure boundaries and unsupported interpreter services |
| Builder retention/recovery | 🟡 **Partial** | Component and automated settlement, pruning and lost-acknowledgement guards | Further crash/power-loss boundaries; no automatic collection |
| GitHub runner | 🟢 **Labbed** | Tokenless setup, rerun and reboot | Registration, real jobs and cancellation |
| Combined build/CI host | 🔵 **In progress** | Separate roles implemented | Composition and shared capacity not yet implemented or qualified |
| Web panels | 🟢 **Labbed** | Selected setup/auth and component lifecycle/recovery; browser fixtures | Full removal/interruption, additional browsers and native console |
| Managed user rename | 🟢 **Labbed** | Selected identity, home and recovery cases | More interruption points and application-specific migration |
| Resource-constrained development | 🟢 **Labbed** | Selected 1 vCPU / 1 GiB Debian workload | Full suite, browser and other platforms at that floor |
| Repository checks | 🟡 **Partial** | Debian 13: [9,009 tests, six skips, 82% coverage](../plans/workstation-feedback-debian.md#debian-completion-record-2026-10-31), 2026-10-31 | Historical revision only; browser and physical hardware are separate |
| Desktop development and visual checks | 🟡 **Partial** | Debian 13 software GLX, small Blender CPU render and XRDP edit/save fixture | Native GPU/display, audio, Wayland and full project images |
| T3 Code and provider workflows | 🟡 **Partial** | Selected enrollment and task checks; additional T3 components | Exact-candidate install, pairing, migration and complete live workflow |
| Game publishing and external alerts | 🟡 **Partial** | Automated checks or selected observations | Full publishing and external-provider paths |

## Proxmox host and guest operations

These host features are separate from Debian guest profile results. Host
setup is selected evidence on one physical node, not cluster qualification.

| Feature | Status | Evidence | Major remaining boundary |
| --- | --- | --- | --- |
| VM lifecycle | 🟢 **Labbed** | Provision, clone, snapshot, backup and same-host restore | Off-host recovery, multi-node and EFI/TPM variants |
| LXC suspend | 🔴 **Failed** | Live CRIU attempt failed | Suspend remains unqualified |
| Storage and networking guards | 🟢 **Labbed** | Selected capacity, mount and access checks; automated tests | HA/Ceph, management isolation and interrupted upgrades |
| Local browser kiosk | 🟡 **Partial** | Debian VM lifecycle and browser/input component checks | Full Proxmox profile and physical display/input |

## Next checks

- On disposable targets, qualify off-host restore and interrupted setup/recovery;
  repeat relevant cases on the exact release candidate.
- Exercise registered GitHub runner jobs and cancellation, then implement and
  qualify combined build/CI host composition.
- Complete public tunnel/certificate issuance and native browser cases; keep
  live provider credentials and raw evidence private.
- Investigate the failed LXC suspend and run the separate Proxmox cluster and
  physical-kiosk cases when appropriate fixtures are available.
