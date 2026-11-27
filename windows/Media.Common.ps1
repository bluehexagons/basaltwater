<# Optional AV executables; installation never selects Python implicitly. #>
function Get-NativeMediaCommand([ValidateSet('ffmpeg','ffprobe')][string]$Name) {
    $saved = [Environment]::GetEnvironmentVariable(('BASALTWATER_' + $Name.ToUpperInvariant() + '_EXE'), 'User')
    if ($saved -and (Test-Path -LiteralPath $saved -PathType Leaf)) { return $saved }
    $command = Get-Command ($Name + '.exe') -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }
    foreach ($root in @((Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'), (Join-Path $env:ProgramFiles 'WinGet\Packages'))) {
        foreach ($package in Get-ChildItem -LiteralPath $root -Filter 'Gyan.FFmpeg_*' -Directory -ErrorAction SilentlyContinue) {
            $file = Get-ChildItem -LiteralPath $package.FullName -Filter ($Name + '.exe') -File -Recurse | Select-Object -First 1
            if ($file) { return $file.FullName }
        }
    }
    return $null
}

function Set-NativeMediaExecutable([string]$Name, [string]$Command) {
    $variable = 'BASALTWATER_' + $Name.ToUpperInvariant() + '_EXE'
    [Environment]::SetEnvironmentVariable($variable, $Command, 'User')
    Set-Item -LiteralPath ('Env:' + $variable) -Value $Command
}

function Install-NativeAvTools([switch]$Plan) {
    $commands = @{}
    foreach ($name in @('ffmpeg','ffprobe')) { $commands[$name] = Get-NativeMediaCommand $name }
    if ($Plan) {
        Write-Host 'AV tools: preserve discovered FFmpeg/ffprobe; install missing tools via exact WinGet Gyan.FFmpeg. Python remains optional.'
        return
    }
    if (-not $commands.ffmpeg -or -not $commands.ffprobe) {
        Ensure-WingetPackage Gyan.FFmpeg 'FFmpeg/ffprobe installation'
        Update-NativePath
    }
    if (-not (Get-Command Invoke-NativeHealthProbe -ErrorAction SilentlyContinue)) {
        . (Join-Path $PSScriptRoot 'WorkstationHealth.Common.ps1')
    }
    foreach ($name in @('ffmpeg','ffprobe')) {
        # Retain each original executable even when the other was missing.
        if (-not $commands[$name]) { $commands[$name] = Get-NativeMediaCommand $name }
        $command = $commands[$name]
        if (-not $command) { throw "$name is unavailable after AV setup" }
        Assert-NativeSkillPath $command
        $probe = Invoke-NativeHealthProbe $command -Arguments '-version'
        if ($probe.State -ne 'ok') { throw "$name version probe failed: $($probe.Detail)" }
        Set-NativeMediaExecutable $name $command
    }
    Write-Host 'AV tools discovered. Preinstalled executables retain their existing update owner; manage Gyan.FFmpeg updates explicitly with WinGet.'
}

function Invoke-NativeMediaCli([string]$Source, [string[]]$Arguments) {
    if ($Arguments.Count -and $Arguments[0] -eq 'doctor') {
        if ($Arguments.Count -gt 2 -or ($Arguments.Count -eq 2 -and $Arguments[1] -ne '--json')) {
            return Write-NativeToolResult @{} $false 'invalid_input' 'Usage: basaltw media doctor [--json]'
        }
        . (Join-Path $Source 'windows\WorkstationHealth.Common.ps1')
        $checks = @(foreach ($name in @('ffmpeg','ffprobe')) {
            $check = Invoke-NativeHealthProbe (Get-NativeMediaCommand $name) -Arguments '-version'
            $check | Add-Member NoteProperty Name $name -PassThru
        })
        return Write-NativeToolResult @{ checks = $checks } (-not @($checks | Where-Object State -ne 'ok').Count) `
            'media_check_failed' 'FFmpeg or ffprobe did not pass its executable check.'
    }
    if ($Arguments.Count -and $Arguments[0] -in @('--help','-h','help')) {
        Write-Host 'Usage: basaltw media INPUT --output NEW_DIRECTORY [--video N] [--audio N] [--max-mib 128] [--max-seconds 120] [--timeout 180]'
        Write-Host 'Optional Python 3.11+ is required only for file inspection. basaltw media doctor probes FFmpeg/ffprobe without Python.'
        return 0
    }
    foreach ($name in @('ffmpeg','ffprobe')) {
        $command = Get-NativeMediaCommand $name
        if ($command) { Set-Item -LiteralPath ('Env:BASALTWATER_' + $name.ToUpperInvariant() + '_EXE') -Value $command }
    }
    $python = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $python -or $python.Source -like '*\Microsoft\WindowsApps\*') {
        throw 'File media inspection requires optional Python 3.11+. Select -Python during setup, or invoke a chosen interpreter with -m lib.media_cli from the checkout.'
    }
    # The absolute entrypoint owns module discovery; no global PYTHONPATH change.
    & $python.Source (Join-Path $Source 'scripts\inspect_media_windows.py') @Arguments | ForEach-Object { Write-Host $_ }
    return $LASTEXITCODE
}
