<# Detached refresh dispatch/lifecycle tests; Scheduler and setup are mocked. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
$source = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
. (Join-Path $source 'windows\AgentWorkstation.Common.ps1')
. (Join-Path $source 'windows\Refresh.Common.ps1')
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
    function Start-ScheduledTask($TaskName, $TaskPath) { if ($script:DispatchFailure) { throw 'fixture dispatch failure' } }
    function Unregister-ScheduledTask($TaskName, $TaskPath, [switch]$Confirm) { }
    function Show-NativeDesktopNotification { }
    function Invoke-NativePowerShell7Installer($Shell, $Installer, $Options) {
        $script:Received = $Options
        Write-Host 'fixture setup output'
        if ($script:Failure) { throw 'private fixture failure' }
    }
    $script:DispatchFailure = $false; $script:Failure = $false; $script:Received = $null
    $storageRoot = "F:\cache & quote's " + [char]0x03bb
    $argsForRefresh = @('-Profile','agent_windows','-Resume','-UpdateSource','-NoReboot','-StorageRoot',$storageRoot)
    $job = Start-NativeRefresh $saved $source $argsForRefresh
    Assert-True ($job.Status -eq 'queued' -and $script:Task.Principal.RunLevel -eq 'Limited' -and
        $script:Task.Action.Arguments -match '-WindowStyle Hidden') 'Refresh did not detach as a hidden limited-account task'
    $blocked = $false
    try { Start-NativeRefresh $saved $source $argsForRefresh | Out-Null } catch { $blocked = $_.Exception.Message -match 'still pending' }
    Assert-True $blocked 'Concurrent refreshes were dispatched'
    # PowerShell 7 state has no BOM; the PowerShell 5.1 dispatcher must read UTF-8.
    $receiptPath = Join-Path (Get-NativeRefreshDirectory $job.Id) 'receipt.json'
    [IO.File]::WriteAllText($receiptPath, [IO.File]::ReadAllText($receiptPath), [Text.UTF8Encoding]::new($false))
    Invoke-NativeRefreshWorker $job.Id
    $receipt = Read-NativeRefreshReceipt $job.Id
    Assert-True ($receipt.Status -eq 'complete' -and $script:Received.NoReboot -and $script:Received.StorageRoot -ceq $storageRoot) 'Refresh worker lost typed options, Unicode storage paths or completion'
    Assert-True ((Get-Content -LiteralPath $receipt.Log -Raw) -match 'fixture setup output') 'Detached refresh omitted output logs'
    $script:Failure = $true
    $job = Start-NativeRefresh $saved $source $argsForRefresh
    Invoke-NativeRefreshWorker $job.Id
    $receipt = Read-NativeRefreshReceipt $job.Id
    Assert-True ($receipt.Status -eq 'failed' -and ($receipt | ConvertTo-Json) -notmatch 'private fixture failure') 'Worker failure was hidden or leaked vendor detail into receipt'
    $script:DispatchFailure = $true
    $failed = $false
    try { Start-NativeRefresh $saved $source $argsForRefresh | Out-Null } catch { $failed = $true }
    Assert-True $failed 'Scheduler dispatch failure was hidden'
    $all = @(Get-ChildItem -LiteralPath (Join-Path $temporary 'Basaltwater\refresh') -Filter receipt.json -Recurse | ForEach-Object { Get-Content $_.FullName -Raw | ConvertFrom-Json })
    Assert-True (@($all | Where-Object Status -eq 'dispatch-failed').Count -eq 1) 'Dispatch failure left a queued receipt'
    $script:DispatchFailure = $false
    $job = Start-NativeRefresh $saved $source $argsForRefresh
    Stop-NativeQueuedRefresh $job.Id | Out-Null
    Assert-True ((Read-NativeRefreshReceipt $job.Id).Status -eq 'cancelled') 'Queued refresh cancellation was not durable'
    Write-Host 'Native detached refresh tests passed.'
} finally {
    $env:LOCALAPPDATA = $savedLocal
    $resolved = [IO.Path]::GetFullPath($temporary)
    if (-not $resolved.StartsWith([IO.Path]::GetTempPath(), [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test cleanup' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
