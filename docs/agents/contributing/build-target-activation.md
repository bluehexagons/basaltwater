# Admitted target release activation

`lib.build_target_activation.TargetReleaseActivator` is an internal callback
for `DeploymentAdmissionStore.activate()`. It is not an exposed server or a
replacement for authentication, current target policy, generation and expiry
checks. Its caller must already hold admission ownership. The artifact accessor
must pin the same application, operation, generation, request, bindings and artifact digest
without reentering the admission store.

The callback reconstructs a manifest from the authorized static/service
projection and the exact ordered build inputs. It executes no build commands.
Service binaries use the first-slice convention of reading `PORT` and `HOST`
from fixed generated runtime values; `HOST` is `127.0.0.1`. A program must honor
those values and its declared health path. Custom commands, arguments, runtime
environment and secret bindings remain outside this adapter. Existing ordinary
local deployment is unaffected.

The administrator's deployment root is used exactly. Generated service users,
ports, persistent directories, prebuilt runtime verification and health rollback
remain owned by `DeploymentOrchestrator.deploy_prebuilt()`. Root changes for an
existing active application require operator reconciliation. Overlapping roots,
generated identity collisions and route conflicts with other retained active
applications are rejected before deployment.

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

## Artifact-mode setup replay

`common.build_target_refresh.reconcile_artifact_deployments(config)` is the
trusted root-local setup wrapper. `plugins.post_setup` selects it whenever
`config.artifact_deployments` is true, including when `deploy_specs` is absent.
It requires Debian 13 x86-64 VM/physical system services. It acquires
`target_maintenance()` exclusive ownership before the first protected
`common.service_tools.build_target.load_config()` read, retaining it through
the complete `reconcile_installed()` call and local success response. It
constructs `BuildTargetArtifactStore` from the installed quotas and roots and
passes `open_verified_bundle` to `TargetReleaseActivator`. No admission store
is constructed and no build/request identity or deployment generation is
created. The result is the reconciler's list of identity-only receipts.

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
this mode; the activator owns scoped retained-route publication. Cloudflare and
SSL setup options are refused during plan construction because they have no
supported target policy in this slice. This does not add runtime environment,
UDP or Node packaging support.

A dry run reports retained artifact reconciliation without entering maintenance,
loading target state, opening artifacts, contacting a builder or changing target
files. It does not establish that installed inputs are available. Ordinary
setup without artifact mode continues to stage source and build on the target.
