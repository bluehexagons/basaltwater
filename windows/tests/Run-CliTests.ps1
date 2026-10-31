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
    # A later .exe must not override an earlier npm .cmd shim on PATH.
    $shimDirectory = Join-Path $temporary 'first shim'
    $exeDirectory = Join-Path $temporary 'later exe'
    New-Item -ItemType Directory -Path $shimDirectory, $exeDirectory | Out-Null
    $shim = Join-Path $shimDirectory 'basaltwater-command-fixture.cmd'
    $executable = Join-Path $exeDirectory 'basaltwater-command-fixture.exe'
    Set-Content -LiteralPath $shim -Encoding ASCII -Value '@echo off', 'echo selected-shim'
    Set-Content -LiteralPath $executable -Value 'discovery-only fixture'
    Set-Content -LiteralPath (Join-Path $shimDirectory 'basaltwater-command-fixture.ps1') -Value "throw 'Must not select a policy-blocked PowerShell shim'"
    try {
        $env:PATH = $shimDirectory + ';' + $exeDirectory + ';' + $savedPath
        $selected = Get-NativeCommand 'basaltwater-command-fixture'
        Assert-True ($selected -eq $shim) 'A later executable overrode the earlier native command shim'
        Assert-True ((& $selected) -eq 'selected-shim') 'Selected command did not run through the native shim'
        $env:PATH = $exeDirectory + ';' + $shimDirectory + ';' + $savedPath
        Assert-True ((Get-NativeCommand 'basaltwater-command-fixture') -eq $executable) 'Discovery ignored executable PATH priority'
    } finally { $env:PATH = $savedPath }
    & {
        $gitRoot = Join-Path $temporary 'Git for Windows'
        New-Item -ItemType Directory -Path (Join-Path $gitRoot 'cmd'), (Join-Path $gitRoot 'bin'), (Join-Path $gitRoot 'mingw64\bin') -Force | Out-Null
        $bash = Join-Path $gitRoot 'bin\bash.exe'
        Set-Content -LiteralPath $bash -Value 'discovery fixture'
        Set-Content -LiteralPath (Join-Path $gitRoot 'git-bash.exe') -Value 'discovery fixture'
        function Get-NativeCommand($Name) { if ($Name -eq 'git') { return $script:GitFixture } }
        function Add-NativeUserPath($Directory) { $script:GitBashPaths += $Directory }
        $script:GitBashPaths = @()
        foreach ($relative in @('cmd\git.exe', 'bin\git.exe', 'mingw64\bin\git.exe')) {
            $script:GitFixture = Join-Path $gitRoot $relative
            Assert-True ((Get-NativeGitBash) -eq $bash) 'Git Bash discovery did not follow the Git for Windows installation'
        }
        Enable-NativeGitBash -Plan
        Assert-True ($script:GitBashPaths.Count -eq 0) 'Git Bash preview changed PATH'
        Enable-NativeGitBash
        Assert-True ($script:GitBashPaths.Count -eq 1 -and $script:GitBashPaths[0] -eq $gitRoot) 'Git Bash launcher was not exposed on PATH'
        Remove-Item -LiteralPath $bash
        Assert-Throws { Enable-NativeGitBash } 'Git Bash is missing'
        Assert-True ($script:GitBashPaths.Count -eq 1) 'Missing Git Bash changed PATH'
    }
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
    Assert-Throws { Invoke-NativeCli @('refresh', '--wait', '--detach') } 'Choose --wait or --detach'
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
param([switch]$Json, [switch]$Skills)
if ($Json) { @{ Healthy = $false; Skills = [bool]$Skills; Checks = @(@{ Name = 'fixture'; State = 'failed' }) } | ConvertTo-Json -Depth 4 }
exit 1
'@
    $receiptPath = Join-Path $bin 'cli-source.json'
    $receiptText = Get-Content -LiteralPath $receiptPath -Raw
    try {
        @{ Source = $doctorSource } | ConvertTo-Json | Set-Content -LiteralPath $receiptPath
        $doctor = & $launcher doctor --json | Out-String
        Assert-True ($LASTEXITCODE -eq 1 -and -not ($doctor | ConvertFrom-Json).Healthy) 'Installed doctor lost JSON or failure status'
        $doctor = & $launcher doctor --skills --json | Out-String
        Assert-True ($LASTEXITCODE -eq 1 -and ($doctor | ConvertFrom-Json).Skills) 'Installed doctor did not forward skill selection'
        Assert-Throws { Invoke-NativeCli @('doctor', '--skills', '--skills') } 'Usage: basaltw doctor'
        # Model a pwsh -> cmd -> Windows PowerShell launch with an incompatible
        # security module first on inherited PSModulePath. Never touch real modules.
        $moduleRoot = Join-Path $temporary 'incompatible modules'
        $module = Join-Path $moduleRoot 'Microsoft.PowerShell.Security'
        New-Item -ItemType Directory -Path $module -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $module 'Microsoft.PowerShell.Security.psd1') -Value "@{ ModuleVersion='99.0'; RootModule='wrong.psm1'; PowerShellVersion='99.0' }"
        Set-Content -LiteralPath (Join-Path $module 'wrong.psm1') -Value "throw 'Incompatible module was loaded'"
        Set-Content -LiteralPath (Join-Path $doctorSource 'windows\Test-AgentWorkstation.ps1') -Value @'
param([switch]$Json)
$ErrorActionPreference = 'Stop'
Get-Acl -LiteralPath $PSScriptRoot | Out-Null
'{"Healthy":true}'
'@
        $savedModules = $env:PSModulePath
        try {
            $env:PSModulePath = $moduleRoot + ';' + $savedModules
            $doctor = & $launcher doctor --json | Out-String
            Assert-True ($LASTEXITCODE -eq 0 -and ($doctor | ConvertFrom-Json).Healthy) 'Batch launcher inherited incompatible PowerShell modules'
            Assert-True ($env:PSModulePath -ceq ($moduleRoot + ';' + $savedModules)) 'Batch launcher changed its parent module environment'
        } finally { $env:PSModulePath = $savedModules }
    } finally { Set-Content -LiteralPath $receiptPath -Value $receiptText -Encoding UTF8 }
    $developmentSource = Join-Path $temporary 'development CLI fixture'
    New-Item -ItemType Directory -Path (Join-Path $developmentSource 'windows') -Force | Out-Null
    foreach ($name in @('AgentWorkstation.Common.ps1','Desktop.Common.ps1')) {
        [IO.File]::WriteAllText((Join-Path $developmentSource ('windows\' + $name)), '# fixture: no system operations')
    }
    [IO.File]::WriteAllText((Join-Path $developmentSource 'windows\Development.Common.ps1'), @'
function Invoke-NativeDevelopmentCli($Arguments) {
    $receipt = [pscustomobject]@{ State = $env:BASALTWATER_TEST_TASK_STATE; ExitCode = 23 }
    if ($Arguments[0] -eq 'wait') {
        return [pscustomobject]@{ WaitState = $env:BASALTWATER_TEST_WAIT_STATE;
            TimedOut = ($env:BASALTWATER_TEST_WAIT_STATE -eq 'timed-out'); Receipt = $receipt }
    }
    return $receipt
}
'@)
    $savedTaskState = $env:BASALTWATER_TEST_TASK_STATE
    $savedWaitState = $env:BASALTWATER_TEST_WAIT_STATE
    try {
        @{ Source = $developmentSource } | ConvertTo-Json | Set-Content -LiteralPath $receiptPath -Encoding UTF8
        foreach ($state in @('failed','timed-out','cancelled','cleanup-uncertain','launch-uncertain','interrupted','queued','running','complete')) {
            $env:BASALTWATER_TEST_TASK_STATE = $state
            $response = & $launcher desktop develop status fixture-id | Out-String
            $expected = if ($state -in @('queued','running','complete')) { 0 } else { 1 }
            Assert-True ($LASTEXITCODE -eq $expected -and ($response | ConvertFrom-Json).State -eq $state) "Development CLI lost status or exit meaning: $state"
        }
        foreach ($case in @(@('completed','complete',0), @('completed','failed',1),
            @('timed-out','running',124), @('attention-required','cleanup-uncertain',1))) {
            $env:BASALTWATER_TEST_WAIT_STATE = $case[0]
            $env:BASALTWATER_TEST_TASK_STATE = $case[1]
            $response = & $launcher desktop develop wait fixture-id --timeout 1 | Out-String
            $code = $LASTEXITCODE
            $wait = $response | ConvertFrom-Json
            Assert-True ($code -eq $case[2] -and $wait.WaitState -eq $case[0] -and
                $wait.Receipt.State -eq $case[1] -and $wait.Receipt.ExitCode -eq 23) "Development wait lost receipt or exit meaning: $($case[0])/$($case[1])"
        }
    } finally {
        $env:BASALTWATER_TEST_TASK_STATE = $savedTaskState
        $env:BASALTWATER_TEST_WAIT_STATE = $savedWaitState
        Set-Content -LiteralPath $receiptPath -Value $receiptText -Encoding UTF8
    }
    $source = Join-Path $temporary ('pinned source with spaces ' + [char]0x03bb)
    New-Item -ItemType Directory -Path $source | Out-Null
    Set-Content -LiteralPath (Join-Path $source 'install.ps1') -Encoding UTF8 -Value @'
param($Profile, [switch]$Resume, [switch]$UpdateSource, [switch]$NoUpdateSource, [switch]$NoRestartT3, [switch]$NoReboot, [switch]$Plan,
      [switch]$GameDev, [switch]$ShaderTools, [switch]$Godot, [switch]$Sunshine, [switch]$NodeVersions,
      $StorageRoot, $Channel, $Version)
$PSBoundParameters | ConvertTo-Json | Set-Content -LiteralPath $env:BASALTWATER_TEST_CAPTURE -Encoding UTF8
[Console]::Error.WriteLine('fixture warning on stderr')
exit ([int]$env:BASALTWATER_TEST_EXIT)
'@
    $statePath = Join-Path $env:LOCALAPPDATA 'Basaltwater\windows-setup.json'
    $state = [pscustomobject]@{ Version = 1; Profile = 'agent_windows';
        OwnerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value;
        Revision = 'a' * 40; Source = $source; Phase = 'complete'; AgentTool = @('gh', 'codex');
        GameDev = $true; Godot = $true; Sunshine = $true; StorageRoot = 'F:\Basaltwater'; Secret = 'must-not-print' }
    [IO.File]::WriteAllText($statePath, ($state | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    $info = & $launcher info --json | Out-String
    Assert-True ($LASTEXITCODE -eq 0 -and $info -notmatch 'must-not-print') 'Info failed or disclosed arbitrary saved fields'
    $record = $info | ConvertFrom-Json
    Assert-True ($record.Revision -eq $state.Revision -and $record.Godot -and $record.Sunshine) 'Info omitted saved installation evidence'
    $env:BASALTWATER_TEST_CAPTURE = Join-Path $temporary 'forwarded.json'
    $env:BASALTWATER_TEST_EXIT = '0'
    $before = (Get-FileHash -LiteralPath $statePath).Hash
    $code = Invoke-NativeCli @('refresh', '--dry-run', '--game-dev', '--shader-tools', '--godot', '--sunshine',
        '--node-versions', '--storage-root', 'F:\cache with spaces & punctuation', '--channel', 'dev', '--version', 'main')
    $forwarded = Get-Content -LiteralPath $env:BASALTWATER_TEST_CAPTURE -Raw | ConvertFrom-Json
    Assert-True ($code -eq 0 -and $forwarded.Resume -and $forwarded.UpdateSource -and $forwarded.Plan -and
        $forwarded.Godot -and $forwarded.GameDev -and $forwarded.ShaderTools -and $forwarded.NodeVersions -and $forwarded.Sunshine) 'Refresh lost its saved-setup/preview/tool contract'
    Assert-True ($forwarded.StorageRoot -eq 'F:\cache with spaces & punctuation' -and
        $forwarded.Profile -eq 'agent_windows') 'Refresh changed native argument boundaries/profile'
    Assert-True ((Get-FileHash -LiteralPath $statePath).Hash -eq $before) 'CLI rewrote saved setup during preview'
    & $launcher refresh --wait --no-update-source --no-restart-t3 --no-reboot | Out-Null
    $forwarded = Get-Content -LiteralPath $env:BASALTWATER_TEST_CAPTURE -Raw | ConvertFrom-Json
    Assert-True ($LASTEXITCODE -eq 0 -and $forwarded.NoUpdateSource -and $forwarded.NoRestartT3 -and $forwarded.NoReboot -and
        -not $forwarded.PSObject.Properties['UpdateSource']) 'Installed refresh lost opt-outs or also requested a source update'
    $trailingRoot = 'F:\cache with spaces\'
    $code = Invoke-NativeCli @('refresh', '--dry-run', '--storage-root', $trailingRoot)
    $forwarded = Get-Content -LiteralPath $env:BASALTWATER_TEST_CAPTURE -Raw | ConvertFrom-Json
    Assert-True ($code -eq 0 -and $forwarded.StorageRoot -eq $trailingRoot.TrimEnd('\')) 'Refresh changed a storage path across native argument forwarding'
    # Exercise the actual cmd -> PowerShell -> installer argument boundary too.
    & $launcher refresh --wait --storage-root 'F:\cache with spaces & punctuation' | Out-Null
    Assert-True ($LASTEXITCODE -eq 0) 'Installed refresh launcher failed'
    $forwarded = Get-Content -LiteralPath $env:BASALTWATER_TEST_CAPTURE -Raw | ConvertFrom-Json
    Assert-True ($forwarded.StorageRoot -eq 'F:\cache with spaces & punctuation') 'Installed launcher split the storage argument'
    # Test default following and explicit detachment without scheduling anything.
    $refreshSource = Join-Path $temporary 'refresh CLI fixture'
    New-Item -ItemType Directory -Path (Join-Path $refreshSource 'windows') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $refreshSource 'windows\AgentWorkstation.Common.ps1') -Value '# fixture'
    Set-Content -LiteralPath (Join-Path $refreshSource 'windows\Refresh.Common.ps1') -Value @'
function Start-NativeRefresh($Saved, $Source, $Arguments) {
    return [pscustomobject]@{ Id = 'fixture'; StatusCommand = 'status fixture'; LogsCommand = 'logs fixture' }
}
function Watch-NativeRefresh($Id) { Write-Host 'fixture live setup output'; return 23 }
'@
    try {
        @{ Source = $refreshSource } | ConvertTo-Json | Set-Content -LiteralPath $receiptPath -Encoding UTF8
        $output = & $launcher refresh | Out-String
        Assert-True ($LASTEXITCODE -eq 23 -and $output -match 'fixture live setup output') 'Default refresh did not follow output or propagate the result'
        $output = & $launcher refresh --detach | Out-String
        Assert-True ($LASTEXITCODE -eq 0 -and $output -notmatch 'fixture live setup output' -and $output -match 'queued') 'Explicit detachment waited or lost submission status'
        $output = & $launcher refresh --follow fixture | Out-String
        Assert-True ($LASTEXITCODE -eq 23 -and $output -match 'fixture live setup output') 'Existing-job follow lost output or result'
    } finally { Set-Content -LiteralPath $receiptPath -Value $receiptText -Encoding UTF8 }
    foreach ($drive in @('f', 'F:')) {
        & $launcher refresh --storage-drive $drive --dry-run | Out-Null
        Assert-True ($LASTEXITCODE -eq 0) 'Installed drive-based refresh failed'
        $forwarded = Get-Content -LiteralPath $env:BASALTWATER_TEST_CAPTURE -Raw | ConvertFrom-Json
        Assert-True ($forwarded.StorageRoot -eq 'F:\Basaltwater' -and $forwarded.Plan) 'Drive shorthand was not forwarded as the canonical root'
    }
    $env:BASALTWATER_TEST_EXIT = '23'
    & $launcher refresh --wait | Out-Null
    Assert-True ($LASTEXITCODE -eq 23) 'Installed CLI swallowed setup failure status'
    $state.OwnerSid = 'S-1-0-0'
    $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    Assert-Throws { Invoke-NativeCli @('refresh') } 'does not match this account'


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
    $env:LOCALAPPDATA = $savedLocal; $env:PATH = $savedPath; $env:BASALTWATER_GODOT_EXE = $savedGodot
    $env:BASALTWATER_TEST_CAPTURE = $savedCapture; $env:BASALTWATER_TEST_EXIT = $savedExit
    $resolved = [IO.Path]::GetFullPath($temporary)
    $root = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe CLI test cleanup path' }
    [IO.Directory]::Delete('\\?\' + $resolved, $true)
}
