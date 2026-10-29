<# Run inside the user's visible Terminal/Start Menu shell. #>
[CmdletBinding()]
param([string]$Project = '', [switch]$Msvc)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'AgentWorkstation.Common.ps1')
. (Join-Path $PSScriptRoot 'PowerShell.Common.ps1')
. (Join-Path $PSScriptRoot 'ShellIntegration.Common.ps1')
if (-not (Test-NativePowerShell7Host)) { throw 'Open this development shell in stable PowerShell 7' }
Update-NativePath
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
if ($Project) { Set-Location -LiteralPath (Get-NativeLocalItem $Project -Directory) }
if ($Msvc) {
    . (Join-Path $PSScriptRoot 'GameDevelopment.Common.ps1')
    Enter-NativeGameDevEnvironment
}
Write-Host 'Basaltwater native development shell. Use basaltw --help for desktop and task commands.'
