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

function ConvertTo-WingetOverride([string]$value) {
    # Legacy native argument passing removes embedded quotes. Escape them for
    # the Windows argv parser while keeping the vendor override one argument.
    $mode = Get-Variable PSNativeCommandArgumentPassing -ValueOnly -ErrorAction SilentlyContinue
    if ($PSVersionTable.PSVersion -lt [version]'7.3' -or $mode -eq 'Legacy') {
        return $value.Replace('"', '\"')
    }
    return $value
}

function Ensure-WingetPackage([string]$packageId, [string]$label, [string]$Override = '') {
    $arguments = @('install', '--id', $packageId, '--exact', '--source', 'winget',
                   '--accept-source-agreements', '--accept-package-agreements')
    if ($Override) { $arguments += @('--override', (ConvertTo-WingetOverride $Override)) }
    & winget.exe @arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -eq 0) { return }
    # An already-current install still needs evidence.
    if ($exitCode -in @(-1978335189, -1978335135)) {
        & winget.exe list --id $packageId --exact --source winget --accept-source-agreements | Out-Null
        if ($LASTEXITCODE -eq 0) { return }
    }
    # Raw installer exits and WinGet's mapped reboot HRESULTs.
    if ($exitCode -in @(1641, 3010, -1978334967, -1978334966, -1978334965)) {
        $failure = New-Object InvalidOperationException("$label requires a Windows restart. Restart, then rerun the same setup command.")
        $failure.Data['Basaltwater.WindowsRestartRequired'] = $true
        $failure.Data['Basaltwater.WindowsRestartReason'] = "$packageId (installer exit code $exitCode)"
        throw $failure
    }
    throw "$label failed (exit code $exitCode)"
}
