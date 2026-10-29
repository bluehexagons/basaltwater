<# Offline Sunshine service setup tests. No packages, services or firewall changes. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Assert-Throws([scriptblock]$Action, [string]$Pattern) {
    $caught = $null
    try { & $Action | Out-Null } catch { $caught = $_ }
    if (-not $caught -or $caught.Exception.Message -notmatch $Pattern) { throw "Expected failure: $Pattern; received: $caught" }
}
. (Join-Path $PSScriptRoot '..\Sunshine.Common.ps1')
& {
    function Test-NativeSunshineAdministrator { return $script:Admin }
    function Ensure-Winget { $script:Calls += 'winget' }
    function Ensure-WingetPackage($PackageId, $Label) {
        Assert-True ($PackageId -eq 'LizardByte.Sunshine') 'Unexpected Sunshine package'
        $script:Calls += 'install'
        if ($script:InstallFailure) { throw 'installer failed' }
    }
    function Get-Service($Name) {
        Assert-True ($Name -eq 'SunshineService') 'Unexpected service discovery'
        return $script:Service
    }
    function Set-Service($Name, $StartupType) {
        Assert-True ($Name -eq 'SunshineService' -and $StartupType -eq 'Automatic') 'Wrong service or startup selection'
        $script:Calls += 'enable'; $script:Service.StartType = 'Automatic'
    }
    function Start-Service { throw 'Unbounded Start-Service was invoked' }
    $script:Admin = $false; $script:InstallFailure = $false; $script:Calls = @(); $script:Service = $null
    Install-NativeSunshine -Plan
    Assert-True ($script:Calls.Count -eq 0) 'Plan changed packages/services'
    Assert-Throws { Install-NativeSunshine } 'administrator PowerShell'
    Assert-True ($script:Calls.Count -eq 0) 'Unelevated setup changed packages/services'
    Assert-True (-not (Get-NativeSunshineHealth).Healthy) 'Missing service was healthy'
    $script:Admin = $true
    Assert-Throws { Install-NativeSunshine } 'did not provide SunshineService'
    $script:Service = [pscustomobject]@{ Status = 'Stopped'; StartType = 'Manual' }
    $script:Disposes = 0; $script:WaitFailure = $false
    $script:Service | Add-Member ScriptMethod Dispose { $script:Disposes++ }
    $script:Service | Add-Member ScriptMethod Refresh { }
    $script:Service | Add-Member ScriptMethod Start { $script:Calls += 'start'; $this.Status = 'StartPending' }
    $script:Service | Add-Member ScriptMethod WaitForStatus {
        param($Status, $Timeout)
        Assert-True ($Status -eq 'Running' -and $Timeout.TotalSeconds -eq 30) 'Service startup timeout was lost'
        $script:Calls += 'wait'
        if ($script:WaitFailure) { throw 'service startup timed out' }
        $this.Status = 'Running'
    }
    Assert-True (-not (Get-NativeSunshineHealth).Healthy) 'Stopped/manual service was healthy'
    $script:Service.Status = 'Running'
    Assert-True (-not (Get-NativeSunshineHealth).Healthy) 'Running service without startup was healthy'
    $script:Calls = @(); $script:Service.Status = 'Stopped'
    Install-NativeSunshine
    Assert-True (($script:Calls -join ',') -eq 'winget,install,enable,start,wait') 'Service started before package installation or enablement'
    Assert-True (Get-NativeSunshineHealth).Healthy 'Running automatic service was not healthy'
    foreach ($state in @('Running', 'StartPending')) {
        $script:Service.Status = $state; $script:Calls = @()
        Install-NativeSunshine
        Assert-True ($script:Calls -notcontains 'start') 'Already running/pending service was started twice'
    }
    $script:WaitFailure = $true; $script:Service.Status = 'StartPending'; $script:Calls = @()
    $beforeDisposes = $script:Disposes
    Assert-Throws { Install-NativeSunshine } 'service startup timed out'
    Assert-True ($script:Disposes -eq $beforeDisposes + 1) 'Timed-out service controller was not disposed'
    $script:Service.Status = 'StopPending'; $script:Calls = @()
    Assert-Throws { Install-NativeSunshine } 'stopping or paused'
    Assert-True ($script:Calls -notcontains 'wait') 'Stopping service entered startup wait'
    $script:InstallFailure = $true; $script:Calls = @()
    Assert-Throws { Install-NativeSunshine } 'installer failed'
    Assert-True (($script:Calls -join ',') -eq 'winget,install') 'Failed install continued to service changes'
}
Write-Host 'Native Sunshine tests passed.'
