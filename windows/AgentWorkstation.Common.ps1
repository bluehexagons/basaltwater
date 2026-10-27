<# Native Windows agent tools and catalogs; no Linux runtime imports. #>
function Update-NativePath {
    # Retain transient paths supplied by the invoking agent or terminal.
    $paths = @($env:PATH, [Environment]::GetEnvironmentVariable('Path', 'Machine'),
              [Environment]::GetEnvironmentVariable('Path', 'User')) -join ';'
    $env:PATH = [Environment]::ExpandEnvironmentVariables($paths)
}

function Add-NativeUserPath([string]$directory) {
    $saved = [Environment]::GetEnvironmentVariable('Path', 'User')
    $entries = @($saved -split ';' | Where-Object { $_ })
    if ($directory -notin @($entries | ForEach-Object { [Environment]::ExpandEnvironmentVariables($_).TrimEnd('\') })) {
        [Environment]::SetEnvironmentVariable('Path', (@($directory) + $entries -join ';'), 'User')
    }
    $env:PATH = $directory + ';' + $env:PATH
}

function Get-NativeCommand([string]$name) {
    foreach ($extension in @('.exe', '.cmd')) {
        $command = Get-Command ($name + $extension) -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) { return $command.Source }
    }
    return $null
}

function Get-NativeT3Desktop {
    $locations = @(Join-Path $env:LOCALAPPDATA 'Programs\t3code')
    foreach ($key in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
                       'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*')) {
        $apps = @(Get-ItemProperty -Path $key -ErrorAction SilentlyContinue |
            Where-Object { $_.PSObject.Properties['DisplayName'] -and $_.DisplayName -like 'T3 Code*' })
        foreach ($app in $apps) {
            if ($app.PSObject.Properties['InstallLocation'] -and $app.InstallLocation) { $locations += $app.InstallLocation }
        }
    }
    foreach ($location in $locations) {
        $app = Get-ChildItem -LiteralPath $location -Filter 'T3 Code*.exe' -File -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($app) { return $app.FullName }
    }
    return $null
}

function Ensure-NativeTool([string]$name, [string]$packageId, [switch]$Plan) {
    $command = Get-NativeCommand $name
    if ($command) {
        Write-Host "Reuse $name`: $command"
    } elseif ($Plan) {
        Write-Host "Install $name via WinGet: $packageId"
    } else {
        Ensure-WingetPackage $packageId "$name installation" -NoUpgrade
        Update-NativePath
        $command = Get-NativeCommand $name
        if (-not $command) { throw "$name is still unavailable. Open a new PowerShell window and rerun setup." }
    }
    if ($command -and -not $Plan) {
        $versionArgument = if ($name -eq 'go') { 'version' } else { '--version' }
        & $command $versionArgument
        if ($LASTEXITCODE -ne 0) { throw "$name version check failed (exit code $LASTEXITCODE)" }
    }
}

function Ensure-NativeGitSettings([switch]$Plan) {
    $git = Get-NativeCommand git
    if (-not $git) {
        if ($Plan) { Write-Host 'Enable Git core.longpaths after Git installation'; return }
        throw 'Git is unavailable for long-path configuration'
    }
    $configured = & $git config --global --get core.longpaths
    $queryExit = $LASTEXITCODE
    if ($queryExit -eq 0) {
        Write-Host "Preserve Git core.longpaths: $configured"
        return
    }
    if ($queryExit -ne 1) { throw "Could not read Git long-path configuration (exit code $queryExit)" }
    if ($Plan) { Write-Host 'Enable Git core.longpaths for the current Windows account'; return }
    & $git config --global core.longpaths true
    if ($LASTEXITCODE -ne 0) { throw "Could not enable Git long-path support (exit code $LASTEXITCODE)" }
    Write-Host 'Enabled Git core.longpaths for the current Windows account'
}

function Assert-NativeSkillPath([string]$path) {
    $current = [IO.Path]::GetFullPath($path)
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing linked skill path: $current" }
        }
        $current = Split-Path -Parent $current
    }
}

function Install-NativeSkill([string]$source, [string]$destination, [switch]$Plan) {
    Assert-NativeSkillPath $source
    Assert-NativeSkillPath $destination
    $entrypoint = Join-Path $source 'SKILL.md'
    if (-not (Test-Path -LiteralPath $entrypoint -PathType Leaf) -or
        (Get-Content -LiteralPath $entrypoint -Raw) -notmatch 'managed-by: basaltwater') {
        throw "Invalid managed skill source: $source"
    }
    $files = @{}
    foreach ($item in Get-ChildItem -LiteralPath $source -Recurse -Force) {
        $relative = $item.FullName.Substring($source.TrimEnd('\').Length + 1)
        if ($relative -match '(?:\A|\\)__pycache__(?:\\|\z)' -or
            (-not $item.PSIsContainer -and $item.Extension -in @('.pyc', '.pyo'))) { continue }
        Assert-NativeSkillPath $item.FullName
        if (-not $item.PSIsContainer) {
            $files[$relative] = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash
        }
    }
    $inventoryPath = Join-Path $destination '.basaltwater-windows-files.json'
    Assert-NativeSkillPath $inventoryPath
    $recorded = @{}
    if (Test-Path -LiteralPath $inventoryPath) {
        $inventory = Get-Content -LiteralPath $inventoryPath -Raw | ConvertFrom-Json
        if ($inventory.Version -ne 1) { throw "Unsupported skill inventory: $inventoryPath" }
        foreach ($property in $inventory.Files.PSObject.Properties) {
            if ($property.Name -notmatch '\A[A-Za-z0-9_.\\-]+\z' -or
                @($property.Name -split '\\' | Where-Object { $_ -in @('', '.', '..') }).Count -or
                @($property.Value).Count -eq 0 -or
                @($property.Value | Where-Object { $_ -notmatch '\A[0-9A-Fa-f]{64}\z' }).Count) {
                throw "Invalid skill inventory: $inventoryPath"
            }
            $recorded[$property.Name] = @($property.Value)
        }
    }
    # Preflight every source and previously managed destination before any write.
    $pending = @{}
    foreach ($relative in @(@($files.Keys) + @($recorded.Keys) | Select-Object -Unique)) {
        $path = Join-Path $destination $relative
        Assert-NativeSkillPath $path
        $digest = $null
        if (Test-Path -LiteralPath $path) {
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Skill file is a directory: $path" }
            $digest = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
            if ($files.ContainsKey($relative) -and $digest -ne $files[$relative] -and
                (-not $recorded.ContainsKey($relative) -or $digest -notin $recorded[$relative])) {
                throw "Refusing to replace personal or modified skill file: $path"
            }
        }
        $pending[$relative] = @(@($recorded[$relative]) + @($files[$relative]) | Where-Object { $_ } | Select-Object -Unique)
    }
    if ($Plan) { Write-Host "Skills: $destination"; return }
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    # Checkpoint both old and new hashes so an interrupted copy can be resumed.
    Write-NativeSkillInventory $inventoryPath $pending
    foreach ($relative in $files.Keys) {
        $target = Join-Path $destination $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $source $relative) -Destination $target -Force
    }
    foreach ($relative in $recorded.Keys) {
        if ($files.ContainsKey($relative)) { continue }
        $path = Join-Path $destination $relative
        if ((Test-Path -LiteralPath $path -PathType Leaf) -and
            (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -in $recorded[$relative]) {
            Remove-Item -LiteralPath $path
        }
    }
    Write-NativeSkillInventory $inventoryPath $files
}

function Write-NativeSkillInventory([string]$path, [hashtable]$files) {
    $temporary = $path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    try {
        @{ Version = 1; Files = $files } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $temporary -Encoding UTF8
        Move-Item -LiteralPath $temporary -Destination $path -Force
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
    }
}

function Get-NativeAgentSkillNames([string]$Source) {
    $path = Join-Path $Source 'windows\agent-skills.json'
    $catalog = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    if ($catalog.version -ne 1 -or $catalog.skills -isnot [array] -or -not $catalog.skills.Count) {
        throw "Invalid native agent skill catalog: $path"
    }
    $names = @($catalog.skills)
    foreach ($name in $names) {
        if ($name -isnot [string] -or $name.Length -gt 64 -or $name -cnotmatch '\Abasaltwater-[a-z0-9-]+\z') {
            throw "Invalid native agent skill name in $path"
        }
    }
    if (@($names | Select-Object -Unique).Count -ne $names.Count) { throw "Duplicate native agent skills in $path" }
    return $names
}

function Invoke-NativeAgentSetup {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$Source,
          [string[]]$AgentTool = @('gh', 'codex'), [switch]$Node, [switch]$Python,
          [switch]$Go, [switch]$T3CodeDesktop, [switch]$Plan)
    $selected = @('gh', 'codex')
    foreach ($selection in $AgentTool) {
        foreach ($tool in $selection.Split(',') | ForEach-Object { $_.Trim().ToLowerInvariant() }) {
            if ($tool -notin @('gh', 'codex', 'claude', 'opencode')) { throw "Unsupported native agent tool: $tool" }
            $selected += $tool
        }
    }
    $AgentTool = @($selected | Select-Object -Unique)
    $Source = [IO.Path]::GetFullPath($Source)
    $skillNames = @(Get-NativeAgentSkillNames $Source)
    Write-Host 'Native Windows coding account (WSL is not required).'
    if (-not $Plan) { Ensure-Winget; Update-NativePath }
    Ensure-NativeTool git Git.Git -Plan:$Plan
    Ensure-NativeGitSettings -Plan:$Plan
    Ensure-NativeTool gh GitHub.cli -Plan:$Plan
    $npmAgents = @($AgentTool | Where-Object { $_ -in @('codex', 'opencode') -and -not (Get-NativeCommand $_) })
    if ($Node -or $npmAgents.Count) { Ensure-NativeTool node OpenJS.NodeJS.LTS -Plan:$Plan }
    foreach ($tool in $AgentTool | Where-Object { $_ -in @('codex', 'opencode') }) {
        $command = Get-NativeCommand $tool
        if (-not $command) {
            $package = if ($tool -eq 'codex') { '@openai/codex' } else { 'opencode-ai' }
            $prefix = Join-Path $env:APPDATA 'npm'
            Write-Host "Install $tool via npm: $package (current user)"
            if (-not $Plan) {
                $npm = Get-NativeCommand npm
                if (-not $npm) { throw 'npm.cmd is unavailable after Node installation; repair Node.js and rerun setup.' }
                & $npm install --global --prefix $prefix $package
                if ($LASTEXITCODE -ne 0) { throw "$tool npm installation failed (exit code $LASTEXITCODE)" }
                Add-NativeUserPath $prefix
            }
        }
        if (-not $Plan) {
            $command = Get-NativeCommand $tool
            if (-not $command) { throw "$tool is unavailable after installation" }
            & $command --version
            if ($LASTEXITCODE -ne 0) { throw "$tool version check failed (exit code $LASTEXITCODE)" }
        } elseif ($command) { Write-Host "Reuse $tool`: $command" }
    }
    if ('claude' -in $AgentTool) { Ensure-NativeTool claude Anthropic.ClaudeCode -Plan:$Plan }
    if ($Python) {
        Ensure-NativeTool uv astral-sh.uv -Plan:$Plan
        Write-Host 'Python: uv python install --default (existing Python commands are preserved)'
        if (-not $Plan) {
            $uv = Get-NativeCommand uv
            & $uv python install --default
            if ($LASTEXITCODE -ne 0) { throw "Python installation failed (exit code $LASTEXITCODE)" }
            $bin = & $uv python dir --bin
            if ($LASTEXITCODE -ne 0 -or -not $bin) { throw 'Could not locate uv Python executables' }
            Add-NativeUserPath ($bin | Select-Object -Last 1)
        }
    }
    if ($Go) { Ensure-NativeTool go GoLang.Go -Plan:$Plan }
    if ($T3CodeDesktop) {
        $desktop = Get-NativeT3Desktop
        if ($desktop) { Write-Host "Reuse T3 Code desktop: $desktop" }
        elseif ($Plan) { Write-Host 'Install T3 Code desktop via WinGet: T3Tools.T3Code' }
        else {
            Ensure-WingetPackage T3Tools.T3Code 'T3 Code desktop installation' -NoUpgrade
            if (-not (Get-NativeT3Desktop)) { throw 'T3 Code desktop was not detected after installation' }
        }
    }
    $catalogs = @('.agents\skills')
    if ('claude' -in $AgentTool) { $catalogs += '.claude\skills' }
    foreach ($catalog in $catalogs) {
        foreach ($skill in $skillNames) {
            Install-NativeSkill (Join-Path $Source "common\agent_skills\$skill") `
                (Join-Path $env:USERPROFILE "$catalog\$skill") -Plan:$Plan
        }
    }
    Write-Host 'Verify in a new terminal: git --version; gh auth status; codex login status.'
    Write-Host 'Set your Git user.name/user.email if missing; select the local Windows connection in T3 Code.'
}
