# Dated testing evidence summaries

Reconciled 2026-11-01 from retained private reports and the owner's stack setup
rerun report. The documentation update executed no additional live tests.
These sanitized summaries supplement the
[platform status](debian.md) and [coverage map](../plans/testing-coverage.md);
they are not a complete run archive or exact-candidate release qualification.
Exact endpoints, credentials, fixture identities, commands and raw evidence stay
in private operational records. Commit IDs below identify tested source, not
necessarily the current checkout or every installed module on a retained guest.

## Owner-reported stack setup rerun

**Reported 2026-11-01; exact execution dates, source revisions and installed
versions were not supplied.** The owner reports successful setup reruns across
the stack, a partial CachyOS setup applied with `basaltw refresh`, and basic
native Windows setup with Godot and T3 Code. This is an indicator of **basic
setup functionality only**. Not all selected features were tested, and the
commands alone do not establish application behavior, service health, notification
delivery, registration state or recovery guarantees. These are owner-reported
results, separate from the retained agent-run tests below.

The following summarizes the supplied selections without publishing host names,
endpoints, deployment repositories, accounts or storage inventories. Options
describe what was included in a setup invocation; they are not individual feature
passes. Counts distinguish command variants, not fresh installations.

| Setup scope | Selected configuration in the reported rerun | Result and limit |
| --- | --- | --- |
| `server_web` — two commands | Provisioned guest and existing server; Cloudflare, including an allow-direct variant; multiple deployments, shared-domain aliases and branch-specific sources. One command also selected hostname, web panel/TLS and webhook notifications | Basic setup reruns reported successful; individual sites, public ingress, certificate issuance and deployment hooks were not separately qualified |
| `server_proxmox` — two commands | Restart-if-needed/wait, LAN source allowlists; one command selected web panel/TLS and another selected webhook notifications | Basic host setup reruns reported successful; actual restart occurrence, panel flows, guest preservation and maintenance recovery were not separately reported |
| `agent_code_vm` — one smaller tool selection | Provisioning, device-pairing and privilege-broker credentials, Guacamole, web panel/TLS, webhooks, Playwright, Node, AV/PDF tools and Inkscape; explicit GitHub CLI exclusion | Basic setup rerun reported successful; individual pairing, approval, remote desktop, browser and application checks were not separately reported |
| `agent_code_vm` — two broader tool selections | OpenCode, Git LFS, AV/audio/GL/container/debug/PDF/game-development bundles, Blender, Inkscape, Audacity, Shotcut and MuseScore; Playwright, Node, Go, Godot web bundle and Python; Guacamole, broker, panel/TLS and notification ingest. One variant used a separate ext4 home disk | Basic setup reruns reported successful; tool launch, builds/exports, hardware acceleration and populated-home migration were not separately qualified |
| `agent_code_vm` — one initially stopped VM | Provision-on rerun with pairing/broker, panel/TLS, webhooks, Playwright, Node, separate ext4 home disk and restart/wait selections | **Starting the stopped VM succeeded**, explicitly reported by the owner; this does not qualify every lifecycle state or restart/recovery boundary |
| `server_github_runner` — one command | Debian provisioned guest, repository/user/label policy, `--ci-register-with-gh`, web panel/TLS, webhooks, restart/wait and LAN allowlist | Basic setup rerun reported successful; no separate registration-state, GitHub job, policy enforcement or cancellation result was supplied |
| `server_lite` — one unprivileged storage server | Samba accounts and multiple writable shares, daily mirror, monthly PAR2 scrub, panel/TLS, webhooks, LAN allowlist and restart/wait | Basic setup rerun reported successful; individual SMB client, scheduled sync/scrub and data recovery checks were not separately reported |
| `server_lite` — one provisioned service guest | Gogs, Syncthing and Samba with separate ext4 data mounts; disk SSD/discard/backup selections; automatic swap mode, zram plus swap file and swappiness; panel/TLS and webhooks | Basic setup rerun reported successful; Git transfers, synchronization, share permissions, swap behavior and disk backup/discard behavior were not separately qualified |
| Partial `agent_cachyos` local setup via `basaltw refresh` | T3 Code desktop, Node/Python/Go/Git LFS, game-development/Godot, AV/GL and gaming/Sunshine selections; OBS, Blender, Kdenlive, Krita, GIMP, Inkscape, Shotcut, Audacity, LMMS and Material Maker; LAN allowlist and webhooks | Partial refresh reported successful; the supplied command was incomplete. Fresh full setup, desktop/provider use, streaming and creative/game application QA remain open |
| Native Windows, non-WSL | Basic setup with Godot and T3 Code; exact command not supplied | Basic setup reported successful; no WSL result or separate application, export, provider, reboot or recovery result was supplied |

The provisioned agent commands also varied memory/ballooning, CPU, image storage
and disk selections, with LAN allowlists and restart/wait flags. These selections
do not establish resource floors, performance or that a reboot was required.
The report adds no full feature sweep or exact-candidate release qualification;
existing unrun cases and historical failures remain open.

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

## USB storage

| Date / source | Check level and observed result | Qualification boundary |
| --- | --- | --- |
| 2026-11-01 / native host configuration; source unchanged | **Authorized storage operation:** mounted existing ext4 by UUID, validated the declaration/generated mount unit, and enabled the image pool while retaining the mount-point guard. Temporary 1 MiB fsynced write/read hash passed and probe was removed. Existing raw guest disk and guest configurations/states/running PIDs were preserved; SMART health passed | No format, repartition or guest start. Newly added mount declaration has not been tested through a later reboot. No endurance, physical disconnect or power-loss qualification |

Earlier single-device Btrfs guest setup/rerun/reboot and sync/PAR2/native read-only
scrub coverage remains recorded in the [coverage map](../plans/testing-coverage.md).
That earlier guest reboot does not qualify the later host mount declaration.

## Shares and Data

| Date / source | Check level and observed result | Qualification boundary |
| --- | --- | --- |
| 2026-10-31 / installed backend `e7f8e43f` | **Installed workflow:** authenticated HTTPS forms, CSRF, independent approval and actual root workers exercised Samba shares/accounts, password reset and enable/disable/removal, backup mirror controls and persistence, PAR2 scans/findings and targeted verify/restore/accept. SMB access and retained Unix identities/files were checked; targeted repair also has real PAR2 component evidence | Selected service qualification on a non-root Debian operator guest, not every setup profile or installed browser interaction. Cleanup retained accounts, files/parity and broker evidence, restored saved configuration and stopped the fixture |
| 2026-10-31 / follow-ups through `bca3b72e` | **Local regression/browser:** later per-task selection, filters, pagination, draft retention and canonical setup-recovery guards passed relevant regressions and six Chromium/Firefox cases, including keyboard, no JavaScript, narrow layout, text zoom and themes | These follow-ups were not redeployed for installed-system qualification. Browser services/approvals were simulated; a live current-source operator/approval/worker pass remains open |

Use [Continue testing on Proxmox](continuing.md) for the next bounded batch.
The Proxmox root panel is not the non-root guest approval environment required
by [Shares & Data](../services/shares-data.md).

## Automated validation

| Date / source | Observed result | Qualification boundary |
| --- | --- | --- |
| 2026-10-31 / Proxmox fixes at `a4400a78` | Full `make -j2 check-ci`: 9,243 tests, six skips, 82% branch-aware aggregate coverage; static/docs/brand/metadata/wheel checks passed. [Hosted CI](https://github.com/bluehexagons/basaltwater/actions/runs/41253522451) passed full Debian/Node 24 and Node 26 compatibility jobs | Historical source revision; mocked system calls and fixture coverage do not establish live behavior of all profiles/options |
| 2026-10-31 / Shares & Data UI at `bca3b72e` | Full `check-ci`: 9,190 tests, six skips, 82% aggregate coverage; all six local Chromium/Firefox Shares cases passed | Separate historical revision and browser run, not an installed-system refresh |

Failures remain in private evidence alongside successful continuations. The
Proxmox suite initially exposed two corrected test-fixture issues; the Shares
UI run initially exposed a corrected navigation wait in its browser test.
Successful reruns do not erase those records. Later documentation/privacy checks
listed in the [overview](README.md) have their own scope and do not repeat these
live or full-suite runs.
