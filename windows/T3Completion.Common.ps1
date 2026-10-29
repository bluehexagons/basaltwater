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
    Start-Process -FilePath $Desktop -WorkingDirectory $env:USERPROFILE -ErrorAction Stop | Out-Null
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
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    try {
        $action = New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
            -Argument ('-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' + $encoded)
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
        try { Queue-NativeT3Completion $Source }
        catch { Write-Warning 'T3 Code could not be queued for start/restart. Close and reopen it manually to load the updated PATH and skills.' }
    }
}
