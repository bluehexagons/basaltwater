# Shared build hosts and artifact deployment

Status: bounded Debian on-demand slice implemented; remaining design revised
2026-10-26. Preserve standard build-on-target
setup and deployment. The unused webhook, build-server and runner interfaces,
profiles and state may be replaced without migration or compatibility shims;
retain their useful security and recovery contracts. The
[controller setup guide](../agents/contributing/build-setup.md) describes the
available static/Go slice, and the [coverage map](testing-coverage.md) records
selected native checks. Webhook reuse, shared runners, panel controls and native
Windows adapters remain planned.

## Current boundary and intended composition

Direct manifest deployment builds on the destination and supports multiple
components, service identities, persistent data, backups and health rollback.
Webhook CI has durable admission, authenticated commit selection, separate
build/deploy credentials and temporary artifact snapshots, but remote manifest
publication supports only one static/Godot component. GitHub runners currently
use a separate profile and capacity lock; see the [runner plan](github-runner-support.md).

A build host should offer independently selected capabilities:

- **On-demand builds:** authenticated controller submissions over SSH, without
  an HTTP listener or GitHub registration.
- **Webhook builds:** signed repository events admitted under configured
  repository/branch policy, optionally followed by authorized deployment.
- **GitHub Runner:** the official runner executes GitHub workflows and can use
  Basaltwater artifact commands. GitHub remains the workflow scheduler.

Compose these capabilities with selected toolchains and an optional panel on
one VM. Installed tools determine possible workloads; saved intent determines
enabled services. Report configured, installed, ready, busy, draining and
unavailable separately. Begin with Debian; [Windows](windows-support.md) shares
the job/artifact contracts through native execution adapters. WSL is optional.

## Controller, builder and target contracts

Use one validated project manifest and one build implementation for manual and
webhook jobs. Consolidate duplicated CI/component build declarations; GitHub
workflows can call the same project-owned commands without being translated
into a new workflow language. Keep scripts explicit and unprivileged.

`setup ... --build-on BUILDER` selects a saved builder reference.
The controller pins source, validates project inputs and builder capabilities,
reserves deployment intent on the target, submits the build, retrieves the
verified artifact, then stages it on the target. The controller needs access
to both machines; the builder needs no target key in this mode. Never forward
the controller's SSH agent or credentials.
Read-only source access may use a scoped builder credential or controller upload.

Before the build, the target's typed admission helper validates application
authorization and records the controller's idempotency key, source/input digest
and requested target bindings. It returns an operation ID and per-application
generation. A new target first receives the minimal protected helper and policy
through authenticated setup bootstrap, without build tools or application
activation. Preflight precedes this bootstrap; a build failure leaves existing
applications intact, but does not roll back new-host bootstrap work. Build-only
requests without deployment need no target reservation.

Initially the controller uploads a clean snapshot of one resolved Git commit;
dirty working-tree builds are outside the first slice. Hash the actual source
snapshot and manifest as well as recording the commit. Disable checkout hooks,
exclude local credentials/configuration, and supply only sanitized Git metadata
needed by declared version commands. LFS objects and pinned submodules must be
materialized and validated, or rejected as unsupported before build admission;
never build pointer files or silently omit dependencies. Builder-side fetches
can follow later under explicit source-access policy.

Save the builder reference for later deploy/patch operations; resolve credentials
locally rather than embedding them in target setup arguments. Distinguish this
selection from enabling a build-host role. No selected builder keeps the
existing build-on-target behavior. An unavailable selected builder fails without
silently installing tools or building on the destination. Dry runs report source,
builder, target and missing requirements without executing repository commands,
installing tools, registering runners or starting builds.

The reference belongs to the controller workspace and applies to all deployment
repositories in that setup initially. Targets retain external-build mode and
the installed artifact/descriptor, not controller credentials or a requirement
to contact the builder. Target-only Admin refresh reconciles that installed
release; it does not fetch a new revision or rebuild it. Missing retained inputs
fail with controller guidance. Controller-driven setup/patch/deploy prepares new
artifacts after admission and before target-side application setup. Artifact
mode uses runtime declarations rather than source-based build-tool detection.

Check source identity, host keys, protocol compatibility, OS/architecture and
required tool versions before target mutation. Build and verify every requested
artifact before replacing any active application. Build requirements and runtime
requirements are separate: Node-built sites need no target Node, while Node
services need a declared runtime. Native libraries/ABI requirements must match.
Do not infer required service files from an arbitrary `exec` string.

Target authorization stays administrator-controlled: permitted applications,
destinations, routes, services and secret bindings constrain repository requests.
Automatic webhook publication may use a separately scoped builder-to-target
connection; building alone grants no deployment authority. Start with SSH and
typed requests, not a mandatory public build API, registry or external manager.

## Artifacts, activation and recovery

Package only declared outputs, binaries, assets and runtime dependencies into a
versioned release bundle with a validated deployment descriptor. Record source
revision, build attempt, platform, toolchain versions, relevant build inputs,
component inventory and file checksums. Target-specific build inputs produce a
different artifact; runtime ports, paths and secret references resolve at deploy.
Create inventories and retained metadata from a broker-owned snapshot after
build work ends, rather than trusting a job-writable archive or live directory.
Apply bounded extraction, path/type/permission checks and authenticated transfer
on both ends. A digest identifies bytes; it does not authorize their source.

Add an optional per-component `artifacts` list of repository-relative files or
directories, preserving their relative layout. Static/Godot outputs and a lone
service `binary` provide defaults; `exec` services require an explicit list for
remote builds. Include assets and vendored runtime dependencies deliberately;
do not copy the whole checkout or infer them from `exec`. Initially reject
symlinks and special files, omit Git/control metadata, and impose configurable
file/count/expanded-byte limits. Project packaging can produce a portable tree
where dependency layouts otherwise use links or absolute build paths. Existing
local version-1 manifests keep their source-tree behavior without new fields.
Declare target runtimes separately from `build_runtimes`; requested packages
must come from supported runtime policy, not arbitrary privileged install hooks.

Retain successful artifacts independently of temporary workspaces and caches.
Bound retention by age/count/bytes, pin active/retained rollback releases and
pending transfers, and refuse admission when protected evidence fills storage.
Explicit root-local [terminal settlement](../agents/contributing/build-retention.md)
now lowers unused terminal-job reservation charges against verified retained
bytes. Explicit root-local source/log pruning uses durable deletion intent,
revision checks and per-job payload locks spanning service reads and streams
while preserving bundles, terminal results and replay identities. Interrupted
pruning retains its old charge until explicit resumption
completes durably; uncertain work stays fully charged. Automatic retention,
bundle deletion and replay-history expiry remain separate policy work; admission
must never delete evidence.
Deleting a cache must affect performance only. Checksum/metadata changes,
including build commands and non-secret environment inputs, affect reuse keys;
a commit alone is insufficient. Track secret version references without values.

Extract target activation from `lib/deployment.py` so prebuilt and local builds
share service users, ports, persistent paths, SQLite backup, health checks and
recovery markers. Stage complete releases before activation; remove CI's direct
rsync into the served directory. Coordinate release, unit and Nginx recovery
without promising a zero-downtime atomic switch across them. Do not rebuild on
the target when deploying a prebuilt release. Run repository commands without
root privileges in either build mode. Database migration rollback and
application-data restoration remain separate, explicit operations.

Keep delivery receipt, build attempt, artifact and deployment operation IDs
distinct. Build success survives publication failure. Lost responses require a
status query by the same request ID, not blind resubmission. Record interrupted
or uncertain outcomes and preserve evidence; cancellation cannot undo completed
deployment or external side effects. Serialize activation per application and
target. Delayed older builds cannot replace a newer desired deployment; rollback
is an explicit selection of a retained compatible release.

Allocate a target-owned per-application generation when deployment is admitted,
before the build, and compare it again under the activation lock. Wall clocks,
commit ancestry and build-completion order do not establish deployment order.
Activation carries the admitted operation/generation, source/input identity and
artifact digest. Validate them against the protected reservation, then bind the
digest durably before mutation; another digest for the same operation is an
error. Superseded, cancelled or expired reservations cannot activate. Repeating
admission or activation queries/reuses that operation, never allocates a new
generation or rebuilds implicitly. Failed builds require an explicit new request
to change desired intent; they do not revive an older reservation.

Persist admission before acknowledging or starting a build. Bind each request
ID to its authenticated owner, project and input digest; the same ID with
different inputs is an error. Store queued/running/terminal state outside the
SSH connection, with status, bounded logs, cancel and artifact retrieval by ID.
Retain replay protection independently of ordinary log/artifact pruning; expired
retry identities cannot create fresh work. A worker crash leaves uncertain work
stopped for reconciliation, rather than automatically replaying build scripts.
Before activation, cancellation preserves the live release; after mutation
starts, finish or reconcile recovery before reporting cancellation complete.

## Capacity, credentials and maintenance

Use one host resource coordinator across all entry points, initially one heavy
job slot with configurable concurrency, bounded waiting and explicit CPU/memory/
disk/time limits. GitHub retains its remote queue; its local adapter coordinates
capacity before workflow execution and reports waiting honestly. Do not invent
global ordering across GitHub and local requests. Prevent double acquisition
when a runner job calls Basaltwater build commands; reuse requires a validated
coordinator-issued lease, not a workflow-controlled environment bypass.
Deployment locks remain separate from build slots. Stop holding a slot only
after supervised work ends; test child cleanup, crashes and uncertain process
state on each platform.

Use separate service identities, workspaces and scoped writable caches; share
read-only toolchains where practical. Job code cannot read deployment keys,
panel credentials or unrelated runtime secrets. Treat colocated persistent
jobs as mutually trusted; untrusted PRs need a separately qualified disposable
boundary. No Docker requirement or privileged container socket is implied.

Drain all enabled entry points before setup, toolchain changes or reboot.
Draining stops admission and new local starts, exposes already assigned
GitHub jobs, and leaves active jobs running unless explicitly cancelled. A
panel restart or disconnected controller must not kill accepted jobs. Stage
compatible protocol upgrades and reject mismatches before activation.
Hold accepted local queued work during drain; fail assigned-but-waiting GitHub
workers before workflow steps so they cannot start against changed tools or
policy. Bound local queue length and wait time. After capacity acquisition,
revalidate the current authorization policy immediately before execution.

The initial typed target slice rejects `env_file` and runtime environment inputs.
Later target-owned binding IDs can resolve administrator-approved environment
files without allowing project-selected paths. Integrate
[secret references](deploy-secrets.md) incrementally; full secret-store and
optional-component work need not block artifact-only builds. Never transport
runtime secret values through the builder or artifact. Build-time secrets are
explicit grants to trusted build code, not values that log filtering can secure.

## Optional web panel

Reuse the existing authenticated panel and backend operations. Show pages only
for selected installed capabilities, retaining unavailable/readiness explanations
when an enabled capability fails. The panel is not required for job execution
and does not become a fleet controller merely because a build host exists.

| Host/page | Initial inspection | Bounded actions after the shared backend exists |
| --- | --- | --- |
| Builder: Builds | Entry point, project, revision, platform, queue/phase, timestamps, resource limits and bounded logs; GitHub job links | Build an authorized project/revision, retry as a new build attempt, cancel local queued/running work, drain/resume admission |
| Builder: Artifacts | Release identity, checksums, components/platform, size and retention references | Download authorized outputs, pin/unpin within policy, deploy a retained artifact only to a configured target |
| Builder: GitHub runner | Registration/scope, labels, policy, local state, waiting/active jobs and authoritative GitHub links | One-time registration, validated allowed-user edits and drain-aware lifecycle controls; GitHub owns workflow dispatch/rerun/cancellation |
| App host: Deployments | Active/previous release, source/build identity, health, last operation, persistent paths and backup status with observation time | Retry/reconcile a known operation, activate an authorized available artifact, or request compatible rollback with clear data/migration limits |
| Builder: Configuration | Selected/detected tool versions, target platforms, capacity, disk use, admission and credential readiness | Validated capacity/retention settings and notification tests; tool installation and new target/credential enrollment remain setup/CLI operations initially |

On an app host without builder access, artifact actions remain unavailable with
controller guidance; do not copy builder credentials there just to enable a
button. The panel reads local bounded records, not arbitrary filesystem paths
or live remote inventories on every page load. Distinguish a failed probe from
idle/healthy and show partial/unavailable snapshots. GitHub logs/artifacts remain
upstream unless explicitly exported into the common artifact store.

All writes use authenticated POST, CSRF protection, typed validated requests,
idempotent request IDs and the same authorization as CLI operations. Scope reads,
logs and downloads to authorized projects; escape build output as text and bound
collection. Never expose tokens, raw environments, arbitrary commands, paths or
editable deployment authority. Sensitive registration payloads use a private
one-time channel, not durable job history. Privileged actions use narrow helpers
or existing approvals without giving the panel/build identity general admin
rights. Record actor, action, IDs and result without secret values.

## First implementation boundary

The implemented first slice uses manifest-driven Debian Trixie x86-64 builds
relayed by the controller, one supervised build slot and no automatic cache
reuse. Selected native checks cover a static site and self-contained Go service
on a separate target without build toolchains. Shared artifact and activation
interfaces serve both build modes; leave Node/
Python services, additional platforms and package-only build targets for their
own runtime/packaging cases. Standard local deployment, including inferred Go
and ready-to-serve static projects, must retain its existing behavior.

Contract and integration tests cover versioned request/artifact schemas, payload
identity, explicit exports, platform/runtime compatibility, durable admission,
selected recovery boundaries and controller-to-target replay. Selected native
saved patch/redeploy checks preserve the chosen revision, builder and sibling
deployment; target-only reconciliation preserves installed identities without
target compilers. Native checks also cover one ready SPA and Go API release
alongside a retained same-domain neighbor, including revision replacement,
retained reconciliation and idle reboot. Controlled truncated upload refuses
activation; an unhealthy API candidate restores both previous exports and its
unit while the neighbor remains available. Recovery guards retain exact IDs and
block implicit remote mutations. An offline SIGKILL synchronized through pipes
after durable target activation, before controller acknowledgement, preserves
exact identities and guards; reentry refuses new work and identical target
replay returns the cached result. Transport and activation effects are mocked
in that crash check. Continue expanding crash/restart qualification.

## Delivery order and acceptance

1. **Common release contract and on-demand Debian builds:** the bounded static/Go
   slice supplies build/activation separation, saved builder selection, typed SSH
   operations, retained bundles, terminal quota settlement and explicit source/log
   pruning. Qualify additional interruption and capacity boundaries; native checks
   cover repeat activation with clean protocol output, settlement preserving
   payloads, and interrupted pruning with same-revision resumption and same-ID
   replay. Preserve default build-on-target
   setup/redeploy behavior and saved settings.
2. **Multi-component deployment and webhook adapter:** native SPA and Go API
   checks cover shared-domain routing, saved revision replacement, retained
   reconciliation and idle reboot with a neighboring application and no target
   compilers. Controlled native transfer and health failures preserve the live
   application or restore both exports while retaining recovery evidence.
   Browser rendering remains unqualified because the preview could not resolve
   the lab hostname; npm-built frontends and stateful bindings are outside this
   slice. Extend
   capacity, lost-reply and restart checks before webhook reuse and stateful
   runtime bindings.
3. **Composable runner and shared capacity:** replace dedicated-profile
   restrictions; qualify simultaneous installed roles, authorization, waiting,
   cancellation, drain/reboot and nested submissions. Keep GitHub workflow
   semantics and qualification limits explicit.
4. **Panel operations:** deliver inspection first, then backend-backed controls;
   test authentication, CSRF, project scope, duplicate requests, stale status,
   unavailable controls and Chromium/Firefox layouts without a live provider.
5. **Native Windows adapter:** follow the Windows plan; share the contracts and
   optional panel model while qualifying native tools and process supervision.

Do not add distributed scheduling, changed-component builds, mandatory registries,
automatic game-store publishing or a general fleet dashboard to this first pass.
These documents are the design; operator guides change when behavior ships.
