<# Discover stable PowerShell 7 and preserve typed installer options at handoff. #>
function Test-NativePowerShell7Host {
    return $PSVersionTable.PSVersion.Major -ge 7 -and
        (-not $PSVersionTable.PSVersion.PSObject.Properties['PreReleaseLabel'] -or -not $PSVersionTable.PSVersion.PreReleaseLabel)
}

function Get-NativePowerShellCandidates {
    if (Test-NativePowerShell7Host) { Join-Path $PSHOME 'pwsh.exe' }
    if ($env:ProgramFiles) { Join-Path $env:ProgramFiles 'PowerShell\7\pwsh.exe' }
    # MSIX may be registered before its app execution alias reaches this process.
    if (Get-Command Get-AppxPackage -ErrorAction SilentlyContinue) {
        Get-AppxPackage -Name Microsoft.PowerShell -ErrorAction SilentlyContinue |
            ForEach-Object { if ($_.InstallLocation) { Join-Path $_.InstallLocation 'pwsh.exe' } }
    }
    # Prefer the actual executable for supervised processes; app execution
    # aliases remain a discovery fallback when no registered path is available.
    Get-Command pwsh.exe -CommandType Application -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source }
    if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\pwsh.exe' }
}

function Test-NativePowerShellExecutable([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    $process = New-Object Diagnostics.Process
    $process.StartInfo = New-Object Diagnostics.ProcessStartInfo
    $process.StartInfo.FileName = $Path
    $command = 'if ($PSVersionTable.PSVersion.Major -ge 7 -and -not $PSVersionTable.PSVersion.PreReleaseLabel) { exit 0 }; exit 1'
    $process.StartInfo.Arguments = '-NoLogo -NoProfile -NonInteractive -EncodedCommand ' +
        [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    try {
        if (-not $process.Start()) { return $false }
        if (-not $process.WaitForExit(5000)) {
            & (Join-Path $env:SystemRoot 'System32\taskkill.exe') /PID $process.Id /T /F 2>$null | Out-Null
            if (-not $process.WaitForExit(5000)) { throw 'PowerShell probe process did not exit' }
            return $false
        }
        return $process.ExitCode -eq 0
    } catch { return $false }
    finally { $process.Dispose() }
}

function Get-NativePowerShell7 {
    if (Test-NativePowerShell7Host) { return Join-Path $PSHOME 'pwsh.exe' }
    foreach ($candidate in @(Get-NativePowerShellCandidates | Select-Object -Unique)) {
        if ($candidate -and (Test-NativePowerShellExecutable $candidate)) { return [IO.Path]::GetFullPath($candidate) }
    }
    return $null
}

function Ensure-NativePowerShell7 {
    $shell = Get-NativePowerShell7
    if (-not $shell) {
        Ensure-Winget
        Ensure-WingetPackage Microsoft.PowerShell 'PowerShell 7 installation'
        Update-NativePath
        $shell = Get-NativePowerShell7
    }
    if (-not $shell) { throw 'Stable PowerShell 7 is unavailable after installation. Repair its WinGet installation/app execution alias and rerun setup.' }
    return $shell
}

function Get-NativeInteractivePowerShell7 {
    $shell = Get-NativePowerShell7
    if (-not $shell) { return $null }
    # MSIX package directories contain a version and disappear on update. UI
    # shortcuts should follow the registered stable alias when it is usable.
    $alias = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\pwsh.exe'
    if (Test-NativePowerShellExecutable $alias) { return $alias }
    return $shell
}

function Invoke-NativePowerShell7Installer([string]$Shell, [string]$Installer, [Collections.IDictionary]$Options) {
    $forward = @{}
    foreach ($key in $Options.Keys) {
        $value = $Options[$key]
        if ($value -is [Management.Automation.SwitchParameter]) { $value = [bool]$value }
        $forward[$key] = $value
    }
    $payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([Management.Automation.PSSerializer]::Serialize($forward)))
    $literal = [IO.Path]::GetFullPath($Installer).Replace("'", "''")
    $temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-pwsh-' + [guid]::NewGuid().ToString('N') + '.ps1')
    $previous = $ErrorActionPreference
    try {
        $command = "`$options = [Management.Automation.PSSerializer]::Deserialize([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$payload'))); `$global:LASTEXITCODE = 0; & '$literal' @options; if (-not `$?) { if (`$LASTEXITCODE -ne 0) { exit `$LASTEXITCODE }; exit 1 }"
        Set-Content -LiteralPath $temporary -Value $command -Encoding UTF8 -ErrorAction Stop
        $ErrorActionPreference = 'Continue'
        $global:LASTEXITCODE = -1
        & $Shell -NoLogo -NoProfile -ExecutionPolicy Bypass -File $temporary 2>&1 | ForEach-Object { Write-Host "$_" }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
    if ($code -ne 0) { throw "PowerShell 7 setup failed (exit code $code). Inspect saved setup and rerun the installer." }
}
