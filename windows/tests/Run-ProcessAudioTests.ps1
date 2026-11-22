<# Offline contracts. Native gate compiles and verifies identity only; never activates audio. #>
[CmdletBinding()]
param([switch]$NativeBuild)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '..\Development.Common.ps1')
. (Join-Path $PSScriptRoot '..\Development.Diagnostics.ps1')
. (Join-Path $PSScriptRoot '..\Audio.Capture.ps1')
function Assert-True([bool]$Value,[string]$Message) { if (-not $Value) { throw $Message } }
function Assert-Throws([scriptblock]$Action,[string]$Pattern) {
    $failure = $null
    try { & $Action | Out-Null } catch { $failure = $_ }
    Assert-True ($null -ne $failure -and $failure.Exception.Message -match $Pattern) "Expected $Pattern; got $failure"
}
$temp = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-process-audio-' + [guid]::NewGuid().ToString('N'))
$savedLocal = $env:LOCALAPPDATA
New-Item -ItemType Directory -Path $temp | Out-Null
try {
    $env:LOCALAPPDATA = $temp
    $id = 'a' * 32; $directory = Get-DevelopmentDirectory $id
    Initialize-DevelopmentDirectory $directory
    $project = Join-Path $temp 'project'; New-Item -ItemType Directory -Path $project | Out-Null
    $helper = Join-Path $temp 'mock.exe'; [IO.File]::WriteAllText($helper,'mock')
    $record = [pscustomobject]@{ Id = $id; Project = $project; State = 'running'; CompletedUtc = $null;
        OwnerSid = Get-DevelopmentSid; SessionId = [Diagnostics.Process]::GetCurrentProcess().SessionId;
        JobName = ('Local\BasaltwaterDevelopment-' + ('b'*32) + '-job'); TrackChildren = $true }
    function Get-DevelopmentStatus($Id) { $record }
    $target = @{ ProcessId = 42; CreatedFileTimeUtc = '1'; State = 'created'; ExitCode = $null }
    Write-DevelopmentJson (Join-Path $directory 'target.json') $target
    $script:calls = 0; $script:expectedCaller = $PID; $script:expectedCreation = $null
    function Invoke-ProcessAudioNative($Helper,$Arguments,$CancellationToken) {
        $script:calls++
        Assert-True ($Arguments[0] -eq '--capture' -and $Arguments[1] -eq '42' -and $Arguments[2] -eq '1' -and
            $Arguments[3] -ceq $record.JobName -and $Arguments[4] -eq '100') 'Helper lost pinned identity, Job or duration'
        Assert-True (-not $CancellationToken.IsCancellationRequested) 'Cancelled request launched'
        Assert-True ($Arguments[6] -eq [string]$script:expectedCaller -and
            [long]$Arguments[7] -gt 0 -and ($null -eq $script:expectedCreation -or
            $Arguments[7] -eq [string]$script:expectedCreation)) 'Original public caller identity was replaced by the intermediary'
        [IO.File]::WriteAllBytes($Arguments[5], [byte[]]::new(44))
        [pscustomobject]@{ ok = $true; reason = 'completed'; frames = 0 }
    }
    $invoke = { Invoke-DevelopmentProcessAudio -Id $id -Project $project -Helper $helper -OptIn -Seconds 0.1 }
    Assert-Throws { Invoke-DevelopmentProcessAudio -Id $id -Project $project -Helper $helper } 'OptIn'
    foreach ($duration in @(0.09,30.01,[double]::NaN,[double]::PositiveInfinity)) {
        Assert-Throws { Invoke-DevelopmentProcessAudio -Id $id -Project $project -Helper $helper -OptIn -Seconds $duration } 'range|duration|greater|less|validate'
    }
    Assert-Throws { Invoke-DevelopmentProcessAudio -Id $id -Project $project -Helper ($helper+'missing') -OptIn } 'helper missing'
    $cancel = [Threading.CancellationTokenSource]::new(); $cancel.Cancel()
    Assert-Throws { Invoke-DevelopmentProcessAudio -Id $id -Project $project -Helper $helper -OptIn -CancellationToken $cancel.Token } 'cancelled'
    $cancel.Dispose()
    $cancel = [Threading.CancellationTokenSource]::new()
    Add-Type -TypeDefinition 'namespace Basaltwater.Audio { public static class Cancellation { public static System.Threading.CancellationTokenRegistration Register(System.Threading.CancellationToken token, System.Threading.EventWaitHandle signal) { return token.Register(() => signal.Set()); } } }'
    $event = [Threading.EventWaitHandle]::new($false,[Threading.EventResetMode]::ManualReset)
    $registration = [Basaltwater.Audio.Cancellation]::Register($cancel.Token,$event)
    try { $cancel.Cancel(); Assert-True ($event.WaitOne(0)) 'Cancellation callback failed to signal native event' }
    finally { $registration.Dispose(); $event.Dispose(); $cancel.Dispose() }
    foreach ($state in @('queued','cleanup-uncertain','interrupted','failed')) {
        $record.State = $state; Assert-Throws $invoke 'live current-account'
    }
    $record.State = 'running'; $record.CompletedUtc = 'terminal'; Assert-Throws $invoke 'live current-account'; $record.CompletedUtc = $null
    $record.SessionId++; Assert-Throws $invoke 'current-account/session'; $record.SessionId--
    $savedSid = $record.OwnerSid; $record.OwnerSid = 'S-1-0-0'; Assert-Throws $invoke 'current-account/session'; $record.OwnerSid = $savedSid
    $record.Project = $temp; Assert-Throws $invoke 'exact retained project'; $record.Project = $project
    $target.State = 'exited'; $target.ExitCode = 0; Write-DevelopmentJson (Join-Path $directory 'target.json') $target
    Assert-Throws $invoke 'exited'
    $target.State = 'created'; $target.ExitCode = $null; Write-DevelopmentJson (Join-Path $directory 'target.json') $target
    $member = @{ ProcessId = 42; CreatedFileTimeUtc = '1'; Token = '42:1'; ParentToken = $null;
        Role = 'child'; State = 'observed'; ExitCode = $null; ExitCodeHex = $null }
    Write-DevelopmentJson (Join-Path $directory 'members.json') @{ Version = 1; Coverage = 'partial'; Overflow = $false; Error = $null; Members = @($member) }
    Assert-Throws { Get-DevelopmentEvidenceTarget $id $project '42:2' } 'stale, unrelated'
    Assert-Throws { Get-DevelopmentEvidenceTarget $id $project '43:1' } 'stale, unrelated'
    $selected = Get-DevelopmentEvidenceTarget $id $project '42:1'
    Assert-True ($selected.Target.Token -ceq '42:1') 'Retained child selection failed'
    Assert-True ($script:calls -eq 0) 'Rejected admission launched a helper'
    $result = & $invoke
    Assert-True ($result.Capture.ok -and $result.TargetToken -ceq '42:1' -and (Test-Path -LiteralPath $result.MetadataPath)) 'Capture metadata not retained'
    Assert-Throws { Invoke-DevelopmentProcessAudio -Id $id -Project $project -Helper $helper -OptIn -CallerProcessId 2147483646 } 'creation identity'
    $script:expectedCaller=2147483646; $script:expectedCreation=1
    $originalCaller=Invoke-DevelopmentProcessAudio -Id $id -Project $project -Helper $helper -OptIn -Seconds 0.1 -CallerProcessId $script:expectedCaller -CallerCreatedFileTimeUtc $script:expectedCreation
    Assert-True ($originalCaller.Caller.ProcessId -eq $script:expectedCaller -and $originalCaller.Caller.CreatedFileTimeUtc -eq '1') 'Original caller provenance missing from receipt'
    function Invoke-ProcessAudioNative { throw 'fixture cancelled' }
    $failure = & $invoke
    Assert-True (-not $failure.Capture.ok -and (Test-Path -LiteralPath $failure.MetadataPath)) 'Failed helper lost receipt'
    $folder = Join-Path $directory 'audio'
    1..6 | ForEach-Object { [IO.File]::WriteAllText((Join-Path $folder "fixture-$_.json"),'{}') }
    Assert-Throws $invoke 'retention limit'
    # Size budget without writing large data: sparse temporary file, no audio.
    Get-ChildItem -LiteralPath $folder -File | Remove-Item -Force
    $stream = [IO.File]::Create((Join-Path $folder 'budget.wav'))
    try { $stream.SetLength(42MB+1) } finally { $stream.Dispose() }
    Assert-Throws $invoke 'retention limit'
    if ($NativeBuild) {
        $exe = & (Join-Path $PSScriptRoot '..\Audio.Capture.Build.ps1') -OutputDirectory (Join-Path $temp 'build') | Select-Object -Last 1
        $fixtureWave = Join-Path $temp 'offline.wav'
        $native = & $exe --self-test $fixtureWave | ConvertFrom-Json
        Assert-True ($LASTEXITCODE -eq 0 -and $native.ok) 'Native offline summary gate failed'
        $bytes = [IO.File]::ReadAllBytes($fixtureWave)
        Assert-True ($bytes.Length -eq 52 -and [Text.Encoding]::ASCII.GetString($bytes,0,4) -ceq 'RIFF' -and
            [BitConverter]::ToUInt32($bytes,4) -eq 44 -and [BitConverter]::ToUInt32($bytes,40) -eq 8 -and
            [BitConverter]::ToInt16($bytes,46) -eq -32768) 'Native WAV header, lengths or sample payload incorrect'
        $self = [Diagnostics.Process]::GetCurrentProcess()
        try { $creation = $self.StartTime.ToUniversalTime().ToFileTimeUtc().ToString() } finally { $self.Dispose() }
        $base = @('--verify',[string]$PID,$creation,$record.JobName,'100',(Join-Path $temp 'never.wav'),[string]$PID,$creation,'-')
        $reply = & $exe @base | ConvertFrom-Json
        Assert-True (-not $reply.ok -and $reply.reason -eq 'job_membership_mismatch') 'Native unrelated process admitted'
        $base[2] = '1'; $reply = & $exe @base | ConvertFrom-Json
        Assert-True (-not $reply.ok -and $reply.reason -eq 'stale_identity') 'Native stale creation admitted'
        $base[4] = '30001'; $reply = & $exe @base | ConvertFrom-Json
        Assert-True (-not $reply.ok -and $reply.reason -eq 'bounds') 'Native duration limit bypassed'
        $signalName = 'Local\BasaltwaterAudioTest-' + [guid]::NewGuid().ToString('N')
        $signal = [Threading.EventWaitHandle]::new($true,[Threading.EventResetMode]::ManualReset,$signalName)
        try {
            $base[4] = '100'; $base[8] = $signalName
            $reply = & $exe @base | ConvertFrom-Json
            Assert-True (-not $reply.ok -and $reply.reason -eq 'cancelled') 'Native pre-cancel ignored'
        } finally { $signal.Dispose() }
        Assert-True (-not (Test-Path -LiteralPath (Join-Path $temp 'never.wav'))) 'Verification opened capture output'
    }
    Write-Output 'Process audio offline contracts passed (no audio activation).'
} finally {
    $env:LOCALAPPDATA = $savedLocal
    # temp was created above with a fixed prefix, outside the checkout.
    if ([IO.Path]::GetFullPath($temp).StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()),[StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $temp -Recurse -Force
    }
}
