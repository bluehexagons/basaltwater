<# Explicit, bounded diagnostics for a verified root or opt-in retained members. #>
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
function Read-DevelopmentMembers([string]$Directory) {
    $path = Join-Path $Directory 'members.json'
    $lease = Enter-DevelopmentStateLock $path
    try {
    Assert-DevelopmentPath $path
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Child catalog unavailable; launch exec/node/run with --track-children' }
    if ((Get-Item -LiteralPath $path).Length -gt 128KB) { throw 'Child catalog exceeds 128 KiB' }
    $catalog = Read-DevelopmentJson $path
    if ($catalog.Version -ne 1 -or @($catalog.Members).Count -gt 128) { throw 'Unsupported child catalog' }
    $seen = @{}
    foreach ($entry in $catalog.Members) {
        $processId = 0; $created = 0L; $code = 0L
        if ([string]$entry.ProcessId -notmatch '\A[0-9]{1,10}\z' -or
            -not [int]::TryParse([string]$entry.ProcessId,[ref]$processId) -or $processId -le 0 -or
            [string]$entry.CreatedFileTimeUtc -notmatch '\A[0-9]{1,19}\z' -or
            -not [long]::TryParse([string]$entry.CreatedFileTimeUtc,[ref]$created) -or $created -le 0 -or
            $entry.Token -cne "${processId}:$created" -or $seen.ContainsKey($entry.Token) -or
            $entry.Role -notin @('helper','root','child') -or $entry.State -notin @('observed','exited')) { throw 'Invalid retained member identity' }
        $null = [DateTime]::FromFileTimeUtc($created); $seen[$entry.Token] = $true
        if ($entry.State -eq 'exited') {
            if ($null -eq $entry.ExitCode -or [string]$entry.ExitCode -notmatch '\A[0-9]{1,10}\z' -or
                -not [long]::TryParse([string]$entry.ExitCode,[ref]$code) -or $code -gt [uint32]::MaxValue -or
                $entry.ExitCodeHex -cne ('0x{0:X8}' -f [uint32]$code)) { throw 'Invalid retained member exit evidence' }
        } elseif ($null -ne $entry.ExitCode -or $null -ne $entry.ExitCodeHex) { throw 'Invalid retained member exit evidence' }
    }
    foreach ($entry in $catalog.Members) {
        if ($entry.ParentToken -and -not $seen.ContainsKey($entry.ParentToken)) { throw 'Invalid retained parent identity' }
    }
    return [pscustomobject]@{ Version = 1; Coverage = [string]$catalog.Coverage; Overflow = [bool]$catalog.Overflow; Error = $catalog.Error;
        Members = @($catalog.Members | ForEach-Object {
            [pscustomobject]@{ ProcessId = [int]$_.ProcessId; CreatedFileTimeUtc = [string]$_.CreatedFileTimeUtc;
                Token = [string]$_.Token; ParentToken = $_.ParentToken; Role = $_.Role; State = $_.State;
                ExitCode = $_.ExitCode; ExitCodeHex = $_.ExitCodeHex }
        }) }
    } finally { Exit-DevelopmentStateLock $lease }
}
function Invoke-DevelopmentMemberWorker($Record,$Members) {
    . (Join-Path $PSScriptRoot 'Desktop.Common.ps1')
    return Invoke-NativeDesktopWorker @{ command = 'diagnostics'; members = @($Members);
        job = $Record.JobName; sid = $Record.OwnerSid; session = [int]$Record.SessionId }
}
function Get-DevelopmentDiagnostics([string]$Id,[switch]$Tree,[string]$Member = '') {
    if ($Tree -and $Member) { throw 'Choose --tree or --member' }
    if ($Member -and $Member -notmatch '\A[1-9][0-9]{0,9}:[1-9][0-9]{0,18}\z') { throw 'Expected member PID:FILETIME token' }
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
            if ($Tree -or $Member) {
                if (-not $record.PSObject.Properties['TrackChildren'] -or -not $record.TrackChildren) { throw 'Child selection unsupported for this task; launch with --track-children' }
                $catalog = Read-DevelopmentMembers $directory
                $selected = @($catalog.Members)
                if ($Member) {
                    $selected = @($selected | Where-Object Token -CEQ $Member)
                    if ($selected.Count -ne 1) { throw 'Member identity is not retained in this task catalog' }
                }
                $omitted = [Math]::Max(0,$selected.Count - 32)
                $selected = @($selected | Select-Object -First 32)
                $report | Add-Member NoteProperty Selection $(if ($Tree) { 'tree' } else { 'member' })
                $report | Add-Member NoteProperty Coverage $catalog.Coverage
                $report | Add-Member NoteProperty Overflow $catalog.Overflow
                $report | Add-Member NoteProperty CatalogError $catalog.Error
                $report | Add-Member NoteProperty OmittedMembers $omitted
                $report | Add-Member NoteProperty Members $selected
                $report | Add-Member NoteProperty Samples @()
                $live = @($selected | Where-Object State -NE 'exited')
                $report | Add-Member NoteProperty UnobservedExits $(if ($record.CompletedUtc) { $live.Count } else { 0 })
                if ($live.Count -and -not $record.CompletedUtc) {
                    if ($record.State -ne 'running' -or $record.JobName -notmatch '\ALocal\\BasaltwaterDevelopment-[a-f0-9]{32}-job\z') { throw 'Task is not verified running; member counters unavailable' }
                    $report.Samples = @((Invoke-DevelopmentMemberWorker $record $live).samples)
                    if (@($report.Samples | Where-Object Error).Count) { throw 'Some selected member counters unavailable; inspect Samples' }
                    $report.State = 'live'
                }
            } else {
                $report.Target = Read-DevelopmentTarget $directory
                if (-not $record.CompletedUtc) {
                    if ($record.State -ne 'running' -or -not $report.Target -or -not $record.PSObject.Properties['JobName'] -or
                        $record.JobName -notmatch '\ALocal\\BasaltwaterDevelopment-[a-f0-9]{32}-job\z') {
                        throw 'Task is not a verified running native target; inspect its receipt and logs'
                    }
                    $report.Resources = Invoke-DevelopmentDiagnosticsWorker $record $report.Target
                    $report.State = 'live'
                }
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
