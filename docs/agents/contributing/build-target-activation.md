# Admitted target release activation

`lib.build_target_activation.TargetReleaseActivator` is an internal callback
for `DeploymentAdmissionStore.activate()`. It is not an exposed server or a
replacement for authentication, current target policy, generation and expiry
checks. Its caller must already hold admission ownership. The artifact accessor
must pin the same application, operation, generation, request, bindings and artifact digest
without reentering the admission store.

The callback reconstructs a manifest from the authorized static/service
projection and the exact ordered build inputs. It executes no build commands.
Ready static exports omit the manifest's optional `build` field when their
request has no commands; activation and retained-release replay preserve that
empty command list.
Legacy service projections without `runtime` read implicit `PORT` and `HOST`
values; `HOST` is `127.0.0.1`. An explicitly approved `runtime` mapping instead
reconstructs the ordinary manifest's exact exec arguments, runtime environment,
port, working directory, SQLite backup settings and UDP declarations. No implicit
environment is added to that mapping. Secret bindings remain unsupported.
Service installation and backups use the existing orchestrator implementation.

The administrator's deployment root is used exactly. Generated service users,
ports, persistent directories, prebuilt runtime verification and health rollback
remain owned by `DeploymentOrchestrator.deploy_prebuilt()`. Root changes for an
existing active application require operator reconciliation. Overlapping roots,
generated identity collisions and route conflicts with other retained active
applications are rejected before deployment.
Artifact activation and reconciliation publish root-owned releases with read
and traversal access for Nginx and generated service accounts. They do not
require the legacy `web-deploy` account; service-writable state remains in the
generated persistent directories outside the release.

A private, bounded registry retains the current route descriptors and installed
request/bindings for up to 256 applications within 4 MiB. These private inputs
include build commands and non-secret build environment; they are never returned
through transport receipts. Admission refuses oversized retained inputs before
release changes, reserving 1 MiB for rendered routes. The registry and
initialization witness are private effective-UID-owned files under an existing `0700` directory with trusted,
non-renamable ancestors. Missing initialized state and malformed records fail
closed. The verification directory must be separately provisioned and must not
overlap that registry or an application root.

Nginx receives the combined retained route group, including separate applications
sharing a domain. Explicit domain scope includes retired domains and preserves
unrelated deployment sites. A durable operation marker precedes release changes;
the registry is saved only after scoped Nginx publication succeeds. Returned
receipts contain operation/application IDs, generation and artifact identity,
without paths or build environment values.

Release activation, Nginx reconciliation and registry persistence are separate
durable boundaries. Failure or interruption retains the operation guard and
requires inspection; this adapter does not automatically replay or promise an
atomic rollback across all three. The admission store retains its independent
uncertain-activation guard. Preserve both records and actual service/route state
before an operator resolves recovery.

`reconcile_installed()` is an internal trusted-local setup operation. Its caller
must exclude typed requests and enrollment changes throughout the call. It
validates every installed descriptor and retained bundle before reapplying
releases through `deploy_prebuilt()`, then republishes the complete scoped route
group. It preserves operation IDs, generations, source and artifact identities;
it does not contact a builder, fetch source, run build commands or reserve new
deployment intent. Revoked SSH authority does not supply new inputs to an
installed release. Missing evidence requires controller deployment; failures
retain the same recovery guard and prevent automatic replay. Reconciliation
across several applications is not atomic. Artifact retention remains separate
work; replay requires the installed bundles to remain available.

For controller final-profile replay, `reconcile_installed(*,
expected_releases=None, admissions=None)` optionally requires exact installed
identities. `parse_expected_releases()` accepts `None` for ordinary Admin
refresh, or a list of 1–16 objects containing exactly `application`,
`operation_id`, `generation`, and `artifact_sha256`. Applications are canonical
lowercase slugs (1–64 characters); operation IDs are 32 lowercase hexadecimal
characters, artifact digests are 64, and generations are integers from 1 through
`2**63 - 1` (booleans are rejected). Duplicate applications or operation IDs,
extra/missing fields, and noncanonical types fail closed. The parsed list is
detached from caller input and remains transient: neither `SetupConfig` nor
the release registry persists these expectations.

Every specified installed identity must match before artifact preflight,
activation ownership/markers, or release changes. Registry equality alone is
insufficient: a newer reservation leaves the older installed release in place.
Expected replay therefore also requires the installed `DeploymentAdmissionStore`
and checks `status()` for each stored operation under the caller's exclusive
maintenance lease. Application, operation, generation, artifact, request and
bindings must match a completed, unsuperseded admission. The `superseded` flag is
checked independently of `effective_status`, which remains `completed` for old
completed operations. Expiry of a completed receipt does not expire the
installed release; a newer reservation refuses replay even if that newer intent
is later cancelled or expires. Missing or uncertain admission evidence refuses
with controller guidance. Status checks never initialize missing ledger/lock
evidence and precede activation ownership, preserving admission-before-activation
lock order. No reserve, activate or build operation is performed. Retained apps
outside the expectation list still participate in ordinary reconciliation and
keep their identities and routes.

`check_expected_releases(*, expected_releases, admissions)` exposes just the
read-only identity/current-admission and recovery-marker checks for trusted
local setup composition. It requires nonempty expectations and exclusive
maintenance across the check and subsequent mutations. It does not acquire
activation ownership, initialize registry/admission state, open retained bundles,
preflight deployment parents, or publish routes. This method and reconciliation
are internal APIs; callers must not bypass the maintenance contract.

## Artifact-mode setup replay

`common.build_target_refresh.reconcile_artifact_deployments(config, *,
expected_releases=None)` is the
trusted root-local setup wrapper. `plugins.post_setup` selects it whenever
`config.artifact_deployments` is true, including when `deploy_specs` is absent.
It requires Debian 13 x86-64 VM/physical system services. It acquires
`target_maintenance()` exclusive ownership before the first protected
`common.service_tools.build_target.load_config()` read, retaining it through
the complete `reconcile_installed()` call and local success response. It
constructs `BuildTargetArtifactStore` from the installed quotas and roots and
passes `open_verified_bundle` to `TargetReleaseActivator`. With no expectations,
no admission store is constructed and existing Admin refresh behavior is
unchanged. Explicit expectations are validated under the lease and forwarded
with the installed `admission/admissions.json` store. No build/request identity
or deployment generation is created. The result is the reconciler's list of
identity-only receipts.

The parent composing the final target profile must enter
`expected_artifact_setup(config, expected_releases)` **before** beginning the
setup operation or executing any profile step:

```python
with expected_artifact_setup(config, expected_releases):
    # Begin the setup operation and run the final target profile here.
    # The normal post-setup callback uses the same config object:
    receipts = reconcile_artifact_deployments(config)
```

This context acquires maintenance before loading installed configuration,
validates the bounded expected identities and current admissions without writes
or bundle verification, then retains the lease across the caller's steps and
local response. A private context-local token lets reconciliation reuse that
exact active composition and detached expectation list without reacquiring the
nonreentrant maintenance lock or reloading configuration. Reconciliation still
rechecks identities/admissions and verifies all retained artifacts before its
first release change. Explicit wrapper expectations inside the scope must match
the scoped list; another config object, nested scope, or expired copied context
is refused. The token is invalidated and reset on normal exit and exceptions;
exceptions from profile steps propagate unchanged.

Enrollment must be omitted from the guarded final profile: an installer needs
its own exclusive lease and cannot run inside this scope. The parent owns this
composition and transient argument transport; this wrapper does not change
controller, parser, configuration, or remote setup wiring. The lease serializes
the supported typed request/enrollment paths; direct internal store callers must
also honor it. It cannot protect against an uncoordinated privileged writer.

Missing, unsafe, held/disabled or busy enrollment refuses before configuration
is read. Missing configuration, installed descriptors or retained artifacts
fails closed with controller guidance. Recovery guards remain authoritative;
inspect protected evidence before operator reconciliation. There is no source
or build fallback. A first artifact-mode setup without installed releases
refuses; initial external bootstrap and artifact delivery must precede the final
profile and belong to the controller, outside this wrapper.

Artifact-mode transport skips repository preparation and source upload, and
target runtime inference returns before inspecting uploaded repositories. Explicit
runtime-install flags retain their ordinary meanings. Post-setup never calls
ordinary `deploy_repository`, unscoped Nginx pruning or manifest UDP setup in
this mode; the activator owns scoped retained-route publication. Administrative
bootstrap enrolls Cloudflare/SSL settings in protected target routing policy;
artifact requests cannot change them. Refresh requires its profile to match
that policy. Cloudflare origins use HTTP without HTTPS redirects or ACME.
Direct SSL activation reuses usable installed certificates; new certificate
issuance and certbot installation occur only during administrative replay,
after the profile's web firewall setup. A missing certificate therefore leaves
HTTP routes until that final profile succeeds. Approved runtime environment and
UDP metadata follow the target policy; interpreter service packaging remains
unsupported.

A dry run reports retained artifact reconciliation without entering maintenance,
loading target state, opening artifacts, contacting a builder or changing target
files. The guarded context similarly yields without consulting target evidence
in a dry run. Neither establishes that installed inputs are available. Ordinary
setup without artifact mode continues to stage source and build on the target.
