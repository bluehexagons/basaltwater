# Feature testing status

**Evidence through 2026-10-31**, except the 2026-11-01 documentation and
fixture checks noted in the [overview](README.md). “Selected live” means the
named workflow passed on a real test system, with the stated boundaries. It
does not qualify every configuration. “Component” includes native probes or
controlled fixtures; “Automated” is repository test coverage. Consult the
[detailed coverage map](../plans/testing-coverage.md) for exact observations
and the [release matrix](../release-qualification.md#stable-qualification-matrix)
for candidate-specific status.

| Feature | Evidence | Major remaining boundary |
| --- | --- | --- |
| Debian server and agent setup | Selected live fresh setup, rerun and reboot | Other profiles, option combinations and exact candidate |
| Shared XFCE/XRDP desktop | Selected live setup, concurrent starts, input, clipboard and reconnect | Additional clients, crash/interruption and release matrix |
| Browser installation | Component: Debian package failure, repair and retry; automated selection | Graphical startup and other installers/platforms |
| Proxmox local kiosk | Component: Debian VM lifecycle and browser/input | Full Proxmox profile and physical display/input |
| Firewall, jails and initramfs | Selected live setup and component failure/retry | Runtime failures, power loss and hibernation |
| AppArmor | Component: packaged service failures, repair and policy preservation | Full setup, boot transitions and interrupted reload |
| Compressed swap | Component: real zram devices and failure/recovery | Native zswap controls, reboot and pressure |
| Security event retention | Component: logs, rotation, gaps and live monitor | Continuous daemon behavior and power loss |
| Agent tools and tasks | Selected live enrollment, managed skills, task/goal flows | Other providers and longer interrupted runs |
| Approval and admin jobs | Selected live approval/refresh; controlled failure and browser fixtures | Package-changing upgrades and uncertain/power-loss boundaries |
| Proxmox VM lifecycle | Selected live provision, clone, snapshot, backup and same-host restore | Off-host recovery, multi-node and EFI/TPM variants |
| LXC suspend | Live attempt failed | CRIU suspend remains unqualified |
| Storage and networking guards | Selected live and automated capacity, mount and access checks | HA/Ceph, management isolation and interrupted upgrades |
| mDNS and automatic updates | Component: packaged services and timer handoff failure/retry | Full setup, remote discovery and scheduled upgrades |
| Sync and PAR2 | Selected live mirror, mount guards and repair; controlled failures | Hard-mounted network storage and power loss |
| Btrfs | Selected live single-device setup, rerun, reboot and scrub | Scheduled scrub, multi-device and self-repair |
| Samba and Syncthing | Selected live SMB3 access/reconnect and two-peer synchronization | Other client OSs, hard mounts and relay/discovery |
| Gogs and mirrors | Selected live Git/LFS, rerun, reboot and same-host restore | GitHub transport and off-host recovery |
| HomeBox | Selected live loopback API, upgrade, restore, reboot and re-enable | Browser, public TLS, replacement host and rollback |
| Web deployment | Selected live Go, Node and SPA/API workflows, rollback and same-host recovery | Public tunnel/ACME, off-host restore and power loss |
| Selected builder | Selected live enrollment, delivery, refresh and multi-application workflows | Public ingress, more failure boundaries and unsupported interpreter services |
| Builder retention/recovery | Component and automated settlement, pruning and lost-acknowledgement guards | Further crash/power-loss boundaries; no automatic collection |
| GitHub runner | Selected live tokenless setup, rerun and reboot | Registration, real jobs and cancellation |
| Combined build/CI host | Separate roles implemented | Composition and shared capacity still in progress |
| Web panels | Selected live setup/auth and component lifecycle/recovery; browser fixtures | Full removal/interruption, additional browsers and native console |
| Client CA enrollment | Native Arch userland component probes | Full desktop/browser trust and other client platforms |
| Managed user rename | Selected live identity, home and recovery cases | More interruption points and application-specific migration |
| Resource-constrained development | Selected live 1 vCPU / 1 GiB Debian workload | Full suite, browser and other platforms at that floor |
| Native Windows workstation | Native offline suite and selected process/Scheduler checks, 2026-10-31 | Fresh install, physical devices, reboot interruption and complete release qualification |
| CachyOS workstation | Selected native development, rendering and task checks | Full desktop/client trust and broader hardware coverage |
| WSL, game publishing, external alerts | Automated or selected observations | End-to-end provider and platform qualification remains open |
