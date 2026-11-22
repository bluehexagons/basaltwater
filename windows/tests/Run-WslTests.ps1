<# Read-only WSL qualification on a prepared Windows 11 self-hosted runner. #>
[CmdletBinding()]
param([string]$Distribution = '')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '..\Bootstrap.Common.ps1')

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
$kernel = (& wsl.exe --distribution $Distribution --exec uname -r | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0 -or $kernel -notmatch '(?i)(microsoft|wsl2)') {
    throw 'Selected distribution is not running on the WSL kernel'
}
$osRelease = & wsl.exe --distribution $Distribution --exec cat /etc/os-release
if ($LASTEXITCODE -ne 0 -or -not @($osRelease | Where-Object { $_ -match '^ID="?ubuntu"?\s*$' })) {
    throw 'Selected distribution is not Ubuntu'
}
if ((Get-UbuntuInitProcess $Distribution) -ne 'systemd') {
    throw 'Selected distribution is not running systemd'
}

$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
& (Join-Path $repo 'install.ps1') -Profile server_wsl -Plan
if (-not $?) { throw 'Installer plan failed' }
$linuxRepo = & wsl.exe --distribution $Distribution --exec wslpath -u $repo | Select-Object -Last 1
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($linuxRepo)) { throw 'Could not translate checkout path into WSL' }
$linuxRepo = $linuxRepo.Trim()
$setupArgs = @(Get-UbuntuSetupArguments $state)
& wsl.exe --distribution $Distribution --user root --exec /usr/bin/python3 -B ($linuxRepo + '/remote_setup.py') @setupArgs --dry-run
if ($LASTEXITCODE -ne 0) { throw 'Ubuntu WSL setup dry run failed' }

# Use the login PATH to exercise the installed tools, including the managed
# Node default and account-local providers. Only fixed command names go to Bash.
$tools = @('git', 'git-lfs', 'rg', 'python3', 'cc', 'make')
if ($state.Node) { $tools += @('node', 'npm', 'pnpm') }
if ($state.Python) { $tools += @('python', 'uv') }
if ($state.Go) { $tools += 'go' }
foreach ($provider in @('gh', 'codex', 'claude', 'opencode')) {
    if ($provider -in $state.AgentTool) { $tools += $provider }
}
$checks = foreach ($tool in $tools) {
    $versionArg = if ($tool -eq 'go') { 'version' } else { '--version' }
    "echo Checking $tool && /usr/bin/timeout --kill-after=5s 30s $tool $versionArg"
}
$versionChecks = 'cd && ' + ($checks -join ' && ')
& wsl.exe --distribution $Distribution --user $state.LinuxUser --exec /bin/bash -lc $versionChecks
if ($LASTEXITCODE -ne 0) { throw 'Installed Ubuntu WSL tool version check failed' }

& wsl.exe --distribution $Distribution --user $state.LinuxUser --cd $linuxRepo `
    --exec /usr/bin/python3 -B -m unittest tests.test_config_wsl tests.test_machine_state tests.test_plugin_registry -q
if ($LASTEXITCODE -ne 0) { throw 'Ubuntu WSL focused tests failed' }
Write-Host "Windows 11 / $Distribution WSL qualification passed."
