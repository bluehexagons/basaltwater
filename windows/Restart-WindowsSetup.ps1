<# Hidden current-account worker for a saved native setup restart/recovery. #>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidateSet('Restart', 'Resume')][string]$Mode,
      [Parameter(Mandatory=$true)][string]$Id)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'AgentWorkstation.Common.ps1')
. (Join-Path $PSScriptRoot 'T3Completion.Common.ps1')
. (Join-Path $PSScriptRoot 'WindowsRestart.Common.ps1')
. (Join-Path $PSScriptRoot 'Development.Common.ps1')
Invoke-NativeWindowsRestartWorker -Mode $Mode -Id $Id
