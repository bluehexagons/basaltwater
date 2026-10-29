<# Native Windows restart requests and one-time recovery; no work on import. #>
function Get-NativeWindowsBoot {
    return (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime.ToUniversalTime().Ticks.ToString()
}

function Get-NativeWindowsRestartPath {
    return Join-Path $env:LOCALAPPDATA 'Basaltwater\windows-restart.json'
}

function Get-NativeWindowsRestartTask([ValidateSet('Restart', 'Resume')][string]$Mode) {
    return 'Basaltwater-Windows-' + $Mode + '-' + (Get-NativeCompletionOwner).User.Value
}

function Write-NativeWindowsRestartRequest($Request) {
    $Request.UpdatedUtc = [datetime]::UtcNow.ToString('o')
    $path = Get-NativeWindowsRestartPath
    Assert-NativeSkillPath $path
    $temporary = $path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    try {
        $Request | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporary -Encoding UTF8
        Move-Item -LiteralPath $temporary -Destination $path -Force
    } finally { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue }
}

function Read-NativeWindowsRestartRequest([string]$Id) {
    if ($Id -notmatch '\A[0-9a-f]{32}\z') { throw 'Invalid Windows restart request identity' }
    $path = Get-NativeWindowsRestartPath
    Assert-NativeSkillPath $path
    $request = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    $statePath = Join-Path $env:LOCALAPPDATA 'Basaltwater\windows-setup.json'
    Assert-NativeSkillPath $statePath
    $saved = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $sid = (Get-NativeCompletionOwner).User.Value
    if ($request.Version -ne 2 -or $request.Id -ne $Id -or $request.OwnerSid -ne $sid -or
        $request.Profile -ne 'agent_windows' -or $request.Revision -notmatch '\A[0-9a-f]{40}\z' -or
        $request.Source -notmatch '\A[A-Za-z]:[\\/]' -or $request.Boot -notmatch '\A[0-9]+\z' -or
        $saved.Version -ne 1 -or $saved.OwnerSid -ne $sid -or $saved.Profile -ne $request.Profile -or
        $saved.Revision -ne $request.Revision -or $saved.Source -ne $request.Source -or
        $saved.Phase -notin @('complete', 'restart-required')) {
        throw 'Windows restart request no longer matches this account and saved setup'
    }
    Assert-NativeSkillPath (Join-Path $request.Source 'install.ps1')
    if (-not (Test-Path -LiteralPath (Join-Path $request.Source 'install.ps1') -PathType Leaf)) {
        throw 'Windows restart recovery source is missing'
    }
    return $request
}

function Test-NativeWindowsRestartIdentity($Request) {
    $path = Get-NativeWindowsRestartPath
    Assert-NativeSkillPath $path
    # Recovery's installer clears the old request. A newer run may replace it
    # while recovery has released the lock; never overwrite that newer work.
    if (-not (Test-Path -LiteralPath $path)) { return $true }
    return (Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json).Id -eq $Request.Id
}

function Set-NativeWindowsRestartTask($Request, [ValidateSet('Restart', 'Resume')][string]$Mode) {
    $worker = Join-Path $Request.Source 'windows\Restart-WindowsSetup.ps1'
    Assert-NativeSkillPath $worker
    if (-not (Test-Path -LiteralPath $worker -PathType Leaf)) { throw 'Windows restart worker is missing' }
    $command = "& '" + $worker.Replace("'", "''") + "' -Mode $Mode -Id '" + $Request.Id + "'"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $action = New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
        -Argument ('-NoLogo -NoProfile -NonInteractive -STA -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand ' + $encoded)
    $principal = New-ScheduledTaskPrincipal -UserId $Request.OwnerSid -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([timespan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $options = @{ TaskName = (Get-NativeWindowsRestartTask $Mode); TaskPath = '\'; Action = $action;
        Principal = $principal; Settings = $settings; Force = $true }
    if ($Mode -eq 'Resume') { $options.Trigger = New-ScheduledTaskTrigger -AtLogOn -User $Request.OwnerSid }
    Register-ScheduledTask @options | Out-Null
}

function Remove-NativeWindowsRestartTasks {
    foreach ($mode in @('Restart', 'Resume')) {
        Unregister-ScheduledTask -TaskName (Get-NativeWindowsRestartTask $mode) -TaskPath '\' -Confirm:$false -ErrorAction SilentlyContinue
    }
}

function Clear-NativeWindowsRestart {
    $path = Get-NativeWindowsRestartPath
    Assert-NativeSkillPath $path
    if (Test-Path -LiteralPath $path) {
        Remove-NativeWindowsRestartTasks
        Remove-Item -LiteralPath $path -Force
    }
}

function Invoke-NativeWindowsRestart($State, [switch]$Required, [switch]$NoReboot, [switch]$NoRestartT3,
                                    [string]$InstallerReason = '') {
    $status = Get-NativeRebootStatus
    Show-NativeRebootAdvisories $status
    if (-not $Required -and -not $status.Required) {
        if ($status.Unknown) { Write-Warning 'Windows restart status could not be fully checked. Check Windows Update for a restart request.' }
        else { Write-Host 'No required Windows restart was detected; Windows will stay running.' }
        return $false
    }
    $reasons = @($status.Reasons)
    if ($Required) {
        if (-not $InstallerReason) { $InstallerReason = 'installer explicitly requires a Windows restart' }
        $reasons += $InstallerReason
    }
    Write-Host ('Windows restart required: ' + ($reasons -join '; '))
    if ($NoReboot) {
        Write-Warning 'Windows requires a restart. Automatic reboot is disabled for this run; restart Windows and rerun setup if it was interrupted.'
        return $false
    }
    if ($State.Profile -ne 'agent_windows' -or $State.OwnerSid -ne (Get-NativeCompletionOwner).User.Value) {
        throw "Automatic restart requires this account's saved native Windows setup"
    }
    $request = [pscustomobject]@{ Version = 2; Id = [guid]::NewGuid().ToString('N');
        OwnerSid = $State.OwnerSid; Profile = $State.Profile; Revision = $State.Revision; Source = $State.Source;
        Boot = Get-NativeWindowsBoot; Resume = ($State.Phase -ne 'complete'); NoRestartT3 = [bool]$NoRestartT3;
        Status = 'queued'; InstallerRequired = [bool]$Required; Reasons = $reasons;
        QueuedUtc = [datetime]::UtcNow.ToString('o'); UpdatedUtc = $null; RestartAtUtc = $null }
    Write-NativeWindowsRestartRequest $request
    try {
        # Replace prior work while the caller owns setup.lock. The worker checks
        # the request identity again after taking that same lock.
        Remove-NativeWindowsRestartTasks
        if ($request.Resume) { Set-NativeWindowsRestartTask $request Resume }
        Set-NativeWindowsRestartTask $request Restart
        Start-ScheduledTask -TaskName (Get-NativeWindowsRestartTask Restart) -TaskPath '\'
    } catch {
        $request.Status = 'manual-restart-required'
        Write-NativeWindowsRestartRequest $request
        Remove-NativeWindowsRestartTasks
        throw
    }
    Write-Warning 'Windows restart queued after setup exits. A desktop dialog will show the reason and a 60-second countdown, with Restart now and Later buttons. Save your work. Apps will not be forced closed.'
    if ($request.Resume) { Write-Host 'Setup will resume once after the next Windows boot and account login.' }
    Write-Host ('Restart status: ' + (Get-NativeWindowsRestartPath))
    return $true
}

function Show-NativeWindowsRestartPrompt($Request) {
    if (-not ('Basaltwater.WindowsRestart.Dialog' -as [type])) {
        Add-Type -Path (Join-Path $PSScriptRoot 'WindowsRestart.Dialog.cs') `
            -ReferencedAssemblies System.dll, System.Drawing.dll, System.Windows.Forms.dll
    }
    return [Basaltwater.WindowsRestart.Dialog]::Show(($Request.Reasons -join '; '),
        [datetime]::Parse($Request.RestartAtUtc).ToUniversalTime())
}

function Test-NativeWindowsRestartStillRequired($Request) {
    # Explicit installer exit codes remain authoritative until the next boot.
    if ($Request.InstallerRequired -and $Request.Boot -eq (Get-NativeWindowsBoot)) { return $true }
    return (Get-NativeRebootStatus).Required
}

function Request-NativeWindowsRestart([string[]]$Reasons = @()) {
    # A positive shutdown.exe timeout implies /f. Delay in our worker instead,
    # then use /t 0 without /f so apps can block shutdown to protect unsaved work.
    # Preserve the trigger in Windows' shutdown event, even after a later setup
    # replaces the per-account request file. Never include vendor stderr/args.
    $comment = 'Basaltwater setup requires a Windows restart.'
    if ($Reasons.Count) { $comment += ' Reason: ' + ($Reasons -join '; ') }
    if ($comment.Length -gt 500) { $comment = $comment.Substring(0, 500) }
    & (Join-Path $env:SystemRoot 'System32\shutdown.exe') /r /t 0 /d p:4:2 /c $comment
    if ($LASTEXITCODE -ne 0) { throw "Windows restart request failed (exit code $LASTEXITCODE)" }
}

function Invoke-NativeWindowsRestartWorker([ValidateSet('Restart', 'Resume')][string]$Mode, [string]$Id) {
    $setupLock = $null
    $request = $null
    $restartTaskRemoved = $false
    try {
        $setupLock = Wait-NativeSetupCompletion (Join-Path $env:LOCALAPPDATA 'Basaltwater\setup.lock')
        $request = Read-NativeWindowsRestartRequest $Id
        if ($Mode -eq 'Restart') {
            if ($request.Status -ne 'queued') { return }
            if ($request.Boot -ne (Get-NativeWindowsBoot)) { return }
            if (Get-Command Assert-NativeDevelopmentIdle -ErrorAction SilentlyContinue) { Assert-NativeDevelopmentIdle }
            if (-not (Test-NativeWindowsRestartStillRequired $request)) {
                $request.Status = 'no-longer-required'
                Write-NativeWindowsRestartRequest $request
                return
            }
            $request.Status = 'countdown'
            $request.RestartAtUtc = [datetime]::UtcNow.AddSeconds(60).ToString('o')
            Write-NativeWindowsRestartRequest $request
            $restart = Show-NativeWindowsRestartPrompt $request
            $request = Read-NativeWindowsRestartRequest $Id
            if (-not $restart) {
                $request.Status = 'restart-deferred'
                Write-NativeWindowsRestartRequest $request
                return
            }
            if ($request.Boot -ne (Get-NativeWindowsBoot) -or -not (Test-NativeWindowsRestartStillRequired $request)) {
                $request.Status = 'no-longer-required'
                Write-NativeWindowsRestartRequest $request
                return
            }
            $request.Status = 'restart-requested'
            Write-NativeWindowsRestartRequest $request
            # Shutdown can terminate us before finally. Remove only our task,
            # while setup.lock still excludes registration of its replacement.
            Unregister-ScheduledTask -TaskName (Get-NativeWindowsRestartTask Restart) -TaskPath '\' -Confirm:$false -ErrorAction SilentlyContinue
            $restartTaskRemoved = $true
            Request-NativeWindowsRestart -Reasons $request.Reasons
        } else {
            if (-not $request.Resume -or $request.Status -in @('resuming', 'resumed')) { return }
            # Logging off/on without a reboot must not consume recovery.
            if ($request.Boot -eq (Get-NativeWindowsBoot)) { return }
            Unregister-ScheduledTask -TaskName (Get-NativeWindowsRestartTask Resume) -TaskPath '\' -Confirm:$false -ErrorAction Stop
            $request.Status = 'resuming'
            Write-NativeWindowsRestartRequest $request
            $setupLock.Dispose(); $setupLock = $null
            # Bound automatic recovery to one boot. A second required restart
            # remains visible for a deliberate setup rerun, preventing loops.
            & (Join-Path $request.Source 'install.ps1') -Profile agent_windows -Resume -NoUpdateSource `
                -NoReboot -NoRestartT3:$request.NoRestartT3
            if (-not $?) { throw 'Setup recovery did not complete' }
            $setupLock = Wait-NativeSetupCompletion (Join-Path $env:LOCALAPPDATA 'Basaltwater\setup.lock')
            if (-not (Test-NativeWindowsRestartIdentity $request)) { return }
            $request.Status = 'resumed'
            Write-NativeWindowsRestartRequest $request
        }
    } catch {
        if ($request) {
            if (-not $setupLock) { $setupLock = Wait-NativeSetupCompletion (Join-Path $env:LOCALAPPDATA 'Basaltwater\setup.lock') }
            if (Test-NativeWindowsRestartIdentity $request) {
                $request.Status = 'manual-restart-required'
                Write-NativeWindowsRestartRequest $request
            }
        }
        Write-Warning 'Windows restart/recovery could not complete. Inspect the saved setup and rerun the standard installer.'
    } finally {
        try {
            if ($Mode -eq 'Restart' -and $request -and $setupLock -and -not $restartTaskRemoved -and
                (Test-NativeWindowsRestartIdentity $request)) {
                Unregister-ScheduledTask -TaskName (Get-NativeWindowsRestartTask Restart) -TaskPath '\' -Confirm:$false -ErrorAction SilentlyContinue
            }
        } finally { if ($setupLock) { $setupLock.Dispose() } }
    }
}
