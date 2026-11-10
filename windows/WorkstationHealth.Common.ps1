<# Read-only native workstation checks; no installer or authentication commands. #>
function Import-NativeHealthOutputType {
    if ('Basaltwater.HealthOutput' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.Threading.Tasks;
namespace Basaltwater {
    public static class HealthOutput {
        public static async Task<string> Drain(TextReader reader) {
            var text = new StringBuilder(512);
            var buffer = new char[4096];
            bool started = false, ended = false;
            int count;
            while ((count = await reader.ReadAsync(buffer, 0, buffer.Length).ConfigureAwait(false)) != 0) {
                for (int i = 0; i < count; i++) {
                    char c = buffer[i];
                    if (!started && Char.IsWhiteSpace(c)) continue;
                    started = true;
                    if (c == '\r' || c == '\n') ended = true;
                    if (!ended && text.Length < 512) text.Append(c);
                }
            }
            return text.ToString().TrimEnd();
        }
    }
}
'@
}

function Invoke-NativeHealthProbe {
    param([string]$Command,
          [ValidateSet('--version', 'version', 'i', '--headless --version')][string]$Arguments = '--version',
          [ValidateRange(1, 300)][int]$TimeoutSeconds = 30)
    if (-not $Command) {
        return [pscustomobject]@{ State = 'missing'; Command = $null; Detail = 'Not found; open a fresh terminal or reconcile setup.' }
    }
    $process = New-Object Diagnostics.Process
    $job = $null; $gate = $null; $started = $false
    $result = [pscustomobject]@{ State = 'failed'; Command = $Command; Detail = 'Version probe could not complete; check the executable and its process cleanup.' }
    $info = New-Object Diagnostics.ProcessStartInfo
    # Gate the wrapper before executing vendor code, so descendants inherit
    # containment even when the vendor's root exits before its pipes close.
    $gateName = 'Local\BasaltwaterHealth-' + [guid]::NewGuid().ToString('N')
    $literal = $Command.Replace("'", "''")
    $target = $literal; $targetArguments = $Arguments
    if ([IO.Path]::GetExtension($Command) -in @('.cmd', '.bat')) {
        # Expand the quoted path once from a child-only variable. This preserves
        # literal percent/exclamation text without CALL or delayed expansion.
        $info.EnvironmentVariables['BASALTWATER_HEALTH_COMMAND'] = $Command
        $target = (Join-Path $env:SystemRoot 'System32\cmd.exe').Replace("'", "''")
        $targetArguments = '/d /v:off /s /c ""%BASALTWATER_HEALTH_COMMAND%" ' + $Arguments + '"'
    }
    # Inherit raw output handles instead of PowerShell's line-buffered native
    # pipeline, which could itself buffer an arbitrarily long vendor line.
    $code = "`$ErrorActionPreference = 'Stop'; `$gate = [Threading.EventWaitHandle]::OpenExisting('$gateName'); try { if (-not `$gate.WaitOne(30000)) { exit 1 } } finally { `$gate.Dispose() }; " +
        "`$info = New-Object Diagnostics.ProcessStartInfo; `$info.FileName = '$target'; `$info.Arguments = '$targetArguments'; `$info.UseShellExecute = `$false; `$info.CreateNoWindow = `$true; `$info.RedirectStandardOutput = `$true; `$info.RedirectStandardError = `$true; " +
        '$probe = [Diagnostics.Process]::Start($info); try { $stdout = $probe.StandardOutput.BaseStream.CopyToAsync([Console]::OpenStandardOutput()); $stderr = $probe.StandardError.BaseStream.CopyToAsync([Console]::OpenStandardError()); $probe.WaitForExit(); if (-not $stdout.Wait(5000) -or -not $stderr.Wait(5000)) { exit 1 }; exit $probe.ExitCode } finally { $probe.Dispose() }'
    $info.FileName = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $info.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -OutputFormat Text -EncodedCommand ' +
        [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
    $info.EnvironmentVariables.Remove('PSModulePath')
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process.StartInfo = $info
    try {
        . (Join-Path $PSScriptRoot 'GitHubRunner.Common.ps1')
        Import-RunnerJobType
        Import-NativeHealthOutputType
        $gate = [Threading.EventWaitHandle]::new($false, 'ManualReset', $gateName)
        if (-not $process.Start()) { throw 'Could not start version probe' }
        $started = $true
        $job = [Basaltwater.ProcessJob]::new($process.Handle)
        $output = [Basaltwater.HealthOutput]::Drain($process.StandardOutput)
        # Discard stderr: vendor diagnostics can contain sensitive configuration.
        $errors = $process.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
        $null = $gate.Set()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $result.State = 'timed-out'; $result.Detail = "Version probe exceeded $TimeoutSeconds seconds."
        } else {
            if (-not $output.Wait(5000) -or -not $errors.Wait(5000)) { throw 'Version probe output did not close' }
            if ($process.ExitCode -ne 0) {
                $result.Detail = "Version probe exited with code $($process.ExitCode)."
            } else {
                $version = $output.GetAwaiter().GetResult()
                if (-not $version) { $version = 'Version probe exited successfully.' }
                $result.State = 'ok'; $result.Detail = $version
            }
        }
    } catch { $result.State = 'failed' }
    finally {
        try {
            if ($job) { $job.TerminateAndWait(5000) }
            elseif ($started -and -not $process.HasExited) {
                $process.Kill()
                if (-not $process.WaitForExit(5000)) { throw 'Probe wrapper did not stop' }
            }
        } catch {
            $result.State = 'failed'; $result.Detail = 'Version probe process cleanup could not be confirmed.'
        } finally {
            if ($job) { $job.Dispose() }
            if ($gate) { $gate.Dispose() }
            $process.Dispose()
        }
    }
    return $result
}

function Get-NativeT3DesktopHealth {
    param([string]$Command)
    $version = $null
    if ($Command) {
        try {
            $candidate = (Get-Item -LiteralPath $Command -ErrorAction Stop).VersionInfo.ProductVersion
            # Metadata is optional and untrusted; do not publish arbitrary text
            # or start Electron to obtain an installed version.
            if ($candidate -is [string] -and $candidate.Length -le 128 -and
                $candidate -cmatch '\A[0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\z') {
                $version = $candidate
            }
        } catch {
            # Keep executable discovery usable when version resources are absent
            # or unreadable, without exposing OS/vendor error details.
        }
    }
    $detail = if (-not $Command) { 'Desktop executable not found.' }
        elseif ($version) { "Installed desktop version: $version. The app was not launched; verify the running version in About and qualify provider/terminal readiness separately." }
        else { 'Desktop executable found; installed version unavailable. The app was not launched; verify the running version in About.' }
    return [pscustomobject]@{ Name = 't3-desktop'; State = $(if ($Command) { 'ok' } else { 'missing' });
        Command = $Command; InstalledVersion = $version; Detail = $detail }
}

function Get-NativeWorkstationHealth {
    param($Saved, [ValidateRange(1, 300)][int]$TimeoutSeconds = 30)
    $checks = New-Object 'Collections.Generic.List[object]'
    $complete = $Saved.Phase -eq 'complete'
    $checks.Add([pscustomobject]@{ Name = 'setup'; State = $(if ($complete) { 'ok' } else { 'failed' });
        Command = $null; Detail = "Saved setup phase: $($Saved.Phase)." })
    $selected = { param($Name) return $Saved.PSObject.Properties[$Name] -and [bool]$Saved.$Name }
    $tools = @('git', 'git-bash', 'gh', 'pwsh')
    if ($Saved.PSObject.Properties['AgentTool']) {
        foreach ($tool in $Saved.AgentTool) {
            if ($tool -notin @('gh', 'codex', 'claude', 'opencode')) { throw 'Unsupported provider in saved Windows setup' }
            $tools += $tool
        }
    }
    # Setup installs npm providers' runtime even without explicit Node selection.
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
        $readinessError=$null
        if($readiness -is [Collections.IDictionary]){$readinessError=$readiness['error']}
        elseif($readiness.PSObject.Properties['error']){$readinessError=$readiness.error}
        $checks.Add([pscustomobject]@{ Name = 'desktop-session'; State = $(if ($readiness.healthy) { 'ok' } else { 'failed' });
            Command = 'basaltw desktop doctor'; Detail = $(if ($readiness.healthy) {
                'Interactive desktop and Windows API readiness; no screenshot or input performed. Rendering, audio and streaming require qualification.'
            } else {
                $detail='Desktop readiness did not complete. Inspect desktop doctor in the intended login session.'
                if($readinessError -is [string] -and $readinessError){$detail+=' Reason: '+$readinessError.Substring(0,[Math]::Min(512,$readinessError.Length))}
                $detail
            }) })
        $desktop = Get-NativeT3Desktop
        $checks.Add((Get-NativeT3DesktopHealth $desktop))
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
