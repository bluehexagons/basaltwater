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

function Invoke-NativeSunshineServiceControl([string]$Operation) {
    $arguments = switch ($Operation) {
        'enable' { 'config SunshineService start= auto' }
        'start' { 'start SunshineService' }
        default { throw 'Unsupported Sunshine service operation' }
    }
    $systemDirectory = [Environment]::SystemDirectory
    $elevation = @{}
    if (-not (Test-NativeSunshineAdministrator)) { $elevation.Verb = 'RunAs' }
    $process = Start-Process -FilePath (Join-Path $systemDirectory 'sc.exe') -ArgumentList $arguments `
        -WorkingDirectory $systemDirectory -WindowStyle Hidden -Wait -PassThru -ErrorAction Stop @elevation
    try {
        # Starting concurrently with Windows or another operator is harmless.
        if ($process.ExitCode -ne 0 -and -not ($Operation -eq 'start' -and $process.ExitCode -eq 1056)) {
            throw "Sunshine service configuration failed (exit code $($process.ExitCode)); rerun setup to retry."
        }
    } finally { $process.Dispose() }
}

function Set-NativeSunshineService {
    $service = Get-Service -Name SunshineService -ErrorAction SilentlyContinue
    if (-not $service) { throw 'The Sunshine installer did not provide SunshineService. Repair the vendor installation with the service feature enabled.' }
    try {
        if ($service.StartType -ne 'Automatic') { Invoke-NativeSunshineServiceControl enable }
        $service.Refresh()
        # The service controller submits the start. Keep the bounded wait in
        # the original process, including for a service already starting.
        if ($service.Status -eq 'Stopped') { Invoke-NativeSunshineServiceControl start }
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
    if (-not $health.Healthy) { Set-NativeSunshineService }
    $health = Get-NativeSunshineHealth
    if (-not $health.Healthy) { throw $health.Detail }
    Write-Host 'Sunshine service is running and starts at Windows boot. Open https://localhost:47990 locally to set credentials and pair Moonlight.'
    Write-Host 'Verify the active display/GPU, video, sound and input from Moonlight. Service readiness alone does not establish streaming readiness.'
}
