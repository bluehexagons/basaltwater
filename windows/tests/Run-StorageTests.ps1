<# Offline policy checks; all Windows observations and cleanup are mocked. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '..\Storage.Common.ps1')
. (Join-Path $PSScriptRoot '..\Cli.Common.ps1')

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function Assert-Throws([scriptblock]$Action, [string]$Pattern) {
    try { & $Action | Out-Null } catch {
        if ($_.Exception.Message -match $Pattern) { return }
        throw
    }
    throw "Expected failure: $Pattern"
}

# Exercise native option boundaries without importing servicing modules.
& {
    $script:ScratchExists = $true; $script:ScratchEntries = @(); $script:Unsafe = $false
    function Resolve-NativeStorageRoot($Path) {
        if ($script:Unsafe) { throw 'Unsafe linked storage path' }
        return $Path
    }
    function Test-Path($LiteralPath, $PathType) { return $script:ScratchExists }
    function Get-ChildItem($LiteralPath, [switch]$Force, $ErrorAction) { return $script:ScratchEntries }
    Assert-True ((Resolve-NativeCleanupScratch 'F:\servicing scratch') -eq 'F:\servicing scratch') 'Scratch validation changed the path'
    $script:ScratchEntries = @('personal.txt')
    Assert-Throws { Resolve-NativeCleanupScratch 'F:\servicing scratch' } 'empty directory'
    $script:ScratchEntries = @(); $script:ScratchExists = $false
    Assert-Throws { Resolve-NativeCleanupScratch 'F:\missing' } 'existing empty'
    $script:Unsafe = $true
    Assert-Throws { Resolve-NativeCleanupScratch 'F:\linked' } 'Unsafe linked'
}

& {
    $script:Imported = @(); $script:Delivery = $false; $script:Components = $false; $script:NativeScratch = ''
    function Resolve-NativeCleanupScratch($Path) { return $Path }
    function Import-Module($Name, $ErrorAction) { $script:Imported += $Name }
    function Delete-DeliveryOptimizationCache([switch]$Force, [switch]$IncludePinnedFiles, $ErrorAction) {
        Assert-True ($Force -and -not $IncludePinnedFiles) 'Cleanup selected pinned delivery content or prompted'
        $script:Delivery = $true
    }
    function Repair-WindowsImage([switch]$Online, [switch]$StartComponentCleanup, [switch]$ResetBase,
                                 [switch]$NoRestart, [string]$ScratchDirectory, $ErrorAction) {
        Assert-True ($Online -and $StartComponentCleanup -and $NoRestart -and -not $ResetBase) 'Unsafe component cleanup options'
        $script:Components = $true
        $script:NativeScratch = $ScratchDirectory
        return [pscustomobject]@{ RestartNeeded = $true }
    }
    Assert-True (-not (Invoke-NativeStorageAction 'delivery-optimization')) 'Delivery cleanup requested a reboot'
    Assert-True (Invoke-NativeStorageAction 'superseded-components') 'Servicing reboot requirement was lost'
    Assert-True ($script:Delivery -and $script:Components -and ($script:Imported -join ',') -eq 'DeliveryOptimization,Dism') 'Native cleanup was not dispatched'
    Invoke-NativeStorageAction 'superseded-components' -ScratchDirectory 'F:\servicing scratch' | Out-Null
    Assert-True ($script:NativeScratch -eq 'F:\servicing scratch') 'Native servicing lost the scratch directory'
    Assert-Throws { Invoke-NativeStorageAction 'unknown' } 'Unknown'
}

& {
    $script:Free = 1GB; $script:Calls = @(); $script:Admin = $true; $script:Pending = $false
    $script:FailDelivery = $false; $script:Gain = 0; $script:AdminChecks = 0
    function Get-NativeStorageCapacity {
        return [pscustomobject]@{ Drive = 'C:'; TotalBytes = 100GB; FreeBytes = $script:Free }
    }
    function Test-NativeStorageAdministrator { $script:AdminChecks++; return $script:Admin }
    function Test-NativeStorageRebootPending { return $script:Pending }
    $script:Scratch = ''; $script:BadScratch = $false
    function Resolve-NativeCleanupScratch($Path) {
        if ($script:BadScratch) { throw 'Invalid scratch directory' }
        return $Path
    }
    function Invoke-NativeStorageAction($Category, $ScratchDirectory) {
        $script:Scratch = $ScratchDirectory
        $script:Calls += $Category
        if ($script:FailDelivery -and $Category -eq 'delivery-optimization') { throw 'fixture cleanup failure' }
        $script:Free += $script:Gain
        return $Category -eq 'superseded-components'
    }

    $preview = Invoke-NativeStorageCleanup
    Assert-True ($preview.Actions.Count -eq 2 -and $script:Calls.Count -eq 0 -and $script:AdminChecks -eq 0) 'Preview mutated storage or required elevation'
    Assert-True (-not $preview.TargetMet -and $preview.NetFreedBytes -eq 0) 'Preview claimed freed capacity'
    $text = @(Invoke-NativeStorageCli -Arguments @() 6>&1)
    Assert-True ($text[-1] -eq 0 -and ($text -join '\n') -match 'Preview only') 'Default storage command did not produce a read-only preview'
    $script:Admin = $false
    Assert-Throws { Invoke-NativeStorageCleanup -Apply } 'administrator'
    Assert-True ($script:Calls.Count -eq 0) 'Unelevated cleanup performed a mutation'

    $script:Free = 6GB
    $healthy = Invoke-NativeStorageCleanup -Apply
    Assert-True ($healthy.TargetMet -and $healthy.Actions.Count -eq 0 -and $script:Calls.Count -eq 0) 'Healthy disk was cleaned'
    $script:Free = 1GB; $script:Admin = $true; $script:Gain = 5GB
    $applied = Invoke-NativeStorageCleanup -Apply
    Assert-True ($applied.TargetMet -and $applied.NetFreedBytes -eq 5GB -and $script:Calls.Count -eq 1) 'Cleanup did not stop at the capacity target'

    $script:Free = 1GB; $script:Calls = @(); $script:Gain = 0; $script:FailDelivery = $true
    $partial = Invoke-NativeStorageCleanup -Apply
    Assert-True ($partial.Actions[0].Status -eq 'failed' -and $partial.Actions[1].Status -eq 'cleaned' -and
        $partial.RebootRequired -and -not $partial.TargetMet) 'Cleanup lost partial failure, remaining shortfall or reboot requirement'
    $script:Pending = $true; $script:Calls = @()
    $pending = Invoke-NativeStorageCleanup -Apply
    Assert-True ($pending.Actions[1].Status -eq 'deferred' -and $pending.RebootRequired -and
        ($script:Calls -join ',') -eq 'delivery-optimization') 'Pending servicing did not defer component cleanup'

    $script:BadScratch = $true; $script:Calls = @()
    Assert-Throws { Invoke-NativeStorageCleanup -Apply -ScratchDirectory 'F:\unsafe' } 'Invalid scratch'
    Assert-True ($script:Calls.Count -eq 0) 'Invalid scratch allowed native cleanup before validation'
    $script:BadScratch = $false

    foreach ($arguments in @(@('--unknown'), @('--apply','--apply'), @('--target-gib'),
        @('--target-gib','0'), @('--target-gib','1025'), @('--target-gib','1.5'), @('--target-gib',"5`n"),
        @('--scratch-dir'), @('--scratch-dir','--apply'))) {
        Assert-Throws { Invoke-NativeStorageCli -Arguments $arguments } 'Usage:|Duplicate|integer|Missing directory'
    }
    $response = @(Invoke-NativeStorageCli -Arguments @('--json') 6>&1)
    $json = ($response[0].ToString() | ConvertFrom-Json)
    Assert-True ($response[-1] -eq 0 -and -not $json.Applied -and $json.Actions.Count -eq 2) 'JSON preview lost its result or exit code'
    $response = @(Invoke-NativeStorageCli -Arguments @('--apply','--json','--target-gib','10') 6>&1)
    Assert-True ($response[-1] -eq 1 -and ($response[0].ToString() | ConvertFrom-Json).TargetBytes -eq 10GB) 'Unmet target or native failure returned success'
    $response = @(Invoke-NativeStorageCli -Arguments @('--apply','--json','--scratch-dir','F:\servicing scratch') 6>&1)
    Assert-True (($response[0].ToString() | ConvertFrom-Json).ScratchDirectory -eq 'F:\servicing scratch' -and
        $script:Scratch -eq 'F:\servicing scratch') 'CLI lost the literal scratch directory in its result or dispatch'
}

# The public dispatch must load the installed source helper before parsing.
function Get-NativeCliSource { return (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) }
Assert-True ((Invoke-NativeCli -Arguments @('storage','--help')) -eq 0) 'Public storage help dispatch failed'
Assert-Throws { Invoke-NativeCli -Arguments @('storage','--unknown') } 'Usage: basaltw storage'
Write-Host 'Windows storage cleanup policy tests passed.'
