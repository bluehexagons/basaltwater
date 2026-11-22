<# Opt-in fault qualification. No capture, GPU calls or desktop input.
   Runs the production watchdog against an intentionally stalled main thread. #>
param([Parameter(Mandatory)][string]$Output)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '..\AgentWorkstation.Common.ps1')
. (Join-Path $PSScriptRoot '..\Desktop.Common.ps1')
. (Join-Path $PSScriptRoot '..\Capture.Common.ps1')
$directory=New-NativeEvidenceBundle $Output
$helper=Get-NativeEvidenceTool 'Capture.Window'
$parent=$null;$child=$null
try {
    $self=[Diagnostics.Process]::GetCurrentProcess()
    try{$created=$self.StartTime.ToFileTimeUtc()}finally{$self.Dispose()}
    $clock=[Diagnostics.Stopwatch]::StartNew()
    $child=Start-Process $helper -ArgumentList @('--watchdog-test',$PID,$created) -WindowStyle Hidden -PassThru
    if(-not $child.WaitForExit(25000)){throw 'Stalled helper deadline cleanup unconfirmed'}
    $deadline=@{exit_code=$child.ExitCode;elapsed_seconds=$clock.Elapsed.TotalSeconds}
    if($child.ExitCode -ne 124 -or $clock.Elapsed.TotalSeconds -lt 19){throw 'Production deadline watchdog did not fire as expected'}
    $child.Dispose();$child=$null
    $parent=Start-Process pwsh.exe -ArgumentList @('-NoProfile','-Command','Start-Sleep -Seconds 60') -WindowStyle Hidden -PassThru
    $created=$parent.StartTime.ToFileTimeUtc()
    $child=Start-Process $helper -ArgumentList @('--watchdog-test',$parent.Id,$created) -WindowStyle Hidden -PassThru
    Start-Sleep -Milliseconds 500
    if($child.HasExited){throw 'Caller-loss probe exited before the owned parent was stopped'}
    $clock.Restart();$parent.Kill();if(-not $parent.WaitForExit(3000)){throw 'Owned probe parent cleanup unconfirmed'}
    if(-not $child.WaitForExit(5000)){throw 'Caller-loss watchdog cleanup unconfirmed'}
    $loss=@{exit_code=$child.ExitCode;elapsed_seconds=$clock.Elapsed.TotalSeconds}
    if($child.ExitCode -ne 125){throw 'Caller-loss watchdog did not fire as expected'}
    Write-NativeHeldJson (Join-Path $directory 'qualification.json') @{schema_version=1;complete=$true;deadline=$deadline;caller_loss=$loss;helper_sha256=(Get-FileHash $helper).Hash.ToLowerInvariant();scope='Production watchdog with intentionally stalled main thread; no claim of an actual driver stall'}
    Write-Host "Capture watchdog deadline and caller-loss passed: $directory"
} finally {
    foreach($process in @($child,$parent)){
        if($process){if(-not $process.HasExited){$process.Kill();if(-not $process.WaitForExit(5000)){throw 'Owned watchdog probe cleanup uncertain'}};$process.Dispose()}
    }
}
