<# Detached refresh dispatch/lifecycle tests; Scheduler and setup are mocked. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
$source = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
. (Join-Path $source 'windows\AgentWorkstation.Common.ps1')
. (Join-Path $source 'windows\Refresh.Common.ps1')
. (Join-Path $source 'windows\RefreshProgress.Common.ps1')
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-refresh-' + [guid]::NewGuid().ToString('N'))
$savedLocal = $env:LOCALAPPDATA
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $env:LOCALAPPDATA = $temporary
    $saved = [pscustomobject]@{ Source = $source; OwnerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value }
    function Read-NativeCliSetup { return $saved }
    function New-ScheduledTaskAction($Execute, $Argument) { return @{ Execute = $Execute; Arguments = $Argument } }
    function New-ScheduledTaskPrincipal($UserId, $LogonType, $RunLevel) { return @{ UserId = $UserId; LogonType = $LogonType; RunLevel = $RunLevel } }
    function New-ScheduledTaskSettingsSet($ExecutionTimeLimit, [switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries) { return @{ Limit = $ExecutionTimeLimit } }
    function Register-ScheduledTask($TaskName, $TaskPath, $Action, $Principal, $Settings, [switch]$Force) { $script:Task = @{ Action = $Action; Principal = $Principal } }
    function Start-ScheduledTask($TaskName, $TaskPath) {
        if ($script:WorkerStartedBeforeFailure) {
            $id = $TaskName.Substring('Basaltwater-Refresh-'.Length)
            $running = Read-NativeRefreshReceipt $id
            $running.Status = 'running'; $running.WorkerPid = $PID
            $self = Get-Process -Id $PID
            try { $running.WorkerStartedUtc = $self.StartTime.ToUniversalTime().ToString('o') }
            finally { $self.Dispose() }
            Write-NativeRefreshReceipt $running
            $script:DispatchClaim = [IO.File]::Open((Join-Path (Get-NativeRefreshDirectory $id) 'claim.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
            $script:DispatchId = $id
            throw 'fixture ambiguous dispatch failure'
        }
        if ($script:DispatchFailure) { throw 'fixture dispatch failure' }
    }
    function Unregister-ScheduledTask($TaskName, $TaskPath, [switch]$Confirm) { }
    function Show-NativeDesktopNotification { }
    function Start-Process($FilePath, $ArgumentList, $WindowStyle) {
        $script:ProgressLaunch = @{ File = $FilePath; Arguments = $ArgumentList; WindowStyle = $WindowStyle }
    }
    function Get-NativeRefreshBoot { return $script:Boot }
    function Invoke-NativePowerShell7Installer($Shell, $Installer, $Options) {
        $script:Received = $Options
        Write-Host 'fixture setup output'
        Write-Host 'fixture second line'
        if ($script:Failure) { throw 'private fixture failure' }
    }
    $script:DispatchFailure = $false; $script:Failure = $false; $script:Received = $null
    $script:Boot = '639200000000000000'
    $script:WorkerStartedBeforeFailure = $false; $script:DispatchClaim = $null
    $storageRoot = "F:\cache & quote's " + [char]0x03bb
    $argsForRefresh = @('-Profile','agent_windows','-Resume','-UpdateSource','-NoRestartT3','-NoReboot','-StorageRoot',$storageRoot)
    $job = Start-NativeRefresh $saved $source $argsForRefresh
    Assert-True ($job.Status -eq 'queued' -and $script:Task.Principal.RunLevel -eq 'Limited' -and
        $script:Task.Action.Arguments -match '-WindowStyle Hidden') 'Refresh did not detach as a hidden limited-account task'
    $window = New-NativeRefreshProgressWindow
    try {
        Assert-True (-not (Update-NativeRefreshProgressWindow $window $job.Id)) 'Queued progress falsely settled'
        Assert-True ($window.Status.Text -match 'queued' -and $window.Summary.Text -match 'Waiting') 'Queued progress did not explain the wait'
    } finally { $window.Form.Dispose() }
    $blocked = $false
    try { Start-NativeRefresh $saved $source $argsForRefresh | Out-Null } catch { $blocked = $_.Exception.Message -match 'still pending' }
    Assert-True $blocked 'Concurrent refreshes were dispatched'
    # PowerShell 7 state has no BOM; the PowerShell 5.1 dispatcher must read UTF-8.
    $receiptPath = Join-Path (Get-NativeRefreshDirectory $job.Id) 'receipt.json'
    [IO.File]::WriteAllText($receiptPath, [IO.File]::ReadAllText($receiptPath), [Text.UTF8Encoding]::new($false))
    # Keep a progress reader open across a real NTFS receipt replacement. It
    # must retain valid old JSON while new readers see the complete new receipt.
    $snapshot = [IO.File]::Open($receiptPath, 'Open', 'Read', ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
    $snapshotReader = New-Object IO.StreamReader($snapshot, [Text.Encoding]::UTF8)
    try {
        $receipt = Read-NativeRefreshReceipt $job.Id
        $receipt | Add-Member NoteProperty ProgressFixture 'updated'
        Write-NativeRefreshReceipt $receipt
        Assert-True (-not ($snapshotReader.ReadToEnd() | ConvertFrom-Json).PSObject.Properties['ProgressFixture'] -and
            (Read-NativeRefreshReceipt $job.Id).ProgressFixture -eq 'updated') 'Concurrent reader/writer saw partial JSON or blocked receipt replacement'
    } finally { $snapshotReader.Dispose() }
    & {
        # Model an older reader denying delete sharing; release it at the first
        # retry, without sleeps or timing-dependent overlap.
        $held = [IO.File]::OpenRead($receiptPath)
        $script:ReceiptRetry = $false
        function Start-Sleep($Milliseconds) {
            Assert-True ($Milliseconds -eq 50) 'Receipt retry was unbounded'
            $script:ReceiptRetry = $true
            $held.Dispose()
        }
        try { Write-NativeRefreshReceipt (Read-NativeRefreshReceipt $job.Id) }
        finally { $held.Dispose() }
        Assert-True $script:ReceiptRetry 'Receipt replacement did not tolerate an overlapping legacy reader'
    }
    Invoke-NativeRefreshWorker $job.Id
    $receipt = Read-NativeRefreshReceipt $job.Id
    Assert-True ($receipt.Status -eq 'complete' -and $script:Received.NoRestartT3 -and $script:Received.NoReboot -and $script:Received.StorageRoot -ceq $storageRoot) 'Refresh worker lost typed options, Unicode storage paths or completion'
    Assert-True ((Get-Content -LiteralPath $receipt.Log -Raw) -match 'fixture setup output') 'Detached refresh omitted output logs'
    Assert-True ($script:ProgressLaunch.WindowStyle -eq 'Hidden') 'Progress helper opened a console alongside its GUI'
    $progressCommand = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String(($script:ProgressLaunch.Arguments -split ' ')[-1]))
    Assert-True ($progressCommand.Contains('Show-RefreshProgress.ps1') -and $progressCommand.Contains($job.Id)) 'Worker did not open progress for its own job'
    $window = New-NativeRefreshProgressWindow
    try {
        Assert-True (Update-NativeRefreshProgressWindow $window $job.Id) 'Completed progress window did not settle'
        Assert-True ($window.Status.Text -match 'complete' -and $window.Summary.Text -match 'successfully' -and
            $window.Output.Text -match 'fixture second line' -and $window.Progress.Text -match 'Elapsed: \d+s') 'Progress omitted output, elapsed time or completion summary'
        Assert-True ((Get-NativeRefreshElapsed ([pscustomobject]@{ StartedUtc = '2026-10-30T11:00:00Z'; CompletedUtc = '2026-10-30T11:01:07Z' })) -eq 67) 'Reopened progress showed monitor uptime instead of job duration'
        $follow = Watch-NativeRefresh $job.Id 6>&1
        Assert-True (@($follow | Where-Object { $_ -is [int] -and $_ -eq 0 }).Count -eq 1 -and
            ($follow | Out-String) -match 'fixture second line') 'Following a completed job lost output or exit status'
        $failedView = [pscustomobject]@{ Status = 'cleanup-uncertain' }
        Assert-True ((Get-NativeRefreshSummary $failedView) -match 'installer may still be running') 'Uncertain progress encouraged a blind retry'
    } finally { $window.Form.Dispose() }
    $script:Failure = $true
    $job = Start-NativeRefresh $saved $source $argsForRefresh
    Invoke-NativeRefreshWorker $job.Id
    $receipt = Read-NativeRefreshReceipt $job.Id
    Assert-True ($receipt.Status -eq 'failed' -and ($receipt | ConvertTo-Json) -notmatch 'private fixture failure') 'Worker failure was hidden or leaked vendor detail into receipt'
    $follow = Watch-NativeRefresh $job.Id 6>&1
    Assert-True (@($follow | Where-Object { $_ -is [int] -and $_ -eq 1 }).Count -eq 1) 'Following failed refresh returned success'
    $script:DispatchFailure = $true
    $failed = $false
    try { Start-NativeRefresh $saved $source $argsForRefresh | Out-Null } catch { $failed = $true }
    Assert-True $failed 'Scheduler dispatch failure was hidden'
    $all = @(Get-ChildItem -LiteralPath (Join-Path $temporary 'Basaltwater\refresh') -Filter receipt.json -Recurse | ForEach-Object { Get-Content $_.FullName -Raw | ConvertFrom-Json })
    Assert-True (@($all | Where-Object Status -eq 'dispatch-failed').Count -eq 1) 'Dispatch failure left a queued receipt'
    $script:DispatchFailure = $false
    $script:WorkerStartedBeforeFailure = $true
    try {
        $failed = $false
        try { Start-NativeRefresh $saved $source $argsForRefresh | Out-Null } catch { $failed = $_.Exception.Message -match 'ambiguous dispatch failure' }
        $running = Get-NativeRefreshStatus $script:DispatchId
        Assert-True ($failed -and $running.Status -eq 'running' -and -not $running.CompletedUtc) 'Dispatch error replaced an active worker receipt with terminal failure'
    } finally { $script:DispatchClaim.Dispose(); $script:WorkerStartedBeforeFailure = $false }
    $running.Status = 'complete'; $running.CompletedUtc = [datetime]::UtcNow.ToString('o')
    Write-NativeRefreshReceipt $running
    $job = Start-NativeRefresh $saved $source $argsForRefresh
    Stop-NativeQueuedRefresh $job.Id | Out-Null
    Assert-True ((Read-NativeRefreshReceipt $job.Id).Status -eq 'cancelled') 'Queued refresh cancellation was not durable'
    # A dead guardian cannot establish whether its installer is still running.
    $job = Start-NativeRefresh $saved $source $argsForRefresh
    $orphan = Read-NativeRefreshReceipt $job.Id
    $orphan.Status = 'running'; $orphan.WorkerPid = 999999; $orphan.WorkerStartedUtc = '2000-01-01T00:00:00Z'
    Write-NativeRefreshReceipt $orphan
    Assert-True ((Get-NativeRefreshStatus $job.Id).Status -eq 'cleanup-uncertain') 'Dead worker falsely reported running'
    $held = [IO.File]::Open((Join-Path $temporary 'Basaltwater\setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    try {
        $blocked = $false
        try { Start-NativeRefresh $saved $source $argsForRefresh | Out-Null } catch { $blocked = $_.Exception.Message -match 'installer may still be running' }
        Assert-True $blocked 'Worker death permitted overlapping setup'
        $script:Boot = '639210000000000000'
        $blocked = $false
        try { Start-NativeRefresh $saved $source $argsForRefresh | Out-Null } catch { $blocked = $true }
        Assert-True ($blocked -and (Read-NativeRefreshReceipt $job.Id).Status -eq 'running') 'Reboot reconciliation raced active setup recovery'
    } finally { $held.Dispose() }
    $claim = [IO.File]::Open((Join-Path (Get-NativeRefreshDirectory $job.Id) 'claim.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    try {
        $blocked = $false
        try { Start-NativeRefresh $saved $source $argsForRefresh | Out-Null } catch { $blocked = $true }
        Assert-True $blocked 'Reconciliation raced a claimed worker'
    } finally { $claim.Dispose() }
    $script:Received = $null
    $replacement = Start-NativeRefresh $saved $source $argsForRefresh
    $settled = Read-NativeRefreshReceipt $job.Id
    Assert-True ($settled.Status -eq 'interrupted' -and $settled.CompletedUtc -and $null -eq $script:Received) 'Reboot recovery replayed setup or left an orphan blocking refresh'
    Stop-NativeQueuedRefresh $replacement.Id | Out-Null
    # Legacy receipts also recover after a boot; queued requests must never replay.
    $old = Start-NativeRefresh $saved $source $argsForRefresh
    $legacy = Read-NativeRefreshReceipt $old.Id
    $legacy.PSObject.Properties.Remove('Boot')
    Write-NativeRefreshReceipt $legacy
    $legacyPath = Join-Path (Get-NativeRefreshDirectory $old.Id) 'receipt.json'
    [IO.File]::SetLastWriteTimeUtc($legacyPath, [datetime]::new(([long]$script:Boot - 10000), [DateTimeKind]::Utc))
    $blocked = $false
    try { Invoke-NativeRefreshWorker $old.Id } catch { $blocked = $_.Exception.Message -match 'queued before reboot' }
    Assert-True $blocked 'Old queued refresh was replayed after login'
    $replacement = Start-NativeRefresh $saved $source $argsForRefresh
    Assert-True ((Read-NativeRefreshReceipt $old.Id).Status -eq 'interrupted') 'Legacy orphan still blocks detached refresh'
    Stop-NativeQueuedRefresh $replacement.Id | Out-Null
    Write-Host 'Native detached refresh tests passed.'
} finally {
    $env:LOCALAPPDATA = $savedLocal
    $resolved = [IO.Path]::GetFullPath($temporary)
    if (-not $resolved.StartsWith([IO.Path]::GetTempPath(), [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test cleanup' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
