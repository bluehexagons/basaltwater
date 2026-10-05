# Planning and Issue Index

Status: current portfolio index for the upcoming `v2.0.0` stable release. This
file organizes active project plans,
audit inputs, and open GitHub issues without replacing the detailed scope in
the [project roadmap](ROADMAP.md) or the
[issue triage](GITHUB_ISSUE_TRIAGE_2026-08-17.md), reverified 2026-09-27.

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

Current examples use `basaltw`. Dated audits and historical proposals retain
the names used when their evidence was recorded; they do not override the
[current CLI reference](../COMMAND_LINE.md). Rename implementation and public
release are separate states, tracked in the [release checklist](../BASALTWATER_RELEASE.md).

## Project portfolio

| Project | State | Priority | Issue alignment | Canonical plan and next boundary |
| --- | --- | --- | --- | --- |
| Basaltwater identity and rename | Repository implementation delivered; release qualification pending | Unscheduled | Stable release [#104](https://github.com/bluehexagons/basaltwater/issues/104) | [Basaltwater rename](BASALTWATER_RENAME.md): source/runtime rename, recent-install migration, documentation, skills and visual identity delivered; existing storage follows the [persistent identity policy](BASALTWATER_CONTRACTS.md#persistent-storage-identities); [release checklist](../BASALTWATER_RELEASE.md) tracks live VM qualification and external publication. |
| Transactional execution and state | Active; setup/deploy markers landed | P0 | Former umbrella [#97](https://github.com/bluehexagons/basaltwater/issues/97), now closed | [Transactional execution](TRANSACTIONAL_EXECUTION.md): add safe phase-specific recovery, tighten corrupt-state handling, and finish the command-caller inventory. |
| Application platform ownership and Coolify | Deferred | Unscheduled | Akaunting request [#100](https://github.com/bluehexagons/basaltwater/issues/100) | [Coolify integration](COOLIFY_INTEGRATION.md): record the decision to keep the controller lightweight and revisit Coolify only as an optional, isolated platform for a concrete complex application. |
| Manifest deployment platform | Queued behind P0 | P1 | [#63](https://github.com/bluehexagons/basaltwater/issues/63); broader scope stays in this plan after closure of #97 | Implement [deploy secrets](DEPLOY_SECRETS.md), transactional activation, then [CI/CD manifest reuse](CICD_MANIFEST_REUSE.md) for the native path. |
| Go site convention and Rails retirement | Proposed for the next fresh production VM | P1 | Deployment scope retained after closure of [#97](https://github.com/bluehexagons/basaltwater/issues/97) | [Go site convention](GO_SITE_CONVENTION.md): implement versioned root-service inference, standard runtime/health/state contracts, compact monorepo composition, the goclick data cutover, and eventual Ruby/Rails removal. |
| Plan, audit, and drift detection | Active in domain-specific slices | P1 | Former [#58](https://github.com/bluehexagons/basaltwater/issues/58) and [#38](https://github.com/bluehexagons/basaltwater/issues/38) scopes, both closed; current audit defect [#102](https://github.com/bluehexagons/basaltwater/issues/102) | [Roadmap](ROADMAP.md), informed by the Proxmox, agent-host, and desktop audit documents below. Define one stable observation/result contract before adding more domain commands. |
| Interactive orchestration | Needs a project brief; depends on P0 | Unscheduled | [#91](https://github.com/bluehexagons/basaltwater/issues/91) | Reuse setup parsing, workspace records, and transactional execution; do not create a second provisioning engine. |
| Agent VM workspaces and credentials | Implemented; authenticated non-GitHub providers and offline snapshots deferred | Reference | No dedicated issue | [Agent VM workspaces](AGENT_VM_WORKSPACES.md): explicit tools, VM-level Git policy, target-owned Codex login, explicit provider files, separate GitHub active-user credentials, guided setup, and optional Playwright browser automation are available. |
| T3 Code compatibility and agent guidance | T3 v0.0.45 and complete Codex/OpenCode/Claude skill bundles delivered; live qualification open | Unscheduled | No dedicated issue | [T3 Code VM improvements](T3_CODE_AGENT_VM_IMPROVEMENTS.md): qualify setup reruns, provider skill discovery, client-origin preview boundaries, and environment-local recording transfer. |
| Mobile workflows for agents | Android implementation deferred; iOS proposal unscheduled | Unscheduled | No dedicated issue | [Mobile agent support](MOBILE_AGENT_SUPPORT.md): define read-only host observations, explicit SDK ownership, an eligible Android device host, SSH-hosted macOS/Xcode simulation, and project connectivity before implementation. |
| One shared desktop session per machine | Runtime, setup, skills and initial productivity tools implemented; live qualification open | Unscheduled | Desktop resize/reconnect validation overlaps the desktop audit | [Single-session desktop](SINGLE_SESSION_DESKTOP.md): qualify human RDP reconnect/resize and native productivity workflows; accessibility, application adapters and broader client coverage remain follow-on work. |
| CachyOS agentic desktop capabilities | Initial native setup and T3 access implemented; live qualification pending | Unscheduled | Software qualification [#106](https://github.com/bluehexagons/basaltwater/issues/106) | [CachyOS agentic desktop](CACHYOS_AGENTIC_DESKTOP.md): qualify package bundles, T3 local/LAN pairing and Connect, GPU/session behavior, then add browser, AT-SPI, portal, application, maintenance, and collaboration slices. |
| Ubuntu WSL on Windows 11 | Plan ready for implementation; Windows qualification pending | Unscheduled | No dedicated issue | [Ubuntu WSL support](UBUNTU_WSL_SUPPORT.md): begin with WSL capability detection and resumable Windows bootstrap, then tools/T3 desktop, Linux/native jobs, and qualification. |
| Shared Debian and CachyOS capability contracts | Proposed; constrained to local-tool overlap and blocked behind core reliability work | Unscheduled | No dedicated issue | [Shared capability contracts](CROSS_DISTRO_SHARED_CAPABILITIES.md): inventory the overlap, add pure capability metadata and adapter tests, then migrate only individually proven low-risk capabilities without broadening OS support. |
| Godot workflow bundles | Web hosting and publishing-tool installation delivered; .NET, GDExtension, and asset authoring queued; Android deferred | P3 | No dedicated issue | [Godot guide](../GODOT.md) and [roadmap](ROADMAP.md): retain repeatable bundle selection and add later bundles only with compatibility, verification, and update contracts; mobile host prerequisites belong to the [mobile proposal](MOBILE_AGENT_SUPPORT.md). |
| Game publishing from VMs | Initial panel/CLI workflows implemented; live qualification and richer adapters open | Unscheduled | No dedicated issue | [Operator guide](../GAME_PUBLISHING.md), [game publishing](GAME_PUBLISHING.md) and [release communications](GAME_RELEASE_COMMUNICATIONS.md): VM-local authentication, unattended uploads, beta/channel promotion, reviewed English/Spanish writing and scheduled editor handoffs. Steam default release stays manual; automatic posts, rich assets and additional destinations remain qualification-gated. |
| Generic VM management, agent interfaces, and lightweight Git hosting | Active; provider/schema-tagged Proxmox host records, the initial VM data-disk/mount slice, explicit Gogs LFS paths, and the server-only T3 Code lifecycle implemented; mutation/recovery slices dependency-gated | Unscheduled | No dedicated issue | [VM management, agent interfaces, and lightweight Git hosting](VM_MANAGEMENT_AND_LIGHTWEIGHT_GIT_HOSTING.md): next validate the storage slice on live Proxmox, then sequence provider-neutral VM observation and Gogs safety/health. T3 Code desktop AppImage support is no longer in scope. |
| Lightweight service monitoring and application candidates | Monitoring proposed; HomeBox native support delivered | Unscheduled | [#99](https://github.com/bluehexagons/basaltwater/issues/99) for HomeBox | [Lightweight service and monitoring candidates](LIGHTWEIGHT_SERVICE_CANDIDATES.md): support Gatus first for service checks, Beszel second for VM telemetry, and consider Memos as the first team application; exclude PHP and gate mandatory PostgreSQL dependencies. [HomeBox support](HOMEBOX_SUPPORT.md) records delivered setup, recovery, recurring updates, and full VM qualification still to run. |
| Background agent capabilities | Proposed project brief; implementation not started | Unscheduled | [#105](https://github.com/bluehexagons/basaltwater/issues/105) and [#107](https://github.com/bluehexagons/basaltwater/issues/107); scheduling scope relates to [#28](https://github.com/bluehexagons/basaltwater/issues/28) | [Background agent capabilities](BACKGROUND_AGENT_CAPABILITIES.md): start with a daily operations brief and a bounded task runner independent of T3, then evidence-backed incident investigation, recovery rehearsals, and drift review; extend existing scheduling owners for later coordination. |
| Recovery workflows | Queued behind transaction and deployment state | P2 | Former recovery scope of closed [#97](https://github.com/bluehexagons/basaltwater/issues/97) | [Roadmap](ROADMAP.md), with Proxmox backup and restore details in the [Proxmox audit](PROXMOX_MAINTENANCE_AUDIT_2026-08-09.md). |
| Safe network apply and rollback | Address handoff delivered; firewall apply remains | P2 | No dedicated open issue | [Roadmap](ROADMAP.md): extend the verified host/guest address handoff model to reviewed Proxmox firewall artifacts with timed rollback and connectivity confirmation. |
| Extensibility and release quality | Queued | P3 | No dedicated open issue | [Roadmap](ROADMAP.md): plugin isolation, retained packaging smoke tests, lint/type/coverage gates, and a documented provider contract. |

## Unscheduled issue backlog

The proposed [password manager sister project](PASSWORD_MANAGER_SISTER_PROJECT.md)
has a separate repository boundary and no scheduled implementation or linked
issue. Its plan covers vault-format evaluation, an extension-first client,
encrypted synchronization, browser filling, Basaltwater deployment and recovery,
and independent security review before a public release.

These open issues have a documented disposition but no scheduled implementation.
Write or assign a project brief and a roadmap slot before implementation so
they do not silently compete with P0/P1 work.

| Issue | Disposition | Planning boundary |
| --- | --- | --- |
| [#28 — Scheduling system](https://github.com/bluehexagons/basaltwater/issues/28) | Backlog; proposed direction documented | [Background agent capabilities](BACKGROUND_AGENT_CAPABILITIES.md) scopes independent task runs and later maintenance coordination; extend the storage orchestrator for its existing jobs without competing timers. |
| [#83 — Apt cache support](https://github.com/bluehexagons/basaltwater/issues/83) | Backlog | Specify server ownership, validated client configuration, failure behavior, and rollback first. |
| [#87 — More default config options](https://github.com/bluehexagons/basaltwater/issues/87) | Backlog | Add a versioned preferences contract only when a supported desktop profile can own it. |
| [#25 — Multimedia packages](https://github.com/bluehexagons/basaltwater/issues/25) | Partial, deferred | CachyOS has individual media tools; scope bundle semantics and profile support after reliability priorities. |
| [#100 — Akaunting support](https://github.com/bluehexagons/basaltwater/issues/100) | Deferred | Use [Coolify evaluation](COOLIFY_INTEGRATION.md) only when a concrete application recipe and recovery need justify it. |
| [#105 — Agent automation systems](https://github.com/bluehexagons/basaltwater/issues/105) and [#107 — Core system agent](https://github.com/bluehexagons/basaltwater/issues/107) | Proposed | [Background agent capabilities](BACKGROUND_AGENT_CAPABILITIES.md) supplies a read-only first slice and bounded runner; define interface and authority before implementation. |

Five implemented issues were closed during the 2026-08-24 tracker
reconciliation. The current [issue triage](GITHUB_ISSUE_TRIAGE_2026-08-17.md)
tracks the 13 issues still open.

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
