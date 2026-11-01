# Dated testing evidence summaries

Reconciled 2026-11-01 from retained private reports, the owner's stack setup
rerun report, subsequent native CachyOS T3 and SDL/OpenGL application observations,
installed native Windows checks, current Shares & Data qualification,
package-changing Admin and Proxmox inventory qualification, and automated checks.
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
| Partial `agent_cachyos` local setup via `basaltw refresh` | T3 Code desktop, Node/Python/Go/Git LFS, game-development/Godot, AV/GL and gaming/Sunshine selections; OBS, Blender, Kdenlive, Krita, GIMP, Inkscape, Shotcut, Audacity, LMMS and Material Maker; LAN allowlist and webhooks | Partial refresh reported successful; the supplied command was incomplete. Fresh full setup, streaming and creative/game application QA remain open. Later T3 session success and an automatic-reopen failure are recorded separately below |
| Native Windows, non-WSL | Basic setup with Godot and T3 Code; exact command not supplied | Basic setup reported successful; no WSL result or separate application, export, provider, reboot or recovery result was supplied |

The provisioned agent commands also varied memory/ballooning, CPU, image storage
and disk selections, with LAN allowlists and restart/wait flags. These selections
do not establish resource floors, performance or that a reboot was required.
The report adds no full feature sweep or exact-candidate release qualification;
existing unrun cases and historical failures remain open.

## Native Windows workstation and tasks

**2026-11-01; prepared Windows 11 x86-64 account, installed and checkout source
`5f549204ae8cc67751a56b5e4475f605c5159bcb`.** This is installed component/workflow
qualification and a native automated rerun, separate from the earlier owner
setup report. Setup was already complete; no refresh, package installation,
desktop input, reboot or WSL operation was performed during this pass.

| Date / source | Check level and observed result | Qualification boundary |
| --- | --- | --- |
| 2026-11-01 / installed `5f549204` | **Installed diagnostics:** all 28 workstation checks passed. Five selected skill bundles report completed delivery and current source bytes; absent unselected bundles remain informational. Desktop doctor reports healthy interactive Win32/UI Automation readiness without capture/input. Presence-only project doctor returned the expected clean checkout revision | Executable/service/SDK discovery is not application QA or fresh installation. Session-loaded skill revisions remain unknown. No screenshot fidelity, input, real login startup or streaming result |
| 2026-11-01 / installed `5f549204` | **Selected live session:** native T3/Codex conversation and shell-tool execution supported repository inspection, documentation work and tests | Current provider session only; no new pairing/Connect, other-provider turn, interactive terminal UI, refresh/relaunch, logout or reboot qualification |
| 2026-11-01 / installed `5f549204` | **Installed task workflow:** public native executable and real limited interactive Scheduler preserved spaces, empty values, quotes, Unicode and shell-looking literal argv, working directory and task-local environment. The 4 MiB head/tail cap preserved the final marker while a flooded child paused and after intentional exit 7; retention-discard and 64 KiB API-tail-omission counts remained separate. Caller wait returned 124 without cancellation; final wait returned the completed failed receipt and CLI exit 1. Owned child, private request and registration were absent afterward | Disposable Node probe, not a real project build or resource benchmark. Expected child failure is a passed propagation test. No T3 closure/reconnect, logoff or interruption during active work |
| 2026-11-01 / checkout `5f549204` | **Automated native validation:** complete `Run-NativeTests.ps1` passed in 231.015 seconds from Windows PowerShell 5.1. Its 11 Python capture tests had two explicit POSIX-only skips. Native process/PowerShell 7 guardian fixtures, launcher/CLI, skill, desktop, refresh, restart and runner contracts passed; expected mocked warnings were inspected | Temporary state and mocked installers, WSL, notifications, sleep/reboot and runner registration do not establish real setup or those live workflows. This is separate from Debian full CI and exact-candidate release qualification |
| 2026-10-31 / launcher `392cb47a`, task output/wait `322e915c` | **Retained historical workflow/component evidence:** real Scheduled Tasks completed after the submitting CLI exited; cancellation settled owned descendants. Prefix-only log loss was reproduced, then head/tail retention and independent wait passed. Literal native-launcher argv, failure exits, compile rollback and active replacement also have fixture coverage | The first batch launcher lost embedded quotes; preserve that failure and its correction. Historical task checks used the reviewed checkout script entry point; the installed executable path was rechecked above |
| 2026-10-28 / project-owned QA at the revisions linked below | **Historical native workloads:** selected C++/Node builds/tests, bounded hardware OpenGL fixtures and fresh extracted-package startup passed; a Godot project's source suites, Windows export and external-directory headless startup passed with verified matching templates | Not rebuilt on 2026-11-01. These qualify those projects, not every game or current account templates, physical presentation/input/audio, a clean-machine package or publishing |

The public [native game QA](https://github.com/bluehexagons/antistatic/blob/main/docs/qa/native.md#verified-windows-local-qualification)
and [Godot QA](https://github.com/bluehexagons/snake2d/blob/main/docs/manual-testing.md#recorded-windows-automated-qualification--2026-10-28)
own their detailed runtime pins, assertions and retained failure/repair records.
The earlier [Basaltwater native record](../workstations/windows-qualification.md#recorded-local-run-2026-10-31)
owns the historical workstation scope. Raw commands, job handles, account paths
and machine/project inventories remain private. No active or uncertain account
development receipt or development Scheduler registration remained after this pass.
Fresh installation/recovery, physical interaction, Moonlight streaming, real
GitHub runner jobs and WSL integration remain separate qualification.

## CachyOS T3 desktop recovery

**2026-11-01; native CachyOS KDE workstation, packaged T3 desktop 0.1.6-1.**
The installed Basaltwater revision for the failed refresh was not established.
Checkout checks below exercised `bf0eb670`; they do not imply that its corrected
completion worker was installed or run against the live desktop.

| Check level | Observed result | Qualification boundary |
| --- | --- | --- |
| **Owner report plus native failure diagnosis** | Refresh closed T3; automatic reopen and an early manual retry failed with a T3 home ownership error. Retained desktop/backend logs confirmed `ServerAlreadyRunningError` while the old backend was still shutting down | The pre-fix automatic restart failed; successful setup selection does not qualify the final desktop handoff |
| **Selected live session after manual recovery** | Owner reopened T3 after waiting. The running packaged desktop and embedded backend were observed; subsequent Codex conversation and shell-tool execution completed repository reads/edits, tests, commit and push through T3 | Qualifies that desktop/Codex session only. No new pairing/Connect enrollment, other-provider turn, interactive terminal UI, KDE login or reboot test |
| **Native read-only component at `bf0eb670`** | New process discovery captured three direct packaged child handles, including the live embedded server. Handles were closed without signaling or restarting the application | Discovery and stable-handle acquisition only; no live corrected shutdown/relaunch, slow-backend wait or timeout |
| **Installed read-only inventory** | `basaltw local cachyos-doctor --attention --json` completed: 104 observations, 89 available, 15 deferred, zero failed | Deferred checks remain unresolved; package/configuration observations do not establish application, firewall, capture or streaming behavior |
| **Automated at `bf0eb670`** | All 42 desktop-focused tests passed, then `make check-fast SUITE=agent` passed all 2,107 tests, compilation, documentation and package metadata checks. Regressions cover backend exit after desktop exit, reverse/simultaneous exit order, one shared timeout, invalid handles, process identity and handle cleanup | Focused tests are included in the broader count. System calls were mocked and process fixtures were temporary; this was not full CI, browser testing or live refresh qualification |

The next native check is a refresh using the corrected completion worker,
followed by an authenticated provider turn and interactive terminal check.
Keep the original failed reopen in the record when that retry succeeds.

## CachyOS native game and capture workflow

**2026-11-01; existing CachyOS KDE Wayland workstation, Antistatic source through
[`af23811f2`](https://github.com/bluehexagons/antistatic/commit/af23811f228dc156c5b01f04270e2000de6c3566).**
The installed Basaltwater revision was not recorded for this application pass.
These results qualify selected installed workflows, not a fresh workstation
setup or an exact Basaltwater release candidate.

| Check level | Observed result | Qualification boundary |
| --- | --- | --- |
| **Project source validation** | Final `npm test` and screenshot smoke passed. Earlier native/rendering cleanup at `643533f60` also passed the project's full gate | Project checks, not Basaltwater unit-test counts or Debian packaging qualification. Initial formatting failures were corrected and the complete source gate rerun |
| **Supervised native application** | `develop exec` launched the project harness in the existing session; `develop wait` retained successful completion. Application records confirmed SDL Wayland and Mesa Intel Arc OpenGL 4.6, separately from software reference captures | Existing installed tools/session only; no refresh, reboot, disconnect recovery, other GPU or performance acceptance |
| **Scripted UI and native capture** | Project-protocol actions and state assertions covered controls-menu scrolling and a pause preference toggle. Application screenshots and a fresh selected-monitor capture were reviewed; owned game processes exited cleanly | Scripted pointer/controller actions do not establish physical input, audio, HDR, scaling or compositor pacing. Portal input holds were not requalified by these application commands |
| **Display-wake recovery** | An initial black monitor capture recovered after the enabled native `wake` command; fresh desktop and game captures were inspected before continuing | One sleeping-display case, not locked/disconnected-output recovery or repeated-cycle qualification |
| **Fixed software references** | Four deterministic 640×360 effect pairs matched byte-for-byte under isolated X11/llvmpipe. Original per-image records matched actual frame, camera, requested/applied graphics and GL identity | Strict batch comparison remained rejected because its manifest lacked complete runtime metadata; the separate sidecar check supplies the comparison evidence. Software results do not qualify native GPU behavior |

Earlier pause captures remained obscured by a presentation countdown when the
fixture advanced simulation without presenting intermediate frames. The final
two-player fixture rendered between bounded steps, checked pause/controller
state, and kept SDL pumping until the parent finished its monitor capture.
Randomized battle images were not counted as whole-image equality passes.
These fixture limitations and the incomplete batch metadata remain in private
evidence alongside the successful continuations; no Basaltwater runtime defect
was confirmed by those failures.

Antistatic's source/desktop checks remain separate from game packaging, which
currently belongs to CI and Debian. No package was built or qualified on CachyOS
in this pass. Follow the [optional integration workflow](../integrations/antistatic/cachyos.md)
and [capture comparison guidance](../workstations/visual-comparisons.md#compare-existing-captures).
Raw captures, transcripts, task handles and account paths remain private.

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
failure visible and diagnose it before extending the HTTP/browser acceptance;
successful host-local and later controller probes do not erase that boundary.

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
| 2026-10-31 / follow-ups through `bca3b72e` | **Local regression/browser:** later per-task selection, filters, pagination, draft retention and canonical setup-recovery guards passed relevant regressions and six Chromium/Firefox cases, including keyboard, no JavaScript, narrow layout, text zoom and themes | Browser services/approvals were simulated; installed HTTP qualification followed on 2026-11-01 below |
| 2026-11-01 / clean installed `5184cef0` | **Installed workflow:** fresh disposable Debian guest, non-root operator, independently authenticated approvals and real root workers. Denial/cancellation and one-use submission, Samba account/share lifecycle, encrypted SMB read/write and access rejection, mirror create/edit/pause/resume/run/remove, literal paths and deletion, PAR2 scans and approved verify/restore passed. Actual HTTPS catalog checks covered 22 tasks, 26 findings, pagination, exact case-sensitive root/parity selection, independent filter/clear links, Unicode queries, unavailable selection and stale copyable drafts | Normal custom-step service setup, not a full-profile installation. Initial missing firewall prerequisites and harness failures were retained and inspected before continuation. Fresh missing-file repair exposed absent identity metadata; fixed below. Acceptance was not repeated in this run |
| 2026-11-01 / fix installed `ed2fa820` | **Installed recovery:** normal source deployment retained saved declarations, pause controls and fixture bytes; all 579 runtime files matched the controller. A fresh missing finding recorded explicit absent identity and separately approved repair reached real PAR2. **Automated:** 608 storage/recovery tests plus compilation, documentation and package checks passed for the fix | PAR2 correctly reported insufficient recovery data for an entirely missing file protected by 10% parity. The source remained absent and its finding open. This qualifies the identity fix and insufficient-parity result, not successful targeted repair |
| 2026-11-01 / installed `ed2fa820` | **Installed exclusion/persistence:** a retained synthetic setup guard blocked controls, approved dispatch and root execution; a live canonical setup owner excluded a real worker. Guarded refresh refused before source activation. Approved task removal retained parity and showed an unavailable selection. Panel restart and an owned-guest reboot preserved approved account/share settings, 21 paused scrub tasks, identities/groups, TLS/auth and exact data/parity bytes; catalog pagination/selection and encrypted SMB writes passed after boot. All 30 real requests retained the normal rate/quota policy | Refresh was an installed component probe; this custom-step fixture does not qualify public full-profile refresh. Only the owned synthetic guard was reconciled. Scheduled storage also refused that guard and resumed afterward. The fixture was stopped with evidence, data and a local snapshot retained; existing guests and host configuration were preserved |
| 2026-11-01 / clean installed `974a1652` | **Installed recovery follow-up:** normal current-source setup preserved prior declarations, pause controls and bytes, with all 574 runtime files matching. A separate paused 100%-parity fixture exercised independently approved real workers: missing-file repair reproduced the exact original, an intervening-file race failed safely with `file_changed` while preserving the new file, and deliberate replacement was accepted with old parity/originals retained. A subsequent separately approved full scan reported healthy with no open finding | Adequate recovery data qualifies this successful repair; the earlier 10%-parity failure remains valid. This is selected service setup on the owned guest, not full-profile refresh or interruption/power-loss qualification |
| 2026-11-01 / fix installed `986d3061` | **Installed browser:** Chromium 156 and Firefox 157 exercised real authenticated navigation, retained share/account display, independent filters, task/finding pagination, exact root/parity selection, keyboard disclosure and recovery review/cancel. Wide light/JavaScript and 320px dark/no-JavaScript/200% text passed. Both engines reproduced valid 200-character supplementary Unicode searches truncating to 100; corrected controls retain and submit all 200 while rejecting overlong input. Normal deployment matched all 574 runtime files and retained both fixtures | Isolated profiles trusted only the public guest CA verified through pinned SSH; TLS verification stayed enabled. Browser recovery checks stopped at review/cancel; approval/worker results are the separate real HTTPS flow above. Shared preview trust and native Firefox ESR remain open. Initial rate-limit, navigation-wait and oversized Firefox screenshot failures were harness issues, inspected before paced/viewport continuations |
| 2026-11-01 / fix installed `7ba41724` | **Installed audit scheduling:** ordinary setup exposed an active but elapsed audit timer with no future deadline. Adding a five-minute deadline relative to timer activation restored scheduling; normal installation retained declarations/controls/bytes and matched all 574 runtime files. An actual unattended export published a newer valid snapshot after about five minutes and rearmed another future deadline. Final Firefox checks also passed at this revision | Observation used the normal timer without manual export/restart or schedule overrides. This covers the selected guest exporter, not the Proxmox inventory timer or interrupted setup |

The configured interactive preview failed twice with `ERR_CERT_AUTHORITY_INVALID`;
its tab was closed. The later permitted fallback used the repository's existing
stable test runtime and isolated CA trust; it does not fix shared preview trust
or qualify native ESR. Initial setup, harness and insufficient-parity failures
remain in private evidence; no uncertain request was replayed and no server
rate/quota policy was relaxed. No host reboot, formatting or disk stress was
performed. Full-profile refresh and interruption/power-loss remain separate checks.

Follow-up cleanup used a ninth separately approved request to remove its routine,
retaining all new data/parity/recovery evidence and restoring the prior declarations,
pause controls and fixture bytes. All 39 request records were retained, with no
pending approvals, setup/unit recovery guards or staged passwords. The owned guest
was stopped with private evidence, an online broker backup and an offline local
snapshot retained. Host configuration/boot and other guest configurations/states/PIDs
matched the fresh baseline; a same-node snapshot does not qualify off-host recovery.

Use [Continue testing on Proxmox](continuing.md) for subsequent bounded batches.
The Proxmox root panel is not the non-root guest approval environment required
by [Shares & Data](../services/shares-data.md).

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

## Automated validation

| Date / source | Observed result | Qualification boundary |
| --- | --- | --- |
| 2026-10-31 / Proxmox fixes at `a4400a78` | Full `make -j2 check-ci`: 9,243 tests, six skips, 82% branch-aware aggregate coverage; static/docs/brand/metadata/wheel checks passed. [Hosted CI](https://github.com/bluehexagons/basaltwater/actions/runs/41253522451) passed full Debian/Node 24 and Node 26 compatibility jobs | Historical source revision; mocked system calls and fixture coverage do not establish live behavior of all profiles/options |
| 2026-10-31 / Shares & Data UI at `bca3b72e` | Full `check-ci`: 9,190 tests, six skips, 82% aggregate coverage; all six local Chromium/Firefox Shares cases passed | Separate historical revision and browser run, not an installed-system refresh |
| 2026-11-01 / Unicode fix `986d3061`, timer fix `7ba41724` | Each fix passed `make check-fast SUITE=web`: 1,289 tests run, one skipped, plus compilation, docs and package metadata. All eight focused local Chromium/Firefox Shares cases passed at `986d3061`; the new Unicode regression failed in both engines before the fix | Focused checks, not a new full CI or release-candidate pass. Local browser services use simulated host actions; installed-service results are recorded separately above |

Failures remain in private evidence alongside successful continuations. The
Proxmox suite initially exposed two corrected test-fixture issues; the Shares
UI run initially exposed a corrected navigation wait in its browser test.
Successful reruns do not erase those records. Later documentation/privacy checks
listed in the [overview](README.md) have their own scope and do not repeat these
live or full-suite runs.
