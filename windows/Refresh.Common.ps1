<# Detached native refresh jobs. The installer owns setup locking and recovery. #>
function Get-NativeRefreshDirectory([string]$Id) {
    if ($Id -notmatch '\A[0-9a-f]{32}\z') { throw 'Refresh ID must be a 32-character job ID' }
    return Join-Path $env:LOCALAPPDATA ('Basaltwater\refresh\' + $Id)
}

function Write-NativeRefreshReceipt($Receipt) {
    $path = Join-Path (Get-NativeRefreshDirectory $Receipt.Id) 'receipt.json'
    Assert-NativeSkillPath $path
    $temporary = $path + '.tmp'
    try {
        $Receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $temporary -Encoding UTF8
        Move-Item -LiteralPath $temporary -Destination $path -Force
    } finally { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue }
}

function Read-NativeRefreshReceipt([string]$Id) {
    $path = Join-Path (Get-NativeRefreshDirectory $Id) 'receipt.json'
    Assert-NativeSkillPath $path
    if ((Get-Item -LiteralPath $path -ErrorAction Stop).Length -gt 131072) { throw 'Refresh receipt exceeds its limit' }
    $options = @{}
    if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $options.DateKind = 'String' }
    $receipt = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json @options
    if ($receipt.Version -ne 1 -or $receipt.Id -ne $Id -or
        $receipt.OwnerSid -ne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) {
        throw 'Refresh receipt does not belong to this account/job'
    }
    return $receipt
}

function Get-NativeRefreshBoot {
    return (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime.ToUniversalTime().Ticks.ToString()
}

function Test-NativeRefreshPreviousBoot($Receipt) {
    $boot = Get-NativeRefreshBoot
    if ($Receipt.PSObject.Properties['Boot'] -and $Receipt.Boot) { return $Receipt.Boot -ne $boot }
    # Older receipts have no boot identity. A receipt last written before this
    # boot cannot describe a surviving worker; claim/setup locks still arbitrate.
    $path = Join-Path (Get-NativeRefreshDirectory $Receipt.Id) 'receipt.json'
    return (Get-Item -LiteralPath $path).LastWriteTimeUtc.Ticks -lt [long]$boot
}

function Get-NativeRefreshStatus([string]$Id) {
    $receipt = Read-NativeRefreshReceipt $Id
    if ($receipt.Status -notin @('queued','running')) { return $receipt }
    if (Test-NativeRefreshPreviousBoot $receipt) {
        $receipt.Status = 'interrupted'
    } elseif ($receipt.Status -eq 'running') {
        $alive = $false
        if ($receipt.PSObject.Properties['WorkerPid'] -and $receipt.WorkerPid) {
            $process = Get-Process -Id $receipt.WorkerPid -ErrorAction SilentlyContinue
            if ($process) {
                try { $alive = $process.StartTime.ToUniversalTime().ToString('o') -eq $receipt.WorkerStartedUtc }
                finally { $process.Dispose() }
            }
        }
        if (-not $alive) { $receipt.Status = 'cleanup-uncertain' }
    }
    return $receipt
}

function Complete-NativeInterruptedRefresh([string]$Id) {
    $directory = Get-NativeRefreshDirectory $Id
    Assert-NativeSkillPath $directory
    $lease = $null; $setup = $null
    try {
        $lease = [IO.File]::Open((Join-Path $directory 'claim.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
        # A boot proves the old installer tree is gone; also exclude recovery
        # or another setup currently applying changes. Never replay the old job.
        $setup = [IO.File]::Open((Join-Path $env:LOCALAPPDATA 'Basaltwater\setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
        $receipt = Read-NativeRefreshReceipt $Id
        if ($receipt.Status -notin @('queued','running') -or -not (Test-NativeRefreshPreviousBoot $receipt)) { throw 'Refresh is not from a previous Windows boot' }
        $receipt.Status = 'interrupted'; $receipt.CompletedUtc = [datetime]::UtcNow.ToString('o')
        Write-NativeRefreshReceipt $receipt
        Unregister-ScheduledTask -TaskName ('Basaltwater-Refresh-' + $Id) -TaskPath '\' -Confirm:$false -ErrorAction SilentlyContinue
    } finally {
        if ($setup) { $setup.Dispose() }
        if ($lease) { $lease.Dispose() }
    }
}

function ConvertTo-NativeRefreshOptions([string[]]$Arguments) {
    $switches = @('Resume','UpdateSource','NoUpdateSource','NoRestartT3','NoReboot','Plan','GameDev','ShaderTools','Godot','Sunshine','NodeVersions')
    $values = @('Profile','StorageRoot','Channel','Version')
    $options = @{}
    for ($index = 0; $index -lt $Arguments.Count; $index++) {
        $name = $Arguments[$index].TrimStart('-')
        if ($Arguments[$index] -notmatch '\A-[A-Za-z]+\z' -or $options.ContainsKey($name)) { throw 'Invalid or duplicate refresh argument' }
        if ($name -in $switches) { $options[$name] = $true }
        elseif ($name -in $values) {
            $index++
            if ($index -ge $Arguments.Count) { throw "Missing refresh value for $name" }
            $options[$name] = $Arguments[$index]
        } else { throw "Invalid refresh option: $name" }
    }
    if ($options.Profile -ne 'agent_windows' -or -not $options.Resume) { throw 'Refresh requires the saved native profile' }
    return $options
}

function Start-NativeRefresh($Saved, [string]$Source, [string[]]$Arguments) {
    $options = ConvertTo-NativeRefreshOptions $Arguments
    if ($options.ContainsKey('Plan')) { throw 'A refresh preview must run in the foreground without creating a job' }
    $worker = Join-Path $Source 'windows\Invoke-Refresh.ps1'
    Assert-NativeSkillPath $worker
    if (-not (Test-Path -LiteralPath $worker -PathType Leaf)) { throw 'Refresh worker is missing; use the current root installer' }
    $root = Join-Path $env:LOCALAPPDATA 'Basaltwater\refresh'
    Assert-NativeSkillPath $root
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    $lock = [IO.File]::Open((Join-Path $root 'refresh.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    try {
        foreach ($directory in Get-ChildItem -LiteralPath $root -Directory) {
            if ($directory.Name -notmatch '\A[0-9a-f]{32}\z') { continue }
            $existing = Get-NativeRefreshStatus $directory.Name
            if ($existing.Status -eq 'interrupted' -and -not $existing.CompletedUtc) {
                Complete-NativeInterruptedRefresh $existing.Id
            }
            if ($existing.Status -eq 'cleanup-uncertain') { throw "Refresh $($existing.Id) lost its worker. An installer may still be running; inspect its logs and reboot Windows before starting another detached refresh." }
            if ($existing.Status -in @('queued','running')) { throw "Refresh $($existing.Id) is still pending; inspect it before starting another" }
        }
        $id = [guid]::NewGuid().ToString('N')
        $directory = Get-NativeRefreshDirectory $id
        New-Item -ItemType Directory -Path $directory | Out-Null
        $receipt = [pscustomobject]@{ Version = 1; Id = $id; OwnerSid = $Saved.OwnerSid;
            Source = $Saved.Source; Status = 'queued'; StartedUtc = $null; CompletedUtc = $null;
            Boot = (Get-NativeRefreshBoot); WorkerPid = 0; WorkerStartedUtc = '';
            Options = $options; Log = (Join-Path $directory 'setup.log'); LogTruncated = $false }
        Write-NativeRefreshReceipt $receipt
        $task = 'Basaltwater-Refresh-' + $id
        try {
            $command = "& '" + $worker.Replace("'", "''") + "' -Id '$id'"
            $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
            $action = New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
                -Argument ('-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand ' + $encoded)
            $principal = New-ScheduledTaskPrincipal -UserId $Saved.OwnerSid -LogonType Interactive -RunLevel Limited
            $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([timespan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
            Register-ScheduledTask -TaskName $task -TaskPath '\' -Action $action -Principal $principal -Settings $settings -Force | Out-Null
            Start-ScheduledTask -TaskName $task -TaskPath '\'
        } catch {
            $dispatchError = $_; $claim = $null
            try {
                # Scheduler may have started the worker before reporting a
                # transport error. Never overwrite its running/completed receipt.
                $claim = [IO.File]::Open((Join-Path $directory 'claim.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
                $receipt = Read-NativeRefreshReceipt $id
                if ($receipt.Status -eq 'queued') {
                    $receipt.Status = 'dispatch-failed'; $receipt.CompletedUtc = [datetime]::UtcNow.ToString('o')
                    Write-NativeRefreshReceipt $receipt
                    Unregister-ScheduledTask -TaskName $task -TaskPath '\' -Confirm:$false -ErrorAction SilentlyContinue
                }
            } catch [IO.IOException] { } # A claimed worker retains authority over its receipt.
            finally { if ($claim) { $claim.Dispose() } }
            throw $dispatchError
        }
        return [pscustomobject]@{ Id = $id; Status = $receipt.Status; Log = $receipt.Log;
            StatusCommand = "basaltw refresh --status $id"; LogsCommand = "basaltw refresh --logs $id" }
    } finally { $lock.Dispose() }
}

function Invoke-NativeRefreshWorker([string]$Id) {
    $lease = [IO.File]::Open((Join-Path (Get-NativeRefreshDirectory $Id) 'claim.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    try {
    $receipt = Read-NativeRefreshReceipt $Id
    if ($receipt.Status -ne 'queued') { throw 'Refresh job was already consumed; do not replay it' }
    if (Test-NativeRefreshPreviousBoot $receipt) { throw 'Refresh was queued before reboot; start a new refresh instead of replaying it' }
    $self = Get-Process -Id $PID
    try {
        $receipt | Add-Member NoteProperty WorkerPid $PID -Force
        $receipt | Add-Member NoteProperty WorkerStartedUtc $self.StartTime.ToUniversalTime().ToString('o') -Force
        $receipt | Add-Member NoteProperty Boot (Get-NativeRefreshBoot) -Force
    }
    finally { $self.Dispose() }
    $receipt.Status = 'running'; $receipt.StartedUtc = [datetime]::UtcNow.ToString('o')
    Write-NativeRefreshReceipt $receipt
    try {
        $saved = Read-NativeCliSetup
        if ($receipt.Source -ne $saved.Source) { throw 'Saved source changed before refresh started; inspect and retry deliberately' }
        $options = @{}
        foreach ($property in $receipt.Options.PSObject.Properties) { $options[$property.Name] = $property.Value }
        # Validate the persisted options again, rather than executing a saved command string.
        $arguments = @()
        foreach ($key in $options.Keys) {
            $arguments += '-' + $key
            if ($options[$key] -isnot [bool]) { $arguments += [string]$options[$key] }
            elseif (-not $options[$key]) { throw 'Unexpected false switch in refresh request' }
        }
        $options = ConvertTo-NativeRefreshOptions $arguments
        Assert-NativeSkillPath $receipt.Log
        $logPath = Join-Path (Get-NativeRefreshDirectory $Id) 'setup.log'
        if ($receipt.Log -ne $logPath) { throw 'Refresh log does not match its job directory' }
        $stream = [IO.File]::Open($logPath, 'CreateNew', 'Write', 'Read')
        $remaining = 16MB
        try {
            & { Invoke-NativePowerShell7Installer -Shell (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
                -Installer (Join-Path $saved.Source 'install.ps1') -Options $options } 6>&1 | ForEach-Object {
                $bytes = [Text.Encoding]::UTF8.GetBytes("$_" + [Environment]::NewLine)
                $count = [Math]::Min($remaining, $bytes.Length)
                if ($count -gt 0) { $stream.Write($bytes, 0, $count); $remaining -= $count }
                if ($count -lt $bytes.Length) { $receipt.LogTruncated = $true }
            }
        } finally { $stream.Dispose() }
        $receipt.Status = 'complete'
    } catch {
        $receipt.Status = 'failed'
        # Raw provider exceptions can contain sensitive arguments; keep a constant receipt.
        Add-Content -LiteralPath $receipt.Log -Value 'Refresh failed. Inspect setup output above and saved installation state before retrying.'
    } finally {
        $receipt.CompletedUtc = [datetime]::UtcNow.ToString('o')
        Write-NativeRefreshReceipt $receipt
        Unregister-ScheduledTask -TaskName ('Basaltwater-Refresh-' + $Id) -TaskPath '\' -Confirm:$false -ErrorAction SilentlyContinue
        try { Show-NativeDesktopNotification 'Basaltwater refresh' ("Refresh " + $receipt.Status + '. Use basaltw refresh --status ' + $Id) }
        catch { } # Notification delivery never changes setup success.
    }
    } finally { $lease.Dispose() }
}

function Stop-NativeQueuedRefresh([string]$Id) {
    $lease = [IO.File]::Open((Join-Path (Get-NativeRefreshDirectory $Id) 'claim.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    try {
        $receipt = Read-NativeRefreshReceipt $Id
        if ($receipt.Status -ne 'queued') { throw 'Only a queued refresh can be cancelled. An applying installer must finish before retrying.' }
        $receipt.Status = 'cancelled'; $receipt.CompletedUtc = [datetime]::UtcNow.ToString('o')
        Write-NativeRefreshReceipt $receipt
        Unregister-ScheduledTask -TaskName ('Basaltwater-Refresh-' + $Id) -TaskPath '\' -Confirm:$false -ErrorAction SilentlyContinue
        return [pscustomobject]@{ Id = $Id; Status = 'cancelled' }
    } finally { $lease.Dispose() }
}
