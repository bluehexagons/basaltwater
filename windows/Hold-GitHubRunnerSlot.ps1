<# Detached guardian: one workflow body at a time, including post-job cleanup. #>
param([Parameter(Mandatory=$true)][int]$WorkerId,
      [Parameter(Mandatory=$true)][long]$StartedTicks,
      [Parameter(Mandatory=$true)][string]$ReadyPath,
      [Parameter(Mandatory=$true)][string]$LockPath)
. (Join-Path $PSScriptRoot 'GitHubRunner.Common.ps1')
$worker = $null
$slot = $null
$job = $null
try {
    $worker = [Diagnostics.Process]::GetProcessById($WorkerId)
    if ($worker.StartTime.ToUniversalTime().Ticks -ne $StartedTicks) { throw 'Runner worker changed' }
    Import-RunnerJobType
    $job = New-Object Basaltwater.ProcessJob($worker.Handle)
    while (-not $worker.HasExited) {
        try {
            $slot = [IO.File]::Open($LockPath, [IO.FileMode]::OpenOrCreate,
                [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
            break
        } catch [IO.IOException] { Start-Sleep -Milliseconds 200 }
    }
    if (-not $slot -or $worker.HasExited) { throw 'Runner worker exited while queued' }
    [Basaltwater.ProcessJob]::KeepSlotUntilExit($slot.SafeFileHandle.DangerousGetHandle(), $worker.Handle)
    [IO.File]::WriteAllText($ReadyPath, 'ready')
    $worker.WaitForExit()
} finally {
    # Close the job first: failure must kill the worker before the lock is freed.
    if ($job) { $job.Dispose() }
    if ($slot) { $slot.Dispose() }
    if ($worker) { $worker.Dispose() }
}
