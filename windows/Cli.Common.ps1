<# Native launcher installation and the basic Windows command surface. #>
function Install-NativeCli([string]$Source, [switch]$Plan) {
    $bin = Join-Path $env:LOCALAPPDATA 'Basaltwater\bin'
    $files = @('basaltw.cmd', 'Invoke-Basaltw.ps1', 'Cli.Common.ps1')
    foreach ($file in $files) {
        Assert-NativeSkillPath (Join-Path $bin $file)
        if (-not (Test-Path -LiteralPath (Join-Path $Source "windows\$file") -PathType Leaf)) {
            throw "Native CLI source is incomplete: $file"
        }
    }
    Assert-NativeSkillPath (Join-Path $bin 'cli-source.json')
    $metadata = Get-Content -LiteralPath (Join-Path $Source 'pyproject.toml') -Raw
    if ($metadata -notmatch '(?m)^version\s*=\s*"([A-Za-z0-9.+-]+)"\s*$') { throw 'Missing Basaltwater package version' }
    $version = $Matches[1]
    Write-Host "Native basaltw command: $bin (help, version, info, doctor, refresh)"
    if ($Plan) { return }
    New-Item -ItemType Directory -Path $bin -Force | Out-Null
    foreach ($file in $files) {
        Copy-Item -LiteralPath (Join-Path $Source "windows\$file") -Destination (Join-Path $bin $file) -Force
    }
    @{ Source = [IO.Path]::GetFullPath($Source); PackageVersion = $version } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $bin 'cli-source.json') -Encoding UTF8
    Add-NativeUserPath $bin
}

function Read-NativeCliSetup {
    $path = Join-Path $env:LOCALAPPDATA 'Basaltwater\windows-setup.json'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw 'No saved native Windows setup. Run install.ps1 -Profile agent_windows first; checkout-only setup does not save refresh selections.'
    }
    $saved = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if ($saved.Version -ne 1 -or $saved.OwnerSid -ne $sid -or $saved.Profile -ne 'agent_windows' -or
        $saved.Revision -notmatch '\A[0-9a-f]{40}\z' -or $saved.Source -notmatch '\A[A-Za-z]:[\\/]' -or
        -not (Test-Path -LiteralPath (Join-Path $saved.Source 'install.ps1') -PathType Leaf)) {
        throw 'Saved Windows setup does not match this account, profile or source; repair the saved installation before refreshing.'
    }
    return $saved
}

function Show-NativeCliHelp {
    Write-Host @'
Usage: basaltw COMMAND [OPTIONS]

Native Windows commands:
  --help, help       Show this help
  --version, version Show the installed Basaltwater version
  info [--json]      Show this account's saved native setup
  doctor [--json]    Check selected native tools without applying setup
  refresh           Update Basaltwater from main and reapply saved selections

Refresh options:
  --dry-run         Preview saved/current selections without writes or downloads
  --game-dev        Add native game build tools, Python, Go and fnm
  --shader-tools    Add Vulkan SDK shader validation
  --godot           Add the standard Godot editor/CLI
  --node-versions   Add fnm for project-selected Node versions
  --storage-root PATH  Select a mounted NTFS directory for development caches
  --storage-drive DRIVE  Use DRIVE:\Basaltwater (for example: --storage-drive F:)
  --channel main|dev   Select the source channel (both currently track main)
  --version REF        Select a specific source reference

Refresh preserves installed tool versions and provider logins. Package upgrades
remain deliberate. Successful setup queues a graceful T3 Code start/restart.
Other open terminals need to be reopened after setup changes PATH.
Linux controller, server, desktop and agent commands require a Linux installation.
'@
}

function Invoke-NativeCli([string[]]$Arguments) {
    if (-not $Arguments -or ($Arguments.Count -eq 1 -and $Arguments[0] -in @('--help', '-h', 'help'))) {
        Show-NativeCliHelp
        return 0
    }
    $command = $Arguments[0]
    $options = @($Arguments | Select-Object -Skip 1)
    if ($command -in @('--version', 'version')) {
        if ($options.Count) { throw 'Usage: basaltw --version' }
        $receipt = Join-Path $PSScriptRoot 'cli-source.json'
        if (Test-Path -LiteralPath $receipt) {
            $version = (Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json).PackageVersion
        } else {
            $metadata = Get-Content -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) 'pyproject.toml') -Raw
            if ($metadata -notmatch '(?m)^version\s*=\s*"([A-Za-z0-9.+-]+)"\s*$') { throw 'Missing Basaltwater package version' }
            $version = $Matches[1]
        }
        Write-Host "Basaltwater $version (native Windows)"
        return 0
    }
    if ($command -eq 'info') {
        if ($options.Count -gt 1 -or ($options.Count -eq 1 -and $options[0] -ne '--json')) { throw 'Usage: basaltw info [--json]' }
        $saved = Read-NativeCliSetup
        $info = [ordered]@{ Platform = 'windows'; Profile = $saved.Profile; Revision = $saved.Revision;
            Source = $saved.Source; Phase = $saved.Phase }
        foreach ($name in @('AgentTool', 'T3CodeDesktop', 'Node', 'Python', 'Go', 'NodeVersions', 'GameDev', 'ShaderTools', 'Godot', 'StorageRoot')) {
            if ($saved.PSObject.Properties[$name]) { $info[$name] = $saved.$name }
        }
        if ($options -contains '--json') { Write-Host ($info | ConvertTo-Json -Depth 4) }
        else { Write-Host (($info.GetEnumerator() | ForEach-Object { "  $($_.Key): $($_.Value -join ', ')" }) -join "`n") }
        return 0
    }
    if ($command -eq 'doctor') {
        if ($options.Count -eq 1 -and $options[0] -in @('--help', '-h')) { Show-NativeCliHelp; return 0 }
        if ($options.Count -gt 1 -or ($options.Count -eq 1 -and $options[0] -ne '--json')) { throw 'Usage: basaltw doctor [--json]' }
        # Use this CLI's source, including when saved setup is interrupted at a
        # different revision. The diagnostic itself reads the saved selections.
        $source = Split-Path -Parent $PSScriptRoot
        $receipt = Join-Path $PSScriptRoot 'cli-source.json'
        if (Test-Path -LiteralPath $receipt) { $source = (Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json).Source }
        $entry = Join-Path $source 'windows\Test-AgentWorkstation.ps1'
        if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) { throw 'Windows diagnostic source is missing; refresh Basaltwater from a current installer.' }
        $diagnosticOptions = @()
        if ($options -contains '--json') { $diagnosticOptions += '-Json' }
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $entry @diagnosticOptions |
            ForEach-Object { Write-Host $_ }
        return $LASTEXITCODE
    }
    if ($command -ne 'refresh') { throw "Unsupported Windows command '$command'. Run basaltw --help for available commands." }
    if ($options.Count -eq 1 -and $options[0] -in @('--help', '-h')) { Show-NativeCliHelp; return 0 }
    $forward = @('-Profile', 'agent_windows', '-Resume', '-UpdateSource')
    $switches = @{ '--dry-run' = '-Plan'; '--game-dev' = '-GameDev'; '--shader-tools' = '-ShaderTools';
                   '--godot' = '-Godot'; '--node-versions' = '-NodeVersions' }
    $values = @{ '--storage-root' = '-StorageRoot'; '--storage-drive' = '-StorageRoot'; '--channel' = '-Channel'; '--version' = '-Version' }
    $storageSelected = $false
    for ($index = 0; $index -lt $options.Count; $index++) {
        $option = $options[$index]
        if ($switches.ContainsKey($option)) { $forward += $switches[$option]; continue }
        if ($values.ContainsKey($option)) {
            $index++
            if ($index -ge $options.Count -or -not $options[$index] -or $options[$index].StartsWith('-')) {
                throw "Missing value for $option"
            }
            $value = $options[$index]
            if ($option -in @('--storage-root', '--storage-drive')) {
                if ($storageSelected) { throw 'Choose one --storage-drive or --storage-root option' }
                $storageSelected = $true
            }
            if ($option -eq '--storage-drive') {
                if ($value -notmatch '\A[A-Za-z]:?\z') { throw 'Storage drive must be one drive letter, such as F or F:' }
                $value = $value.Substring(0, 1).ToUpperInvariant() + ':\Basaltwater'
            }
            if ($option -eq '--channel' -and $value -notin @('main', 'dev')) { throw 'Channel must be main or dev' }
            if ($option -eq '--version' -and $value -notmatch '\A[A-Za-z0-9._-]+\z') { throw 'Invalid release reference' }
            if ($option -eq '--storage-root' -and ($value -notmatch '\A[A-Za-z]:[\\/]' -or $value -match '["\x00-\x1f]')) {
                throw 'Storage root must be an absolute local Windows drive path'
            }
            # A trailing backslash can escape PS 5.1's native argument quotes.
            if ($option -eq '--storage-root' -and $value.Length -gt 3) { $value = $value.TrimEnd('\', '/') }
            $forward += @($values[$option], $value)
            continue
        }
        throw "Unsupported refresh option '$option'. Run basaltw refresh --help."
    }
    $saved = Read-NativeCliSetup
    $previousPreference = $ErrorActionPreference
    try {
        # PS 5.1 converts redirected native stderr (including harmless tool
        # warnings) to error records. The installer exit code owns success.
        $ErrorActionPreference = 'Continue'
        & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File (Join-Path $saved.Source 'install.ps1') @forward 2>&1 |
            ForEach-Object { Write-Host "$_" }
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    return $code
}
