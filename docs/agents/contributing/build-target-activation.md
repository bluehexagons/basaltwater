# Admitted target release activation

`lib.build_target_activation.TargetReleaseActivator` is an internal callback
for `DeploymentAdmissionStore.activate()`. It is not an exposed server or a
replacement for authentication, current target policy, generation and expiry
checks. Its caller must already hold admission ownership. The artifact accessor
must pin the same operation, generation, request, bindings and artifact digest
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

A private, bounded registry retains the current route descriptors for up to 256
applications within 4 MiB. The registry and initialization witness are private
effective-UID-owned files under an existing `0700` directory with trusted,
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
before an operator resolves recovery. Artifact retention and target-only refresh
integration remain separate work.
