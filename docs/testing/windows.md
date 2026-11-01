# Windows and WSL testing status

**Evidence reconciled 2026-11-01 from installed native checks, retained task
and project QA records, and the owner's basic setup report.** Native Windows
and Ubuntu WSL are separate environments: a pass on one does not establish the other. See the
[status legend](README.md#status-legend) and
[Windows qualification procedure](../workstations/windows-qualification.md).

## Native Windows 11

The experimental `agent_windows` profile prepares an existing x86-64 account
for coding and native tooling. It does not install the Linux controller, managed
web services, privilege broker or remote setup profiles. See the
[native workstation guide](../workstations/windows.md).

| Available feature | Status | Evidence | Next qualification boundary |
| --- | --- | --- | --- |
| Workstation setup and tool discovery | 🟡 **Partial** | Saved setup complete at `5f549204`; installed doctor passed all 28 checks on 2026-11-01, including selected tools and SDK discovery | Observation of a prepared account, not a witnessed fresh installation; WinGet/UAC, missing-package installation and live recovery remain open |
| T3 desktop with Codex | 🟢 **Labbed** | Native T3 conversation and shell-tool execution support repository work and tests; enabled login task and executable discovered | Interactive terminal UI, pairing/Connect, other providers, actual login startup, refresh/relaunch and reboot recovery were not exercised in this pass |
| Installed skill freshness | 🟢 **Labbed** | Five selected bundles have complete delivery and match the saved source at `5f549204`; absent unselected bundles are informational | Session-loaded revisions remain unknown; this inventory is not an instruction-reload test |
| Native launcher and task help | 🟢 **Labbed** | Installed executable preserved literal/empty/quoted/Unicode argv, working directory and task-local environment on 2026-11-01; namespace help explains npm scripts, explicit executables and waiting | Compilation rollback and active replacement have fixture coverage; fresh installation and other invoking-shell binders remain separate |
| Process supervision | 🟡 **Partial** | Native process fixtures cover failures, timeouts, cancellation and child cleanup | Logoff, reboot and additional interruption boundaries |
| Scheduled development tasks | 🟢 **Labbed** | Earlier real tasks cover cancellation and completion after CLI exit; installed `5f549204` preserves late flood output, separate byte counters and exit 7, while caller wait timeout leaves work running | Logoff/reconnect, T3 closure during active work and live reboot/interruption remain open |
| Desktop API readiness | 🟡 **Partial** | Installed doctor reports a healthy interactive Win32/UI Automation backend, with no capture or input | Window inspection, mutation, screenshot fidelity and physical interaction require separate tests |
| Physical presentation, audio and input | ⚪ **Untested** | No complete manual interaction pass recorded; bounded OpenGL project fixtures are separate evidence | Actual display presentation, audible audio, physical input, lock and reconnect |
| Sunshine streaming | 🟡 **Partial** | Installed service observed running with automatic startup on 2026-11-01; offline package/service fixtures pass | Fresh install, firewall, actual boot startup, pairing and Moonlight video/audio/input/reconnect remain open |
| Windows/WSL GitHub runner | 🟡 **Partial** | Offline native runner tests; live Windows 11/WSL qualification open | Registration, real jobs, cancellation and reboot |
| Selected native builds, packages and Godot exports | 🟢 **Labbed** | Historical project-owned QA from 2026-10-28 covers C++/Node builds and tests, bounded hardware OpenGL fixtures, extracted-package startup and Godot source suites/export/headless startup; Godot 4.7.2 executable rechecked on 2026-11-01 | Projects were not rebuilt in this pass; matching templates are not revalidated by doctor. Manual presentation, physical input/audio, clean-machine packages and broader projects remain open |
| Managed native build host | ⚪ **Planning only** | Proposed backend; distinct from the existing runner | Implementation, isolation, transport and native workload qualification |

The 2026-10-31 [native record](../workstations/windows-qualification.md#recorded-local-run-2026-10-31)
passed the complete offline suite on Windows PowerShell 5.1 and focused contracts
and native process fixtures on PowerShell 7. Installers, notifications and sleep
were mocked, and POSIX-only cases were skipped.
Later selected task/launcher checks are described there. The
[dated native Windows evidence](evidence.md#native-windows-workstation-and-tasks)
separates historical checks from the installed 2026-11-01 results and current
offline validation. None is a fresh installation or release pass.

The installed task probe used the public native executable, real limited
interactive Scheduler and a disposable project. It confirmed the end marker
both while the child paused after overflowing stdout and after its intentional
exit 7. A one-second caller wait returned 124 without cancellation; the final
wait returned a completed failed receipt and CLI exit 1. The child, private
environment request and Scheduler registration were absent afterward. No
refresh, package change, desktop input, reboot or WSL command was performed.

Selected project checks retain their own dates and limits in the public
[native game QA record](https://github.com/bluehexagons/antistatic/blob/main/docs/qa/native.md#verified-windows-local-qualification)
and [Godot project QA record](https://github.com/bluehexagons/snake2d/blob/main/docs/manual-testing.md#recorded-windows-automated-qualification--2026-10-28).
These qualify those workloads only, independently of Basaltwater; their detailed
assertion counts, runtime pins and manual follow-ups stay with the projects.

The separate [owner report](evidence.md#owner-reported-stack-setup-rerun) indicates
basic setup functionality only; not all features were tested. Its exact execution
date, source revision, Windows version and setup command were not supplied.
It adds no WSL qualification or broader native application QA.

## Ubuntu WSL

The `server_wsl` path prepares an Ubuntu build/agent environment inside Windows;
Windows host setup and the Linux environment have separate evidence. See the
[WSL guide](../workstations/windows-wsl.md).

| Feature | Status | Evidence | Next qualification boundary |
| --- | --- | --- | --- |
| WSL setup and Linux build/agent tools | 🟡 **Partial** | Native suite exercises installer planning and mocked WSL recovery | Complete fresh Windows/WSL installation, resume and actual workloads |
| Native T3 desktop connected to WSL | ⚪ **Untested** | Native Windows/WSL 0.1.6 interaction remains unqualified | Real connection, provider/terminal and restart flow |
| Windows runner using WSL | ⚪ **Untested** | Documented setup path; live Windows 11/WSL qualification open | Real native job invoking the selected WSL environment |

## Next checks

1. Use a fresh disposable Windows VM to exercise installer, package, reboot
   and recovery paths; retain individual failures and retries in private evidence.
2. Separately exercise native desktop/streaming hardware and any project-owned
   build, export and manual QA cases that matter to the intended use.
3. Qualify WSL setup and the selected Windows/WSL integration, then real runner
   registration and jobs when an authorized test repository is available.
