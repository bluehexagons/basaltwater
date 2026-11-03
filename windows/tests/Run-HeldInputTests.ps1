<# Offline ownership/cleanup tests: all input transport is replaced. #>
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '..\Desktop.Common.ps1')
. (Join-Path $PSScriptRoot '..\Desktop.Hold.ps1')
Initialize-NativeDesktopTypes
function Assert-Held([bool]$Value,[string]$Message) { if (-not $Value) { throw $Message } }
$temporary=Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-held-tests-' + [guid]::NewGuid().ToString('N'))
New-NativeDesktopPrivateDirectory $temporary
$flags=[Reflection.BindingFlags]'Static,NonPublic'
$press=[Basaltwater.Desktop.Native].GetMethod('HeldPress',$flags)
$release=[Basaltwater.Desktop.Native].GetMethod('HeldRelease',$flags)
$plan=[Basaltwater.Desktop.HeldPlan]@{ window=123; identity='fixture'; width=640; height=480; keys=@(
    @{vk=162; scan=29; flags=8}, @{vk=160; scan=42; flags=8}, @{vk=36; scan=71; flags=9}) }
$marker=[UIntPtr]::new([uint64]1234)
try {
    $physical=[Basaltwater.Desktop.Native].GetMethod('IsHeldPhysicalKey',$flags)
    Assert-Held ([bool]$physical.Invoke($null,@($plan.keys[1],[uint32]16,[uint32]42,[uint32]0))) 'Generic modifier lost physical ownership'
    Assert-Held (-not [bool]$physical.Invoke($null,@($plan.keys[2],[uint32]103,[uint32]71,[uint32]0))) 'Keypad key confused with extended navigation ownership'
    $repeatStop=[Basaltwater.Desktop.Native].GetMethod('HeldRepeatStop',$flags)
    $frequency=[Diagnostics.Stopwatch]::Frequency
    Assert-Held ($repeatStop.Invoke($null,@((2*$frequency),$frequency,(2*$frequency),$frequency)) -eq 'expired') 'A repeat could send after lease expiry'
    Assert-Held ($repeatStop.Invoke($null,@([long]($frequency+$frequency/5),$frequency,(3*$frequency),$frequency)) -eq 'schedule-late') 'A repeat could send after a slow guard'
    Assert-Held ($repeatStop.Invoke($null,@([long]($frequency+$frequency/20),$frequency,(3*$frequency),[long]($frequency/20))) -eq 'schedule-late') 'Missed repeat intervals could catch up in a burst'
    foreach ($case in @('complete','partial','rejected','release-failed','physical-owner','crashed-intent')) {
        $dir=Join-Path $temporary $case; New-NativeDesktopPrivateDirectory $dir
        [Basaltwater.Desktop.HeldStore]::Create($dir)
        $store=[Basaltwater.Desktop.HeldStore]::new($dir,('Local\Basaltwater.HeldTest.'+[guid]::NewGuid().ToString('N')))
        $events=[Threading.EventWaitHandle[]]@(
            (New-Object Threading.EventWaitHandle($false,'ManualReset')),
            (New-Object Threading.EventWaitHandle($false,'ManualReset')),
            (New-Object Threading.EventWaitHandle($false,'ManualReset')))
        try {
            $script:HeldTestBatches=@(); $script:HeldTestCase=$case
            $send=[Management.Automation.LanguagePrimitives]::ConvertTo({ param($batch)
                $script:HeldTestBatches+=,$batch
                if ($script:HeldTestBatches.Count -eq 1) {
                    if ($script:HeldTestCase -eq 'partial') { return [uint32]2 }
                    if ($script:HeldTestCase -eq 'rejected') { return [uint32]0 }
                    if ($script:HeldTestCase -eq 'crashed-intent') { throw 'fixture crash after durable intent' }
                }
                if ($script:HeldTestCase -eq 'release-failed' -and $script:HeldTestBatches.Count -eq 2) { return [uint32]1 }
                return [uint32]$batch.Length
            },$press.GetParameters()[6].ParameterType)
            $heartbeat=[Func[Basaltwater.Desktop.HeldStore+State,int]]{ param($s) $s.heartbeat=[Diagnostics.Stopwatch]::GetTimestamp(); return 0 }
            $null=$store.GetType().GetMethod('Locked').MakeGenericMethod([int]).Invoke($store,@($heartbeat))
            $receipt=[Basaltwater.Desktop.HeldReceipt]::new()
            $now=[Diagnostics.Stopwatch]::GetTimestamp(); $caught=$null
            try { $null=$press.Invoke($null,@($store,$plan,$marker,$receipt,$now,($now+[Diagnostics.Stopwatch]::Frequency),$send)) } catch { $caught=$_ }
            Assert-Held (($null -ne $caught) -eq ($case -in @('partial','rejected','crashed-intent'))) "Unexpected down result: $case $caught"
            if ($case -eq 'physical-owner') { $null=$events[2].Set() }
            $null=$release.Invoke($null,@($store,$plan,$events,$marker,$receipt,'fixture-stop',$send))
            $expectedCount=if ($case -eq 'rejected') { 1 } else { 2 }
            Assert-Held ($script:HeldTestBatches.Count -eq $expectedCount) "Down was replayed or unrelated release sent: $case"
            if ($case -ne 'rejected') {
                $up=$script:HeldTestBatches[1]
                Assert-Held (@($up | Where-Object { ($_.data.key.flags -band 2) -eq 0 }).Count -eq 0) 'Release included a key down'
                $scans=@($up | ForEach-Object { $_.data.key.scan }) -join ','
                $expected=if ($case -in @('partial','physical-owner')) { '42,29' } else { '71,42,29' }
                Assert-Held ($scans -eq $expected) "Owned subset/reverse release changed: $case $scans"
                Assert-Held ($up[0].data.key.extra -eq $marker) 'Lease marker missing'
            }
            if ($case -eq 'release-failed') { Assert-Held ($receipt.release_state -eq 'uncertain' -and $receipt.pending_mask -eq 3) 'Partial release hidden' }
            elseif ($case -eq 'physical-owner') { Assert-Held ($receipt.release_state -eq 'physical-owner' -and $receipt.transferred_mask -eq 4) 'Physical ownership was released' }
            else { Assert-Held ($receipt.release_state -eq 'submitted' -and $receipt.pending_mask -eq 0) "Cleanup not recorded: $case" }
            $count=$script:HeldTestBatches.Count
            $null=$release.Invoke($null,@($store,$plan,$events,$marker,$receipt,'second-observer',$send))
            Assert-Held ($script:HeldTestBatches.Count -eq $count) 'A second observer replayed cleanup'
            Assert-Held ($receipt.sends.Count -le 2) 'Unbounded send journal'
            if($case -in @('release-failed','physical-owner')) {
                $reconcile=[Basaltwater.Desktop.Native].GetMethod('ReconcileHeldRelease',$flags)
                Assert-Held (-not [bool]$reconcile.Invoke($null,@($store,$plan.keys,[Func[uint16,bool]]{param($vk) return $false}))) 'A held physical key was reconciled'
                Assert-Held ([bool]$reconcile.Invoke($null,@($store,$plan.keys,[Func[uint16,bool]]{param($vk) return $true}))) 'Observed-up keys could not reconcile without injection'
                Assert-Held ($script:HeldTestBatches.Count -eq $count) 'Reconciliation injected input'
            }
        } finally { $store.Dispose(); foreach ($e in $events) { $e.Dispose() } }
    }
    # Private admission/inspection without touching the workstation's state.
    function Get-NativeDesktopStatePath { return Join-Path $temporary 'state.json' }
    $root=Get-NativeHeldRoot; New-NativeDesktopPrivateDirectory $root
    $id='a'*32; $dir=Join-Path $root $id; New-NativeDesktopPrivateDirectory $dir
    [Basaltwater.Desktop.HeldStore]::Create($dir)
    Write-NativeHeldJson (Join-Path $dir 'request.json') @{version=1;id=$id;sid='fixture';session='fixture-session';directory=$dir;revision='r';task=('Basaltwater-Hold-'+$id);name=('Local\Basaltwater.Hold.'+$id)}
    Write-NativeHeldJson (Join-Path $root 'active.json') @{id=$id}
    $caught=$null; try { Assert-NativeHeldIdle } catch { $caught=$_ }
    Assert-Held ($caught.Exception.Message -match 'active or cleanup uncertain') 'Unsettled lease did not block mutations'
    # Keep this lease active for cancellation-marker inspection (no real input).
    $store=[Basaltwater.Desktop.HeldStore]::new($dir,('Local\Basaltwater.Hold.'+$id))
    try {
        $active=[Func[Basaltwater.Desktop.HeldStore+State,int]]{param($s) $s.phase=1;return 0}
        $null=$store.GetType().GetMethod('Locked').MakeGenericMethod([int]).Invoke($store,@($active))
    } finally {$store.Dispose()}
    $status=Get-NativeHeldLease $id @{Sid='fixture';Session='fixture-session'} $true
    Assert-Held ($status.ok -and (Test-Path -LiteralPath (Join-Path $dir 'cancel'))) 'Cancellation did not address the exact lease'
    $caught=$null; try { Get-NativeHeldLease $id @{Sid='fixture';Session='another'} $true } catch { $caught=$_ }
    Assert-Held ($null -ne $caught) 'Cross-session cancellation accepted'
    Write-NativeHeldJson (Join-Path $dir 'receipt.json') @{cleanup_confirmed=$false}
    $caught=$null; try { Assert-NativeHeldIdle } catch { $caught=$_ }
    Assert-Held ($null -ne $caught) 'Uncertain cleanup released admission'
    Write-NativeHeldJson (Join-Path $dir 'receipt.json') @{version=1;lease=$id;scheduler_retired=$true;cleanup_confirmed=$true}
    $caught=$null;try{Assert-NativeHeldIdle}catch{$caught=$_}
    Assert-Held ($null -ne $caught) 'A receipt overrode still-active ownership'
    $store=[Basaltwater.Desktop.HeldStore]::new($dir,('Local\Basaltwater.Hold.'+$id))
    try {
        $settled=[Func[Basaltwater.Desktop.HeldStore+State,int]]{param($s) $s.phase=2;return 0}
        $null=$store.GetType().GetMethod('Locked').MakeGenericMethod([int]).Invoke($store,@($settled))
    } finally {$store.Dispose()}
    Assert-NativeHeldIdle
    Assert-Held (-not (Test-Path -LiteralPath (Join-Path $root 'active.json'))) 'Confirmed cleanup did not release admission'
    $unstarted=Join-Path $temporary 'unstarted'; New-NativeDesktopPrivateDirectory $unstarted
    [Basaltwater.Desktop.HeldStore]::Create($unstarted)
    $name='Local\Basaltwater.HeldTest.'+[guid]::NewGuid().ToString('N')
    Assert-Held ([Basaltwater.Desktop.Native]::SettleUnstartedHold($unstarted,$name)) 'Unstarted ownership could not close admission'
    Assert-Held (-not [Basaltwater.Desktop.Native]::SettleUnstartedHold($unstarted,$name)) 'Terminal ownership reopened'
    Assert-Held ([Basaltwater.Desktop.Native]::HeldReleaseConfirmed($unstarted,$name)) 'Closed unstarted ownership not confirmed'
    $expired=Join-Path $temporary 'expired-before-down';New-NativeDesktopPrivateDirectory $expired
    [Basaltwater.Desktop.HeldStore]::Create($expired)
    $store=[Basaltwater.Desktop.HeldStore]::new($expired,('Local\Basaltwater.HeldTest.'+[guid]::NewGuid().ToString('N')))
    try {
        $heartbeat=[Func[Basaltwater.Desktop.HeldStore+State,int]]{param($s) $s.heartbeat=[Diagnostics.Stopwatch]::GetTimestamp();return 0}
        $null=$store.GetType().GetMethod('Locked').MakeGenericMethod([int]).Invoke($store,@($heartbeat))
        $script:ExpiredSendCalls=0
        $send=[Management.Automation.LanguagePrimitives]::ConvertTo({param($batch) $script:ExpiredSendCalls++;return [uint32]$batch.Length},$press.GetParameters()[6].ParameterType)
        $now=[Diagnostics.Stopwatch]::GetTimestamp();$caught=$null
        try {$null=$press.Invoke($null,@($store,$plan,$marker,[Basaltwater.Desktop.HeldReceipt]::new(),$now,($now-1),$send))} catch {$caught=$_}
        Assert-Held ($null -ne $caught -and $script:ExpiredSendCalls -eq 0) 'Expired startup injected a down'
    } finally {$store.Dispose()}
    $record=@{sid='fixture';session='fixture-session';directory=$dir;revision='r'}
    Remove-Item -LiteralPath (Join-Path $dir 'cancel')
    Write-NativeHeldJson (Get-NativeDesktopStatePath) @{owner='fixture';session='fixture-session';mode='enabled';revision='r'}
    Assert-Held ([string]::IsNullOrEmpty((Get-NativeHeldControlReason $record))) 'Unchanged enabled control refused'
    Write-NativeHeldJson (Get-NativeDesktopStatePath) @{owner='fixture';session='fixture-session';mode='human-override';revision='changed'}
    Assert-Held ((Get-NativeHeldControlReason $record) -eq 'control-changed') 'Control handoff did not stop a lease'
    Remove-Item -LiteralPath (Get-NativeDesktopStatePath)
    Assert-Held ((Get-NativeHeldControlReason $record) -eq 'control-unavailable') 'Missing control state failed open'
    [IO.File]::WriteAllText((Join-Path $dir 'cancel'),'cancel')
    Assert-Held ((Get-NativeHeldControlReason $record) -eq 'cancelled') 'Cancellation was not observed'
    & {
        $script:HeldSchedulerPresent=$true; $script:HeldSchedulerFailure=$false
        function Get-ScheduledTask($TaskPath,$ErrorAction) {
            if($script:HeldSchedulerFailure){throw 'fixture Scheduler query unavailable'}
            if($script:HeldSchedulerPresent){return @{TaskName=('Basaltwater-Hold-'+'a'*32)}}
        }
        function Unregister-ScheduledTask($TaskName,$TaskPath,$Confirm,$ErrorAction){$script:HeldSchedulerPresent=$false}
        Remove-NativeHeldScheduledTask ('Basaltwater-Hold-'+'a'*32)
        Assert-Held (-not $script:HeldSchedulerPresent) 'Registration was not retired'
        $script:HeldSchedulerFailure=$true;$caught=$null
        try {Remove-NativeHeldScheduledTask ('Basaltwater-Hold-'+'a'*32)} catch {$caught=$_}
        Assert-Held ($null -ne $caught) 'Scheduler query failure was mistaken for absence'
    }
    & {
        function Get-NativeHeldAdmissionPath {return Join-Path $temporary 'setup.lock'}
        function Get-ScheduledTask($TaskPath,$ErrorAction){return @()}
        $oldBoot=639000000000000000L; $newBoot=639000000100000000L
        $previous=[pscustomobject]@{version=1;id=$id;sid='fixture';session="5:0000000000000001:$oldBoot";directory=$dir;revision='r';task=('Basaltwater-Hold-'+$id);name=('Local\Basaltwater.Hold.'+$id);created_utc=([DateTime]::new($oldBoot+1,[DateTimeKind]::Utc)).ToString('o')}
        $next=@{Sid='fixture';Session="5:0000000000000002:$newBoot"}
        Assert-Held (Test-NativeHeldPreviousBoot $previous $next) 'Later boot was not recognized'
        Assert-Held (-not (Test-NativeHeldPreviousBoot $previous @{Session="6:0000000000000002:$oldBoot"})) 'Session change was mistaken for a boot'
        Write-NativeHeldJson (Join-Path $dir 'request.json') $previous
        $result=Get-NativeHeldLease $id $next $true
        Assert-Held ($result.receipt.cleanup_confirmed -and $result.receipt.reconciliation -match 'verified Windows boot') 'Previous-boot lease was not retired without replay'
        Assert-Held (Test-Path -LiteralPath (Join-Path $dir 'before-boot-recovery.json')) 'Boot recovery discarded previous evidence'
    }
    # The real response transport reserves top-level error. A nullable error
    # inside the held receipt must remain evidence, not become throw $null.
    & {
        function Invoke-NativeDesktopWorker($Request,[int]$TimeoutSeconds) {
            return @{held=@{reason='expired';release_state='submitted';error=$null;sends=@(@{action='down';requested=1;accepted=1})}}
        }
        $eventName='Local\Basaltwater.HeldTest.'+[guid]::NewGuid().ToString('N')
        $ready=[Threading.EventWaitHandle]::new($true,'ManualReset',($eventName+'.ready'))
        try {
            Write-NativeHeldJson (Join-Path $dir 'receipt.json') @{cleanup_confirmed=$true;scheduler_retired=$true;watchdog=$null}
            $result=Wait-NativeHeldLease @{name=$eventName;id=$id;directory=$dir}
            Assert-Held ($result.ok -and $result.duration_completed -and $result.result.release_state -eq 'submitted') 'Nullable receipt error corrupted successful transport'
            Assert-Held (Test-Path -LiteralPath (Join-Path $dir 'driver.json')) 'Driver evidence was not retained'
        } finally {$ready.Dispose()}
    }
    & {
        $savedLocal=$env:LOCALAPPDATA
        try {
            $env:LOCALAPPDATA=Join-Path $temporary 'maintenance-account'
            New-NativeDesktopPrivateDirectory $env:LOCALAPPDATA
            . (Join-Path $PSScriptRoot '..\Development.Common.ps1')
            $leaseRoot=Join-Path $env:LOCALAPPDATA 'Basaltwater\desktop\leases'
            $leaseDirectory=Join-Path $leaseRoot $id;New-NativeDesktopPrivateDirectory $leaseDirectory
            Write-NativeHeldJson (Join-Path $leaseRoot 'active.json') @{id=$id}
            $caught=$null;try{Assert-NativeDevelopmentIdle}catch{$caught=$_}
            Assert-Held ($caught.Exception.Message -match 'Held-input lease') 'Setup/reboot admission ignored an unsettled keyboard lease'
        } finally {$env:LOCALAPPDATA=$savedLocal}
    }
    Write-Host 'Held-input offline ownership and cleanup contracts passed (no live input).'
} finally {
    $resolved=[IO.Path]::GetFullPath($temporary)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe held test cleanup' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
