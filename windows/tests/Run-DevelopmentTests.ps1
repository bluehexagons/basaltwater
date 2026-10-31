<# Offline development task contracts. Scheduler, identity and system mutations are mocked. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '..\Development.Common.ps1')
$schedulerImplementation = ${function:Start-DevelopmentScheduledTask}
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Assert-Throws([scriptblock]$Action, [string]$Pattern) {
    $caught = $null
    try { & $Action | Out-Null } catch { $caught = $_ }
    if (-not $caught -or $caught.Exception.Message -notmatch $Pattern) { throw "Expected $Pattern; received $caught" }
}
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-development-test-' + [guid]::NewGuid().ToString('N'))
$savedLocal = $env:LOCALAPPDATA; $savedFnm = $env:FNM_DIR; $savedProvider = $env:BASALTWATER_PROVIDER_FIXTURE
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $env:LOCALAPPDATA = Join-Path $temporary 'profile'
    $env:FNM_DIR = Join-Path $temporary 'fnm'
    $env:BASALTWATER_PROVIDER_FIXTURE = 'provider shell value'
    $project = Join-Path $temporary 'project spaces'
    New-Item -ItemType Directory -Path $project | Out-Null
    $shell = (Get-Process -Id $PID).Path
    & {
        function New-ScheduledTaskAction($Execute, $Argument) { return @{ Execute = $Execute; Argument = $Argument } }
        function New-ScheduledTaskPrincipal($UserId, $LogonType, $RunLevel) { return @{ UserId = $UserId; LogonType = $LogonType; RunLevel = $RunLevel } }
        function New-ScheduledTaskSettingsSet($ExecutionTimeLimit, [switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries) { return @{ Limit = $ExecutionTimeLimit } }
        function Register-ScheduledTask($TaskName, $Action, $Principal, $Settings) {
            Assert-True ($Principal.LogonType -eq 'Interactive' -and $Principal.RunLevel -eq 'Limited') 'Worker requested elevated/noninteractive account'
            Assert-True ($Action.Execute -eq $shell -and $Action.Argument -match '-WindowStyle Hidden') 'Worker was not hidden or used an unverified executable'
            Assert-True ($Action.Argument -match '-ExecutionPolicy Bypass') 'Scheduled helper depends on the account script execution policy'
            Assert-True ($Settings.Limit -eq [timespan]::Zero) 'Scheduler could terminate guardian before cleanup'
        }
        function Start-ScheduledTask($TaskName) { $script:schedulerStarted = $TaskName }
        & $schedulerImplementation ([pscustomobject]@{ Id = ('a' * 32); OwnerSid = 'fixture'; TaskName = 'fixture-development' }) $shell
        Assert-True ($script:schedulerStarted -eq 'fixture-development') 'Worker was not submitted to Scheduler'
    }
    function Get-NativePowerShell7 { return $shell }
    function Get-DevelopmentSid { return 'S-1-5-21-fixture' }
    function Get-DevelopmentBoot { return $script:FixtureBoot }
    function Initialize-DevelopmentDirectory([string]$Directory) { New-Item -ItemType Directory -Path $Directory -Force | Out-Null }
    $script:launchFailure = $false; $script:launched = $null
    $script:FixtureBoot = '639200000000000000'; $script:PublishedState = ''; $script:TaskNames = @{}
    $script:RetireFailure = $false
    function Get-ScheduledTask($TaskPath) { foreach ($name in $script:TaskNames.Keys) { [pscustomobject]@{ TaskName = $name } } }
    function Unregister-ScheduledTask($TaskName, $TaskPath, [switch]$Confirm) {
        if ($script:RetireFailure) { throw 'mock retirement failure' }
        $script:TaskNames.Remove($TaskName)
    }
    function Start-DevelopmentScheduledTask($Record, [string]$PowerShell) {
        $script:launched = $Record
        $script:TaskNames[$Record.TaskName] = $true
        if ($script:PublishedState) {
            $owned = Get-DevelopmentRecord $Record.Id
            $owned.State = $script:PublishedState; $owned.WorkerPid = 12345; $owned.WorkerStartedUtc = 'fixture worker'
            if ($owned.State -eq 'complete') { $owned.CompletedUtc = [DateTime]::UtcNow.ToString('o'); $owned.ExitCode = 0 }
            Write-DevelopmentJson (Join-Path (Get-DevelopmentDirectory $Record.Id) 'receipt.json') $owned
            throw 'mock dispatch failure after worker publication'
        }
        if ($script:launchFailure) { throw 'mock Scheduler unavailable' }
    }
    $unicode = [string][char]0x96ea + ' space'
    $literal = @('two words', $unicode, '', 'quote"here', 'trailing\', '--dry-run', '$literal; &')
    $argv = @('exec','--project',$project,'--env','TASK_ONLY=child value','--dry-run','--',$shell) + $literal
    $plan = Invoke-NativeDevelopmentCli $argv
    Assert-True ($plan.Argv.Count -eq $literal.Count) 'Literal argv count changed'
    for ($i = 0; $i -lt $literal.Count; $i++) { Assert-True ($plan.Argv[$i] -ceq $literal[$i]) 'Literal argv changed' }
    Assert-True (-not (Test-Path -LiteralPath (Get-DevelopmentRoot))) 'Dry run created task state'
    Assert-True (-not [Environment]::GetEnvironmentVariable('TASK_ONLY')) 'Task env leaked into provider'
    $msvcPlan = Invoke-NativeDevelopmentCli @('exec','--project',$project,'--msvc','--dry-run','--','cl.exe','/c','source with spaces.cpp')
    Assert-True ($msvcPlan.Executable -eq 'cl.exe' -and $msvcPlan.Msvc) 'MSVC executable required provider-shell VS activation'
    $doctor = Invoke-NativeDevelopmentCli @('doctor','--project',$project)
    Assert-True ($doctor.NodeDiagnostic -match 'numeric project pin') 'Doctor hid missing project runtime'
    Assert-True (-not (Test-Path -LiteralPath (Get-DevelopmentRoot))) 'Doctor wrote task state'
    Assert-True ($doctor.Checkout.State -in @('nongit','unknown')) 'Nongit directory claimed revision metadata'
    & {
        function Get-NativePowerShell7 { throw 'Presence discovery executed a version probe' }
        $savedGodot = $env:GODOT_BIN
        try {
            [IO.File]::WriteAllText((Join-Path $project 'project.godot'), '; fixture')
            $env:GODOT_BIN = Join-Path $project 'gui-shim.cmd'
            [IO.File]::WriteAllText($env:GODOT_BIN, '@echo executed > should-never-exist')
            $discovered = Invoke-NativeDevelopmentCli @('doctor','--project',$project)
            Assert-True ($discovered.MissingRequirements.Count -gt 0 -and -not (Test-Path -LiteralPath (Join-Path $project 'should-never-exist'))) 'Discovery executed a GUI shim'
        } finally { $env:GODOT_BIN = $savedGodot }
    }
    $git = Get-DevelopmentTrustedGit
    if ($git) {
        $gitProject = Join-Path $temporary 'git project'
        New-Item -ItemType Directory -Path $gitProject | Out-Null
        foreach ($command in @('init -q','config user.name Fixture','config user.email fixture@example.invalid','commit --allow-empty --no-verify -m fixture')) {
            $setup = Invoke-DevelopmentGit $git $gitProject $command
            Assert-True ($setup.ExitCode -eq 0) "Git fixture setup failed: $command"
        }
        $checkout = Get-DevelopmentCheckout $gitProject $git
        Assert-True ($checkout.State -eq 'available' -and $checkout.Revision -match '^[0-9a-f]{40,64}$' -and -not $checkout.Dirty) 'Clean checkout discovery failed'
        $untrackedPath = Join-Path $gitProject 'untracked.txt'
        [IO.File]::WriteAllText($untrackedPath, 'untracked')
        $checkout = Get-DevelopmentCheckout $gitProject $git
        Assert-True ($checkout.Dirty -and $checkout.Untracked -and -not $checkout.TrackedDirty) 'Untracked checkout changes were hidden'
        Remove-Item -LiteralPath $untrackedPath
        [IO.File]::WriteAllText((Join-Path $gitProject 'tracked.txt'), 'original')
        $null = Invoke-DevelopmentGit $git $gitProject 'add tracked.txt'
        Assert-True ((Get-DevelopmentCheckout $gitProject $git).Dirty) 'Staged dirty checkout was hidden'
        $null = Invoke-DevelopmentGit $git $gitProject 'commit --no-verify -m tracked'
        [IO.File]::WriteAllText((Join-Path $gitProject 'tracked.txt'), 'changed')
        # If either project callback runs, the query fails or creates the marker.
        [IO.File]::WriteAllText((Join-Path $gitProject '.gitattributes'), '*.txt filter=fixture')
        $null = Invoke-DevelopmentGit $git $gitProject 'config filter.fixture.clean "cmd.exe /c echo executed>callback-marker"'
        $null = Invoke-DevelopmentGit $git $gitProject 'config filter.fixture.process "cmd.exe /c echo executed>callback-marker"'
        $null = Invoke-DevelopmentGit $git $gitProject 'config core.fsmonitor "cmd.exe /c echo executed>callback-marker"'
        Assert-True ((Get-DevelopmentCheckout $gitProject $git).Dirty -and -not (Test-Path -LiteralPath (Join-Path $gitProject 'callback-marker'))) 'Working dirty discovery executed project callbacks'
        # Large local configuration must fail closed, without unbounded capture.
        $null = Invoke-DevelopmentGit $git $gitProject ('config filter.fixture.clean "' + ('x' * 9000) + '"')
        Assert-Throws { Invoke-DevelopmentGit $git $gitProject 'config --get filter.fixture.clean' } 'exceeded its bound'
        Assert-True ((Get-DevelopmentCheckout $gitProject '').State -eq 'unknown') 'Unavailable Git invented metadata'
        [IO.File]::WriteAllText((Join-Path $gitProject '.git\HEAD'), 'broken metadata')
        Assert-True ((Get-DevelopmentCheckout $gitProject $git).State -eq 'unknown') 'Corrupt checkout invented revision metadata'
    }
    Assert-Throws { Invoke-NativeDevelopmentCli @('exec','--project',$project,'--timeout','0','--',$shell) } '1-86400'
    Assert-Throws { Invoke-NativeDevelopmentCli @('exec','--project',$project,'--env','LOCALAPPDATA=elsewhere','--',$shell) } 'cannot be overridden'
    Assert-Throws { Invoke-NativeDevelopmentCli @('exec','--project',$project,'--what','--',$shell) } 'Unexpected option'
    $record = Invoke-NativeDevelopmentCli (@('exec','--project',$project,'--env','TASK_ONLY=child value','--msvc','--',$shell) + $literal)
    $directory = Get-DevelopmentDirectory $record.Id
    Assert-True ((Get-DevelopmentStatus $record.Id).State -eq 'queued') 'Fresh UTC task appeared stale in the local time zone'
    $request = Get-Content -LiteralPath (Join-Path $directory 'request.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-True ($request.Environment.BASALTWATER_PROVIDER_FIXTURE -eq 'provider shell value') 'Provider environment lost'
    Assert-True ($request.Environment.TASK_ONLY -eq 'child value' -and $request.Msvc) 'Task options lost'
    Assert-True ($request.Project -eq $project) 'Task cwd changed'
    Assert-True ($request.Argv[1] -ceq $unicode) 'Stored Unicode argv changed'
    Assert-Throws { Assert-NativeDevelopmentIdle } 'before setup/reboot'
    $guardian = [IO.File]::Open((Join-Path $directory 'guardian.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    try { $cancel = Invoke-NativeDevelopmentCli @('cancel',$record.Id) }
    finally { $guardian.Dispose() }
    Assert-True ($cancel.CancelRequested -and (Test-Path -LiteralPath (Join-Path $directory 'cancel'))) 'Cancel request lost'
    Assert-True (-not (Get-DevelopmentRecord $record.Id).CompletedUtc) 'Cancel claimed cleanup before guardian settlement'
    [IO.File]::WriteAllText((Join-Path $directory 'stdout.log'), ('x' * 100000))
    [IO.File]::WriteAllText((Join-Path $directory 'stderr.log'), 'failure detail')
    $logs = Invoke-NativeDevelopmentCli @('logs',$record.Id)
    Assert-True ($logs.Stdout.Length -eq 65536 -and $logs.Stderr -eq 'failure detail') 'Log response was not bounded'
    Assert-True ($logs.Tails.stdout.OmittedBytes -eq 34464 -and $logs.Tails.stdout.Truncated -and
        $logs.Tails.stderr.OmittedBytes -eq 0 -and -not $logs.Tails.stderr.Truncated -and $null -eq $logs.Streams) 'Tail omission or unknown retention was misreported'
    $record.WorkerPid = 999999; $record.WorkerStartedUtc = '2000-01-01T00:00:00Z'
    Write-DevelopmentJson (Join-Path $directory 'receipt.json') $record
    Assert-True ((Get-DevelopmentStatus $record.Id).State -eq 'cleanup-uncertain') 'Dead guardian falsely reported running'
    Assert-True ($null -eq (Get-DevelopmentStatus $record.Id).ElapsedSeconds) 'Dead guardian invented elapsed time'
    $record.CompletedUtc = [DateTime]::UtcNow.ToString('o'); $record.State = 'cancelled'
    Write-DevelopmentJson (Join-Path $directory 'receipt.json') $record
    Assert-NativeDevelopmentIdle
    & {
        $legacyPath = Join-Path $directory 'receipt.json'
        $legacy = Get-DevelopmentRecord $record.Id
        foreach ($name in @('ExecutionStartedUtc','ExecutionEndedUtc','ElapsedSeconds','Streams','StreamLimitBytes')) { $legacy.PSObject.Properties.Remove($name) }
        Write-DevelopmentJson $legacyPath $legacy
        $before = [IO.File]::ReadAllText($legacyPath)
        $old = Get-DevelopmentStatus $record.Id
        Assert-True (-not $old.PSObject.Properties['ElapsedSeconds'] -and [IO.File]::ReadAllText($legacyPath) -ceq $before) 'Legacy status invented timing or changed stored history'
        [IO.File]::WriteAllText($legacyPath, '{broken')
        Assert-Throws { Get-DevelopmentStatus $record.Id } 'JSON|Unexpected|Invalid'
        Assert-True ([IO.File]::ReadAllText($legacyPath) -eq '{broken') 'Corrupt record was overwritten'
        Write-DevelopmentJson $legacyPath $record
    }
    $script:launchFailure = $true
    Assert-Throws { Invoke-NativeDevelopmentCli @('exec','--project',$project,'--',$shell) } 'Worker launch could not be confirmed'
    $failed = Get-DevelopmentRecord $script:launched.Id
    Assert-True ($failed.State -eq 'queued' -and -not $failed.CompletedUtc) 'Launch failure falsely claimed running or settled'
    Assert-Throws { Assert-NativeDevelopmentIdle } 'queued'
    $script:RetireFailure = $true
    Assert-Throws { Invoke-NativeDevelopmentCli @('cancel',$failed.Id) } 'mock retirement failure'
    Assert-True (-not (Get-DevelopmentRecord $failed.Id).CompletedUtc) 'Failed retirement published terminal cleanup'
    $script:RetireFailure = $false
    $cancelled = Invoke-NativeDevelopmentCli @('cancel',$failed.Id)
    Assert-True ($cancelled.State -eq 'cancelled' -and (Get-DevelopmentRecord $failed.Id).CompletedUtc -and
        -not (Test-Path -LiteralPath (Join-Path (Get-DevelopmentDirectory $failed.Id) 'request.json'))) 'Unstarted task could not be safely cancelled'
    Assert-NativeDevelopmentIdle
    $script:launchFailure = $false
    foreach ($state in @('running','complete')) {
        $script:PublishedState = $state
        Assert-Throws { Invoke-NativeDevelopmentCli @('exec','--project',$project,'--',$shell) } 'Worker launch could not be confirmed'
        $owned = Get-DevelopmentRecord $script:launched.Id
        Assert-True ($owned.State -eq $state -and $owned.WorkerPid -eq 12345 -and $owned.WorkerStartedUtc -eq 'fixture worker') 'Dispatcher overwrote worker-owned state'
        if ($state -eq 'running') {
            Assert-Throws { Invoke-NativeDevelopmentCli @('cancel',$owned.Id) } 'reboot Windows'
            Assert-Throws { Assert-NativeDevelopmentIdle } 'cleanup-uncertain'
            $script:FixtureBoot = '639210000000000000'
            Assert-NativeDevelopmentIdle
            $settled = Get-DevelopmentRecord $owned.Id
            Assert-True ($settled.State -eq 'interrupted' -and $settled.CompletedUtc -and
                -not (Test-Path -LiteralPath (Join-Path (Get-DevelopmentDirectory $owned.Id) 'request.json'))) 'Boot recovery did not retire abandoned work and private environment'
        } else { Assert-True ($owned.CompletedUtc -and $owned.ExitCode -eq 0) 'Dispatcher erased worker completion' }
    }
    $script:PublishedState = ''
    Assert-Throws { Invoke-NativeDevelopmentCli @('logs','..') } 'task ID'
    # Only installed fnm trees are eligible; no system Node fallback or global runtime selection.
    Set-Content -LiteralPath (Join-Path $project '.node-version') -Value '22.9.0'
    Assert-Throws { Resolve-DevelopmentNode $project '' } 'not installed under fnm'
    $installation = Join-Path $env:FNM_DIR 'node-versions\v22.9.0\installation'
    New-Item -ItemType Directory -Path (Join-Path $installation 'node_modules\npm\bin') -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $installation 'node.exe'), 'fixture, never executed')
    [IO.File]::WriteAllText((Join-Path $installation 'node_modules\npm\bin\npm-cli.js'), 'fixture')
    [IO.File]::WriteAllText((Join-Path $installation 'node_modules\npm\package.json'), '{"version":"10.9.0"}')
    [IO.File]::WriteAllText((Join-Path $project 'package.json'), '{"scripts":{"build":"fixture"},"packageManager":"npm@10.9.0"}')
    $nodePlan = Invoke-NativeDevelopmentCli @('node','build','--project',$project,'--dry-run','--',$unicode)
    Assert-True ($nodePlan.Executable -eq (Join-Path $installation 'node.exe')) 'Selected incompatible system Node'
    Assert-True ($nodePlan.Argv[-1] -ceq $unicode -and $nodePlan.Environment.PATH.StartsWith($installation + ';')) 'Node child arguments/environment lost'
    Assert-Throws { Resolve-DevelopmentNode $project '24.0.0' } 'conflicts'
    Set-Content -LiteralPath (Join-Path $project '.nvmrc') -Value '24'
    Assert-Throws { Resolve-DevelopmentNode $project '' } 'Conflicting'
    # Cleanup ordering is shared by failure, cancel and timeout. Terminal publication follows this call.
    $script:events = @()
    $job = [pscustomobject]@{}
    $job | Add-Member ScriptMethod TerminateAndWait { param($Timeout); $script:events += 'tree settled' }
    $copy = [pscustomobject]@{}
    $copy | Add-Member ScriptMethod Wait { param($Timeout); $script:events += 'logs settled'; return $true }
    Stop-DevelopmentProcess $job $null $true @($copy)
    Assert-True (($script:events -join ',') -eq 'tree settled,logs settled') 'Cleanup did not settle tree before output'
    $job | Add-Member ScriptMethod TerminateAndWait { param($Timeout); throw 'mock cleanup uncertain' } -Force
    Assert-Throws { Stop-DevelopmentProcess $job $null $true @($copy) } 'mock cleanup uncertain'
    $process = [pscustomobject]@{ HasExited = $false }
    $process | Add-Member ScriptMethod Kill { $script:events += 'blocked killed' }
    $process | Add-Member ScriptMethod WaitForExit { param($Timeout); $script:events += 'blocked settled'; return $true }
    Stop-DevelopmentProcess $null $process $true @()
    Assert-True ($script:events[-1] -eq 'blocked settled') 'Containment failure returned before child exit'
    . (Join-Path $PSScriptRoot '..\Development.Process.ps1')
    Import-DevelopmentProcessType
    $inputStream = [IO.MemoryStream]::new([Text.Encoding]::UTF8.GetBytes(('z' * 10000)))
    $bounded = Join-Path $temporary 'bounded.log'
    try {
        $drain = [Basaltwater.DevelopmentStreams]::Drain($inputStream, $bounded, 1024).GetAwaiter().GetResult()
        Assert-True ((Get-Item -LiteralPath $bounded).Length -eq 1024 -and $inputStream.Position -eq 10000) 'Stream cap did not drain discarded output'
        Assert-True ($drain.RetainedBytes -eq 1024 -and $drain.DiscardedBytes -eq 8976 -and $drain.Truncated -and $drain.LimitBytes -eq 1024) 'Drain accounting lost bytes'
    } finally { $inputStream.Dispose() }
    foreach ($file in @('Development.Common.ps1','Development.Process.ps1','Invoke-DevelopmentTask.ps1')) {
        $tokens = $null; $errors = $null
        $null = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot ('..\' + $file)), [ref]$tokens, [ref]$errors)
        Assert-True ($errors.Count -eq 0) "PowerShell parse failed: $file $errors"
    }
    Write-Host 'Development task offline contracts passed (Scheduler/system calls mocked; no live installation).'
} finally {
    $env:LOCALAPPDATA = $savedLocal; $env:FNM_DIR = $savedFnm; $env:BASALTWATER_PROVIDER_FIXTURE = $savedProvider
    $resolved = [IO.Path]::GetFullPath($temporary)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe fixture cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
