<# Read-only native workstation checks; no installer or authentication commands. #>
function Invoke-NativeHealthProbe {
    param([string]$Command,
          [ValidateSet('--version', 'version', 'i', '--headless --version')][string]$Arguments = '--version',
          [ValidateRange(1, 300)][int]$TimeoutSeconds = 30)
    if (-not $Command) {
        return [pscustomobject]@{ State = 'missing'; Command = $null; Detail = 'Not found; open a fresh terminal or reconcile setup.' }
    }
    $process = New-Object Diagnostics.Process
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $Command
    $info.Arguments = $Arguments
    if ([IO.Path]::GetExtension($Command) -in @('.cmd', '.bat')) {
        # A literal PowerShell string preserves spaces, Unicode and punctuation
        # without interpolating a discovered path into cmd.exe syntax.
        $literal = $Command.Replace("'", "''")
        $code = "& '$literal' $Arguments; exit " + '$LASTEXITCODE'
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
        $info.FileName = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $info.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' + $encoded
        $info.EnvironmentVariables.Remove('PSModulePath')
    }
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process.StartInfo = $info
    try {
        if (-not $process.Start()) { throw 'Could not start version probe' }
        $output = $process.StandardOutput.ReadToEndAsync()
        # Discard stderr: vendor diagnostics can contain sensitive configuration.
        $errors = $process.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0 -or -not $process.WaitForExit(5000)) {
                throw 'Version probe timed out; process cleanup could not be confirmed'
            }
            return [pscustomobject]@{ State = 'timed-out'; Command = $Command; Detail = "Version probe exceeded $TimeoutSeconds seconds." }
        }
        if (-not $output.Wait(5000) -or -not $errors.Wait(5000)) { throw 'Version probe output did not close' }
        if ($process.ExitCode -ne 0) {
            return [pscustomobject]@{ State = 'failed'; Command = $Command; Detail = "Version probe exited with code $($process.ExitCode)." }
        }
        $version = ($output.GetAwaiter().GetResult().Trim() -split '\r?\n')[0]
        if ($version.Length -gt 512) { $version = $version.Substring(0, 512) }
        if (-not $version) { $version = 'Version probe exited successfully.' }
        return [pscustomobject]@{ State = 'ok'; Command = $Command; Detail = $version }
    } catch {
        return [pscustomobject]@{ State = 'failed'; Command = $Command; Detail = 'Version probe could not complete; check the executable and its process cleanup.' }
    } finally { $process.Dispose() }
}

function Get-NativeWorkstationHealth {
    param($Saved, [ValidateRange(1, 300)][int]$TimeoutSeconds = 30)
    $checks = New-Object 'Collections.Generic.List[object]'
    $complete = $Saved.Phase -eq 'complete'
    $checks.Add([pscustomobject]@{ Name = 'setup'; State = $(if ($complete) { 'ok' } else { 'failed' });
        Command = $null; Detail = "Saved setup phase: $($Saved.Phase)." })
    $selected = { param($Name) return $Saved.PSObject.Properties[$Name] -and [bool]$Saved.$Name }
    $tools = @('git', 'git-bash', 'gh', 'codex', 'pwsh')
    if ($Saved.PSObject.Properties['AgentTool']) {
        foreach ($tool in $Saved.AgentTool) {
            if ($tool -notin @('gh', 'codex', 'claude', 'opencode')) { throw 'Unsupported provider in saved Windows setup' }
            $tools += $tool
        }
    }
    # Codex is part of every native profile and setup installs npm providers
    # even when the explicit Node selection is false or absent in older state.
    if ((& $selected 'Node') -or 'codex' -in $tools -or 'opencode' -in $tools) { $tools += @('node', 'npm') }
    if ((& $selected 'Python') -or (& $selected 'GameDev')) { $tools += @('uv', 'python') }
    if ((& $selected 'Go') -or (& $selected 'GameDev')) { $tools += 'go' }
    if ((& $selected 'NodeVersions') -or (& $selected 'GameDev')) { $tools += 'fnm' }
    if (& $selected 'GameDev') {
        $cpp = $null; $cppState = 'missing'
        $cppDetail = 'Visual Studio 2022 C++ tools and Windows SDK discovery; compile/build qualification is separate.'
        try {
            $cpp = Get-NativeVisualStudio
            if ($cpp -and (Test-NativeWindowsSdk)) { $cppState = 'ok' }
        } catch { $cppState = 'failed'; $cppDetail = 'Visual Studio/Windows SDK discovery failed; inspect the vendor installation.' }
        $checks.Add([pscustomobject]@{ Name = 'cpp'; State = $cppState; Command = $cpp; Detail = $cppDetail })
        $tools += @('cmake', 'ctest', 'ninja', 'clang', 'clang-format', 'clang-tidy', 'llvm-symbolizer', '7z')
    }
    if (& $selected 'ShaderTools') {
        $shader = Get-NativeGameCommand glslang @(Get-NativeShaderDirectories)
        $shaderName = if ($shader) { 'glslang' } else { 'glslangValidator' }
        $tools += $shaderName
    }
    if (& $selected 'Godot') { $tools += 'godot' }
    foreach ($name in $tools | Select-Object -Unique) {
        $command = Get-NativeCommand $name
        if ($name -eq 'git-bash') { $command = Get-NativeGitBash }
        if ($name -eq 'pwsh') { $command = Get-NativePowerShell7 }
        if ($name -eq 'godot' -and (-not $command -or
            $command -eq (Join-Path $env:LOCALAPPDATA 'Basaltwater\bin\godot.cmd'))) {
            $command = Get-NativeGodotPackageCommand
        } elseif ($name -in @('cmake', 'ctest', 'ninja', 'clang', 'clang-format', 'clang-tidy', 'llvm-symbolizer', '7z', 'glslang', 'glslangValidator')) {
            $command = Get-NativeGameCommand $name @(Get-NativeGameDirectories $name)
        }
        $arguments = switch ($name) { 'go' { 'version' }; '7z' { 'i' }; 'godot' { '--headless --version' }; default { '--version' } }
        $check = Invoke-NativeHealthProbe $command -Arguments $arguments -TimeoutSeconds $TimeoutSeconds
        if ($name -eq 'python' -and $command -like '*\Microsoft\WindowsApps\*' -and $check.State -ne 'ok') {
            $check.Detail += ' Microsoft Store alias did not provide a usable Python; use the project uv environment or adjust App execution aliases.'
        }
        $check | Add-Member -NotePropertyName Name -NotePropertyValue $name
        $checks.Add($check)
    }
    if (& $selected 'T3CodeDesktop') {
        $readiness = Get-NativeDesktopReadiness
        $checks.Add([pscustomobject]@{ Name = 'desktop-session'; State = $(if ($readiness.healthy) { 'ok' } else { 'failed' });
            Command = 'basaltw desktop doctor'; Detail = $(if ($readiness.healthy) {
                'Interactive desktop and Windows API readiness; no screenshot or input performed. Rendering, audio and streaming require qualification.'
            } else { 'Interactive desktop is unavailable, locked or inaccessible. Inspect desktop doctor in the intended login session.' }) })
        $desktop = Get-NativeT3Desktop
        $checks.Add([pscustomobject]@{ Name = 't3-desktop'; State = $(if ($desktop) { 'ok' } else { 'missing' });
            Command = $desktop; Detail = 'Desktop executable discovery only; the app was not launched.' })
        $checks.Add((Get-NativeT3LoginHealth $Saved.Source))
    }
    if (& $selected 'Sunshine') {
        $sunshine = Get-NativeSunshineHealth
        $checks.Add([pscustomobject]@{ Name = 'sunshine'; State = $sunshine.State; Command = 'SunshineService'; Detail = $sunshine.Detail })
    }
    if ($Saved.PSObject.Properties['StorageRoot'] -and $Saved.StorageRoot) {
        $storageState = 'ok'; $detail = 'Configured storage volume and managed directory paths are valid.'
        try { Assert-NativeDevelopmentStorage $Saved.StorageRoot }
        catch { $storageState = 'failed'; $detail = 'Storage directories or saved cache variables are missing, changed or unsafe; check the mounted NTFS volume and rerun setup.' }
        $checks.Add([pscustomobject]@{ Name = 'storage'; State = $storageState; Command = $Saved.StorageRoot; Detail = $detail })
    }
    return [pscustomobject]@{ Schema = 1; Platform = 'windows'; Profile = $Saved.Profile;
        Revision = $Saved.Revision; Phase = $Saved.Phase;
        Healthy = @($checks | Where-Object { $_.State -ne 'ok' }).Count -eq 0; Checks = @($checks.ToArray()) }
}
