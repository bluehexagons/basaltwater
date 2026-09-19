# Planning and Issue Index

Status: current portfolio index for the upcoming `v2.0.0` stable release. This
file organizes active project plans,
audit inputs, and open GitHub issues without replacing the detailed scope in
the [project roadmap](ROADMAP.md) or the
[issue triage](GITHUB_ISSUE_TRIAGE_2026-08-17.md), reverified 2026-08-24.

## Planning model

- **P0** is the reliability foundation and should block dependent mutation
  work.
- **P1** extends that foundation into shared deployment and audit workflows.
- **P2** adds recovery and broader safe-apply workflows after their underlying
  state and verification contracts exist.
- **P3** improves extensibility and release quality once the core lifecycle is
  dependable.
- Prioritize safeguards that reduce operator effort; defer controls that add
  recurring manual administration until usage or incidents justify them.
- **Unscheduled** work needs a project brief or an explicit roadmap slot before
  implementation starts.
- **Reference** documents preserve evidence or completed review decisions; they
  are not independent implementation queues.

The roadmap owns priority. The issue triage owns issue-to-implementation
evidence. A detailed project plan owns delivery sequence and acceptance
criteria.

The live operator contract is maintained under `docs/`; these plans may
intentionally describe work deferred beyond `v2.0.0`.

## Project portfolio

| Project | State | Priority | Issue alignment | Canonical plan and next boundary |
| --- | --- | --- | --- | --- |
| Basaltwater identity and rename | v2 package/CLI cutover implemented; public hosting and visual identity pending | Unscheduled | No dedicated issue | [Basaltwater rename](BASALTWATER_RENAME.md): `basaltwater` package and `basaltw` command, supported v2 transition launcher, retained state/resource contracts and upgrade/rollback instructions; next qualify live VMs and prepare public hosting and visual identity. |
| Transactional execution and state | Active; setup/deploy markers landed | P0 | [#97](https://github.com/bluehexagons/infra_tools/issues/97) | [Transactional execution](TRANSACTIONAL_EXECUTION.md): add safe phase-specific recovery, tighten corrupt-state handling, and finish the command-caller inventory. |
| Application platform ownership and Coolify | Deferred | Unscheduled | No dedicated issue | [Coolify integration](COOLIFY_INTEGRATION.md): record the decision to keep the controller lightweight and revisit Coolify only as an optional, isolated platform for a concrete complex application. |
| Manifest deployment platform | Queued behind P0 | P1 | [#63](https://github.com/bluehexagons/infra_tools/issues/63) and the remaining deployment scope of [#97](https://github.com/bluehexagons/infra_tools/issues/97) | Implement [deploy secrets](DEPLOY_SECRETS.md), transactional activation, then [CI/CD manifest reuse](CICD_MANIFEST_REUSE.md) for the native path. |
| Go site convention and Rails retirement | Proposed for the next fresh production VM | P1 | Deployment scope of [#97](https://github.com/bluehexagons/infra_tools/issues/97) | [Go site convention](GO_SITE_CONVENTION.md): implement versioned root-service inference, standard runtime/health/state contracts, compact monorepo composition, the goclick data cutover, and eventual Ruby/Rails removal. |
| Plan, audit, and drift detection | Active in domain-specific slices | P1 | [#58](https://github.com/bluehexagons/infra_tools/issues/58), [#38](https://github.com/bluehexagons/infra_tools/issues/38), and part of [#97](https://github.com/bluehexagons/infra_tools/issues/97) | [Roadmap](ROADMAP.md), informed by the Proxmox, agent-host, and desktop audit documents below. Define one stable observation/result contract before adding more domain commands. |
| Interactive orchestration | Needs a project brief; depends on P0 | Unscheduled | [#91](https://github.com/bluehexagons/infra_tools/issues/91) | Reuse setup parsing, workspace records, and transactional execution; do not create a second provisioning engine. |
| Agent VM workspaces and credentials | Implemented; authenticated non-GitHub providers and offline snapshots deferred | Reference | No dedicated issue | [Agent VM workspaces](AGENT_VM_WORKSPACES.md): explicit tools, VM-level Git policy, active-user or specified-file credentials, guided setup, and optional Playwright browser automation are available. |
| One shared desktop session per machine | Runtime, setup, skills and initial productivity tools implemented; live qualification open | Unscheduled | Desktop resize/reconnect validation overlaps the desktop audit | [Single-session desktop](SINGLE_SESSION_DESKTOP.md): qualify human RDP reconnect/resize and native productivity workflows; accessibility, application adapters and broader client coverage remain follow-on work. |
| CachyOS agentic desktop capabilities | Initial native setup and T3 access implemented; live qualification pending | Unscheduled | No dedicated issue | [CachyOS agentic desktop](CACHYOS_AGENTIC_DESKTOP.md): qualify package bundles, T3 local/LAN pairing and Connect, GPU/session behavior, then add browser, AT-SPI, portal, application, maintenance, and collaboration slices. |
| Shared Debian and CachyOS capability contracts | Proposed; constrained to local-tool overlap and blocked behind core reliability work | Unscheduled | No dedicated issue | [Shared capability contracts](CROSS_DISTRO_SHARED_CAPABILITIES.md): inventory the overlap, add pure capability metadata and adapter tests, then migrate only individually proven low-risk capabilities without broadening OS support. |
| Godot workflow bundles | Web and publishing delivered; .NET, Android, GDExtension, and asset authoring queued | P3 | No dedicated issue | [Godot guide](../GODOT.md) and [roadmap](ROADMAP.md): retain repeatable bundle selection and add later bundles only with compatibility, verification, and update contracts. |
| Generic VM management, agent interfaces, and lightweight Git hosting | Active; provider/schema-tagged Proxmox host records, the initial VM data-disk/mount slice, explicit Gogs LFS paths, and the server-only T3 Code lifecycle implemented; mutation/recovery slices dependency-gated | Unscheduled | No dedicated issue | [VM management, agent interfaces, and lightweight Git hosting](VM_MANAGEMENT_AND_LIGHTWEIGHT_GIT_HOSTING.md): next validate the storage slice on live Proxmox, then sequence provider-neutral VM observation and Gogs safety/health. T3 Code desktop AppImage support is no longer in scope. |
| Lightweight service monitoring and application candidates | Monitoring proposed; HomeBox native support delivered | Unscheduled | [#99](https://github.com/bluehexagons/infra_tools/issues/99) for HomeBox | [Lightweight service and monitoring candidates](LIGHTWEIGHT_SERVICE_CANDIDATES.md): support Gatus first for service checks, Beszel second for VM telemetry, and consider Memos as the first team application; exclude PHP and gate mandatory PostgreSQL dependencies. [HomeBox support](HOMEBOX_SUPPORT.md) records delivered setup, recovery, recurring updates, and full VM qualification still to run. |
| Recovery workflows | Queued behind transaction and deployment state | P2 | Recovery portion of [#97](https://github.com/bluehexagons/infra_tools/issues/97) | [Roadmap](ROADMAP.md), with Proxmox backup and restore details in the [Proxmox audit](PROXMOX_MAINTENANCE_AUDIT_2026-08-09.md). |
| Safe network apply and rollback | Address handoff delivered; firewall apply remains | P2 | No dedicated open issue | [Roadmap](ROADMAP.md): extend the verified host/guest address handoff model to reviewed Proxmox firewall artifacts with timed rollback and connectivity confirmation. |
| Extensibility and release quality | Queued | P3 | No dedicated open issue | [Roadmap](ROADMAP.md): plugin isolation, retained packaging smoke tests, lint/type/coverage gates, and a documented provider contract. |

## Unscheduled issue backlog

These open issues have a documented disposition but no standalone delivery
plan. Write or assign a project brief before implementation so they do not
silently compete with P0/P1 work.

| Issue | Disposition | Planning boundary |
| --- | --- | --- |
| [#85 — Improve RAM over-provisioning behavior](https://github.com/bluehexagons/infra_tools/issues/85) | Backlog | Define an explicit saved cluster/workload policy; do not infer cache pressure from VM labels. |
| [#28 — Scheduling system](https://github.com/bluehexagons/infra_tools/issues/28) | Backlog | Extend the storage orchestrator with maintenance windows, coordination, and resource-aware ordering. |
| [#83 — Apt cache support](https://github.com/bluehexagons/infra_tools/issues/83) | Backlog | Specify server ownership, validated client configuration, failure behavior, and rollback first. |
| [#87 — More default config options](https://github.com/bluehexagons/infra_tools/issues/87) | Backlog | Add a versioned preferences contract only when a supported desktop profile can own it. |
| [#25 — Multimedia packages](https://github.com/bluehexagons/infra_tools/issues/25) | Deferred | Reconsider after the transactional, audit, and recovery priorities are substantially complete. |

The five issues completed in `main` were closed during the 2026-08-24 tracker
reconciliation; the issue triage records their evidence and remaining scope.

## Audit and decision records

| Document | Lifecycle | Use |
| --- | --- | --- |
| [Architectural risk review](ARCHITECTURAL_RISK_REVIEW_2026-08-07.md) | Reference with open findings | Evidence feeding transactional execution, startup isolation, and CI/CD trust-boundary work. |
| [Proxmox setup and maintenance audit](PROXMOX_MAINTENANCE_AUDIT_2026-08-09.md) | Active roadmap input | Proxmox update, observability, recovery, hardening, and policy slices. |
| [CLI-only agent host audit](AGENT_CLI_MAINTENANCE_AUDIT_2026-08-09.md) | Active roadmap input | Agent update lifecycle, maintenance windows, audit output, and cache/credential lifecycle. |
| [RDP desktop agent audit](DESKTOP_AGENT_MAINTENANCE_AUDIT_2026-08-09.md) | Active roadmap input | XRDP identity, configuration rollback, workload safety, and live smoke coverage. |
| [Test slop audit](TEST_SLOP_AUDIT_2026-08-09.md) | Complete reference | Records test-retention decisions; it is not active project work. |
| [Test suite audit and coverage plan](TEST_SUITE_AUDIT_2026-09-02.md) | Finalized implementation plan | Canonical backlog for test coverage, domain grouping, redundancy reduction, and unowned test-surface decisions. |
| [Codebase audit](CODEBASE_AUDIT_2026-09-13.md) | Complete reference | Findings addressed; current operator contracts cover recovery, CI/CD credential isolation, installer policy, and Proxmox maintenance. Live environment qualification remains separate. |
| [Codebase audit](CODEBASE_AUDIT_2026-08-21.md) | Reference with residual items | Earlier cross-cutting packaging, host-key, state, failure-contract, webhook, and destructive-CLI findings; remaining items are consolidated in the current residual plan. |

## Keeping the portfolio current

When work lands, update the detailed owner first, then this index, the roadmap,
and the issue triage when their status or ordering changes. Do not create a new
plan for a slice already owned by one of the projects above. Move completed
audit documents to reference status rather than leaving them in the active
queue, and record newly deferred work explicitly instead of relying on file
age or issue inactivity.
