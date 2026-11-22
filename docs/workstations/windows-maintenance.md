# Maintain and recover the Windows profile

[Topic index](windows.md)

## Pinned source and recovery

The [download installer](windows-install.md#install) resolves `main` to one commit, stages its source under
`%LOCALAPPDATA%\Basaltwater\source`, and
saves the account, selected tools, and revision in `windows-setup.json`,
separately from WSL state. The download-only preview summarizes selections;
the [checkout preview](windows-tool-selection.md#review-selections-from-a-checkout)
also discovers installed tools and skill collisions.
Source extraction handles paths longer than 260 characters without changing
Windows policy and rejects archive paths that escape the staging directory.
Use `-Version REF` to select a published tag, branch, or commit supported by
the installer. Both `-Channel main` and `-Channel dev` currently resolve `main`;
an explicit `-Version` overrides the channel. A reference predating the native
profile cannot supply it.

Fresh installs and resumes share an account-level setup lock with WSL setup.
If another setup is running, wait for it to exit before retrying. `-Plan` remains
read-only and can run while that lock is held. An updated source is saved as
`staged` before execution; it becomes `complete` only after setup succeeds.

For interruption, a package requiring restart, or later reconciliation, rerun
the download command or use `basaltw refresh`. To retry the saved Basaltwater
revision without fetching newer source, run:

```powershell
$state = Get-Content "$env:LOCALAPPDATA\Basaltwater\windows-setup.json" -Raw -Encoding UTF8 | ConvertFrom-Json
powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $state.Source 'install.ps1') -Profile agent_windows -NoUpdateSource
```

Native Windows reruns reuse saved tool selections and update source by default.
`-Resume` remains available to require an existing saved installation.
Explicit runtime, game-tool and desktop
switches update those selections; `-AgentTool` adds providers to the saved list.
For example, append `-Go -AgentTool claude` to add Go and Claude while
retaining existing providers. Add `-Plan` to preview the merged selections
without writes. Selected bundles continue to supply their dependencies;
selected host tools receive available updates and provider logins are retained.
The downloaded and checkout entry points share the same pinned-installer state.

## Native basaltw commands

Setup adds `%LOCALAPPDATA%\Basaltwater\bin` to the account PATH. Its native
`basaltw.exe` launcher works from PowerShell and Command Prompt without installing
Python or changing the account execution policy. Setup compiles this small shim
using Windows' included .NET Framework/PowerShell 5.1. Arguments cross a private
stdin pipe into the existing PowerShell CLI; no argument files are written.
CLI commands do not consume user stdin. Compilation finishes before atomic
launcher replacement, including when refresh's launcher is still running.
Open a new terminal after first setup.

```powershell
basaltw --help
basaltw --version
basaltw info --json
basaltw doctor
basaltw doctor --json
basaltw doctor --skills --json
basaltw storage
basaltw refresh --dry-run
basaltw refresh
basaltw refresh --godot
basaltw refresh --sunshine
```

`refresh` submits a limited current-account job, resolves current `main`
and reapplies the saved native selections,
including providers, game tools and storage. It installs available updates for
selected host tools and preserves provider logins.
Its job survives the invoking T3 desktop closing during an upgrade. By default,
the terminal follows live output and returns the completed job's exit status.
The CLI shows status, elapsed time and setup output, then prints a completion
or failure summary and the retained log path. Pressing Ctrl+C stops following
and leaves setup running. Long steps can be quiet while their installer runs; the
terminal prints a status heartbeat every 15 seconds. No completion percentage
is estimated. Review setup output for separate T3 restart/reboot notices.
The job ID supports `refresh --status ID`, `--logs ID`, `--follow ID`, and
cancellation while queued with `--cancel ID`. Use `--detach` to return
immediately after submission, or `--wait`
to run setup directly in the terminal without an independent worker.
If an update closes T3 and its terminal, reconnect from a new PowerShell terminal
with `--follow ID` to see output and the result.
Logs retain up to 16 MiB; truncation is reported. Submission success alone does
not establish completion.
`--dry-run` previews the currently pinned source without downloads or writes.
`--channel dev` is currently an alias for `main`; use `--version REF` for a
specific source reference.
`--no-update-source` reapplies the saved Basaltwater revision while still updating
selected tools. `--no-restart-t3` skips the completion start/restart but retains
login startup. `--no-reboot` defers required Windows reboots. These options are
not saved for subsequent runs. A source-update
opt-out cannot be combined with an explicit source version.
`--game-dev`, `--shader-tools`, `--godot`, `--sunshine`, `--node-versions` and
`--storage-drive F:` (or `--storage-root PATH`) can add saved selections;
run `basaltw refresh --help`. Storage selection requires a healthy mounted
NTFS volume. Invalid selections are rejected before changing saved setup.
Trailing directory separators are normalized before forwarding storage paths
through Windows PowerShell's native command boundary.
The CLI reports setup failures through its exit code, including restart needs.

`info` reports only the saved local native installation. `desktop` supplies the
native Windows application/task interface. Linux controller, server and agent
subcommands remain unavailable on Windows.
`refresh`/`info`/`doctor` require the pinned installation created by
`install.ps1 -Profile agent_windows -AgentTool PROVIDER`. Existing
PCs without the launcher can use the same current installer download with
`-Profile agent_windows`; existing selections are retained automatically.

Use the prepared PowerShell 7 shell for literal arguments, including embedded
quotes, empty strings, Unicode and `%NAME%` text. The native executable avoids
batch parsing and preserves these values into task planning. The retained explicit
`basaltw.cmd` fallback and Windows PowerShell 5.1's native argument binder have
legacy quoting/expansion limitations. On an older installation, invoke the
PowerShell script directly from PowerShell 7 to keep values literal:

```powershell
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Basaltwater\bin\Invoke-Basaltw.ps1" refresh --storage-root 'F:\cache %NAME%' --dry-run
```

The direct installer/checkout scripts also accept literal paths through
`-StorageRoot`. For Godot project paths use its real console executable,
available as `BASALTWATER_GODOT_EXE` for Basaltwater's managed launcher.

## Low-space recovery

`basaltw storage` measures the Windows system volume and previews cleanup only
when it has less than 5 GiB available. Use an administrator PowerShell terminal
to apply it, or choose a larger target for a planned update:

```powershell
basaltw storage --json
basaltw storage --apply
basaltw storage --apply --target-gib 10 --json
```

The command first clears unpinned Delivery Optimization download cache through
the [native cache cmdlet](https://learn.microsoft.com/en-us/powershell/module/deliveryoptimization/delete-deliveryoptimizationcache),
then runs [Windows component cleanup](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/clean-up-the-winsxs-folder)
with `Repair-WindowsImage -Online -StartComponentCleanup -NoRestart` if more
space is needed. Component cleanup removes superseded components immediately,
without the scheduled task's usual 30-day grace period. It never uses
`ResetBase`. Pending Windows Update/servicing reboots defer component cleanup;
reboot normally and retry. The command reports reboot requirements without
restarting Windows. Servicing can take several minutes; leave the terminal open.

When the system disk is short on temporary workspace, prepare an empty directory
on another local healthy NTFS volume and select it for
[servicing scratch files](https://learn.microsoft.com/en-us/powershell/module/dism/repair-windowsimage#-scratchdirectory):

```powershell
basaltw storage --apply --scratch-dir 'F:\servicing-scratch'
```

The directory must already exist and be empty. Linked directories and network
paths are rejected. The command validates it before cleanup and again before
servicing. This moves servicing workspace; Windows still needs enough space on
the system volume to finish the operation.

Capacity is rechecked between operations. Results include before/after free
space, net space change, individual failures and an unmet target. Concurrent
disk use can affect that net change; it is not a reclaimable-size estimate.
Preview requires no elevation and runs no cleanup commands. Apply exits nonzero
for a native failure or an unmet target. Missing native modules are reported
as failures while remaining operations are still attempted.

This is an explicit command, without a scheduled job or setup retry. It targets
the system volume; development storage on another drive needs separate review.
Repositories, builds, provider history, Downloads, the Recycle Bin, WSL disks,
Windows Installer caches and restore points remain outside this policy. Use
Windows Storage settings to review other categories and their retention.
Offline policy tests mock all Windows observations and native cleanup:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\tests\Run-StorageTests.ps1
```

The complete native suite includes these tests. Live cache reclamation and
component servicing on Windows still require native qualification.
