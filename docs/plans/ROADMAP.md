# Project Roadmap

Status: active planning guidance for the upcoming `v2.0.0` stable release.

This roadmap is intentionally opinionated. Basaltwater already supports a
wide range of setup and operations tasks; the next releases should complete
the safety and recovery loops around those tasks before adding more operating
systems, desktop applications, or service-specific installers.

See the [planning and issue index](README.md) for the portfolio view and the
[GitHub issue triage](GITHUB_ISSUE_TRIAGE_2026-08-17.md) for current
issue-to-implementation evidence.

## Basaltwater release checkpoint (2026-09-19)

The rename's repository implementation is complete in
[PR #101](https://github.com/bluehexagons/infra_tools/pull/101): distribution
`basaltwater`, command `basaltw`, a supported v2 `infra-tools` transition
launcher, migration documentation, refreshed agent guidance, and visual assets.
Persistent paths, import modules, service identities and skill IDs remain
stable. The [rename plan](BASALTWATER_RENAME.md) owns the detailed scope;
the [release checklist](../BASALTWATER_RELEASE.md) owns disposable-host
qualification and external publication. Implementation complete does not mean
merged, tagged, published or live-host qualified. These release checks do not
replace or reorder the reliability work below.

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

## Verification snapshot (2026-08-24)

The ordering remains justified by the current implementation:

- required command failures now raise from `remote_utils.run(check=True)`;
  nginx setup, firewall initialization, SSH reload, and CI/CD prerequisite
  paths propagate required failures with focused coverage. The argv-native
  command API and high-impact required-caller migrations now cover deployment,
  storage, app-server, build-server, and CI/CD setup paths; the broader caller
  classification remains incomplete;
- full setup no longer tears down all managed services before replacement
  steps run;
- manifest deployment builds and validates a sibling release before stopping
  app-scoped services, restores the prior release and unit files after failed
  activation, and treats exhausted health checks as deployment failures;
- manifest builds use application-specific accounts, automatically detect
  common language runtimes, and retain isolated Node/uv build homes;
- deployment-owned Nginx files are snapshotted, validated, atomically written,
  and restored on failure without overwriting unmanaged same-name sites;
- persistent JSON state/configuration now uses the shared atomic writer, but
  corrupt-state readers still fall back permissively;
- shared SSH/SCP/rsync, Proxmox node builders, and CI/CD deployment targets
  require strict checking against explicitly enrolled workspace host keys;
- remote setup and manifest activation now use versioned markers that block
  overlapping work and preserve interruption/recovery context; remembered
  target state is finalized only after setup succeeds;
- package metadata now produces a self-contained wheel, local `make check`
  builds and installs it in an isolated smoke environment, and tagged-release
  CI installs, smoke-tests, and publishes the artifact;
- Proxmox read-only status/list/health/top/audit commands share the versioned
  JSON envelope used by provider-neutral VM commands;
- signed webhook deliveries have no delivery-ID replay protection; this is
  intentionally deferred until CI/CD work resumes;
- command diagnostics redact complete quoted shell values, and the shared
  command runner now enforces a caller-overridable one-hour bound; and
- active agent config staging rejects source symlinks, and manifest release
  activation now fails closed on app-unit stop or verification failures.
- the old callback-based transaction framework has been removed; sync and scrub
  now fail explicitly, while durable setup/deployment operation records are
  landed and retain interruption/recovery context.
- hosted Proxmox provisioning now accepts fractional memory sizes, reconciles
  provider-side vCPU drift when requested, and uses the corrected named-disk
  allocation syntax; these remain covered by focused provider tests.

The manifest environment-key injection finding is resolved. CI now tests
Python 3.13, the interpreter shipped by Debian Trixie, and `make compile`
propagates compilation failures.
Those completed items should remain regression coverage, not active roadmap
work.

The following work packets are complete as of this snapshot:

- the first high-impact `remote_utils.run()` caller migration, including the
  argv-native API and focused setup coverage;
- the local wheel-install smoke test and consistent Proxmox status/`--json`
  output; and
- explicit CI/CD deployment-target SSH enrollment with fail-closed setup.

The best next work packets are:

1. Continue the `remote_utils.run()` caller inventory beyond the completed
   nginx, firewall, SSH reload, and CI/CD prerequisite paths, moving remaining
   required setup callers toward strict, argv-native execution. Strict,
   secret-safe, and bounded execution are already landed; intentional probes
   and cleanup remain best-effort.
2. Add lightweight, phase-specific recovery inspection and rerun guidance to
   the landed target-setup and manifest markers; do not build a generalized
   transaction coordinator.
3. Replace permissive corrupt-state fallbacks with schema/version checks and
   actionable remediation.
4. Add low-administration release and operator improvements around the
   supported recovery path and command-level diagnostics.

## Planning portfolio and GitHub issue alignment (2026-08-24)

The issue tracker was reconciled with the implementation and the plans in this
document on 2026-08-24. Five completed operator-facing requests were closed
after their documentation and test evidence was checked. Partial requests
remain open with their residual scope recorded. The
[planning index](README.md) separates active projects, queued dependencies,
unscheduled issue backlog, and completed reference records. See the complete
[GitHub issue triage](GITHUB_ISSUE_TRIAGE_2026-08-17.md) for issue-by-issue
evidence and ownership. The five closure comments on GitHub link the same
implementation evidence.

The triage confirms that Proxmox VM provisioning, guest lifecycle management,
local self-setup, Proxmox restart defaults, and Debian kernel-package cleanup
are delivered. Interactive setup orchestration, generic deployment secrets,
unified diagnostics, cross-host scheduling, and live XRDP verification remain
follow-up work. APT caching, desktop widget preferences, and multimedia
bundles are not current implementation priorities.

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
infra-tools controller lightweight and independent of Docker, a Coolify
service, or an external application-management API. The [Coolify integration
plan](COOLIFY_INTEGRATION.md) records an optional future boundary for complex
applications such as Akaunting; it does not change the supported deployment
path or create a dependency.

The direct deployment and webhook paths should converge on `infra.json` as the
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

## Small improvements to land continuously

Small, well-contained fixes should not wait for a larger phase. Good follow-ups
include plugin import fault isolation, extending consistent `--json` support to
remaining read-only commands, and auditing remaining non-JSON configuration
writes for atomic replacement and permissions.

## Deliberately deferred

Until P0 and P1 are substantially complete, avoid prioritizing:

- additional browsers, desktop environments, and language installers;
- broad non-Debian support;
- multiple network providers before safe Proxmox apply exists; and
- a generalized public plugin SDK before startup isolation is in place;
- webhook delivery replay protection before CI/CD work resumes; and
- updater signature infrastructure or other recurring trust administration
  without a concrete operational need.

These features increase surface area without improving the reliability of the
core workflows operators already depend on.
