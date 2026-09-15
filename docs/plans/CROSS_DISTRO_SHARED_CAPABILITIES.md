# Shared Debian and CachyOS Capability Contracts

Status: proposed, unscheduled implementation plan. This is a constrained
maintainability project for the supported local-tool overlap. It must not turn
the Debian server reconciler into a general multi-distribution provisioning
engine, and it remains behind the P0/P1 reliability work in the
[project roadmap](ROADMAP.md).

## Decision summary

- Keep Debian and CachyOS as distinct profiles with distinct authority,
  lifecycle, and support boundaries.
- Share declarative capability intent, package/probe metadata, safe
  user-owned-file helpers, and tests where their contracts genuinely match.
- Keep package transactions, package-source policy, privilege escalation,
  service placement, firewall policy, remote staging, and machine policy in
  distro/profile adapters.
- Do not introduce a universal `PackageManager` abstraction until two concrete
  consumers require the same operation and its failure/recovery contract is
  specified.
- Begin with pure planning and verification data. Move a currently duplicated
  capability only after its Debian and CachyOS behavior has a tested parity
  matrix and a safe downgrade path.

## Problem and evidence

The `agent_cachyos` profile is deliberately a local, existing-user workflow.
It installs missing packages using the current pacman database, does not
synchronize repositories or upgrade the system, and retains user-owned desktop
policy. Debian setup is primarily remote and privileged: it may reconcile APT
sources and system packages, create users, manage services, and persist target
state. These are not interchangeable execution models.

There is nevertheless a meaningful overlap in user-facing intent: Git, coding
agents, repository workspaces, language runtimes, Git LFS, T3, managed skills,
PATH construction, and command readiness. The current CachyOS implementation
already reuses managed agent skills from `common.agent_steps`, but package
lists, readiness logic, user-session wrappers, and parts of the T3 lifecycle
remain parallel. That makes a new capability or probe likely to be implemented
twice and permits documentation, parser, package, and readiness behavior to
drift.

## Goals

- Give each portable local-tool capability one name, selection contract,
  package/probe mapping, and supported-profile matrix.
- Preserve the existing CachyOS guarantees: local non-root execution, only
  selected native dependencies may invoke `sudo`, no AUR/Flatpak/vendor package
  source changes, no package database sync, and no full-system upgrade.
- Preserve the Debian setup boundary and its existing remote/root operations.
- Make dry-run plans and readiness results explain both unsupported selection
  and unavailable prerequisites without running package commands.
- Let tests exercise shared metadata and behavior using fake distro adapters;
  retain focused integration tests for each profile's security boundary.
- Generate or validate operator documentation from the same support metadata
  where practical.

## Non-goals

- Supporting generic Arch, other Debian derivatives, or arbitrary package
  managers.
- Converting Debian server, desktop/XRDP, security, networking, Samba,
  Proxmox, or firewall steps to CachyOS.
- Translating `apt update` to `pacman -Sy`, adding repositories, or weakening
  the CachyOS ownership model for code reuse.
- Unifying root-owned Debian T3 deployment with the CachyOS user service.
- Replacing profile-specific CLI validation with a permissive cross-distro
  option set.

## Target model

Use a small, declarative capability catalog. A catalog entry describes intent,
not a shell command:

| Field | Purpose |
| --- | --- |
| Stable capability ID | Examples: `git`, `git_lfs`, `node_runtime`, `python_tooling`, `agent_skills`, `t3_runtime`. |
| Selection and profiles | Which configuration flags select it and which profiles support it. |
| Dependencies/conflicts | Other capabilities and explicit profile constraints. |
| Distro mapping | Native package names, user-runtime installer, or `unsupported`; never a fallback package guess. |
| Readiness probes | Commands and expected facts, with a bounded, redacted result contract. |
| Ownership/lifecycle | System package, user-managed runtime, root-managed service, or user service; update and removal policy. |
| Privilege/network policy | Whether `sudo`, a user session, loopback binding, or an interactive action is required. |

The catalog must be pure Python data plus validation. A provider adapter turns
an already-selected capability into a plan or apply action. It owns package
query/install syntax, package-manager locks, effective user, and recovery
advice. Adapters must return structured results rather than leak provider
commands into shared callers.

This deliberately stops short of a broad package-manager class hierarchy. The
initial adapters only need the operations already proven common: report package
availability, plan/install a fixed native package set, and verify required
commands. Update, upgrade, source repair, removal, and repository management
remain adapter-private until a future contract justifies sharing them.

## Boundaries

| Concern | Debian setup | CachyOS `agent_cachyos` | Shared? |
| --- | --- | --- | --- |
| Invocation | Controller/remote setup, often root | Existing local desktop user | No |
| System package lifecycle | APT source/lock/update policy as selected by Debian setup | Query first; install only missing packages; never sync or upgrade | Provider contract only |
| User tools and skills | Target user via controlled login-user execution | Invoking desktop user | Yes, with a user-context adapter |
| Workspaces/Git LFS | Managed target-user workflow | Existing user workspace; retain existing repository policy | Contract and safety helpers |
| T3 service | Managed VM/server service and pairing/firewall integration | User systemd service and local/LAN policy | Runtime/probe primitives only |
| Security/networking | Server security policy | User-owned desktop policy | No |

## Delivery sequence

### Phase 0 — inventory and acceptance contract

Before moving code, create a matrix for every proposed capability. It must name
the current Debian owner, CachyOS owner, package names, executable probes,
effective user, side effects, dry-run behavior, failure message, and recovery
path. Mark entries `shared`, `same-intent-different-lifecycle`, or
`profile-specific`.

Start with `git`, `git_lfs`, `node_runtime`, `python_tooling`, `go_runtime`,
`av_tools`, `gl_tools`, managed agent skills, agent CLI tools, workspaces, and
T3 runtime health. The expected outcome is that several entries stay
profile-specific; that is a successful finding, not a reason to force reuse.

**Acceptance:** The matrix is reviewed against the current steps and docs. No
behavior changes, package-manager commands, or option acceptance changes.

### Phase 1 — pure catalog and plan/readiness contract

Add a distro-neutral module such as `lib/capabilities.py` containing typed,
validated catalog entries and structured plan/readiness results. It must not
import `remote_setup`, invoke a command, inspect the local host, or mutate
state at import time.

Implement catalog validation for unique identifiers, supported profiles,
mapping completeness, dependency cycles, valid command probes, and a declared
owner/lifecycle for every mapping. Add a renderer used by dry-run output and a
readiness-result schema that distinguishes `available`, `missing`,
`unsupported`, `deferred`, and `failed` without treating an optional missing
capability as an unrelated setup failure.

**Acceptance:** Unit tests cover catalog validation, profile filtering,
dependency ordering, stable rendered plans, and redaction/size limits. Existing
Debian and CachyOS dry-run output remains compatible unless an intentional,
documented wording improvement is approved.

### Phase 2 — native-package adapter slice

Extract only the shared query/install/verify shape into small APT and pacman
adapters. Keep `ensure_debian_package_sources`, Debian refresh/upgrade,
DEBIAN_FRONTEND handling, pacman's no-sync guarantee, and error remediation in
their respective adapters.

Migrate one low-risk capability with clear command probes—prefer the CLI
baseline or `git_lfs`—rather than a broad bundle. The migration must preserve
the exact policy that a CachyOS rerun does not refresh repositories and a
Debian dry run does not call APT.

**Acceptance:** Fake-adapter tests assert every generated argv, installed-versus
missing result, failure response, and no-op rerun. Existing CachyOS tests still
prove no `pacman -Sy`/upgrade is requested. Debian tests retain APT source and
lock behavior.

### Phase 3 — user-context and local-tool lifecycle slice

Extract safe, distribution-neutral primitives for home resolution, managed
home-relative paths, PATH composition, noninteractive command environments,
executable provenance, and bounded readiness probes. Each adapter remains
responsible for how a command becomes the target user: Debian's controlled
`runuser` path must not be replaced with CachyOS direct execution.

Then migrate managed skills, agent-tool provenance/update decisions, and Git
workspace reconciliation one capability at a time. Preserve CachyOS's rule to
retain externally managed agent binaries and the Debian credential/payload
boundaries.

**Acceptance:** Tests cover symlinks, incorrect ownership, unsafe home paths,
prompt suppression, externally managed tools, idempotent reruns, and no
credential copying into CachyOS.

### Phase 4 — runtime-specific sharing decision

Evaluate T3 after the prior contracts are exercised. Extract only independently
testable primitives such as version parsing, native-addon health checks,
release-candidate verification, and bounded HTTP readiness. Do not share unit
placement, service activation, pairing, firewall changes, or recovery records
unless both profiles can meet one explicit ownership and rollback contract.

**Acceptance:** A written decision records what stays separate and why. Any
shared helper has tests for both system and user-service callers; no test pass
is treated as CachyOS graphical-session qualification.

### Phase 5 — CLI/docs consolidation

Only after capability migrations are stable, consider a neutral internal
representation for operator-requested package capabilities. Preserve
`--apt-install` semantics for Debian. Do not add a generic custom-package flag
or CachyOS package request until its policy, documentation, and failure
semantics are approved.

Render the supported-profile matrix into command documentation or validate it
in CI so flags, parser validation, package mappings, readiness probes, and
operator docs cannot silently diverge.

## Implementation rules

- Do not alter function signatures or move a step until all callers and tests
  are migrated in the same change.
- Keep configuration selection separate from execution; `SetupConfig` remains
  the profile-validation boundary.
- Use argv-native commands and existing validators for all provider inputs.
- A dry-run must report intent without package-manager or desktop/session
  probes that can alter state.
- Treat mapping changes as operator-contract changes: document affected
  packages, command names, privileges, and recovery instructions.
- Commit each independently reversible phase separately. Do not combine a
  metadata introduction with broad behavior migration.

## Verification and rollout

Run focused unit tests for each touched domain, the existing CachyOS profile
suite, and the normal repository test suite before widening a phase. Add a
matrix test that creates fake Debian and CachyOS adapters; it must verify
package selection, plan output, probes, side-effect classification, and failure
messages without inspecting the developer host.

For every applied CachyOS slice, perform live qualification on a disposable
bare-metal CachyOS KDE system and record it in the existing
[qualification checklist](CACHYOS_AGENTIC_DESKTOP_QUALIFICATION.md). Debian CI
or mocked tests cannot establish that a desktop-user/systemd service behaves
correctly on CachyOS.

Roll out behind internal catalog use first, then one migrated low-risk
capability, then additional capabilities only after a rerun and failure-path
review. Leave an adapter-local implementation in place when unification would
obscure a security, authority, or recovery difference.

## Completion criteria

This project is complete when the catalog and profile matrix are authoritative,
at least the selected genuinely portable capabilities use them, unsupported and
same-intent/different-lifecycle capabilities are explicitly documented,
adapter and boundary tests protect both execution models, operator docs are
validated against the catalog, and live CachyOS evidence exists for every
changed desktop-facing behavior. Completion does not broaden the supported OS
matrix or imply parity for Debian server features.
