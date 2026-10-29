<# Offline CLI/Godot checks. Only temporary files and process environment change. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw $message } }
function Assert-Throws([scriptblock]$action, [string]$pattern) {
    $caught = $null
    try { & $action | Out-Null } catch { $caught = $_ }
    if (-not $caught -or $caught.Exception.Message -notmatch $pattern) { throw "Expected $pattern; received $caught" }
}
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
. (Join-Path $repo 'windows\AgentWorkstation.Common.ps1')
. (Join-Path $repo 'windows\Cli.Common.ps1')
. (Join-Path $repo 'windows\Godot.Common.ps1')
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-cli-' + [guid]::NewGuid().ToString('N'))
$savedLocal = $env:LOCALAPPDATA; $savedPath = $env:PATH
$savedGodot = $env:BASALTWATER_GODOT_EXE
$savedCapture = $env:BASALTWATER_TEST_CAPTURE; $savedExit = $env:BASALTWATER_TEST_EXIT
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $env:LOCALAPPDATA = Join-Path $temporary ('account with spaces ' + [char]0x03bb)
    $script:Paths = @()
    function Add-NativeUserPath($directory) { $script:Paths += $directory }
    Install-NativeCli -Source $repo -Plan
    $bin = Join-Path $env:LOCALAPPDATA 'Basaltwater\bin'
    Assert-True (-not (Test-Path -LiteralPath $bin) -and $script:Paths.Count -eq 0) 'CLI plan wrote files/PATH'
    Install-NativeCli -Source $repo
    Assert-True ($script:Paths.Count -eq 1 -and $script:Paths[0] -eq $bin) 'CLI directory was not exposed on PATH'
    $launcher = Join-Path $bin 'basaltw.cmd'
    $version = & $launcher --version | Out-String
    Assert-True ($LASTEXITCODE -eq 0 -and $version -match 'Basaltwater .+native Windows') 'Installed CLI did not run from a Unicode account directory'
    $env:PATH = $bin + ';' + $env:PATH
    $version = & powershell.exe -NoProfile -ExecutionPolicy Restricted -Command 'basaltw --version; exit $LASTEXITCODE' | Out-String
    Assert-True ($LASTEXITCODE -eq 0 -and $version -match 'native Windows') 'Unqualified basaltw command was blocked by script execution policy'
    $help = & $launcher | Out-String
    Assert-True ($LASTEXITCODE -eq 0 -and $help -match 'refresh' -and $help -match '--godot') 'CLI help failed without saved state'
    Assert-Throws { Invoke-NativeCli @('refresh', '--unknown') } 'Unsupported refresh option'
    Assert-Throws { Invoke-NativeCli @('refresh', '--storage-root') } 'Missing value'
    Assert-Throws { Invoke-NativeCli @('refresh', '--storage-root', 'C:relative') } 'absolute local'
    Assert-Throws { Invoke-NativeCli @('refresh', '--storage-drive') } 'Missing value'
    Assert-Throws { Invoke-NativeCli @('refresh', '--storage-drive', 'F:\folder') } 'one drive letter'
    Assert-Throws { Invoke-NativeCli @('refresh', '--storage-root', 'F:\root', '--storage-drive', 'G') } 'Choose one'
    Assert-Throws { Invoke-NativeCli @('refresh', '--storage-drive', 'F', '--storage-root', 'G:\root') } 'Choose one'
    Assert-Throws { Invoke-NativeCli @('refresh', '--channel', 'other') } 'Channel must'
    Assert-Throws { Invoke-NativeCli @('refresh', '--version', "main`n") } 'Invalid release'
    Assert-Throws { Invoke-NativeCli @('agent') } 'Unsupported Windows command'
    Assert-Throws { Invoke-NativeCli @('refresh') } 'No saved native Windows setup'
    Assert-Throws { Invoke-NativeCli @('doctor', '--unknown') } 'Usage: basaltw doctor'
    # Dispatch the installed CLI against a disposable diagnostic source. This
    # proves policy bypass, JSON isolation and failure propagation without
    # probing the host or requiring the saved setup's source to be current.
    $doctorSource = Join-Path $temporary 'diagnostic source'
    New-Item -ItemType Directory -Path (Join-Path $doctorSource 'windows') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $doctorSource 'windows\Test-AgentWorkstation.ps1') -Value @'
param([switch]$Json)
if ($Json) { '{"Healthy":false,"Checks":[{"Name":"fixture","State":"failed"}]}' }
exit 1
'@
    $receiptPath = Join-Path $bin 'cli-source.json'
    $receiptText = Get-Content -LiteralPath $receiptPath -Raw
    try {
        @{ Source = $doctorSource } | ConvertTo-Json | Set-Content -LiteralPath $receiptPath
        $doctor = & $launcher doctor --json | Out-String
        Assert-True ($LASTEXITCODE -eq 1 -and -not ($doctor | ConvertFrom-Json).Healthy) 'Installed doctor lost JSON or failure status'
    } finally { Set-Content -LiteralPath $receiptPath -Value $receiptText -Encoding UTF8 }
    $source = Join-Path $temporary 'pinned source with spaces'
    New-Item -ItemType Directory -Path $source | Out-Null
    Set-Content -LiteralPath (Join-Path $source 'install.ps1') -Encoding UTF8 -Value @'
param($Profile, [switch]$Resume, [switch]$UpdateSource, [switch]$Plan,
      [switch]$GameDev, [switch]$ShaderTools, [switch]$Godot, [switch]$NodeVersions,
      $StorageRoot, $Channel, $Version)
$PSBoundParameters | ConvertTo-Json | Set-Content -LiteralPath $env:BASALTWATER_TEST_CAPTURE -Encoding UTF8
[Console]::Error.WriteLine('fixture warning on stderr')
exit ([int]$env:BASALTWATER_TEST_EXIT)
'@
    $statePath = Join-Path $env:LOCALAPPDATA 'Basaltwater\windows-setup.json'
    $state = [pscustomobject]@{ Version = 1; Profile = 'agent_windows';
        OwnerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value;
        Revision = 'a' * 40; Source = $source; Phase = 'complete'; AgentTool = @('gh', 'codex');
        GameDev = $true; Godot = $true; StorageRoot = 'F:\Basaltwater'; Secret = 'must-not-print' }
    $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    $info = & $launcher info --json | Out-String
    Assert-True ($LASTEXITCODE -eq 0 -and $info -notmatch 'must-not-print') 'Info failed or disclosed arbitrary saved fields'
    $record = $info | ConvertFrom-Json
    Assert-True ($record.Revision -eq $state.Revision -and $record.Godot) 'Info omitted saved installation evidence'
    $env:BASALTWATER_TEST_CAPTURE = Join-Path $temporary 'forwarded.json'
    $env:BASALTWATER_TEST_EXIT = '0'
    $before = (Get-FileHash -LiteralPath $statePath).Hash
    $code = Invoke-NativeCli @('refresh', '--dry-run', '--game-dev', '--shader-tools', '--godot',
        '--node-versions', '--storage-root', 'F:\cache with spaces & punctuation', '--channel', 'dev', '--version', 'main')
    $forwarded = Get-Content -LiteralPath $env:BASALTWATER_TEST_CAPTURE -Raw | ConvertFrom-Json
    Assert-True ($code -eq 0 -and $forwarded.Resume -and $forwarded.UpdateSource -and $forwarded.Plan -and
        $forwarded.Godot -and $forwarded.GameDev -and $forwarded.ShaderTools -and $forwarded.NodeVersions) 'Refresh lost its saved-setup/preview/tool contract'
    Assert-True ($forwarded.StorageRoot -eq 'F:\cache with spaces & punctuation' -and
        $forwarded.Profile -eq 'agent_windows') 'Refresh changed native argument boundaries/profile'
    Assert-True ((Get-FileHash -LiteralPath $statePath).Hash -eq $before) 'CLI rewrote saved setup during preview'
    $trailingRoot = 'F:\cache with spaces\'
    $code = Invoke-NativeCli @('refresh', '--dry-run', '--storage-root', $trailingRoot)
    $forwarded = Get-Content -LiteralPath $env:BASALTWATER_TEST_CAPTURE -Raw | ConvertFrom-Json
    Assert-True ($code -eq 0 -and $forwarded.StorageRoot -eq $trailingRoot.TrimEnd('\')) 'Refresh changed a storage path across native argument forwarding'
    # Exercise the actual cmd -> PowerShell -> installer argument boundary too.
    & $launcher refresh --storage-root 'F:\cache with spaces & punctuation' | Out-Null
    Assert-True ($LASTEXITCODE -eq 0) 'Installed refresh launcher failed'
    $forwarded = Get-Content -LiteralPath $env:BASALTWATER_TEST_CAPTURE -Raw | ConvertFrom-Json
    Assert-True ($forwarded.StorageRoot -eq 'F:\cache with spaces & punctuation') 'Installed launcher split the storage argument'
    foreach ($drive in @('f', 'F:')) {
        & $launcher refresh --storage-drive $drive --dry-run | Out-Null
        Assert-True ($LASTEXITCODE -eq 0) 'Installed drive-based refresh failed'
        $forwarded = Get-Content -LiteralPath $env:BASALTWATER_TEST_CAPTURE -Raw | ConvertFrom-Json
        Assert-True ($forwarded.StorageRoot -eq 'F:\Basaltwater' -and $forwarded.Plan) 'Drive shorthand was not forwarded as the canonical root'
    }
    $env:BASALTWATER_TEST_EXIT = '23'
    & $launcher refresh | Out-Null
    Assert-True ($LASTEXITCODE -eq 23) 'Installed CLI swallowed setup failure status'
    $state.OwnerSid = 'S-1-0-0'
    $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    Assert-Throws { Invoke-NativeCli @('refresh') } 'does not match this account'

    & {
        $global:BasaltwaterShortcutTest = @{ Downloads = @(); Exit = 0; Arguments = @(); Warning = $false }
        function Invoke-WebRequest {
            param($Uri, [switch]$UseBasicParsing, $OutFile, $TimeoutSec, $ErrorAction)
            Assert-True ($Uri -eq 'https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.ps1') 'Shortcut selected an unexpected installer source'
            $global:BasaltwaterShortcutTest.Downloads += $OutFile
            Set-Content -LiteralPath $OutFile -Value '# downloaded fixture'
        }
        function powershell.exe {
            $global:BasaltwaterShortcutTest.Arguments = @($args)
            if ($global:BasaltwaterShortcutTest.Warning) { Write-Error 'fixture warning on stderr' }
            $global:LASTEXITCODE = $global:BasaltwaterShortcutTest.Exit
        }
        $shortcut = Join-Path $repo 'windows\Install-GameWorkstation.ps1'
        & $shortcut -StorageRoot 'F:\development cache' -Plan
        $forwarded = $global:BasaltwaterShortcutTest.Arguments
        Assert-True ($forwarded -contains '-Godot' -and $forwarded -contains '-GameDev' -and
            $forwarded -contains '-ShaderTools' -and $forwarded -contains '-Node' -and
            $forwarded -contains '-Resume' -and $forwarded -contains '-UpdateSource' -and
            $forwarded -contains '-Plan' -and $forwarded -contains 'F:\development cache') 'Shortcut lost the game bundle/resume/storage selections'
        Assert-True (-not (Test-Path -LiteralPath $global:BasaltwaterShortcutTest.Downloads[-1])) 'Shortcut retained its temporary download'
        & $shortcut -StorageRoot 'F:\development cache\' -Plan
        Assert-True ($global:BasaltwaterShortcutTest.Arguments -contains 'F:\development cache' -and
            $global:BasaltwaterShortcutTest.Arguments -notcontains 'F:\development cache\') 'Shortcut did not normalize a trailing storage separator'
        $shortcutText = Get-Content -LiteralPath $shortcut -Raw
        & ([scriptblock]::Create($shortcutText)) -StorageDrive f: -Plan
        $forwarded = $global:BasaltwaterShortcutTest.Arguments
        Assert-True ($forwarded -contains 'F:\Basaltwater' -and $forwarded -notcontains '-StorageDrive') 'Downloaded shortcut did not resolve drive selection for the installer'
        $beforeConflict = $global:BasaltwaterShortcutTest.Downloads.Count
        Assert-Throws { & $shortcut -StorageDrive F -StorageRoot 'F:\custom' } 'Choose StorageDrive or StorageRoot'
        Assert-Throws { & $shortcut -StorageDrive 'F:\invalid' } 'StorageDrive'
        Assert-True ($global:BasaltwaterShortcutTest.Downloads.Count -eq $beforeConflict) 'Invalid shortcut storage downloaded an installer'
        $global:BasaltwaterShortcutTest.Exit = 19
        Assert-Throws { & $shortcut } 'exit code 19'
        Assert-True (-not (Test-Path -LiteralPath $global:BasaltwaterShortcutTest.Downloads[-1])) 'Failed shortcut retained its download'
        $global:BasaltwaterShortcutTest.Exit = 0
        $global:BasaltwaterShortcutTest.Warning = $true
        & $shortcut
        Assert-True ($global:BasaltwaterShortcutTest.Arguments -contains '-Resume') 'Warning on stderr interrupted the shortcut'
        $global:BasaltwaterShortcutTest.Warning = $false
        $priorLocal = $env:LOCALAPPDATA
        try {
            $env:LOCALAPPDATA = Join-Path $temporary 'fresh shortcut account'
            # The public one-liner evaluates a downloaded script without PSScriptRoot.
            Invoke-Expression (Get-Content -LiteralPath $shortcut -Raw)
            Assert-True ($global:BasaltwaterShortcutTest.Arguments -notcontains '-Resume') 'Fresh shortcut tried to resume an absent setup'
        } finally { $env:LOCALAPPDATA = $priorLocal }
        $beforeDownloadFailure = $global:BasaltwaterShortcutTest.Arguments -join "`n"
        function Invoke-WebRequest { throw 'simulated download failure' }
        Assert-Throws { & $shortcut } 'simulated download failure'
        Assert-True (($global:BasaltwaterShortcutTest.Arguments -join "`n") -eq $beforeDownloadFailure) 'Shortcut executed an installer after a failed download'
    }

    & {
        $script:Tool = $null; $script:PackageTool = $null; $script:Installs = 0; $script:VersionExit = 0
        $script:GodotWrites = @(); $script:VersionArguments = @()
        function Get-NativeCommand { return $script:Tool }
        function Get-NativeGodotPackageCommand { return $script:PackageTool }
        function Update-NativePath { }
        function Set-NativeGodotExecutable($command) { $script:GodotWrites += $command; $env:BASALTWATER_GODOT_EXE = $command }
        function Mock-GodotVersion { $script:VersionArguments = @($args); $global:LASTEXITCODE = $script:VersionExit }
        function Mock-ReplacementGodotVersion { Mock-GodotVersion @args }
        function Invoke-NativeGodotVersion($command) {
            & $command --headless --version
            if ($LASTEXITCODE -ne 0) { throw 'Godot version check failed' }
        }
        function Ensure-WingetPackage {
            param($packageId, $label)
            Assert-True ($packageId -eq 'GodotEngine.GodotEngine') 'Godot used the wrong package'
            $script:Installs++
            if ($script:InstallWorks) { $script:PackageTool = 'Mock-GodotVersion' }
        }
        $godotLauncher = Join-Path $bin 'godot.cmd'
        $script:InstallWorks = $true
        Install-NativeGodot -Plan
        Assert-True ($script:Installs -eq 0 -and -not (Test-Path -LiteralPath $godotLauncher)) 'Godot plan installed/wrote a launcher'
        Install-NativeGodot
        Assert-True ($script:Installs -eq 1 -and (Test-Path -LiteralPath $godotLauncher) -and
            $script:GodotWrites.Count -eq 1 -and ($script:VersionArguments -join ' ') -eq '--headless --version') 'Godot portable fallback was not installed and checked headlessly'
        $script:Tool = $godotLauncher
        Install-NativeGodot
        Assert-True ($script:Installs -eq 2 -and $script:GodotWrites.Count -eq 2) 'Managed Godot launcher was not updated and reconciled'
        # The old portable package disappeared and a real PATH command replaced
        # it. Project scripts and the retained launcher must follow that tool.
        $script:PackageTool = $null
        $script:Tool = 'Mock-ReplacementGodotVersion'
        Install-NativeGodot
        Assert-True ($script:Installs -eq 3 -and $script:GodotWrites.Count -eq 3 -and
            $script:GodotWrites[-1] -eq $script:Tool -and $env:BASALTWATER_GODOT_EXE -eq $script:Tool) 'Godot refresh retained the obsolete portable executable'
        $script:VersionExit = 7
        Assert-Throws { Install-NativeGodot } 'version check failed'
        Assert-True ($script:GodotWrites.Count -eq 3) 'Failed Godot verification changed the saved executable'
        $script:VersionExit = 0; $script:Tool = $null; $script:PackageTool = $null; $script:InstallWorks = $false
        Assert-Throws { Install-NativeGodot } 'unavailable after installation'
    }
    # Real GUI-subsystem fixtures protect the PS 5.1 wait/exit-code boundary.
    $guiFailure = Join-Path $temporary 'Godot GUI failure.exe'
    Add-Type -TypeDefinition 'public class GodotFailureFixture { public static int Main() { System.Threading.Thread.Sleep(250); return 23; } }' `
        -OutputAssembly $guiFailure -OutputType WindowsApplication
    $global:LASTEXITCODE = 0
    Assert-Throws { Invoke-NativeGodotVersion $guiFailure } 'exit code 23'
    $guiHang = Join-Path $temporary 'Godot GUI timeout.exe'
    Add-Type -TypeDefinition 'public class GodotHangFixture { public static int Main() { System.Threading.Thread.Sleep(20000); return 0; } }' `
        -OutputAssembly $guiHang -OutputType WindowsApplication
    Assert-Throws { Invoke-NativeGodotVersion $guiHang -TimeoutSeconds 1 } 'version check timed out'
    # Discover only the standard Godot package, ignoring the .NET variant.
    $packages = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    foreach ($package in @('GodotEngine.GodotEngine_Microsoft.Winget.Source_test', 'GodotEngine.GodotEngine.Mono_test')) {
        $directory = Join-Path $packages $package
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $directory 'Godot_v4.7.2-stable_win64_console.exe') -Value 'fixture'
    }
    Assert-True ((Get-NativeGodotPackageCommand) -like '*GodotEngine.GodotEngine_Microsoft*') 'Godot package discovery chose the wrong edition'
    Write-Host 'Native CLI and Godot tests passed.'
} finally {
    Remove-Variable BasaltwaterShortcutTest -Scope Global -ErrorAction SilentlyContinue
    $env:LOCALAPPDATA = $savedLocal; $env:PATH = $savedPath; $env:BASALTWATER_GODOT_EXE = $savedGodot
    $env:BASALTWATER_TEST_CAPTURE = $savedCapture; $env:BASALTWATER_TEST_EXIT = $savedExit
    $resolved = [IO.Path]::GetFullPath($temporary)
    $root = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe CLI test cleanup path' }
    [IO.Directory]::Delete('\\?\' + $resolved, $true)
}
