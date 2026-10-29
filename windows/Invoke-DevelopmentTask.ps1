<# Scheduled interactive-account guardian. Only the gated child executes project code. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{32}$')][string]$TaskId,
    [switch]$Child,
    [string]$Gate = ''
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Development.Common.ps1')
$directory = Get-DevelopmentDirectory $TaskId
$record = Get-DevelopmentRecord $TaskId
$request = Get-Content -LiteralPath (Join-Path $directory 'request.json') -Raw | ConvertFrom-Json
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'Development workers require verified PowerShell 7. Run native doctor and repair PowerShell.' }
if ($Child) {
    $event = [Threading.EventWaitHandle]::OpenExisting($Gate)
    try { if (-not $event.WaitOne(30000)) { throw 'Containment gate was not opened; refusing project code.' } }
    finally { $event.Dispose() }
    Set-Location -LiteralPath $request.Project
    if ($request.Msvc) {
        . (Join-Path $PSScriptRoot 'GameDevelopment.Common.ps1')
        function Get-NativeCommand([string]$Name) {
            $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($command) { $command.Source }
        }
        Enter-NativeGameDevEnvironment
    }
    # ArgumentList avoids PowerShell version-specific native quoting (including empty argv).
    # This process inherits the child's Job membership and redirected standard handles.
    $nativeInfo = [Diagnostics.ProcessStartInfo]::new()
    $nativeInfo.FileName = $request.Executable
    $nativeInfo.WorkingDirectory = $request.Project
    $nativeInfo.UseShellExecute = $false
    foreach ($arg in @($request.Argv)) { $nativeInfo.ArgumentList.Add([string]$arg) }
    $native = [Diagnostics.Process]::new(); $native.StartInfo = $nativeInfo
    try {
        if (-not $native.Start()) { throw 'Project executable failed to start.' }
        $native.WaitForExit()
        exit $native.ExitCode
    } finally { $native.Dispose() }
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
    $self = Get-Process -Id $PID
    $record.WorkerPid = $PID; $record.WorkerStartedUtc = $self.StartTime.ToUniversalTime().ToString('o')
    $self.Dispose()
    $record.State = 'starting'
    Write-DevelopmentJson (Join-Path $directory 'receipt.json') $record
    . (Join-Path $PSScriptRoot 'GitHubRunner.Common.ps1')
    . (Join-Path $PSScriptRoot 'Development.Process.ps1')
    Import-RunnerJobType
    Import-DevelopmentProcessType
    if (Test-Path -LiteralPath (Join-Path $directory 'cancel')) { $desired = 'cancelled'; return }
    $gateName = 'Local\BasaltwaterDevelopment-' + [guid]::NewGuid().ToString('N')
    $gateHandle = [Threading.EventWaitHandle]::new($false, 'ManualReset', $gateName)
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $request.PowerShell
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $info.WorkingDirectory = $request.Project
    foreach ($arg in @('-NoProfile','-NonInteractive','-File', $PSCommandPath, '-TaskId', $TaskId, '-Child', '-Gate', $gateName)) { $info.ArgumentList.Add($arg) }
    $info.Environment.Clear()
    foreach ($property in $request.Environment.PSObject.Properties) { $info.Environment[$property.Name] = [string]$property.Value }
    $process = [Diagnostics.Process]::new(); $process.StartInfo = $info
    if (-not $process.Start()) { throw 'Gated child could not start.' }
    $started = $true
    $job = New-Object Basaltwater.ProcessJob($process.Handle)
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
        Write-DevelopmentJson (Join-Path $directory 'receipt.json') $record
        # Environment snapshots may contain provider credentials; retain only task receipts/logs.
        if ($record.CompletedUtc) {
            Remove-Item -LiteralPath (Join-Path $directory 'request.json') -Force
            Unregister-ScheduledTask -TaskName $record.TaskName -Confirm:$false -ErrorAction SilentlyContinue
        }
    } finally { $lease.Dispose() }
}
