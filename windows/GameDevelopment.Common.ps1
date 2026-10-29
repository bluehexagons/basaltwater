<# Optional host prerequisites. Project bootstraps own native libraries and pins. #>
function Get-NativeVisualStudio {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) { return $null }
    $instances = & $vswhere -latest -products '*' -version '[17.0,18.0)' `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -format json | Out-String | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Visual Studio discovery failed' }
    foreach ($instance in @($instances)) {
        if ($instance -and $instance.isComplete -and $instance.isLaunchable -and
            (Test-Path -LiteralPath (Join-Path $instance.installationPath 'Common7\Tools\Launch-VsDevShell.ps1'))) {
            return $instance.installationPath
        }
    }
    return $null
}

function Test-NativeWindowsSdk {
    $kits = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows Kits\Installed Roots' -ErrorAction SilentlyContinue
    if (-not $kits -or -not $kits.PSObject.Properties['KitsRoot10']) { return $false }
    foreach ($include in Get-ChildItem -LiteralPath (Join-Path $kits.KitsRoot10 'Include') -Directory -ErrorAction SilentlyContinue) {
        $version = $include.Name
        if ((Test-Path -LiteralPath (Join-Path $include.FullName 'um\Windows.h') -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $include.FullName 'shared\sdkddkver.h') -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $include.FullName 'ucrt\stdio.h') -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $kits.KitsRoot10 "Lib\$version\um\x64\kernel32.lib") -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $kits.KitsRoot10 "Lib\$version\ucrt\x64\ucrt.lib") -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $kits.KitsRoot10 "bin\$version\x64\rc.exe") -PathType Leaf)) { return $true }
    }
    return $false
}

function Get-NativeVisualStudioPackage {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    $product = & $vswhere -latest -products '*' -version '[17.0,18.0)' `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property productId
    if ($LASTEXITCODE -ne 0) { throw 'Visual Studio product discovery failed' }
    switch ($product) {
        'Microsoft.VisualStudio.Product.BuildTools' { return 'Microsoft.VisualStudio.2022.BuildTools' }
        'Microsoft.VisualStudio.Product.Community' { return 'Microsoft.VisualStudio.2022.Community' }
        'Microsoft.VisualStudio.Product.Professional' { return 'Microsoft.VisualStudio.2022.Professional' }
        'Microsoft.VisualStudio.Product.Enterprise' { return 'Microsoft.VisualStudio.2022.Enterprise' }
        default { throw 'Unrecognized Visual Studio 2022 product; update it with Visual Studio Installer.' }
    }
}

function Ensure-NativeCppTools([switch]$Plan, [string]$StorageRoot = '') {
    $visualStudio = Get-NativeVisualStudio
    if ($visualStudio -and (Test-NativeWindowsSdk)) {
        Write-Host "Update Visual Studio 2022 C++ tools and Windows SDK: $visualStudio"
        if (-not $Plan) {
            Ensure-WingetPackage (Get-NativeVisualStudioPackage) 'Visual Studio C++ tools update'
            if (-not (Get-NativeVisualStudio) -or -not (Test-NativeWindowsSdk)) {
                throw 'C++ tools/Windows SDK discovery failed after updating Visual Studio'
            }
        }
        return
    }
    if ($Plan) {
        Write-Host 'Install/complete Visual Studio 2022 Build Tools: C++ x64/x86 tools and Windows SDK (UAC/restart may be required).'
        return
    }
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path -LiteralPath $vswhere -PathType Leaf) {
        $existing = & $vswhere -latest -products '*' -version '[17.0,18.0)' -property installationPath
        if ($LASTEXITCODE -ne 0) { throw 'Visual Studio discovery failed' }
        if ($existing) {
            throw 'Complete the existing Visual Studio 2022 installation in Visual Studio Installer > Modify: Desktop development with C++, MSVC x64/x86 tools and a Windows SDK. Then rerun setup.'
        }
    }
    $override = '--wait --passive --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended'
    if ($StorageRoot) { $override += ' --installPath "' + (Join-Path $StorageRoot 'tools\vs2022') + '"' }
    Ensure-WingetPackage Microsoft.VisualStudio.2022.BuildTools 'C++ Build Tools installation' -Override $override
    if (-not (Get-NativeVisualStudio) -or -not (Test-NativeWindowsSdk)) {
        throw 'C++ tools/Windows SDK are incomplete. Restart if requested; use Visual Studio Installer > Modify to complete Desktop development with C++, then rerun setup.'
    }
}

function Get-NativeGameDirectories([string]$name) {
    switch ($name) {
        { $_ -in @('cmake', 'ctest') } { Join-Path $env:ProgramFiles 'CMake\bin' }
        { $_ -in @('clang', 'clang-format', 'clang-tidy', 'llvm-symbolizer') } { Join-Path $env:ProgramFiles 'LLVM\bin' }
        '7z' { Join-Path $env:ProgramFiles '7-Zip' }
        { $_ -in @('glslang', 'glslangValidator') } { Get-NativeShaderDirectories }
    }
}

function Get-NativeGameCommand([string]$name, [string[]]$directories = @()) {
    # Discovery must agree with the disk preflight and must not change PATH.
    $command = Get-NativeCommand $name
    foreach ($directory in $directories) {
        if ($command) { break }
        $candidate = Join-Path $directory ($name + '.exe')
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $command = $candidate
        }
    }
    return $command
}

function Ensure-NativeGameCommand([string]$name, [string]$packageId, [string[]]$directories = @(),
                                 [string]$versionArgument = '--version', [switch]$Plan) {
    $command = Get-NativeGameCommand $name $directories
    if (-not $Plan) {
        Ensure-WingetPackage $packageId "$name installation"
        Update-NativePath
        if ($packageId -eq 'KhronosGroup.VulkanSDK') { $directories += @(Get-NativeShaderDirectories) }
        $command = Get-NativeGameCommand $name $directories
        if (-not $command) { throw "$name is unavailable after $packageId installation; repair the package/PATH and rerun setup." }
    }
    if ($Plan) {
        Write-Host "Install/update $name via WinGet: $packageId"
        return
    }
    if (-not (Get-NativeCommand $name)) { Add-NativeUserPath (Split-Path -Parent $command) }
    & $command $versionArgument
    if ($LASTEXITCODE -ne 0) { throw "$name version check failed (exit code $LASTEXITCODE)" }
}

function Get-NativeShaderDirectories {
    foreach ($sdk in @($env:VULKAN_SDK, [Environment]::GetEnvironmentVariable('VULKAN_SDK', 'User'),
                       [Environment]::GetEnvironmentVariable('VULKAN_SDK', 'Machine')) | Select-Object -Unique) {
        if ($sdk) { Join-Path $sdk 'Bin' }
    }
}

function Test-NativeGameToolsPresent([switch]$GameDev, [switch]$ShaderTools) {
    if ($GameDev) {
        if (-not (Get-NativeVisualStudio) -or -not (Test-NativeWindowsSdk)) { return $false }
        foreach ($name in @('cmake', 'ctest', 'ninja', 'clang', 'clang-format', 'clang-tidy', 'llvm-symbolizer', '7z')) {
            if (-not (Get-NativeGameCommand $name @(Get-NativeGameDirectories $name))) { return $false }
        }
    }
    if ($ShaderTools -and -not (Get-NativeGameCommand glslangValidator @(Get-NativeShaderDirectories)) -and
        -not (Get-NativeGameCommand glslang @(Get-NativeShaderDirectories))) { return $false }
    return $true
}

function Assert-NativeGameSpace([string]$StorageRoot = '') {
    # This is a bootstrap floor, not an estimate of a project's build/cache size.
    $roots = @{}
    $roots[[IO.Path]::GetPathRoot($env:ProgramFiles)] = 15GB
    $roots[[IO.Path]::GetPathRoot($env:LOCALAPPDATA)] = 15GB
    $cacheRoot = if ($StorageRoot) { $StorageRoot } else { $env:LOCALAPPDATA }
    $roots[[IO.Path]::GetPathRoot($cacheRoot)] = 25GB
    foreach ($root in $roots.Keys) {
        $drive = Get-PSDrive -Name $root.Substring(0, 1)
        if ($drive.Free -lt $roots[$root]) {
            throw "Game tooling requires at least $($roots[$root] / 1GB) GiB free on $root before installation; allow additional space for project vcpkg builds and caches."
        }
    }
}

function Install-NativeGameTools([switch]$GameDev, [switch]$ShaderTools, [switch]$Plan, [string]$StorageRoot = '') {
    if ($GameDev) {
        Ensure-NativeCppTools -Plan:$Plan -StorageRoot $StorageRoot
        Ensure-NativeGameCommand cmake Kitware.CMake @(Get-NativeGameDirectories cmake) -Plan:$Plan
        Ensure-NativeGameCommand ctest Kitware.CMake @(Get-NativeGameDirectories ctest) -Plan:$Plan
        Ensure-NativeGameCommand ninja Ninja-build.Ninja -Plan:$Plan
        foreach ($name in @('clang', 'clang-format', 'clang-tidy', 'llvm-symbolizer')) {
            Ensure-NativeGameCommand $name LLVM.LLVM @(Get-NativeGameDirectories $name) -Plan:$Plan
        }
        Ensure-NativeGameCommand 7z 7zip.7zip @(Get-NativeGameDirectories 7z) -versionArgument 'i' -Plan:$Plan
    }
    if ($ShaderTools) {
        # VULKAN_SDK is set by the SDK installer for new processes.
        $name = if (Get-NativeGameCommand glslang @(Get-NativeShaderDirectories)) { 'glslang' } else { 'glslangValidator' }
        Ensure-NativeGameCommand $name KhronosGroup.VulkanSDK @(Get-NativeShaderDirectories) -Plan:$Plan
    }
}

function Enter-NativeGameDevEnvironment {
    $visualStudio = Get-NativeVisualStudio
    if (-not $visualStudio -or -not (Test-NativeWindowsSdk)) {
        throw 'Visual Studio 2022 C++ tools/Windows SDK are missing; apply setup with -GameDev first.'
    }
    $projectVcpkgRoot = [Environment]::GetEnvironmentVariable('VCPKG_ROOT', 'Process')
    # Vendor environment extensions call vswhere by name, even for custom VS paths.
    $installerBin = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer'
    if ($installerBin -notin @($env:PATH -split ';')) { $env:PATH = $installerBin + ';' + $env:PATH }
    try {
        & (Join-Path $visualStudio 'Common7\Tools\Launch-VsDevShell.ps1') -Arch amd64 -HostArch amd64 -SkipAutomaticLocation
    } finally {
        # VS injects its bundled vcpkg, which bypasses project-owned checkouts.
        [Environment]::SetEnvironmentVariable('VCPKG_ROOT', $projectVcpkgRoot, 'Process')
    }
    if (-not (Get-NativeCommand cl)) { throw 'MSVC developer environment did not provide cl.exe' }
    Write-Host 'MSVC x64 developer environment ready in this process. Select the project Node version before running npm.cmd.'
}
