<# Shared Windows package bootstrap; no work is performed when dot-sourced. #>
function Ensure-Winget {
    if (Get-Command winget.exe -ErrorAction SilentlyContinue) { return }
    try {
        Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe
    } catch {
        Write-Host 'App Installer registration did not make WinGet available; trying repair.'
    }
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        Install-PackageProvider -Name NuGet -Scope CurrentUser -Force | Out-Null
        Install-Module -Name Microsoft.WinGet.Client -Scope CurrentUser -Force -Repository PSGallery
        Import-Module Microsoft.WinGet.Client
        Repair-WinGetPackageManager -Force -Latest
    }
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw 'WinGet is unavailable after App Installer registration/repair'
    }
}

function Ensure-WingetPackage([string]$packageId, [string]$label, [switch]$NoUpgrade) {
    $arguments = @('install', '--id', $packageId, '--exact', '--source', 'winget',
                   '--accept-source-agreements', '--accept-package-agreements')
    if ($NoUpgrade) { $arguments += '--no-upgrade' }
    & winget.exe @arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -eq 0) { return }
    # An already-current (or deliberately preserved) install still needs evidence.
    # UPDATE_NOT_APPLICABLE and PACKAGE_ALREADY_INSTALLED (--no-upgrade).
    if ($exitCode -in @(-1978335189, -1978335135)) {
        & winget.exe list --id $packageId --exact --source winget --accept-source-agreements | Out-Null
        if ($LASTEXITCODE -eq 0) { return }
    }
    # Raw installer exits and WinGet's mapped reboot HRESULTs.
    if ($exitCode -in @(1641, 3010, -1978334967, -1978334966, -1978334965)) {
        throw "$label requires a Windows restart. Restart, then rerun the same setup command."
    }
    throw "$label failed (exit code $exitCode)"
}
