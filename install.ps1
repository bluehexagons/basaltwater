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
    [switch]$Plan,
    [switch]$Resume
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$stateDirectory = Join-Path $env:LOCALAPPDATA "Basaltwater"
$statePath = Join-Path $stateDirectory "wsl-setup.json"
$owner = [Security.Principal.WindowsIdentity]::GetCurrent()
$setupLock = $null

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

function Select-Distro {
    if ($state.Distro) { return }
    $available = & wsl.exe --list --online
    if ($LASTEXITCODE -ne 0) { throw "Could not list official WSL distributions" }
    $names = @([regex]::Matches(($available -join " "), 'Ubuntu-(\d{2})\.(\d{2})') |
        ForEach-Object { $_.Value } | Sort-Object -Unique -Descending)
    if (-not $names) { throw "No versioned stable Ubuntu distribution is available from WSL" }
    $state.Distro = $names[0]
    Save-State $state
}

function Ensure-Distro {
    $installed = (& wsl.exe --list --quiet | Out-String) -replace [char]0, ''
    if ($LASTEXITCODE -ne 0) { throw "Could not list installed WSL distributions" }
    if ($installed -match ("(?m)^" + [regex]::Escape($state.Distro) + "\s*$")) { return }
    $online = (& wsl.exe --list --online | Out-String) -replace [char]0, ''
    if ($LASTEXITCODE -ne 0 -or $online -notmatch ("\b" + [regex]::Escape($state.Distro) + "\b")) {
        throw "Selected Ubuntu distribution is neither installed nor available online"
    }
    $arguments = @('--install', '--distribution', $state.Distro, '--no-launch')
    if ($state.DistroLocation) { $arguments += @('--location', $state.DistroLocation) }
    Invoke-Checked "Ubuntu installation" { & wsl.exe @arguments }
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
            DistroLocation = $DistroLocation
            LinuxUser = $LinuxUser
            BuildServer = [bool]$BuildServer
            T3CodeDesktop = [bool]$T3CodeDesktop
            Node = [bool]$Node
            Python = [bool]$Python
            Go = [bool]$Go
            AgentTool = $agentTools
            Phase = "staged"
        }
        Save-State $state
        $stagedInstaller = Join-Path $state.Source "install.ps1"
        & $stagedInstaller -Resume
        if (-not $?) { throw "Staged WSL setup failed; rerun the saved installer with -Resume" }
        return
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
    $osRelease = & wsl.exe --distribution $state.Distro --exec cat /etc/os-release
    if ($LASTEXITCODE -ne 0 -or -not @($osRelease | Where-Object { $_ -match '^ID="?ubuntu"?\s*$' })) {
        throw "Selected WSL distribution is not Ubuntu"
    }
    Invoke-Checked "WSL 2 selection" { & wsl.exe --set-version $state.Distro 2 }
    $prepare = Wsl-Path (Join-Path $state.Source "windows\wsl_prepare.py")
    $sourcePath = Wsl-Path $state.Source
    $changed = & wsl.exe --distribution $state.Distro --user root --exec python3 $prepare configure --user $state.LinuxUser
    if ($LASTEXITCODE -ne 0) { throw "Ubuntu user/systemd preparation failed" }
    if (($changed | Select-Object -Last 1) -eq "CHANGED") {
        Invoke-Checked "WSL distribution restart" { & wsl.exe --terminate $state.Distro }
    }
    Invoke-Checked "source staging" {
        & wsl.exe --distribution $state.Distro --user root --exec python3 $prepare stage --source $sourcePath --revision $state.Revision
    }
    $linuxSource = "/opt/basaltwater/releases/" + $state.Revision
    $setupArgs = @("--system-type", "server_wsl", "--username", $state.LinuxUser, "--machine", "wsl")
    if ($state.Node) { $setupArgs += "--node" }
    if ($state.Python) { $setupArgs += "--python" }
    if ($state.Go) { $setupArgs += "--go" }
    foreach ($tool in $state.AgentTool) { $setupArgs += @("--agent-tool", $tool) }
    Invoke-Checked "Ubuntu WSL setup" {
        & wsl.exe --distribution $state.Distro --user root --exec python3 ($linuxSource + "/remote_setup.py") @setupArgs
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
    Write-Host "Basaltwater Ubuntu WSL setup complete. Authenticate selected agents in Ubuntu as $($state.LinuxUser)."
    if ($state.T3CodeDesktop) { Write-Host "In T3 Code Settings > Connections, select $($state.Distro)." }
} catch {
    Write-Error $_
    exit 1
} finally {
    if ($setupLock) { $setupLock.Dispose() }
}
