# Runtime binding qualification

**2026-11-17 UTC; runtime source `94477d18`, Debian 13 x86-64 on an owned
disposable QEMU guest.** The eight manifest, binding, deployment and target
runtime modules used by the probe matched that revision. This is native
component qualification with real systemd, application accounts, artifact
storage, admission state and Nginx. The fixture used synthetic values created
on the guest after its native executable was built.

| Check | Observed result |
| --- | --- |
| Ordinary deployment | Missing binding refused before release creation. The real application build account could read neither the registry nor the private environment file and received no secret environment variable. Its build copied the prepared native executable; target compiler installation was outside this check |
| systemd delivery | A service running as its separate unprivileged account received the expected value, verified through a hash-only loopback response. The account could not read the original root-owned mode-`0600` file. The generated unit contained its path without its values |
| Manual rotation | Publishing a new private file and changing the registry selected the new value after ordinary redeployment and retained artifact reconciliation |
| Failed rotation | A private file that made the candidate unhealthy caused both ordinary and prebuilt deployment to fail. Real rollback restored the previous release inode, exact unit bytes and previous environment. Old and new private files retained their contents, ownership, modes and inodes |
| Target admission and activation | The typed target handler rejected missing grants before admission. Revocation after real artifact staging rejected activation while retaining the original reserved identity and leaving the release absent. Restoring the grant allowed that original operation to complete |
| Retained refresh | Reconciliation used the retained verified artifact and current binding. Revocation refused before changing the release, running PID or release registry, without leaving an activation marker |
| Route publication | The selected service answered HTTP 200 through the real Nginx HTTPS route with certificate and hostname verification over loopback |
| Privacy | Fixture values were absent from release trees, artifacts, units, admission/target metadata and operational logs. Target protocol responses omitted values and resolved private paths. The target release registry and admission records contained no resolved environment-file paths |
| Guest reboot | A changed boot ID established an actual reboot. Both enabled services returned the rotated value afterward. Registry, admission and retained-release identities, unit bytes, account identities and private-file contents/metadata remained unchanged |

The initial probe used an incorrect ordinary destination and then assumed a
fixture hostname had DNS. These harness failures were inspected before continuing
from the observed state. HTTPS qualification subsequently used the fixture's
certificate and hostname with a loopback connection; verification stayed enabled.
No runtime-binding product defect was reproduced. Automated coverage additionally
checks revoked staging before callback invocation or payload consumption.

The guest was stopped with autostart disabled and its private fixture/evidence
retained. Existing guest configurations, states and running process identities
were preserved. Raw commands, credentials, values, inventory and fixture handles
remain outside Git.

## Remaining boundaries

This pass directly invoked the target's typed handler and deployment components
from a pinned source archive. Full controller setup, enrollment and the SSH
transport with a runtime binding need their own end-to-end qualification. The
selected artifact was packaged from a prepared native executable; this pass did
not exercise a remote build worker or new target toolchain.

Public DNS, ACME/Tunnel ingress, external clients, browser rendering, interrupted
activation, concurrent administrator edits and power loss remain unqualified.
Environment-file edits and deployment are separate transactions. Revocation
blocks future deployment; it does not remove values from a running process.
Automatic rotation, consumer inventory and old-file retirement remain planned.
See [binding setup and recovery](../services/runtime-bindings.md).
