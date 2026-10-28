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
                        'windows\Setup-AgentWorkstation.ps1', 'windows\Install-GameWorkstation.ps1', 'windows\GameDevelopment.Common.ps1',
                        'windows\DevelopmentStorage.Common.ps1', 'windows\Invoke-Basaltw.ps1',
                        'windows\Cli.Common.ps1', 'windows\Godot.Common.ps1', 'windows\tests\Run-CliTests.ps1',
                        'windows\Enter-GameDevShell.ps1', 'windows\tests\Run-GameDevelopmentTests.ps1',
                        'windows\tests\Run-AgentWorkstationTests.ps1')) {
        $tokens = $null; $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $repo $file), [ref]$tokens, [ref]$errors) | Out-Null
        Assert-True ($errors.Count -eq 0) "PowerShell syntax errors in $file`: $errors"
    }
    & (Join-Path $PSScriptRoot 'Run-GameDevelopmentTests.ps1')
    & (Join-Path $PSScriptRoot 'Run-CliTests.ps1')
    . (Join-Path $repo 'windows\Bootstrap.Common.ps1')
    . (Join-Path $repo 'windows\AgentWorkstation.Common.ps1')
    # Cross a real native argv boundary: function mocks do not model PS 5.1 quoting.
    $python = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $python -or $python.Source -match '\\WindowsApps\\') { throw 'Native argument tests require an installed Python on PATH' }
    $argvProbe = Join-Path $temporary 'native argv.py'
    Set-Content -LiteralPath $argvProbe -Encoding UTF8 -Value "from __future__ import annotations`nimport json, sys`nprint(json.dumps(sys.argv[1:]))"
    foreach ($override in @('--wait --installPath "F:\coding with spaces\tools\vs2022"',
                            '--wait --path cache="F:\cache with spaces"')) {
        $nativeArgs = @('--override', (ConvertTo-WingetOverride $override))
        $received = & $python.Source $argvProbe @nativeArgs | ConvertFrom-Json
        Assert-True ($LASTEXITCODE -eq 0 -and $received.Count -eq 2 -and
            $received[0] -eq '--override' -and $received[1] -eq $override) 'WinGet vendor override changed across the native argv boundary'
    }
    $invalidCatalog = Join-Path $temporary 'invalid catalog'
    New-Item -ItemType Directory -Path (Join-Path $invalidCatalog 'windows') -Force | Out-Null
    $catalogPath = Join-Path $invalidCatalog 'windows\agent-skills.json'
    foreach ($catalog in @(
        @{ version = 1; skills = @('..\outside') },
        @{ version = 1; skills = @('Basaltwater-example') },
        @{ version = 1; skills = @('basaltwater-example', 'basaltwater-example') },
        @{ version = 1; skills = @() }
    )) {
        $catalog | ConvertTo-Json | Set-Content -LiteralPath $catalogPath -Encoding UTF8
        Assert-Throws { Get-NativeAgentSkillNames $invalidCatalog } 'Invalid native|Duplicate native'
    }
    $tokens = $null; $errors = $null
    $installerAst = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $repo 'install.ps1'), [ref]$tokens, [ref]$errors)
    $extractor = $installerAst.Find({ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Expand-SourceArchive'
    }, $true)
    Invoke-Expression $extractor.Extent.Text
    $optionBuilder = $installerAst.Find({ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-NativeOptionalArguments'
    }, $true)
    Invoke-Expression $optionBuilder.Extent.Text
    & {
        function Invoke-NativeAgentSetup { param($Source, $AgentTool, [switch]$Node, [switch]$Python, [switch]$Go) }
        $legacy = [pscustomobject]@{ NodeVersions = $false; GameDev = $false; ShaderTools = $false; Godot = $false; StorageRoot = '' }
        Assert-True ((Get-NativeOptionalArguments $legacy).Count -eq 0) 'Older pinned helpers received unsupported defaults'
        $legacy.GameDev = $true
        Assert-Throws { Get-NativeOptionalArguments $legacy } 'Pinned source lacks GameDev'
    }
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
    $firstPath = $env:PATH
    Update-NativePath
    Assert-True ($env:PATH -eq $firstPath) 'Repeated PATH refresh accumulated duplicate entries'
    $merged = Merge-NativePath @($null, '', 'C:\transient;C:\bin;C:\bin\;c:/BIN;;""', '"C:\tools with spaces";C:\tools with spaces')
    Assert-True ($merged -eq 'C:\transient;C:\bin;"C:\tools with spaces"') 'PATH merge changed priority or retained equivalent Windows entries'
    $merged = Merge-NativePath @('%SystemRoot%\System32', (Join-Path $env:SystemRoot 'system32'))
    Assert-True ($merged -eq (Join-Path $env:SystemRoot 'System32')) 'PATH merge did not expand variables before deduplicating'
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
        $script:InstallExit = 0
        $override = '--wait --installPath "F:\tools with spaces"'
        Ensure-WingetPackage Microsoft.VisualStudio.2022.BuildTools 'Compiler install' -NoUpgrade -Override $override
        $forwarded = $script:WingetCalls[-1]
        Assert-True ($forwarded[-2] -eq '--override' -and $forwarded[-1] -eq (ConvertTo-WingetOverride $override)) 'Vendor overrides were split or dropped'
    } finally { Remove-Item Function:\winget.exe }

    # Source staging and resume use the saved native selection, never WSL.
    function global:Get-CimInstance { return [pscustomobject]@{ ProductType = 1; BuildNumber = 26100 } }
    function global:Get-PSDrive { return [pscustomobject]@{ Free = 100GB } }
    function global:Get-Volume {
        param($DriveLetter)
        if ($DriveLetter -ne 'X') { return @{ FileSystem = 'NTFS'; HealthStatus = 'Healthy' } }
    }
    function global:Invoke-RestMethod {
        param($Uri)
        $global:AgentInstallerTestRequests += $Uri
        return @{ sha = $global:AgentInstallerTestRevision }
    }
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
    $global:AgentInstallerTestRequests = @()
    $global:AgentInstallerTestFailure = $true
    try {
        $splitPlan = & (Join-Path $repo 'windows\Setup-AgentWorkstation.ps1') -StorageDrive f: -Plan 6>&1 | Out-String
        Assert-True ($splitPlan -match 'F:\\Basaltwater\\cache\\npm') 'Checkout setup did not resolve the drive-based layout'
        Assert-Throws { & (Join-Path $repo 'windows\Setup-AgentWorkstation.ps1') -StorageDrive F -StorageRoot 'F:\custom' -Plan } 'Choose StorageDrive or StorageRoot'
        foreach ($revision in @(('a' * 40), ('b' * 40), ('c' * 40), ('d' * 40))) {
            $pinned = Join-Path $temporary "archive-source\basaltwater-$revision"
            New-Item -ItemType Directory -Path (Join-Path $pinned 'windows') -Force | Out-Null
            Copy-Item -LiteralPath (Join-Path $repo 'install.ps1') -Destination (Join-Path $pinned 'install.ps1')
            if ($revision -eq ('c' * 40)) {
                Set-Content -LiteralPath (Join-Path $pinned 'install.ps1') -Value @'
[CmdletBinding()]
param([string]$Profile, [switch]$Resume)
throw 'Older pinned installer must not execute with unsupported selections'
'@
            }
            if ($revision -eq ('d' * 40)) {
                $entry = Join-Path $pinned 'install.ps1'
                (Get-Content -LiteralPath $entry -Raw).Replace('$ErrorActionPreference = "Stop"',
                    "throw 'simulated source entry failure'") | Set-Content -LiteralPath $entry -Encoding UTF8
            }
            Set-Content -LiteralPath (Join-Path $pinned 'windows\Bootstrap.Common.ps1') -Value 'function Ensure-Winget { }'
            Copy-Item -LiteralPath (Join-Path $repo 'windows\DevelopmentStorage.Common.ps1') -Destination (Join-Path $pinned 'windows\DevelopmentStorage.Common.ps1')
            Set-Content -LiteralPath (Join-Path $pinned 'windows\AgentWorkstation.Common.ps1') -Value @'
function Invoke-NativeAgentSetup {
    param($Source, $AgentTool, [switch]$Node, [switch]$Python, [switch]$Go, [switch]$NodeVersions,
          [switch]$GameDev, [switch]$ShaderTools, [switch]$Godot, $StorageRoot, [switch]$T3CodeDesktop, [switch]$Plan)
    $global:AgentInstallerTestCalls += [pscustomobject]@{ Source = $Source; Tools = $AgentTool; Node = [bool]$Node;
        Python = [bool]$Python; Go = [bool]$Go; NodeVersions = [bool]$NodeVersions;
        GameDev = [bool]$GameDev; ShaderTools = [bool]$ShaderTools; Godot = [bool]$Godot; StorageRoot = $StorageRoot;
        Desktop = [bool]$T3CodeDesktop; Plan = [bool]$Plan }
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
        $lockDirectory = Join-Path $env:LOCALAPPDATA 'Basaltwater'
        New-Item -ItemType Directory -Path $lockDirectory -Force | Out-Null
        $held = [IO.File]::Open((Join-Path $lockDirectory 'setup.lock'), [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        try {
            foreach ($profile in @('agent_windows', 'server_wsl')) {
                Assert-Throws { & (Join-Path $repo 'install.ps1') -Profile $profile } 'Another Windows setup'
            }
            Assert-True ($global:AgentInstallerTestRequests.Count -eq 0) 'Locked fresh setup resolved source before taking the lock'
            Assert-True (-not (Test-Path -LiteralPath (Join-Path $lockDirectory 'windows-setup.json'))) 'Locked fresh setup wrote state'
        } finally { $held.Dispose() }
        Assert-Throws { & (Join-Path $repo 'install.ps1') -Profile agent_windows -Channel dev -Node -Python -GameDev -ShaderTools -Godot -StorageRoot 'F:\coding with spaces' } 'interrupted native setup'
        Assert-True ($global:AgentInstallerTestRequests[-1] -match '/commits/main$') 'Dev channel did not resolve main'
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
        Assert-True ($global:AgentInstallerTestCalls[-1].GameDev -and $global:AgentInstallerTestCalls[-1].ShaderTools) 'Resume plan lost game selections'
        Assert-True $global:AgentInstallerTestCalls[-1].Godot 'Resume plan lost Godot selection'
        Assert-True ((Get-FileHash -LiteralPath $nativeStatePath -Algorithm SHA256).Hash -eq $beforePlan) 'Resume plan rewrote setup state'
        & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume
        $nativeState = Get-Content -LiteralPath $nativeStatePath -Raw | ConvertFrom-Json
        Assert-True ($nativeState.Phase -eq 'complete') 'Native resume did not complete'
        $held = [IO.File]::Open((Join-Path $lockDirectory 'setup.lock'), [IO.FileMode]::Open,
            [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $beforeLocked = (Get-FileHash -LiteralPath $nativeStatePath).Hash
        try {
            foreach ($profile in @('agent_windows', 'server_wsl')) {
                Assert-Throws { & (Join-Path $repo 'install.ps1') -Profile $profile -Resume } 'Another Windows setup'
            }
            & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume -Plan
            Assert-True ((Get-FileHash -LiteralPath $nativeStatePath).Hash -eq $beforeLocked) 'Locked resume/plan changed state'
        } finally { $held.Dispose() }
        Assert-True ($global:AgentInstallerTestCalls[-1].Node -and $global:AgentInstallerTestCalls[-1].Python -and
            $global:AgentInstallerTestCalls[-1].Desktop) 'Native resume lost the saved tools/desktop defaults'
        Assert-True ('codex' -in $global:AgentInstallerTestCalls[-1].Tools) 'Native default provider was not saved'
        $beforeRuntimePlan = (Get-FileHash -LiteralPath $nativeStatePath).Hash
        & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume -Go -AgentTool claude -Node:$false -Python:$false -T3CodeDesktop:$false -Plan
        $selected = $global:AgentInstallerTestCalls[-1]
        Assert-True ($selected.Go -and -not $selected.Node -and -not $selected.Python -and
            -not $selected.Desktop -and 'claude' -in $selected.Tools -and 'codex' -in $selected.Tools) 'Resume preview ignored explicit runtime/provider/desktop changes'
        Assert-True ((Get-FileHash -LiteralPath $nativeStatePath).Hash -eq $beforeRuntimePlan) 'Runtime/provider preview rewrote saved state'
        & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume -Go -AgentTool claude -Node:$false -Python:$false -T3CodeDesktop:$false
        $selected = $global:AgentInstallerTestCalls[-1]
        Assert-True ($selected.Go -and -not $selected.Node -and -not $selected.Python -and
            -not $selected.Desktop -and 'claude' -in $selected.Tools) 'Resume application ignored explicit runtime/provider/desktop changes'
        & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume -Node -Python -T3CodeDesktop
        $selected = $global:AgentInstallerTestCalls[-1]
        Assert-True ($selected.Node -and $selected.Python -and $selected.Desktop -and $selected.Go -and
            'claude' -in $selected.Tools) 'Reselection lost saved runtime/provider settings'
        $global:AgentInstallerTestRevision = 'b' * 40
        & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume -UpdateSource
        $nativeState = Get-Content -LiteralPath $nativeStatePath -Raw | ConvertFrom-Json
        Assert-True ($nativeState.Revision -eq $global:AgentInstallerTestRevision -and
            $global:AgentInstallerTestCalls[-1].Source -eq $nativeState.Source) 'Source update did not execute the newly pinned source'
        Assert-True ($global:AgentInstallerTestCalls[-1].Go -and 'claude' -in $global:AgentInstallerTestCalls[-1].Tools) 'Source update lost saved runtime/provider additions'
        Assert-True ($global:AgentInstallerTestCalls[-1].GameDev -and $global:AgentInstallerTestCalls[-1].ShaderTools) 'Source update lost game selections'
        Assert-True $global:AgentInstallerTestCalls[-1].Godot 'Source update lost Godot selection'
        Assert-True ($global:AgentInstallerTestCalls[-1].StorageRoot -eq 'F:\coding with spaces') 'Source update lost external storage'
        $global:AgentInstallerTestRevision = 'd' * 40
        Assert-Throws { & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume -UpdateSource -Channel dev } 'source entry failure'
        $failedUpdate = Get-Content -LiteralPath $nativeStatePath -Raw | ConvertFrom-Json
        Assert-True ($failedUpdate.Phase -eq 'staged' -and $failedUpdate.Revision -eq ('d' * 40)) 'Failed source entry was marked complete'
        Assert-True ($global:AgentInstallerTestRequests[-1] -match '/commits/main$') 'Dev source update did not resolve main'
        $global:AgentInstallerTestRevision = 'b' * 40
        & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume -UpdateSource
        Assert-True ((Get-Content -LiteralPath $nativeStatePath -Raw | ConvertFrom-Json).Phase -eq 'complete') 'Source recovery did not complete'
        & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume -StorageDrive 'f:' -Plan
        Assert-True ($global:AgentInstallerTestCalls[-1].StorageRoot -eq 'F:\Basaltwater') 'Drive preview retained the old custom root'
        & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume -StorageDrive f
        $nativeState = Get-Content -LiteralPath $nativeStatePath -Raw | ConvertFrom-Json
        Assert-True ($nativeState.StorageRoot -eq 'F:\Basaltwater' -and
            -not $nativeState.PSObject.Properties['StorageDrive']) 'Drive selection was not saved as one canonical root'
        & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume -UpdateSource
        Assert-True ($global:AgentInstallerTestCalls[-1].StorageRoot -eq 'F:\Basaltwater') 'Refresh forgot the split-storage selection'
        $beforeBadStorage = (Get-FileHash -LiteralPath $nativeStatePath).Hash
        $beforeBadCalls = $global:AgentInstallerTestCalls.Count
        Assert-Throws { & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume -StorageDrive X } 'healthy mounted NTFS'
        Assert-Throws { & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume -UpdateSource -StorageDrive X } 'healthy mounted NTFS'
        $linkedRoot = Join-Path $temporary 'unsafe storage'
        New-Item -ItemType Directory -Path (Join-Path $linkedRoot 'cache') -Force | Out-Null
        $linkedCache = Join-Path $linkedRoot 'cache\npm'
        New-Item -ItemType Junction -Path $linkedCache -Target $temporary | Out-Null
        try {
            Assert-Throws { & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume -StorageRoot $linkedRoot } 'junctions/symlinks'
        } finally { [IO.Directory]::Delete($linkedCache) }
        Assert-True ((Get-FileHash -LiteralPath $nativeStatePath).Hash -eq $beforeBadStorage -and
            $global:AgentInstallerTestCalls.Count -eq $beforeBadCalls) 'Invalid storage changed saved state or entered setup'
        Assert-Throws { & (Join-Path $repo 'install.ps1') -Profile agent_windows -StorageDrive F -StorageRoot 'F:\other' -Plan } 'Choose StorageDrive or StorageRoot'
        Assert-Throws { & (Join-Path $repo 'install.ps1') -Profile agent_windows -StorageDrive 'F:\folder' -Plan } 'StorageDrive'
        $savedFreshLocal = $env:LOCALAPPDATA
        try {
            $env:LOCALAPPDATA = Join-Path $temporary 'fresh split account'
            Assert-Throws { & (Join-Path $repo 'install.ps1') -Profile agent_windows -StorageDrive X } 'healthy mounted NTFS'
            Assert-True (-not (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'Basaltwater\windows-setup.json'))) 'Fresh invalid storage created saved selections'
            & (Join-Path $repo 'install.ps1') -Profile agent_windows -StorageDrive F: -Channel dev -Version release-test
            Assert-True ($global:AgentInstallerTestRequests[-1] -match '/commits/release-test$') 'Version did not override the channel'
            $freshState = Get-Content -LiteralPath (Join-Path $env:LOCALAPPDATA 'Basaltwater\windows-setup.json') -Raw | ConvertFrom-Json
            Assert-True ($freshState.Phase -eq 'complete' -and $freshState.StorageRoot -eq 'F:\Basaltwater') 'Fresh drive-based setup did not complete with saved storage'
        } finally { $env:LOCALAPPDATA = $savedFreshLocal }
        # Old saved states acquire false defaults; new opt-ins can be saved on resume.
        $nativeState.PSObject.Properties.Remove('NodeVersions')
        $nativeState.PSObject.Properties.Remove('GameDev')
        $nativeState.PSObject.Properties.Remove('ShaderTools')
        $nativeState.PSObject.Properties.Remove('Godot')
        $nativeState.PSObject.Properties.Remove('StorageRoot')
        $nativeState | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $nativeStatePath -Encoding UTF8
        & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume -NodeVersions
        Assert-True ($global:AgentInstallerTestCalls[-1].NodeVersions -and
            -not $global:AgentInstallerTestCalls[-1].GameDev -and -not $global:AgentInstallerTestCalls[-1].Godot) 'Legacy state defaults/resume opt-in were not respected'
        & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume -Godot
        Assert-True $global:AgentInstallerTestCalls[-1].Godot 'Existing Windows setup could not adopt Godot'
        $beforeOldSource = (Get-FileHash -LiteralPath $nativeStatePath).Hash
        $global:AgentInstallerTestRevision = 'c' * 40
        Assert-Throws { & (Join-Path $repo 'install.ps1') -Profile agent_windows -Resume -UpdateSource -GameDev } 'Selected source does not support'
        Assert-True ((Get-FileHash -LiteralPath $nativeStatePath).Hash -eq $beforeOldSource) 'Unsupported source update replaced saved selections/revision'
        $savedInstallerLocal = $env:LOCALAPPDATA
        try {
            $env:LOCALAPPDATA = Join-Path $temporary 'fresh old source account'
            Assert-Throws { & (Join-Path $repo 'install.ps1') -Profile agent_windows -GameDev } 'Selected source does not support'
            Assert-True (-not (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'Basaltwater\windows-setup.json'))) 'Unsupported source was saved as an installation'
        } finally { $env:LOCALAPPDATA = $savedInstallerLocal }
        Assert-Throws { & (Join-Path $repo 'install.ps1') -Profile agent_windows -BuildServer -Plan } 'not available with agent_windows'
        Assert-Throws { & (Join-Path $repo 'install.ps1') -Profile agent_windows -Version "main`n" -Plan } 'Invalid release reference'
        Assert-Throws { & (Join-Path $repo 'install.ps1') -GameDev -Plan } 'requires agent_windows'
        Assert-Throws { & (Join-Path $repo 'install.ps1') -Godot -Plan } 'requires agent_windows'
        Assert-Throws { & (Join-Path $repo 'install.ps1') -StorageDrive F -Plan } 'requires agent_windows'
    } finally {
        foreach ($name in @('Get-CimInstance', 'Get-PSDrive', 'Get-Volume', 'Invoke-RestMethod', 'Invoke-WebRequest', 'wsl.exe', 'Read-Host')) {
            Remove-Item "Function:\$name"
        }
        Remove-Variable AgentInstallerTestRevision, AgentInstallerTestArchives, AgentInstallerTestCalls, AgentInstallerTestFailure, AgentInstallerTestRequests -Scope Global
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
    & {
        function Copy-Item {
            param($LiteralPath, $Destination, [switch]$Force)
            if ($LiteralPath -like '*references\guide.md') {
                [IO.File]::WriteAllText($Destination, 'partial interrupted copy')
                throw 'simulated interrupted skill copy'
            }
            Microsoft.PowerShell.Management\Copy-Item -LiteralPath $LiteralPath -Destination $Destination -Force:$Force
        }
        Assert-Throws { Install-NativeSkill $source $destination } 'interrupted skill copy'
    }
    Assert-True ((Get-Content -LiteralPath (Join-Path $destination 'references\guide.md')) -eq 'first version') 'Interrupted skill copy replaced the managed file'
    Assert-True (@(Get-ChildItem -LiteralPath $destination -Recurse -Filter '*.tmp').Count -eq 0) 'Interrupted skill copy left temporary files'
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
            'Schniz.fnm' { $script:Tools.fnm = 'Mock-Version' }
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
        if ($args[0] -eq 'python') {
            Assert-True ($args -contains '--no-config' -and -not $env:UV_PYTHON) 'Host Python adopted a project configuration/request'
            $script:UvCalls += ,$args
            $global:LASTEXITCODE = $script:UvExit
        }
        if ($args.Count -ge 2 -and $args[1] -eq 'dir') { return (Join-Path $env:USERPROFILE '.local\bin') }
    }
    $script:EnsureCalls = 0; $script:Packages = @(); $script:Tools = @{}; $script:Desktop = $false
    $script:AddedPaths = @(); $script:NpmCalls = @(); $script:NpmExit = 0
    $script:VersionExit = 0; $script:FailPackage = $false
    $script:UvCalls = @(); $script:UvExit = 0
    Invoke-NativeAgentSetup -Source $repo -AgentTool 'gh,codex,claude,opencode' -Node -Python -Go -NodeVersions -T3CodeDesktop -Plan
    Assert-True ($script:EnsureCalls -eq 0 -and $script:Packages.Count -eq 0) 'Plan invoked a package manager'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $env:USERPROFILE '.claude'))) 'Plan changed the fake home'
    Invoke-NativeAgentSetup -Source $repo -AgentTool 'gh,codex,claude,opencode' -Node -Python -Go -NodeVersions -T3CodeDesktop
    Assert-True ($script:Packages.Count -eq 8) 'Fresh setup missed a selected native package'
    Assert-True ($script:NpmCalls.Count -eq 2) 'Fresh setup missed npm providers'
    Assert-True ($script:NpmCalls[0] -contains '--prefix') 'npm install was not scoped to the user prefix'
    Assert-True (Test-Path -LiteralPath (Join-Path $env:USERPROFILE '.claude\skills\basaltwater-subagents\references\t3-orchestration.md')) 'Claude supporting resources were not copied'
    Assert-True (Test-Path -LiteralPath (Join-Path $env:USERPROFILE '.agents\skills\basaltwater-windows-workspace\SKILL.md')) 'Windows workspace guidance was not installed'
    $pinnedProject = Join-Path $temporary 'project with Python pin'
    New-Item -ItemType Directory -Path $pinnedProject | Out-Null
    Set-Content -LiteralPath (Join-Path $pinnedProject '.python-version') -Value '3.12'
    $savedPythonRequest = $env:UV_PYTHON
    Push-Location -LiteralPath $pinnedProject
    try {
        $env:UV_PYTHON = '3.11'
        Invoke-NativeAgentSetup -Source $repo -Python
        Assert-True ($env:UV_PYTHON -eq '3.11' -and $script:UvCalls[-2] -contains '--default') 'Host setup changed the caller Python request'
        $script:UvExit = 23
        Assert-Throws { Invoke-NativeAgentSetup -Source $repo -Python } 'Python installation failed'
        Assert-True ($env:UV_PYTHON -eq '3.11') 'Python failure lost the caller request'
    } finally {
        $script:UvExit = 0
        $env:UV_PYTHON = $savedPythonRequest
        Pop-Location
    }
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
