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
$task=$null;$window=$null;$complete=$false;$failure=$null
function Invoke-QualificationCli([string[]]$Arguments){
    $lines=& pwsh.exe -NoProfile -File $entry @Arguments
    $code=$LASTEXITCODE
    $value=($lines | Out-String) | ConvertFrom-Json
    if($code -ne 0){throw "Native qualification CLI failed (exit $code): $($value | ConvertTo-Json -Depth 8 -Compress)"}
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
    $inventory=Invoke-QualificationCli @('desktop','windows')
    $window=@($inventory.result.windows | Where-Object id -eq $info.window)[0]
    Assert-Gpu ($window.pid -eq $info.pid) 'Fixture window identity mismatch'
    $selectors=@('--window',[string]$window.id,'--identity',$window.identity,'--generation',$inventory.generation,'--width',[string]$window.width,'--height',[string]$window.height)
    $capture=Invoke-QualificationCli (@('desktop','capture')+$selectors+@('--output',(Join-Path $directory 'capture'),'--milliseconds','2200','--frames','5','--interval-ms','400'))
    Write-NativeHeldJson (Join-Path $directory 'capture-result.json') $capture 262144
    Assert-Gpu ($capture.result.frames.Count -eq 5 -and $null -eq $capture.result.observation_id -and $capture.result.cleanup_confirmed) 'GPU capture lacks frames, cleanup or authority boundary'
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
        Assert-Gpu ($trace.result.cleanup_confirmed -and $trace.result.summary.rows -ge 20 -and
            @($trace.result.summary.swap_chains | Where-Object hitches_over_50ms -GT 0).Count -ge 1) 'Trace did not observe the deliberately delayed D3D workload'
    }
    $complete=$true
} catch {$failure=$_.Exception.Message}
finally {
    if($task){
        try {
            if($window){
                $inventory=Invoke-QualificationCli @('desktop','windows')
                $current=@($inventory.result.windows | Where-Object { $_.id -eq $window.id -and $_.identity -ceq $window.identity })
                if($current.Count -eq 1){$null=Invoke-QualificationCli @('desktop','window','close','--window',[string]$window.id,'--identity',$window.identity,'--generation',$inventory.generation)}
            } else {$null=Invoke-QualificationCli @('desktop','develop','cancel',$task.Id)}
            $settled=Invoke-QualificationCli @('desktop','develop','wait',$task.Id,'--timeout','45')
            Write-NativeHeldJson (Join-Path $directory 'settled.json') $settled 65536
            Assert-Gpu ($settled.Receipt.State -eq 'complete' -and $settled.Receipt.TestSession.ProfileCleanup -eq 'removed') 'Fixture session cleanup incomplete'
        } catch {$complete=$false;$failure='Cleanup: '+$_.Exception.Message}
    }
    Write-NativeHeldJson (Join-Path $directory 'qualification.json') @{schema_version=1;complete=$complete;error=$failure;tested_source=(git -C (Split-Path -Parent $windows) rev-parse HEAD);dirty=$true;finished_utc=[DateTime]::UtcNow.ToString('o');limitations=@('Synthetic D3D11 window only','No HDR fidelity or physical scanout assertion','No game or audio qualification')} 65536
}
if(-not $complete){throw $failure}
Write-Host "GPU-window qualification passed; private evidence: $directory"
