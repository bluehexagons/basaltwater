<# Basic status and lifecycle commands for a login-owned WSL worker. #>
[CmdletBinding()]
param([Parameter(Mandatory = $true)][ValidateSet('status', 'start', 'stop', 'refresh', 'logs')][string]$Action)
$ErrorActionPreference = 'Stop'
$statePath = Join-Path $env:LOCALAPPDATA 'Basaltwater\wsl-setup.json'
if (-not (Test-Path -LiteralPath $statePath)) { throw 'No Basaltwater WSL installation state exists' }
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
if ($state.OwnerSid -ne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) {
    throw 'Setup belongs to another Windows account'
}
$taskName = 'Basaltwater WSL worker'
switch ($Action) {
    'status' {
        Write-Host "Setup phase: $($state.Phase); Ubuntu: $($state.Distro); source: $($state.Revision)"
        & wsl.exe --list --verbose
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($task) { Write-Host "Login worker: $($task.State)" }
    }
    'start' {
        if (-not $state.BuildServer) { throw 'This installation has no login worker' }
        Start-ScheduledTask -TaskName $taskName
    }
    'stop' {
        if (-not $state.BuildServer) { throw 'This installation has no login worker' }
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        & wsl.exe --terminate $state.Distro
        if ($LASTEXITCODE -ne 0) { throw 'Could not stop the Ubuntu distribution' }
    }
    'refresh' {
        & (Join-Path $state.Source 'install.ps1') -Resume
        if (-not $?) { throw 'WSL setup refresh failed' }
    }
    'logs' {
        $jobs = Join-Path $env:LOCALAPPDATA 'Basaltwater\jobs'
        if (Test-Path -LiteralPath $jobs) {
            Get-ChildItem -LiteralPath $jobs -Filter receipt.json -Recurse |
                Sort-Object LastWriteTime -Descending | Select-Object -First 10 -ExpandProperty FullName
        }
    }
}
