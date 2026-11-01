# CachyOS testing status

**Evidence includes native T3 checks and automated validation on 2026-11-01,
an owner report received that day, and earlier native checks recorded by
2026-10-31.** Exact execution dates and revisions for the earlier setup report,
and dates for earlier native checks, were not supplied in the public summary.
The experimental `agent_cachyos` profile adds tools to an existing, updated
x86-64 KDE workstation. Server profiles and
CachyOS VM/container provisioning are outside its scope. See the
[setup guide](../workstations/cachyos.md) and [status legend](README.md#status-legend).

| Available feature | Status | Evidence | Next qualification boundary |
| --- | --- | --- | --- |
| Workstation setup and tool discovery | 🟡 **Partial** | Owner-reported partial local setup via `basaltw refresh`; native read-only doctor completed with 89 available, 15 deferred and no failed observations on 2026-11-01 | Fresh full setup, individual tool behavior, reboot and recovery; inventory is not application QA |
| T3 Code desktop setup | 🟡 **Partial** | Selected in the owner-reported refresh; installed `t3code-bin` 0.1.6-1 and running packaged desktop/backend observed natively | Fresh installation, actual AUR update, KDE login autostart and pairing |
| T3 Code desktop session with Codex | 🟢 **Labbed** | Owner's manual reopen after waiting succeeded; subsequent live Codex conversation, shell-tool execution, repository edits, tests, commit and push worked through T3 | Other providers, T3's interactive terminal UI, pairing/Connect and broader GUI workflows |
| T3 Code desktop refresh/relaunch | 🟡 **Partial** | Native logs confirmed the old backend still owned the T3 home during premature reopen. Fix `bf0eb670` passed automated shutdown/timeout regressions; read-only native discovery captured the live embedded server | Corrected worker has not been exercised through a live shutdown/relaunch; repeat refresh, slow-backend exit, timeout, reboot and interruption remain open |
| Native graphics | 🟡 **Partial** | Accelerated GLX and isolated llvmpipe contexts | Native Wayland, Vulkan, physical display and application-specific GPU use |
| Blender and visual workflows | 🟡 **Partial** | Small native Blender CPU render | Full project scenes, image fidelity and hardware rendering |
| Supervised development tasks | 🟡 **Partial** | Disposable owned tasks exercised natively | More interruption, long-running task and project-specific cases |
| T3 web runtime | 🟡 **Partial** | 0.1.6 archive, PTY, disposable HTTP startup, SQLite and generated unit checks | Live unit activation/restart, pressure and complete client/provider workflows |
| KDE desktop capture and input | ⚪ **Untested** | No complete native desktop-control pass recorded in these summaries | Consent, capture, input, takeover and reconnect |
| Optional Sunshine streaming | ⚪ **Untested** | No end-to-end stream pass recorded in these summaries | Service, pairing, video/audio/input and reconnect |
| Creative/game tools and publishing | 🟡 **Partial** | Broad tool selections included in the owner-reported refresh; application QA remains untested | Launch, project-specific build/export, input and publishing |
| Desktop/browser CA enrollment | ⚪ **Untested** | Arch userland probes ran as container root, not on the CachyOS desktop | Full desktop and browser trust with the intended client |
| Managed Playwright | ⚪ **Planning only** | Deferred | Implementation and native qualification |

The owner supplied a partial `agent_cachyos` command including development,
Godot, AV/GL, creative/audio, gaming/Sunshine, LAN access and webhook options.
The refresh is an indicator of basic setup functionality; not all selected
features were tested. Sunshine streaming and application workflows retain their
separate qualification gaps. See the
[sanitized selections and limits](evidence.md#owner-reported-stack-setup-rerun).

The later [T3 recovery and validation record](evidence.md#cachyos-t3-desktop-recovery)
qualifies the narrow running desktop/Codex session and discovery component.
It records the failed automatic reopen separately from the successful manual
recovery. At `bf0eb670`, all 2,107 agent-suite tests passed, including 42 focused
desktop tests, alongside compilation, documentation and package metadata checks.
Those mocked regressions do not establish a successful live refresh with the fix.

Sources: [native workstation observations](../plans/workstation-feedback-debian.md#cross-platform-findings),
[T3 component record](../agents/t3-code.md), and the
[detailed coverage map](../plans/testing-coverage.md). An isolated component
pass does not qualify a fresh workstation installation or another graphics backend.

## Next checks

1. Exercise the corrected T3 completion worker through a real shutdown/relaunch,
   then verify a provider turn and the interactive terminal; preserve the
   previous failure evidence and record the exact installed revision.
2. On a suitable disposable or explicitly authorized workstation, exercise
   fresh full setup, repeat refresh, reboot and recovery with recorded tool and
   OS versions.
3. Qualify desktop consent, capture and input; test the actual application and
   graphics backend separately from software-rendered references.
4. If selected, test T3 web-service restart and Sunshine video, audio, input and
   reconnect. Record private evidence and publish only sanitized outcomes.
