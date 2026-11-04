<# Offline doctor tests; executable fixtures and state live only in a temporary directory. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
$windows = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $windows 'WorkstationHealth.Common.ps1')
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-health-' + [guid]::NewGuid().ToString('N'))
$savedMode = $env:BASALTWATER_HEALTH_FIXTURE_MODE
$savedPid = $env:BASALTWATER_HEALTH_FIXTURE_PID
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $batch = Join-Path $temporary ('version & ' + [char]0x03bb + "'s.cmd")
    Set-Content -LiteralPath $batch -Encoding ASCII -Value '@echo off', 'echo batch-version', 'exit /b 0'
    $result = Invoke-NativeHealthProbe $batch
    Assert-True ($result.State -eq 'ok' -and $result.Detail -eq 'batch-version') 'Batch/Unicode/punctuation version probe failed'
    Assert-True ((Invoke-NativeHealthProbe '').State -eq 'missing') 'Missing tool was reported healthy'
    Assert-True ((Invoke-NativeHealthProbe (Join-Path $temporary 'missing.exe')).State -eq 'failed') 'Failed process start was reported healthy'

    $fixture = Join-Path $temporary 'health fixture.exe'
    Add-Type -OutputAssembly $fixture -OutputType ConsoleApplication -TypeDefinition @'
using System;
using System.IO;
using System.Threading;
[assembly: System.Reflection.AssemblyInformationalVersion("0.1.7+fixture")]
public class HealthProbeFixture {
    public static int Main(string[] args) {
        string mode = Environment.GetEnvironmentVariable("BASALTWATER_HEALTH_FIXTURE_MODE");
        if (mode == "fail") { Console.Error.WriteLine("private vendor diagnostic"); return 23; }
        if (mode == "hang") {
            File.WriteAllText(Environment.GetEnvironmentVariable("BASALTWATER_HEALTH_FIXTURE_PID"),
                System.Diagnostics.Process.GetCurrentProcess().Id.ToString());
            Thread.Sleep(30000);
        }
        Console.WriteLine(String.Join(" ", args));
        return 0;
    }
}
'@
    $result = Get-NativeT3DesktopHealth $fixture
    Assert-True ($result.InstalledVersion -eq '0.1.7+fixture') 'Desktop health did not read executable product-version metadata'
    $env:BASALTWATER_HEALTH_FIXTURE_MODE = ''
    $result = Invoke-NativeHealthProbe $fixture -Arguments '--headless --version'
    Assert-True ($result.State -eq 'ok' -and $result.Detail -eq '--headless --version') 'Version arguments changed across the native boundary'
    $env:BASALTWATER_HEALTH_FIXTURE_MODE = 'fail'
    $result = Invoke-NativeHealthProbe $fixture
    Assert-True ($result.State -eq 'failed' -and $result.Detail -match '23' -and
        ($result | ConvertTo-Json) -notmatch 'private vendor diagnostic') 'Failed probe lost its exit code or exposed stderr'
    $env:BASALTWATER_HEALTH_FIXTURE_MODE = 'hang'
    $env:BASALTWATER_HEALTH_FIXTURE_PID = Join-Path $temporary 'probe.pid'
    $result = Invoke-NativeHealthProbe $fixture -TimeoutSeconds 1
    Assert-True ($result.State -eq 'timed-out') 'Hanging version probe did not time out'
    $probeId = [int][IO.File]::ReadAllText($env:BASALTWATER_HEALTH_FIXTURE_PID)
    Assert-True (-not (Get-Process -Id $probeId -ErrorAction SilentlyContinue)) 'Timed-out version process survived cleanup'

    $gui = Join-Path $temporary 'health GUI.exe'
    Add-Type -OutputAssembly $gui -OutputType WindowsApplication -TypeDefinition @'
public class HealthGuiFixture {
    public static int Main() { System.Threading.Thread.Sleep(250); return 19; }
}
'@
    $result = Invoke-NativeHealthProbe $gui
    Assert-True ($result.State -eq 'failed' -and $result.Detail -match '19') 'GUI version probe did not wait for its exit code'

    & {
        # Discovery and system interactions are mocked; only real probe lifecycle
        # is exercised above. Any attempt to apply setup fails this test.
        function Ensure-WingetPackage { throw 'Doctor tried to install a package' }
        function Add-NativeUserPath { throw 'Doctor tried to change PATH' }
        function Set-NativeGodotExecutable { throw 'Doctor tried to persist Godot discovery' }
        function Get-NativeCommand($Name) {
            if ($Name -eq 'npm' -and $script:MissingNpm) { return $null }
            if ($Name -eq 'go' -and $script:MissingGo) { return $null }
            if ($Name -eq 'python' -and $script:StorePython) { return 'C:\Users\fixture\AppData\Local\Microsoft\WindowsApps\python.exe' }
            if ($Name -eq 'godot') { return Join-Path $env:LOCALAPPDATA 'Basaltwater\bin\godot.cmd' }
            return 'C:\fixture\' + $Name + '.exe'
        }
        function Get-NativeGameDirectories($Name) { return @('C:\vendor') }
        function Get-NativeShaderDirectories { return @('C:\shader') }
        function Get-NativeGameCommand($Name, $Directories) { return 'C:\vendor\' + $Name + '.exe' }
        function Get-NativeGodotPackageCommand { return 'C:\package\Godot_console.exe' }
        function Get-NativeVisualStudio { return 'C:\VS2022' }
        function Test-NativeWindowsSdk { return $true }
        function Get-NativeT3Desktop { if (-not $script:MissingT3) { return 'C:\T3 Code.exe' } }
        function Get-Item($LiteralPath, $ErrorAction) {
            Assert-True ($LiteralPath -eq 'C:\T3 Code.exe') 'Unexpected desktop metadata path'
            $script:T3MetadataReads++
            if ($script:T3MetadataFailure) { throw 'private metadata failure' }
            if ($script:T3MetadataAbsent) { return [pscustomobject]@{} }
            return [pscustomobject]@{ VersionInfo = [pscustomobject]@{ ProductVersion = $script:T3Version } }
        }
        function Get-NativeGitBash { if (-not $script:MissingGitBash) { return 'C:\Git\bin\bash.exe' } }
        function Get-NativePowerShell7 { if (-not $script:MissingPowerShell) { return 'C:\fixture\pwsh.exe' } }
        function Get-NativeDesktopReadiness {
            $value=@{healthy=(-not $script:LockedDesktop)}
            if($script:DesktopFailure){$value.error=$script:DesktopFailure}
            if($script:ReadinessObject){return [pscustomobject]$value}
            return $value
        }
        function Get-NativeSunshineHealth {
            return [pscustomobject]@{ State = $(if ($script:BadSunshine) { 'failed' } else { 'ok' }); Detail = 'fixture service' }
        }
        function Get-NativeT3LoginHealth($Source) {
            $state = if ($script:BadLogin) { 'failed' } else { 'ok' }
            return [pscustomobject]@{ Name = 't3-login'; State = $state; Command = $null; Detail = 'fixture task' }
        }
        function Set-NativeDevelopmentStorage { throw 'Doctor applied storage configuration' }
        function Assert-NativeDevelopmentStorage($StorageRoot) {
            if ($script:BadStorage) { throw 'unavailable volume' }
        }
        function Invoke-NativeHealthProbe($Command, $Arguments, $TimeoutSeconds) {
            $script:Probes += [pscustomobject]@{ Command = $Command; Arguments = $Arguments }
            $state = if (-not $Command) { 'missing' } elseif (($Command -like '*codex*' -and $script:BrokenCodex) -or
                ($Command -like '*WindowsApps*' -and $script:BrokenPython)) { 'failed' } else { 'ok' }
            return [pscustomobject]@{ State = $state; Command = $Command; Detail = 'fixture version' }
        }
        $saved = [pscustomobject]@{ Profile = 'agent_windows'; Revision = 'a' * 40; Phase = 'complete'; Source = 'C:\source';
            AgentTool = @('gh', 'codex', 'claude', 'opencode'); Node = $true; GameDev = $true;
            ShaderTools = $true; Godot = $true; Sunshine = $true; T3CodeDesktop = $true; StorageRoot = 'F:\coding'; Secret = 'never-publish' }
        $script:Probes = @(); $script:MissingGo = $false; $script:StorePython = $false; $script:MissingNpm = $false
        $script:BadStorage = $false; $script:BrokenCodex = $false
        $script:BrokenPython = $false; $script:BadLogin = $false; $script:BadSunshine = $false
        $script:MissingPowerShell = $false; $script:LockedDesktop = $false; $script:MissingGitBash = $false
        $script:DesktopFailure=$null;$script:ReadinessObject=$false
        $script:MissingT3=$false;$script:T3MetadataFailure=$false;$script:T3MetadataAbsent=$false
        $script:T3MetadataReads=0;$script:T3Version='0.1.7'
        $report = Get-NativeWorkstationHealth $saved -TimeoutSeconds 1
        Assert-True $report.Healthy 'Complete healthy selection did not pass'
        $desktop = @($report.Checks | Where-Object Name -eq 't3-desktop')[0]
        Assert-True ($desktop.InstalledVersion -eq '0.1.7' -and $desktop.Detail -match 'running version') 'Installed desktop version was not distinguished from the running app'
        Assert-True (@($script:Probes | Where-Object { $_.Command -like '*T3 Code.exe' }).Count -eq 0) 'Doctor launched the desktop to determine its version'
        foreach ($version in @('0.1.8-rc.1+fixture', '0.1.7.0', $null, '', "private`nmetadata", ('1' * 129))) {
            $script:T3Version = $version
            $report = Get-NativeWorkstationHealth $saved
            $desktop = @($report.Checks | Where-Object Name -eq 't3-desktop')[0]
            if ($version -in @('0.1.8-rc.1+fixture', '0.1.7.0')) {
                Assert-True ($desktop.InstalledVersion -eq $version) 'Valid desktop version metadata was lost'
            } else {
                Assert-True ($null -eq $desktop.InstalledVersion -and $desktop.Detail -match 'unavailable' -and
                    ($desktop | ConvertTo-Json) -notmatch 'private|metadata') 'Invalid or absent version metadata was published'
            }
            Assert-True $report.Healthy 'Optional version metadata changed executable discovery health'
        }
        $script:T3Version = '0.1.7'
        foreach ($failure in @('T3MetadataAbsent', 'T3MetadataFailure')) {
            Set-Variable -Name $failure -Scope Script -Value $true
            $report = Get-NativeWorkstationHealth $saved
            $desktop = @($report.Checks | Where-Object Name -eq 't3-desktop')[0]
            Assert-True ($null -eq $desktop.InstalledVersion -and $desktop.Detail -match 'unavailable' -and
                ($desktop | ConvertTo-Json) -notmatch 'private') 'Unreadable version metadata leaked diagnostics or became a version'
            Set-Variable -Name $failure -Scope Script -Value $false
        }
        $reads = $script:T3MetadataReads
        $script:MissingT3 = $true
        $report = Get-NativeWorkstationHealth $saved
        $desktop = @($report.Checks | Where-Object Name -eq 't3-desktop')[0]
        Assert-True ($desktop.State -eq 'missing' -and $null -eq $desktop.InstalledVersion -and
            $script:T3MetadataReads -eq $reads -and -not $report.Healthy) 'Missing desktop was probed or reported healthy'
        $script:MissingT3 = $false; $script:T3Version = '0.1.7'; $script:Probes = @()
        $report = Get-NativeWorkstationHealth $saved -TimeoutSeconds 1
        Assert-True (($report | ConvertTo-Json -Depth 5) -notmatch 'never-publish|Secret') 'Doctor exposed arbitrary saved fields'
        Assert-True (@($script:Probes | Where-Object { $_.Command -like '*\codex.exe' }).Count -eq 1) 'Provider was probed more than once'
        Assert-True (@($script:Probes | Where-Object { $_.Command -like '*Godot_console.exe' -and $_.Arguments -eq '--headless --version' }).Count -eq 1) 'Doctor did not resolve the managed Godot launcher to its executable'
        Assert-True (@($script:Probes | Where-Object { $_.Command -like '*\go.exe' -and $_.Arguments -eq 'version' }).Count -eq 1) 'Game bundle dependencies or Go arguments were lost'
        $script:MissingGitBash = $true
        $report = Get-NativeWorkstationHealth $saved
        Assert-True (-not $report.Healthy -and @($report.Checks | Where-Object { $_.Name -eq 'git-bash' -and $_.State -eq 'missing' }).Count -eq 1) 'Missing Git Bash was reported healthy'
        $script:MissingGitBash = $false
        $script:MissingPowerShell = $true
        $report = Get-NativeWorkstationHealth $saved
        Assert-True (-not $report.Healthy -and @($report.Checks | Where-Object { $_.Name -eq 'pwsh' -and $_.State -eq 'missing' }).Count -eq 1) 'Missing stable PowerShell 7 was reported healthy'
        $script:MissingPowerShell = $false; $script:LockedDesktop = $true
        $report = Get-NativeWorkstationHealth $saved
        Assert-True (-not $report.Healthy -and @($report.Checks | Where-Object { $_.Name -eq 'desktop-session' -and $_.State -eq 'failed' }).Count -eq 1) 'Locked desktop readiness was reported healthy'
        foreach($asObject in @($false,$true)){
            $script:ReadinessObject=$asObject;$script:DesktopFailure='Desktop controller busy; '+('x'*600)
            $report=Get-NativeWorkstationHealth $saved
            $desktop=@($report.Checks|Where-Object Name -eq 'desktop-session')[0]
            Assert-True (-not $report.Healthy -and $desktop.Detail -match 'controller busy' -and
                $desktop.Detail.Length -lt 700 -and $desktop.Detail -notmatch 'locked') 'Doctor hid or failed to bound the actual busy reason'
        }
        $script:DesktopFailure=$null;$script:ReadinessObject=$false
        $script:LockedDesktop = $false
        $script:StorePython = $true
        Assert-True (Get-NativeWorkstationHealth $saved).Healthy 'Working Store Python was rejected solely by its path'
        $script:BadLogin = $true
        Assert-True (-not (Get-NativeWorkstationHealth $saved).Healthy) 'Broken login task was reported healthy'
        $script:BadLogin = $false
        $script:BadSunshine = $true
        Assert-True (-not (Get-NativeWorkstationHealth $saved).Healthy) 'Selected Sunshine service failure was reported healthy'
        $script:BadSunshine = $false
        $script:BrokenCodex = $true; $script:MissingGo = $true; $script:BrokenPython = $true; $script:BadStorage = $true
        $report = Get-NativeWorkstationHealth $saved
        Assert-True (-not $report.Healthy -and @($report.Checks | Where-Object { $_.State -ne 'ok' }).Count -eq 4) 'Doctor stopped early or lost independent failures'
        $saved.Phase = 'staged'
        Assert-True (-not (Get-NativeWorkstationHealth $saved).Healthy) 'Incomplete setup was reported healthy'
        $minimal = [pscustomobject]@{ Profile = 'agent_windows'; Revision = 'b' * 40; Phase = 'complete' }
        $script:Probes = @(); $script:BrokenCodex = $false
        Assert-True (Get-NativeWorkstationHealth $minimal).Healthy 'Legacy state without optional selections failed'
        Assert-True ($script:Probes.Count -eq 7) 'Default Git Bash/provider runtime dependencies were omitted or optional runtimes required'
        $minimal | Add-Member NoteProperty Node $false
        $minimal | Add-Member NoteProperty AgentTool @('gh', 'codex')
        $script:MissingNpm = $true
        $report = Get-NativeWorkstationHealth $minimal
        Assert-True (-not $report.Healthy -and @($report.Checks | Where-Object { $_.Name -eq 'npm' -and $_.State -eq 'missing' }).Count -eq 1) 'Implicit npm dependency failure was reported healthy'
    }
    Write-Host 'Native workstation doctor tests passed.'
} finally {
    if ($env:BASALTWATER_HEALTH_FIXTURE_PID -and (Test-Path -LiteralPath $env:BASALTWATER_HEALTH_FIXTURE_PID)) {
        $probeId = [int][IO.File]::ReadAllText($env:BASALTWATER_HEALTH_FIXTURE_PID)
        $owned = Get-Process -Id $probeId -ErrorAction SilentlyContinue
        if ($owned -and $owned.ProcessName -eq 'health fixture') { $owned.Kill(); $owned.WaitForExit() }
    }
    $env:BASALTWATER_HEALTH_FIXTURE_MODE = $savedMode
    $env:BASALTWATER_HEALTH_FIXTURE_PID = $savedPid
    $resolved = [IO.Path]::GetFullPath($temporary)
    $root = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe doctor test cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
