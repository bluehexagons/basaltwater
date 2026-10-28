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
        function Get-NativeT3Desktop { return 'C:\T3 Code.exe' }
        function Set-NativeDevelopmentStorage($StorageRoot, [switch]$Plan) {
            Assert-True $Plan 'Doctor applied storage configuration'
            if ($script:BadStorage) { throw 'unavailable volume' }
        }
        function Invoke-NativeHealthProbe($Command, $Arguments, $TimeoutSeconds) {
            $script:Probes += [pscustomobject]@{ Command = $Command; Arguments = $Arguments }
            $state = if (-not $Command) { 'missing' } elseif (($Command -like '*codex*' -and $script:BrokenCodex) -or
                ($Command -like '*WindowsApps*' -and $script:BrokenPython)) { 'failed' } else { 'ok' }
            return [pscustomobject]@{ State = $state; Command = $Command; Detail = 'fixture version' }
        }
        $saved = [pscustomobject]@{ Profile = 'agent_windows'; Revision = 'a' * 40; Phase = 'complete';
            AgentTool = @('gh', 'codex', 'claude', 'opencode'); Node = $true; GameDev = $true;
            ShaderTools = $true; Godot = $true; T3CodeDesktop = $true; StorageRoot = 'F:\coding'; Secret = 'never-publish' }
        $script:Probes = @(); $script:MissingGo = $false; $script:StorePython = $false
        $script:BadStorage = $false; $script:BrokenCodex = $false
        $script:BrokenPython = $false
        $report = Get-NativeWorkstationHealth $saved -TimeoutSeconds 1
        Assert-True $report.Healthy 'Complete healthy selection did not pass'
        Assert-True (($report | ConvertTo-Json -Depth 5) -notmatch 'never-publish|Secret') 'Doctor exposed arbitrary saved fields'
        Assert-True (@($script:Probes | Where-Object { $_.Command -like '*\codex.exe' }).Count -eq 1) 'Provider was probed more than once'
        Assert-True (@($script:Probes | Where-Object { $_.Command -like '*Godot_console.exe' -and $_.Arguments -eq '--headless --version' }).Count -eq 1) 'Doctor did not resolve the managed Godot launcher to its executable'
        Assert-True (@($script:Probes | Where-Object { $_.Command -like '*\go.exe' -and $_.Arguments -eq 'version' }).Count -eq 1) 'Game bundle dependencies or Go arguments were lost'
        $script:StorePython = $true
        Assert-True (Get-NativeWorkstationHealth $saved).Healthy 'Working Store Python was rejected solely by its path'
        $script:BrokenCodex = $true; $script:MissingGo = $true; $script:BrokenPython = $true; $script:BadStorage = $true
        $report = Get-NativeWorkstationHealth $saved
        Assert-True (-not $report.Healthy -and @($report.Checks | Where-Object { $_.State -ne 'ok' }).Count -eq 4) 'Doctor stopped early or lost independent failures'
        $saved.Phase = 'staged'
        Assert-True (-not (Get-NativeWorkstationHealth $saved).Healthy) 'Incomplete setup was reported healthy'
        $minimal = [pscustomobject]@{ Profile = 'agent_windows'; Revision = 'b' * 40; Phase = 'complete' }
        $script:Probes = @(); $script:BrokenCodex = $false
        Assert-True (Get-NativeWorkstationHealth $minimal).Healthy 'Legacy state without optional selections failed'
        Assert-True ($script:Probes.Count -eq 3) 'Unselected runtimes were required'
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
