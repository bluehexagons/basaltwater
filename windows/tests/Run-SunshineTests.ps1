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
    Assert-True (-not (Get-NativeSunshineHealth).Healthy) 'Missing service was healthy'
    $script:Admin = $true
    Assert-Throws { Install-NativeSunshine } 'Install the native'
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
    Assert-True ($script:Disposes -eq $beforeDisposes + 2) 'Health/startup service controllers were not disposed after timeout'
    $script:Service.Status = 'StopPending'; $script:Calls = @()
    Assert-Throws { Install-NativeSunshine } 'stopping or paused'
    Assert-True ($script:Calls -notcontains 'wait') 'Stopping service entered startup wait'
    $script:InstallFailure = $true; $script:Calls = @()
    Assert-Throws { Install-NativeSunshine } 'installer failed'
    Assert-True (($script:Calls -join ',') -eq 'winget,install') 'Failed install continued to service changes'
    $script:InstallFailure = $false; $script:WaitFailure = $false; $script:Admin = $false
    $script:Elevations = 0; $script:ElevatedExit = 0; $script:CancelElevation = $false
    function Start-Process($FilePath, $ArgumentList, $Verb, $WindowStyle, [switch]$Wait, [switch]$PassThru) {
        Assert-True ($Verb -eq 'RunAs' -and $WindowStyle -eq 'Hidden' -and $Wait -and $PassThru) 'Service elevation changed interactive account setup or showed a console'
        $command = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String(($ArgumentList -split ' ')[-1]))
        Assert-True ($command.Contains('Set-NativeSunshineService') -and -not $command.Contains('Invoke-NativeAgentSetup')) 'Elevation included account setup'
        $script:Elevations++
        if ($script:CancelElevation) { throw 'UAC cancelled' }
        if ($script:ElevatedExit -eq 0) { Set-NativeSunshineService }
        $result = [pscustomobject]@{ ExitCode = $script:ElevatedExit }
        $result | Add-Member ScriptMethod Dispose { }
        return $result
    }
    $script:Service.Status = 'Running'
    Install-NativeSunshine
    Assert-True ($script:Elevations -eq 0) 'Healthy service unnecessarily prompted for UAC'
    $script:Service.Status = 'Stopped'
    Install-NativeSunshine
    Assert-True ($script:Elevations -eq 1 -and $script:Service.Status -eq 'Running') 'Standard-user setup did not configure its selected service through UAC'
    $script:Service.Status = 'Stopped'; $script:ElevatedExit = 19
    Assert-Throws { Install-NativeSunshine } 'exit code 19'
    $script:CancelElevation = $true
    Assert-Throws { Install-NativeSunshine } 'UAC cancelled'
}
Write-Host 'Native Sunshine tests passed.'
