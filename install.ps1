<# Fresh Windows 11 bootstrap. Run as the intended WSL/desktop account. #>
[CmdletBinding()]
param(
    [ValidateSet("server_wsl")][string]$Profile = "server_wsl",
    [switch]$BuildServer,
    [switch]$T3CodeDesktop,
    [switch]$Node,
    [switch]$Python,
    [switch]$Go,
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
$statePath = Join-Path $stateDirectory "wsl-setup.json"
$owner = [Security.Principal.WindowsIdentity]::GetCurrent()
$setupLock = $null
$runnerHelpers = Join-Path $PSScriptRoot 'windows\GitHubRunner.Common.ps1'
if (Test-Path -LiteralPath $runnerHelpers) { . $runnerHelpers }

function Save-State($value) {
    if (-not (Test-Path -LiteralPath $stateDirectory)) {
        New-Item -ItemType Directory -Path $stateDirectory | Out-Null
    }
    $temporary = Join-Path $stateDirectory ("state-" + [guid]::NewGuid() + ".json")
    $value | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $statePath -Force
}

function Invoke-Checked([string]$label, [scriptblock]$command) {
    & $command
    if ($LASTEXITCODE -ne 0) {
        throw "$label failed (exit code $LASTEXITCODE)"
    }
}

function Check-Host {
    $system = Get-CimInstance Win32_OperatingSystem
    if ($system.ProductType -ne 1 -or [int]$system.BuildNumber -lt 22000 -or
        $env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
        throw "A fully updated x86-64 Windows 11 desktop installation is required"
    }
    $drive = Get-PSDrive -Name ([IO.Path]::GetPathRoot($stateDirectory).Substring(0, 1))
    if ($drive.Free -lt 15GB) {
        throw "At least 15 GiB of free space is required for WSL and build tools"
    }
}

function Resolve-Source([string]$reference) {
    if ($reference -notmatch '^[A-Za-z0-9._-]+$') {
        throw "Invalid release reference"
    }
    $commit = Invoke-RestMethod -UseBasicParsing -Uri ("https://api.github.com/repos/bluehexagons/basaltwater/commits/" + $reference)
    $sha = [string]$commit.sha
    if ($sha -notmatch '^[0-9a-f]{40}$') {
        throw "Could not resolve a full source commit SHA"
    }
    New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
    $root = Join-Path $stateDirectory ("source\" + $sha)
    if (-not (Test-Path -LiteralPath (Join-Path $root "windows\wsl_prepare.py")) -or
        -not (Test-Path -LiteralPath (Join-Path $root "install.ps1"))) {
        if (Test-Path -LiteralPath $root) { throw "Pinned source directory is incomplete: $root" }
        $archive = Join-Path $stateDirectory ("source-" + $sha + ".zip")
        $unpack = Join-Path $stateDirectory ("unpack-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $unpack | Out-Null
        try {
            Invoke-WebRequest -UseBasicParsing -Uri ("https://github.com/bluehexagons/basaltwater/archive/" + $sha + ".zip") -OutFile $archive
            Expand-Archive -LiteralPath $archive -DestinationPath $unpack
            $children = @(Get-ChildItem -LiteralPath $unpack -Directory)
            if ($children.Count -ne 1 -or -not (Test-Path -LiteralPath (Join-Path $children[0].FullName "windows\wsl_prepare.py"))) {
                throw "Downloaded source archive is incomplete"
            }
            New-Item -ItemType Directory -Path (Split-Path -Parent $root) -Force | Out-Null
            Move-Item -LiteralPath $children[0].FullName -Destination $root
        } finally {
            Remove-Item -LiteralPath $unpack -Recurse -Force -ErrorAction SilentlyContinue
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

function Ensure-Winget {
    if (Get-Command winget.exe -ErrorAction SilentlyContinue) { return }
    try {
        Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe
    } catch {
        Write-Host "App Installer registration did not make WinGet available; trying repair."
    }
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        Install-PackageProvider -Name NuGet -Force | Out-Null
        Install-Module -Name Microsoft.WinGet.Client -Scope CurrentUser -Force -Repository PSGallery
        Import-Module Microsoft.WinGet.Client
        Repair-WinGetPackageManager -Force -Latest
    }
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw "WinGet is unavailable after App Installer registration/repair"
    }
}

function Ensure-WingetPackage([string]$packageId, [string]$label) {
    & winget.exe install --id $packageId --exact --source winget --accept-source-agreements --accept-package-agreements
    $exitCode = $LASTEXITCODE
    if ($exitCode -eq 0) { return }
    # WinGet reports "no applicable update" as a failure when a package is already current.
    if ($exitCode -eq -1978335189) {
        & winget.exe list --id $packageId --exact --source winget | Out-Null
        if ($LASTEXITCODE -eq 0) { return }
    }
    throw "$label failed (exit code $exitCode)"
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
    if ($LinuxUser -and ($LinuxUser -notmatch '^[a-z_][a-z0-9_-]{0,31}$' -or $LinuxUser -eq "root")) {
        throw "Invalid Ubuntu username"
    }
    if ($DistroName -and $DistroName -notmatch '^Ubuntu(?:-[0-9]{2}\.[0-9]{2})?$') {
        throw "Distribution name must be an official stable Ubuntu WSL name"
    }
    if ($DistroLocation -and $DistroLocation -notmatch '^[A-Za-z]:[\\/]') {
        throw "Distribution location must be an absolute Windows drive path"
    }
    if ($Version -and $Version -notmatch '^[A-Za-z0-9._-]+$') { throw "Invalid release reference" }
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
                throw "Unsupported Ubuntu agent tool: $tool"
            }
            $agentTools += $tool
        }
    }
    if ($Plan) {
        Write-Host "Plan: install WinGet/WSL and stable Ubuntu, configure $Profile, selected runtimes/agents, and optional T3 desktop/worker."
        Write-Host "A reboot and interactive Ubuntu/provider login may be required."
        if ($runnerConfig) { Write-Host 'GitHub Actions: native Windows login tasks with WSL access; registration may be completed later.' }
        return
    }
    if (Test-Path -LiteralPath $statePath) {
        if (-not $Resume) { throw "An installation already exists. Rerun the saved installer with -Resume." }
        $setupLock = [IO.File]::Open((Join-Path $stateDirectory 'setup.lock'),
            [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        if ($state.OwnerSid -ne $owner.User.Value -or $state.Revision -notmatch '^[0-9a-f]{40}$' -or
            -not (Test-Path -LiteralPath (Join-Path $state.Source "windows\wsl_prepare.py"))) {
            throw "Saved setup state does not match this account or source"
        }
    } elseif ($Resume) {
        throw "No saved WSL setup is available to resume"
    } else {
        if (-not $LinuxUser) { $LinuxUser = $env:USERNAME.ToLowerInvariant() }
        if ($LinuxUser -notmatch '^[a-z_][a-z0-9_-]{0,31}$' -or $LinuxUser -eq "root") {
            $LinuxUser = Read-Host "Choose a lowercase Ubuntu username"
        }
        if ($LinuxUser -notmatch '^[a-z_][a-z0-9_-]{0,31}$' -or $LinuxUser -eq "root") {
            throw "Invalid Ubuntu username"
        }
        $resolved = Resolve-Source $(if ($Version) { $Version } else { $Channel })
        $state = [pscustomobject]@{
            Version = 1
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
            AgentTool = $agentTools
            GitHubRunner = $runnerConfig
            Phase = "staged"
        }
        Save-State $state
        $stagedInstaller = Join-Path $state.Source "install.ps1"
        & $stagedInstaller -Resume -CIRegisterWithGh:$CIRegisterWithGh `
            -CIRegistrationTokenFile $CIRegistrationTokenFile -CIPromptToken:$CIPromptToken
        if (-not $?) { throw "Staged WSL setup failed; rerun the saved installer with -Resume" }
        return
    }

    Initialize-DistroSelection
    if (-not $state.PSObject.Properties['GitHubRunner']) {
        $state | Add-Member -NotePropertyName GitHubRunner -NotePropertyValue $null
    }
    if ($runnerConfig) { $state.GitHubRunner = $runnerConfig; Save-State $state }
    if ($UpdateSource) {
        $resolved = Resolve-Source $(if ($Version) { $Version } else { $Channel })
        $state.Revision = $resolved.Revision
        $state.Source = $resolved.Source
        Save-State $state
        $setupLock.Dispose()
        $setupLock = $null
        & (Join-Path $state.Source 'install.ps1') -Resume -CIRegisterWithGh:$CIRegisterWithGh `
            -CIRegistrationTokenFile $CIRegistrationTokenFile -CIPromptToken:$CIPromptToken
        if (-not $?) { throw 'Updated source setup failed; rerun the saved installer with -Resume' }
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
