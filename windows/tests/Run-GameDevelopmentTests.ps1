<# Offline native game-tool tests. No vendor installers, registry or PATH writes. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw $message } }
function Assert-Throws([scriptblock]$action, [string]$pattern) {
    $caught = $null
    try { & $action | Out-Null } catch { $caught = $_ }
    if (-not $caught -or $caught.Exception.Message -notmatch $pattern) { throw "Expected $pattern; received $caught" }
}
. (Join-Path $PSScriptRoot '..\GameDevelopment.Common.ps1')
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-game-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    & {
        $fakeVswhere = Join-Path $temporary 'product-vswhere.cmd'
        Set-Content -LiteralPath $fakeVswhere -Encoding ASCII -Value '@echo off', 'echo %BASALTWATER_TEST_VS_PRODUCT%', 'exit /b 0'
        function Join-Path { return $fakeVswhere }
        $savedProduct = $env:BASALTWATER_TEST_VS_PRODUCT
        try {
            foreach ($edition in @('BuildTools', 'Community', 'Professional', 'Enterprise')) {
                $env:BASALTWATER_TEST_VS_PRODUCT = 'Microsoft.VisualStudio.Product.' + $edition
                Assert-True ((Get-NativeVisualStudioPackage) -eq ('Microsoft.VisualStudio.2022.' + $edition)) 'VS update selected another edition'
            }
            $env:BASALTWATER_TEST_VS_PRODUCT = 'unknown'
            Assert-Throws { Get-NativeVisualStudioPackage } 'Unrecognized Visual Studio'
        } finally { $env:BASALTWATER_TEST_VS_PRODUCT = $savedProduct }
    }
    & {
        $sdk = Join-Path $temporary 'SDK'
        function Get-ItemProperty { return [pscustomobject]@{ KitsRoot10 = $sdk } }
        Assert-True (-not (Test-NativeWindowsSdk)) 'Missing SDK was accepted'
        foreach ($file in @('Include\10.0.26100.0\um\Windows.h', 'Include\10.0.26100.0\shared\sdkddkver.h',
                            'Include\10.0.26100.0\ucrt\stdio.h', 'Lib\10.0.26100.0\um\x64\kernel32.lib',
                            'Lib\10.0.26100.0\ucrt\x64\ucrt.lib')) {
            $path = Join-Path $sdk $file
            New-Item -ItemType Directory -Path (Split-Path -Parent $path) -Force | Out-Null
            Set-Content -LiteralPath $path -Value 'fixture'
        }
        Assert-True (-not (Test-NativeWindowsSdk)) 'SDK without a resource compiler was accepted'
        $rc = Join-Path $sdk 'bin\10.0.26100.0\x64\rc.exe'
        New-Item -ItemType Directory -Path (Split-Path -Parent $rc) -Force | Out-Null
        Set-Content -LiteralPath $rc -Value 'fixture'
        Assert-True (Test-NativeWindowsSdk) 'Complete x64 SDK was not recognized'
    }
    & {
        $fakeVs = Join-Path $temporary 'fake VS'
        $launcher = Join-Path $fakeVs 'Common7\Tools\Launch-VsDevShell.ps1'
        New-Item -ItemType Directory -Path (Split-Path -Parent $launcher) -Force | Out-Null
        Set-Content -LiteralPath $launcher -Value @'
param($Arch, $HostArch, [switch]$SkipAutomaticLocation)
if ($Arch -ne 'amd64' -or $HostArch -ne 'amd64' -or -not $SkipAutomaticLocation) { throw 'Incorrect developer environment arguments' }
if ((Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer') -notin @($env:PATH -split ';')) { throw 'Vendor vswhere lookup has no PATH entry' }
$env:VCPKG_ROOT = 'C:\VS\bundled-vcpkg'
'@
        function Get-NativeVisualStudio { return $fakeVs }
        function Test-NativeWindowsSdk { return $true }
        function Get-NativeCommand { return 'mock-cl.exe' }
        $savedVcpkg = [Environment]::GetEnvironmentVariable('VCPKG_ROOT', 'Process')
        $savedDeveloperPath = $env:PATH
        try {
            foreach ($prior in @($null, 'F:\project vcpkg')) {
                [Environment]::SetEnvironmentVariable('VCPKG_ROOT', $prior, 'Process')
                Enter-NativeGameDevEnvironment
                # Framework and modern .NET expose an unset variable as null or
                # empty respectively. Both mean no project selection; compare
                # nonempty selections exactly, including case and spaces.
                Assert-True ([string][Environment]::GetEnvironmentVariable('VCPKG_ROOT', 'Process') -ceq [string]$prior) 'Developer environment replaced project vcpkg selection'
            }
        } finally {
            [Environment]::SetEnvironmentVariable('VCPKG_ROOT', $savedVcpkg, 'Process')
            $env:PATH = $savedDeveloperPath
        }
    }
    & {
        function Get-NativeVisualStudio { return $script:VisualStudio }
        function Get-NativeVisualStudioPackage { return $script:VisualStudioPackage }
        function Test-NativeWindowsSdk { return $script:Sdk }
        function Test-Path { return $script:Vswhere }
        function Ensure-WingetPackage {
            param($packageId, $label, $Override)
            $script:Installs++
            Assert-True ($packageId -eq $script:VisualStudioPackage) 'Wrong compiler package'
            if ($Override) {
                Assert-True ($Override -match '--wait' -and $Override -match '--norestart' -and
                    $Override -match '--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended') 'Missing compiler/SDK workload or reboot control'
            }
            if ($script:Reboot) { throw 'restart required' }
            if ($script:CompleteInstall) { $script:VisualStudio = 'C:\VS'; $script:Sdk = $true }
        }
        $script:VisualStudioPackage = 'Microsoft.VisualStudio.2022.BuildTools'
        $script:VisualStudio = $null; $script:Sdk = $false; $script:Vswhere = $false
        $script:Installs = 0; $script:CompleteInstall = $true; $script:Reboot = $false
        Ensure-NativeCppTools -Plan
        Assert-True ($script:Installs -eq 0) 'Compiler plan installed a workload'
        Ensure-NativeCppTools
        Ensure-NativeCppTools
        Assert-True ($script:Installs -eq 2) 'Complete compiler/SDK skipped its package update'
        $script:VisualStudioPackage = 'Microsoft.VisualStudio.2022.Community'
        Ensure-NativeCppTools
        $script:VisualStudioPackage = 'Microsoft.VisualStudio.2022.BuildTools'
        $script:VisualStudio = $null; $script:Sdk = $false; $script:CompleteInstall = $false
        Assert-Throws { Ensure-NativeCppTools } 'incomplete'
        $script:Reboot = $true
        Assert-Throws { Ensure-NativeCppTools } 'restart required'
        $script:Vswhere = $true
        $fakeVswhere = Join-Path $temporary 'vswhere.cmd'
        Set-Content -LiteralPath $fakeVswhere -Value "@echo off`r`necho C:\existing VS`r`nexit /b 0" -Encoding ASCII
        function Join-Path { return $fakeVswhere }
        $before = $script:Installs
        Assert-Throws { Ensure-NativeCppTools } 'Complete the existing'
        Assert-True ($before -eq $script:Installs) 'Partial existing Visual Studio was overwritten'
    }
    & {
        function Get-PSDrive { return @{ Free = $script:Free } }
        $script:Free = 25GB
        Assert-NativeGameSpace
        $script:Free = 25GB - 1
        Assert-Throws { Assert-NativeGameSpace } 'at least 25 GiB'
        function Get-PSDrive($Name) { return @{ Free = $(if ($Name -eq 'F') { 25GB } else { $script:Free }) } }
        $script:Free = 15GB
        Assert-NativeGameSpace 'F:\development'
        $script:Free = 15GB - 1
        Assert-Throws { Assert-NativeGameSpace 'F:\development' } 'at least 15 GiB'
    }
    & {
        $script:Missing = ''; $script:ReadySdk = $true
        function Get-NativeVisualStudio { return 'F:\VS' }
        function Test-NativeWindowsSdk { return $script:ReadySdk }
        function Get-NativeGameDirectories { return (Join-Path $temporary 'vendor discovery') }
        function Get-NativeShaderDirectories { return (Join-Path $temporary 'shader discovery') }
        function Get-NativeCommand($name) { if ($name -ne $script:Missing) { return "mock-$name.exe" } }
        Assert-True (Test-NativeGameToolsPresent -GameDev -ShaderTools) 'Complete installed tooling was not recognized for reconciliation'
        $script:Missing = 'clang-tidy'
        Assert-True (-not (Test-NativeGameToolsPresent -GameDev)) 'Missing selected analyzer bypassed installation space checks'
        Assert-True (Test-NativeGameToolsPresent -ShaderTools) 'Unselected game tooling blocked shader-only reconciliation'
        $vendor = Join-Path $temporary 'vendor discovery'
        New-Item -ItemType Directory -Path $vendor | Out-Null
        foreach ($name in @('7z', 'cmake', 'ctest', 'clang', 'clang-format', 'clang-tidy', 'llvm-symbolizer')) {
            $script:Missing = $name
            Set-Content -LiteralPath (Join-Path $vendor "$name.exe") -Value 'fixture'
            Assert-True (Test-NativeGameToolsPresent -GameDev) "Installed $name outside PATH triggered installation space requirements"
        }
        function Add-NativeUserPath { throw 'Discovery changed PATH' }
        function Ensure-WingetPackage { throw 'Discovery installed a package' }
        $script:Missing = '7z'
        Assert-True (Test-NativeGameToolsPresent -GameDev) 'Vendor-only tool was not reusable without installation'
        $script:ReadySdk = $false
        Assert-True (-not (Test-NativeGameToolsPresent -GameDev)) 'Missing SDK bypassed installation space checks'
    }
    & {
        $script:Command = $null; $script:Installs = 0; $script:Paths = @(); $script:VersionExit = 0; $script:InstallWorks = $false
        $bin = Join-Path $temporary 'vendor with spaces'
        New-Item -ItemType Directory -Path $bin | Out-Null
        $fakeTool = Join-Path $bin 'fake-tool.cmd'
        Set-Content -LiteralPath $fakeTool -Value "@echo off`r`nexit /b 0" -Encoding ASCII
        function Get-NativeCommand { return $script:Command }
        function Add-NativeUserPath($directory) { $script:Paths += $directory }
        function Update-NativePath { }
        function Ensure-WingetPackage {
            $script:Installs++
            if ($script:InstallWorks) { $script:Command = 'Mock-GameVersion' }
        }
        function Mock-GameVersion { $script:VersionArgs = @($args); $global:LASTEXITCODE = $script:VersionExit }
        # An existing vendor CLI outside PATH can be reused without reinstalling.
        Copy-Item -LiteralPath $fakeTool -Destination (Join-Path $bin '7z.exe')
        Ensure-NativeGameCommand 7z 7zip.7zip @($bin) -versionArgument i -Plan
        Assert-True ($script:Paths.Count -eq 0 -and $script:Installs -eq 0) 'Plan changed PATH/packages'
        # Mock invocation; only discovery is expected to choose this file.
        $script:Command = 'Mock-GameVersion'
        Ensure-NativeGameCommand 7z 7zip.7zip @($bin) -versionArgument i
        Assert-True ($script:VersionArgs[0] -eq 'i') '7-Zip was probed with an unsupported version switch'
        $script:VersionExit = 7
        Assert-Throws { Ensure-NativeGameCommand clang LLVM.LLVM } 'version check failed'
        $script:VersionExit = 0; $script:Command = $null; $script:InstallWorks = $false
        Assert-Throws { Ensure-NativeGameCommand cmake Kitware.CMake } 'unavailable after'
        $script:InstallWorks = $true
        Ensure-NativeGameCommand cmake Kitware.CMake
        Assert-True ($script:Installs -eq 4) 'Existing/missing commands were not updated and verified'
    }
    & {
        $script:Calls = @(); $script:Cpp = 0
        function Ensure-NativeCppTools { param([switch]$Plan, $StorageRoot) $script:Cpp++ }
        function Ensure-NativeGameCommand { $script:Calls += ,@($args) }
        function Get-NativeShaderDirectories { return (Join-Path $temporary 'SDK\Bin') }
        function Get-NativeCommand { return $null }
        Install-NativeGameTools -GameDev -Plan
        Assert-True ($script:Cpp -eq 1 -and $script:Calls.Count -eq 8) 'Game bundle omitted build/lint/archive prerequisites'
        Assert-True (-not ($script:Calls | Where-Object { $_ -contains 'KhronosGroup.VulkanSDK' })) 'Game bundle selected the optional SDK'
        Install-NativeGameTools -ShaderTools -Plan
        Assert-True ($script:Cpp -eq 1 -and $script:Calls[-1] -contains 'KhronosGroup.VulkanSDK') 'Shader-only selection changed compiler selection'
    }
    & {
        . (Join-Path $PSScriptRoot '..\DevelopmentStorage.Common.ps1')
        $script:FileSystem = 'NTFS'; $script:Health = 'Healthy'; $script:Mounted = $true; $script:StorageWrites = @{}
        function Get-Volume { if ($script:Mounted) { return @{ FileSystem = $script:FileSystem; HealthStatus = $script:Health } } }
        function Set-NativeStorageVariable($name, $path) { $script:StorageWrites[$name] = $path }
        $root = Join-Path $temporary 'development storage'
        foreach ($invalid in @('relative\path', '\\server\share', 'C:\', 'C:\NUL', 'C:\cache:stream')) {
            Assert-Throws { Resolve-NativeStorageRoot $invalid } 'StorageRoot'
        }
        $script:FileSystem = 'exFAT'
        Assert-Throws { Resolve-NativeStorageRoot $root } 'NTFS'
        $script:FileSystem = 'NTFS'
        $script:Health = 'Unhealthy'
        Assert-Throws { Set-NativeDevelopmentStorage $root } 'healthy mounted NTFS'
        $script:Health = 'Healthy'; $script:Mounted = $false
        Assert-Throws { Set-NativeDevelopmentStorage $root } 'healthy mounted NTFS'
        $script:Mounted = $true
        Set-NativeDevelopmentStorage $root -Plan
        Assert-True ($script:StorageWrites.Count -eq 0 -and -not (Test-Path -LiteralPath $root)) 'Storage plan wrote directories/environment'
        Set-NativeDevelopmentStorage $root
        Assert-True ($script:StorageWrites.Count -eq 6 -and
            $script:StorageWrites.FNM_DIR -eq (Join-Path $root 'node')) 'Storage selection lost runtime/cache paths'
        Assert-True (Test-Path -LiteralPath (Join-Path $root 'repos') -PathType Container) 'New workspace root was not prepared'
        function Get-NativeStorageVariable($name) { return $script:StorageWrites[$name] }
        Assert-NativeDevelopmentStorage $root
        $script:StorageWrites.Remove('GOCACHE')
        Assert-Throws { Assert-NativeDevelopmentStorage $root } 'storage variable GOCACHE'
        $script:StorageWrites.GOCACHE = 'C:\wrong-cache'
        Assert-Throws { Assert-NativeDevelopmentStorage $root } 'storage variable GOCACHE'
        $script:StorageWrites.GOCACHE = Join-Path $root 'cache\go-build'
        Remove-Item -LiteralPath (Join-Path $root 'cache\go-build')
        Assert-Throws { Assert-NativeDevelopmentStorage $root } 'directory is missing'
        Assert-True (-not (Test-Path -LiteralPath (Join-Path $root 'cache\go-build'))) 'Storage health repaired a missing directory'
        $linked = Join-Path $root 'linked'
        New-Item -ItemType Junction -Path $linked -Target $temporary | Out-Null
        try { Assert-Throws { Resolve-NativeStorageRoot (Join-Path $linked 'outside') } 'junctions/symlinks' }
        finally { [IO.Directory]::Delete($linked) }
    }
    Write-Host 'Native game development tests passed.'
} finally {
    $resolved = [IO.Path]::GetFullPath($temporary)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe game-test cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
