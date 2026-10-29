<# Optional machine-wide Windows Sunshine setup, independent of coding tools. #>
[CmdletBinding()]
param([switch]$Plan, [switch]$Status)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($Plan -and $Status) { throw 'Choose Plan or Status' }
. (Join-Path $PSScriptRoot 'Bootstrap.Common.ps1')
. (Join-Path $PSScriptRoot 'Sunshine.Common.ps1')
if ($Status) {
    $health = Get-NativeSunshineHealth
    $health | ConvertTo-Json
    if (-not $health.Healthy) { exit 1 }
    exit 0
}
$system = Get-CimInstance Win32_OperatingSystem
if ($system.ProductType -ne 1 -or [int]$system.BuildNumber -lt 22000 -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
    throw 'Native Sunshine setup requires x86-64 Windows 11'
}
Install-NativeSunshine -Plan:$Plan
