# Windows desktop and development integration

The native `agent_windows` setup installs stable PowerShell 7 when missing and
continues the pinned installation in it before applying development tools.
Windows PowerShell 5.1 remains the initial bootstrap and the bounded UI Automation
worker. Setup preserves switches, arrays and literal paths across the handoff.
`-Plan` neither installs PowerShell nor starts a replacement shell.

The same one-command installer and saved `basaltw refresh` selections supply
these features. No separate desktop installer is required. See
[Windows setup](windows.md) for installation and recovery.

## Choose a topic

<a id="terminal-and-artifact-handoff"></a>
<a id="supervised-project-tasks"></a>
<a id="optional-ninja-analysis-build"></a>

- [Windows desktop project work](windows-desktop-project-work.md) Run commands, hand off artifacts, and perform supervised project tasks.

<a id="observe-a-native-application"></a>
<a id="act-on-observed-controls"></a>

- [Observe and control Windows applications](windows-desktop-native-controls.md) Inspect native application state and act on observed controls within the desktop session.
- [Maintenance and qualification](#maintenance-and-qualification) Refresh the installation and review verification requirements.

## Maintenance and qualification

`basaltw refresh` submits a durable background job and follows its live output
by default, allowing T3 upgrades to close/restart the desktop without killing
setup. `--detach` returns its ID without following; Ctrl+C stops following
without cancelling setup. Use `refresh --status ID` and `refresh --logs ID` for
progress, or `--follow ID` to reconnect. `--wait` runs setup in the invoking terminal.
`refresh --cancel ID` can cancel a queued job before setup starts; an applying
package installer must finish before another attempt.
Status reports `cleanup-uncertain` when a running job loses its worker in the
current boot. An installer may have survived, so inspect its logs and reboot
before another detached refresh. After reboot, the next refresh marks abandoned
queued/running jobs `interrupted` while holding their claim and setup locks.
It starts a new job without replaying the abandoned one; `--status` returns
nonzero for failed, interrupted or uncertain jobs.
`--dry-run` remains read-only and foreground. Refresh logs are capped at 16 MiB.
Active or uncertain development tasks block setup/reboot; wait for them or cancel
and confirm cleanup. Explicit installer, Windows Update or component servicing
requirements queue a visible desktop dialog showing the reason and a 60-second
countdown with Restart now and Later buttons. Pending file operations alone do
not trigger reboot. `--no-reboot` defers reboot for this run; see
[restart conditions and status](windows.md) for details.

The workstation doctor checks stable PowerShell 7 and, when T3 is selected,
desktop readiness. After a Windows logon change, doctor can check the current
desktop without rewriting saved state. A successful inventory records the
current session and invalidates the previous session's window references and
capture receipts.
Neither installed GPU drivers, an accessible desktop nor a
healthy Sunshine service proves rendering, audio, encoder or streaming readiness.
Qualify actual Moonlight video/audio/input, display/scaling changes, lock and
reconnect behavior using the [Sunshine guide](windows-sunshine.md).
Use the [Windows qualification procedure](windows-qualification.md) for recorded
workload evidence. Offline contracts and owned-process tests do not establish
unattended GUI access, UAC automation or hardware-rendered capture support.

### November 2026 qualification record

The 2026-11-02 CachyOS feedback pass initially included Windows feedback in error.
Windows follow-up is owned by a separate workstream. Commit `d6f3f191` already
added scan-code keys/chords and offline test cases, but those cases and live
SDL/WinForms delivery were not run on CachyOS. The Windows owner should review
and qualify that change; it is not a native Windows pass or an installed refresh.
The task/Ninja guidance from `ae5fe136` also awaits native recipe validation.

The subsequent Windows review reconciled those changes with bounded input
sequences, target-thread layout mapping and partial-delivery cleanup. The full
native Windows offline suite and focused desktop/CLI contracts passed on
2026-11-02, along with both documentation checkers. Live SDL/WinForms delivery,
frame-polled game input and the Ninja recipe remain unqualified; no refresh was
performed by that review. See the [game-testing plan](../plans/windows-game-testing.md)
for the remaining facilities and acceptance checks.
