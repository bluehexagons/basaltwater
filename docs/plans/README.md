# Project plans

The [roadmap](roadmap.md) sets priorities. Operator guides describe available
behavior; plans describe remaining work and acceptance. The
[coverage map](testing-coverage.md) records selected checks and gaps, while the
[release matrix](../release-qualification.md) gates an exact release candidate.

The package is `2.0.0rc1`; no preview tag is published. Stable v2.0.0 remains
qualification-gated. Completed namespace migration is documented in the
[migration guide](../migration-guide.md).

## Active priorities

| Priority | Current state | Next deliverable |
| --- | --- | --- |
| Shared Debian build/CI host | Bounded on-demand builds are implemented and qualified for selected static/Go workflows. Webhooks and GitHub runners remain separate. | Compose all three roles on one VM, share capacity and draining, then qualify conversion of an existing runner. See the [build-host plan](cicd-manifest-reuse.md) and [runner plan](github-runner-support.md). |
| Resource-aware optimization | Serial startup, output, artifact I/O, source scanning and CI preparation are improved; the pinned basic-development workload has selected native 1 vCPU / 1 GiB Debian qualification. | Measure further coupled changes before introducing worker ownership, shared budgets or caches. See the [optimization plan](resource-aware-optimization.md) and [coverage limits](testing-coverage.md). |
| Release qualification | Repository checks and selected live workflows exist; they do not qualify the complete candidate. | Run the remaining exact-candidate [release cases](../release-qualification.md#stable-qualification-matrix), including user rename, privilege execution, desktop/browser and supported application paths. |
| Setup reliability | Firewall entrypoints respect machine capabilities. XRDP jails follow desktop setup and pass configuration validation before restart; resume changes retain pending initramfs rebuild intent. Selected native service and failure-recovery probes pass. | Qualify the complete fresh desktop/RDP flow and remaining setup interruption boundaries; bounded jail/rebuild probes do not pass the release matrix. See [authentication hardening](../services/authentication-hardening.md) and [transaction recovery](../operations/transaction-recovery.md). |
| Read-only planning and drift | Domain-specific observations exist, but no general desired-versus-observed plan command. | Define a stable observation/result contract; address security-monitor source-retention detection in [#108](https://github.com/bluehexagons/basaltwater/issues/108). |

## Follow-up work

These areas are not prerequisites for the shared Debian build/CI host.

| Area | Next boundary and owner |
| --- | --- |
| Build retention and recovery | Preserve explicit settlement/pruning/bundle release and stop-and-inspect recovery. Automatic retention, replay-history retirement and further interruption checks need separate acceptance in the [build plan](cicd-manifest-reuse.md). |
| Deployment secrets | Add administrator-owned runtime binding IDs before a broader secret store; selected-builder deployment currently rejects runtime environment and `env_file`. See [secret references](deploy-secrets.md). |
| Proxmox, storage and Gogs | Extend selected same-host recovery to off-host restore; coordinate guest filesystem growth separately from provider disk resize. See [VM/storage work](vm-management-and-lightweight-git-hosting.md). |
| Agent, T3 Code and desktop | Follow the [T3 Code](../agents/t3-code.md), [browser](../agents/browser-testing.md) and [XRDP](../workstations/xrdp.md) guides. Selected upgrade and workflow checks do not close fresh-install, enrollment or release-matrix gaps. |
| Ubuntu WSL and Windows | Finish [WSL qualification](ubuntu-wsl-support.md); native [Windows build hosts](windows-support.md) remain planned and outside the current stable-release gate. |
| CachyOS and mobile | Use the existing [CachyOS profile](../workstations/cachyos.md). Further workstation enhancements and the [mobile proposal](mobile-agent-support.md) are deferred. |
| Games and publishing | Keep [Godot/release bundle scope](game-publishing.md) bounded. Live provider behavior, rendering and automatic public posts remain gated in the [communications plan](game-release-communications.md). |
| Broader recovery | Test restore on another physical host and remaining storage/network failures; use the [roadmap](roadmap.md) and [transaction recovery guide](../operations/transaction-recovery.md). |

## Backlog references

These links preserve context, not a scheduled commitment or a live GitHub issue
status inventory. Define a bounded deliverable before starting:

- [#91: interactive orchestration](https://github.com/bluehexagons/basaltwater/issues/91)
  and [#28: scheduling](https://github.com/bluehexagons/basaltwater/issues/28):
  reuse existing operation ownership rather than adding competing schedulers.
- [#63: secrets](https://github.com/bluehexagons/basaltwater/issues/63):
  follow the deployment-secret plan.
- [#105](https://github.com/bluehexagons/basaltwater/issues/105) and
  [#107](https://github.com/bluehexagons/basaltwater/issues/107): the
  [maintenance supervisor](../operations/maintenance-agents.md) and
  [independent task worker](../agents/agent-tasks.md) exist. Define remaining
  fleet evidence, conversational authority and cost boundaries before broader
  agent automation.
- [#25: multimedia bundles](https://github.com/bluehexagons/basaltwater/issues/25),
  [#83: APT cache](https://github.com/bluehexagons/basaltwater/issues/83),
  [#87: preferences](https://github.com/bluehexagons/basaltwater/issues/87) and
  [#100: Akaunting](https://github.com/bluehexagons/basaltwater/issues/100):
  establish scope and an operation/recovery owner.
- [#111: Arch certificates](https://github.com/bluehexagons/basaltwater/issues/111)
  and [#112: unavailable tools](https://github.com/bluehexagons/basaltwater/issues/112):
  obtain a reproducible case and clear acceptance.

## Keeping plans useful

Update status, next actions and acceptance in place. Link detailed contracts
instead of duplicating them. Keep commands, revisions, raw evidence and progress
chronologies in private work records; do not append change logs to plans.
GitHub issues own discussion, operator guides own usage, and the coverage map
owns qualification limits.
