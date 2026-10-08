# Project Roadmap

Status: active planning guidance for the `v2.0.0` release-candidate preview
series and its eventual stable release.

This roadmap is intentionally opinionated. Basaltwater already supports a
wide range of setup and operations tasks; the next releases should complete
the safety and recovery loops around those tasks before adding more operating
systems, desktop applications, or service-specific installers.

See the [planning and issue index](README.md) for the portfolio view and the
[GitHub issue triage](GITHUB_ISSUE_TRIAGE_2026-08-17.md) for current
issue-to-implementation evidence.

## Current pass checkpoint (2026-10-08)

The source package version is `2.0.0rc1`, corresponding to the opt-in preview
tag `v2.0.0-rc.1`. No preview tag or public release has been created. The
release checklist now defines the preview publication gate, stable live-test
matrix, evidence format, and final sign-off. The current pass is release
qualification; it does not include further CachyOS enhancements.

The P0 transactional-execution residuals remain active: finish the required
command-caller inventory, add phase-specific recovery guidance, and replace
permissive corrupt-state fallbacks with actionable errors. Security-monitor
backlog pagination is implemented; issue #108 still needs reliable detection of
source-retention gaps and a release disposition. The maintainers report no
recurrence for #102 and #103 and the code fixes are in `main`, but both issue
records still request checks on their originally affected hosts. The release
matrix tracks broad candidate checks; those issue-specific acceptance checks
remain open.

## Product direction

The project should become a small, dependable infrastructure reconciler for
Debian and Proxmox environments. A successful operation should mean more than
"the commands ran": the requested state was validated, the change was applied,
the result was verified, and a documented recovery path remains available.

Priorities are ordered by these principles:

1. A failed operation must stop and preserve or restore the last working state.
2. One declaration should drive manual setup, redeploys, and webhook CI/CD.
3. Every destructive or connectivity-sensitive apply path needs a preview and
   rollback path.
4. Saved configuration should support fleet-wide audit and drift detection.
5. New providers and platforms should reuse these guarantees rather than add
   parallel execution models.

These guarantees should remain proportional to the tool's small-business
operating model. Prefer safeguards that reduce operator effort and make
failures obvious; defer controls that require recurring manual administration
until actual usage or incidents justify them.

## Issue and project ownership

The [planning index](README.md) owns the current portfolio, queued work, and
unscheduled issue backlog. The
[GitHub issue triage](GITHUB_ISSUE_TRIAGE_2026-08-17.md) records issue-specific
implementation evidence and acceptance. Keep historical closure details in
GitHub and the issue triage instead of duplicating them here.

## P0: Transactional execution and state

The first priority is resolving the execution and partial-apply risks captured
in [the architectural risk review](ARCHITECTURAL_RISK_REVIEW_2026-08-07.md).
The detailed implementation plan is in
[Transactional execution and reconciliation](TRANSACTIONAL_EXECUTION.md).

Required outcomes:

- command helpers have explicit fail-fast and best-effort contracts;
- setup stages service changes and does not remove a working service until its
  replacement is ready;
- deployment health checks gate success and restore the previous release when
  activation fails;
- persistent state is written atomically and corrupt state produces actionable
  errors instead of silent defaults; and
- privileged SSH paths use verified host keys rather than automatic first-use
  trust.

This work is the foundation for every later apply or rollback feature.

## P1: One manifest-driven deployment platform

Planning update, 2026-09-07: Coolify evaluation is deferred. Keep the
Basaltwater controller lightweight and independent of Docker, a Coolify
service, or an external application-management API. The [Coolify integration
plan](COOLIFY_INTEGRATION.md) records an optional future boundary for complex
applications such as Akaunting; it does not change the supported deployment
path or create a dependency.

The direct deployment and webhook paths should converge on `basaltwater.json` as the
shared application model.

The sequence should be:

1. Implement [deploy secrets and optional components](DEPLOY_SECRETS.md).
2. Teach [CI/CD to reuse manifests](CICD_MANIFEST_REUSE.md).
3. Add component-level change detection and artifact reuse after correctness is
   shared across both entry points.

The desired result is one definition for components, build inputs, secrets,
runtime users, health checks, routing, and persistent data. Repository-authored
scripts may remain an escape hatch, but they should not be the primary model.

## P1: Plan, audit, and drift detection

`--dry-run` is useful, but it is not a desired-versus-observed state diff. Add
a read-only planning layer before expanding mutation features:

- proposed `basaltw plan HOST` reports changes and unavailable facts;
- proposed `basaltw audit PATTERN` checks saved hosts without changing them;
- plans use stable change categories and meaningful exit codes;
- text output remains human-friendly while JSON output is consistent enough
  for CI and external tooling; and
- the applied result is verified against the same observations used by the
  plan.

Start with services, managed files, packages, and deployments. Avoid promising
a universal package-level diff until the existing setup steps expose enough
structured state.

The Proxmox-specific facts, update preflights, and storage checks for this layer
are detailed in the
[Proxmox setup and maintenance audit](PROXMOX_MAINTENANCE_AUDIT_2026-08-09.md).

The CLI-only coding-host slice is detailed in the
[agent host and maintenance audit](AGENT_CLI_MAINTENANCE_AUDIT_2026-08-09.md).
Its P1 work adds verified/versioned agent-tool updates, workload-aware restart
holds and maintenance windows, and agent/timer health to the same audit surface.
It should reuse the transactional execution and observed-state contracts rather
than introduce agent-specific apply machinery.

The RDP-capable coding-desktop slice is detailed in the
[RDP desktop agent audit](DESKTOP_AGENT_MAINTENANCE_AUDIT_2026-08-09.md). It
adds RDP exposure and certificate policy, supported session lifecycle,
version-aware configuration, and live desktop smoke tests to those shared
contracts.

## P2: Recovery as a first-class workflow

Backup creation without tested restoration is incomplete. Build operator-facing
recovery commands for the assets the project already manages:

- restore, retention, and scheduled verification for Proxmox backups;
- application and database `backup`, `restore`, and `rollback` operations;
- a recovery inventory showing available snapshots, backups, and releases; and
- documented destructive confirmations and dry runs for every restore path.

Restore verification should be designed before adding more backup backends.
The Proxmox discovery, retention, verification-job, and isolated-restore slice
is scoped in the
[Proxmox setup and maintenance audit](PROXMOX_MAINTENANCE_AUDIT_2026-08-09.md).
Storage integrity proposals for verify-before-sync snapshots, locking,
interrupted-manifest reconciliation, resumable scans, incident notifications,
and recovery retention remain unscheduled; their scope is in the
[storage integrity review](../STORAGE_REVIEW.md).

## P2: Safe network apply and rollback

The existing network inventory and Proxmox renderer are the right boundary for
a controlled apply workflow. The first mutation path should:

1. validate management sources and current reachability;
2. save the exact current firewall artifacts;
3. install a timed automatic rollback before applying changes;
4. apply and verify management connectivity; and
5. require explicit confirmation before cancelling the rollback.

Switch, router, and cloud adapters should follow only after the Proxmox apply
contract proves this safety model.

## P3: Extensibility and release quality

Once the lifecycle work is established:

- isolate malformed plugins so one optional capability cannot break unrelated
  commands;
- document a stable provider/adapter contract for network and secret backends;
- retain the landed packaging installation smoke tests and explicit package
  metadata;
- add linting, incremental type checking, and a measured coverage floor; and
- consider another Debian-like distribution only when its CI and live-test
  expectations can be stated precisely.

Broad operating-system support is not currently worth the extra branching in
security, package, service, and networking behavior.

The Godot workflow-bundle surface is intentionally incremental. The delivered
`web` and `publishing` bundles establish repeatable selection, user-scoped
tooling, verified release artifacts where publishers provide digests, and
weekly reconciliation. Later P3 slices may add `dotnet`, `android`,
`gdextension`, and `assets` once each bundle has a
version-compatibility contract, architecture policy, bounded installation,
headless verification, and an update path. Until then those names remain
documented roadmap values rather than accepted CLI choices.

The [game publishing plan](GAME_PUBLISHING.md), finalized 2026-10-05, extends
the installed Butler/SteamCMD tools with authentication and upload management
inside the existing web panel, plus unattended and scheduled uploads using
VM-local sessions. The initial implementation now includes independent tool
selection, shared maintenance leases, native panel login, durable uploads and
panel-owned polling. Live provider qualification remains open; this work does
not reorder P0/P1 priorities. The [operator guide](../GAME_PUBLISHING.md) records
the current contract and limits.
The [release and communications extension](GAME_RELEASE_COMMUNICATIONS.md)
adds build promotion and human-reviewed writing, translation, and publication.
Steam default-branch release stays manual on Steamworks. Steam announcements
and itch.io posts currently use reviewed exports and tracked human editor
handoffs; automatic post submission, rich assets, website/blog and social
adapters remain follow-on work. Supported manifest language settings default
to English only; English/Spanish structural tests cover both translation
directions, and other validated language tags remain allowed.

## Small improvements to land continuously

Small, well-contained fixes should not wait for a larger phase. Good follow-ups
include plugin import fault isolation, extending consistent `--json` support to
remaining read-only commands, and auditing remaining non-JSON configuration
writes for atomic replacement and permissions.

## Deliberately deferred

Until P0 and P1 are substantially complete, additional platform and installer
work remains lower priority.

Android support is additionally deferred by explicit project direction as of
2026-10-02. The [mobile agent support proposal](MOBILE_AGENT_SUPPORT.md) covers
potential Android hosts and iOS through an operator-provided SSH Mac. Neither
is an accepted setup capability or a scheduled implementation commitment;
viewport-based web checks do not count as native mobile qualification.

The remaining deferred work includes:

- additional browsers, desktop environments, and language installers;
- broad non-Debian support;
- multiple network providers before safe Proxmox apply exists; and
- a generalized public plugin SDK before startup isolation is in place;
- webhook delivery replay protection before CI/CD work resumes; and
- updater signature infrastructure or other recurring trust administration
  without a concrete operational need.

These features increase surface area without improving the reliability of the
core workflows operators already depend on.
