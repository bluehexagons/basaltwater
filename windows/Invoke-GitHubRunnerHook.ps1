<# Fail closed before workflow steps, then wait for the Windows host job slot. #>
param([Parameter(Mandatory=$true)][string]$PolicyPath)
$ErrorActionPreference = 'Stop'
$temporary = $null
$worker = $null
$guardian = $null
$ownsWorker = $false
try {
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$PID"
    $worker = [Diagnostics.Process]::GetProcessById($parent.ParentProcessId)
    if ($worker.ProcessName -ne 'Runner.Worker') { throw 'Hook must be launched by Runner.Worker' }
    $ownsWorker = $true
    . (Join-Path $PSScriptRoot 'GitHubRunner.Common.ps1')
    $setup = Get-RunnerSetup
    $temporary = Join-Path (Get-RunnerRoot) ('temporary\' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $temporary -Force | Out-Null
    $context = @{}
    foreach ($name in @('GITHUB_EVENT_NAME', 'GITHUB_REPOSITORY', 'GITHUB_SERVER_URL',
                        'GITHUB_RUN_ATTEMPT', 'GITHUB_ACTOR', 'GITHUB_TRIGGERING_ACTOR')) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($null -ne $value) { $context[$name] = $value }
    }
    $contextPath = Join-Path $temporary 'context.json'
    Write-RunnerJson $contextPath $context
    $outcomes = Join-Path (Split-Path -Parent $PolicyPath) 'outcomes.json'
    $arguments = @('--policy', (ConvertTo-RunnerWslPath $PolicyPath $setup),
        '--event', (ConvertTo-RunnerWslPath $env:GITHUB_EVENT_PATH $setup),
        '--context', (ConvertTo-RunnerWslPath $contextPath $setup),
        '--outcomes', (ConvertTo-RunnerWslPath $outcomes $setup))
    Invoke-RunnerGate $setup $arguments

    $ready = Join-Path $temporary 'ready'
    $slot = Join-Path (Get-RunnerRoot) 'queue.lock'
    $entry = Join-Path $PSScriptRoot 'Hold-GitHubRunnerSlot.ps1'
    $command = "& '" + $entry.Replace("'", "''") + "' -WorkerId " + $worker.Id +
        ' -StartedTicks ' + $worker.StartTime.ToUniversalTime().Ticks + " -ReadyPath '" +
        $ready.Replace("'", "''") + "' -LockPath '" + $slot.Replace("'", "''") + "'"
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $info.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' +
        [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    # Runner orphan cleanup must not kill the guardian before its worker exits.
    $info.EnvironmentVariables.Clear()
    foreach ($name in @('SystemRoot', 'TEMP', 'TMP', 'LOCALAPPDATA', 'USERPROFILE', 'PATH', 'PSModulePath')) {
        $info.EnvironmentVariables[$name] = [Environment]::GetEnvironmentVariable($name)
    }
    $guardian = [Diagnostics.Process]::Start($info)
    Write-Host 'GitHub runner queue: waiting for the host job slot'
    while (-not (Test-Path -LiteralPath $ready)) {
        if ($guardian.HasExited -or $worker.HasExited) { throw 'Queue guardian unavailable' }
        Start-Sleep -Milliseconds 200
    }
    if ($guardian.HasExited) { throw 'Queue guardian unavailable' }
    Write-Host 'GitHub runner queue: host job slot acquired'
} catch {
    Write-Host 'GitHub runner denied: identity check or queue unavailable. Check runner status and WSL readiness.'
    # Failing the hook alone still permits steps with if: always(). End the
    # verified worker to keep those steps from executing after a denial.
    if ($ownsWorker -and -not $worker.HasExited) { $worker.Kill() }
    exit 1
} finally {
    if ($temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue }
    if ($guardian) { $guardian.Dispose() }
    if ($worker) { $worker.Dispose() }
}
