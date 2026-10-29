<# Apply the current checkout's native coding tools without downloading another revision. #>
[CmdletBinding()]
param([string[]]$AgentTool = @('gh', 'codex'), [switch]$Node, [switch]$Python,
      [switch]$Go, [switch]$NodeVersions, [switch]$GameDev, [switch]$ShaderTools, [switch]$Godot,
      [string]$StorageRoot = '',
      [string]$StorageDrive = '',
      [switch]$T3CodeDesktop = $true, [switch]$Plan)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($StorageDrive) {
    if ($StorageDrive -notmatch '\A[A-Za-z]:?\z') { throw 'StorageDrive must be one drive letter, such as F or F:' }
    if ($PSBoundParameters.ContainsKey('StorageRoot')) { throw 'Choose StorageDrive or StorageRoot, not both' }
    $StorageRoot = $StorageDrive.Substring(0, 1).ToUpperInvariant() + ':\Basaltwater'
}
$system = Get-CimInstance Win32_OperatingSystem
if ($system.ProductType -ne 1 -or [int]$system.BuildNumber -lt 22000 -or
    $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'Native coding setup requires x86-64 Windows 11' }
. (Join-Path $PSScriptRoot 'Bootstrap.Common.ps1')
. (Join-Path $PSScriptRoot 'AgentWorkstation.Common.ps1')
$setupLock = $null
try {
    if (-not $Plan) {
        $directory = Join-Path $env:LOCALAPPDATA 'Basaltwater'
        Assert-NativeSkillPath $directory
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        try {
            $setupLock = [IO.File]::Open((Join-Path $directory 'setup.lock'), [IO.FileMode]::OpenOrCreate,
                [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        } catch [IO.IOException] { throw 'Another Windows setup or T3 completion is active. Wait for it to finish, then retry.' }
    }
    Invoke-NativeAgentSetup -Source (Split-Path -Parent $PSScriptRoot) -AgentTool $AgentTool `
        -Node:$Node -Python:$Python -Go:$Go -NodeVersions:$NodeVersions -GameDev:$GameDev `
        -ShaderTools:$ShaderTools -Godot:$Godot -StorageRoot $StorageRoot -T3CodeDesktop:$T3CodeDesktop -Plan:$Plan
    if (-not $Plan) {
        Write-Host 'Native setup complete.'
        . (Join-Path $PSScriptRoot 'T3Completion.Common.ps1')
        Invoke-NativeSetupCompletion -Source (Split-Path -Parent $PSScriptRoot) -T3CodeDesktop:$T3CodeDesktop
    }
} finally {
    if ($setupLock) { $setupLock.Dispose() }
}
