<# Explicit native GPU qualification. Creates only a disposable owned fixture/session.
   Not included in the offline suite. All raw evidence remains outside the checkout. #>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Output,[switch]$Pacing)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$windows=Split-Path -Parent $PSScriptRoot
. (Join-Path $windows 'AgentWorkstation.Common.ps1')
. (Join-Path $windows 'Desktop.Common.ps1')
. (Join-Path $windows 'Capture.Common.ps1')
. (Join-Path $windows 'Development.Common.ps1')
function Assert-Gpu([bool]$Condition,[string]$Message){if(-not $Condition){throw $Message}}
$directory=New-NativeEvidenceBundle $Output
$entry=Join-Path $windows 'Invoke-Basaltw.ps1'
$repo=Split-Path -Parent $windows
$revision=git -C $repo rev-parse HEAD
if($LASTEXITCODE -ne 0){throw 'Could not read the qualification source revision'}
$changes=git -C $repo status --porcelain
if($LASTEXITCODE -ne 0){throw 'Could not read the qualification worktree status'}
$task=$null;$complete=$false;$failure=$null
function Invoke-QualificationCli([string[]]$Arguments,[switch]$AllowWaitFailure){
    $lines=& pwsh.exe -NoProfile -File $entry @Arguments
    $code=$LASTEXITCODE
    $value=($lines | Out-String) | ConvertFrom-Json
    if($code -ne 0 -and -not ($AllowWaitFailure -and $null -ne $value -and $value.PSObject.Properties['WaitState'])){
        throw "Native qualification CLI failed (exit $code): $($value | ConvertTo-Json -Depth 8 -Compress)"
    }
    return $value
}
try {
    . (Join-Path $windows 'GameDevelopment.Common.ps1')
    Enter-NativeGameDevEnvironment
    $fixture=Join-Path $directory 'RenderWindow.exe'
    & cl.exe /nologo /O2 /EHsc (Join-Path $PSScriptRoot 'RenderWindow.cpp') ('/Fo'+(Join-Path $directory 'fixture.obj')) ('/Fe'+$fixture) /link d3d11.lib dxgi.lib user32.lib *> (Join-Path $directory 'fixture-build.log')
    Assert-Gpu ($LASTEXITCODE -eq 0) 'GPU fixture compilation failed'
    $launch=@('desktop','develop','exec','--project',$directory,'--test-session','--ready-timeout','30','--timeout','120','--',$fixture)
    if($Pacing){$launch+='--hitch'}
    $task=Invoke-QualificationCli $launch
    Write-NativeHeldJson (Join-Path $directory 'submitted.json') $task 65536
    $null=Invoke-QualificationCli @('desktop','develop','ready',$task.Id,'--timeout','45')
    $state=Invoke-QualificationCli @('desktop','develop','status',$task.Id)
    Write-NativeHeldJson (Join-Path $directory 'ready.json') $state 65536
    $testEvidence=Read-NativeHeldJson (Join-Path $state.TestSession.EvidencePath 'session-evidence.json') 262144
    Assert-Gpu ($null -ne $testEvidence.HostEnvironment.Power -and $testEvidence.HostEnvironment.Power.State -eq 'available') 'Session did not retain native power evidence'
    $info=Read-NativeHeldJson (Join-Path $state.TestSession.EvidencePath 'artifacts\window.json')
    $inventory=Invoke-QualificationCli @('desktop','windows','--task',$task.Id)
    $windowsInTask=@($inventory.result.windows)
    Assert-Gpu ($inventory.result.task_id -ceq $task.Id -and $windowsInTask.Count -eq 1 -and
        $windowsInTask[0].id -eq $info.window -and $windowsInTask[0].pid -eq $info.pid) 'Task-scoped window inventory did not isolate the fixture'
    $window=$windowsInTask[0]
    $selectors=@('--window',[string]$window.id,'--identity',$window.identity,'--generation',$inventory.generation,'--width',[string]$window.width,'--height',[string]$window.height)
    $expectedFrames=5
    $capture=Invoke-QualificationCli (@('desktop','capture')+$selectors+@('--output',(Join-Path $directory 'capture'),'--milliseconds','2200','--frames',[string]$expectedFrames,'--interval-ms','400'))
    Write-NativeHeldJson (Join-Path $directory 'capture-result.json') $capture 262144
    Assert-Gpu $capture.result.cleanup_confirmed 'GPU capture helper cleanup was unconfirmed'
    Assert-Gpu ($null -eq $capture.result.observation_id) 'GPU capture returned input authority'
    $frameCount=@($capture.result.frames).Count
    Assert-Gpu ($frameCount -eq $expectedFrames) "GPU fixture received $frameCount of $expectedFrames expected capture frames"
    Add-Type -AssemblyName System.Drawing
    $colors=@()
    foreach($frame in $capture.result.frames){
        $image=[Drawing.Bitmap]::FromFile((Join-Path $directory ('capture\'+$frame.artifact)))
        try {$pixel=$image.GetPixel([int]($image.Width/2),[int]($image.Height/2));$colors+=,@($pixel.R,$pixel.G,$pixel.B)}finally{$image.Dispose()}
        Assert-Gpu ($frame.dpi -gt 0 -and $frame.client_geometry[0] -gt 0 -and $frame.frame_time_100ns -gt 0) 'Frame metadata unavailable'
    }
    $orange=@($colors | Where-Object {$_[0] -gt 220 -and $_[1] -gt 90 -and $_[1] -lt 160 -and $_[2] -lt 20}).Count
    $blue=@($colors | Where-Object {$_[0] -lt 20 -and $_[1] -lt 20 -and $_[2] -gt 220}).Count
    Assert-Gpu ($orange -gt 0 -and $blue -gt 0) 'Captured pixels do not independently show both D3D-rendered phases'
    if($Pacing){
        $trace=Invoke-QualificationCli @('desktop','pacing','--task',$task.Id,'--project',$directory,'--output',(Join-Path $directory 'pacing'),'--seconds','5')
        Write-NativeHeldJson (Join-Path $directory 'pacing-result.json') $trace 262144
        $sampleCount=$trace.result.summary.rows-$trace.result.summary.unavailable_cpu_intervals
        Assert-Gpu ($trace.result.cleanup_confirmed -and $sampleCount -ge 20 -and
            @($trace.result.summary.swap_chains | Where-Object hitches_over_50ms -GT 0).Count -ge 1) 'Trace did not observe the deliberately delayed D3D workload'
    }
    $complete=$true
} catch {$failure=$_.Exception.Message}
finally {
    if($task){
        try {
            $profile=$task.TestSession.ProfilePath
            try {
                if(Test-Path -LiteralPath $profile -PathType Container){
                    [IO.File]::WriteAllText((Join-Path $profile 'stop'),'stop')
                }
            } catch {
                $complete=$false
                $failure=(@($failure,('Fixture stop request: '+$_.Exception.Message)) | Where-Object {$_}) -join ' | '
            }
            $settled=Invoke-QualificationCli @('desktop','develop','wait',$task.Id,'--timeout','15') -AllowWaitFailure
            if($settled.TimedOut){
                try {$null=Invoke-QualificationCli @('desktop','develop','cancel',$task.Id)}
                catch {
                    # The request can take effect before the CLI returns an error.
                    $complete=$false
                    $failure=(@($failure,('Fixture cancellation: '+$_.Exception.Message)) | Where-Object {$_}) -join ' | '
                }
                $settled=Invoke-QualificationCli @('desktop','develop','wait',$task.Id,'--timeout','30') -AllowWaitFailure
            }
            Write-NativeHeldJson (Join-Path $directory 'settled.json') $settled 65536
            Assert-Gpu ($settled.WaitState -eq 'completed' -and $settled.Receipt.TestSession.ProfileCleanup -eq 'removed') 'Fixture session cleanup incomplete'
            Assert-Gpu (-not $complete -or $settled.Receipt.State -eq 'complete') 'Fixture did not exit normally'
        } catch {$complete=$false;$failure=(@($failure,('Cleanup: '+$_.Exception.Message)) | Where-Object {$_}) -join ' | '}
    }
    Write-NativeHeldJson (Join-Path $directory 'qualification.json') @{schema_version=1;complete=$complete;error=$failure;tested_source=$revision;dirty=[bool]$changes;finished_utc=[DateTime]::UtcNow.ToString('o');limitations=@('Synthetic D3D11 window only','No HDR fidelity or physical scanout assertion','No game or audio qualification')} 65536
}
if(-not $complete){throw $failure}
Write-Host "GPU-window qualification passed; private evidence: $directory"
