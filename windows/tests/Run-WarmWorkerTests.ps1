<# Real owned helper, mocked Scheduler and desktop APIs; private temp mailboxes. #>
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '..\Desktop.Common.ps1')
function Assert-Warm([bool]$Value,[string]$Message){if(-not $Value){throw $Message}}
$temporary=Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-warm-tests-'+[guid]::NewGuid().ToString('N'))
New-NativeDesktopPrivateDirectory $temporary
$script:WarmFixtureRoot=$temporary
function Get-NativeDesktopStatePath {return Join-Path $script:WarmFixtureRoot 'state.json'}
function Get-NativeHeldAdmissionPath {return Join-Path $script:WarmFixtureRoot 'setup.lock'}
function Remove-NativeWarmRegistration($Record) { }
$originalSource=$script:NativeDesktopSource;$process=$null
try {
    $context=Get-NativeDesktopContext
    $state=@{owner=$context.Sid;session=$context.Session;revision=('a'*32);generation=('b'*32);mode='enabled'}
    Write-NativeHeldJson (Get-NativeDesktopStatePath) $state
    $source=Join-Path $temporary 'source';New-NativeDesktopPrivateDirectory $source
    foreach($name in Get-NativeWarmSources){Copy-Item -LiteralPath (Join-Path $originalSource $name) -Destination (Join-Path $source $name)}
    [IO.File]::WriteAllText((Join-Path $source 'Desktop.Native.cs'),'namespace Basaltwater.Desktop { public static class Native { public static void Ready() {} public static void Dpi() {} } }')
    $rootLiteral=$temporary.Replace("'","''")
    [IO.File]::AppendAllText((Join-Path $source 'Desktop.Common.ps1'),@"

function Get-NativeDesktopStatePath {return Join-Path '$rootLiteral' 'state.json'}
function Initialize-NativeDesktopTypes {
    Add-Type -Path (Join-Path `$PSScriptRoot 'Desktop.Native.cs')
    [IO.File]::AppendAllText((Join-Path '$rootLiteral' 'compilations'),'1')
}
function Invoke-NativeDesktopWorkerBody(`$Request) {
    if(`$Request.PSObject.Properties['delay_ms']){Start-Sleep -Milliseconds ([int]`$Request.delay_ms)}
    `$guardReason=`$script:NativeDesktopInputGuard.Invoke()
    if(`$guardReason){throw `$guardReason}
    if(`$Request.command -eq 'input'){[IO.File]::AppendAllText((Join-Path '$rootLiteral' 'effects'),'1')}
    return @{worker_pid=`$PID;echo=`$Request.text}
}
"@)
    [IO.File]::AppendAllText((Join-Path $source 'Desktop.Worker.ps1'),"`nfunction Remove-NativeWarmRegistration(`$Record) { }`n")
    $script:NativeDesktopSource=$source
    function New-ScheduledTaskAction($Execute,$Argument){return @{execute=$Execute;arguments=$Argument}}
    function New-ScheduledTaskPrincipal($UserId,$LogonType,$RunLevel){return @{}}
    function New-ScheduledTaskSettingsSet($ExecutionTimeLimit,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries){return @{}}
    function Register-ScheduledTask($TaskName,$Action,$Principal,$Settings){$script:WarmFixtureAction=$Action}
    function Start-ScheduledTask($TaskName){
        $start=[Diagnostics.ProcessStartInfo]::new();$start.FileName=$script:WarmFixtureAction.execute;$start.Arguments=$script:WarmFixtureAction.arguments
        $start.UseShellExecute=$false;$start.CreateNoWindow=$true;$start.WindowStyle='Hidden';$start.EnvironmentVariables.Remove('PSModulePath')
        $script:WarmFixtureProcess=[Diagnostics.Process]::Start($start)
    }
    $worker=Start-NativeWarmWorker $context 30 60
    $process=$script:WarmFixtureProcess
    $caught=$null;try{Assert-NativeWarmIdle}catch{$caught=$_}
    Assert-Warm ($caught.Exception.Message -match 'active or uncertain') 'Active worker did not block maintenance'
    & {
        $readRecord=${function:Read-NativeHeldJson};$script:WarmReadAttempts=0
        function Read-NativeHeldJson([string]$Path,[int]$Limit=65536){
            $script:WarmReadAttempts++
            if($script:WarmReadAttempts -eq 1){throw [IO.FileNotFoundException]::new('Replacement in progress')}
            return & $readRecord $Path $Limit
        }
        $retried=Read-NativeWarmRecord $worker.id
        Assert-Warm ($retried.id -eq $worker.id -and $script:WarmReadAttempts -eq 2) 'Transient metadata read was not bounded and recovered'
    }
    $caught=$null;try{Start-NativeWarmWorker $context 30 60}catch{$caught=$_}
    Assert-Warm ($caught.Exception.Message -match 'already active') 'A second worker bypassed admission'
    $first=Invoke-NativeWarmRequest $worker @{command='ready';text='first'} 8
    $second=Invoke-NativeWarmRequest $worker @{command='ready';text='second'} 8
    Assert-Warm ($first.worker_pid -eq $second.worker_pid -and $second.echo -eq 'second') 'Warm requests did not reuse the same helper'
    Assert-Warm ([IO.File]::ReadAllText((Join-Path $temporary 'compilations')) -eq '1') 'Native types compiled for every request'
    $original=[IO.File]::ReadAllText((Join-Path $source 'Desktop.Hold.cs'))
    [IO.File]::AppendAllText((Join-Path $source 'Desktop.Hold.cs'),"`n// changed fixture source")
    $caught=$null;try{Invoke-NativeWarmRequest $worker @{command='ready';text='changed'} 8}catch{$caught=$_}
    Assert-Warm ($caught.Exception.Message -match 'source changed') 'Worker accepted source drift'
    [IO.File]::WriteAllText((Join-Path $source 'Desktop.Hold.cs'),$original)
    $state.mode='disabled';Write-NativeHeldJson (Get-NativeDesktopStatePath) $state
    $caught=$null;try{Invoke-NativeWarmRequest $worker @{command='input';text='blocked'} 8}catch{$caught=$_}
    Assert-Warm ($caught.Exception.Message -match 'not enabled' -and -not(Test-Path -LiteralPath (Join-Path $temporary 'effects'))) 'Disabled control dispatched input'
    $state.mode='enabled';Write-NativeHeldJson (Get-NativeDesktopStatePath) $state
    # Direct, already-expired request: no native action and no reply replay.
    $id=[guid]::NewGuid().ToString('N');$mailbox=Join-Path (Join-Path $worker.directory 'requests') $id
    New-NativeDesktopPrivateDirectory $mailbox
    $self=[Diagnostics.Process]::GetCurrentProcess()
    try{$created=$self.StartTime.ToUniversalTime().Ticks}finally{$self.Dispose()}
    Write-NativeHeldJson (Join-Path $mailbox 'request.json') @{version=1;id=$id;pid=$PID;created=$created;session=$context.Session;revision=$state.revision;generation=$state.generation;deadline=([Diagnostics.Stopwatch]::GetTimestamp()-1);request=@{command='input';text='expired'}}
    $clock=[Diagnostics.Stopwatch]::StartNew();$response=Join-Path $mailbox 'response.json'
    while(-not(Test-Path -LiteralPath $response) -and $clock.Elapsed.TotalSeconds -lt 8){Start-Sleep -Milliseconds 10}
    $result=Read-NativeHeldJson $response
    Assert-Warm (-not $result.ok -and -not $result.submitted -and $result.error -match 'expired' -and -not(Test-Path -LiteralPath (Join-Path $temporary 'effects'))) 'Expired queued request was dispatched'
    # Reject a recycled caller identity without ever claiming its mutation.
    $id=[guid]::NewGuid().ToString('N');$stale=Join-Path (Join-Path $worker.directory 'requests') $id
    New-NativeDesktopPrivateDirectory $stale
    Write-NativeHeldJson (Join-Path $stale 'request.json') @{version=1;id=$id;pid=$PID;created=($created+1);session=$context.Session;revision=$state.revision;generation=$state.generation;deadline=([Diagnostics.Stopwatch]::GetTimestamp()+8*[Diagnostics.Stopwatch]::Frequency);request=@{command='input';text='stale'}}
    Send-NativeWarmSignal $worker
    $clock.Restart();$response=Join-Path $stale 'response.json'
    while(-not(Test-Path -LiteralPath $response) -and $clock.Elapsed.TotalSeconds -lt 8){Start-Sleep -Milliseconds 10}
    $result=Read-NativeHeldJson $response
    Assert-Warm (-not $result.ok -and -not $result.submitted -and -not(Test-Path -LiteralPath (Join-Path $stale 'claimed'))) 'Stale caller identity reached dispatch'
    Assert-Warm (-not(Test-Path -LiteralPath (Join-Path $temporary 'effects'))) 'A rejected mailbox was replayed'
    $stopped=Stop-NativeWarmWorker
    Assert-Warm $stopped.cleanup_confirmed 'Warm worker did not settle'
    Assert-Warm ($process.WaitForExit(3000)) 'Worker process survived graceful shutdown'
    Assert-NativeWarmIdle
    $process.Dispose();$process=$null
    # A Scheduler delivery arriving after retirement cannot reopen old work.
    Start-ScheduledTask $worker.task;$process=$script:WarmFixtureProcess
    Assert-Warm ($process.WaitForExit(5000)) 'A retired worker was restarted by late dispatch'
    Assert-Warm ((Get-NativeWarmRecord).cleanup_confirmed -and -not(Test-Path -LiteralPath (Join-Path $temporary 'effects'))) 'Late dispatch changed cleanup or replayed input'
    $process.Dispose();$process=$null
    # A claimed timeout cannot be retried by the next request or worker.
    $worker=Start-NativeWarmWorker $context 30 60;$process=$script:WarmFixtureProcess
    $caught=$null;try{Invoke-NativeWarmRequest $worker @{command='ready';text='slow';delay_ms=2000} 1}catch{$caught=$_}
    Assert-Warm ($caught.Exception.Message -match 'outcome unknown' -and $process.WaitForExit(3000)) 'Timed-out helper was not stopped or uncertainty hidden'
    $status=Get-NativeWarmStatus
    Assert-Warm ($status.state -eq 'unavailable' -and -not $status.cleanup_confirmed) 'Dead helper status still advertised running or settled'
    $stopped=Stop-NativeWarmWorker
    Assert-Warm $stopped.cleanup_confirmed 'Dead-worker metadata could not retire'
    Write-Host 'Warm worker lifecycle, source, deadline and control contracts passed (desktop APIs mocked).'
} finally {
    if($process){if(-not $process.HasExited){$process.Kill();$null=$process.WaitForExit(3000)};$process.Dispose()}
    $script:NativeDesktopSource=$originalSource
    $resolved=[IO.Path]::GetFullPath($temporary)
    if(-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe warm test cleanup'}
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
