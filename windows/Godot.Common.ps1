<# Standard Godot host engine; project engine pins and export SDKs remain separate. #>
function Get-NativeGodotPackageCommand {
    # WinGet may extract portable packages without creating its CLI symlink.
    $roots = @((Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'),
               (Join-Path $env:ProgramFiles 'WinGet\Packages'))
    foreach ($root in $roots) {
        foreach ($package in Get-ChildItem -LiteralPath $root -Filter 'GodotEngine.GodotEngine_*' -Directory -ErrorAction SilentlyContinue) {
            $command = Get-ChildItem -LiteralPath $package.FullName -Filter 'Godot_v*_win64_console.exe' -File -Recurse |
                Sort-Object FullName | Select-Object -First 1
            if ($command) { return $command.FullName }
        }
    }
    return $null
}

function Set-NativeGodotExecutable([string]$command) {
    [Environment]::SetEnvironmentVariable('BASALTWATER_GODOT_EXE', $command, 'User')
    $env:BASALTWATER_GODOT_EXE = $command
}

function Invoke-NativeGodotVersion([string]$command, [ValidateRange(1, 300)][int]$TimeoutSeconds = 30) {
    # PowerShell 5.1 does not wait for GUI executables invoked with &.
    $process = Start-Process -FilePath $command -ArgumentList @('--headless', '--version') `
        -WindowStyle Hidden -PassThru
    try {
        $null = $process.Handle
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            & taskkill.exe /PID $process.Id /T /F | Out-Null
            if ($LASTEXITCODE -ne 0 -or -not $process.WaitForExit(5000)) {
                throw 'Godot version check timed out; process cleanup could not be confirmed'
            }
            throw 'Godot version check timed out'
        }
        if ($process.ExitCode -ne 0) { throw "Godot version check failed (exit code $($process.ExitCode))" }
    } finally { $process.Dispose() }
}

function Install-NativeGodot([switch]$Plan) {
    $bin = Join-Path $env:LOCALAPPDATA 'Basaltwater\bin'
    $launcher = Join-Path $bin 'godot.cmd'
    $command = Get-NativeCommand godot
    $managed = $command -eq $launcher
    if ($Plan) {
        Write-Host 'Install/update standard Godot via WinGet: GodotEngine.GodotEngine'
        return
    }
    Ensure-WingetPackage GodotEngine.GodotEngine 'Godot installation'
    Update-NativePath
    $command = Get-NativeCommand godot
    if ($command -eq $launcher) { $command = $null }
    if (-not $command) { $command = Get-NativeGodotPackageCommand }
    if (-not $command) { throw 'Godot is unavailable after installation; repair its WinGet package/PATH and rerun setup.' }
    Invoke-NativeGodotVersion $command
    if ($managed -or -not (Get-NativeCommand godot)) {
        Assert-NativeSkillPath $launcher
        New-Item -ItemType Directory -Path $bin -Force | Out-Null
        # cmd source is ASCII; use an environment variable for Unicode executable paths.
        Set-NativeGodotExecutable $command
        '@echo off', '"%BASALTWATER_GODOT_EXE%" %*', 'exit /b %errorlevel%' |
            Set-Content -LiteralPath $launcher -Encoding ASCII
        Add-NativeUserPath $bin
    } elseif ($env:BASALTWATER_GODOT_EXE -or [Environment]::GetEnvironmentVariable('BASALTWATER_GODOT_EXE', 'User')) {
        # A new PATH executable can supersede the portable package. Keep the
        # existing managed launcher and project consumers on the validated tool.
        Set-NativeGodotExecutable $command
    }
    Write-Host 'Godot ready. Match each project engine version; install matching export templates in the editor. C# requires Godot .NET and its SDK separately.'
}
