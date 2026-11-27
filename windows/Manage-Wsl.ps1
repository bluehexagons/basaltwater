<# Basic status and lifecycle commands for a login-owned WSL worker. #>
[CmdletBinding()]
param([Parameter(Mandatory = $true)][ValidateSet('status', 'start', 'stop', 'refresh', 'logs')][string]$Action,
      [switch]$Json)
$ErrorActionPreference = 'Stop'
if ($Json) {
    . (Join-Path $PSScriptRoot 'Cli.Common.ps1')
    if ($Action -notin @('status','logs')) {
        exit (Write-NativeToolResult @{ stage = 'validation'; effect = 'none' } $false 'invalid_input' 'Use -Json with status or logs.')
    }
}
try {
    $statePath = Join-Path $env:LOCALAPPDATA 'Basaltwater\wsl-setup.json'
    if (-not (Test-Path -LiteralPath $statePath)) { throw 'No Basaltwater WSL installation state exists' }
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if ($state.OwnerSid -ne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) {
        throw 'Setup belongs to another Windows account'
    }
    $taskName = 'Basaltwater WSL worker'
    switch ($Action) {
        'status' {
            if ($Json) {
                $distributionList = @(& wsl.exe --list --verbose)
                $childExit = $LASTEXITCODE
                $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
                $fields = @{ phase = $state.Phase; distribution = $state.Distro; revision = $state.Revision;
                    worker_state = $(if ($task) { [string]$task.State } else { $null }); child_exit_code = $childExit;
                    distribution_inventory_text = ($distributionList -join "`n").Replace([string][char]0, '') }
                exit (Write-NativeToolResult $fields ($childExit -eq 0) 'wsl_inventory_failed' 'The WSL distribution inventory failed.')
            }
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
            if ($Json) {
                $receipts = @()
                if (Test-Path -LiteralPath $jobs) {
                    $receipts = @(Get-ChildItem -LiteralPath $jobs -Filter receipt.json -Recurse -File |
                        Sort-Object LastWriteTime -Descending | Select-Object -First 11)
                }
                $files = @($receipts | Select-Object -First 10 | ForEach-Object {
                    @{ kind = 'job_receipt'; path = $_.FullName; media_type = 'application/json' }
                })
                exit (Write-NativeToolResult @{ artifacts = $files; output_truncated = ($receipts.Count -gt 10) })
            }
            if (Test-Path -LiteralPath $jobs) {
                Get-ChildItem -LiteralPath $jobs -Filter receipt.json -Recurse |
                    Sort-Object LastWriteTime -Descending | Select-Object -First 10 -ExpandProperty FullName
            }
        }
    }
} catch {
    if (-not $Json) { throw }
    exit (Write-NativeToolResult @{} $false 'wsl_status_unavailable' $_.Exception.Message)
}
