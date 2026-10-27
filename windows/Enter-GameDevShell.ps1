<# Dot-source in a process with script execution enabled, or use powershell.exe -NoExit -ExecutionPolicy Bypass -File this-script. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'AgentWorkstation.Common.ps1')
. (Join-Path $PSScriptRoot 'GameDevelopment.Common.ps1')
Enter-NativeGameDevEnvironment
