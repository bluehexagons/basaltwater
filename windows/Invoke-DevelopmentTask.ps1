<# Scheduled interactive-account guardian. Only the gated child executes project code. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('\A[a-f0-9]{32}\z')][string]$TaskId,
    [switch]$Child,
    [string]$Gate = '',
    [string]$JobName = ''
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($Child) {
    # Wait before module discovery, request reads or any other helper work:
    # module autoloading itself can create compatibility subprocesses.
    $event = [Threading.EventWaitHandle]::OpenExisting($Gate)
    try { if (-not $event.WaitOne(30000)) { throw 'Containment gate was not opened; refusing project code.' } }
    finally { $event.Dispose() }
}
. (Join-Path $PSScriptRoot 'Development.Common.ps1')
if (-not (Test-Path 'Function:\Show-NativeDesktopNotification')) {
    . (Join-Path $PSScriptRoot 'ShellIntegration.Common.ps1')
}
$directory = Get-DevelopmentDirectory $TaskId
$record = Get-DevelopmentRecord $TaskId
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'Development workers require verified PowerShell 7. Run native doctor and repair PowerShell.' }
if ($Child) {
    $request = Read-DevelopmentJson (Join-Path $directory 'request.json')
    Set-Location -LiteralPath $request.Project
    if ($request.Msvc) {
        . (Join-Path $PSScriptRoot 'GameDevelopment.Common.ps1')
        function Get-NativeCommand([string]$Name) {
            $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($command) { $command.Source }
        }
        Enter-NativeGameDevEnvironment
    }
    . (Join-Path $PSScriptRoot 'Development.Process.ps1')
    Import-DevelopmentProcessType
    try {
        $executable = Resolve-DevelopmentExecutable $request.Executable
        exit [Basaltwater.DevelopmentProcess]::Run($JobName, $executable, [string[]]$request.Argv, $request.Project)
    } catch {
        if ($_.Exception.ToString().Contains('BASALTWATER_CLEANUP_UNCERTAIN')) {
            [IO.File]::WriteAllText((Join-Path $directory 'child-cleanup-uncertain'), 'Native cleanup requires inspection')
        }
        throw
    }
}
# An exclusive guardian lease prevents duplicate scheduler starts and request replay.
$lease = [IO.File]::Open((Join-Path $directory 'guardian.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
$record = Get-DevelopmentRecord $TaskId
if ($record.CompletedUtc -or $record.WorkerPid) {
    $lease.Dispose()
    throw 'Task already acknowledged; refusing replay.'
}
$process = $null; $job = $null; $gateHandle = $null; $started = $false; $awake = $false
$copies = @(); $cleanupError = $null; $desired = 'failed'
try {
    if (Test-DevelopmentPreviousBoot $record) { $desired = 'interrupted'; return }
    $self = Get-Process -Id $PID
    $record.WorkerPid = $PID; $record.WorkerStartedUtc = $self.StartTime.ToUniversalTime().ToString('o')
    $self.Dispose()
    $record | Add-Member NoteProperty Boot (Get-DevelopmentBoot) -Force
    $record.State = 'starting'
    Write-DevelopmentJson (Join-Path $directory 'receipt.json') $record
    $request = Read-DevelopmentJson (Join-Path $directory 'request.json')
    . (Join-Path $PSScriptRoot 'GitHubRunner.Common.ps1')
    . (Join-Path $PSScriptRoot 'Development.Process.ps1')
    Import-RunnerJobType
    Import-DevelopmentProcessType
    if (Test-Path -LiteralPath (Join-Path $directory 'cancel')) { $desired = 'cancelled'; return }
    $gateName = 'Local\BasaltwaterDevelopment-' + [guid]::NewGuid().ToString('N')
    $jobName = $gateName + '-job'
    $gateHandle = [Threading.EventWaitHandle]::new($false, 'ManualReset', $gateName)
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $request.PowerShell
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $info.WorkingDirectory = $request.Project
    foreach ($arg in @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File', $PSCommandPath, '-TaskId', $TaskId, '-Child', '-Gate', $gateName, '-JobName', $jobName)) { $info.ArgumentList.Add($arg) }
    $info.Environment.Clear()
    foreach ($property in $request.Environment.PSObject.Properties) { $info.Environment[$property.Name] = [string]$property.Value }
    $process = [Diagnostics.Process]::new(); $process.StartInfo = $info
    if (-not $process.Start()) { throw 'Gated child could not start.' }
    $started = $true
    $job = [Basaltwater.ProcessJob]::new($process.Handle, $jobName)
    $record.ChildPid = $process.Id; $record.ChildStartedUtc = $process.StartTime.ToUniversalTime().ToString('o')
    $copies = @([Basaltwater.DevelopmentStreams]::Drain($process.StandardOutput.BaseStream, (Join-Path $directory 'stdout.log'), 4MB),
                [Basaltwater.DevelopmentStreams]::Drain($process.StandardError.BaseStream, (Join-Path $directory 'stderr.log'), 4MB))
    [Basaltwater.DevelopmentStreams]::Awake($true); $awake = $true
    $record.State = 'running'
    Write-DevelopmentJson (Join-Path $directory 'receipt.json') $record
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $gateHandle.Set() | Out-Null
    while (-not $process.WaitForExit(200)) {
        if (Test-Path -LiteralPath (Join-Path $directory 'cancel')) { $desired = 'cancelled'; break }
        if ($timer.Elapsed.TotalSeconds -ge $request.TimeoutSeconds) { $desired = 'timed-out'; break }
    }
    if ($process.HasExited) {
        $record.ExitCode = $process.ExitCode
        if ($desired -notin @('cancelled','timed-out')) { $desired = if ($process.ExitCode -eq 0) { 'complete' } else { 'failed' } }
    }
} catch { $record.Message = $_.Exception.Message }
finally {
    try {
        Stop-DevelopmentProcess $job $process $started $copies
        if (Test-Path -LiteralPath (Join-Path $directory 'child-cleanup-uncertain')) { throw 'Native child cleanup could not be confirmed; inspect owned processes.' }
    } catch { $cleanupError = $_.Exception.Message }
    if ($job) { $job.Dispose() }
    if ($process) { $process.Dispose() }
    if ($gateHandle) { $gateHandle.Dispose() }
    if ($awake) {
        try { [Basaltwater.DevelopmentStreams]::Awake($false) }
        catch { $cleanupError = $_.Exception.Message }
    }
    if ($cleanupError) { $record.State = 'cleanup-uncertain'; $record.Message = $cleanupError }
    else { $record.State = $desired; $record.CompletedUtc = [DateTime]::UtcNow.ToString('o') }
    try {
        if ($record.CompletedUtc) {
            try { Remove-DevelopmentRequest $directory }
            catch {
                $record.State = 'cleanup-uncertain'; $record.CompletedUtc = $null
                $record.Message = 'Private environment cleanup could not be confirmed; inspect the task before maintenance.'
            }
        }
        Write-DevelopmentJson (Join-Path $directory 'receipt.json') $record
        # Environment snapshots may contain provider credentials; retain only task receipts/logs.
        if ($record.CompletedUtc) {
            Unregister-ScheduledTask -TaskName $record.TaskName -Confirm:$false -ErrorAction SilentlyContinue
            try {
                Show-NativeDesktopNotification 'Basaltwater task' ("Task " + $record.State + '. Use develop status/logs ' + $record.Id)
            } catch { } # Notification failure does not invalidate verified cleanup.
        }
    } finally { $lease.Dispose() }
}
