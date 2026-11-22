<# Opt-in integration seam. Dot-source after Development.Common.ps1 and
   Development.Diagnostics.ps1. No desktop worker, setup, or audio routing. #>
. (Join-Path $PSScriptRoot 'Evidence.Target.ps1')
function Invoke-ProcessAudioNative([string]$Helper,[string[]]$Arguments,[Threading.CancellationToken]$CancellationToken) {
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $Helper; $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    $process = [Diagnostics.Process]::new(); $process.StartInfo = $info
    $started = $false
    try {
        if ($CancellationToken.IsCancellationRequested) { throw 'Audio cancelled before launch' }
        $started = $process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync(); $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(44000)) { $process.Kill(); if (-not $process.WaitForExit(2000)) { throw 'Audio helper cleanup uncertain' }; throw 'Audio helper exceeded deadline' }
        if (-not $stdout.Wait(2000) -or -not $stderr.Wait(2000)) { throw 'Audio helper streams did not settle' }
        if ($stdout.Result.Length -gt 8192 -or $stderr.Result.Length -gt 1024) { throw 'Audio helper response exceeds bound' }
        if (-not $stdout.Result) { throw ('Audio helper terminated without receipt, exit ' + $process.ExitCode) }
        $report = $stdout.Result | ConvertFrom-Json
        if ($process.ExitCode -ne 0 -and $report.ok) { throw 'Audio helper exit/receipt mismatch' }
        return $report
    } finally {
        if ($started -and -not $process.HasExited) { $process.Kill(); if (-not $process.WaitForExit(2000)) { throw 'Audio helper cleanup uncertain' } }
        $process.Dispose()
    }
}
function Invoke-DevelopmentProcessAudio {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Id, [Parameter(Mandatory)][string]$Project,
        [Parameter(Mandatory)][string]$Helper, [switch]$OptIn,
        [ValidateRange(0.1,30.0)][double]$Seconds = 1, [string]$Member = '',
        [ValidateRange(1,2147483647)][int]$CallerProcessId = $PID,
        [long]$CallerCreatedFileTimeUtc = 0,
        [Threading.CancellationToken]$CancellationToken = [Threading.CancellationToken]::None)
    if (-not $OptIn) { throw 'Process audio requires explicit -OptIn' }
    if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'Process audio requires PowerShell 7' }
    if ([double]::IsNaN($Seconds) -or [double]::IsInfinity($Seconds)) { throw 'Invalid audio duration' }
    if ($CancellationToken.IsCancellationRequested) { throw 'Audio cancelled before admission' }
    if ($CallerCreatedFileTimeUtc -lt 0 -or ($CallerProcessId -ne $PID -and $CallerCreatedFileTimeUtc -le 0)) { throw 'Original audio caller requires its creation identity' }
    if ($CallerCreatedFileTimeUtc -gt 0) { $null = [DateTime]::FromFileTimeUtc($CallerCreatedFileTimeUtc) }
    Assert-DevelopmentPath $Helper
    if (-not (Test-Path -LiteralPath $Helper -PathType Leaf)) { throw 'Optional process audio helper missing; build Audio.Capture.cpp with Audio.Capture.Build.ps1 first' }
    $admission = Get-DevelopmentEvidenceTarget $Id $Project $Member
    $folder = Join-Path $admission.Directory 'audio'
    $lease = Enter-DevelopmentStateLock (Join-Path $admission.Directory 'audio.lock')
    $cancel = $null; $registration = $null
    try {
        Assert-DevelopmentPath $folder
        if (-not (Test-Path -LiteralPath $folder)) { Initialize-DevelopmentDirectory $folder }
        $acl = Get-Acl -LiteralPath $folder
        $sid = Get-DevelopmentSid
        if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid) { throw 'Audio evidence owner mismatch' }
        foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
            if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin @($sid,'S-1-5-18','S-1-5-32-544')) { throw 'Audio evidence ACL permits another account' }
        }
        $files = @(Get-ChildItem -LiteralPath $folder -Force)
        $totalBytes = 0L
        foreach ($file in $files) { if (-not $file.PSIsContainer) { $totalBytes += $file.Length } }
        if (@($files | Where-Object Extension -EQ '.json').Count -ge 8 -or $files.Count -gt 14 -or
            @($files | Where-Object PSIsContainer).Count -or $totalBytes -gt 42MB) { throw 'Audio retention limit reached (8 captures, 48 MiB maximum); inspect private evidence' }
        # Re-read state and exact retained selection after acquiring serialization.
        $admission = Get-DevelopmentEvidenceTarget $Id $Project $Member
        $stem = Join-Path $folder ([guid]::NewGuid().ToString('N'))
        $cancelName = 'Local\BasaltwaterAudio-' + [guid]::NewGuid().ToString('N')
        $cancel = [Threading.EventWaitHandle]::new($false,[Threading.EventResetMode]::ManualReset,$cancelName)
        # Register a managed callback: no PowerShell runspace is needed on cancellation.
        if (-not ('Basaltwater.Audio.Cancellation' -as [type])) {
            Add-Type -TypeDefinition 'namespace Basaltwater.Audio { public static class Cancellation { public static System.Threading.CancellationTokenRegistration Register(System.Threading.CancellationToken token, System.Threading.EventWaitHandle signal) { return token.Register(() => signal.Set()); } } }'
        }
        $registration = [Basaltwater.Audio.Cancellation]::Register($CancellationToken,$cancel)
        $self = [Diagnostics.Process]::GetCurrentProcess()
        try { $parentCreated = $(if ($CallerCreatedFileTimeUtc -gt 0) { $CallerCreatedFileTimeUtc } else { $self.StartTime.ToUniversalTime().ToFileTimeUtc() }).ToString([Globalization.CultureInfo]::InvariantCulture) } finally { $self.Dispose() }
        $arguments = @('--capture',[string]$admission.Target.ProcessId,[string]$admission.Target.CreatedFileTimeUtc,
            $admission.Record.JobName,[string][int][Math]::Round($Seconds*1000),($stem + '.wav'),[string]$CallerProcessId,$parentCreated,$cancelName)
        try { $report = Invoke-ProcessAudioNative $Helper $arguments $CancellationToken }
        catch { $report = [pscustomobject]@{ ok = $false; reason = 'helper_unavailable'; Error = $_.Exception.Message } }
        $wave = $stem + '.wav'
        if (Test-Path -LiteralPath $wave) {
            Assert-DevelopmentPath $wave
            if ((Get-Item -LiteralPath $wave).Length -gt 5292044) { throw 'Audio WAV exceeds hard size limit' }
        } elseif ($report.ok) { throw 'Audio helper returned success without WAV' }
        $envelope = [pscustomobject]@{ Version = 1; Id = $Id; Project = $admission.Record.Project;
            Caller = @{ ProcessId = $CallerProcessId; CreatedFileTimeUtc = $parentCreated };
            TargetToken = "$($admission.Target.ProcessId):$($admission.Target.CreatedFileTimeUtc)";
            Selection = $(if ($Member) { 'member-tree' } else { 'root-tree' });
            WavPath = $(if (Test-Path -LiteralPath $wave) { $wave } else { $null });
            MetadataPath = ($stem + '.json'); Capture = $report }
        Write-DevelopmentJson $envelope.MetadataPath $envelope
        return $envelope
    } finally {
        if ($null -ne $registration) { $registration.Dispose() }
        if ($cancel) { $cancel.Dispose() }
        Exit-DevelopmentStateLock $lease
    }
}
