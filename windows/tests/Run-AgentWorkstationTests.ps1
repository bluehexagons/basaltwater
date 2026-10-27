<# Offline native coding setup tests. Packages, providers and registry writes are mocked. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw $message } }
function Assert-Throws([scriptblock]$action, [string]$pattern) {
    $caught = $null
    try { & $action | Out-Null } catch { $caught = $_ }
    if (-not $caught -or $caught.Exception.Message -notmatch $pattern) { throw "Expected failure: $pattern; received: $caught" }
}
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-agent-test-' + [guid]::NewGuid().ToString('N'))
$savedProfile = $env:USERPROFILE
$savedLocal = $env:LOCALAPPDATA
$savedAppData = $env:APPDATA
$savedPath = $env:PATH
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    foreach ($file in @('install.ps1', 'windows\Bootstrap.Common.ps1', 'windows\AgentWorkstation.Common.ps1',
                        'windows\Setup-AgentWorkstation.ps1', 'windows\tests\Run-AgentWorkstationTests.ps1')) {
        $tokens = $null; $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $repo $file), [ref]$tokens, [ref]$errors) | Out-Null
        Assert-True ($errors.Count -eq 0) "PowerShell syntax errors in $file`: $errors"
    }
    . (Join-Path $repo 'windows\Bootstrap.Common.ps1')
    . (Join-Path $repo 'windows\AgentWorkstation.Common.ps1')
    $tokens = $null; $errors = $null
    $installerAst = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $repo 'install.ps1'), [ref]$tokens, [ref]$errors)
    $extractor = $installerAst.Find({ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Expand-SourceArchive'
    }, $true)
    Invoke-Expression $extractor.Extent.Text
    Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
    $unsafeZip = Join-Path $temporary 'unsafe.zip'
    $zip = [IO.Compression.ZipFile]::Open($unsafeZip, [IO.Compression.ZipArchiveMode]::Create)
    try { $zip.CreateEntry('../outside.txt') | Out-Null } finally { $zip.Dispose() }
    Assert-Throws { Expand-SourceArchive $unsafeZip (Join-Path $temporary 'extract') } 'Unsafe source archive entry'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $temporary 'outside.txt'))) 'Archive escaped the extraction directory'
    $env:USERPROFILE = Join-Path $temporary 'user with spaces'
    $env:LOCALAPPDATA = Join-Path $env:USERPROFILE 'AppData\Local'
    $env:APPDATA = Join-Path $env:USERPROFILE 'AppData\Roaming'
    New-Item -ItemType Directory -Path $env:LOCALAPPDATA -Force | Out-Null
    $env:PATH = 'C:\transient-provider'
    Update-NativePath
    Assert-True ($env:PATH.StartsWith('C:\transient-provider;')) 'PATH refresh dropped the invoking provider'
    $env:PATH = $savedPath

    & {
        function Get-NativeCommand { if ($script:GitAvailable) { return 'Mock-NativeGit' } }
        function Mock-NativeGit {
            if ($args -contains '--get') {
                $global:LASTEXITCODE = $script:GitQueryExit
                if ($script:GitQueryExit -eq 0) { return $script:GitSetting }
            } else {
                $script:GitWrites++
                Assert-True (($args -join ' ') -eq 'config --global core.longpaths true') 'Git setting changed outside the intended user scope'
                $global:LASTEXITCODE = $script:GitWriteExit
            }
        }
        $script:GitAvailable = $false
        Ensure-NativeGitSettings -Plan
        Assert-Throws { Ensure-NativeGitSettings } 'Git is unavailable'
        $script:GitAvailable = $true
        $script:GitQueryExit = 1; $script:GitWriteExit = 0; $script:GitWrites = 0
        Ensure-NativeGitSettings -Plan
        Assert-True ($script:GitWrites -eq 0) 'Git configuration changed during a plan'
        Ensure-NativeGitSettings
        Assert-True ($script:GitWrites -eq 1) 'Unset Git long-path support was not enabled'
        foreach ($value in @('true', 'false')) {
            $script:GitQueryExit = 0; $script:GitSetting = $value
            Ensure-NativeGitSettings
        }
        Assert-True ($script:GitWrites -eq 1) 'An explicit Git setting was overwritten'
        $script:GitQueryExit = 128
        Assert-Throws { Ensure-NativeGitSettings } 'Could not read Git'
        $script:GitQueryExit = 1; $script:GitWriteExit = 5
        Assert-Throws { Ensure-NativeGitSettings } 'Could not enable Git'
    }

    # WinGet repair must bootstrap its PowerShell dependencies for this account.
    & {
        $script:WingetAvailable = $false
        function Get-Command { if ($script:WingetAvailable) { return 'winget.exe' } }
        function Add-AppxPackage { }
        function Install-PackageProvider {
            param($Name, $Scope, [switch]$Force)
            Assert-True ($Name -eq 'NuGet' -and $Scope -eq 'CurrentUser') 'NuGet bootstrap requires machine-wide rights'
        }
        function Install-Module {
            param($Name, $Scope, [switch]$Force, $Repository)
            Assert-True ($Name -eq 'Microsoft.WinGet.Client' -and $Scope -eq 'CurrentUser') 'WinGet module requires machine-wide rights'
        }
        function Import-Module { }
        function Repair-WinGetPackageManager { $script:WingetAvailable = $true }
        Ensure-Winget
        Assert-True $script:WingetAvailable 'Missing WinGet was not repaired'
    }

    # WinGet must verify its already-installed exit and propagate other failures.
    function global:winget.exe {
        $script:WingetCalls += ,$args
        $global:LASTEXITCODE = if ($args[0] -eq 'list') { $script:ListExit } else { $script:InstallExit }
    }
    try {
        $script:WingetCalls = @(); $script:ListExit = 0; $script:InstallExit = -1978335189
        Ensure-WingetPackage T3Tools.T3Code 'T3 install' -NoUpgrade
        Assert-True ($script:WingetCalls.Count -eq 2) 'Already-installed exit was not verified'
        Assert-True ($script:WingetCalls[0] -contains '--no-upgrade') 'Native setup could upgrade a running desktop'
        $script:InstallExit = -1978335135
        Ensure-WingetPackage T3Tools.T3Code 'T3 install' -NoUpgrade
        $script:ListExit = 1
        Assert-Throws { Ensure-WingetPackage T3Tools.T3Code 'T3 install' } 'failed'
        $script:InstallExit = 3010
        Assert-Throws { Ensure-WingetPackage Git.Git 'Git install' } 'restart'
        $script:InstallExit = -1978334966
        Assert-Throws { Ensure-WingetPackage Git.Git 'Git install' } 'restart'
        $script:InstallExit = 17
        Assert-Throws { Ensure-WingetPackage Git.Git 'Git install' } 'exit code 17'
    } finally { Remove-Item Function:\winget.exe }

    # Source staging and resume use the saved native selection, never WSL.
    function global:Get-CimInstance { return [pscustomobject]@{ ProductType = 1; BuildNumber = 26100 } }
    function global:Get-PSDrive { return [pscustomobject]@{ Free = 100GB } }
    function global:Invoke-RestMethod { return @{ sha = $global:AgentInstallerTestRevision } }
    function global:Invoke-WebRequest {
        param([switch]$UseBasicParsing, [string]$Uri, [string]$OutFile)
        if ($Uri -notmatch '/archive/([a-f0-9]{40})\.zip\z' -or
            -not $global:AgentInstallerTestArchives.ContainsKey($Matches[1])) { throw 'Unexpected source download' }
        Copy-Item -LiteralPath $global:AgentInstallerTestArchives[$Matches[1]] -Destination $OutFile
    }
    function global:wsl.exe { throw 'Native installer invoked WSL' }
    function global:Read-Host { throw 'Native installer prompted for a Linux account' }
    $global:AgentInstallerTestRevision = 'a' * 40
    $global:AgentInstallerTestArchives = @{}
    $global:AgentInstallerTestCalls = @()
    $global:AgentInstallerTestFailure = $true
    try {
        foreach ($revision in @(('a' * 40), ('b' * 40))) {
            $pinned = Join-Path $temporary "archive-source\basaltwater-$revision"
            New-Item -ItemType Directory -Path (Join-Path $pinned 'windows') -Force | Out-Null
            Copy-Item -LiteralPath (Join-Path $repo 'install.ps1') -Destination (Join-Path $pinned 'install.ps1')
            Set-Content -LiteralPath (Join-Path $pinned 'windows\Bootstrap.Common.ps1') -Value 'function Ensure-Winget { }'
            Set-Content -LiteralPath (Join-Path $pinned 'windows\AgentWorkstation.Common.ps1') -Value @'
function Invoke-NativeAgentSetup {
    param($Source, $AgentTool, [switch]$Node, [switch]$Python, [switch]$Go, [switch]$T3CodeDesktop, [switch]$Plan)
    $global:AgentInstallerTestCalls += [pscustomobject]@{ Source = $Source; Tools = $AgentTool; Node = [bool]$Node;
        Python = [bool]$Python; Go = [bool]$Go; Desktop = [bool]$T3CodeDesktop; Plan = [bool]$Plan }
    if ($global:AgentInstallerTestFailure) { throw 'simulated interrupted native setup' }
}
'@
            $archive = Join-Path $temporary ($revision + '.zip')
            Compress-Archive -LiteralPath $pinned -DestinationPath $archive
            $zip = [IO.Compression.ZipFile]::Open($archive, [IO.Compression.ZipArchiveMode]::Update)
            try { $zip.CreateEntry("basaltwater-$revision/" + ('long-path/' * 40) + 'proof.txt') | Out-Null }
            finally { $zip.Dispose() }
            $global:AgentInstallerTestArchives[$revision] = $archive
        }
        Assert-Throws { & (Join-Path $repo 'install.ps1') -Profile agent_windows -Node -Python } 'interrupted native setup'
        $nativeStatePath = Join-Path $env:LOCALAPPDATA 'Basaltwater\windows-setup.json'
        $nativeState = Get-Content -LiteralPath $nativeStatePath -Raw | ConvertFrom-Json
        Assert-True ($nativeState.Phase -eq 'applying') 'Failed native setup was marked complete'
        Assert-True ($nativeState.Source -eq (Join-Path $env:LOCALAPPDATA "Basaltwater\source\$($nativeState.Revision)")) 'Downloaded source was not staged at the pinned revision'
        Assert-True ([IO.File]::Exists('\\?\' + $nativeState.Source + '\' + ('long-path\' * 40) + 'proof.txt')) 'Long source archive path was not extracted and moved'
        Assert-True (-not (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'Basaltwater\wsl-setup.json'))) 'Native installer wrote WSL state'
        $global:AgentInstallerTestFailure = $false
        $beforePlan = (Get-FileHash -LiteralPath $nativeStatePath -Algorithm SHA256).Hash
        & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume -Plan
        Assert-True ($global:AgentInstallerTestCalls[-1].Plan -and $global:AgentInstallerTestCalls[-1].Python) 'Resume plan lost saved selections'
        Assert-True ((Get-FileHash -LiteralPath $nativeStatePath -Algorithm SHA256).Hash -eq $beforePlan) 'Resume plan rewrote setup state'
        & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume
        $nativeState = Get-Content -LiteralPath $nativeStatePath -Raw | ConvertFrom-Json
        Assert-True ($nativeState.Phase -eq 'complete') 'Native resume did not complete'
        Assert-True ($global:AgentInstallerTestCalls[-1].Node -and $global:AgentInstallerTestCalls[-1].Python -and
            $global:AgentInstallerTestCalls[-1].Desktop) 'Native resume lost the saved tools/desktop defaults'
        Assert-True ('codex' -in $global:AgentInstallerTestCalls[-1].Tools) 'Native default provider was not saved'
        $global:AgentInstallerTestRevision = 'b' * 40
        & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume -UpdateSource
        $nativeState = Get-Content -LiteralPath $nativeStatePath -Raw | ConvertFrom-Json
        Assert-True ($nativeState.Revision -eq $global:AgentInstallerTestRevision -and
            $global:AgentInstallerTestCalls[-1].Source -eq $nativeState.Source) 'Source update did not execute the newly pinned source'
        Assert-Throws { & (Join-Path $repo 'install.ps1') -Profile agent_windows -BuildServer -Plan } 'not available with agent_windows'
        Assert-Throws { & (Join-Path $repo 'install.ps1') -Profile agent_windows -Version "main`n" -Plan } 'Invalid release reference'
    } finally {
        foreach ($name in @('Get-CimInstance', 'Get-PSDrive', 'Invoke-RestMethod', 'Invoke-WebRequest', 'wsl.exe', 'Read-Host')) {
            Remove-Item "Function:\$name"
        }
        Remove-Variable AgentInstallerTestRevision, AgentInstallerTestArchives, AgentInstallerTestCalls, AgentInstallerTestFailure -Scope Global
    }

    # Real bundle copies only touch a private fake home; supporting files are preserved.
    $source = Join-Path $temporary 'source skill'
    $destination = Join-Path $env:USERPROFILE '.agents\skills\example'
    New-Item -ItemType Directory -Path (Join-Path $source 'references') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $source 'SKILL.md') -Value "metadata:`n  managed-by: basaltwater"
    Set-Content -LiteralPath (Join-Path $source 'references\guide.md') -Value 'first version'
    New-Item -ItemType Directory -Path (Join-Path $source 'scripts\__pycache__') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $source 'scripts\__pycache__\helper.cpython-315.pyc') -Value 'generated cache'
    Set-Content -LiteralPath (Join-Path $source 'scripts\legacy.pyc') -Value 'generated cache'
    Install-NativeSkill $source $destination -Plan
    Assert-True (-not (Test-Path -LiteralPath $destination)) 'Plan wrote a skill catalog'
    Install-NativeSkill $source $destination
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $destination 'scripts'))) 'Generated Python caches were installed as skill resources'
    Set-Content -LiteralPath (Join-Path $destination 'personal.txt') -Value 'keep'
    Set-Content -LiteralPath (Join-Path $source 'references\guide.md') -Value 'second version'
    Install-NativeSkill $source $destination
    Assert-True ((Get-Content -LiteralPath (Join-Path $destination 'references\guide.md')) -eq 'second version') 'Managed reference was not refreshed'
    Set-Content -LiteralPath (Join-Path $destination 'references\guide.md') -Value 'personal edit'
    Assert-Throws { Install-NativeSkill $source $destination } 'personal or modified'
    Remove-Item -LiteralPath (Join-Path $source 'references\guide.md')
    $script:InventoryWriter = ${function:Write-NativeSkillInventory}
    function Write-NativeSkillInventory([string]$path, [hashtable]$files) {
        & $script:InventoryWriter $path $files
        throw 'interrupted after inventory checkpoint'
    }
    Assert-Throws { Install-NativeSkill $source $destination } 'interrupted after inventory checkpoint'
    Set-Item Function:\Write-NativeSkillInventory $script:InventoryWriter
    Install-NativeSkill $source $destination
    Assert-True ((Get-Content -LiteralPath (Join-Path $destination 'references\guide.md')) -eq 'personal edit') 'Modified obsolete resource was removed'
    Assert-True ((Get-Content -LiteralPath (Join-Path $destination 'personal.txt')) -eq 'keep') 'Personal file was removed'
    @{ Version = 1; Files = @{ '..\outside.txt' = @('A' * 64) } } | ConvertTo-Json -Depth 4 |
        Set-Content -LiteralPath (Join-Path $destination '.basaltwater-windows-files.json')
    Assert-Throws { Install-NativeSkill $source $destination } 'Invalid skill inventory'

    # A directory junction must not redirect writes outside the skill catalog.
    $junction = Join-Path $temporary 'linked catalog'
    New-Item -ItemType Junction -Path $junction -Target $source | Out-Null
    try { Assert-Throws { Install-NativeSkill $source (Join-Path $junction 'example') } 'linked skill path' }
    finally { [IO.Directory]::Delete($junction) }

    # Exercise the complete tool workflow without invoking any actual installer/provider.
    function Ensure-Winget { $script:EnsureCalls++ }
    function Update-NativePath { }
    function Add-NativeUserPath([string]$directory) { $script:AddedPaths += $directory }
    function Get-NativeCommand([string]$name) {
        if ($script:Tools.ContainsKey($name)) { return $script:Tools[$name] }
        return $null
    }
    function Get-NativeT3Desktop { if ($script:Desktop) { return 'C:\apps\T3 Code.exe' }; return $null }
    function Ensure-WingetPackage([string]$packageId, [string]$label, [switch]$NoUpgrade) {
        $script:Packages += $packageId
        if ($script:FailPackage) { throw 'simulated package failure' }
        switch ($packageId) {
            'Git.Git' { $script:Tools.git = 'Mock-Version' }
            'GitHub.cli' { $script:Tools.gh = 'Mock-Version' }
            'OpenJS.NodeJS.LTS' { $script:Tools.node = 'Mock-Version'; $script:Tools.npm = 'Mock-Npm' }
            'astral-sh.uv' { $script:Tools.uv = 'Mock-Uv' }
            'GoLang.Go' { $script:Tools.go = 'Mock-Version' }
            'Anthropic.ClaudeCode' { $script:Tools.claude = 'Mock-Version' }
            'T3Tools.T3Code' { $script:Desktop = $true }
            default { throw "Unexpected package: $packageId" }
        }
    }
    function Mock-Version { $global:LASTEXITCODE = $script:VersionExit; 'test version' }
    function Mock-Npm {
        $script:NpmCalls += ,$args
        $global:LASTEXITCODE = $script:NpmExit
        if ($script:NpmExit -eq 0) {
            $tool = if ($args[-1] -eq '@openai/codex') { 'codex' } else { 'opencode' }
            $script:Tools[$tool] = 'Mock-Version'
        }
    }
    function Mock-Uv {
        $global:LASTEXITCODE = 0
        if ($args.Count -ge 2 -and $args[1] -eq 'dir') { return (Join-Path $env:USERPROFILE '.local\bin') }
    }
    $script:EnsureCalls = 0; $script:Packages = @(); $script:Tools = @{}; $script:Desktop = $false
    $script:AddedPaths = @(); $script:NpmCalls = @(); $script:NpmExit = 0
    $script:VersionExit = 0; $script:FailPackage = $false
    Invoke-NativeAgentSetup -Source $repo -AgentTool 'gh,codex,claude,opencode' -Node -Python -Go -T3CodeDesktop -Plan
    Assert-True ($script:EnsureCalls -eq 0 -and $script:Packages.Count -eq 0) 'Plan invoked a package manager'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $env:USERPROFILE '.claude'))) 'Plan changed the fake home'
    Invoke-NativeAgentSetup -Source $repo -AgentTool 'gh,codex,claude,opencode' -Node -Python -Go -T3CodeDesktop
    Assert-True ($script:Packages.Count -eq 7) 'Fresh setup missed a selected native package'
    Assert-True ($script:NpmCalls.Count -eq 2) 'Fresh setup missed npm providers'
    Assert-True ($script:NpmCalls[0] -contains '--prefix') 'npm install was not scoped to the user prefix'
    Assert-True (Test-Path -LiteralPath (Join-Path $env:USERPROFILE '.claude\skills\basaltwater-subagents\references\t3-orchestration.md')) 'Claude supporting resources were not copied'
    Assert-True (Test-Path -LiteralPath (Join-Path $env:USERPROFILE '.agents\skills\basaltwater-windows-workspace\SKILL.md')) 'Windows workspace guidance was not installed'
    $script:Packages = @(); $script:NpmCalls = @()
    Invoke-NativeAgentSetup -Source $repo -T3CodeDesktop
    Assert-True ($script:Packages.Count -eq 0 -and $script:NpmCalls.Count -eq 0) 'Rerun reinstalled existing tools'
    $script:VersionExit = 9
    Assert-Throws { Invoke-NativeAgentSetup -Source $repo } 'version check failed'
    $script:VersionExit = 0
    $script:Tools.Remove('codex'); $script:NpmExit = 23
    Assert-Throws { Invoke-NativeAgentSetup -Source $repo } 'npm installation failed'
    $script:Tools = @{}; $script:FailPackage = $true
    Assert-Throws { Invoke-NativeAgentSetup -Source $repo } 'simulated package failure'
    Assert-Throws { Invoke-NativeAgentSetup -Source $repo -AgentTool unsupported -Plan } 'Unsupported native agent tool'
    Write-Host 'Native agent workstation tests passed.'
} finally {
    $env:USERPROFILE = $savedProfile; $env:LOCALAPPDATA = $savedLocal
    $env:APPDATA = $savedAppData; $env:PATH = $savedPath
    $resolvedTemporary = [IO.Path]::GetFullPath($temporary)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolvedTemporary.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test cleanup path' }
    [IO.Directory]::Delete('\\?\' + $resolvedTemporary, $true)
}
