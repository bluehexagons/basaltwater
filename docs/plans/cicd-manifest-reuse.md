# Shared build hosts and artifact deployment

Status: the bounded Debian on-demand build path is implemented. The next
deliverable is one Debian VM hosting on-demand builds, signed webhooks and
GitHub Actions runners together. Combined setup and shared capacity are not
implemented. Use the [build-system guide](../services/build-systems.md) for
available commands and the [coverage map](testing-coverage.md) for test limits.

`--build-on SAVED-BUILDER` selects that VM from another deployment's controller
configuration. It does not enable services on the builder. Ordinary deployments
without a selected builder continue to build on the destination.

## What is available

| Area | Current boundary |
| --- | --- |
| On-demand builds | Managed Debian 13 x86-64 enrollment, typed SSH, pinned public HTTPS source, declared exports and one supervised build slot. Ready static sites, Python static builds and self-contained Go services are supported. |
| Artifact delivery | Saved builder selection and revision replacement; target bootstrap/admission; verified staging and shared prebuilt activation; retained target refresh without source or builder access. |
| Recovery and retention | Durable IDs/guards, offline controller inspection, explicit terminal settlement, source/log pruning, successful-bundle release and capacity inspection. Slots and replay history remain retained. |
| Webhook CI/CD | Separate signed-event receiver/executor and queue; remote manifest publication is limited to one static/Godot component. |
| GitHub Actions | Separate dedicated Debian runner profile, local identity policy, runner-only capacity lock and deferred registration. Live GitHub qualification is open. |

Current source, admission, artifact and recovery contracts live in the
[contributor build references](../agents/contributing/README.md). Do not repeat
their implementation history here.

## Delivery order

### 1. Compose roles and convert an existing runner

Allow on-demand, webhook and runner capabilities to be selected independently
or together, with explicit toolchains. Replace dedicated-profile restrictions
and conflicting configuration paths; define and document the public setup
interface as part of implementation.

Start in `plugins/server.py`, `plugins/web.py`, setup configuration and their
owning role installers. Keep installed tools separate from enabled services;
report configured, ready, busy, draining and unavailable states accurately.

Conversion must preserve requested repository/organization scope, allowed
users, labels and working registration where valid. Require explicit
re-enrollment when identity or scope demands it; never copy or expose runner
credentials to other job identities. Do not add a general compatibility layer.

**Acceptance:** parsed fresh and saved configurations select every requested
role, omission/disable is explicit, and conversion plus rerun does not silently
drop a role or desired setting. Until this exists, do not advertise an all-role
setup command.

### 2. Share capacity, draining and maintenance

Replace independent build/webhook/runner capacity locks with one host
coordinator, initially one heavy-job slot. Keep bounded waits and CPU, memory,
disk, output and time limits. GitHub remains its own scheduler; local waiting
does not imply global FIFO ordering or zero resource use before job hooks.

Nested Basaltwater builds from a runner must reuse a validated coordinator
lease rather than deadlock or trust a workflow-controlled environment bypass.
Release capacity only after owned children stop. Keep deployment locks separate.

Drain every enabled entry point before tool/policy replacement or reboot.
Hold local queued work; fail assigned-but-waiting GitHub work before workflow
steps with a bounded reason. Let active jobs finish unless explicitly
cancelled. Recheck current authorization after waiting, immediately before
execution.

**Acceptance:** concurrent entry points respect the slot; nested builds,
cancellation, policy revocation during wait, drain and surviving-child failures
do not start unauthorized work or release capacity early.

### 3. Adapt webhooks to the common artifact path

Reuse validated manifests, build requests, retained bundles and target
activation instead of maintaining separate component/build declarations and
direct publication into served directories. Begin with the qualified static/Go
scope; do not silently broaden runtimes.

Preserve signed-event verification, repository/branch grants, delivery
deduplication and authenticated commit selection. Building grants no deployment
authority. Publication needs a separately scoped target connection and grant,
with target intent reserved before building and checked again at activation.
Keep build success distinct from publication outcome.

**Acceptance:** manual and webhook requests for the same declared inputs use
the same artifact contract; rejected or duplicate events create no replacement
work. Superseded, expired or unauthorized publication cannot replace a release.

### 4. Qualify the combined host

Use a disposable Debian VM and trusted repositories. Cover fresh setup and
conversion of an existing runner; allowed and denied real GitHub jobs; webhook
and on-demand submissions; shared waiting, nested builds, cancellation and
draining; repeated setup and reboot.

Exercise selected worker crashes, lost acknowledgements and storage exhaustion.
Preserve exact IDs, guards, receipts and unaffected target releases. Check
ordinary build-on-target behavior with the affected automated suite.

**Acceptance:** all three services remain configured and usable after rerun
and reboot, denied jobs execute no workflow steps, shared limits hold, and
uncertain outcomes stop for inspection. Record native versus mocked coverage
in the coverage map, not as a progress log in this plan.

## Contracts to preserve

- Controller credentials remain local; never forward an agent. The builder
  has no target key in controller-relayed mode. Runtime secrets stay on targets.
- Target administrators own application roots, routes and grants. Reserve all
  requested target intents before builds, stage all verified artifacts before
  activation, and recheck generations under target ownership.
- Request, build, artifact and deployment identities remain distinct.
  Identical retries use retained identities; lost replies never authorize
  replacement IDs or automatic execution of uncertain work.
- Bundle verification retains path/type/permission, checksum, expanded-size
  and entry-count bounds. Use broker-owned output snapshots, not job-writable
  metadata. Validate platform and runtime separately from build tool readiness.
- Persistent colocated jobs are mutually trusted. Use separate identities and
  writable state; no sudo, deployment credentials or privileged container socket
  is implied. Untrusted jobs need a separately qualified disposable boundary.
- Explicit retention actions never become admission-triggered deletion.
  Bundle release ends retrieval and frees bytes, not input/history slots.
  Capacity reports are observations, not physical reservations or readiness.
- Prebuilt activation shares deployment recovery without promising atomicity
  across releases, units and Nginx, or rollback of databases and application data.

## Follow-ups

Optional panel inspection comes before controls. Reuse typed backend operations,
project authorization, bounded escaped output, authenticated POST/CSRF and the
existing one-time registration channel. The panel is not needed for execution;
it must not gain arbitrary command, path, credential or deployment authority.

Runtime environment/`env_file` bindings, Node/package-manager builds,
interpreter services and stateful declarations remain outside selected-builder
deployment. Extend them through explicit [runtime secret bindings](deploy-secrets.md)
and independently qualified runtime cases.

Automatic retention, orphan-bundle release and replay-history retirement need
separate policy. [Windows adapters](windows-support.md), distributed scheduling,
changed-component builds, mandatory registries and automatic store publishing
are outside this Debian deliverable.
