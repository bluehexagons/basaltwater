<# Visible refresh output and summary; closing this window never stops setup. #>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Id)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'AgentWorkstation.Common.ps1')
. (Join-Path $PSScriptRoot 'Refresh.Common.ps1')
. (Join-Path $PSScriptRoot 'RefreshProgress.Common.ps1')
Show-NativeRefreshProgress $Id
