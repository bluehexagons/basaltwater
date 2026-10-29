<# Detached refresh worker, owned by a limited current-account Scheduled Task. #>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Id)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'AgentWorkstation.Common.ps1')
. (Join-Path $PSScriptRoot 'Cli.Common.ps1')
. (Join-Path $PSScriptRoot 'Refresh.Common.ps1')
Invoke-NativeRefreshWorker $Id
