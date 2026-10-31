<# Real owned process trees in a temporary store; Scheduler, notifications and sleep requests mocked. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
$windows = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $windows 'PowerShell.Common.ps1')
. (Join-Path $windows 'Development.Common.ps1')
$shell = Get-NativePowerShell7
if (-not $shell) { throw 'Process qualification requires PowerShell 7; run native setup first' }
$python = Get-Command python.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1
if ($python.Source -like '*\WindowsApps\*') { throw 'Process qualification requires real Python, not a Store alias' }
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-process-' + [guid]::NewGuid().ToString('N'))
$savedLocal = $env:LOCALAPPDATA
$guardian = $null
$unrelated = $null
$passed = $false
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $unrelatedInfo = [Diagnostics.ProcessStartInfo]::new()
    $unrelatedInfo.FileName = $python.Source; $unrelatedInfo.Arguments = '-c "import time; time.sleep(300)"'
    $unrelatedInfo.UseShellExecute = $false; $unrelatedInfo.CreateNoWindow = $true
    $unrelated = [Diagnostics.Process]::Start($unrelatedInfo)
    $env:LOCALAPPDATA = Join-Path $temporary 'account'
    $fixtureSource = Join-Path $temporary 'worker source'
    New-Item -ItemType Directory -Path $fixtureSource | Out-Null
    foreach ($file in @('Development.Common.ps1','Development.Process.ps1','Invoke-DevelopmentTask.ps1','GitHubRunner.Common.ps1','ShellIntegration.Common.ps1')) {
        Copy-Item -LiteralPath (Join-Path $windows $file) -Destination (Join-Path $fixtureSource $file)
    }
    $processTypes = Join-Path $fixtureSource 'Development.Process.ps1'
    $types = Get-Content -LiteralPath $processTypes -Raw
    $types = [regex]::Replace($types, '(?s)public static void Awake\(bool active\) \{.*?\n        \}', 'public static void Awake(bool active) { }')
    Set-Content -LiteralPath $processTypes -Value $types -Encoding UTF8
    $project = Join-Path $temporary ('project ' + [char]0x03bb)
    New-Item -ItemType Directory -Path $project | Out-Null
    $fixture = Join-Path $project 'task.py'
    Set-Content -LiteralPath $fixture -Encoding UTF8 -Value @'
from __future__ import annotations
import json, os, pathlib, subprocess, sys, time
mode, child_path = sys.argv[1:3]
if mode in ('flood', 'failed-flood', 'paused-flood'):
    for stream in (sys.stdout.buffer, sys.stderr.buffer):
        head, tail = b'BEGIN-OF-PROBE\n', b'\nEND-OF-PROBE\n'
        stream.write(head + b'z' * (4 * 1024 * 1024 + 12345 - len(head) - len(tail)) + tail)
        stream.flush()
    if mode == 'paused-flood':
        while not pathlib.Path(child_path + '.release').exists():
            time.sleep(0.01)
    sys.exit(23 if mode == 'failed-flood' else 0)
if mode in ('cancel', 'timeout', 'orphan', 'crash'):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    marker = pathlib.Path(child_path + '.tmp')
    marker.write_text(str(child.pid), encoding='utf-8')
    os.replace(marker, child_path)
    time.sleep(1 if mode == 'orphan' else 60)
print(json.dumps({'argv': sys.argv[3:], 'date': os.environ.get('TASK_LITERAL_DATE')}))
sys.exit(23 if mode == 'fail' else 0)
'@
    foreach ($mode in @('success','fail','flood','failed-flood','paused-flood','timeout','cancel','orphan','cleanup-fail','crash')) {
        $id = [guid]::NewGuid().ToString('N')
        $directory = Get-DevelopmentDirectory $id
        Initialize-DevelopmentDirectory $directory
        $childFile = Join-Path $directory 'child.pid'
        $literal = @('two words', '', 'quote"here', "lambda $([char]0x03bb)", 'trailing\', '$literal & ;')
        $environment = @{}
        foreach ($name in @('SystemRoot','SystemDrive','ComSpec','PATH','PATHEXT','TEMP','TMP','USERPROFILE')) { $environment[$name] = [Environment]::GetEnvironmentVariable($name) }
        $environment.LOCALAPPDATA = $env:LOCALAPPDATA
        $environment.TASK_LITERAL_DATE = '2026-10-29T12:34:56Z'
        $request = [pscustomobject]@{ Project = $project; Executable = $python.Source;
            Argv = @($fixture, $mode, $childFile) + $literal; Environment = $environment; Msvc = $false;
            # Include startup/compilation headroom so timeout exercises a live native tree.
            PowerShell = $shell; TimeoutSeconds = $(if ($mode -eq 'timeout') { 10 } else { 30 }) }
        $record = [pscustomobject]@{ Id = $id; OwnerSid = (Get-DevelopmentSid); SessionId = (Get-Process -Id $PID).SessionId;
            WorkerPid = 0; WorkerStartedUtc = ''; ChildPid = 0; ChildStartedUtc = ''; CreatedUtc = [datetime]::UtcNow.ToString('o');
            CompletedUtc = $null; State = 'queued'; ExitCode = $null; TaskName = 'Basaltwater Development ' + $id; Message = '' }
        if ($mode -eq 'success') {
            # A long queue across wall-clock days must not inflate execution time.
            $record.CreatedUtc = [datetime]::UtcNow.AddDays(-30).ToString('o')
            $record | Add-Member NoteProperty Boot (Get-DevelopmentBoot)
        }
        Write-DevelopmentJson (Join-Path $directory 'request.json') $request
        Write-DevelopmentJson (Join-Path $directory 'receipt.json') $record
        $workerPath = (Join-Path $fixtureSource 'Invoke-DevelopmentTask.ps1').Replace("'", "''")
        $code = "function Unregister-ScheduledTask { }; function Show-NativeDesktopNotification { }; & '$workerPath' -TaskId '$id'"
        if ($mode -eq 'cleanup-fail') {
            $code = @'
function Remove-Item {
    [CmdletBinding()] param([string]$LiteralPath, [switch]$Force)
    if ($LiteralPath -like '*\request.json') { throw 'fixture request deletion failure' }
    Microsoft.PowerShell.Management\Remove-Item @PSBoundParameters
};
'@ + $code
        }
        $start = New-Object Diagnostics.ProcessStartInfo
        $start.FileName = $shell; $start.UseShellExecute = $false; $start.CreateNoWindow = $true
        $start.Arguments = '-NoLogo -NoProfile -NonInteractive -EncodedCommand ' + [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
        $guardian = [Diagnostics.Process]::Start($start)
        $timer = [Diagnostics.Stopwatch]::StartNew()
        $descendant = $null
        $liveTailSeen = $false
        while (-not $guardian.WaitForExit(100)) {
            if ($timer.Elapsed.TotalSeconds -gt 45) { throw "Owned $mode guardian exceeded qualification deadline" }
            if ($mode -eq 'paused-flood' -and -not $liveTailSeen -and (Test-Path -LiteralPath (Join-Path $directory 'stderr.log'))) {
                $live = Invoke-NativeDevelopmentCli @('logs',$id)
                if ($live.Stdout.EndsWith("`nEND-OF-PROBE`n") -and $live.Stderr.EndsWith("`nEND-OF-PROBE`n")) {
                    $liveTailSeen = $true
                    [IO.File]::WriteAllText($childFile + '.release', 'release')
                }
            }
            if ($mode -in @('timeout','cancel','orphan','crash') -and -not $descendant -and (Test-Path -LiteralPath $childFile)) {
                $descendant = Get-Process -Id ([int](Get-Content -LiteralPath $childFile)) -ErrorAction Stop
                $null = $descendant.Handle
                $active = Get-DevelopmentStatus $id
                Assert-True ($active.State -eq 'running') 'A live PS7 guardian was misidentified as interrupted'
                if ($mode -eq 'cancel') { [IO.File]::WriteAllText((Join-Path $directory 'cancel'), 'cancel') }
                if ($mode -eq 'crash') { $guardian.Kill() }
            }
        }
        $guardian.Dispose(); $guardian = $null
        Assert-True (-not $unrelated.HasExited) 'Owned cleanup terminated an unrelated process'
        if ($mode -eq 'cleanup-fail') {
            $uncertain = Get-DevelopmentRecord $id
            Assert-True ($uncertain.State -eq 'cleanup-uncertain' -and -not $uncertain.CompletedUtc -and
                $uncertain.Message -match 'Private environment cleanup' -and (Test-Path -LiteralPath (Join-Path $directory 'request.json'))) 'Environment deletion failure was hidden by a terminal receipt'
            Assert-True ($null -eq $uncertain.ElapsedSeconds -and $null -eq $uncertain.ExecutionEndedUtc) 'Uncertain cleanup invented timing'
            continue
        }
        if ($mode -eq 'crash') {
            Assert-True ($null -ne $descendant -and $descendant.WaitForExit(10000)) 'Guardian crash leaked a native descendant'
            $descendant.Dispose()
            $interrupted = Get-DevelopmentStatus $id
            Assert-True ($interrupted.State -eq 'cleanup-uncertain' -and -not $interrupted.CompletedUtc) 'Guardian crash claimed verified completion'
            Assert-True ($interrupted.ExecutionStartedUtc -and $null -eq $interrupted.ElapsedSeconds -and $null -eq $interrupted.ExecutionEndedUtc) 'Worker loss invented timing'
            $blocked = $false
            try { Assert-NativeDevelopmentIdle } catch { $blocked = $_.Exception.Message -match 'cleanup-uncertain' }
            Assert-True $blocked 'Maintenance did not block on the interrupted guardian receipt'
            continue
        }
        $result = Get-DevelopmentRecord $id
        $expected = switch ($mode) { success { 'complete' }; fail { 'failed' }; flood { 'complete' }; failed-flood { 'failed' }; paused-flood { 'complete' }; timeout { 'timed-out' }; cancel { 'cancelled' }; orphan { 'complete' } }
        Assert-True ($result.State -eq $expected -and $result.CompletedUtc) "Expected $expected after process cleanup; got $($result.State): $($result.Message)"
        Assert-True (-not (Test-Path -LiteralPath (Join-Path $directory 'request.json'))) 'Terminal task retained its environment snapshot'
        Assert-True ($result.ExecutionStartedUtc.EndsWith('Z') -and $result.ExecutionEndedUtc.EndsWith('Z') -and
            $result.ElapsedSeconds -gt 0 -and $result.ElapsedSeconds -lt 40) "Invalid $mode execution timing"
        if ($mode -in @('flood','failed-flood','paused-flood')) {
            if ($mode -eq 'paused-flood') { Assert-True $liveTailSeen 'Idle live stream did not publish its latest tail' }
            foreach ($stream in @($result.Streams.Stdout, $result.Streams.Stderr)) {
                Assert-True ($stream.RetainedBytes -eq 4MB -and $stream.DiscardedBytes -eq 12345 -and $stream.Truncated -and $stream.LimitBytes -eq 4MB) 'Flood retention accounting was incomplete'
                Assert-True ($stream.RetentionPolicy -eq 'head-tail' -and $stream.HeadBytes -eq 2MB -and $stream.TailBytes -eq 2MB) 'Flood retention policy was not explicit'
            }
            $tail = Invoke-NativeDevelopmentCli @('logs',$id)
            Assert-True ($tail.Tails.stdout.ReturnedBytes -eq 65536 -and $tail.Tails.stdout.OmittedBytes -eq (4MB - 65536) -and $tail.Tails.stdout.Truncated) 'Tail omission confused with cap loss'
            Assert-True ($tail.Stdout.EndsWith("`nEND-OF-PROBE`n") -and $tail.Stderr.EndsWith("`nEND-OF-PROBE`n")) 'Late output was discarded from a flooded stream'
            Assert-True ((Get-Content -LiteralPath (Join-Path $directory 'stdout.log') -TotalCount 1) -eq 'BEGIN-OF-PROBE') 'Flood retention lost initial output'
            if ($mode -eq 'failed-flood') { Assert-True ($result.ExitCode -eq 23) 'Flood hid failure exit code' }
            continue
        }
        if ($mode -eq 'success') {
            $waited = Invoke-NativeDevelopmentCli @('wait',$id,'--timeout','1')
            Assert-True ($waited.WaitState -eq 'completed' -and $waited.Receipt.State -eq 'complete' -and -not $waited.TimedOut) 'Wait did not return the settled native receipt'
            # Retired tasks refuse late scheduler starts before reading the
            # removed environment or running any project code again.
            $start.RedirectStandardError = $true; $start.RedirectStandardOutput = $true
            $replay = [Diagnostics.Process]::Start($start)
            try {
                $errors = $replay.StandardError.ReadToEndAsync()
                $outputDrain = $replay.StandardOutput.BaseStream.CopyToAsync([IO.Stream]::Null)
                Assert-True ($replay.WaitForExit(10000)) 'Retired task replay was not bounded'
                Assert-True ($errors.Wait(1000) -and $outputDrain.Wait(1000) -and $replay.ExitCode -ne 0 -and
                    $errors.Result -match 'refusing replay') 'Retired task did not reject replay before reading its environment'
            } finally { if (-not $replay.HasExited) { $replay.Kill(); $replay.WaitForExit() }; $replay.Dispose() }
        }
        if ($mode -in @('success','fail')) {
            Assert-True (-not $result.Streams.Stdout.Truncated -and $result.Streams.Stdout.DiscardedBytes -eq 0 -and
                $result.Streams.Stderr.RetainedBytes -eq 0) 'Clean stream reported loss'
            $tail = Invoke-NativeDevelopmentCli @('logs',$id)
            Assert-True (-not $tail.Tails.stdout.Truncated -and $tail.Tails.stdout.OmittedBytes -eq 0) 'Clean tail reported omission'
            $output = Read-DevelopmentJson (Join-Path $directory 'stdout.log')
            Assert-True (($output.argv -join '|') -ceq ($literal -join '|') -and $output.date -ceq $environment.TASK_LITERAL_DATE) 'Worker changed Unicode/empty argv or ISO-looking environment values'
            if ($mode -eq 'fail') { Assert-True ($result.ExitCode -eq 23) 'Worker hid native failure code' }
        } else {
            Assert-True ($null -ne $descendant -and $descendant.WaitForExit(0)) 'Terminal receipt preceded descendant cleanup'
            $descendant.Dispose()
        }
    }
    $passed = $true
    Write-Host 'PowerShell 7 owned development process tests passed.'
} finally {
    if ($guardian) { if (-not $guardian.HasExited) { $guardian.Kill(); $guardian.WaitForExit(10000) | Out-Null }; $guardian.Dispose() }
    if ($unrelated) { if (-not $unrelated.HasExited) { $unrelated.Kill(); $null = $unrelated.WaitForExit(10000) }; $unrelated.Dispose() }
    $env:LOCALAPPDATA = $savedLocal
    $resolved = [IO.Path]::GetFullPath($temporary)
    if (-not $resolved.StartsWith([IO.Path]::GetTempPath(), [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test cleanup' }
    if ($passed) { Remove-Item -LiteralPath $resolved -Recurse -Force }
    else { Write-Host "Retained process qualification evidence: $resolved" }
}
