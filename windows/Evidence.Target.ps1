<# Shared retained-task selection for opt-in process audio and presentation traces.
   Requires Development.Common.ps1 and Development.Diagnostics.ps1. Native helpers
   revalidate the returned identity/account/session/Job before collecting data. #>
function Get-DevelopmentEvidenceTarget([string]$Id,[string]$Project,[string]$Member = '') {
    $record = Get-DevelopmentStatus $Id
    $directory = Get-DevelopmentDirectory $Id
    Assert-DevelopmentPath $Project
    $projectPath = [IO.Path]::GetFullPath($Project).TrimEnd('\','/')
    if (-not (Test-Path -LiteralPath $projectPath -PathType Container) -or
        -not [string]::Equals($projectPath, [string]$record.Project, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Evidence requires the exact retained project directory'
    }
    if ($record.State -ne 'running' -or $record.CompletedUtc -or
        $record.OwnerSid -ne (Get-DevelopmentSid) -or
        [int]$record.SessionId -ne [Diagnostics.Process]::GetCurrentProcess().SessionId -or
        $record.JobName -cnotmatch '\ALocal\\BasaltwaterDevelopment-[a-f0-9]{32}-job\z') {
        throw 'Evidence requires a live current-account/session task and its exact Job'
    }
    if ($Member) {
        if ($Member -notmatch '\A[1-9][0-9]{0,9}:[1-9][0-9]{0,18}\z' -or -not $record.TrackChildren) { throw 'Evidence requires a retained member PID:FILETIME and --track-children' }
        $catalog = Read-DevelopmentMembers $directory
        $selected = @($catalog.Members | Where-Object Token -CEQ $Member)
        if ($selected.Count -ne 1 -or $selected[0].State -ne 'observed' -or $selected[0].Role -notin @('root','child')) { throw 'Evidence member is stale, unrelated, or not a native target' }
        $target = $selected[0]
    } else {
        $target = Read-DevelopmentTarget $directory
        if (-not $target -or $target.State -ne 'created') { throw 'Evidence native root identity unavailable or exited' }
    }
    [pscustomobject]@{ Record = $record; Target = $target; Directory = $directory }
}
