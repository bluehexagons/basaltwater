function Update-DevelopmentChildren($Tracker,$Record,[string]$Directory,[bool]$Enumerate = $true) {
    $target = Read-DevelopmentTarget $Directory
    $rootId = 0; $created = 0L
    if ($target) { $rootId = $target.ProcessId; $created = [long]$target.CreatedFileTimeUtc }
    if ($Tracker.Poll($Record.JobName,$rootId,$created,$Enumerate)) {
        $catalog = [pscustomobject]@{ Version = 1; Coverage = 'partial: sampled every 200ms; short-lived members may be missed';
            Limit = 128; Overflow = $Tracker.Overflow; Error = $Tracker.Error; Members = @($Tracker.Members) }
        $path = Join-Path $Directory 'members.json'
        if ([Text.Encoding]::UTF8.GetByteCount(($catalog | ConvertTo-Json -Depth 20)) -gt 128KB) { throw 'Child catalog exceeds 128 KiB' }
        Write-DevelopmentJson $path $catalog
    }
}
