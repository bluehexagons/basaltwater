<# Real owned process trees in a temporary store; Scheduler, notifications and sleep requests mocked. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
$windows = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $windows 'PowerShell.Common.ps1')
. (Join-Path $windows 'Development.Common.ps1')
. (Join-Path $windows 'Development.Diagnostics.ps1')
function Get-DevelopmentEnvironment { return [pscustomobject]@{ State = 'unavailable'; Graphics = $null; Displays = $null; Errors = @('fixture: no hardware probes') } }
$shell = Get-NativePowerShell7
if (-not $shell) { throw 'Process qualification requires PowerShell 7; run native setup first' }
$python = Get-Command python.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1
if ($python.Source -like '*\WindowsApps\*') { throw 'Process qualification requires real Python, not a Store alias' }
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-process-' + [guid]::NewGuid().ToString('N'))
$savedLocal = $env:LOCALAPPDATA
$guardian = $null
$unrelated = $null
$diagnosticProbe = $null
$publicationRelease = $null
$diagnosticRelease = $null
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
    foreach ($file in @('Development.Common.ps1','Development.Session.ps1','Development.Process.ps1','Invoke-DevelopmentTask.ps1','GitHubRunner.Common.ps1','ShellIntegration.Common.ps1')) {
        Copy-Item -LiteralPath (Join-Path $windows $file) -Destination (Join-Path $fixtureSource $file)
    }
    $processTypes = Join-Path $fixtureSource 'Development.Process.ps1'
    $types = Get-Content -LiteralPath $processTypes -Raw
    $types = [regex]::Replace($types, '(?s)public static void Awake\(bool active\) \{.*?\n        \}', 'public static void Awake(bool active) { }')
    # Delay only private copied fixtures at the potentially blocking operations.
    # Killing the guardian must still kill native members while these gates stay shut.
    $publicationGate = @'
            if(!code.HasValue && File.Exists(path+".pause")) {
                File.WriteAllText(path+".entered",pid.ToString());
                var deadline=DateTime.UtcNow.AddSeconds(20);
                while(!File.Exists(path+".release") && DateTime.UtcNow<deadline) System.Threading.Thread.Sleep(10);
            }
'@
    $types = $types.Replace('static void WriteTarget(string path,uint pid,long created,uint? code) {',
        ('static void WriteTarget(string path,uint pid,long created,uint? code) {' + "`n" + $publicationGate))
    Set-Content -LiteralPath $processTypes -Value $types -Encoding UTF8
    $diagnosticSource = Join-Path $fixtureSource 'Diagnostics.Native.cs'
    $diagnosticEntered = Join-Path $fixtureSource 'diagnostic.entered'
    $diagnosticRelease = Join-Path $fixtureSource 'diagnostic.release'
    $diagnosticGate = @'
            System.IO.File.WriteAllText(@"ENTERED_PATH","entered");
            var deadline=DateTime.UtcNow.AddSeconds(20);
            while(!System.IO.File.Exists(@"RELEASE_PATH") && DateTime.UtcNow<deadline) System.Threading.Thread.Sleep(10);
'@
    $diagnosticGate = $diagnosticGate.Replace('ENTERED_PATH',$diagnosticEntered.Replace('"','""')).Replace('RELEASE_PATH',$diagnosticRelease.Replace('"','""'))
    $diagnosticTypes = (Get-Content -LiteralPath (Join-Path $windows 'Diagnostics.Native.cs') -Raw).Replace(
        'if(!OpenProcessToken(process,8,out token))', ($diagnosticGate + "`n            if(!OpenProcessToken(process,8,out token))"))
    Set-Content -LiteralPath $diagnosticSource -Value $diagnosticTypes -Encoding UTF8
    $project = Join-Path $temporary ('project ' + [char]0x03bb)
    New-Item -ItemType Directory -Path $project | Out-Null
    $fixture = Join-Path $project 'task.py'
    Set-Content -LiteralPath $fixture -Encoding UTF8 -Value @'
from __future__ import annotations
import json, os, pathlib, subprocess, sys, time
mode, child_path = sys.argv[1:3]
if mode.startswith('session-'):
    profile = pathlib.Path(os.environ['BASALTWATER_TEST_PROFILE'])
    (profile / 'settings.json').write_text('{}', encoding='utf-8')
    if mode == 'session-early':
        sys.exit(0)
    if mode != 'session-timeout':
        ready = pathlib.Path(os.environ['BASALTWATER_TEST_READY_FILE'])
        marker = ready.with_suffix('.tmp')
        marker.write_text(json.dumps({'Token': os.environ['BASALTWATER_TEST_READY_TOKEN']}), encoding='utf-8')
        os.replace(marker, ready)
    if mode in ('session-ready', 'session-scheduler-cleanup-fail'):
        print('session evidence retained', flush=True)
        time.sleep(2)
        sys.exit(0)
if mode in ('flood', 'failed-flood', 'paused-flood'):
    for stream in (sys.stdout.buffer, sys.stderr.buffer):
        head, tail = b'BEGIN-OF-PROBE\n', b'\nEND-OF-PROBE\n'
        stream.write(head + b'z' * (4 * 1024 * 1024 + 12345 - len(head) - len(tail)) + tail)
        stream.flush()
    if mode == 'paused-flood':
        while not pathlib.Path(child_path + '.release').exists():
            time.sleep(0.01)
    sys.exit(23 if mode == 'failed-flood' else 0)
if mode in ('cancel', 'timeout', 'orphan', 'crash', 'diagnostic-crash', 'session-timeout', 'session-cancel'):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    marker = pathlib.Path(child_path + '.tmp')
    marker.write_text(str(child.pid), encoding='utf-8')
    os.replace(marker, child_path)
    time.sleep(1 if mode == 'orphan' else 60)
print(json.dumps({'argv': sys.argv[3:], 'date': os.environ.get('TASK_LITERAL_DATE')}))
sys.exit(23 if mode == 'fail' else 0)
'@
    foreach ($mode in @('success','fail','flood','failed-flood','paused-flood','timeout','cancel','orphan','cleanup-fail','scheduler-cleanup-fail','crash','publication-crash','diagnostic-crash',
        'session-ready','session-early','session-timeout','session-cancel','session-scheduler-cleanup-fail')) {
        $id = [guid]::NewGuid().ToString('N')
        $directory = Get-DevelopmentDirectory $id
        Initialize-DevelopmentDirectory $directory
        $childFile = Join-Path $directory 'child.pid'
        if ($mode -eq 'publication-crash') {
            $publicationRelease = Join-Path $directory 'target.json.release'
            [IO.File]::WriteAllText((Join-Path $directory 'target.json.pause'), 'pause')
        }
        $literal = @('two words', '', 'quote"here', "lambda $([char]0x03bb)", 'trailing\', '$literal & ;')
        $environment = @{}
        foreach ($name in @('SystemRoot','SystemDrive','ComSpec','PATH','PATHEXT','TEMP','TMP','USERPROFILE')) { $environment[$name] = [Environment]::GetEnvironmentVariable($name) }
        $environment.LOCALAPPDATA = $env:LOCALAPPDATA
        $environment.TASK_LITERAL_DATE = '2026-10-29T12:34:56Z'
        $request = [pscustomobject]@{ Project = $project; Executable = $python.Source;
            Argv = @($fixture, $mode, $childFile) + $literal; Environment = $environment; Msvc = $false;
            # Include startup/compilation headroom so timeout exercises a live native tree.
            PowerShell = $shell; Node = ''; TimeoutSeconds = $(if ($mode -eq 'timeout') { 10 } else { 30 }) }
        $record = [pscustomobject]@{ Id = $id; OwnerSid = (Get-DevelopmentSid); SessionId = (Get-Process -Id $PID).SessionId;
            WorkerPid = 0; WorkerStartedUtc = ''; ChildPid = 0; ChildStartedUtc = ''; CreatedUtc = [datetime]::UtcNow.ToString('o');
            CompletedUtc = $null; State = 'queued'; ExitCode = $null; TaskName = 'Basaltwater Development ' + $id; Message = '' }
        if ($mode -eq 'success') {
            # A long queue across wall-clock days must not inflate execution time.
            $record.CreatedUtc = [datetime]::UtcNow.AddDays(-30).ToString('o')
            $record | Add-Member NoteProperty Boot (Get-DevelopmentBoot)
        }
        if ($mode -like 'session-*') {
            $record | Add-Member NoteProperty TestSession (New-DevelopmentSession $id 10)
            Initialize-DevelopmentSession $record $request
        }
        Write-DevelopmentJson (Join-Path $directory 'request.json') $request
        Write-DevelopmentJson (Join-Path $directory 'receipt.json') $record
        $workerPath = (Join-Path $fixtureSource 'Invoke-DevelopmentTask.ps1').Replace("'", "''")
        $schedulerFixture = @'
$global:FixtureRegistration = $true
function Get-ScheduledTask {
    [CmdletBinding()] param([string]$TaskPath)
    if ($global:FixtureRegistration) { [pscustomobject]@{ TaskName = 'Basaltwater Development FIXTURE_ID' } }
}
function Unregister-ScheduledTask {
    [CmdletBinding()] param([string]$TaskName, [string]$TaskPath, [switch]$Confirm)
    $directory = Get-DevelopmentDirectory 'FIXTURE_ID'
    $published = Get-DevelopmentRecord 'FIXTURE_ID'
    [IO.File]::WriteAllText((Join-Path $directory 'scheduler-cleanup-observed.json'),
        (@{ TerminalPublished = [bool]$published.CompletedUtc } | ConvertTo-Json))
    if ('FIXTURE_MODE' -in @('scheduler-cleanup-fail','session-scheduler-cleanup-fail')) { throw 'fixture Scheduler retirement failure' }
    $global:FixtureRegistration = $false
}
function Show-NativeDesktopNotification { }
'@
        $code = $schedulerFixture.Replace('FIXTURE_ID', $id).Replace('FIXTURE_MODE', $mode) +
            "`n& '$workerPath' -TaskId '$id'"
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
            if ($mode -eq 'publication-crash' -and -not $descendant -and (Test-Path -LiteralPath (Join-Path $directory 'target.json.entered'))) {
                $descendant = Get-Process -Id ([int](Get-Content -LiteralPath (Join-Path $directory 'target.json.entered')))
                $null = $descendant.Handle
                $guardian.Kill()
                Assert-True ($descendant.WaitForExit(2000)) 'Blocked target publication prolonged Job lifetime after guardian loss'
                Assert-True (-not (Test-Path -LiteralPath $publicationRelease)) 'Publication gate released before cleanup observation'
            }
            if ($mode -eq 'paused-flood' -and -not $liveTailSeen -and (Test-Path -LiteralPath (Join-Path $directory 'stderr.log'))) {
                $live = Invoke-NativeDevelopmentCli @('logs',$id)
                if ($live.Stdout.EndsWith("`nEND-OF-PROBE`n") -and $live.Stderr.EndsWith("`nEND-OF-PROBE`n")) {
                    $liveTailSeen = $true
                    [IO.File]::WriteAllText($childFile + '.release', 'release')
                }
            }
            if ($mode -in @('timeout','cancel','orphan','crash','diagnostic-crash','session-timeout','session-cancel') -and -not $descendant -and (Test-Path -LiteralPath $childFile)) {
                $descendant = Get-Process -Id ([int](Get-Content -LiteralPath $childFile)) -ErrorAction Stop
                $null = $descendant.Handle
                $active = Get-DevelopmentStatus $id
                Assert-True ($active.State -eq 'running') 'A live PS7 guardian was misidentified as interrupted'
                if ($mode -eq 'cancel') {
                    $diagnostic = Invoke-NativeDevelopmentCli @('diagnose',$id)
                    Assert-True ($diagnostic.ok -and $diagnostic.State -eq 'live' -and
                        $diagnostic.Target.ProcessId -ne $active.ChildPid -and $diagnostic.Resources.ProcessId -eq $diagnostic.Target.ProcessId -and
                        $diagnostic.Resources.WorkingSetBytes -gt 0 -and $diagnostic.Resources.ThreadCount -gt 0 -and
                        (Test-Path -LiteralPath $diagnostic.SnapshotPath)) "Native diagnostics failed: $($diagnostic | ConvertTo-Json -Depth 5 -Compress)"
                    $stale = $diagnostic.Target.PSObject.Copy(); $stale.CreatedFileTimeUtc = ([long]$stale.CreatedFileTimeUtc + 1).ToString()
                    $caught = $null
                    try { Invoke-DevelopmentDiagnosticsWorker $active $stale | Out-Null } catch { $caught = $_ }
                    Assert-True ($null -ne $caught -and $caught.Exception.Message -match 'Stale native target') 'Diagnostics accepted a stale process identity'
                    $foreign = [pscustomobject]@{ ProcessId = $unrelated.Id; CreatedFileTimeUtc = $unrelated.StartTime.ToUniversalTime().ToFileTimeUtc().ToString() }
                    $caught = $null
                    try { Invoke-DevelopmentDiagnosticsWorker $active $foreign | Out-Null } catch { $caught = $_ }
                    Assert-True ($null -ne $caught -and $caught.Exception.Message -match 'outside the task Job') 'Diagnostics accepted an unrelated process'
                }
                if ($mode -in @('cancel','session-cancel')) { [IO.File]::WriteAllText((Join-Path $directory 'cancel'), 'cancel') }
                if ($mode -eq 'crash') { $guardian.Kill() }
                if ($mode -eq 'diagnostic-crash') {
                    $target = Read-DevelopmentTarget $directory
                    $probeCode = "Add-Type -Path '" + $diagnosticSource.Replace("'","''") + "'; try { [Basaltwater.Diagnostics.Native]::Capture(" +
                        $target.ProcessId + ',[long]' + $target.CreatedFileTimeUtc + ",'" + $active.JobName + "','" + $active.OwnerSid + "'," + $active.SessionId + ') | Out-Null } catch { }'
                    $probeStart = [Diagnostics.ProcessStartInfo]::new()
                    $probeStart.FileName = $shell; $probeStart.UseShellExecute = $false; $probeStart.CreateNoWindow = $true
                    $probeStart.Arguments = '-NoProfile -EncodedCommand ' + [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($probeCode))
                    $diagnosticProbe = [Diagnostics.Process]::Start($probeStart)
                    $probeClock = [Diagnostics.Stopwatch]::StartNew()
                    while (-not (Test-Path -LiteralPath $diagnosticEntered) -and -not $diagnosticProbe.HasExited -and $probeClock.Elapsed.TotalSeconds -lt 10) { Start-Sleep -Milliseconds 25 }
                    Assert-True ((Test-Path -LiteralPath $diagnosticEntered) -and -not $diagnosticProbe.HasExited) 'Diagnostic fixture did not reach its counter-query gate'
                    $guardian.Kill()
                    Assert-True ($descendant.WaitForExit(2000)) 'Blocked diagnostic query prolonged Job lifetime after guardian loss'
                    Assert-True (-not $diagnosticProbe.HasExited) 'Diagnostic gate released before cleanup observation'
                    [IO.File]::WriteAllText($diagnosticRelease, 'release')
                    Assert-True ($diagnosticProbe.WaitForExit(10000)) 'Diagnostic fixture did not settle after gate release'
                    $diagnosticProbe.Dispose(); $diagnosticProbe = $null
                }
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
        if ($mode -in @('scheduler-cleanup-fail','session-scheduler-cleanup-fail')) {
            $uncertain = Get-DevelopmentRecord $id
            Assert-True ($uncertain.State -eq 'cleanup-uncertain' -and -not $uncertain.CompletedUtc -and
                $uncertain.Message -match 'Scheduler' -and -not (Test-Path -LiteralPath (Join-Path $directory 'request.json'))) 'Scheduler retirement failure was hidden by a terminal receipt'
            Assert-True ($null -eq $uncertain.ElapsedSeconds -and $null -eq $uncertain.ExecutionEndedUtc) 'Uncertain Scheduler cleanup invented timing'
            if ($mode -like 'session-*') { Assert-True (Test-Path -LiteralPath $uncertain.TestSession.ProfilePath) 'Uncertain Scheduler cleanup removed the session profile' }
            continue
        }
        if ($mode -in @('crash','publication-crash','diagnostic-crash')) {
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
        $target = Read-DevelopmentTarget $directory
        Assert-True ($null -ne $target -and $target.ProcessId -ne $result.ChildPid) 'Native root identity missing or confused with the PowerShell launcher'
        if ($mode -like 'session-*') {
            $expected = switch ($mode) { session-ready { 'complete' }; session-early { 'failed' }; session-timeout { 'failed' }; session-cancel { 'cancelled' } }
            Assert-True ($result.State -eq $expected -and $result.CompletedUtc -and $result.TestSession.ProfileCleanup -eq 'removed' -and
                -not (Test-Path -LiteralPath $result.TestSession.ProfilePath)) "Session $mode did not settle: $($result | ConvertTo-Json -Depth 5 -Compress)"
            Assert-True ((Test-Path -LiteralPath (Join-Path $directory 'session-evidence.json')) -and
                -not (Test-Path -LiteralPath (Join-Path $directory 'request.json'))) 'Session lost provenance or retained credentials'
            if ($mode -eq 'session-ready') { Assert-True ($result.TestSession.ReadyState -eq 'ready' -and $result.TestSession.ReadyElapsedSeconds -gt 0) 'Live readiness not observed' }
            if ($mode -eq 'session-timeout') { Assert-True ($result.TestSession.ReadyState -eq 'timed-out' -and $result.Message -match 'readiness deadline') 'Readiness deadline not distinct from task timeout' }
            if ($descendant) { Assert-True ($descendant.WaitForExit(0)) 'Session terminal receipt preceded descendant cleanup'; $descendant.Dispose() }
            continue
        }
        $expected = switch ($mode) { success { 'complete' }; fail { 'failed' }; flood { 'complete' }; failed-flood { 'failed' }; paused-flood { 'complete' }; timeout { 'timed-out' }; cancel { 'cancelled' }; orphan { 'complete' } }
        Assert-True ($result.State -eq $expected -and $result.CompletedUtc) "Expected $expected after process cleanup; got $($result.State): $($result.Message)"
        $retirement = Read-DevelopmentJson (Join-Path $directory 'scheduler-cleanup-observed.json')
        Assert-True (-not $retirement.TerminalPublished) 'Terminal receipt preceded Scheduler retirement'
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
            $postmortem = Invoke-NativeDevelopmentCli @('diagnose',$id)
            $expectedExit = if ($mode -eq 'fail') { 23 } else { 0 }
            Assert-True ($postmortem.ok -and $postmortem.State -eq 'settled' -and $null -eq $postmortem.Resources -and
                $postmortem.Target.State -eq 'exited' -and $postmortem.Target.ExitCode -eq $expectedExit) 'Postmortem diagnostics lost native exit evidence or queried a recycled PID'
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
    if ($publicationRelease) { [IO.File]::WriteAllText($publicationRelease, 'release') }
    if ($diagnosticRelease) { [IO.File]::WriteAllText($diagnosticRelease, 'release') }
    if ($guardian) { if (-not $guardian.HasExited) { $guardian.Kill(); $guardian.WaitForExit(10000) | Out-Null }; $guardian.Dispose() }
    if ($diagnosticProbe) { if (-not $diagnosticProbe.WaitForExit(2000)) { $diagnosticProbe.Kill(); $diagnosticProbe.WaitForExit(10000) | Out-Null }; $diagnosticProbe.Dispose() }
    if ($unrelated) { if (-not $unrelated.HasExited) { $unrelated.Kill(); $null = $unrelated.WaitForExit(10000) }; $unrelated.Dispose() }
    $env:LOCALAPPDATA = $savedLocal
    $resolved = [IO.Path]::GetFullPath($temporary)
    if (-not $resolved.StartsWith([IO.Path]::GetTempPath(), [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test cleanup' }
    if ($passed) { Remove-Item -LiteralPath $resolved -Recurse -Force }
    else { Write-Host "Retained process qualification evidence: $resolved" }
}
