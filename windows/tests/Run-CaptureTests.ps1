<# Offline evidence contracts: no windows, GPU capture, ETW or downloads. #>
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '..\Desktop.Common.ps1')
. (Join-Path $PSScriptRoot '..\Capture.Common.ps1')
. (Join-Path $PSScriptRoot '..\Capture.Pacing.ps1')
function Assert-Capture([bool]$Value,[string]$Message){if(-not $Value){throw $Message}}
function Assert-CaptureThrows([scriptblock]$Action,[string]$Pattern){
    $caught=$null;try{& $Action | Out-Null}catch{$caught=$_}
    Assert-Capture ($caught -and $caught.Exception.Message -match $Pattern) "Expected $Pattern; got $caught"
}
$temporary=Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-capture-tests-'+[guid]::NewGuid().ToString('N'))
New-NativeDesktopPrivateDirectory $temporary
try {
    $caller=Get-NativeEvidenceCaller
    Assert-Capture ($caller.pid -eq $PID -and $caller.filetime -gt 0) 'Direct source caller identity missing'
    $global:BasaltwaterNativeCaller=@{pid=42;filetime=123}
    try{
        $caller=Get-NativeEvidenceCaller
        Assert-Capture ($caller.pid -eq 42 -and $caller.filetime -eq 123) 'Native launcher caller replaced by PowerShell bootstrap'
    }finally{Remove-Variable BasaltwaterNativeCaller -Scope Global}
    # Exact Windows argv encoding, without passing text through a shell.
    Assert-Capture ((ConvertTo-NativeEvidenceArgument 'a"b\') -ceq '"a\"b\\"') 'Literal quote/trailing backslash escaping changed'
    Assert-Capture ((ConvertTo-NativeEvidenceArgument '') -ceq '""') 'Empty argument lost'
    Assert-CaptureThrows {ConvertTo-NativeEvidenceArgument ([string][char]0)} 'Invalid'
    Assert-Capture ((Invoke-NativeWindowCapture @('--help')).ok) 'Capture help contacted the desktop'
    Assert-Capture ((Invoke-NativePacing @('--help')).ok) 'Pacing help contacted the desktop'
    function Get-NativeDesktopContext {return @{Sid='fixture-owner';Session='fixture-session'}}
    function Get-NativeDesktopTime {return 100L}
    $script:CaptureFixtureState=@{generation='fixture-generation';window_observed=100L;windows=@(@{id=123L;identity='42:638000000000000000:fixture';width=640;height=480})}
    function Read-NativeDesktopState($Context){return $script:CaptureFixtureState}
    $helper=Join-Path $temporary 'fixture.exe';[IO.File]::WriteAllText($helper,'never execute')
    function Get-NativeEvidenceTool($Name){return $helper}
    $script:CaptureFixtureCalls=0;$script:CaptureFixtureFailure=$false
    function Invoke-NativeEvidenceProcess($Executable,$Arguments,$TimeoutSeconds,$Directory){
        $script:CaptureFixtureCalls++
        Assert-Capture ($Arguments[0] -eq '123' -and $Arguments[1] -eq '42' -and $Arguments[2] -eq '133088768000000000') 'Capture lost process creation identity'
        Assert-Capture ($TimeoutSeconds -eq 15) 'Capture deadline changed'
        [IO.File]::WriteAllBytes((Join-Path $Directory 'frame-0000.png'),[byte[]]@(137,80,78,71))
        [IO.File]::WriteAllText((Join-Path $Directory 'frames.jsonl'),'{"sequence":0,"artifact":"frame-0000.png","frame_time_100ns":1000,"acquired_qpc":1100,"qpc_frequency":10000000,"width":640,"height":480}')
        return @{ExitCode=$(if($script:CaptureFixtureFailure){1}else{0});Receipt=@{schema_version=1;frames=1;complete=(-not $script:CaptureFixtureFailure)}}
    }
    $selectors=@('--window','123','--identity','42:638000000000000000:fixture','--generation','fixture-generation','--width','640','--height','480')
    foreach($extra in @(@('--frames','121'),@('--milliseconds','99'),@('--interval-ms','15'),@('--frames','2','--frames','3'),@('--surprise','value'))){
        Assert-CaptureThrows {Invoke-NativeWindowCapture ($selectors+@('--output',(Join-Path $temporary 'not-created'))+$extra)} 'bounds|option'
    }
    Assert-Capture ($script:CaptureFixtureCalls -eq 0) 'Invalid trailing capture option dispatched work'
    $script:CaptureFixtureState.generation='changed'
    Assert-CaptureThrows {Invoke-NativeWindowCapture ($selectors+@('--output',(Join-Path $temporary 'stale')))} 'fresh matching'
    $script:CaptureFixtureState.generation='fixture-generation'
    $good=Invoke-NativeWindowCapture ($selectors+@('--output',(Join-Path $temporary 'good'),'--frames','1'))
    Assert-Capture ($good.ok -and $good.result.frames.Count -eq 1 -and $null -eq $good.result.observation_id -and $good.result.cleanup_confirmed) "Capture lost evidence or minted input authority. Errors: $($good.result.errors -join ', ')"
    Assert-NativeDesktopPrivatePath (Join-Path $temporary 'good\frames.jsonl')
    Assert-NativeDesktopPrivatePath (Join-Path $temporary 'good\frame-0000.png')
    Assert-Capture ($good.result.frames[0].sha256 -eq (Get-FileHash (Join-Path $temporary 'good\frame-0000.png')).Hash.ToLowerInvariant()) 'Frame digest missing'
    Assert-CaptureThrows {Invoke-NativeWindowCapture ($selectors+@('--output',(Join-Path $temporary 'good')))} 'new directory'
    Assert-Capture ($script:CaptureFixtureCalls -eq 1) 'Existing output replayed capture'
    $script:CaptureFixtureFailure=$true
    $bad=Invoke-NativeWindowCapture ($selectors+@('--output',(Join-Path $temporary 'failed')))
    Assert-Capture (-not $bad.ok -and $bad.result.frames.Count -eq 1 -and (Test-Path -LiteralPath (Join-Path $temporary 'failed\manifest.json'))) 'Failed helper lost its completed frame and incomplete receipt'
    $events=Join-Path $temporary 'events.jsonl'
    $valid='{"schema_version":1,"sequence":1,"utc":"2026-01-01T00:00:00Z","qpc":10,"qpc_frequency":10000000,"event":"stage"}'
    [IO.File]::WriteAllText($events,$valid+"`n")
    $journal=Read-NativeProjectEvents $events
    Assert-Capture ($journal.status -eq 'observed' -and $journal.events.Count -eq 1) 'Opt-in event journal lost data'
    [IO.File]::WriteAllText($events,$valid)
    Assert-CaptureThrows {Read-NativeProjectEvents $events} 'incomplete'
    [IO.File]::WriteAllText($events,$valid+"`n"+$valid+"`n")
    Assert-CaptureThrows {Read-NativeProjectEvents $events} 'contract'
    [IO.File]::WriteAllText($events,('x'*(128KB+1)))
    Assert-CaptureThrows {Read-NativeProjectEvents $events} '128 KiB'
    $csv=Join-Path $temporary 'pacing.csv'
    $trace = "ProcessID,SwapChainAddress,CPUFrameTime,CPUStartQPC,PresentRuntime,PresentMode`n42,0x1,5,100,DXGI,Composed`n42,0x1,16,200,DXGI,Composed`n42,0x1,100,300,DXGI,Composed`n"
    New-NativeDesktopPrivateFile $csv ([Text.Encoding]::UTF8.GetBytes($trace))
    $summary=Get-NativePacingSummary $csv 42
    Assert-Capture ($summary.rows -eq 3 -and $summary.swap_chains[0].cpu_frame_ms_max -eq 100 -and
        $summary.swap_chains[0].hitches_over_50ms -eq 1 -and $null -eq $summary.swap_chains[0].cpu_frame_ms_p95 -and $null -eq $summary.event_loss) 'Pacing summary invented percentile/loss or missed a hitch'
    Assert-CaptureThrows {Get-NativePacingSummary $csv 43} 'outside the pinned'
    [IO.File]::WriteAllText($csv,"ProcessID,SwapChainAddress,CPUFrameTime,CPUStartQPC`n42,0x1,5,200`n42,0x1,5,100`n")
    Assert-CaptureThrows {Get-NativePacingSummary $csv 42} 'QPC order'
    [IO.File]::WriteAllText($csv,"ProcessID,SwapChainAddress,CPUFrameTime,CPUStartQPC`n")
    Assert-CaptureThrows {Get-NativePacingSummary $csv 42} 'No presentation'
    $header="ProcessID,SwapChainAddress,CPUFrameTime,CPUStartQPC`n"
    $unavailable="42,0x1,,100`n42,0x1,0,200`n"
    [IO.File]::WriteAllText($csv,$header+$unavailable)
    Assert-CaptureThrows {Get-NativePacingSummary $csv 42} 'No usable CPU frame intervals'
    [IO.File]::WriteAllText($csv,$header+$unavailable+"42,0x1,16,300`n")
    $partial=Get-NativePacingSummary $csv 42
    Assert-Capture ($partial.rows -eq 3 -and $partial.unavailable_cpu_intervals -eq 2 -and
        $partial.swap_chains[0].samples -eq 1 -and $partial.swap_chains[0].cpu_frame_ms_max -eq 16) 'Partial timings lost their sample or unavailable count'
    $owned=Join-Path $temporary 'owned.json'
    Write-NativeEvidenceJson $owned @{value=1}
    Write-NativeEvidenceJson $owned @{value=2}
    Assert-Capture ((Read-NativeHeldJson $owned).value -eq 2) 'Private evidence publication lost ownership or atomic replacement'
    Write-Host 'Windows GPU capture, event-journal and pacing offline contracts passed.'
} catch {
    Write-Host $_.ScriptStackTrace
    throw
} finally {
    $full=[IO.Path]::GetFullPath($temporary);$root=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
    if(-not $full.StartsWith($root,[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe capture test cleanup'}
    Remove-Item -LiteralPath $full -Recurse -Force
}
