<# Explicit low-space recovery through Windows-owned cleanup operations. #>
function Get-NativeStorageCapacity {
    $drive = [IO.Path]::GetPathRoot([Environment]::GetFolderPath('Windows')).Substring(0, 1)
    $volume = Get-Volume -DriveLetter $drive -ErrorAction Stop
    if (-not $volume -or $null -eq $volume.SizeRemaining -or $volume.Size -le 0) {
        throw 'Could not measure the Windows system volume'
    }
    return [pscustomobject]@{ Drive = $drive + ':'; TotalBytes = [long]$volume.Size;
        FreeBytes = [long]$volume.SizeRemaining }
}

function Test-NativeStorageAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-NativeStorageRebootPending {
    return ((Test-Path -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or
        (Test-Path -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'))
}

function Invoke-NativeStorageAction([string]$Category) {
    # Keep JSON output isolated from native diagnostic streams; errors still
    # terminate the action and become its structured failure reason.
    $WarningPreference = 'SilentlyContinue'
    $VerbosePreference = 'SilentlyContinue'
    $ProgressPreference = 'SilentlyContinue'
    switch ($Category) {
        'delivery-optimization' {
            Import-Module DeliveryOptimization -ErrorAction Stop
            # Native retention protects pinned content unless explicitly opted in.
            Delete-DeliveryOptimizationCache -Force -ErrorAction Stop | Out-Null
            return $false
        }
        'superseded-components' {
            Import-Module Dism -ErrorAction Stop
            # Never use ResetBase, manually remove WinSxS, or reboot the host.
            $result = Repair-WindowsImage -Online -StartComponentCleanup -NoRestart -ErrorAction Stop
            return [bool]$result.RestartNeeded
        }
        default { throw 'Unknown Windows storage cleanup category' }
    }
}

function Invoke-NativeStorageCleanup([switch]$Apply, [ValidateRange(1, 1024)][int]$TargetGiB = 5) {
    $before = Get-NativeStorageCapacity
    $target = [long]$TargetGiB * 1GB
    # Preview works without elevation. Applying machine cleanup never silently
    # elevates or starts a background operation that outlives this command.
    $actions = @()
    $reboot = $false
    foreach ($category in @('delivery-optimization', 'superseded-components')) {
        $capacity = Get-NativeStorageCapacity
        if ($capacity.FreeBytes -ge $target) { break }
        if ($Apply -and -not (Test-NativeStorageAdministrator)) {
            throw 'Open an administrator PowerShell terminal to run basaltw storage --apply'
        }
        $row = [ordered]@{ Category = $category; Status = 'preview'; Reason = '' }
        $actions += [pscustomobject]$row
        if (-not $Apply) { continue }
        try {
            if ($category -eq 'superseded-components' -and (Test-NativeStorageRebootPending)) {
                $actions[-1].Status = 'deferred'
                $actions[-1].Reason = 'Windows servicing requires a reboot; reboot normally and retry'
                $reboot = $true
                continue
            }
            $reboot = (Invoke-NativeStorageAction $category) -or $reboot
            $actions[-1].Status = 'cleaned'
        } catch {
            $actions[-1].Status = 'failed'
            $message = $_.Exception.Message
            $actions[-1].Reason = $message.Substring(0, [Math]::Min(2000, $message.Length))
        }
    }
    $after = Get-NativeStorageCapacity
    return [pscustomobject]@{ Applied = [bool]$Apply; Before = $before; After = $after; TargetBytes = $target;
        NetFreedBytes = $after.FreeBytes - $before.FreeBytes; TargetMet = $after.FreeBytes -ge $target;
        RebootRequired = $reboot; Actions = @($actions) }
}

function Invoke-NativeStorageCli([string[]]$Arguments) {
    $apply = $false; $json = $false; $target = 5; $seen = @{}
    if ($Arguments.Count -eq 1 -and $Arguments[0] -in @('--help', '-h', 'help')) {
        Write-Host 'Usage: basaltw storage [--apply] [--target-gib 5] [--json]'
        Write-Host 'Preview low-space recovery on the Windows system volume. Apply requires an administrator terminal.'
        Write-Host 'Clears unpinned Delivery Optimization downloads, then superseded Windows components until the target is met.'
        return 0
    }
    for ($index = 0; $index -lt $Arguments.Count; $index++) {
        $option = $Arguments[$index]
        if ($seen.ContainsKey($option)) { throw 'Duplicate storage option' }
        $seen[$option] = $true
        switch ($option) {
            '--apply' { $apply = $true }
            '--json' { $json = $true }
            '--target-gib' {
                $index++
                if ($index -ge $Arguments.Count -or $Arguments[$index] -notmatch '\A[0-9]{1,4}\z' -or
                    [int]$Arguments[$index] -lt 1 -or [int]$Arguments[$index] -gt 1024) {
                    throw 'Storage target must be an integer from 1 to 1024 GiB'
                }
                $target = [int]$Arguments[$index]
            }
            default { throw 'Usage: basaltw storage [--apply] [--target-gib 5] [--json]' }
        }
    }
    $result = Invoke-NativeStorageCleanup -Apply:$apply -TargetGiB $target
    if ($json) { Write-Host ($result | ConvertTo-Json -Depth 6) }
    else {
        Write-Host ('Disk {0}: {1:N2} GiB available; target {2} GiB' -f $result.After.Drive, ($result.After.FreeBytes / 1GB), $target)
        foreach ($action in $result.Actions) { Write-Host ("  {0}: {1} {2}" -f $action.Category, $action.Status, $action.Reason) }
        if ($apply) { Write-Host ('Net space change: {0:N2} GiB' -f ($result.NetFreedBytes / 1GB)) }
        else { Write-Host 'Preview only. Run basaltw storage --apply in an administrator terminal to clean.' }
        if ($result.RebootRequired) { Write-Host 'Windows requires a reboot; no reboot was initiated.' }
        if (-not $result.TargetMet) { Write-Host 'The free-space target remains unmet. Cleanup may be insufficient for the next update.' }
    }
    if ($apply -and (-not $result.TargetMet -or @($result.Actions | Where-Object { $_.Status -eq 'failed' }).Count)) { return 1 }
    return 0
}
