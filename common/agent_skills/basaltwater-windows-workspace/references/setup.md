# Native Windows setup and maintenance

Use for authorized installation, refresh or recovery. Read-only doctor is the
diagnostic route; neither an unhealthy check nor this reference authorizes repair.

`basaltw refresh --dry-run` previews; `basaltw refresh` updates source from main
and reapplies saved selections, including available WinGet/npm updates. Provider
logins are preserved. The root `install.ps1 -Profile agent_windows` and the
documented downloaded entrypoint save the same selections; the one-command
invocation handles a new or existing PC. Setup supplies stable PowerShell 7 and
hands off before development-tool setup. It adds a managed Windows Terminal
profile and Start Menu development shell without replacing personal settings.

## Update lifecycle

Refresh follows live output, status, elapsed time and final summary by default.
Ctrl+C stops following, not setup; `--detach` returns after submission. The worker
survives T3 closing. Retain the job ID and inspect `refresh --status ID`,
`--logs ID`, or reconnect with `--follow ID`. `--wait` instead runs setup in the
invoking terminal; `--cancel ID` only cancels a queued job.

T3 starts or gracefully restarts after successful setup by default. The current
account's login startup uses a hidden PowerShell helper without an output console.
`-NoUpdateSource` / `refresh --no-update-source` retains saved source while still
updating tools. `-NoRestartT3` / `refresh --no-restart-t3` skips completion restart,
retaining login startup; a vendor upgrade can still close T3. Reopen other
terminals for PATH changes and inspect completion when restart was deferred/failed.
Doctor's `t3-desktop` `InstalledVersion` is file metadata, not the running version;
null means unknown. Check About separately. WinGet can lag the upstream stable feed.
`doctor --skills --json` compares installed bundles with saved installation source,
not necessarily the developer checkout. Legacy inventories have unknown completion
until setup succeeds; matching bytes alone do not establish a completed install
or which revision a running agent loaded.

## Reboot and recovery

Native setup can reboot when an installer explicitly requires it or Windows
Update/component servicing requires it. Pending renames/deletions alone are
advisory. A visible dialog gives the reason, restart time and 60-second countdown
with Restart now/Later; closing it defers. Locked/unavailable desktops or failed
notification defer reboot. Apps are not forced closed.

`-NoReboot` / `refresh --no-reboot` defers reboot for that run. Interrupted setup
continues once after real boot and account login using saved source/selections;
the continuation defers another reboot to prevent a loop. Inspect
`%LOCALAPPDATA%\Basaltwater\windows-restart.json` for reasons, timestamps and outcome.
Reconcile recovery before rerunning setup. WSL keeps its manual Windows-reboot flow.
Active/uncertain supervised tasks block maintenance until confirmed cleanup.

## Optional storage and streaming

`-StorageDrive F:` / `refresh --storage-drive F:` selects `F:\Basaltwater`.
`-StorageRoot PATH` chooses a custom directory instead; select one option. The
resolved root is saved for refresh/resume and puts Node versions and npm/uv/Go/
vcpkg caches on that mounted NTFS drive, providing `repos` for new checkouts.
An unavailable drive fails without falling back to C: or replacing selections.
Keep the drive connected and inspect saved cache variables. Existing checkout
and build trees remain in place.

`-Sunshine` / `refresh --sunshine` saves native streaming selection. Setup can
request UAC for packages/services while preserving the intended account's tools
and logins. Doctor only inspects the selected service, which starts at boot.
Pairing, GPU/display, sound and input still require a Moonlight check; see
[Windows Sunshine](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/windows-sunshine.md).
