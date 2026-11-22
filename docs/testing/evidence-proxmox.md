# Proxmox and USB storage

[Documentation index](evidence.md)

## Proxmox setup and maintenance

| Date / source | Check level and observed result | Qualification boundary |
| --- | --- | --- |
| 2026-10-31 / `a4400a78` | **Installed workflow:** ordinary saved public patch completed all 21 steps, recovered the existing interrupted panel operation and left no active recovery record. Guest states/PIDs/configurations, host SSH identity, network/storage policy and existing auth were preserved | Populated repair/rerun, not a clean complete host installation; no password replacement or host reboot by the agent |
| 2026-10-31 / `a4400a78` | **Installed CLI:** managed launcher help, Proxmox local-update preview and refusal with the maintenance lock occupied passed; deployed changed-module hashes matched source | No package-changing local upgrade |
| 2026-10-31 / identity fix `e722de50`, integrated in `a4400a78` | **Automated plus native component:** regression reproduces the old wrong-user payload rejection; root normalization is checked before hashing/serialization. Isolated Nginx accepted generated root auth and rejected another user and anonymous access; real installed auth was retained | The isolated listener is not the public controller-to-host password-replacement flow; native replacement and interruption remain open |
| 2026-10-31 / installed host maintenance | **Installed services:** guarded APT updater and cleanup succeeded; no packages required upgrade. Cache cleanup reclaimed approximately 367 MiB. Package audit and selected SSD health checks passed | No package-changing upgrade, destructive disk test or endurance qualification; prior-boot journal unavailable |
| 2026-11-01 / host source last deployed at `a4400a78` | **Actual HTTPS component:** the operator-configured root credential returned HTTP 200 through the real panel with CA and hostname verification; installed auth bytes were unchanged. Controller cached endpoint was aligned with the target | HTTP authentication, not interactive browser or complete Proxmox console/exporter qualification; user configuration changed independently of source deployment |

The user rebooted the host between the repair and the latest storage/auth checks.
The running kernel changed, and the guest inventory had fewer running guests
afterward. Preservation claims compare each operation with its own baseline;
they do not imply unchanged PIDs or all-guest service health across that reboot.
No Admin power-action path was exercised by that user action.

## Proxmox inventory and exporter

**2026-11-01; existing single-node host.** The overall installation receipt remains
`1aa7a632`; the five qualified collector/renderer modules and five supporting
transaction helpers match `ece5e5bc` byte for byte. No host source redeployment
or complete current-profile qualification is claimed.

| Check level | Observed result | Qualification boundary |
| --- | --- | --- |
| **Installed inventory and scheduling** | Native guest identities, states, types and template flags matched the cached inventory. Two unattended root exports published distinct snapshots, exited successfully and left a future timer deadline. Actual panel and Nginx worker mount namespaces denied access to pmxcfs and its backing database; the unprivileged panel could read, but not write, its root-owned cache | Existing standalone node; no multi-node, HA/Ceph or native console qualification. Observation did not manually start or restart the production collector/timer |
| **Installed host-local HTTPS** | CA/hostname verification, public CA identity, correct/incorrect/anonymous authentication, twelve inventory/filter/sort views, malformed query rejection and absent management routes passed. Reported guest/storage rows matched native or cached references | Full acceptance ran from the host. Controller-origin requests intermittently timed out after partial bodies, with and without compression. Four bounded TCP-observed GETs passed; a further paced probe reproduced the stall with repeated server TCP retransmissions and a stopped client receive counter. Cause remains unresolved, so complete controller transport and interactive browser behavior remain unqualified |
| **Native owned component lifecycle** | Separately named units used the current setup/transaction code with only names and snapshot/wrapper paths overridden in memory. Real read-only Proxmox collection, busy-removal refusal, explicit collector interruption and native-child termination, byte-identical last-readable publication, reconfiguration/recovery and removal passed. Actual unprivileged panel namespace reads covered valid/stale/corrupt copies; managed removal preserved an owned unknown file | Component fixture, not public full-host setup or production panel/Nginx teardown. SIGTERM produced the expected failed oneshot state before recovery; no host failure was injected. Initial harness state, syntax and filename assertions were inspected and corrected; an overly fast HTTP probe hit the unchanged request limit |
| **Preservation and cleanup** | Component evidence was privately archived before enumerated fixture cleanup. All pre-existing guest configurations/states/PIDs, host boot/setup/configuration, storage policy and mounts matched baseline. Production panel/Nginx identities and credentials were preserved; no failed units, active Proxmox tasks or owned lifecycle units remained | No guest allocation/start, host reboot, password replacement, formatting, stress or server limit change. Production teardown, setup interruption/power loss and off-host recovery remain separate |

No Basaltwater defect was confirmed in this batch. Keep the controller transfer
failure visible; successful host-local and later controller probes do not erase
the incomplete HTTP/browser acceptance boundary.
Further bounded captures on 2026-11-02 UTC retained 24 successful persistent
responses and another partial-body timeout. In that failed connection-close
probe, the final 306 encrypted bytes and FIN were repeatedly retransmitted while
the client acknowledgement stayed immediately before them. A server-only capture
cannot identify the dropping endpoint/intermediary; no network configuration or
application fix was applied. All owned capture processes/directories were removed
after private archival. Complete controller/browser acceptance remains open.
The owner deferred further transport investigation on 2026-11-02; unrelated
qualification may continue. No driver or hardware fault has been established.
Retained evidence supports paired endpoint captures and a controlled
keepalive/connection-close comparison when investigation resumes.

## USB storage

| Date / source | Check level and observed result | Qualification boundary |
| --- | --- | --- |
| 2026-11-01 / native host configuration; source unchanged | **Authorized storage operation:** mounted existing ext4 by UUID, validated the declaration/generated mount unit, and enabled the image pool while retaining the mount-point guard. Temporary 1 MiB fsynced write/read hash passed and probe was removed. Existing raw guest disk and guest configurations/states/running PIDs were preserved; SMART health passed | No format, repartition or guest start. Newly added mount declaration has not been tested through a later reboot. No endurance, physical disconnect or power-loss qualification |

Earlier single-device Btrfs guest setup/rerun/reboot and sync/PAR2/native read-only
scrub coverage remains recorded in the [coverage map](../plans/testing-coverage.md).
That earlier guest reboot does not qualify the later host mount declaration.
