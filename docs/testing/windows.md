# Windows and WSL testing status

**Evidence reconciled 2026-11-01 from installed and checkout native checks,
retained task and project QA records, and the owner's basic setup report.** Native Windows
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
| Local process supervision | 🟢 **Labbed** | Real corrected checkout guardians settled timeout and cancellation after owned parent/child exit, private-request removal and verified Scheduler retirement; native fixtures also cover failures and cleanup uncertainty | The installed guardian's premature-completion failure is retained in the follow-up below; logoff, reboot and additional interruption boundaries remain open |
| Scheduled development tasks | 🟢 **Labbed** | Installed `5f549204` preserves late flood output, separate byte counters and exit 7; caller wait timeout leaves work running. This round also ran real pinned npm, MSVC and Godot tasks | Logoff/reconnect, T3 closure during active work and live reboot/interruption remain open |
| Desktop observation and control | 🟢 **Labbed**, scoped WinForms fixtures | Installed and corrected checkout CLIs exercised UI Automation, literal Unicode text, stale-reference rejection, invoke, window actions, inspected window capture, raw keys/text/click and close. Provider disappearance now returns an explicit partial scan in the corrected checkout | Other provider errors remain failures. Hardware-rendered capture, physical interaction, mixed DPI, secure desktop and lock/reconnect remain separate |
| Supervised native workloads | 🟢 **Labbed** | Installed public CLI selected an installed fnm runtime/npm in a spaced Unicode project; task-local MSVC/SDK compiled and ran C; managed Godot imported and ran a disposable headless project | Synthetic projects, not a complete project build/export, template, performance or physical presentation qualification |
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

The earlier installed task probe used the public native executable, real limited
interactive Scheduler and a disposable project. It confirmed the end marker
both while the child paused after overflowing stdout and after its intentional
exit 7. A one-second caller wait returned 124 without cancellation; the final
wait returned a completed failed receipt and CLI exit 1. The child, private
environment request and Scheduler registration were absent afterward. No
refresh, package change, desktop input, reboot or WSL command was performed.

### Follow-up native qualification: 2026-11-01

This round retained installed source `5f549204` and began from checkout
`70373002`. The installed executable passed health, skill freshness and desktop
readiness again; `refresh --dry-run` completed without applying setup. Four
real limited-interactive tasks passed: pinned Node/npm, MSVC C compile/run,
Godot headless import and headless execution. No packages were installed.

The disposable WinForms pass reproduced a restore failure after maximize →
minimize → restore: Windows resumed the prior maximized presentation, but the
adapter incorrectly rejected it. Fix `9ec105aa` passed offline outcome
regressions and live checkout-CLI checks for both that transition and subsequent
restore to normal size. Captured window pixels were inspected. Raw input was
verified in the fixture's text and button state, rather than inferred from
input-queue acceptance. One earlier UIA inspection raced an unavailable element;
fresh observation succeeded, but no fix is claimed for that intermittent error.
The later provider-disappearance qualification below addresses that failure.

A real timeout also exposed `wait` publishing completion before Scheduler
unregistration. The corrected guardian retires and verifies registration before
terminal publication; retirement failure remains `cleanup-uncertain`, without
a completed timestamp. The process regression reproduces the old ordering and
covers unregister failure. Real corrected timeout/cancel tasks returned only
after descendants, registration and private environment requests were gone.
Guardian fix `c53f4a50` includes those regressions; the unchanged installed source
does not include either correction until a later refresh applies them.

The complete native suite passed after both fixes, in 213.840 seconds, with two
explicit POSIX-only skips in its 11 Python capture tests. This is Windows
qualification, not the full Debian CI suite. Its Windows PowerShell 5.1 entry
point also exercises PowerShell 7 guardians. The first captured launch failed because a
Python intermediary inherited PowerShell 7 module paths. Child-only isolation
resolved that failure; the [qualification command](../workstations/windows-qualification.md#check-host-health-and-offline-behavior)
now documents it. Running framework UIA compilation directly under PowerShell 7
is not a supported substitute for that 5.1 suite entry point.

Original adapter failures, fixture corrections and successful continuations
remain in private evidence. Saved installation bytes and human control mode
were unchanged; all owned GUI windows and real task processes, requests and
registrations were gone at the final check. Corrected behavior was tested from
the checkout; no installed refresh, reboot, WSL, streaming or physical-input
qualification is implied.

### Provider-disappearance qualification: 2026-11-01

A disposable changing-controls WinForms application reproduced the installed
adapter's unavailable-element error. The corrected checkout retained observed
controls and marked incomplete scans explicitly, without swallowing unrelated
provider errors or accepting stale action references. All six checkout
observations succeeded, including three partial scans; the installed baseline
failed two of its six observations. These are bounded functional probes, not a
reliability benchmark. Fake-provider regressions exercise the production
observation boundary without desktop APIs, and the full native suite passed.
The installed copy remains unchanged until a later refresh applies the fix.

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

1. Expand changing-application coverage to other UIA providers; partial scans
   must remain distinguishable from complete trees and successful mutations.
2. Use a fresh disposable Windows VM to exercise installer, package, reboot
   and recovery paths; retain individual failures and retries in private evidence.
3. Separately exercise native desktop/streaming hardware and any project-owned
   build, export and manual QA cases that matter to the intended use.
4. Qualify WSL setup and the selected Windows/WSL integration, then real runner
   registration and jobs when an authorized test repository is available.
