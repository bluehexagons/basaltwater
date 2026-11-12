param([Parameter(Mandatory=$true)][string]$Directory)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Desktop.Common.ps1')
$record = $null; $result = $null; $failure = $null; $retired = $false; $validated=$false
try {
    Assert-NativeDesktopPrivatePath $Directory
    $record = Read-NativeHeldJson (Join-Path $Directory 'request.json')
    if ($Directory -cne (Resolve-NativeHeldDirectory $record.id)) { throw 'Invalid held-input directory identity' }
    Assert-NativeHeldRecord $record $record.id $Directory
    $validated=$true
    $context=Get-NativeDesktopContext
    if ($record.sid -cne $context.Sid -or $record.session -cne $context.Session) { throw 'Held-input interactive session changed' }
    $self=[Diagnostics.Process]::GetCurrentProcess()
    try { Write-NativeHeldJson (Join-Path $Directory 'worker.json') @{ pid=$self.Id; created=$self.StartTime.ToUniversalTime().Ticks; session=$self.SessionId } }
    finally { $self.Dispose() }
    Initialize-NativeDesktopTypes
    [Basaltwater.Desktop.Native]::Ready(); [Basaltwater.Desktop.Native]::Dpi()
    $plan=[Basaltwater.Desktop.HeldPlan]$record.plan
    $control=[Basaltwater.Desktop.HeldControlGuard]::new((Get-NativeDesktopStatePath),$Directory,$record.sid,$record.session,$record.revision,$record.generation,$plan)
    $check=[Delegate]::CreateDelegate([Func[string]],$control,'Check')
    $result=[Basaltwater.Desktop.Native]::WatchHold($plan,$Directory,$record.name,[uint64]$record.marker,[int]$record.caller_pid,[long]$record.caller_created,$check)
} catch { $failure=$_.Exception.Message }
finally {
    if ($record -and $validated) {
        Write-NativeHeldJson (Join-Path $Directory 'watchdog.json') @{ result=$result; error=$failure }
        try {
            Remove-NativeHeldScheduledTask $record.task; $retired=$true
        } catch { $failure='Scheduler retirement unconfirmed: ' + $_.Exception.Message }
        $unstarted=$false
        if (-not $result -and ('Basaltwater.Desktop.Native' -as [type])) {
            try { $unstarted=[Basaltwater.Desktop.Native]::SettleUnstartedHold($Directory,$record.name) } catch { }
        }
        $clean = $retired -and ($unstarted -or ($result -and $result.release_state -eq 'submitted'))
        Write-NativeHeldJson (Join-Path $Directory 'receipt.json') @{ version=1; lease=$record.id; cleanup_confirmed=[bool]$clean; scheduler_retired=$retired; watchdog=$result; error=$failure; completed_utc=[DateTime]::UtcNow.ToString('o') }
    }
}
