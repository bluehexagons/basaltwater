# GitHub Issue Triage (reverified 2026-08-24)

Status: current issue-to-implementation map. Public issue metadata and the
implementation evidence below were reverified on 2026-08-24. The tracker has
10 open issues: four active or queued roadmap issues, five unscheduled backlog
issues, and one deliberately deferred issue. Issues #79, #81, #90, #92, and
#93 were closed on GitHub during this reconciliation with implementation and
documentation evidence recorded in their closing comments.

Use the [planning and issue index](README.md) for the project portfolio and
dependency order. This file records what each open issue means against current
code; it does not replace the issue discussion or acceptance criteria.

## Triage rules

- Close an issue only when the requested operator-facing behavior is present,
  documented, and covered by focused tests where the repository can provide
  them.
- Keep an issue open when only a slice is implemented, when live-system
  validation is still required, or when the request needs a design decision.
- Treat this document and the linked roadmap plans as the implementation
  reference. Apply the corresponding state/comment changes on GitHub when the
  issue service is available.
- Give unscheduled work a project brief and roadmap slot before implementation;
  issue age alone does not determine priority.

## Closed during the 2026-08-24 reconciliation

| Issue | Result | Evidence |
| --- | --- | --- |
| [#79 — Add simpler self-setup](https://github.com/bluehexagons/basaltwater/issues/79) | Closed | `bootstrap`/`self-setup` installs the launcher, packages, and completions; `install.sh --local-setup` supports one-command local setup. See [Installation](../INSTALLATION.md). |
| [#81 — Container lifecycle management](https://github.com/bluehexagons/basaltwater/issues/81) | Closed for the requested lifecycle surface | `infra-tools proxmox` supports live guest listing, status, start, stop, pause, resume, destroy, resource changes, disk resize, snapshots, health checks, and host registration in the workspace. Explicit desired-state guest inventory remains a separate follow-up under #91/#97. See [Proxmox workflows](../PROXMOX.md). |
| [#90 — Clean up old kernels](https://github.com/bluehexagons/basaltwater/issues/90) | Closed for Debian package-managed kernels | `cleanup-maintenance` runs `apt-get autoremove --purge`, allowing APT's kernel-retention policy to remove superseded packages. Proxmox boot entries are intentionally outside this cleanup boundary. See [Recurring Maintenance](../MAINTENANCE.md). |
| [#92 — Support creating VMs on Proxmox, not just containers](https://github.com/bluehexagons/basaltwater/issues/92) | Closed | Hosted setup defaults to VMs and provisions them with `qm` and cloud-init; guest management handles both `qm` VMs and `pct` containers. See [Proxmox workflows](../PROXMOX.md). |
| [#93 — Proxmox setups are auto-restarting](https://github.com/bluehexagons/basaltwater/issues/93) | Closed | `server_proxmox` defaults to no automatic restart and no forced restart deadline. Operators must opt in, and the behavior is documented in [Proxmox workflows](../PROXMOX.md). |

## Open, partially implemented

| Issue | Current slice | Disposition, remaining work, and owner |
| --- | --- | --- |
| [#97 — V1 Rearchitecture](https://github.com/bluehexagons/basaltwater/issues/97) | Saved setup state is atomic, Proxmox hosts are workspace-registered, VM/LXC provisioning and lifecycle management exist, and read-only Proxmox planning/audit commands are available. | **P0 active.** This remains the umbrella issue for transactional setup/deploy activation, strict corrupt-state handling, verified SSH enrollment, and manifest-driven recovery. The immediate owner is [Transactional execution](TRANSACTIONAL_EXECUTION.md); later slices are sequenced in the [roadmap](ROADMAP.md). |
| [#91 — Interactive orchestration](https://github.com/bluehexagons/basaltwater/issues/91) | An interactive Proxmox shell can select registered hosts, inspect guests, run lifecycle operations, and perform backups, migration, snapshots, and maintenance actions. | **Unscheduled; project brief required after P0.** It does not create a new setup from an interactive form, queue/run arbitrary setup jobs, or monitor those jobs. Reuse the existing setup command, workspace records, and transactional execution rather than a second provisioning engine. |
| [#85 — Improve RAM over-provisioning behavior](https://github.com/bluehexagons/basaltwater/issues/85) | Hosted VMs support an explicit `--balloon-min` lower bound; the default equals requested memory, so provisioning does not silently overcommit. Placement planning reports live memory headroom. | **Unscheduled capacity-policy backlog.** No automatic host page-cache policy or saved cluster-wide overcommit policy exists. Any future policy must be explicit and workload-aware; do not infer safe cache pressure from a VM label. |
| [#63 — Secrets/credential management system](https://github.com/bluehexagons/basaltwater/issues/63) | Workspace credentials support validated, mode-`0600` named credentials for Samba, SMB mounts, and Antistatic administration, with interactive set/list/remove commands. | **P1 queued behind P0.** Generic deployment secret references, optional secret-gated manifest components, rotation, and consumer inventory remain unimplemented. Continue with [Deploy secrets](DEPLOY_SECRETS.md). |
| [#58 — Diagnostics monitoring](https://github.com/bluehexagons/basaltwater/issues/58) | Shared notification targets are used by maintenance, security monitoring, storage operations, CI/CD, and ecosystem update jobs. The security monitor and storage runner collect and report several failure classes. | **P1 active in domain slices.** There is no unified diagnostics/audit contract covering shares, websites, repositories, timers, and repair actions. Extend the roadmap's read-only audit surface instead of adding another notification-only service. |
| [#38 — Resizing desktop over RDP causes screen and session to freeze](https://github.com/bluehexagons/basaltwater/issues/38) | XRDP now uses the Xorg/xrdpdev path with dynamic-channel support, reconnectable native session policy, explicit channel controls, and documented troubleshooting. | **P1 validation backlog.** The repository still lacks the disposable live VM/LXC smoke test for resize, disconnect/reconnect, and package-upgrade behavior. Keep this open until live evidence confirms the supported matrix. See [XRDP](../XRDP.md) and the [desktop audit](DESKTOP_AGENT_MAINTENANCE_AUDIT_2026-08-09.md). |
| [#28 — Scheduling system](https://github.com/bluehexagons/basaltwater/issues/28) | Sync and scrub specifications use a shared hourly timer, per-spec due intervals, atomic scheduling state, and a persistent lock to prevent overlap. | **Unscheduled orchestration backlog.** Cross-host coordination, configurable maintenance windows, and resource-aware ordering are not implemented. Extend the existing storage orchestrator rather than add competing timers. See [Storage operations](../STORAGE_OPERATIONS.md). |

## Open, not implemented or deliberately deferred

| Issue | Disposition | Decision |
| --- | --- | --- |
| [#83 — Apt cache support](https://github.com/bluehexagons/basaltwater/issues/83) | Unscheduled backlog | No APT cache server installation or client proxy configuration exists. Plan the server package/service boundary, validated cache endpoint, client source configuration, and rollback behavior before implementation. |
| [#87 — More default config options](https://github.com/bluehexagons/basaltwater/issues/87) | Unscheduled backlog | The current profile defaults cover install-time choices such as browsers, office, RDP, agents, and maintenance policy. Panel layout and widget preferences are not modeled. Add a versioned desktop-preferences contract only if a supported desktop profile can own it. |
| [#25 — Multimedia packages](https://github.com/bluehexagons/basaltwater/issues/25) | Deliberately deferred | Multimedia flags and package bundles are not implemented. Reconsider this only after the roadmap's transactional safety, audit/drift detection, and recovery work is substantially complete. |

## Follow-up policy

New implementation work should update its detailed plan, the
[planning index](README.md), the roadmap, and this issue map whenever their
status or order changes. A closed issue should be reopened only for a concrete
regression or an acceptance criterion that was explicitly excluded from the
closure comment.
