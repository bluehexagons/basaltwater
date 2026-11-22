<# Opt-in live synthetic audio. Uses low-amplitude generated tones, no microphone,
   routing, device, preference or shared-application changes. PowerShell 7. #>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Output,[string]$PublicLauncher='')
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$windows=Split-Path -Parent $PSScriptRoot
. (Join-Path $windows 'AgentWorkstation.Common.ps1')
. (Join-Path $windows 'Desktop.Common.ps1')
. (Join-Path $windows 'Development.Common.ps1')
. (Join-Path $windows 'Development.Diagnostics.ps1')
. (Join-Path $windows 'Capture.Common.ps1')
. (Join-Path $windows 'Audio.Capture.ps1')
$directory=New-NativeEvidenceBundle $Output
$entry=Join-Path $windows 'Invoke-Basaltw.ps1';$tone=Join-Path $windows 'Audio.Capture.Tone.ps1'
$task=$null;$other=$null;$publicCaller=$null;$profile=$null;$otherProfile=$null;$complete=$false;$failure=$null;$cases=@{}
function Assert-Audio([bool]$Condition,[string]$Message){if(-not $Condition){throw $Message}}
function Invoke-AudioQualificationCli([string[]]$Arguments){
    $lines=& pwsh.exe -NoProfile -ExecutionPolicy Bypass -File $entry @Arguments
    $code=$LASTEXITCODE;$value=($lines | Out-String) | ConvertFrom-Json
    if($code -ne 0){throw "Qualification CLI exited $code`: $($value | ConvertTo-Json -Depth 9 -Compress)"}
    return $value
}
function Wait-ToneState([string]$Profile,[bool]$Playing){
    $clock=[Diagnostics.Stopwatch]::StartNew()
    do {
        $path=Join-Path $Profile 'tone-state.json'
        if(Test-Path -LiteralPath $path){$state=Read-NativeHeldJson $path 4096;if($state.playing -eq $Playing){return $state}}
        Start-Sleep -Milliseconds 20
    }while($clock.Elapsed.TotalSeconds -lt 10)
    throw 'Owned tone fixture did not acknowledge its state'
}
Add-Type -TypeDefinition @'
using System;
namespace Basaltwater.AudioQualification {
public static class Spectrum {
    public static double Tone(byte[] wav,double frequency) {
        if(wav.Length<48 || wav.Length>5292044 || BitConverter.ToInt16(wav,20)!=1 ||
           BitConverter.ToInt16(wav,22)!=2 || BitConverter.ToInt32(wav,24)!=44100 ||
           BitConverter.ToInt16(wav,34)!=16 || BitConverter.ToInt32(wav,40)!=wav.Length-44)
            throw new Exception("Unexpected bounded PCM WAV");
        int frames=(wav.Length-44)/4,n=Math.Min(44100,frames),start=(frames-n)/2;
        if(n<22050) throw new Exception("Insufficient observed audio for spectral qualification");
        double real=0,imaginary=0;
        for(int i=0;i<n;i++) {
            int p=44+(start+i)*4;
            double value=(BitConverter.ToInt16(wav,p)+BitConverter.ToInt16(wav,p+2))/65536.0;
            double angle=2*Math.PI*frequency*i/44100;
            real+=value*Math.Cos(angle);imaginary+=value*Math.Sin(angle);
        }
        return 2*Math.Sqrt(real*real+imaginary*imaginary)/n;
    }
}}
'@
try {
    $helper=Get-NativeEvidenceTool 'Audio.Capture'
    $project=Join-Path $directory 'project-a';$otherProfile=Join-Path $directory 'profile-b'
    New-NativeDesktopPrivateDirectory $project;New-NativeDesktopPrivateDirectory $otherProfile
    $shell=(Get-Command pwsh.exe).Source
    $task=Invoke-AudioQualificationCli @('desktop','develop','exec','--project',$project,'--test-session','--track-children','--ready-timeout','30','--timeout','150','--',$shell,'-NoProfile','-ExecutionPolicy','Bypass','-File',$tone,'-Frequency','440','-DurationSeconds','120')
    $profile=$task.TestSession.ProfilePath
    Write-NativeHeldJson (Join-Path $directory 'submitted.json') $task 65536
    $null=Invoke-AudioQualificationCli @('desktop','develop','ready',$task.Id,'--timeout','45')
    $state=Invoke-AudioQualificationCli @('desktop','develop','status',$task.Id)
    $profile=$state.TestSession.ProfilePath
    $null=Wait-ToneState $profile $true
    $start=[Diagnostics.ProcessStartInfo]::new();$start.FileName=$shell;$start.UseShellExecute=$false;$start.CreateNoWindow=$true;$start.WindowStyle='Hidden'
    $arguments=@('-NoProfile','-ExecutionPolicy','Bypass','-File',$tone,'-Frequency','997','-DurationSeconds','120','-ProfilePath',$otherProfile)
    foreach($argument in $arguments){$start.ArgumentList.Add($argument)}
    $other=[Diagnostics.Process]::Start($start);$null=Wait-ToneState $otherProfile $true
    $capture=Invoke-AudioQualificationCli @('desktop','audio','--task',$task.Id,'--project',$project,'--helper',$helper,'--seconds','2')
    $wave=[IO.File]::ReadAllBytes($capture.result.WavPath)
    $own=[Basaltwater.AudioQualification.Spectrum]::Tone($wave,440)
    $unrelated=[Basaltwater.AudioQualification.Spectrum]::Tone($wave,997)
    $cases.owned_and_excluded=@{own_amplitude=$own;unrelated_amplitude=$unrelated;capture=$capture.result.MetadataPath;peak=$capture.result.Capture.peakNormalized}
    Assert-Audio ($own -gt 0.001 -and $unrelated -lt ($own*0.02+0.00001)) 'Owned tone missing or unrelated tone leaked into process-tree capture'
    [IO.File]::WriteAllText((Join-Path $profile 'silence'),'silence')
    $null=Wait-ToneState $profile $false;$null=Wait-ToneState $otherProfile $true
    $silence=Invoke-AudioQualificationCli @('desktop','audio','--task',$task.Id,'--project',$project,'--helper',$helper,'--seconds','2')
    $silentWave=[IO.File]::ReadAllBytes($silence.result.WavPath)
    $silentOwn=[Basaltwater.AudioQualification.Spectrum]::Tone($silentWave,440)
    $silentOther=[Basaltwater.AudioQualification.Spectrum]::Tone($silentWave,997)
    $cases.silence_with_unrelated_playing=@{own_amplitude=$silentOwn;unrelated_amplitude=$silentOther;capture=$silence.result.MetadataPath;frames=$silence.result.Capture.frames}
    Assert-Audio ($silentOwn -lt 0.0001 -and $silentOther -lt 0.0001 -and -not $other.HasExited) 'Muted owned fixture captured unrelated sound'
    Remove-Item -LiteralPath (Join-Path $profile 'silence');$null=Wait-ToneState $profile $true
    $cancel=[Threading.CancellationTokenSource]::new();$cancel.CancelAfter(2000)
    try {$partial=Invoke-DevelopmentProcessAudio -Id $task.Id -Project $project -Helper $helper -OptIn -Seconds 10 -CancellationToken $cancel.Token}finally{$cancel.Dispose()}
    $cases.cancellation=@{capture=$partial.MetadataPath;reason=$partial.Capture.reason;frames=$partial.Capture.frames}
    Assert-Audio (-not $partial.Capture.ok -and $partial.Capture.reason -eq 'cancelled' -and $partial.Capture.frames -gt 0) 'Native cancellation did not retain observed partial audio'
    # Kill only the outer public CLI, leaving its intermediary alive. The native
    # helper must watch that original caller rather than the intermediary.
    $callerStart=[Diagnostics.ProcessStartInfo]::new();$callerStart.FileName=$shell;$callerStart.UseShellExecute=$false;$callerStart.CreateNoWindow=$true;$callerStart.WindowStyle='Hidden'
    $prefix=@('-NoProfile','-ExecutionPolicy','Bypass','-File',$entry)
    if($PublicLauncher){Assert-NativeDesktopPath $PublicLauncher;$callerStart.FileName=$PublicLauncher;$prefix=@()}
    foreach($argument in ($prefix+@('desktop','audio','--task',$task.Id,'--project',$project,'--helper',$helper,'--seconds','30'))){$callerStart.ArgumentList.Add($argument)}
    $publicCaller=[Diagnostics.Process]::Start($callerStart)
    $native=$null;$clock=[Diagnostics.Stopwatch]::StartNew()
    while(-not $native -and $clock.Elapsed.TotalSeconds -lt 20){
        $candidate=@(Get-CimInstance Win32_Process -Filter "Name = 'Audio.Capture.exe'" -OperationTimeoutSec 2 | Where-Object { $_.ExecutablePath -ceq $helper -and $_.CommandLine.Contains([string]$publicCaller.Id) })
        if($candidate.Count -eq 1){$native=Get-Process -Id $candidate[0].ProcessId -ErrorAction Stop;break}
        if($publicCaller.HasExited){throw 'Public audio caller exited before helper admission'}
        Start-Sleep -Milliseconds 50
    }
    Assert-Audio ($null -ne $native) 'Native audio helper did not become observable for caller-loss probe'
    $publicCaller.Kill();Assert-Audio ($publicCaller.WaitForExit(3000)) 'Owned public caller did not stop'
    try {Assert-Audio ($native.WaitForExit(5000)) 'Recording survived the original public caller';$cases.original_caller_loss=@{native_exit=$native.ExitCode;bounded_exit=$true}}finally{$native.Dispose()}
    $complete=$true
}catch{$failure=$_.Exception.Message}
finally {
    if($publicCaller){if(-not $publicCaller.HasExited){$publicCaller.Kill();$null=$publicCaller.WaitForExit(3000)};$publicCaller.Dispose()}
    if($task){
        try {
            if($profile -and (Test-Path -LiteralPath $profile)){[IO.File]::WriteAllText((Join-Path $profile 'stop'),'stop')}
            $settled=Invoke-AudioQualificationCli @('desktop','develop','wait',$task.Id,'--timeout','45')
            Write-NativeHeldJson (Join-Path $directory 'settled.json') $settled 65536
            Assert-Audio ($settled.Receipt.State -eq 'complete' -and $settled.Receipt.TestSession.ProfileCleanup -eq 'removed') 'Owned audio fixture session did not settle normally'
        }catch{$complete=$false;$failure='Cleanup: '+$_.Exception.Message}
    }
    if($other){
        if(-not $other.HasExited){[IO.File]::WriteAllText((Join-Path $otherProfile 'stop'),'stop');if(-not $other.WaitForExit(5000)){$other.Kill();$null=$other.WaitForExit(3000);$complete=$false;$failure='Unrelated owned fixture required forced cleanup'}}
        $other.Dispose()
    }
    Write-NativeHeldJson (Join-Path $directory 'qualification.json') @{schema_version=1;complete=$complete;error=$failure;cases=$cases;tested_source=(git -C (Split-Path -Parent $windows) rev-parse HEAD);dirty=$true;finished_utc=[DateTime]::UtcNow.ToString('o');limitations=@('Synthetic process-loopback tones; no physical listening or device recovery')} 262144
}
if(-not $complete){throw $failure}
Write-Host "Process audio native qualification passed; private evidence: $directory"
