# Windows and WSL testing status

**Evidence includes an owner report received 2026-11-01 and earlier checks
recorded through 2026-10-31.** Native Windows and Ubuntu WSL are
separate environments: a pass on one does not establish the other. See the
[status legend](README.md#status-legend) and
[Windows qualification procedure](../workstations/windows-qualification.md).

## Native Windows 11

The experimental `agent_windows` profile prepares an existing x86-64 account
for coding and native tooling. It does not install the Linux controller, managed
web services, privilege broker or remote setup profiles. See the
[native workstation guide](../workstations/windows.md).

| Available feature | Status | Evidence | Next qualification boundary |
| --- | --- | --- | --- |
| Native workstation setup | 🟡 **Partial** | Owner reports basic non-WSL setup with Godot and T3 Code; earlier offline installers were mocked | Exact command/revision and separate feature checks not supplied; fresh account/machine, WinGet/UAC and live install recovery remain open |
| Native Godot and T3 Code setup | 🟡 **Partial** | Included in the owner's basic setup report | Launch, project export, pairing/provider use, reboot and recovery were not separately reported |
| Skills and native launcher | 🟡 **Partial** | Delivery/freshness and argument, exit and rollback fixtures | Fresh installation and active-session instruction reload |
| Process supervision | 🟡 **Partial** | Native process fixtures cover failures, timeouts, cancellation and child cleanup | Logoff, reboot and additional interruption boundaries |
| Scheduled development tasks | 🟢 **Labbed** | Real limited interactive tasks: arguments, logs, cancellation and completion after CLI exit | Logoff/reconnect and interruption during active work |
| Desktop and physical input | ⚪ **Untested** | No complete physical interaction pass recorded in the native run | Display/GPU, audio/input, lock and reconnect |
| Sunshine streaming | 🟡 **Partial** | Offline package/service fixtures | Live install, firewall, boot and Moonlight streaming |
| Windows/WSL GitHub runner | 🟡 **Partial** | Offline native runner tests; live Windows 11/WSL qualification open | Registration, real jobs, cancellation and reboot |
| Game tools and project exports | ⚪ **Untested** | Godot setup reported; no project QA result supplied | Native build, package/export, rendering, audio and input |
| Managed native build host | ⚪ **Planning only** | Proposed backend; distinct from the existing runner | Implementation, isolation, transport and native workload qualification |

The 2026-10-31 [native record](../workstations/windows-qualification.md#recorded-local-run-2026-10-31)
passed the offline suite on Windows PowerShell 5.1 and PowerShell 7; installers,
notifications and sleep were mocked, and POSIX-only cases were skipped.
Later selected task/launcher checks are described there. This is historical
evidence for the stated cases, not a fresh installation or release pass.

The new [owner report](evidence.md#owner-reported-stack-setup-rerun) indicates
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
