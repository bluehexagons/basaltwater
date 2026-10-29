<# Runs only as a current-user interactive Scheduled Task after successful setup. #>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$RequestPath,
      [Parameter(Mandatory=$true)][string]$TaskName)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'AgentWorkstation.Common.ps1')
. (Join-Path $PSScriptRoot 'T3Completion.Common.ps1')
Invoke-NativeT3CompletionWorker -RequestPath $RequestPath -TaskName $TaskName
