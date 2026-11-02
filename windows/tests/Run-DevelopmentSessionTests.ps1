<# Session contracts use temporary files and mocked discovery/Scheduler calls. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '..\Development.Common.ps1')
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Assert-Throws([scriptblock]$Action, [string]$Pattern) {
    $caught = $null
    try { & $Action | Out-Null } catch { $caught = $_ }
    Assert-True ($null -ne $caught -and $caught.Exception.Message -match $Pattern) "Expected $Pattern; got $caught"
}
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-sessions-' + [guid]::NewGuid().ToString('N'))
$savedLocal = $env:LOCALAPPDATA
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $env:LOCALAPPDATA = Join-Path $temporary 'account'
    $project = Join-Path $temporary 'project spaces'; New-Item -ItemType Directory -Path $project | Out-Null
    $other = Join-Path $temporary 'other'; New-Item -ItemType Directory -Path $other | Out-Null
    $shell = (Get-Process -Id $PID).Path
    function Get-NativePowerShell7 { return $shell }
    function Get-DevelopmentBoot { return '639200000000000000' }
    function Get-DevelopmentTrustedGit { return '' }
    function Get-DevelopmentEnvironment { return [pscustomobject]@{ State = 'partial'; Graphics = @(@{ Name = 'Fixture GPU'; DriverVersion = '1.2' }); Displays = $null } }
    function Get-DevelopmentCheckout($Project, $Git) { return [pscustomobject]@{ State = 'available'; Revision = ('a' * 40); Dirty = $false } }
    function Get-ScheduledTask($TaskPath) { }
    function Start-DevelopmentScheduledTask($Record, $PowerShell) { $script:Dispatched = $Record.Id }
    $script:Dispatched = ''
    $arguments = @('exec','--project',$project,'--test-session','--ready-timeout','10','--',$shell,'-NoProfile')
    $dry = Invoke-NativeDevelopmentCli (@('exec','--project',$project,'--test-session','--dry-run','--',$shell))
    Assert-True ($dry.TestSession -and $dry.ReadyTimeoutSeconds -eq 30 -and -not (Test-Path -LiteralPath (Get-DevelopmentRoot))) 'Session dry run wrote files or hid defaults'
    foreach ($invalid in @(
        @('doctor','--project',$project,'--test-session'),
        @('exec','--project',$project,'--ready-timeout','2','--',$shell),
        @('exec','--project',$project,'--test-session','--ready-timeout','301','--',$shell),
        @('exec','--project',$project,'--env','BASALTWATER_TEST_PROFILE=other','--',$shell)
    )) { Assert-Throws { Invoke-NativeDevelopmentCli $invalid } 'test-session|1-300|reserved' }
    $record = Invoke-NativeDevelopmentCli $arguments
    $directory = Get-DevelopmentDirectory $record.Id
    Assert-True ($script:Dispatched -eq $record.Id -and (Test-Path -LiteralPath $record.TestSession.ProfilePath)) 'Session not prepared before dispatch'
    $request = Read-DevelopmentJson (Join-Path $directory 'request.json')
    Assert-True ($request.Environment.BASALTWATER_TEST_PROFILE -ceq $record.TestSession.ProfilePath -and
        $request.Environment.BASALTWATER_TEST_READY_TOKEN -ceq $record.TestSession.ReadyToken -and
        -not $env:BASALTWATER_TEST_PROFILE) 'Session paths/token lost or leaked into caller'
    $evidence = Read-DevelopmentJson (Join-Path $directory 'session-evidence.json')
    Assert-True ($evidence.HostEnvironment.State -eq 'partial' -and $evidence.Graphics[0].Name -eq 'Fixture GPU' -and
        $null -eq $evidence.Display) 'Session lost available hardware metadata or hid partial collection'
    Assert-True ($evidence.Checkout.Revision -eq ('a' * 40) -and $evidence.Receipt -eq 'receipt.json' -and
        -not $evidence.PSObject.Properties['Environment'] -and -not $evidence.PSObject.Properties['Argv']) 'Evidence omitted provenance or retained environment/arguments'
    Assert-Throws { Invoke-NativeDevelopmentCli $arguments } 'Project reserved'
    Assert-Throws { Invoke-NativeDevelopmentCli @('exec','--project',$project,'--',$shell) } 'Project reserved'
    Assert-DevelopmentProjectAdmission $other $false
    Assert-DevelopmentProjectAdmission $other $true
    Update-DevelopmentSessionReadiness $record 1
    Assert-True ($record.TestSession.ReadyState -eq 'pending') 'Missing marker claimed ready'
    Write-DevelopmentJson $record.TestSession.ReadyFile @{ Token = 'wrong' }
    Assert-Throws { Update-DevelopmentSessionReadiness $record 1 } 'token mismatch'
    Write-DevelopmentJson $record.TestSession.ReadyFile @{ Token = @($record.TestSession.ReadyToken) }
    Assert-Throws { Update-DevelopmentSessionReadiness $record 1 } 'token mismatch'
    Write-DevelopmentJson $record.TestSession.ReadyFile @{ Token = $record.TestSession.ReadyToken }
    Update-DevelopmentSessionReadiness $record 1.5
    Assert-True ($record.TestSession.ReadyState -eq 'ready' -and $record.TestSession.ReadyUtc -and
        $record.TestSession.ReadyElapsedSeconds -eq 1.5) 'Readiness not published with timestamp'
    & {
        function Get-DevelopmentStatus($Id) { return $record }
        $record.State = 'running'
        Assert-True ((Invoke-NativeDevelopmentCli @('ready',$record.Id,'--timeout','1')).WaitState -eq 'ready') 'Ready wait ignored the live readiness receipt'
        $record.CompletedUtc = 'settled'
        Assert-True ((Invoke-NativeDevelopmentCli @('ready',$record.Id,'--timeout','1')).WaitState -eq 'completed') 'Exited session was presented as live'
        $record.CompletedUtc = $null; $record.State = 'queued'
        Assert-True ((Invoke-NativeDevelopmentCli @('ready',$record.Id,'--timeout','1')).TimedOut) 'Ready wait did not bound a queued session'
    }
    $record.TestSession.ReadyState = 'pending'
    Assert-Throws { Update-DevelopmentSessionReadiness $record 10 } 'deadline exceeded'
    Assert-True ($record.TestSession.ReadyState -eq 'timed-out') 'Readiness timeout not distinct'
    $record.TestSession.ReadyState = 'pending'
    [IO.File]::WriteAllText($record.TestSession.ReadyFile, ('x' * 1025))
    Assert-Throws { Update-DevelopmentSessionReadiness $record 1 } '1024 bytes'
    $profile = $record.TestSession.ProfilePath
    $record.TestSession.ProfilePath = $other
    Assert-Throws { Remove-DevelopmentSessionProfile $record } 'identity mismatch'
    $record.TestSession.ProfilePath = $profile
    $junction = Join-Path $profile 'redirect'
    New-Item -ItemType Junction -Path $junction -Target $other | Out-Null
    try {
        Assert-Throws { Remove-DevelopmentSessionProfile $record } 'reparse'
        Assert-Throws { Invoke-NativeDevelopmentCli @('cancel',$record.Id) } 'reparse'
        Assert-True (-not (Get-DevelopmentRecord $record.Id).CompletedUtc) 'Failed queued cleanup published terminal completion'
        Assert-Throws { Assert-DevelopmentProjectAdmission $project $true } 'Project reserved'
    }
    finally { [IO.Directory]::Delete($junction) }
    Assert-True ((Test-Path -LiteralPath $other) -and (Test-Path -LiteralPath $profile)) 'Cleanup traversed a junction'
    $cancel = Invoke-NativeDevelopmentCli @('cancel',$record.Id)
    $settled = Get-DevelopmentRecord $record.Id
    Assert-True ($cancel.State -eq 'cancelled' -and $settled.CompletedUtc -and
        $settled.TestSession.ProfileCleanup -eq 'removed' -and -not (Test-Path -LiteralPath $profile) -and
        (Test-Path -LiteralPath (Join-Path $directory 'session-evidence.json'))) 'Queued cancellation lost evidence or retained profile'
    Assert-DevelopmentProjectAdmission $project $true
    # Ordinary tasks reserve their project against a later session, including uncertainty.
    $ordinary = Invoke-NativeDevelopmentCli @('exec','--project',$project,'--',$shell)
    Assert-Throws { Invoke-NativeDevelopmentCli $arguments } 'Project reserved'
    $ordinary.WorkerPid = 999999; $ordinary.State = 'cleanup-uncertain'
    Write-DevelopmentJson (Join-Path (Get-DevelopmentDirectory $ordinary.Id) 'receipt.json') $ordinary
    Assert-Throws { Assert-DevelopmentProjectAdmission $project $true } 'Project reserved'
    $ordinary.PSObject.Properties.Remove('Project')
    Write-DevelopmentJson (Join-Path (Get-DevelopmentDirectory $ordinary.Id) 'receipt.json') $ordinary
    Assert-Throws { Assert-DevelopmentProjectAdmission $other $true } 'unknown project scope'
    $ordinary.CompletedUtc = [DateTime]::UtcNow.ToString('o')
    Write-DevelopmentJson (Join-Path (Get-DevelopmentDirectory $ordinary.Id) 'receipt.json') $ordinary
    & {
        function Get-DevelopmentCheckout { throw 'fixture evidence failure' }
        Assert-Throws { Invoke-NativeDevelopmentCli $arguments } 'preparation failed'
        $failed = @(Get-ChildItem -LiteralPath (Get-DevelopmentRoot) -Directory | ForEach-Object { Get-DevelopmentRecord $_.Name } | Where-Object Message -like '*fixture evidence failure*')
        Assert-True ($failed.Count -eq 1 -and $failed[0].State -eq 'failed' -and $failed[0].CompletedUtc -and
            -not (Test-Path -LiteralPath $failed[0].TestSession.ProfilePath)) 'Preparation failure leaked an untracked profile/reservation'
    }
    & {
        function Get-DevelopmentEnvironment { return [pscustomobject]@{ State = 'unavailable'; Graphics = $null; Displays = $null; Errors = @('fixture worker timed out') } }
        $withoutHardware = Invoke-NativeDevelopmentCli $arguments
        $index = Read-DevelopmentJson (Join-Path (Get-DevelopmentDirectory $withoutHardware.Id) 'session-evidence.json')
        Assert-True ($script:Dispatched -eq $withoutHardware.Id -and $index.HostEnvironment.State -eq 'unavailable' -and
            $index.HostEnvironment.Errors[0] -match 'timed out' -and $null -eq $index.Graphics) 'Unavailable optional hardware probe blocked launch or hid its error'
        $null = Invoke-NativeDevelopmentCli @('cancel',$withoutHardware.Id)
    }
    Write-Host 'Development session offline contracts passed.'
} finally {
    $env:LOCALAPPDATA = $savedLocal
    $resolved = [IO.Path]::GetFullPath($temporary)
    if (-not $resolved.StartsWith([IO.Path]::GetTempPath(), [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe session fixture cleanup' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
