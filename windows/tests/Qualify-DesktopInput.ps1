<# LIVE, opt-in source qualification. Opens one disposable WinForms fixture.
Run only on the parent's authorized interactive desktop. Not an offline test.
No setup, control transitions, installed-source refresh or permission changes.
#>
param([Parameter(Mandatory=$true)][string]$EvidenceDirectory,[switch]$Warm)
$ErrorActionPreference='Stop'
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
`$form.Add_MouseUp({param(`$s,`$e) [IO.File]::AppendAllText('$literal',('mouse-up:'+`$e.Button+"``n"))})
`$form.Add_MouseWheel({param(`$s,`$e) [IO.File]::AppendAllText('$literal',('wheel:'+`$e.Delta+"``n"))})
`$timer=New-Object Windows.Forms.Timer
`$timer.Interval=20
`$timer.Add_Tick({if(`$held.Count){[IO.File]::AppendAllText('$literal',('tick:'+((@(`$held)|Sort-Object)-join '+')+"``n"))}})
`$timer.Start()
[Windows.Forms.Application]::Run(`$form)
`$timer.Dispose();`$form.Dispose()
"@)
$process=$null;$ownedWorker=$false
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
try {
    $worker=Invoke-Probe @('worker','status')
    if(-not $worker.result.cleanup_confirmed){throw 'Stop the existing desktop worker before qualification'}
    if($Warm){$null=Invoke-Probe @('worker','start');$ownedWorker=$true}
    $start=[Diagnostics.ProcessStartInfo]::new()
    $start.FileName=Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $code="& '"+$fixture.Replace("'","''")+"'"
    $start.Arguments='-NoProfile -Sta -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
    $start.UseShellExecute=$false;$start.CreateNoWindow=$true;$start.WindowStyle='Hidden';$start.EnvironmentVariables.Remove('PSModulePath')
    $process=[Diagnostics.Process]::Start($start)
    $startup=[Diagnostics.Stopwatch]::StartNew();$target=$null
    while(-not $target -and $startup.Elapsed.TotalSeconds -lt 15){
        if($process.HasExited){throw 'Fixture exited before window readiness'}
        try{$target=Get-ProbeTarget}catch{Start-Sleep -Milliseconds 100}
    }
    if(-not $target){throw 'Fixture window startup timed out'}
    $null=Invoke-Probe @('window','focus','--window',[string]$target.window.id,'--identity',$target.window.identity,'--generation',$target.generation)
    $null=Invoke-Probe (@('input','hold')+(Get-ProbeInputArgs)+@('--key','W+J','--milliseconds','400'))
    $null=Invoke-Probe (@('input','hold')+(Get-ProbeInputArgs)+@('--timeline','0:W+J,150:J,250:A+J,350:-','--milliseconds','450'))
    $null=Invoke-Probe (@('input','move')+(Get-ProbeInputArgs)+@('--x','100','--y','100'))
    foreach($button in @('left','right','middle')){
        $null=Invoke-Probe (@('input','hold')+(Get-ProbeInputArgs)+@('--button',$button,'--x','100','--y','100','--to-x','200','--to-y','100','--milliseconds','400'))
        $null=Invoke-Probe (@('input','move')+(Get-ProbeInputArgs)+@('--x','100','--y','100'))
    }
    $null=Invoke-Probe (@('input','wheel')+(Get-ProbeInputArgs)+@('--x','100','--y','100','--delta','120','--observe',(Join-Path $EvidenceDirectory 'post.png')))
    $log=[IO.File]::ReadAllText($events)
    foreach($expected in @('tick:J+W','tick:J','tick:A+J','up:W','up:J','up:A','mouse-down:Left','mouse-up:Left','mouse-down:Right','mouse-up:Right','mouse-down:Middle','mouse-up:Middle','wheel:120')){
        if(-not $log.Contains($expected)){throw "Fixture effect missing: $expected"}
    }
    Write-Host 'Source WinForms input effects passed. SDL, hard-loss, physical takeover, fullscreen and warm-worker qualification remain separate.'
} finally {
    try{if($ownedWorker){$null=Invoke-Probe @('worker','stop')}}finally{
        if($process){
            if(-not $process.HasExited){$null=$process.CloseMainWindow();if(-not $process.WaitForExit(3000)){$process.Kill();$null=$process.WaitForExit(3000)}}
            $process.Dispose()
        }
    }
}
