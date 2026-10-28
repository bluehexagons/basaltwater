<# Optional account-owned storage on a mounted native Windows volume. #>
function Resolve-NativeStorageRoot([string]$StorageRoot) {
    if ($StorageRoot -notmatch '\A[A-Za-z]:[\\/]' -or $StorageRoot -match '[\x00-\x1f<>"|?*]' -or
        ($StorageRoot.Length -ge 3 -and $StorageRoot.Substring(3).Contains(':'))) {
        throw 'StorageRoot must be an absolute local Windows drive path'
    }
    $root = [IO.Path]::GetFullPath($StorageRoot).TrimEnd('\')
    if ($root -eq [IO.Path]::GetPathRoot($root).TrimEnd('\')) { throw 'StorageRoot must name a directory below the drive root' }
    $parts = $root.Substring(3) -split '\\'
    if (@($parts | Where-Object { $_ -match '[:]|[. ]\z' -or $_ -match '\A(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|\z)' }).Count) {
        throw 'StorageRoot contains an invalid Windows directory name'
    }
    $volume = Get-Volume -DriveLetter $root.Substring(0, 1) -ErrorAction SilentlyContinue
    if (-not $volume -or $volume.FileSystem -ne 'NTFS' -or $volume.HealthStatus -ne 'Healthy') {
        throw 'StorageRoot requires a healthy mounted NTFS volume; setup does not format or mount disks'
    }
    $current = $root
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "StorageRoot must use ordinary directories, without junctions/symlinks: $current"
            }
        }
        $current = Split-Path -Parent $current
    }
    return $root
}

function Set-NativeStorageVariable([string]$name, [string]$path) {
    [Environment]::SetEnvironmentVariable($name, $path, 'User')
    [Environment]::SetEnvironmentVariable($name, $path, 'Process')
}

function Set-NativeDevelopmentStorage([string]$StorageRoot, [switch]$Plan) {
    $root = Resolve-NativeStorageRoot $StorageRoot
    $variables = [ordered]@{
        FNM_DIR = 'node'; UV_CACHE_DIR = 'cache\uv'; npm_config_cache = 'cache\npm'
        GOMODCACHE = 'cache\go-mod'; GOCACHE = 'cache\go-build'; VCPKG_DEFAULT_BINARY_CACHE = 'cache\vcpkg'
    }
    # Preflight every destination before creating directories or changing environment.
    foreach ($relative in @($variables.Values) + @('repos', 'tools\vs2022')) {
        Resolve-NativeStorageRoot (Join-Path $root $relative) | Out-Null
    }
    foreach ($name in $variables.Keys) {
        $path = Join-Path $root $variables[$name]
        Write-Host "Development storage: $name = $path"
        if (-not $Plan) {
            New-Item -ItemType Directory -Path $path -Force | Out-Null
            Set-NativeStorageVariable $name $path
        }
    }
    if (-not $Plan) { New-Item -ItemType Directory -Path (Join-Path $root 'repos') -Force | Out-Null }
    Write-Host "New checkouts may use $(Join-Path $root 'repos'). Existing checkouts/caches are not moved. Keep this drive connected."
}
