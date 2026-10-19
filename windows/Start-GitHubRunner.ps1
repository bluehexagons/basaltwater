<# Scheduled task entry point; it runs only while the WSL owner is logged in. #>
param([Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{16}$')][string]$Instance)
. (Join-Path $PSScriptRoot 'GitHubRunner.Common.ps1')
Import-RunnerJobType
$directory = Join-Path (Get-RunnerRoot) ('instances\' + $Instance)
$listenerLock = [IO.File]::Open((Join-Path $directory 'listener.lock'),
    [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
try {
    while ($true) {
        $setup = Get-RunnerSetup
        $policy = Get-Content -LiteralPath (Join-Path $directory 'policy.json') -Raw | ConvertFrom-Json
        if (-not $policy.enabled -or $policy.registration -ne 'registered') { return }
        $info = New-Object Diagnostics.ProcessStartInfo
        $info.FileName = Join-Path $directory 'app\bin\Runner.Listener.exe'
        $info.Arguments = 'run'
        $info.WorkingDirectory = Join-Path $directory 'app'
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        # Newly installed WinGet tools are absent from this PowerShell session's PATH.
        foreach ($path in @("$env:ProgramFiles\Git\cmd", "$env:ProgramFiles\PowerShell\7")) {
            if (Test-Path -LiteralPath $path) { $info.EnvironmentVariables['PATH'] = $path + ';' + $info.EnvironmentVariables['PATH'] }
        }
        foreach ($name in @('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN',
                            'GH_DEBUG', 'ACTIONS_RUNNER_INPUT_TOKEN', 'RUNNER_TRACKING_ID')) {
            $info.EnvironmentVariables.Remove($name)
        }
        foreach ($name in @($info.EnvironmentVariables.Keys)) {
            if ($name.StartsWith('ACTIONS_RUNNER_INPUT_', [StringComparison]::OrdinalIgnoreCase)) {
                $info.EnvironmentVariables.Remove($name)
            }
        }
        $info.EnvironmentVariables['BASALTWATER_WSL_DISTRO'] = $setup.Distro
        $info.EnvironmentVariables['BASALTWATER_WSL_USER'] = $setup.LinuxUser
        $process = [Diagnostics.Process]::Start($info)
        $job = $null
        try {
            $job = New-Object Basaltwater.ProcessJob($process.Handle)
            $process.WaitForExit()
        } finally {
            if ($job) { $job.Dispose() } elseif (-not $process.HasExited) { $process.Kill() }
            $process.Dispose()
        }
        Start-Sleep -Seconds 5
    }
} finally { $listenerLock.Dispose() }
