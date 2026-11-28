<# Shared Windows and WSL bootstrap helpers; no work is performed when dot-sourced. #>
function Test-Winget {
    $command = Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $command) { return $false }
    $PSNativeCommandUseErrorActionPreference = $false
    try {
        $version = & $command.Source --version 2>$null
        return ($LASTEXITCODE -eq 0 -and [bool]$version)
    } catch {
        return $false
    }
}

function Ensure-Winget {
    if (Test-Winget) { return }
    try {
        Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe
    } catch {
        Write-Host 'App Installer registration did not make WinGet available; trying repair.'
    }
    if (Test-Winget) { return }
    Install-PackageProvider -Name NuGet -Scope CurrentUser -Force | Out-Null
    Install-Module -Name Microsoft.WinGet.Client -Scope CurrentUser -Force -Repository PSGallery
    Import-Module Microsoft.WinGet.Client
    Repair-WinGetPackageManager -Force -Latest
    if (-not (Test-Winget)) {
        throw 'WinGet is unavailable after App Installer registration and repair. Update App Installer, check winget --version in a fresh PowerShell window, then rerun setup.'
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

function Update-WingetPackages {
    Write-Host 'Upgrading installed packages from configured WinGet sources.'
    # Let the explicit exit-code handling below work under PowerShell 7 too.
    $PSNativeCommandUseErrorActionPreference = $false
    & winget.exe upgrade --all --disable-interactivity --silent `
        --accept-source-agreements --accept-package-agreements
    $exitCode = $LASTEXITCODE
    # An empty inventory or no applicable update is valid on fresh or current hosts.
    if ($exitCode -in @(-1978335212, -1978335189)) { return }
    # UPDATE_ALL_HAS_FAILURE can come from any installed app. Setup still
    # reconciles its own selected packages separately through exact installs.
    if ($exitCode -eq -1978335188) {
        Write-Warning "WinGet package upgrade completed with failures (exit code $exitCode). Basaltwater setup will continue. Review the per-package output above and retry the affected apps separately after resolving their errors or restarting if requested."
        return
    }
    Assert-WingetOperationSucceeded $exitCode 'WinGet package upgrade' 'WinGet bulk upgrade'
}

function Ensure-WingetPackage([string]$packageId, [string]$label, [string]$Override = '') {
    $PSNativeCommandUseErrorActionPreference = $false
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
    Assert-WingetOperationSucceeded $exitCode $label $packageId
}

function Assert-WingetOperationSucceeded([int]$exitCode, [string]$label, [string]$target) {
    if ($exitCode -eq 0) { return }
    # Raw installer exits and WinGet's mapped reboot HRESULTs.
    if ($exitCode -in @(1641, 3010, -1978334967, -1978334966, -1978334965)) {
        $failure = New-Object InvalidOperationException("$label requires a Windows restart. Restart, then rerun the same setup command.")
        $failure.Data['Basaltwater.WindowsRestartRequired'] = $true
        $failure.Data['Basaltwater.WindowsRestartReason'] = "$target (installer exit code $exitCode)"
        throw $failure
    }
    throw "$label failed (exit code $exitCode)"
}

function Get-UbuntuInitProcess([string]$Distribution) {
    $init = & wsl.exe --distribution $Distribution --user root --exec /usr/bin/cat /proc/1/comm
    if ($LASTEXITCODE -ne 0) { throw 'Could not check Ubuntu systemd readiness' }
    return ($init | Select-Object -Last 1)
}

function Get-UbuntuSetupArguments($Selection) {
    $arguments = @('--system-type', 'server_wsl', '--username', $Selection.LinuxUser, '--machine', 'wsl', '--refresh-packages')
    if ($Selection.Node) { $arguments += '--node' }
    if ($Selection.Python) { $arguments += '--python' }
    if ($Selection.Go) { $arguments += '--go' }
    foreach ($tool in $Selection.AgentTool) { $arguments += @('--agent-tool', $tool) }
    return $arguments
}
