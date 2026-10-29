<# Current-user login task; never restart a desktop that is already running. #>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'AgentWorkstation.Common.ps1')
. (Join-Path $PSScriptRoot 'T3Completion.Common.ps1')
Invoke-NativeT3Login
