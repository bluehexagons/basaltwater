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
$env:VCPKG_ROOT = 'C:\VS\bundled-vcpkg'
'@
        function Get-NativeVisualStudio { return $fakeVs }
        function Test-NativeWindowsSdk { return $true }
        function Get-NativeCommand { return 'mock-cl.exe' }
        $savedVcpkg = [Environment]::GetEnvironmentVariable('VCPKG_ROOT', 'Process')
        try {
            foreach ($prior in @($null, 'F:\project vcpkg')) {
                [Environment]::SetEnvironmentVariable('VCPKG_ROOT', $prior, 'Process')
                Enter-NativeGameDevEnvironment
                Assert-True ([Environment]::GetEnvironmentVariable('VCPKG_ROOT', 'Process') -eq $prior) 'Developer environment replaced project vcpkg selection'
            }
        } finally { [Environment]::SetEnvironmentVariable('VCPKG_ROOT', $savedVcpkg, 'Process') }
    }
    & {
        function Get-NativeVisualStudio { return $script:VisualStudio }
        function Test-NativeWindowsSdk { return $script:Sdk }
        function Test-Path { return $script:Vswhere }
        function Ensure-WingetPackage {
            param($packageId, $label, [switch]$NoUpgrade, $Override)
            $script:Installs++
            Assert-True ($packageId -eq 'Microsoft.VisualStudio.2022.BuildTools' -and $NoUpgrade) 'Wrong compiler package or upgrade policy'
            Assert-True ($Override -match '--wait' -and $Override -match '--norestart' -and
                $Override -match '--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended') 'Missing compiler/SDK workload or reboot control'
            if ($script:Reboot) { throw 'restart required' }
            if ($script:CompleteInstall) { $script:VisualStudio = 'C:\VS'; $script:Sdk = $true }
        }
        $script:VisualStudio = $null; $script:Sdk = $false; $script:Vswhere = $false
        $script:Installs = 0; $script:CompleteInstall = $true; $script:Reboot = $false
        Ensure-NativeCppTools -Plan
        Assert-True ($script:Installs -eq 0) 'Compiler plan installed a workload'
        Ensure-NativeCppTools
        Ensure-NativeCppTools
        Assert-True ($script:Installs -eq 1) 'Complete compiler/SDK was not reused'
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
        $script:Command = $null; $script:Installs = 0; $script:Paths = @(); $script:VersionExit = 0
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
        Assert-True ($script:Installs -eq 2) 'Missing command was not installed and verified'
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
        $script:FileSystem = 'NTFS'; $script:StorageWrites = @{}
        function Get-Volume { return @{ FileSystem = $script:FileSystem; HealthStatus = 'Healthy' } }
        function Set-NativeStorageVariable($name, $path) { $script:StorageWrites[$name] = $path }
        $root = Join-Path $temporary 'development storage'
        foreach ($invalid in @('relative\path', '\\server\share', 'C:\', 'C:\NUL', 'C:\cache:stream')) {
            Assert-Throws { Resolve-NativeStorageRoot $invalid } 'StorageRoot'
        }
        $script:FileSystem = 'exFAT'
        Assert-Throws { Resolve-NativeStorageRoot $root } 'NTFS'
        $script:FileSystem = 'NTFS'
        Set-NativeDevelopmentStorage $root -Plan
        Assert-True ($script:StorageWrites.Count -eq 0 -and -not (Test-Path -LiteralPath $root)) 'Storage plan wrote directories/environment'
        Set-NativeDevelopmentStorage $root
        Assert-True ($script:StorageWrites.Count -eq 6 -and
            $script:StorageWrites.FNM_DIR -eq (Join-Path $root 'node')) 'Storage selection lost runtime/cache paths'
        Assert-True (Test-Path -LiteralPath (Join-Path $root 'repos') -PathType Container) 'New workspace root was not prepared'
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
