# Admin package and service operations

[Documentation index](evidence.md)

## Package-changing Admin update

**2026-11-01; clean installed `176caeff62ad` on a fresh disposable Debian 13
guest.** Normal custom-step setup configured a non-root panel operator, separate
HTTPS approval identity and real task/broker workers. All 574 runtime files
matched source before and after qualification. Base OS updates and task-tool
installation were fixture preparation, separate from the approved transactions.

| Check level | Observed result | Qualification boundary |
| --- | --- | --- |
| **Installed HTTPS and approval guards** | Controller-origin CA/hostname-verified panel and approval responses passed, as did direct panel Unix-socket HTTP. Anonymous and cross-password access, invalid CSRF, reused submission and reused approval were rejected. Cancellation and denial left packages, tasks and services unchanged | Actual HTTP forms and separate credentials, without a new interactive browser pass. Initial missing task-tool and private fixture-directory permission failures were inspected and corrected before the transaction baseline |
| **Installed failed update** | An owned unreachable repository produced a separately approved real worker failure at index refresh, command 1, exit 100. Upgrade did not run; the older package, task records, service identities, credentials and data remained unchanged. The temporary source was removed after the terminal outcome | Controlled localhost index failure, not an interrupted download, package-hook failure or power-loss test |
| **Installed package change and rerun** | Normal repositories offered only GNU Hello `2.10-3` → `2.10-5`, after authenticated retrieval of the older Debian package through an isolated index. The approved root worker waited for a real APT frontend lock; active-job controls withheld another submission form. Package version, changed binary and dpkg history proved the upgrade. A new separately approved worker then completed without package or dpkg-log changes | A small leaf-package upgrade, not kernel/service-restarting packages, conffile conflicts, distribution upgrade, host maintenance or interruption recovery. Broker `dispatched` was correlated with distinct worker IDs, serialized request events, action/times and observed outcomes; it was not treated as completion |
| **Installed preservation and cleanup** | The draft, active future schedule and task-state bytes survived both workers, as did all other package versions, task/fixture/panel/broker/approval/Nginx PIDs and activation times, setup/auth/PKI and fixture bytes. The heartbeat continued. Cleanup paused only the owned future schedule and retained all five broker records, three worker outcomes, an online database backup and private evidence | No provider task executed. The owned guest was gracefully stopped with an offline local snapshot; existing guest configurations/states/PIDs and host configuration/boot matched baseline. Same-node retention does not qualify off-host recovery |

No Basaltwater defect was reproduced in this batch. The expected failed worker
and fixture-preparation failures remain in private evidence. No host reboot,
formatting, disk stress, server quota relaxation or uncertain-request replay
occurred. Repeat affected checks when the Admin implementation changes; wider
package and interruption boundaries remain open.

## Admin service restart and conffile conflict

**2026-11-02 UTC; clean installed `3c6fd7fa`, fresh disposable Debian 13 guest.**
Normal public custom-step setup configured a non-root panel operator, independent
HTTPS approval service and actual root Admin worker. All 573 runtime files matched
source. An owned signed guest-local APT repository offered exactly one tiny native
Debian package upgrade; its conffile, daemon and data were isolated fixtures.
No provider credentials or tasks were used. This is real APT/dpkg execution with
a controlled package, not qualification of distribution maintainer scripts.

| Check level | Observed result | Qualification boundary |
| --- | --- | --- |
| **Installed HTTPS/worker** | Controller and guest HTTPS passed CA/hostname verification; anonymous and cross-password access were rejected. Two one-use submissions received separate approvals and completed through distinct actual root workers. Serialized request events, action/times and terminal worker outcomes were correlated | Actual HTTP, without a new browser pass. Broker dispatch alone was not completion |
| **Installed conffile conflict/restart** | Package `1.0` → `2.0` retained the operator-modified conffile and saved the changed packaged default as `.dpkg-dist`. Its real post-install hook restarted the owned daemon: PID/start record changed once, v2 loaded the retained config, and exact data bytes survived. All other package versions, panel/broker/approval/Nginx identities, user IDs/groups, setup/auth/PKI and boot identity stayed exact | The hook briefly waited on an owned bounded fixture signal so the real root worker could be inspected before restart. No distribution service, kernel, failed hook, interruption or power-loss qualification |
| **Installed separately approved no-op** | Another actual worker completed with no package, conffile, dpkg/APT history, data or service-identity changes; the daemon did not restart again | Available upgrade plan was empty; this qualifies repeat behavior after the preceding package change |

Cleanup removed the owned APT source and trust entry, stopped its daemon, and
retained the package, config variants, exact data, two terminal broker records,
worker/APT logs and an integrity-checked online broker backup. The guest is
stopped with autostart disabled and an offline local snapshot retained. Existing
guest configurations/states/PIDs and host boot/setup/storage/mounts matched baseline;
no active hypervisor tasks, guest locks or failed host units remained. Same-node
retention does not qualify off-host recovery.

No product defect was reproduced. All 32 existing Admin job tests passed; final
documentation checks also passed. Base OS/tool installation was preparation.
Its original SSH wrapper ended while the guest's APT process continued; the
original upgrade was inspected to completion, with a clean dpkg audit and empty
upgrade plan. Remaining fixture preparation then ran in an owned independent
unit without replaying that upgrade. This retained preparation failure is
separate from the two successful approved transactions.

## Admin failed maintainer hook and recovery

**2026-11-02 UTC; clean installed `785c3ffd`, fresh disposable Debian 13 guest.**
Normal selected-step setup configured a non-root panel operator, independent
HTTPS approval service and real root Admin workers. All 575 runtime files matched
source. A signed guest-local repository offered exactly one owned native package
upgrade; its post-install hook had an explicit disposable failure condition.

| Check level | Observed result | Qualification boundary |
| --- | --- | --- |
| **Installed failed transaction** | A separately approved worker reported `failed`, command 2, APT exit 100 and `nonzero_exit`; the authenticated panel displayed the same result. The real root hook recorded its matching worker ID and maintenance cgroup before deliberately exiting 42. Dpkg retained v2 as half-configured while the existing v1 daemon continued unchanged | Expected package failure, not a Basaltwater defect or an atomic package rollback. The new package files were already unpacked; the retained running process still used v1 |
| **Installed independently approved recovery** | After removing only the owned failure flag, a new approved update worker configured the retained v2 without a manual APT/dpkg repair command. The package audit became clean and the owned daemon restarted exactly once with v2 and the retained operator configuration | Recovery from this controlled post-install failure; no distribution maintainer script, interrupted process or power-loss qualification |
| **Installed no-op and preservation** | A third separately approved worker completed without another hook call, restart, package change or dpkg/APT-history change. Across all three jobs, other package versions/states, panel/broker/approval/Nginx identities, user IDs/groups, credentials/PKI, boot identity and exact fixture data stayed unchanged. Three distinct terminal workers were correlated with serialized approval records | Real verified HTTPS and workers, without browser or provider execution. Broker dispatch alone was not treated as completion |

Cleanup removed the owned APT source/trust entry and stopped the owned daemon and
completed preparation/qualification units. Package/configuration/data, original
failure records, three terminal requests and an integrity-checked online broker
backup were retained privately. The guest was stopped with autostart disabled and
an offline local snapshot. Existing guest configurations/states/PIDs and host
boot/setup/storage/mounts were preserved; no tasks, locks or failed host units
remained. Same-node retention does not qualify off-host recovery.

No product defect was reproduced. Base OS updates were preparation, separate
from the approved transactions. Distribution service/kernel packages, broader
hook failures, interruption and power loss remain open.
