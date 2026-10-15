# Planning and issue index

Status: current portfolio for the v2.0.0 preview and stable-release work,
refreshed 2026-10-15. The roadmap owns priority; an active project plan owns
its remaining design and acceptance boundary; the issue map records open
issue scope and evidence.

The package version is 2.0.0rc1. The v2.0.0-rc.1 tag is not published, and
the stable release remains gated by the [qualification matrix](../release-qualification.md).
Further CachyOS enhancements are outside this release pass.

## Current portfolio

| Area | Current status | Next boundary |
| --- | --- | --- |
| Release and migration | The fleet namespace cutover is complete; the preview candidate is not published. | Complete the [release matrix](../release-qualification.md) before publishing v2.0.0. |
| Target-user rename | Implementation and mocked coverage are in main; live qualification is open. | Run the disposable Debian/systemd case in the release matrix. |
| Privilege execution | The approved executor, review policy, and safe status contract are implemented. | Complete the disposable Debian refresh/recovery qualification in [privilege execution](privilege-execution-qualification.md). |
| Transaction safety | Bounded recovery is implemented for managed operations; host qualification is open. | Use the [transaction plan](transactional-execution.md) and release matrix for remaining host coverage. |
| Deployment manifests and secrets | Direct and webhook deployment share existing transaction safeguards; broader manifest reuse is queued. | Sequence [deploy secrets](deploy-secrets.md) before [CI/CD manifest reuse](cicd-manifest-reuse.md). |
| Go deployment convention | The project-specific `go-site/v1` contract remains proposed; it is not a general CLI support promise. | See the [Go site convention](go-site-convention.md) linked from deployment guidance. |
| Planning and drift | Domain-specific observations exist; there is no general desired-versus-observed plan command. | Define one stable read-only result contract and address the retention gap in issue #108. |
| VM management, storage, and Gogs | Provisioning supports declared blank QEMU data disks and local Gogs LFS storage; broader mutation and recovery remain gated. | Validate the current slice on Proxmox, then scope provider-neutral mutation and Gogs restore in [VM management](vm-management-and-lightweight-git-hosting.md). |
| Proxmox maintenance | Routine audits, guarded updates and reboot checks are available; HA/Ceph orchestration and verified restore are not. | Use [Proxmox maintenance](proxmox-maintenance.md) for current gaps and host qualification.
| HomeBox | Native service setup, updates, backup, and restore are documented; a disposable-VM release check remains open. | Complete APP-01 in the release matrix and follow [HomeBox operations](../services/homebox.md). |
| Agent host maintenance | Explicit user-tool updates, safe cache cleanup, and narrow diagnostics are available; fleet policy gaps remain unscheduled. | See the [agent host maintenance brief](agent-host-maintenance.md) and current [agent guides](../agents/README.md). |
| T3 Code VM | Stable v0.0.45 remains the deployment target; setup and client qualification are incomplete. | Complete the open cases in [T3 Code VM qualification](t3-code-agent-vm-improvements.md). |
| Shared desktop | The XRDP session, bounded native tools, and human handoff are available; resize can interrupt the RDP connection. | Complete live cases in [XRDP guidance](../workstations/xrdp.md) and review open host/client boundaries in [desktop maintenance](desktop-maintenance.md). |
| Browser support | Chromium and opt-in Firefox workflows are documented; native ESR/deployment qualification remains open. | Finish the browser cases in [browser testing](../agents/browser-testing.md) and the release matrix. |
| CachyOS workstation | Current supported setup is documented; new experimental enhancements are deferred. | Use the [workstation guides](../workstations/cachyos.md); issue #106 remains separate from the Debian release gate. |
| Ubuntu WSL | Bootstrap, profiles, and native jobs are implemented; Windows live qualification is pending. | Qualify a fresh and resumed Windows 11 setup in [WSL support](ubuntu-wsl-support.md). |
| Shared distro capabilities | The cross-distro adapter proposal is unscheduled. | Keep scope behind reliability work in [shared capability contracts](cross-distro-shared-capabilities.md). |
| Godot workflows | Web and publishing bundles are supported; later bundles have no compatibility or rollout commitment. | Add a bundle only with its own verification and update contract. |
| Game publishing | The panel and CLI workflows are available; provider account and rendering behavior are not live-qualified. | Use the [operator guide](../workstations/game-publishing.md), [publishing status](game-publishing.md), and [communications plan](game-release-communications.md). |
| Background agents | Account-level Codex tasks are available; fleet evidence, fixed recipes, and broader authority remain proposed. | Keep the proposal in [background agent capabilities](background-agent-capabilities.md) unscheduled until a roadmap slot exists. |
| Storage recovery | Core integrity controls and read-only panel snapshots are documented; additional recovery automation is unscheduled. | Track only the remaining shared recovery needs in the [roadmap](roadmap.md). |

## Unscheduled open issues

These issues have no scheduled implementation. Write a focused brief and assign
a roadmap slot before starting work.

| Issue | Current disposition | Planning boundary |
| --- | --- | --- |
| [#91 — Interactive orchestration](https://github.com/bluehexagons/basaltwater/issues/91) | Partial shell; job lifecycle is not defined. | Reuse setup parsing, workspace records, and transaction recovery. |
| [#63 — Secrets and credentials](https://github.com/bluehexagons/basaltwater/issues/63) | Private workspace credentials exist for selected services. | Add versioned deployment secret references, rotation, and consumer inventory after P0. |
| [#28 — Scheduling](https://github.com/bluehexagons/basaltwater/issues/28) | Storage and panel jobs have separate existing owners. | Define later fleet coordination without introducing competing timers. |
| [#25 — Multimedia bundles](https://github.com/bluehexagons/basaltwater/issues/25) | Individual native tools are available; the requested broad bundles are not. | Decide profile scope and package ownership before scheduling. |
| [#83 — Apt cache](https://github.com/bluehexagons/basaltwater/issues/83) | Cache cleanup exists; no cache server or client proxy is configured. | Specify ownership, endpoint validation, failure behavior, and rollback. |
| [#87 — Default configuration](https://github.com/bluehexagons/basaltwater/issues/87) | Panel layout and widgets are not modeled. | Define a versioned preference owner for a supported desktop. |
| [#100 — Akaunting](https://github.com/bluehexagons/basaltwater/issues/100) | Not implemented; Coolify evaluation is deferred. | See the [Coolify decision](coolify-integration.md); reopen only with a concrete deployment and recovery owner. |
| [#105 and #107 — Agent automation](https://github.com/bluehexagons/basaltwater/issues/105) | Saved account-level Codex tasks exist; a fleet framework is only proposed. | Define evidence, authority, and cost boundaries before implementation. |
| [#111 — Arch certificate install](https://github.com/bluehexagons/basaltwater/issues/111) | Report lacks a reproducible command or host details. | Collect reproduction details before changing support claims. |
| [#112 — Hide uninstalled tools](https://github.com/bluehexagons/basaltwater/issues/112) | Request lacks scope and acceptance criteria. | Clarify which panels and unavailable states are expected. |

The [issue map](issue-triage.md) contains the current 14 open issues as of
2026-10-15; reports #111 and #112 need more detail before implementation.

## Documentation ownership

Operator guides describe current behavior. The release matrix records
uncompleted qualification. This portfolio and the roadmap describe active or
explicitly deferred work. Completed audit snapshots and implementation
chronologies are not maintained as separate documents.
