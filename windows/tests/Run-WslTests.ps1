<# Read-only WSL qualification on a prepared Windows 11 self-hosted runner. #>
[CmdletBinding()]
param([string]$Distribution = '')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$system = Get-CimInstance Win32_OperatingSystem
if ($system.ProductType -ne 1 -or [int]$system.BuildNumber -lt 22000 -or
    $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
    throw 'WSL qualification requires an x86-64 Windows 11 desktop runner'
}
$statePath = Join-Path $env:LOCALAPPDATA 'Basaltwater\wsl-setup.json'
if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
    throw 'Run this runner as the owner of a completed Basaltwater WSL setup'
}
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
if ($state.OwnerSid -ne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -or
    $state.Phase -ne 'complete') {
    throw 'The current Windows account has no completed WSL setup'
}
if ($Distribution -and $Distribution -ne $state.Distro) {
    throw 'Requested distribution does not match the completed setup state'
}
$Distribution = $state.Distro

& wsl.exe --version
if ($LASTEXITCODE -ne 0) { throw 'WSL version check failed' }
& wsl.exe --list --verbose
if ($LASTEXITCODE -ne 0) { throw 'WSL distribution list failed' }
$kernel = (& wsl.exe --distribution $Distribution --exec uname -r | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0 -or $kernel -notmatch '(?i)(microsoft|wsl2)') {
    throw 'Selected distribution is not running on the WSL kernel'
}
$init = (& wsl.exe --distribution $Distribution --exec cat /proc/1/comm | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0 -or $init -ne 'systemd') {
    throw 'Selected distribution is not running systemd'
}

$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
& (Join-Path $repo 'install.ps1') -Plan | Out-Host
$linuxRepo = (& wsl.exe --distribution $Distribution --exec wslpath -u $repo | Select-Object -Last 1).Trim()
if ($LASTEXITCODE -ne 0 -or -not $linuxRepo) { throw 'Could not translate checkout path into WSL' }
& wsl.exe --distribution $Distribution --user root --exec python3 `
    ($linuxRepo + '/remote_setup.py') --system-type server_wsl `
    --username $state.LinuxUser --machine wsl --dry-run
if ($LASTEXITCODE -ne 0) { throw 'Ubuntu WSL setup dry run failed' }
& wsl.exe --distribution $Distribution --user $state.LinuxUser --cd $linuxRepo `
    --exec python3 -m unittest tests.test_config_wsl tests.test_machine_state tests.test_plugin_registry -q
if ($LASTEXITCODE -ne 0) { throw 'Ubuntu WSL focused tests failed' }
Write-Host "Windows 11 / $Distribution WSL qualification passed."
