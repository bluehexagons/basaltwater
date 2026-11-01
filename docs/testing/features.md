# Feature testing status

**Evidence through 2026-10-31**, except the 2026-11-01 documentation and
fixture checks noted in the [overview](README.md). Status applies only to the
workflow described in each row; see the [legend](README.md#status-legend).
“Component” includes native probes or controlled fixtures; “Automated” is
repository test coverage. Consult the
[detailed coverage map](../plans/testing-coverage.md) for exact observations
and the [release matrix](../release-qualification.md#stable-qualification-matrix)
for candidate-specific status.

| Feature | Status | Evidence | Major remaining boundary |
| --- | --- | --- | --- |
| Debian server and agent setup | 🟢 **Labbed** | Fresh setup, rerun and reboot | Other profiles, option combinations and exact candidate |
| Shared XFCE/XRDP desktop | 🟢 **Labbed** | Setup, concurrent starts, input, clipboard and reconnect | Additional clients, crash/interruption and release matrix |
| Browser installation | 🟡 **Partial** | Debian package failure, repair and retry; automated selection | Graphical startup and other installers/platforms |
| Proxmox local kiosk | 🟡 **Partial** | Debian VM lifecycle and browser/input components | Full Proxmox profile and physical display/input |
| Firewall, jails and initramfs | 🟢 **Labbed** | Selected setup and component failure/retry | Runtime failures, power loss and hibernation |
| AppArmor | 🟡 **Partial** | Packaged service failures, repair and policy preservation | Full setup, boot transitions and interrupted reload |
| Compressed swap | 🟡 **Partial** | Real zram devices and component failure/recovery | Native zswap controls, reboot and pressure |
| Security event retention | 🟡 **Partial** | Logs, rotation, gaps and live monitor components | Continuous daemon behavior and power loss |
| Agent tools and tasks | 🟢 **Labbed** | Selected enrollment, managed skills, task/goal flows | Other providers and longer interrupted runs |
| Approval and admin jobs | 🟢 **Labbed** | Selected approval/refresh; controlled failure and browser fixtures | Package-changing upgrades and uncertain/power-loss boundaries |
| Proxmox VM lifecycle | 🟢 **Labbed** | Provision, clone, snapshot, backup and same-host restore | Off-host recovery, multi-node and EFI/TPM variants |
| LXC suspend | 🔴 **Failed** | Live attempt failed | CRIU suspend remains unqualified |
| Storage and networking guards | 🟢 **Labbed** | Selected capacity, mount and access checks; automated tests | HA/Ceph, management isolation and interrupted upgrades |
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
| Client CA enrollment | 🟡 **Partial** | Native Arch userland component probes | Full desktop/browser trust and other client platforms |
| Managed user rename | 🟢 **Labbed** | Selected identity, home and recovery cases | More interruption points and application-specific migration |
| Resource-constrained development | 🟢 **Labbed** | Selected 1 vCPU / 1 GiB Debian workload | Full suite, browser and other platforms at that floor |
| Native Windows workstation | 🟡 **Partial** | Native offline suite and selected process/Scheduler checks, 2026-10-31 | Fresh install, physical devices, reboot interruption and complete release qualification |
| CachyOS workstation | 🟡 **Partial** | Selected native development, rendering and task checks | Full desktop/client trust and broader hardware coverage |
| WSL, game publishing, external alerts | 🟡 **Partial** | Automated checks or selected observations | End-to-end provider and platform qualification remains open |
