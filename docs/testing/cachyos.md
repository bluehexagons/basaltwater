# CachyOS testing status

**Evidence recorded by 2026-10-31; exact dates for earlier native checks were
not recorded in the public summary.** The experimental `agent_cachyos` profile
adds tools to an existing, updated x86-64 KDE workstation. Server profiles and
CachyOS VM/container provisioning are outside its scope. See the
[setup guide](../workstations/cachyos.md) and [status legend](README.md#status-legend).

| Available feature | Status | Evidence | Next qualification boundary |
| --- | --- | --- | --- |
| Workstation setup and tool discovery | 🟡 **Partial** | Earlier native workstation discovery | Fresh setup, package changes, rerun and recovery |
| Native graphics | 🟡 **Partial** | Accelerated GLX and isolated llvmpipe contexts | Native Wayland, Vulkan, physical display and application-specific GPU use |
| Blender and visual workflows | 🟡 **Partial** | Small native Blender CPU render | Full project scenes, image fidelity and hardware rendering |
| Supervised development tasks | 🟡 **Partial** | Disposable owned tasks exercised natively | More interruption, long-running task and project-specific cases |
| T3 web runtime | 🟡 **Partial** | 0.1.6 archive, PTY, disposable HTTP startup, SQLite and generated unit checks | Live unit activation/restart, pressure and complete client/provider workflows |
| KDE desktop capture and input | ⚪ **Untested** | No complete native desktop-control pass recorded in these summaries | Consent, capture, input, takeover and reconnect |
| Optional Sunshine streaming | ⚪ **Untested** | No end-to-end stream pass recorded in these summaries | Service, pairing, video/audio/input and reconnect |
| Creative/game tools and publishing | ⚪ **Untested** | Package selection is documented; package presence is not app QA | Launch, project-specific build/export, input and publishing |
| Desktop/browser CA enrollment | ⚪ **Untested** | Arch userland probes ran as container root, not on the CachyOS desktop | Full desktop and browser trust with the intended client |
| Managed Playwright | ⚪ **Planning only** | Deferred | Implementation and native qualification |

Sources: [native workstation observations](../plans/workstation-feedback-debian.md#cross-platform-findings),
[T3 component record](../agents/t3-code.md), and the
[detailed coverage map](../plans/testing-coverage.md). An isolated component
pass does not qualify a fresh workstation installation or another graphics backend.

## Next checks

1. On a suitable disposable or explicitly authorized workstation, exercise
   setup, rerun and recovery with recorded tool and OS versions.
2. Qualify desktop consent, capture and input; test the actual application and
   graphics backend separately from software-rendered references.
3. If selected, test T3 service restart and Sunshine video, audio, input and
   reconnect. Record private evidence and publish only sanitized outcomes.
