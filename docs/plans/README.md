# Planning and issue index

Status: current portfolio for the v2.0.0 preview and stable-release work.
The roadmap defines priority, operator and contributor guides describe current
behavior, and this index records the release gate, current portfolio, and
unscheduled issues.

The package version is 2.0.0rc1. The v2.0.0-rc.1 tag is not published, and
the stable release remains gated by the [qualification matrix](../release-qualification.md).
Further CachyOS enhancements are outside this release pass.

## Current portfolio

| Area | Current status | Next boundary |
| --- | --- | --- |
| Release and migration | The fleet namespace cutover is complete; the preview candidate is not published. | Complete the [release matrix](../release-qualification.md) before publishing v2.0.0. |
| Target-user rename | Implemented; disposable Debian/systemd qualification remains open. | Complete USR-01 in the [release matrix](../release-qualification.md#stable-qualification-matrix). |
| Privilege execution | The approved executor, review policy, and safe status contract are implemented. | Complete the disposable Debian lifecycle case in the [release matrix](../release-qualification.md#stable-qualification-matrix). |
| Transaction safety | Bounded recovery is implemented for managed operations; host qualification is open. | Use the [contributor reference](../agents/contributing/transaction-safety.md), [recovery guide](../operations/transaction-recovery.md), and release matrix. |
| Deployment manifests and secrets | Direct and webhook deployment share existing transaction safeguards; broader manifest reuse is queued. | Sequence [deploy secrets](deploy-secrets.md) before [CI/CD manifest reuse](cicd-manifest-reuse.md). |
| GitHub self-hosted runners | Debian phase 1 is implemented with offline checks; live qualification is open. | Qualify a clean Debian 13 x86-64 VM, reboot, Docker-free workflow and all supported identity gates per the [runner guide](../services/github-runner.md) and [support plan](github-runner-support.md); Windows parity, organization-wide registration and disposable jobs remain deferred. |
| Planning and drift | Domain-specific observations exist; there is no general desired-versus-observed plan command. | Define one stable read-only result contract and address the retention gap in issue #108. |
| VM management, storage, and Gogs | Provisioning supports declared blank QEMU data disks and local Gogs LFS storage; broader mutation and recovery remain gated. | Validate the current slice on Proxmox, then scope provider-neutral mutation and Gogs restore in [VM management](vm-management-and-lightweight-git-hosting.md). |
| Proxmox maintenance | Routine audits, guarded updates and reboot checks are available; HA/Ceph orchestration and verified restore are not. | See [Proxmox operations](../operations/proxmox.md) for current limits and PVE-01 in the [release matrix](../release-qualification.md#stable-qualification-matrix) for qualification. |
| HomeBox | Native service setup, updates, backup, and restore are documented; a disposable-VM release check remains open. | Complete APP-01 in the release matrix and follow [HomeBox operations](../services/homebox.md). |
| T3 Code | Basaltwater targets npm's latest stable T3 release; fresh install, browser enrollment, and cloud sign-in remain unverified. | Complete T3-01 in the [release matrix](../release-qualification.md#stable-qualification-matrix) and follow the current [T3 Code guide](../agents/t3-code.md). |
| Shared desktop | The XRDP session, bounded native tools, and human handoff are available; resize can briefly interrupt the RDP connection. | Complete DSK-01 in the [release matrix](../release-qualification.md#stable-qualification-matrix) and follow [XRDP guidance](../workstations/xrdp.md). |
| Browser support | Chromium and opt-in Firefox workflows are documented; native ESR/deployment qualification remains open. | Complete BRW-01 in the [release matrix](../release-qualification.md#stable-qualification-matrix) using [browser testing](../agents/browser-testing.md). |
| CachyOS workstation | Current supported setup is documented; new experimental enhancements are deferred. | Use the [workstation guides](../workstations/cachyos.md); issue #106 remains separate from the Debian release gate. |
| Ubuntu WSL | Bootstrap, profiles, and native jobs are implemented; Windows live qualification is pending and is outside the v2.0.0 stable-release gate. | Complete the manual Windows checks in the [operator guide](../workstations/windows-wsl.md) before claiming production readiness; add a matrix case if WSL enters release scope. |
| Godot workflows | Web and publishing bundles are supported; later bundles have no compatibility or rollout commitment. | Add a bundle only with its own verification and update contract. |
| Game publishing | The panel and CLI workflows are available; provider account and rendering behavior are not live-qualified. | Use the [operator guide](../workstations/game-publishing.md), [publishing status](game-publishing.md), and [communications plan](game-release-communications.md). |
| Storage recovery | Core integrity controls and read-only panel snapshots are documented; additional recovery automation is unscheduled. | Track only the remaining shared recovery needs in the [roadmap](roadmap.md). |

## Unscheduled open issues

These issues have no scheduled implementation. Write a focused brief and assign
a roadmap slot before starting work.

| Issue | Current disposition | Planning boundary |
| --- | --- | --- |
| [#91 — Interactive orchestration](https://github.com/bluehexagons/basaltwater/issues/91) | Interactive Proxmox host and guest operations are available; setup-job lifecycle is not defined. | Reuse setup parsing, workspace records, and transaction recovery. |
| [#63 — Secrets and credentials](https://github.com/bluehexagons/basaltwater/issues/63) | Private workspace credentials exist for selected services. | Add versioned deployment secret references, rotation, and consumer inventory after P0. |
| [#28 — Scheduling](https://github.com/bluehexagons/basaltwater/issues/28) | Storage and panel jobs have separate existing owners. | Define later fleet coordination without introducing competing timers. |
| [#25 — Multimedia bundles](https://github.com/bluehexagons/basaltwater/issues/25) | Individual native tools are available; the requested broad bundles are not. | Decide profile scope and package ownership before scheduling. |
| [#83 — Apt cache](https://github.com/bluehexagons/basaltwater/issues/83) | Cache cleanup exists; no cache server or client proxy is configured. | Specify ownership, endpoint validation, failure behavior, and rollback. |
| [#87 — Default configuration](https://github.com/bluehexagons/basaltwater/issues/87) | Panel layout and widgets are not modeled. | Define a versioned preference owner for a supported desktop. |
| [#100 — Akaunting](https://github.com/bluehexagons/basaltwater/issues/100) | Not implemented; Coolify evaluation is deferred. | Reopen only with a concrete deployment and recovery owner. |
| [#105](https://github.com/bluehexagons/basaltwater/issues/105) and [#107](https://github.com/bluehexagons/basaltwater/issues/107) — Agent automation | Saved Codex tasks and panel task preparation exist; a fleet manager, shared evidence, and agent chat are not implemented. | Define evidence, authority, and cost boundaries before implementation. |
| [#111 — Arch certificate install](https://github.com/bluehexagons/basaltwater/issues/111) | Report lacks a reproducible command or host details. | Collect reproduction details before changing support claims. |
| [#112 — Hide uninstalled tools](https://github.com/bluehexagons/basaltwater/issues/112) | Request lacks scope and acceptance criteria. | Clarify which panels and unavailable states are expected. |

GitHub issues remain the canonical record for discussion and detailed
acceptance. This table keeps a compact current disposition; reports #111 and
#112 need more detail before implementation.

## Documentation ownership

Operator guides describe current behavior. The release matrix records required
qualification. This portfolio and the roadmap describe active or explicitly
deferred work; GitHub issues own detailed acceptance. Completed audit snapshots
and implementation chronologies are not maintained as separate documents.
