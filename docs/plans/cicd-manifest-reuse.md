# Shared build hosts and artifact deployment

Status: proposed design, revised 2026-10-26. Preserve standard build-on-target
setup and deployment. The unused webhook, build-server and runner interfaces,
profiles and state may be replaced without migration or compatibility shims;
retain their useful security and recovery contracts. This plan does not claim
the proposed commands, panel controls or platform adapters are implemented.

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

A proposed `setup ... --build-on BUILDER` selects a saved builder reference.
The controller pins source, validates project inputs and builder capabilities,
submits a bounded request, retrieves the verified artifact, then stages it on
the target. The controller needs access to both machines; the builder needs no
target key in this mode. Never forward the controller's SSH agent or credentials.
Read-only source access may use a scoped builder credential or controller upload.

Save the builder reference for later deploy/patch operations; resolve credentials
locally rather than embedding them in target setup arguments. Distinguish this
selection from enabling a build-host role. No selected builder keeps the
existing build-on-target behavior. An unavailable selected builder fails without
silently installing tools or building on the destination. Dry runs report source,
builder, target and missing requirements without executing repository commands,
installing tools, registering runners or starting builds.

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

Retain successful artifacts independently of temporary workspaces and caches.
Bound retention by age/count/bytes, pin active/retained rollback releases and
pending transfers, and refuse admission when protected evidence fills storage.
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

Existing target `env_file` bindings can support the first release. Integrate
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

## Delivery order and acceptance

1. **Common release contract and on-demand Debian builds:** split build from
   activation, add builder selection, typed SSH operations and retained bundles.
   Preserve default build-on-target setup/redeploy behavior and saved settings.
2. **Multi-component deployment and webhook adapter:** use the same activation
   path; prove a static SPA, Go service and stateful frontend/API application on
   targets without build tools. Test transfer failure, disk exhaustion, health
   rollback, lost replies, stale publication and restart recovery.
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
