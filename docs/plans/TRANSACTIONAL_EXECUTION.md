# Transactional Execution and Reconciliation

Status: partially implemented. The misleading in-memory transaction framework
has been retired and durable operation state is landed; required-command
classification, corrupt-state handling, and lightweight recovery guidance
remain the active scope.

This plan turns setup and deployment from a sequence of mostly independent
commands into an operation with explicit preparation, activation, verification,
and recovery phases. It addresses ARCH-01, ARCH-03, ARCH-05, ARCH-06, and
ARCH-08 from the [architectural risk review](ARCHITECTURAL_RISK_REVIEW_2026-08-07.md).

## Goals

- Stop immediately when a required command fails.
- Preserve the last working service or release until replacement activation.
- Make partial state visible and recoverable after interruption.
- Use the same validation result for planning and post-apply verification.
- Keep best-effort maintenance operations possible, but make that choice
  explicit at each call site.

## Verified implementation baseline (2026-08-19)

- `lib.remote_utils.run(check=True)` now raises `CommandExecutionError` with a
  bounded stderr diagnostic; callers that intentionally inspect failure use
  explicit `check=False`. Commands have a one-hour default timeout,
  caller-specific overrides, typed timeout diagnostics, and shell process-group
  termination.
- `remote_setup.py` no longer removes all managed services before setup steps.
- `DeploymentOrchestrator.deploy_manifest()` builds and validates a sibling
  release before stopping app-scoped services. Failed activation restores the
  prior release and generated unit files.
- Manifest health polling accepts only 2xx responses and rejects an unhealthy
  release. Stable ports, app-scoped build identities, SQLite pre-deploy backups,
  and deployment serialization are implemented.
- Nginx deployment files are snapshotted and restored after failed validation;
  managed files use atomic replacement and unmanaged same-name sites are not
  overwritten.
- A shared `lib.atomic_io.write_json_atomic()` now protects machine/setup state,
  caches/history, webhook and deploy-target configuration, deployment/release
  metadata, host/network inventories, Cloudflare state, remote argument files,
  and Gogs admin credentials. Its tests cover replacement interruption and
  restrictive permissions.
- Readers still fall back on corrupt JSON in several paths; schema versions and
  actionable remediation remain open work.
- Shared SSH/SCP/rsync and Proxmox node builders use
  `StrictHostKeyChecking=yes` with the workspace enrollment file. Build-server
  deployment targets now require explicit enrollment and no longer populate
  trust from an unauthenticated `ssh-keyscan`.
- `lib.operation_state` defines a schema-versioned, atomically persisted marker
  with explicit in-progress and recovery-required states. It rejects corrupt,
  unsupported, symlinked, and stale-ID updates rather than replacing them.
  A nonblocking kernel lock protects ownership through completion or store
  closure; another process cannot transition or remove a live owner's marker.
  Process exit releases the lock, but recovery must still supply the recorded
  operation ID. Stable `.lock` files must not be deleted to bypass ownership.
- Manifest deployment creates that marker before staging, records deterministic
  staging/backup paths and units before activation, clears it after success or
  verified rollback, and retains recovery errors when rollback is incomplete.
- Target setup records its current step before mutation, finalizes remembered
  machine/setup state only after the full operation succeeds, and retains
  interrupted or failed state for the next invocation.
- Nginx setup, firewall initialization, SSH reload, and CI/CD prerequisite
  setup now propagate required command and verification failures. Probes,
  stale-rule cleanup, and the container-capability firewall exception remain
  explicitly best-effort.

The former `lib/transaction.py` callback framework was removed on 2026-08-21.
It reran completed steps, could report success after continue-on-error failures,
required callers to trigger rollback manually, and kept all transaction and
checkpoint state in memory. Sync and scrub callers now use explicit fail-fast
control flow and retain `lib/operation_log.py` only for diagnostic events. Their
initial-operation failures now propagate to setup instead of being logged as
successful configuration. Durable recovery state is a small, versioned
operation record rather than a registry of non-serializable callbacks.

## Phase 0: Contract and primitive convergence

Before changing broad execution behavior:

1. Inventory and classify every `remote_utils.run()` caller as required,
   optional, probe, or cleanup, including its current return-code handling.
2. **Complete:** retire `lib/transaction.py`, preserve operation logging as
   diagnostic evidence, and require explicit orchestration code to own apply,
   verification, and recovery behavior.
3. **Complete:** define the durable operation-marker schema and crash-safe
   storage primitive. Integration owns marker location and recovery behavior at
   each setup or deployment boundary.
4. Add fault-injection tests at the orchestration boundary, not only unit tests
   of transaction primitives.

## Phase 1: Execution contracts

Split the ambiguous `lib.remote_utils.run()` behavior into explicit contracts.
The preferred shape is a strict default that raises a project-specific command
error containing the argv, return code, and bounded stderr. Callers that can
legitimately continue should request a result-returning best-effort mode and
inspect it.

Migration requirements:

- inventory every `run()` caller before changing the helper;
- classify failures as required, optional, probe, or cleanup;
- keep secrets out of command displays and exception messages;
- avoid shell execution when an argv form is sufficient; and
- add regression tests for failure propagation through complete setup steps.

Do not change the helper and assume all callers want strict behavior. Existing
verification-based installers and probes intentionally inspect failed results.

The first execution-contract slice landed on 2026-08-09: strict failures now
raise, best-effort failures remain inspectable, and result-inspecting database,
release-fetch, and host-metric callers explicitly request `check=False`.
Diagnostic redaction was completed on 2026-08-21 for quoted, escaped,
delimiter-bearing, and unterminated secret values in command and stderr text.
Bounded execution was completed the same day: commands default to one hour,
callers may select a positive override or explicitly opt out with `None`, and
timeouts always raise `CommandTimeoutError`. Shell commands are isolated in a
new session so TERM/KILL cleanup covers descendants. The argv-native command
API landed on 2026-08-24, and required callers in deployment, storage,
app-server, build-server, and CI/CD setup were migrated; the full caller
classification remains open.

## Phase 2: Atomic persistent state

The first implementation slice (2026-08-09) created one shared atomic JSON
writer using a same-directory temporary file, flush and `fsync`, restrictive
mode, and `os.replace`, then migrated workspace caches, machine/setup state,
operation history, webhook/deploy configuration, release metadata, host/network
inventories, Cloudflare state, remote argument files, and Gogs credentials to
it.

Readers should distinguish:

- missing state, where defaults may be valid;
- invalid or corrupt state, which should name the file and remediation; and
- unsupported schema versions, which should fail without overwriting data.

State schemas should gain an explicit version when the next incompatible
change is required. Replace permissive corrupt-state fallbacks with an error
that names the file and remediation, while retaining defaults only for missing
state where they are valid.

## Phase 3: Staged service reconciliation

Manifest deployments now record application units, build and validate before
stopping app-scoped services, require and verify successful stops before the
release rename, remove obsolete units only after activation, and restore the
previous release and units on failure. Rollback stop/restart failures are
reported as incomplete recovery. The remaining work is to apply the broader
contract to non-manifest setup services and persist interruption/recovery
markers.

Replace cleanup-first setup with a staged reconciliation model:

1. Record the currently managed services and configuration artifacts.
2. Prepare new files and units without removing the active versions.
3. Validate generated configuration and executable prerequisites.
4. Stop only the services that must change.
5. Activate replacements and run health checks.
6. Remove obsolete managed services only after successful activation.
7. Restore the recorded state when activation fails.

A failed run should leave an operation marker explaining whether rollback
succeeded and which manual action remains. A later run must detect and resolve
that marker instead of silently starting another cleanup.

## Phase 4: Release activation and health gates

The manifest deployment path now stages releases, validates declared outputs,
gates activation on service startup and health, and restores the prior release
and unit files on failure. Immutable release history/current symlinks and
database migration policy remain open.

Deploy into immutable release directories and switch a stable `current` link
only after builds and preflight validation pass. Retain at least the previously
active release. Health checks must determine deployment success for components
that declare them.

Database migrations require a separate policy because switching application
files cannot always reverse a schema change. Record the migration boundary,
create and verify backups before migration, and clearly report when application
rollback also requires database restoration.

Manifest activation now leaves a durable, versioned marker when a process or
machine is interrupted and blocks another deployment until the operator
reconciles it. Automated recovery from every recorded phase and longer-term
operation history remain open.

## Phase 5: Managed SSH trust

Separate host-key enrollment from privileged operations. Enrollment should
display fingerprints for operator verification and persist approved keys in the
workspace. Subsequent setup, deploy, and transfer commands should require an
existing matching key. Host-key rotation needs an explicit command and audit
entry.

Interactive convenience commands may offer a clearly labelled trust-on-first-
use mode, but automated privileged paths should not enable it by default.

The Proxmox guest helper and CI/CD deploy-target workflow now use the workspace
enrollment file. A successful `ssh-keyscan` is discovery data, not operator
approval; deployment must not be enabled until the expected fingerprint is
explicitly verified. Host-key rotation remains an explicit operator action.

## Acceptance criteria

- A required command failure produces a non-zero top-level result and stops
  dependent steps.
- Injected failures before and after service stop preserve or restore the last
  healthy service.
- Interrupted state writes never replace valid JSON with a partial file.
- A declared health-check failure prevents deployment success.
- Operation history records apply, verification, rollback, and rollback result.
- Existing best-effort probes and optional installers retain intentional
  behavior through explicit APIs and tests.
- Hosted VM, unprivileged LXC, and direct Debian setup paths have regression
  coverage for the new phases.

## Recommended first delivery slice

The atomic persistence, core execution contract, transaction-framework
retirement, setup/deployment operation markers, and high-impact required-caller
migrations are complete. The next delivery should continue the
`remote_utils.run()` caller inventory outside the migrated setup paths, add
lightweight phase-specific recovery guidance, and tighten corrupt-state
readers. Broader recovery automation should wait until real operational
experience identifies a repeated need.

## Non-goals

- Building a general distributed transaction system.
- Automatically reversing arbitrary repository-authored scripts.
- Pretending every database migration is reversible.
- Adding new supported operating systems during this work.
