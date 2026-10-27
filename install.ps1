<# Windows 11 bootstrap. Run as the intended coding/desktop account. #>
[CmdletBinding()]
param(
    [ValidateSet("server_wsl", "agent_windows")][string]$Profile = "server_wsl",
    [switch]$BuildServer,
    [switch]$T3CodeDesktop,
    [switch]$Node,
    [switch]$Python,
    [switch]$Go,
    [switch]$NodeVersions,
    [switch]$GameDev,
    [switch]$ShaderTools,
    [string]$StorageRoot = '',
    [string[]]$AgentTool = @(),
    [string]$LinuxUser = "",
    [string]$DistroName = "",
    [string]$DistroLocation = "",
    [ValidateSet("main", "dev")][string]$Channel = "main",
    [string]$Version = "",
    [string[]]$CIRepository = @(),
    [string]$CIOrganization = '',
    [string[]]$CIAllowedUser = @(),
    [string[]]$CILabel = @('windows-ci', 'basaltwater-wsl'),
    [string]$CIRunnerGroup = '',
    [switch]$CIRegisterWithGh,
    [string]$CIRegistrationTokenFile = '',
    [switch]$CIPromptToken,
    [switch]$Plan,
    [switch]$UpdateSource,
    [switch]$Resume
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$stateDirectory = Join-Path $env:LOCALAPPDATA "Basaltwater"
$statePath = Join-Path $stateDirectory $(if ($Profile -eq 'agent_windows') { 'windows-setup.json' } else { 'wsl-setup.json' })
$sourceMarker = if ($Profile -eq 'agent_windows') { 'windows\AgentWorkstation.Common.ps1' } else { 'windows\wsl_prepare.py' }
$owner = [Security.Principal.WindowsIdentity]::GetCurrent()
$setupLock = $null
$runnerHelpers = Join-Path $PSScriptRoot 'windows\GitHubRunner.Common.ps1'
if (Test-Path -LiteralPath $runnerHelpers) { . $runnerHelpers }
$bootstrapHelpers = Join-Path $PSScriptRoot 'windows\Bootstrap.Common.ps1'
if (Test-Path -LiteralPath $bootstrapHelpers) { . $bootstrapHelpers }

function Save-State($value) {
    if (-not (Test-Path -LiteralPath $stateDirectory)) {
        New-Item -ItemType Directory -Path $stateDirectory | Out-Null
    }
    $temporary = Join-Path $stateDirectory ("state-" + [guid]::NewGuid() + ".json")
    $value | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $statePath -Force
}

function Test-SourceFile([string]$path) {
    return [IO.File]::Exists('\\?\' + $path)
}

function Read-State {
    $saved = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if ($saved.Version -ne 1 -or $saved.OwnerSid -ne $owner.User.Value -or
        $saved.Revision -notmatch '\A[0-9a-f]{40}\z' -or
        -not (Test-SourceFile (Join-Path $saved.Source $sourceMarker)) -or
        ($saved.PSObject.Properties['Profile'] -and $saved.Profile -ne $Profile)) {
        throw 'Saved setup state does not match this account, profile or source'
    }
    foreach ($option in @('NodeVersions', 'GameDev', 'ShaderTools')) {
        if (-not $saved.PSObject.Properties[$option]) {
            $saved | Add-Member -NotePropertyName $option -NotePropertyValue $false
        }
    }
    if (-not $saved.PSObject.Properties['StorageRoot']) { $saved | Add-Member -NotePropertyName StorageRoot -NotePropertyValue '' }
    return $saved
}

function Invoke-Checked([string]$label, [scriptblock]$command) {
    & $command
    if ($LASTEXITCODE -ne 0) {
        throw "$label failed (exit code $LASTEXITCODE)"
    }
}

function Get-NativeOptionalArguments($selection) {
    $arguments = @{}
    $supported = (Get-Command Invoke-NativeAgentSetup).Parameters
    foreach ($option in @('NodeVersions', 'GameDev', 'ShaderTools', 'StorageRoot')) {
        if ($selection.$option) {
            if (-not $supported.ContainsKey($option)) { throw "Pinned source lacks $option; use the current installer with -Resume -UpdateSource." }
            $arguments[$option] = $selection.$option
        }
    }
    return $arguments
}

function Assert-NativeSourceOptions([string]$source, $selection) {
    $selected = @('NodeVersions', 'GameDev', 'ShaderTools', 'StorageRoot') | Where-Object { $selection.$_ }
    if (-not $selected) { return }
    $tokens = $null; $errors = $null
    $installer = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $source 'install.ps1'), [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw 'Selected source installer has invalid PowerShell syntax' }
    $parameters = @($installer.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
    foreach ($option in $selected) {
        if ($option -notin $parameters) { throw "Selected source does not support $option; select a newer Basaltwater revision." }
    }
}

function Check-Host {
    $system = Get-CimInstance Win32_OperatingSystem
    if ($system.ProductType -ne 1 -or [int]$system.BuildNumber -lt 22000 -or
        $env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
        throw "A fully updated x86-64 Windows 11 desktop installation is required"
    }
    $drive = Get-PSDrive -Name ([IO.Path]::GetPathRoot($stateDirectory).Substring(0, 1))
    $requiredSpace = if ($Profile -eq 'agent_windows') { 5GB } else { 15GB }
    if ($drive.Free -lt $requiredSpace) {
        throw "At least $($requiredSpace / 1GB) GiB of free space is required for the selected Windows setup"
    }
}

function Expand-SourceArchive([string]$archive, [string]$destination) {
    # PowerShell 5.1's Expand-Archive fails on long staging paths. Extended
    # Win32 paths let .NET copy entries without changing machine-wide policy.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $destination = [IO.Path]::GetFullPath($destination).TrimEnd('\')
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        foreach ($entry in $zip.Entries) {
            $relative = $entry.FullName.Replace('/', '\').TrimEnd('\')
            $parts = @($relative -split '\\')
            if (-not $relative -or $relative -match '[<>:"|?*\x00-\x1f]' -or
                @($parts | Where-Object { $_ -in @('', '.', '..') -or $_ -match '[. ]\z' -or
                    $_ -match '\A(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|\z)' }).Count -or
                (($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) {
                throw "Unsafe source archive entry: $($entry.FullName)"
            }
            $target = '\\?\' + $destination + '\' + $relative
            if ($entry.FullName.EndsWith('/') -or $entry.FullName.EndsWith('\')) {
                [IO.Directory]::CreateDirectory($target) | Out-Null
                continue
            }
            [IO.Directory]::CreateDirectory($target.Substring(0, $target.LastIndexOf('\'))) | Out-Null
            $inputStream = $entry.Open()
            try {
                $outputStream = [IO.File]::Open($target, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
                try { $inputStream.CopyTo($outputStream) } finally { $outputStream.Dispose() }
            } finally { $inputStream.Dispose() }
        }
    } finally { $zip.Dispose() }
}

function Resolve-Source([string]$reference) {
    if ($reference -notmatch '\A[A-Za-z0-9._-]+\z') {
        throw "Invalid release reference"
    }
    $commit = Invoke-RestMethod -UseBasicParsing -Uri ("https://api.github.com/repos/bluehexagons/basaltwater/commits/" + $reference)
    $sha = [string]$commit.sha
    if ($sha -notmatch '\A[0-9a-f]{40}\z') {
        throw "Could not resolve a full source commit SHA"
    }
    New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
    $root = Join-Path $stateDirectory ("source\" + $sha)
    if (-not (Test-SourceFile (Join-Path $root $sourceMarker)) -or
        -not (Test-SourceFile (Join-Path $root "install.ps1"))) {
        if (Test-Path -LiteralPath $root) { throw "Pinned source directory is incomplete: $root" }
        $archive = Join-Path $stateDirectory ("source-" + $sha + ".zip")
        $unpack = Join-Path $stateDirectory ("unpack-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $unpack | Out-Null
        try {
            Invoke-WebRequest -UseBasicParsing -Uri ("https://github.com/bluehexagons/basaltwater/archive/" + $sha + ".zip") -OutFile $archive
            Expand-SourceArchive $archive $unpack
            $children = @(Get-ChildItem -LiteralPath $unpack -Directory)
            if ($children.Count -ne 1 -or -not (Test-SourceFile (Join-Path $children[0].FullName $sourceMarker))) {
                throw "Downloaded source archive is incomplete"
            }
            New-Item -ItemType Directory -Path (Split-Path -Parent $root) -Force | Out-Null
            $unpackRoot = [IO.Path]::GetFullPath($unpack).TrimEnd('\') + '\'
            $sourceRoot = [IO.Path]::GetFullPath($stateDirectory).TrimEnd('\') + '\source\'
            if (-not $children[0].FullName.StartsWith($unpackRoot, [StringComparison]::OrdinalIgnoreCase) -or
                -not $root.StartsWith($sourceRoot, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Refusing to move source outside the staging/setup directories'
            }
            [IO.Directory]::Move('\\?\' + $children[0].FullName, '\\?\' + $root)
        } finally {
            $unpackFull = [IO.Path]::GetFullPath($unpack)
            $stateRoot = [IO.Path]::GetFullPath($stateDirectory).TrimEnd('\') + '\'
            if (-not $unpackFull.StartsWith($stateRoot, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Refusing source cleanup outside the setup directory'
            }
            if ([IO.Directory]::Exists('\\?\' + $unpackFull)) { [IO.Directory]::Delete('\\?\' + $unpackFull, $true) }
            Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
        }
    }
    return @{ Revision = $sha; Source = $root }
}

function Ensure-Wsl {
    & wsl.exe --status 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { return $true }
    Write-Host "Enabling WSL. Windows may require an administrator prompt."
    $process = Start-Process -FilePath "wsl.exe" -ArgumentList @("--install", "--no-distribution") -Verb RunAs -Wait -PassThru
    if ($process.ExitCode -ne 0 -and $process.ExitCode -ne 3010) {
        throw "WSL installation failed (exit code $($process.ExitCode))"
    }
    return $false
}

function Ensure-UbuntuPython {
    $pythonCheck = @('/usr/bin/python3', '-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 2)')
    & wsl.exe --distribution $state.Distro --user root --exec @pythonCheck 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { return }
    if ($LASTEXITCODE -eq 2) {
        throw "Ubuntu system Python 3.11 or newer is required; select a supported Ubuntu distro. Setup does not upgrade the Ubuntu release."
    }
    Invoke-Checked "Ubuntu Python prerequisite package index" {
        & wsl.exe --distribution $state.Distro --user root --exec /usr/bin/apt-get update -qq
    }
    Invoke-Checked "Ubuntu Python prerequisite" {
        & wsl.exe --distribution $state.Distro --user root --exec /usr/bin/env `
            DEBIAN_FRONTEND=noninteractive /usr/bin/apt-get install -y -qq python3
    }
    Invoke-Checked "Ubuntu Python 3.11+ prerequisite verification" {
        & wsl.exe --distribution $state.Distro --user root --exec @pythonCheck
    }
}

function Select-Distro {
    if ($state.Distro) { return }
    $available = & wsl.exe --list --online
    if ($LASTEXITCODE -ne 0) { throw "Could not list official WSL distributions" }
    $catalog = ($available -join "`n") -replace [char]0, ''
    $names = @([regex]::Matches($catalog, '(?m)^\s*(Ubuntu-\d{2}\.\d{2})(?=\s|$)') |
        ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique -Descending)
    if (-not $names) { throw "No versioned stable Ubuntu distribution is available from WSL" }
    $state.Distro = $names[0]
    Save-State $state
}

function Ensure-Distro {
    $installed = (& wsl.exe --list --quiet | Out-String) -replace [char]0, ''
    if ($LASTEXITCODE -ne 0) { throw "Could not list installed WSL distributions" }
    if ($installed -match ("(?m)^" + [regex]::Escape($state.Distro) + "\s*$")) {
        if ($state.DistroSelection -ne 'explicit' -and -not $state.DistroOwned) {
            throw "Existing distro was not explicitly selected or confirmed created by setup. Rerun -Resume -DistroName $($state.Distro) to authorize reuse."
        }
        return
    }
    $online = (& wsl.exe --list --online | Out-String) -replace [char]0, ''
    if ($LASTEXITCODE -ne 0 -or $online -notmatch ("\b" + [regex]::Escape($state.Distro) + "\b")) {
        throw "Selected Ubuntu distribution is neither installed nor available online"
    }
    $arguments = @('--install', '--distribution', $state.Distro, '--no-launch')
    if ($state.DistroLocation) { $arguments += @('--location', $state.DistroLocation) }
    Invoke-Checked "Ubuntu installation" { & wsl.exe @arguments }
    $state.DistroOwned = $true
    Save-State $state
}

function Initialize-DistroSelection {
    # Old states cannot establish whether the user explicitly selected a distro.
    if (-not $state.PSObject.Properties['DistroSelection']) {
        $state | Add-Member -NotePropertyName DistroSelection -NotePropertyValue 'unknown'
    }
    if (-not $state.PSObject.Properties['DistroOwned']) {
        $state | Add-Member -NotePropertyName DistroOwned -NotePropertyValue $false
    }
    if (-not $state.PSObject.Properties['RestartRequired']) {
        $state | Add-Member -NotePropertyName RestartRequired -NotePropertyValue $false
    }
    if ($DistroName) {
        if ($state.Distro -and $state.Distro -ne $DistroName) {
            throw 'Resume -DistroName must match the saved distribution'
        }
        $state.Distro = $DistroName
        $state.DistroSelection = 'explicit'
    }
    Save-State $state
}

function Ensure-UbuntuConfiguration([string]$prepare) {
    $pendingRestart = $state.RestartRequired
    # Persist before Linux configuration: an interrupted run must still restart
    # even when the next configure invocation reports UNCHANGED.
    $state.RestartRequired = $true
    Save-State $state
    $changed = & wsl.exe --distribution $state.Distro --user root --exec /usr/bin/python3 $prepare configure --user $state.LinuxUser
    if ($LASTEXITCODE -ne 0) { throw "Ubuntu user/systemd preparation failed" }
    $pidOne = & wsl.exe --distribution $state.Distro --user root --exec /usr/bin/cat /proc/1/comm
    if ($LASTEXITCODE -ne 0) { throw 'Could not check Ubuntu systemd readiness' }
    if ($pendingRestart -or ($changed | Select-Object -Last 1) -eq 'CHANGED' -or
        ($pidOne | Select-Object -Last 1) -ne 'systemd') {
        Invoke-Checked "WSL distribution restart" { & wsl.exe --terminate $state.Distro }
    }
    $state.RestartRequired = $false
    Save-State $state
}

function Wsl-Path([string]$windowsPath) {
    $path = & wsl.exe --distribution $state.Distro --user root --exec wslpath -u $windowsPath
    if ($LASTEXITCODE -ne 0 -or -not $path) { throw "Could not translate a Windows source path for WSL" }
    return ($path | Select-Object -Last 1).Trim()
}

function Ensure-WorkerTask {
    if (-not $state.BuildServer) { return }
    $taskName = "Basaltwater WSL worker"
    $taskScript = Join-Path $state.Source "windows\Start-WslWorker.ps1"
    $arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' +
        $taskScript + '" -Distribution "' + $state.Distro + '"'
    $userName = $owner.Name
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $userName
    $principal = New-ScheduledTaskPrincipal -UserId $userName -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
}

try {
    Check-Host
    if ($UpdateSource -and -not $Resume) { throw 'UpdateSource requires Resume' }
    if ($Profile -eq 'agent_windows') {
        foreach ($option in @('BuildServer', 'LinuxUser', 'DistroName', 'DistroLocation',
                              'CIRepository', 'CIOrganization', 'CIAllowedUser', 'CILabel',
                              'CIRunnerGroup', 'CIRegisterWithGh', 'CIRegistrationTokenFile', 'CIPromptToken')) {
            if ($PSBoundParameters.ContainsKey($option)) { throw "$option is not available with agent_windows" }
        }
        if (-not $PSBoundParameters.ContainsKey('T3CodeDesktop')) { $T3CodeDesktop = $true }
    } else {
        foreach ($option in @('NodeVersions', 'GameDev', 'ShaderTools', 'StorageRoot')) {
            if ($PSBoundParameters.ContainsKey($option)) { throw "$option requires agent_windows" }
        }
    }
    if ($LinuxUser -and ($LinuxUser -notmatch '^[a-z_][a-z0-9_-]{0,31}$' -or $LinuxUser -eq "root")) {
        throw "Invalid Ubuntu username"
    }
    if ($DistroName -and $DistroName -notmatch '^Ubuntu(?:-[0-9]{2}\.[0-9]{2})?$') {
        throw "Distribution name must be an official stable Ubuntu WSL name"
    }
    if ($DistroLocation -and $DistroLocation -notmatch '^[A-Za-z]:[\\/]') {
        throw "Distribution location must be an absolute Windows drive path"
    }
    if ($Version -and $Version -notmatch '\A[A-Za-z0-9._-]+\z') { throw "Invalid release reference" }
    $runnerConfig = $null
    if ($CIRepository.Count -or $CIOrganization) {
        # The download-only entry point has no companion files. The pinned
        # installer validates the saved options before any Windows/WSL changes.
        if (Get-Command Assert-RunnerArguments -ErrorAction SilentlyContinue) {
            Assert-RunnerArguments $CIRepository $CIOrganization $CIAllowedUser $CILabel $CIRunnerGroup `
                $CIRegisterWithGh $CIRegistrationTokenFile $CIPromptToken
        }
        $runnerConfig = [pscustomobject]@{ Repository = @($CIRepository); Organization = $CIOrganization;
            AllowedUser = @($CIAllowedUser); Label = @($CILabel); RunnerGroup = $CIRunnerGroup }
    } elseif (($CIAllowedUser.Count -or $CIRegisterWithGh -or $CIRegistrationTokenFile -or $CIPromptToken) -and -not $Resume) {
        throw 'CI options require CIRepository or CIOrganization'
    }
    $agentTools = @()
    foreach ($selection in $AgentTool) {
        foreach ($tool in $selection.Split(',') | ForEach-Object { $_.Trim().ToLowerInvariant() }) {
            if ($tool -notin @('gh', 'codex', 'claude', 'opencode')) {
                throw "Unsupported agent tool: $tool"
            }
            $agentTools += $tool
        }
    }
    if ($Profile -eq 'agent_windows') { $agentTools = @(@('gh', 'codex') + $agentTools | Select-Object -Unique) }
    if ($Plan) {
        if ($Profile -eq 'agent_windows') {
            $planSource = $PSScriptRoot
            if ($Resume) {
                $preview = Read-State
                $planSource = $preview.Source
                $agentTools = @($preview.AgentTool)
                $Node = $preview.Node; $Python = $preview.Python; $Go = $preview.Go
                $T3CodeDesktop = $preview.T3CodeDesktop
                foreach ($option in @('NodeVersions', 'GameDev', 'ShaderTools')) {
                    if (-not $PSBoundParameters.ContainsKey($option)) { Set-Variable -Name $option -Value $preview.$option }
                }
                if (-not $PSBoundParameters.ContainsKey('StorageRoot')) { $StorageRoot = $preview.StorageRoot }
            }
            $nativeHelpers = Join-Path $planSource 'windows\AgentWorkstation.Common.ps1'
            if (Test-Path -LiteralPath $nativeHelpers) {
                . $nativeHelpers
                $nativeOptions = Get-NativeOptionalArguments ([pscustomobject]@{
                    NodeVersions = [bool]$NodeVersions; GameDev = [bool]$GameDev;
                    ShaderTools = [bool]$ShaderTools; StorageRoot = $StorageRoot })
                Invoke-NativeAgentSetup -Source $planSource -AgentTool $agentTools -Node:$Node `
                    -Python:$Python -Go:$Go -T3CodeDesktop:$T3CodeDesktop -Plan @nativeOptions
            } else {
                Write-Host "Plan: native Windows Git, agents $($agentTools -join ', '), shared skills; Node: $Node; Python: $Python; Go: $Go; Node versions: $NodeVersions; Game dev: $GameDev; Shader tools: $ShaderTools; T3 desktop: $T3CodeDesktop."
                if ($StorageRoot) { Write-Host "Development storage: $StorageRoot (mounted NTFS directory; existing data is not moved)" }
            }
            return
        }
        Write-Host "Plan: install WinGet/WSL and stable Ubuntu, configure $Profile, selected runtimes/agents, and optional T3 desktop/worker."
        Write-Host "A reboot and interactive Ubuntu/provider login may be required."
        if ($runnerConfig) { Write-Host 'GitHub Actions: native Windows login tasks with WSL access; registration may be completed later.' }
        return
    }
    if (Test-Path -LiteralPath $statePath) {
        if (-not $Resume) { throw "An installation already exists. Rerun the saved installer with -Resume." }
        $setupLock = [IO.File]::Open((Join-Path $stateDirectory 'setup.lock'),
            [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $state = Read-State
    } elseif ($Resume) {
        throw "No saved $Profile setup is available to resume"
    } else {
        if ($Profile -eq 'server_wsl') {
            if (-not $LinuxUser) { $LinuxUser = $env:USERNAME.ToLowerInvariant() }
            if ($LinuxUser -notmatch '^[a-z_][a-z0-9_-]{0,31}$' -or $LinuxUser -eq "root") {
                $LinuxUser = Read-Host "Choose a lowercase Ubuntu username"
            }
            if ($LinuxUser -notmatch '^[a-z_][a-z0-9_-]{0,31}$' -or $LinuxUser -eq "root") {
                throw "Invalid Ubuntu username"
            }
        }
        $resolved = Resolve-Source $(if ($Version) { $Version } else { $Channel })
        if ($Profile -eq 'agent_windows') {
            Assert-NativeSourceOptions $resolved.Source ([pscustomobject]@{
                NodeVersions = [bool]$NodeVersions; GameDev = [bool]$GameDev;
                ShaderTools = [bool]$ShaderTools; StorageRoot = $StorageRoot })
        }
        $state = [pscustomobject]@{
            Version = 1
            Profile = $Profile
            OwnerSid = $owner.User.Value
            Revision = $resolved.Revision
            Source = $resolved.Source
            Distro = $DistroName
            DistroSelection = $(if ($DistroName) { 'explicit' } else { 'automatic' })
            DistroOwned = $false
            RestartRequired = $false
            DistroLocation = $DistroLocation
            LinuxUser = $LinuxUser
            BuildServer = [bool]$BuildServer
            T3CodeDesktop = [bool]$T3CodeDesktop
            Node = [bool]$Node
            Python = [bool]$Python
            Go = [bool]$Go
            NodeVersions = [bool]$NodeVersions
            GameDev = [bool]$GameDev
            ShaderTools = [bool]$ShaderTools
            StorageRoot = $StorageRoot
            AgentTool = $agentTools
            GitHubRunner = $runnerConfig
            Phase = "staged"
        }
        Save-State $state
        $stagedInstaller = Join-Path $state.Source "install.ps1"
        $resumeOptions = @{ Profile = $Profile; Resume = $true }
        if ($Profile -eq 'server_wsl') {
            $resumeOptions.CIRegisterWithGh = $CIRegisterWithGh
            $resumeOptions.CIRegistrationTokenFile = $CIRegistrationTokenFile
            $resumeOptions.CIPromptToken = $CIPromptToken
        }
        & $stagedInstaller @resumeOptions
        if (-not $?) { throw "Staged setup failed; rerun the saved installer with -Profile $Profile -Resume" }
        return
    }

    if ($Profile -eq 'server_wsl') { Initialize-DistroSelection }
    if (-not $state.PSObject.Properties['GitHubRunner']) {
        $state | Add-Member -NotePropertyName GitHubRunner -NotePropertyValue $null
    }
    if ($runnerConfig) { $state.GitHubRunner = $runnerConfig; Save-State $state }
    if ($Profile -eq 'agent_windows') {
        foreach ($option in @('NodeVersions', 'GameDev', 'ShaderTools')) {
            if ($PSBoundParameters.ContainsKey($option)) { $state.$option = [bool](Get-Variable -Name $option -ValueOnly) }
        }
        if ($PSBoundParameters.ContainsKey('StorageRoot')) { $state.StorageRoot = $StorageRoot }
    }
    if ($UpdateSource) {
        $resolved = Resolve-Source $(if ($Version) { $Version } else { $Channel })
        if ($Profile -eq 'agent_windows') { Assert-NativeSourceOptions $resolved.Source $state }
        $state.Revision = $resolved.Revision
        $state.Source = $resolved.Source
        Save-State $state
        $setupLock.Dispose()
        $setupLock = $null
        $resumeOptions = @{ Profile = $Profile; Resume = $true }
        if ($Profile -eq 'server_wsl') {
            $resumeOptions.CIRegisterWithGh = $CIRegisterWithGh
            $resumeOptions.CIRegistrationTokenFile = $CIRegistrationTokenFile
            $resumeOptions.CIPromptToken = $CIPromptToken
        }
        & (Join-Path $state.Source 'install.ps1') @resumeOptions
        if (-not $?) { throw 'Updated source setup failed; rerun the saved installer with -Resume' }
        return
    }
    if ($Profile -eq 'agent_windows') {
        . (Join-Path $state.Source 'windows\AgentWorkstation.Common.ps1')
        $nativeOptions = Get-NativeOptionalArguments $state
        $state.Phase = 'applying'
        Save-State $state
        Invoke-NativeAgentSetup -Source $state.Source -AgentTool @($state.AgentTool) -Node:$state.Node `
            -Python:$state.Python -Go:$state.Go -T3CodeDesktop:$state.T3CodeDesktop @nativeOptions
        $state.Phase = 'complete'
        Save-State $state
        Write-Host 'Basaltwater native Windows coding setup complete. Restart T3 Code to load the updated PATH and skills.'
        return
    }
    if ($state.GitHubRunner) {
        $ci = $state.GitHubRunner
        Assert-RunnerArguments @($ci.Repository) $ci.Organization @($ci.AllowedUser) @($ci.Label) $ci.RunnerGroup `
            $CIRegisterWithGh $CIRegistrationTokenFile $CIPromptToken
    }
    if (-not (Ensure-Wsl)) {
        $state.Phase = "restart-required"
        Save-State $state
        Write-Host "Restart Windows, log back into this account, then run:"
        Write-Host ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' +
            (Join-Path $state.Source "install.ps1") + '" -Resume')
        return
    }
    Ensure-Winget
    Invoke-Checked "WSL update" { & wsl.exe --update }
    Select-Distro
    Ensure-Distro
    $osRelease = & wsl.exe --distribution $state.Distro --user root --exec /usr/bin/cat /etc/os-release
    if ($LASTEXITCODE -ne 0 -or -not @($osRelease | Where-Object { $_ -match '^ID="?ubuntu"?\s*$' })) {
        throw "Selected WSL distribution is not Ubuntu"
    }
    Invoke-Checked "WSL 2 selection" { & wsl.exe --set-version $state.Distro 2 }
    Ensure-UbuntuPython
    $prepare = Wsl-Path (Join-Path $state.Source "windows\wsl_prepare.py")
    $sourcePath = Wsl-Path $state.Source
    Ensure-UbuntuConfiguration $prepare
    Invoke-Checked "source staging" {
        & wsl.exe --distribution $state.Distro --user root --exec /usr/bin/python3 $prepare stage --source $sourcePath --revision $state.Revision
    }
    $linuxSource = "/opt/basaltwater/releases/" + $state.Revision
    $setupArgs = @("--system-type", "server_wsl", "--username", $state.LinuxUser, "--machine", "wsl")
    if ($state.Node) { $setupArgs += "--node" }
    if ($state.Python) { $setupArgs += "--python" }
    if ($state.Go) { $setupArgs += "--go" }
    foreach ($tool in $state.AgentTool) { $setupArgs += @("--agent-tool", $tool) }
    Invoke-Checked "Ubuntu WSL setup" {
        & wsl.exe --distribution $state.Distro --user root --exec /usr/bin/python3 ($linuxSource + "/remote_setup.py") @setupArgs
    }
    if ($state.BuildServer) {
        Ensure-WingetPackage 'Git.Git' 'Git for Windows installation'
    }
    if ($state.T3CodeDesktop) {
        Ensure-WingetPackage 'T3Tools.T3Code' 'T3 Code desktop installation'
    }
    Ensure-WorkerTask
    $state.Phase = "complete"
    Save-State $state
    if ($state.GitHubRunner) {
        try {
            Ensure-WingetPackage 'Git.Git' 'Git for Windows installation'
            Ensure-WingetPackage 'Microsoft.PowerShell' 'PowerShell installation'
            if ($CIRegisterWithGh) { Ensure-WingetPackage 'GitHub.cli' 'GitHub CLI installation' }
            $runnerOptions = @{
                Repository = @($state.GitHubRunner.Repository); Organization = $state.GitHubRunner.Organization
                AllowedUser = @($state.GitHubRunner.AllowedUser); Label = @($state.GitHubRunner.Label)
                RunnerGroup = $state.GitHubRunner.RunnerGroup; RegisterWithGh = $CIRegisterWithGh
                RegistrationTokenFile = $CIRegistrationTokenFile; PromptToken = $CIPromptToken
            }
            & (Join-Path $state.Source 'windows\Setup-GitHubRunner.ps1') @runnerOptions
        } catch {
            Write-Warning ('GitHub runner setup remains incomplete. Other setup completed; retry windows\Setup-GitHubRunner.ps1. ' + $_.Exception.Message)
        }
    }
    Write-Host "Basaltwater Ubuntu WSL setup complete. Authenticate selected agents in Ubuntu as $($state.LinuxUser)."
    if ($state.T3CodeDesktop) { Write-Host "In T3 Code Settings > Connections, select $($state.Distro)." }
} catch {
    Write-Error $_
    exit 1
} finally {
    if ($setupLock) { $setupLock.Dispose() }
}
