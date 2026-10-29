<# Successful setup completion, independent of a T3-owned terminal process tree. #>
function Get-NativeCompletionOwner {
    return [Security.Principal.WindowsIdentity]::GetCurrent()
}

function Get-NativeWindowsUpdateReboot {
    $information = New-Object -ComObject Microsoft.Update.SystemInfo
    try { return [bool]$information.RebootRequired }
    finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($information) }
}

function Get-NativeRebootStatus {
    $reasons = @()
    $unknown = $false
    try { if (Get-NativeWindowsUpdateReboot) { $reasons += 'Windows Update' } }
    catch { $unknown = $true }
    foreach ($signal in @(
        @{ Path = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'; Label = 'component servicing' },
        @{ Path = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'; Label = 'Windows Update registry' }
    )) {
        try {
            if (Test-Path -LiteralPath $signal.Path -ErrorAction Stop) { $reasons += $signal.Label }
        } catch { $unknown = $true }
    }
    try {
        $session = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -ErrorAction Stop
        if ($session.PSObject.Properties['PendingFileRenameOperations'] -and
            @($session.PendingFileRenameOperations | Where-Object { $_ }).Count) {
            $reasons += 'pending file replacements'
        }
    } catch { $unknown = $true }
    return [pscustomobject]@{ Required = ($reasons.Count -gt 0); Unknown = $unknown; Reasons = $reasons }
}

function Show-NativeRebootNotice {
    $status = Get-NativeRebootStatus
    if ($status.Required) {
        Write-Warning ('Windows reports a pending restart (' + ($status.Reasons -join ', ') + '). Save your work and restart Windows when convenient; Basaltwater will not restart it.')
    } elseif ($status.Unknown) {
        Write-Warning 'Windows restart status could not be fully checked. Check Windows Update for a restart request; Basaltwater will not restart Windows.'
    } else {
        Write-Host 'No pending Windows restart was detected by the available update, servicing and file-replacement checks.'
    }
}

function Get-NativeT3Roots([string]$Desktop, [string]$OwnerSid) {
    # Never inspect or retain command lines: they can contain provider credentials.
    $candidates = @(Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, SessionId, ExecutablePath, CreationDate -ErrorAction Stop |
        Where-Object { $_.ExecutablePath -and [string]::Equals($_.ExecutablePath, $Desktop, [StringComparison]::OrdinalIgnoreCase) })
    $owned = @(foreach ($process in $candidates) {
        $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid -ErrorAction Stop
        if ($owner.ReturnValue -ne 0) { throw 'Could not establish T3 process ownership' }
        if ($owner.Sid -eq $OwnerSid) { $process }
    })
    $ids = @($owned | ForEach-Object { $_.ProcessId })
    foreach ($process in $owned) {
        if ($process.ParentProcessId -in $ids) { continue }
        if (-not $process.CreationDate) { throw 'Could not establish T3 process identity' }
        [pscustomobject]@{ Id = [int]$process.ProcessId; Session = [int]$process.SessionId
            Created = $process.CreationDate.ToUniversalTime().Ticks.ToString() }
    }
}

function Close-NativeT3Window($Root) {
    $process = Get-Process -Id $Root.Id -ErrorAction Stop
    # CIM timestamps have microsecond precision; tolerate only that rounding.
    if ([Math]::Abs($process.StartTime.ToUniversalTime().Ticks - [long]$Root.Created) -gt 10) {
        throw 'T3 process identity changed'
    }
    if (-not $process.CloseMainWindow()) { throw 'T3 has no available graceful close window' }
    if (-not $process.WaitForExit(60000)) { throw 'T3 did not close within one minute' }
}

function Start-NativeT3Desktop([string]$Desktop) {
    # A scheduled task may inherit an old environment from Task Scheduler. Rebuild
    # PATH from the persisted account settings rather than a T3 terminal's PATH.
    $env:PATH = Merge-NativePath @([Environment]::GetEnvironmentVariable('Path', 'Machine'),
                                 [Environment]::GetEnvironmentVariable('Path', 'User'))
    . (Join-Path $PSScriptRoot 'DevelopmentStorage.Common.ps1')
    foreach ($name in @((Get-NativeStorageLayout).Keys) + @('BASALTWATER_GODOT_EXE')) {
        $value = [Environment]::GetEnvironmentVariable($name, 'User')
        if (-not $value) { $value = [Environment]::GetEnvironmentVariable($name, 'Machine') }
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
    Start-Process -FilePath $Desktop -WorkingDirectory $env:USERPROFILE -ErrorAction Stop | Out-Null
}

function Get-NativeT3TaskArguments([string]$Command) {
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Command))
    return '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand ' + $encoded
}

function New-NativeT3TaskAction([string]$Command) {
    return New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
        -Argument (Get-NativeT3TaskArguments $Command)
}

function Get-NativeT3AccountSid([string]$Account) {
    if ($Account -match '\AS-1-') { return $Account }
    return (New-Object Security.Principal.NTAccount($Account)).Translate([Security.Principal.SecurityIdentifier]).Value
}

function Get-NativeT3LoginHealth([string]$Source) {
    $owner = Get-NativeCompletionOwner
    $taskName = 'Basaltwater-T3-Login-' + $owner.User.Value
    $state = 'failed'
    try {
        $task = Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction Stop
        $worker = Join-Path ([IO.Path]::GetFullPath($Source)) 'windows\Start-T3Desktop.ps1'
        $expected = Get-NativeT3TaskArguments ("& '" + $worker.Replace("'", "''") + "'")
        $actions = @($task.Actions); $triggers = @($task.Triggers)
        if ($task.Settings.Enabled -and $actions.Count -eq 1 -and $triggers.Count -eq 1 -and
            $task.Principal.LogonType -in @('Interactive', 3) -and $task.Principal.RunLevel -in @('Limited', 0) -and
            (Get-NativeT3AccountSid $task.Principal.UserId) -eq $owner.User.Value -and
            $triggers[0].CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' -and $triggers[0].Enabled -and
            (Get-NativeT3AccountSid $triggers[0].UserId) -eq $owner.User.Value -and
            $actions[0].Execute -eq (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -and
            $actions[0].Arguments -eq $expected -and (Test-Path -LiteralPath $worker -PathType Leaf)) {
            $state = 'ok'
        }
    } catch { $state = 'failed' }
    $detail = if ($state -eq 'ok') { 'Current-account T3 login task is enabled with the expected hidden helper; the app was not launched.' }
              else { 'T3 login task is missing, unreadable, disabled or differs from setup. Reapply desktop setup to repair it.' }
    return [pscustomobject]@{ Name = 't3-login'; State = $state; Command = $taskName; Detail = $detail }
}

function Set-NativeT3Login([string]$Source) {
    $owner = Get-NativeCompletionOwner
    $worker = Join-Path ([IO.Path]::GetFullPath($Source)) 'windows\Start-T3Desktop.ps1'
    Assert-NativeSkillPath $worker
    if (-not (Test-Path -LiteralPath $worker -PathType Leaf)) { throw 'T3 login worker is missing' }
    $action = New-NativeT3TaskAction ("& '" + $worker.Replace("'", "''") + "'")
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $owner.User.Value
    $principal = New-ScheduledTaskPrincipal -UserId $owner.User.Value -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([timespan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName ('Basaltwater-T3-Login-' + $owner.User.Value) -Action $action `
        -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Write-Host 'T3 Code will start at login for this Windows account.'
}

function Invoke-NativeT3Login {
    $desktop = Get-NativeT3Desktop
    if (-not $desktop) { throw 'T3 Code desktop is unavailable; reconcile setup.' }
    $owner = Get-NativeCompletionOwner
    $session = (Get-Process -Id $PID -ErrorAction Stop).SessionId
    # Login only starts a stopped app; it never closes an existing desktop.
    if (@(Get-NativeT3Roots $desktop $owner.User.Value | Where-Object { $_.Session -eq $session }).Count) { return }
    Start-NativeT3Desktop $desktop
}

function Complete-NativeT3Desktop($Request, [int]$Session) {
    $current = @(Get-NativeT3Roots $Request.Desktop $Request.OwnerSid | Where-Object { $_.Session -eq $Session })
    if ($current.Count -gt 1) { throw 'Multiple T3 instances require manual restart' }
    if ($current.Count -eq 1) {
        $root = $current[0]
        if (-not @($Request.Roots | Where-Object { $_.Id -eq $root.Id -and $_.Created -eq $root.Created }).Count) {
            return 'already-running'
        }
        Close-NativeT3Window $root
        # The application or an updater may have relaunched it while closing.
        if (@(Get-NativeT3Roots $Request.Desktop $Request.OwnerSid | Where-Object { $_.Session -eq $Session }).Count) {
            return 'already-relaunched'
        }
        Start-NativeT3Desktop $Request.Desktop
        return 'restarted'
    }
    Start-NativeT3Desktop $Request.Desktop
    return 'started'
}

function Wait-NativeSetupCompletion([string]$LockPath) {
    Assert-NativeSkillPath $LockPath
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        try {
            # Retain ownership through relaunch: another refresh must not begin
            # between the successful receipt and the desktop closing its terminal.
            return [IO.File]::Open($LockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        } catch [IO.IOException] { Start-Sleep -Seconds 1 }
    }
    throw 'Setup is still active; leave T3 running'
}

function Write-NativeT3CompletionStatus([string]$Path, [string]$Status) {
    Assert-NativeSkillPath $Path
    @{ Version = 1; Status = $Status; UpdatedUtc = [datetime]::UtcNow.ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Invoke-NativeT3CompletionWorker([string]$RequestPath, [string]$TaskName) {
    if ($TaskName -notmatch '\ABasaltwater-T3-Complete-[0-9a-f]{32}\z') { throw 'Invalid completion task name' }
    $directory = Join-Path $env:LOCALAPPDATA 'Basaltwater\t3-completion'
    $expected = Join-Path $directory ($TaskName.Substring('Basaltwater-T3-Complete-'.Length) + '.json')
    if (-not [string]::Equals([IO.Path]::GetFullPath($RequestPath), $expected, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Invalid completion request path'
    }
    Assert-NativeSkillPath $RequestPath
    $statusPath = $RequestPath + '.status.json'
    $completionLock = $null
    $setupLock = $null
    try {
        $request = Get-Content -LiteralPath $RequestPath -Raw | ConvertFrom-Json
        $sid = (Get-NativeCompletionOwner).User.Value
        if ($request.Version -ne 1 -or $request.OwnerSid -ne $sid -or
            $request.Desktop -notmatch '\A[A-Za-z]:[\\/]' -or
            -not [string]::Equals($request.Desktop, (Get-NativeT3Desktop), [StringComparison]::OrdinalIgnoreCase)) {
            throw 'T3 installation or request owner changed'
        }
        $session = (Get-Process -Id $PID -ErrorAction Stop).SessionId
        if ($request.Session -ne $session) { throw 'Completion task ran in a different Windows login session' }
        $setupLock = Wait-NativeSetupCompletion (Join-Path $env:LOCALAPPDATA 'Basaltwater\setup.lock')
        # Allow the invoking terminal to flush its final successful output.
        Start-Sleep -Seconds 3
        $lockPath = Join-Path $directory 'completion.lock'
        Assert-NativeSkillPath $lockPath
        $completionLock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $status = Complete-NativeT3Desktop $request $session
        Write-NativeT3CompletionStatus $statusPath $status
    } catch {
        # Constant status only; raw errors and inherited arguments can be sensitive.
        Write-NativeT3CompletionStatus $statusPath 'manual-restart-required'
    } finally {
        if ($completionLock) { $completionLock.Dispose() }
        if ($setupLock) { $setupLock.Dispose() }
        Remove-Item -LiteralPath $RequestPath -Force -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
}

function Queue-NativeT3Completion([string]$Source) {
    $desktop = Get-NativeT3Desktop
    if (-not $desktop) { throw 'Selected T3 desktop is not installed' }
    $owner = Get-NativeCompletionOwner
    $roots = @(Get-NativeT3Roots $desktop $owner.User.Value)
    $directory = Join-Path $env:LOCALAPPDATA 'Basaltwater\t3-completion'
    Assert-NativeSkillPath $directory
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $id = [guid]::NewGuid().ToString('N')
    $requestPath = Join-Path $directory ($id + '.json')
    $taskName = 'Basaltwater-T3-Complete-' + $id
    $worker = Join-Path ([IO.Path]::GetFullPath($Source)) 'windows\Complete-T3Desktop.ps1'
    if (-not (Test-Path -LiteralPath $worker -PathType Leaf)) { throw 'T3 completion worker is missing' }
    $session = (Get-Process -Id $PID -ErrorAction Stop).SessionId
    @{ Version = 1; OwnerSid = $owner.User.Value; Session = $session; Desktop = $desktop; Roots = $roots } |
        ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $requestPath -Encoding UTF8
    # EncodedCommand preserves literal spaces/quotes across Windows PS 5.1 argv.
    # Only paths and a generated task name cross this boundary, never setup args.
    $command = "& '" + $worker.Replace("'", "''") + "' -RequestPath '" + $requestPath.Replace("'", "''") + "' -TaskName '" + $taskName + "'"
    try {
        $action = New-NativeT3TaskAction $command
        $principal = New-ScheduledTaskPrincipal -UserId $owner.Name -LogonType Interactive -RunLevel Limited
        # Do not let Scheduler's task timeout later terminate the desktop child.
        # The worker itself bounds its setup wait and graceful close separately.
        $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([timespan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
        Write-NativeT3CompletionStatus ($requestPath + '.status.json') 'queued'
        Start-ScheduledTask -TaskName $taskName
    } catch {
        Remove-Item -LiteralPath $requestPath -Force -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-NativeT3CompletionStatus ($requestPath + '.status.json') 'manual-restart-required'
        throw
    }
    Write-Host "T3 Code start/restart queued outside this terminal. Completion status: $requestPath.status.json"
}

function Invoke-NativeSetupCompletion([string]$Source, [switch]$T3CodeDesktop) {
    Show-NativeRebootNotice
    if ($T3CodeDesktop) {
        try { Set-NativeT3Login $Source }
        catch { Write-Warning 'T3 Code login startup could not be configured. Rerun setup to retry Task Scheduler registration.' }
        try { Queue-NativeT3Completion $Source }
        catch { Write-Warning 'T3 Code could not be queued for start/restart. Close and reopen it manually to load the updated PATH and skills.' }
    }
}
