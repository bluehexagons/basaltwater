<# Explicit, bounded diagnostics for the task's verified native root only. #>
function Read-DevelopmentTarget([string]$Directory) {
    $path = Join-Path $Directory 'target.json'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    Assert-DevelopmentPath $path
    if ((Get-Item -LiteralPath $path).Length -gt 4096) { throw 'Native target identity exceeds its size limit' }
    $target = Read-DevelopmentJson $path
    $pidValue = 0; $created = 0L; $exitCode = 0L
    if (-not $target.PSObject.Properties['ProcessId'] -or [string]$target.ProcessId -notmatch '\A[0-9]{1,10}\z' -or
        -not [int]::TryParse([string]$target.ProcessId,[ref]$pidValue) -or $pidValue -le 0 -or
        -not $target.PSObject.Properties['CreatedFileTimeUtc'] -or [string]$target.CreatedFileTimeUtc -notmatch '\A[0-9]{1,19}\z' -or
        -not [long]::TryParse([string]$target.CreatedFileTimeUtc,[ref]$created) -or $created -le 0 -or
        -not $target.PSObject.Properties['State'] -or $target.State -notin @('created','exited') -or
        -not $target.PSObject.Properties['ExitCode']) { throw 'Invalid native target identity' }
    $null = [DateTime]::FromFileTimeUtc($created)
    if (($target.State -eq 'created' -and $null -ne $target.ExitCode) -or ($target.State -eq 'exited' -and
        ($null -eq $target.ExitCode -or [string]$target.ExitCode -notmatch '\A[0-9]{1,10}\z' -or
        -not [long]::TryParse([string]$target.ExitCode,[ref]$exitCode) -or $exitCode -gt [uint32]::MaxValue))) { throw 'Invalid native target exit evidence' }
    return [pscustomobject]@{ ProcessId = $pidValue; CreatedFileTimeUtc = $created.ToString([Globalization.CultureInfo]::InvariantCulture);
        State = $target.State; ExitCode = $(if ($target.State -eq 'exited') { [uint32]$exitCode } else { $null });
        ExitCodeHex = $(if ($target.State -eq 'exited') { '0x{0:X8}' -f [uint32]$exitCode } else { $null }) }
}
function Invoke-DevelopmentDiagnosticsWorker($Record,$Target) {
    . (Join-Path $PSScriptRoot 'Desktop.Common.ps1')
    return Invoke-NativeDesktopWorker @{
        command = 'diagnostics'; pid = [int]$Target.ProcessId; created = [long]$Target.CreatedFileTimeUtc;
        job = $Record.JobName; sid = $Record.OwnerSid; session = [int]$Record.SessionId
    }
}
function Get-DevelopmentDiagnostics([string]$Id) {
    $record = Get-DevelopmentStatus $Id; $directory = Get-DevelopmentDirectory $Id
    $diagnostics = Join-Path $directory 'diagnostics'
    $lease = Enter-DevelopmentStateLock (Join-Path $directory 'diagnostics.lock')
    try {
        Assert-DevelopmentPath $diagnostics
        if (@(Get-ChildItem -LiteralPath $diagnostics -File -Filter '*.json' -ErrorAction SilentlyContinue | Select-Object -First 32).Count -ge 32) {
            throw 'Diagnostics limit is 32 snapshots per task; inspect retained evidence'
        }
        $report = [pscustomobject]@{ ok = $true; Id = $Id; State = 'settled'; TaskState = $record.State;
            CapturedUtc = [DateTime]::UtcNow.ToString('o'); Target = $null; Resources = $null; Error = $null; SnapshotPath = $null }
        try {
            $report.Target = Read-DevelopmentTarget $directory
            if (-not $record.CompletedUtc) {
                if ($record.State -ne 'running' -or -not $report.Target -or -not $record.PSObject.Properties['JobName'] -or
                    $record.JobName -notmatch '\ALocal\\BasaltwaterDevelopment-[a-f0-9]{32}-job\z') {
                    throw 'Task is not a verified running native target; inspect its receipt and logs'
                }
                $report.Resources = Invoke-DevelopmentDiagnosticsWorker $record $report.Target
                $report.State = 'live'
            }
        } catch { $report.ok = $false; $report.State = 'unavailable'; $report.Error = $_.Exception.Message }
        if (-not (Test-Path -LiteralPath $diagnostics)) { Initialize-DevelopmentDirectory $diagnostics }
        else {
            # Verify an existing folder rather than resetting its ACL on each
            # snapshot (which can require unavailable privileges on PS 5.1).
            $acl = Get-Acl -LiteralPath $diagnostics
            $sid = Get-DevelopmentSid
            if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid) { throw 'Diagnostic evidence owner mismatch' }
            foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
                if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin @($sid,'S-1-5-18','S-1-5-32-544')) {
                    throw 'Diagnostic evidence ACL permits another account'
                }
            }
        }
        $path = Join-Path $diagnostics ([guid]::NewGuid().ToString('N') + '.json')
        $report.SnapshotPath = $path
        if ([Text.Encoding]::UTF8.GetByteCount(($report | ConvertTo-Json -Depth 20)) -gt 32768) { throw 'Diagnostics response exceeds 32 KiB limit' }
        Assert-DevelopmentPath $diagnostics
        Write-DevelopmentJson $path $report
        return $report
    } finally { Exit-DevelopmentStateLock $lease }
}
