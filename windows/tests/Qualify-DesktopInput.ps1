<# LIVE, opt-in source qualification. Opens one disposable WinForms fixture.
Run only on the parent's authorized interactive desktop. Not an offline test.
No setup, control transitions, installed-source refresh or permission changes.
#>
param([Parameter(Mandatory=$true)][string]$EvidenceDirectory,[switch]$Warm,[switch]$ObservationsOnly,[switch]$RealAges,[switch]$TightTimeline)
$ErrorActionPreference='Stop'
if($PSVersionTable.PSVersion.Major -eq 5){$env:PSModulePath=[Environment]::GetEnvironmentVariable('PSModulePath','Machine')}
. (Join-Path $PSScriptRoot '..\Desktop.Common.ps1')
Assert-NativeDesktopPath $EvidenceDirectory
if(Test-Path -LiteralPath $EvidenceDirectory){throw 'Evidence directory must be NEW'}
New-NativeDesktopPrivateDirectory $EvidenceDirectory
$fixture=Join-Path $EvidenceDirectory 'fixture.ps1'
$events=Join-Path $EvidenceDirectory 'events.txt'
$literal=$events.Replace("'","''")
[IO.File]::WriteAllText($fixture,@"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
`$form=New-Object Windows.Forms.Form
`$form.Text='Basaltwater disposable input probe'
`$form.Width=640;`$form.Height=480;`$form.KeyPreview=`$true
`$held=New-Object 'Collections.Generic.HashSet[string]'
`$form.Add_KeyDown({param(`$s,`$e) `$null=`$held.Add([string]`$e.KeyCode);[IO.File]::AppendAllText('$literal',('down:'+`$e.KeyCode+"``n"))})
`$form.Add_KeyUp({param(`$s,`$e) `$null=`$held.Remove([string]`$e.KeyCode);[IO.File]::AppendAllText('$literal',('up:'+`$e.KeyCode+"``n"))})
`$form.Add_MouseDown({param(`$s,`$e) [IO.File]::AppendAllText('$literal',('mouse-down:'+`$e.Button+"``n"))})
`$form.Add_MouseUp({param(`$s,`$e) `$point=`$form.PointToScreen(`$e.Location);[IO.File]::AppendAllText('$literal',('mouse-up:'+`$e.Button+':'+`$point.X+','+`$point.Y+"``n"))})
`$form.Add_MouseWheel({param(`$s,`$e) [IO.File]::AppendAllText('$literal',('wheel:'+`$e.Delta+"``n"))})
`$timer=New-Object Windows.Forms.Timer
`$timer.Interval=20
`$timer.Add_Tick({if(`$held.Count){[IO.File]::AppendAllText('$literal',('tick:'+((@(`$held)|Sort-Object)-join '+')+"``n"))}})
`$timer.Start()
[Windows.Forms.Application]::Run(`$form)
`$timer.Dispose();`$form.Dispose()
"@)
$process=$null;$ownedWorker=$false;$holder=$null
function Invoke-Probe([string[]]$Arguments){
    $reply=Invoke-NativeDesktopCli $Arguments
    [IO.File]::AppendAllText((Join-Path $EvidenceDirectory 'receipts.jsonl'),(($reply|ConvertTo-Json -Depth 20 -Compress)+"`n"))
    if(-not $reply.ok){throw ($reply|ConvertTo-Json -Depth 15 -Compress)}
    return $reply
}
function Get-ProbeTarget {
    $inventory=Invoke-Probe @('windows')
    $matches=@($inventory.result.windows|Where-Object {$_.pid -eq $process.Id})
    if($matches.Count -ne 1){throw 'Expected one owned fixture window'}
    return @{window=$matches[0];generation=$inventory.generation}
}
function Get-ProbeInputArgs {
    $target=Get-ProbeTarget;$w=$target.window
    return @('--window',[string]$w.id,'--identity',$w.identity,'--generation',$target.generation,'--width',[string]$w.width,'--height',[string]$w.height)
}
function Save-ProbeReply($Reply){[IO.File]::AppendAllText((Join-Path $EvidenceDirectory 'receipts.jsonl'),(($Reply|ConvertTo-Json -Depth 20 -Compress)+"`n"))}
function New-ProbeCapture([int]$Age=60){
    $target=Get-ProbeTarget
    $reply = Invoke-Probe @('screenshot','--window',[string]$target.window.id,'--identity',$target.window.identity,'--generation',$target.generation,'--output',(Join-Path $EvidenceDirectory ([guid]::NewGuid().ToString('N')+'.png')),'--max-age-seconds',[string]$Age)
    $actual = (Get-FileHash -LiteralPath $reply.result.output -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($reply.result.artifact_sha256 -cne $actual) { throw 'Capture receipt does not match the saved PNG hash' }
    return $reply
}
function Get-CapturedKeyArgs($Capture){
    $w=$target.window
    return @('input','key','--key','B','--window',[string]$w.id,'--identity',$w.identity,'--generation',$Capture.generation,'--width',[string]$w.width,'--height',[string]$w.height,'--observation',$Capture.result.observation_id)
}
function Assert-ProbeRefusal([string[]]$Arguments,[string]$Pattern){
    $reply=Invoke-NativeDesktopCli $Arguments;Save-ProbeReply $reply
    if($reply.ok -or $reply.error -notmatch $Pattern){throw ('Expected '+$Pattern+': '+($reply|ConvertTo-Json -Depth 12 -Compress))}
}
try {
    $worker=Invoke-Probe @('worker','status')
    if(-not $worker.result.cleanup_confirmed){throw 'Stop the existing desktop worker before qualification'}
    if($Warm){$null=Invoke-Probe @('worker','start');$ownedWorker=$true}
    $start=[Diagnostics.ProcessStartInfo]::new()
    $start.FileName=Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $code="& '"+$fixture.Replace("'","''")+"'"
    $start.Arguments='-NoProfile -ExecutionPolicy Bypass -Sta -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
    $start.UseShellExecute=$false;$start.CreateNoWindow=$true;$start.WindowStyle='Hidden';$start.EnvironmentVariables.Remove('PSModulePath')
    $process=[Diagnostics.Process]::Start($start)
    $startup=[Diagnostics.Stopwatch]::StartNew();$target=$null
    while(-not $target -and $startup.Elapsed.TotalSeconds -lt 15){
        if($process.HasExited){throw 'Fixture exited before window readiness'}
        try{$target=Get-ProbeTarget}catch{Start-Sleep -Milliseconds 100}
    }
    if(-not $target){throw 'Fixture window startup timed out'}
    $null=Invoke-Probe @('window','focus','--window',[string]$target.window.id,'--identity',$target.window.identity,'--generation',$target.generation)
    if(-not $ObservationsOnly){
    $null=Invoke-Probe (@('input','hold')+(Get-ProbeInputArgs)+@('--key','W+J','--milliseconds','400'))
    $timeline=if($TightTimeline){@('--timeline','0:W+J,150:J,250:A+J,350:-','--milliseconds','450')}else{@('--timeline','0:W+J,300:J,600:A+J,900:-','--milliseconds','1200')}
    $null=Invoke-Probe (@('input','hold')+(Get-ProbeInputArgs)+$timeline)
    $null=Invoke-Probe (@('input','move')+(Get-ProbeInputArgs)+@('--x','100','--y','100'))
    foreach($button in @('left','right','middle')){
        $before=Get-ProbeTarget
        $null=Invoke-Probe (@('input','hold')+(Get-ProbeInputArgs)+@('--button',$button,'--x','100','--y','100','--to-x','200','--to-y','100','--milliseconds','400'))
        $expected='mouse-up:'+$button.Substring(0,1).ToUpper()+$button.Substring(1)+':'+($before.window.x+200)+','+($before.window.y+100)
        if(-not [IO.File]::ReadAllText($events).Contains($expected)){throw "Drag endpoint effect missing: $expected"}
        $null=Invoke-Probe (@('input','move')+(Get-ProbeInputArgs)+@('--x','100','--y','100'))
    }
    $null=Invoke-Probe (@('input','wheel')+(Get-ProbeInputArgs)+@('--x','100','--y','100','--delta','120','--observe',(Join-Path $EvidenceDirectory 'post.png')))
    $log=[IO.File]::ReadAllText($events)
    foreach($expected in @('tick:J+W','tick:J','tick:A+J','up:W','up:J','up:A','mouse-down:Left','mouse-up:Left','mouse-down:Right','mouse-up:Right','mouse-down:Middle','mouse-up:Middle','wheel:120')){
        if(-not $log.Contains($expected)){throw "Fixture effect missing: $expected"}
    }
    }
    $target=Get-ProbeTarget
    $capture=New-ProbeCapture
    $saved=Join-Path $EvidenceDirectory 'mapped-capture.json';Write-NativeHeldJson $saved $capture 65536
    $mapped=Invoke-Probe @('map-point','--capture',$saved,'--view-width','320','--view-height','240','--x','160','--y','120')
    if($mapped.x -ne 320 -or $mapped.y -ne 240 -or $mapped.authority_renewed){throw 'Native receipt mapping differs from the presented image'}
    $afterPath = Join-Path $EvidenceDirectory 'mapped-follow-up.png'
    $post=Invoke-Probe ((Get-CapturedKeyArgs $capture)+@('--observe',$afterPath))
    if ($post.result.observation_id -cne $capture.result.observation_id -or
        $post.result.input_id -cne $post.result.observation.last_input.input_id -or
        $post.result.observation.artifact_sha256 -cne (Get-FileHash -LiteralPath $afterPath -Algorithm SHA256).Hash.ToLowerInvariant()) {
        throw 'Live input or follow-up receipt lost the inspected capture or exact PNG'
    }
    Assert-ProbeRefusal (Get-CapturedKeyArgs $capture) 'latest successful'
    $old=New-ProbeCapture;$capture=New-ProbeCapture
    Assert-ProbeRefusal (Get-CapturedKeyArgs $old) 'latest successful'
    $null=Invoke-Probe (Get-CapturedKeyArgs $capture)
    # Real owned lease, observed through fixture ticks before capture admission.
    $selectors=Get-ProbeInputArgs;$eventOffset=[IO.File]::ReadAllText($events).Length
    $arguments=@('desktop','input','hold')+$selectors+@('--key','W','--milliseconds','5000')
    $entry=Join-Path (Split-Path -Parent $PSScriptRoot) 'Invoke-Basaltw.ps1'
    $literalArgs=@($arguments|ForEach-Object {"'"+$_.Replace("'","''")+"'"}) -join ','
    $command='$probeArguments=@('+$literalArgs+"); & '"+$entry.Replace("'","''")+"' @probeArguments"
    $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $holder=Start-Process powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand',$encoded) -WindowStyle Hidden -RedirectStandardOutput (Join-Path $EvidenceDirectory 'monitor-hold.json') -RedirectStandardError (Join-Path $EvidenceDirectory 'monitor-hold.stderr') -PassThru
    $clock=[Diagnostics.Stopwatch]::StartNew()
    while(-not [IO.File]::ReadAllText($events).Substring($eventOffset).Contains('tick:W')){
        if($holder.HasExited -or $clock.Elapsed.TotalSeconds -gt 25){throw 'Monitoring probe never observed an actual held key'}
        Start-Sleep -Milliseconds 20
    }
    # Starting the lease invalidates the pre-input window inventory.
    $monitorSelectors=Get-ProbeInputArgs
    $monitor=Invoke-Probe (@('screenshot')+$monitorSelectors[0..5]+@('--output',(Join-Path $EvidenceDirectory 'during-hold.png')))
    if($null -ne $monitor.result.observation_id -or $monitor.result.observation_unavailable_reason -ne 'keyboard-lease-active-or-uncertain'){throw 'Capture during actual held input minted authority'}
    if(-not $holder.WaitForExit(30000)){throw 'Monitoring hold did not settle'}
    $held=Get-Content -LiteralPath (Join-Path $EvidenceDirectory 'monitor-hold.json') -Raw|ConvertFrom-Json
    if(-not $held.receipt.cleanup_confirmed){throw 'Monitoring hold cleanup unconfirmed'}
    if(-not $held.ok){throw "Monitoring hold stopped early: $($held.result.reason) $($held.error)"}
    $holder.Dispose();$holder=$null
    $capture=New-ProbeCapture
    if(-not $capture.result.observation_id){throw 'Fresh capture after cleanup did not regain authority'}
    $null=Invoke-Probe (Get-CapturedKeyArgs $capture)
    if($RealAges){
        $capture=New-ProbeCapture
        Start-Sleep -Seconds 61
        $null=Invoke-Probe @('status')
        Assert-ProbeRefusal (Get-CapturedKeyArgs $capture) 'expired'
        $capture=New-ProbeCapture 180
        Start-Sleep -Seconds 91
        $null=Invoke-Probe (Get-CapturedKeyArgs $capture)
        $capture=New-ProbeCapture
        if($capture.result.capture_freshness.max_age_seconds -ne 60){throw 'Explicit lifetime leaked into the next capture'}
    }
    Write-NativeHeldJson (Join-Path $EvidenceDirectory 'qualification.json') @{complete=$true;warm=[bool]$Warm;observations_only=[bool]$ObservationsOnly;real_ages=[bool]$RealAges;scope='WinForms effects, single-use/supersession/mapping, actual held-input monitoring and cleanup; optional real expiry'}
    Write-Host 'Source WinForms input and observation effects passed. SDL, physical takeover and fullscreen remain separate.'
} finally {
    if($holder -and -not $holder.HasExited){$holder.Kill();$null=$holder.WaitForExit(3000)}
    if($holder){$holder.Dispose()}
    try{if($ownedWorker){$null=Invoke-Probe @('worker','stop')}}finally{
        if($process){
            if(-not $process.HasExited){$null=$process.CloseMainWindow();if(-not $process.WaitForExit(3000)){$process.Kill();$null=$process.WaitForExit(3000)}}
            $process.Dispose()
        }
    }
}
