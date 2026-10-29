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
        throw 'No saved native Windows setup. Run install.ps1 -Profile agent_windows first.'
    }
    $saved = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if ($saved.Version -ne 1 -or $saved.OwnerSid -ne $sid -or $saved.Profile -ne 'agent_windows' -or
        $saved.Revision -notmatch '\A[0-9a-f]{40}\z' -or $saved.Source -notmatch '\A[A-Za-z]:[\\/]' -or
        -not (Test-Path -LiteralPath (Join-Path $saved.Source 'install.ps1') -PathType Leaf)) {
        throw 'Saved Windows setup does not match this account, profile or source; repair the saved installation before refreshing.'
    }
    return $saved
}

function Get-NativeCliSource {
    $receipt = Join-Path $PSScriptRoot 'cli-source.json'
    if (Test-Path -LiteralPath $receipt) { return (Get-Content -LiteralPath $receipt -Raw -Encoding UTF8 | ConvertFrom-Json).Source }
    return Split-Path -Parent $PSScriptRoot
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
  desktop           Native desktop status, observation, control and handoff
  desktop develop   Supervise project tasks, logs and cancellation

Refresh options:
  --wait            Run refresh in this terminal instead of a detached job
  --status ID       Read a detached refresh receipt
  --logs ID         Read the last 100 lines of its log
  --cancel ID       Cancel a queued refresh before setup starts
  --dry-run         Preview saved/current selections without writes or downloads
  --no-update-source   Reapply the saved Basaltwater revision; still update tools
  --no-restart-t3      Skip the T3 Code start/restart after setup
  --no-reboot          Report required Windows reboots without rebooting
  --game-dev        Add native game build tools, Python, Go and fnm
  --shader-tools    Add Vulkan SDK shader validation
  --godot           Add the standard Godot editor/CLI
  --sunshine        Add the native Sunshine streaming service (UAC may be required)
  --node-versions   Add fnm for project-selected Node versions
  --storage-root PATH  Select a mounted NTFS directory for development caches
  --storage-drive DRIVE  Use DRIVE:\Basaltwater (for example: --storage-drive F:)
  --channel main|dev   Select the source channel (both currently track main)
  --version REF        Select a specific source reference

Refresh installs available updates for selected host tools and preserves provider
logins. Successful setup queues a graceful T3 Code start/restart.
Required Windows reboots are automatic, with a 30-second grace period and no
forced app closure. Interrupted setup continues once after boot and login.
Other open terminals need to be reopened after setup changes PATH.
Desktop handoff: open PATH [--reveal], terminal --project PATH [--msvc],
notify --title TEXT --message TEXT. Other desktop commands list their own help.
Linux controller, server and agent commands require a Linux installation.
'@
}

function Invoke-NativeCli([string[]]$Arguments) {
    if (-not $Arguments -or ($Arguments.Count -eq 1 -and $Arguments[0] -in @('--help', '-h', 'help'))) {
        Show-NativeCliHelp
        return 0
    }
    $command = $Arguments[0]
    $options = @($Arguments | Select-Object -Skip 1)
    if ($command -eq 'desktop') {
        if (-not $options.Count -or $options[0] -in @('--help','help','-h')) {
            Write-Host @'
Native desktop commands (explicit actions use the current account's session):
  status|doctor|windows [--json]
  inspect --pid PID [--name NAME] [--role ROLE] [--json]
  screenshot --window HWND --identity TOKEN --generation GEN --output NEW.png
  window focus|move|resize|maximize|minimize|restore|close --window HWND --identity TOKEN --generation GEN
  element invoke|set-text|focus --ref REF --generation GEN [--text TEXT]
  input key|text|click --window HWND --identity TOKEN --generation GEN --width W --height H
  control enabled|human-override|disabled --human-approved [--seconds N|--indefinite]
  open PATH [--reveal] | terminal --project PATH [--msvc]
  notify --title TEXT --message TEXT
  develop doctor|exec|node|editor|run|import|status|logs|cancel ...
Observe windows/inspect before actions. Pixel capture does not prove rendered content or input outcome.
'@
            return 0
        }
        $source = Get-NativeCliSource
        . (Join-Path $source 'windows\AgentWorkstation.Common.ps1')
        . (Join-Path $source 'windows\Desktop.Common.ps1')
        if ($options.Count -and $options[0] -eq 'develop') {
            . (Join-Path $source 'windows\Development.Common.ps1')
            $result = Invoke-NativeDevelopmentCli @($options | Select-Object -Skip 1)
            if ($null -ne $result -and $options.Count -ge 2 -and $options[1] -eq 'status' -and
                $result.State -in @('failed','timed-out','cancelled','cleanup-uncertain','launch-uncertain','interrupted')) {
                Write-Host ($result | ConvertTo-Json -Depth 12)
                return 1
            }
        } elseif ($options.Count -and $options[0] -in @('open','terminal','notify')) {
            $result = Invoke-NativeDesktopHandoff -Source $source -Arguments $options
        } else { $result = Invoke-NativeDesktopCli $options }
        if ($null -ne $result) { Write-Host ($result | ConvertTo-Json -Depth 12) }
        if ($result -and (($result -is [Collections.IDictionary] -and $result.Contains('ok') -and -not $result.ok) -or
            ($result.PSObject.Properties['ok'] -and -not $result.ok))) { return 1 }
        if ($options[0] -eq 'doctor' -and -not $result.healthy) { return 1 }
        return 0
    }
    if ($command -in @('--version', 'version')) {
        if ($options.Count) { throw 'Usage: basaltw --version' }
        $receipt = Join-Path $PSScriptRoot 'cli-source.json'
        if (Test-Path -LiteralPath $receipt) {
            $version = (Get-Content -LiteralPath $receipt -Raw -Encoding UTF8 | ConvertFrom-Json).PackageVersion
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
        foreach ($name in @('AgentTool', 'T3CodeDesktop', 'Node', 'Python', 'Go', 'NodeVersions', 'GameDev', 'ShaderTools', 'Godot', 'Sunshine', 'StorageRoot')) {
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
        if (Test-Path -LiteralPath $receipt) { $source = (Get-Content -LiteralPath $receipt -Raw -Encoding UTF8 | ConvertFrom-Json).Source }
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
    if ($options.Count -eq 2 -and $options[0] -in @('--status','--logs','--cancel')) {
        $source = Get-NativeCliSource
        . (Join-Path $source 'windows\AgentWorkstation.Common.ps1')
        . (Join-Path $source 'windows\Refresh.Common.ps1')
        if ($options[0] -eq '--cancel') {
            Write-Host ((Stop-NativeQueuedRefresh $options[1]) | ConvertTo-Json)
            return 0
        }
        $receipt = Get-NativeRefreshStatus $options[1]
        if ($options[0] -eq '--logs') {
            $log = Join-Path (Get-NativeRefreshDirectory $receipt.Id) 'setup.log'
            Assert-NativeSkillPath $log
            if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 100 -Encoding UTF8 | ForEach-Object { Write-Host $_ } }
        } else {
            Write-Host ($receipt | Select-Object Id,Status,StartedUtc,CompletedUtc,Log | ConvertTo-Json)
        }
        if ($options[0] -eq '--status' -and $receipt.Status -in @('failed','dispatch-failed','interrupted','cleanup-uncertain')) { return 1 }
        return 0
    }
    $wait = $options -contains '--wait'
    $options = @($options | Where-Object { $_ -ne '--wait' })
    # Explicit switches also let an installed CLI update an older saved entry
    # point that predates automatic native reruns and source updates.
    $forward = @('-Profile', 'agent_windows', '-Resume', '-UpdateSource')
    $switches = @{ '--dry-run' = '-Plan'; '--game-dev' = '-GameDev'; '--shader-tools' = '-ShaderTools';
                   '--godot' = '-Godot'; '--sunshine' = '-Sunshine'; '--node-versions' = '-NodeVersions';
                   '--no-restart-t3' = '-NoRestartT3'; '--no-reboot' = '-NoReboot' }
    $values = @{ '--storage-root' = '-StorageRoot'; '--storage-drive' = '-StorageRoot'; '--channel' = '-Channel'; '--version' = '-Version' }
    $storageSelected = $false
    for ($index = 0; $index -lt $options.Count; $index++) {
        $option = $options[$index]
        if ($option -eq '--no-update-source') {
            $forward = @($forward | Where-Object { $_ -ne '-UpdateSource' })
            $forward += '-NoUpdateSource'
            continue
        }
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
    if (-not $wait -and '-Plan' -notin $forward) {
        $source = Get-NativeCliSource
        . (Join-Path $source 'windows\AgentWorkstation.Common.ps1')
        . (Join-Path $source 'windows\Refresh.Common.ps1')
        $job = Start-NativeRefresh -Saved $saved -Source $source -Arguments $forward
        Write-Host ($job | ConvertTo-Json)
        return 0
    }
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
