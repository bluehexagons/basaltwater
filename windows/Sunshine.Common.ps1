<# Optional native streaming host. The vendor installer owns service/firewall files. #>
function Test-NativeSunshineAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-NativeSunshineHealth {
    $service = Get-Service -Name SunshineService -ErrorAction SilentlyContinue
    if (-not $service) {
        return [pscustomobject]@{ Healthy = $false; State = 'missing'; Detail = 'Install the native LizardByte.Sunshine package with its Windows service.' }
    }
    try {
        $ready = $service.Status -eq 'Running' -and $service.StartType -eq 'Automatic'
        return [pscustomobject]@{ Healthy = $ready; State = $(if ($ready) { 'ok' } else { 'failed' });
            Detail = "SunshineService: $($service.Status), startup: $($service.StartType). Video, audio, input and pairing require a Moonlight check." }
    } finally { $service.Dispose() }
}

function Set-NativeSunshineService {
    $service = Get-Service -Name SunshineService -ErrorAction SilentlyContinue
    if (-not $service) { throw 'The Sunshine installer did not provide SunshineService. Repair the vendor installation with the service feature enabled.' }
    try {
        Set-Service -Name SunshineService -StartupType Automatic -ErrorAction Stop
        $service.Refresh()
        # Start-Service itself waits without a bound in StartPending. Start()
        # submits the request, leaving our explicit timeout in control.
        if ($service.Status -eq 'Stopped') { $service.Start() }
        elseif ($service.Status -notin @('Running', 'StartPending')) { throw 'Sunshine service is stopping or paused; resolve its state before retrying setup.' }
        $service.WaitForStatus('Running', [timespan]::FromSeconds(30))
    } finally { $service.Dispose() }
}

function Install-NativeSunshine([switch]$Plan) {
    if ($Plan) {
        Write-Host 'Install/update LizardByte.Sunshine through WinGet; enable and start the vendor SunshineService at Windows boot (UAC may be required).'
        Write-Host 'The installer manages firewall rules; credentials, pairing and input-driver selection remain manual.'
        return
    }
    Ensure-Winget
    Ensure-WingetPackage LizardByte.Sunshine 'Sunshine installation'
    $health = Get-NativeSunshineHealth
    if ($health.State -eq 'missing') { throw $health.Detail }
    if (-not $health.Healthy) {
        if (Test-NativeSunshineAdministrator) { Set-NativeSunshineService }
        else {
            # Elevate only machine-wide service configuration, retaining the
            # intended user's PATH, skills, selections and provider logins.
            $helper = Join-Path $PSScriptRoot 'Sunshine.Common.ps1'
            $command = "`$ErrorActionPreference = 'Stop'; . '" + $helper.Replace("'", "''") + "'; Set-NativeSunshineService"
            $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
            $process = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
                -ArgumentList ('-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' + $encoded) `
                -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ErrorAction Stop
            try {
                if ($process.ExitCode -ne 0) { throw "Sunshine service configuration failed (exit code $($process.ExitCode)); rerun setup to retry." }
            } finally { $process.Dispose() }
        }
    }
    $health = Get-NativeSunshineHealth
    if (-not $health.Healthy) { throw $health.Detail }
    Write-Host 'Sunshine service is running and starts at Windows boot. Open https://localhost:47990 locally to set credentials and pair Moonlight.'
    Write-Host 'Verify the active display/GPU, video, sound and input from Moonlight. Service readiness alone does not establish streaming readiness.'
}
