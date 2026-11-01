# Local Windows qualification

Use this procedure on the Windows account that owns the native workstation.
Windows testing can remain local; GitHub Actions is not a prerequisite. Keep
Basaltwater host checks separate from project-owned builds and tests.

Copy the [report template](../examples/windows-qualification-report.md) into
private local evidence for each run. Record the exact source revisions, tool
versions, commands, exit codes and unresolved checks. Use `Pass`, `Fail`,
`Blocked` or `Not run`; identify whether evidence came from a command, direct
observation or a user report. A successful reboot reported by the operator is
useful evidence, without implying that interruption recovery was exercised.

## Record the host and Basaltwater source

Run from a reviewed Basaltwater checkout in PowerShell. These commands create
an evidence directory and read selected host/state fields; they do not apply
setup. The source checkout and saved installation can be different revisions.

```powershell
$evidence = Join-Path $env:LOCALAPPDATA ('Basaltwater\qualification\' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $evidence -Force
$revision = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Run from the Basaltwater checkout' }
$changes = @(git status --porcelain)
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the Basaltwater worktree' }
$state = Get-Content "$env:LOCALAPPDATA\Basaltwater\windows-setup.json" -Raw | ConvertFrom-Json
$windows = Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber, OSArchitecture
$graphics = @(Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion)
[ordered]@{
    CheckedUtc = [DateTime]::UtcNow.ToString('o')
    SourceRevision = $revision
    SourceModified = $changes.Count -ne 0
    SavedRevision = $state.Revision
    SavedPhase = $state.Phase
    PowerShell = $PSVersionTable.PSVersion.ToString()
    Windows = $windows
    Graphics = $graphics
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $evidence 'host.json') -Encoding UTF8
```

Keep raw logs and JSON private: executable paths can identify the account,
and project diagnostics can contain local paths or configuration. Publish only
a reviewed summary. Do not include credentials, environment dumps or personal
hostnames in a committed qualification record.

## Check host health and offline behavior

Use the current checkout's doctor before applying a source update. It reads
the account's saved selections and probes their tools. Capture its exit status
even when a report was produced:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\Test-AgentWorkstation.ps1 -Json |
    Set-Content -LiteralPath (Join-Path $evidence 'doctor.json') -Encoding UTF8
$doctorExit = $LASTEXITCODE
Set-Content -LiteralPath (Join-Path $evidence 'doctor-exit.txt') -Value $doctorExit
```

An installed current CLI also supports `basaltw doctor --json`. A healthy
report establishes selected executable health and SDK/desktop discovery, not
provider login or project correctness. See the [doctor scope](windows.md#verify-this-account).

Run the complete offline suite after Basaltwater setup/diagnostic changes.
It uses temporary state and mocked installers and requires Python 3.11+:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\tests\Run-NativeTests.ps1 *> (Join-Path $evidence 'native-tests.log')
$nativeExit = $LASTEXITCODE
Set-Content -LiteralPath (Join-Path $evidence 'native-tests-exit.txt') -Value $nativeExit
```

Inspect every nonzero result before rerunning. Preserve failure logs and use
the relevant focused test entry point for diagnosis. A successful retry does
not erase the earlier failure. Documentation-only changes need link/content
review; they do not require rebuilding every workload.

## Check real workloads

Run commands from each project's root at a recorded commit and inspect its
worktree first. Use the project-owned pins and Windows instructions; these
checks must remain usable without Basaltwater.

| Workload | Required local evidence |
| --- | --- |
| Native software/game | Select the project's runtime/toolchain pins, use its supported developer shell, and run locked installation, doctor, build and source/native tests. Record generators, budgets, failures and explicit skips. |
| Packages and exports | Use the project's package/export commands and matching templates. Test a fresh extraction outside the checkout with isolated data and an intentional PATH. Inspect actual startup and asset diagnostics; a launcher exit alone is insufficient. |
| Interactive behavior | Use the project's manual QA checklist for rendering, input, audio, resizing/scaling and persistence. Automated startup or GPU fixtures do not establish these results. |

See [Windows game tooling](windows-game-development.md#native-project-qualification)
for host preparation. Optional [Antistatic](../integrations/antistatic/windows.md)
and [snake2d](../integrations/snake2d.md) examples describe their own commands;
neither is a prerequisite for qualifying another project. Tests that reset
settings or scores must use disposable data or backed-up saves.

## Lifecycle and fresh-install boundaries

After an operator-authorized reboot, start a new terminal and T3 session,
check tool discovery, and repeat the relevant workload. Record reboot/login
success separately from setup recovery after an interrupted package install.

Use mocked tests for unavailable drives, unsafe paths and interruption cases
on an active workstation. Live interruption, WinGet repair and missing-package
installation belong on a disposable system. A fresh account on an existing
machine tests account setup while retaining machine-wide packages; a clean VM
is needed to establish their installation from scratch. Leave untested cases
open rather than inferring them from reconciliation on a configured PC.

## Recorded local run: 2026-10-31

Historical native Windows validation covered Windows PowerShell 5.1 and
PowerShell 7. The combined workstation implementation was tested at
`82d5313369abed89f16a1b8a4d559e0a763a8e00`; later launcher and task-log/wait
corrections were tested in their owning increments. This summary describes
past coverage, not the state of any current installation.

- The complete offline native suite and documentation checkers passed.
  Process fixtures covered success/failure, cancellation, deadlines, guardian
  loss, both-stream floods and cleanup uncertainty. Owned descendants terminated
  while an unrelated fixture survived. Installers, notifications and sleep
  remained mocked; POSIX-only Python cases were explicitly skipped.
- Skill delivery/freshness checks exercised guarded completion, legacy unknown
  state and preservation of personal resources. Installed bytes and session-loaded
  instructions remain separate concepts.
- Native launcher regressions preserved literal/empty/Unicode arguments and
  percent text, failure exit codes, compilation rollback and replacement while
  an old launcher was executing. Direct API fixtures alone had missed the batch
  quoting boundary.
- Real limited interactive Scheduled Tasks retained arguments and bounded logs,
  settled cancellation cleanup, and completed after the submitting CLI exited.
  Prefix-plus-tail retention preserved end markers after a stream exceeded its
  cap. A bounded caller wait timed out without cancelling the task, then reported
  its later terminal failure. These checks qualified the current-login-session
  Scheduler path, not logoff or reconnect during a running job.

Detailed implementation scope is in the
[Windows workstation feedback record](../plans/workstation-feedback-windows.md#windows-completion-record-2026-10-31).
Raw commands, account paths, installation inventories and machine troubleshooting
history remain private. Project-specific results belong in their own QA records.

Physical display/GPU, streaming, audible audio/input, lock/reconnect during active
work, fresh installation and live installer/reboot interruption remain separate
qualification. Host reconciliation and an operator-reported reboot do not
establish these cases.
