# Stack setup and native workstations

[Documentation index](evidence.md)

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
