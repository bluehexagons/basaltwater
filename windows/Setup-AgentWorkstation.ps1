<# Apply the current checkout's native coding tools without downloading another revision. #>
[CmdletBinding()]
param([string[]]$AgentTool = @('gh', 'codex'), [switch]$Node, [switch]$Python,
      [switch]$Go, [switch]$NodeVersions, [switch]$GameDev, [switch]$ShaderTools, [switch]$Godot,
      [string]$StorageRoot = '',
      [switch]$T3CodeDesktop = $true, [switch]$Plan)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$system = Get-CimInstance Win32_OperatingSystem
if ($system.ProductType -ne 1 -or [int]$system.BuildNumber -lt 22000 -or
    $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'Native coding setup requires x86-64 Windows 11' }
. (Join-Path $PSScriptRoot 'Bootstrap.Common.ps1')
. (Join-Path $PSScriptRoot 'AgentWorkstation.Common.ps1')
Invoke-NativeAgentSetup -Source (Split-Path -Parent $PSScriptRoot) -AgentTool $AgentTool `
    -Node:$Node -Python:$Python -Go:$Go -NodeVersions:$NodeVersions -GameDev:$GameDev `
    -ShaderTools:$ShaderTools -Godot:$Godot -StorageRoot $StorageRoot -T3CodeDesktop:$T3CodeDesktop -Plan:$Plan
if (-not $Plan) { Write-Host 'Native setup complete. Restart T3 Code to load the updated PATH and skills.' }
