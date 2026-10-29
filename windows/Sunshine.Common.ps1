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
    $ready = $service.Status -eq 'Running' -and $service.StartType -eq 'Automatic'
    return [pscustomobject]@{ Healthy = $ready; State = $(if ($ready) { 'ok' } else { 'failed' });
        Detail = "SunshineService: $($service.Status), startup: $($service.StartType). Video, audio, input and pairing require a Moonlight check." }
}

function Install-NativeSunshine([switch]$Plan) {
    if ($Plan) {
        Write-Host 'Install/update LizardByte.Sunshine through WinGet; enable and start the vendor SunshineService at Windows boot.'
        Write-Host 'Requires an administrator PowerShell. The installer manages firewall rules; credentials, pairing and input-driver selection remain manual.'
        return
    }
    if (-not (Test-NativeSunshineAdministrator)) { throw 'Sunshine service setup requires an administrator PowerShell. Preview with -Plan or rerun this command elevated.' }
    Ensure-Winget
    Ensure-WingetPackage LizardByte.Sunshine 'Sunshine installation'
    $service = Get-Service -Name SunshineService -ErrorAction SilentlyContinue
    if (-not $service) { throw 'The Sunshine installer did not provide SunshineService. Repair the vendor installation with the service feature enabled.' }
    Set-Service -Name SunshineService -StartupType Automatic -ErrorAction Stop
    Start-Service -Name SunshineService -ErrorAction Stop
    $service.WaitForStatus('Running', [timespan]::FromSeconds(30))
    $health = Get-NativeSunshineHealth
    if (-not $health.Healthy) { throw $health.Detail }
    Write-Host 'Sunshine service is running and starts at Windows boot. Open https://localhost:47990 locally to set credentials and pair Moonlight.'
    Write-Host 'Verify the active display/GPU, video, sound and input from Moonlight. Service readiness alone does not establish streaming readiness.'
}
