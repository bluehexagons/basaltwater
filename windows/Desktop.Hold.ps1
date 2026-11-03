<# Finite keyboard lease dispatch and private evidence. PowerShell 5.1. #>
$script:NativeHeldJsonOptions=@{}
if((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')){$script:NativeHeldJsonOptions.DateKind='String'}
function Get-NativeHeldRoot { return Join-Path (Split-Path -Parent (Get-NativeDesktopStatePath)) 'leases' }
function Get-NativeHeldAdmissionPath { return Join-Path $env:LOCALAPPDATA 'Basaltwater\setup.lock' }
function Test-NativeHeldPreviousBoot($Record,$Context) {
    if ($Record.session -notmatch '\A[0-9]+:[0-9a-f]{16}:([0-9]{16,19})\z') { return $false }
    $previous=[long]$Matches[1]
    if ($Context.Session -notmatch '\A[0-9]+:[0-9a-f]{16}:([0-9]{16,19})\z') { return $false }
    $current=[long]$Matches[1]
    try { $created=([DateTimeOffset]::Parse($Record.created_utc)).UtcDateTime.Ticks } catch { return $false }
    return $previous -lt $current -and $created -lt $current -and $current -le [DateTime]::UtcNow.Ticks
}
function Read-NativeHeldJson([string]$Path,[int]$Limit = 65536) {
    Assert-NativeDesktopPrivatePath $Path
    # Allow atomic publishers to replace the snapshot while this handle reads
    # its original bytes, including human control transitions during a hold.
    $stream=[IO.File]::Open($Path,'Open','Read',([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
    try {
        if ($stream.Length -gt $Limit) { throw 'Held-input record exceeds bound' }
        $bytes=New-Object byte[] ([int]$stream.Length+1); $count=0
        while ($count -lt $bytes.Length) { $read=$stream.Read($bytes,$count,$bytes.Length-$count); if ($read -eq 0) { break }; $count+=$read }
        if ($count -gt $Limit -or $count -eq $bytes.Length) { throw 'Held-input record grew during read' }
        $text=(New-Object Text.UTF8Encoding($false,$true)).GetString($bytes,0,$count).TrimStart([char]0xfeff)
        $options=$script:NativeHeldJsonOptions
        return $text | ConvertFrom-Json @options
    } finally { $stream.Dispose() }
}
function Write-NativeHeldJson([string]$Path,$Record,[int]$Limit=65536) {
    Assert-NativeDesktopPrivatePath (Split-Path -Parent $Path)
    Assert-NativeDesktopPath $Path
    if (Test-Path -LiteralPath $Path) { Assert-NativeDesktopPrivatePath $Path }
    $temp = $Path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    try {
        [IO.File]::WriteAllText($temp,($Record | ConvertTo-Json -Depth 12 -Compress),(New-Object Text.UTF8Encoding($false)))
        if ((Get-Item -LiteralPath $temp).Length -gt $Limit) { throw 'Held-input receipt exceeds bound' }
        if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temp,$Path,[Management.Automation.Language.NullString]::Value) }
        else { [IO.File]::Move($temp,$Path) }
    } finally { if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp } }
}
function Resolve-NativeHeldDirectory([string]$Id) {
    if ($Id -notmatch '\A[0-9a-f]{32}\z') { throw 'Invalid held-input lease ID' }
    $directory = Join-Path (Get-NativeHeldRoot) $Id
    Assert-NativeDesktopPrivatePath $directory
    return $directory
}
function Assert-NativeHeldRecord($Record,[string]$Id,[string]$Directory) {
    if ($Record.version -ne 1 -or $Record.id -cne $Id -or $Record.directory -cne $Directory -or
        $Record.task -cne ('Basaltwater-Hold-' + $Id) -or $Record.name -cne ('Local\Basaltwater.Hold.' + $Id)) { throw 'Invalid held-input request identity' }
    Assert-NativeDesktopPrivatePath (Join-Path $Directory 'ownership.bin')
}
function Request-NativeHeldCancellation([string]$Directory,[string]$Name) {
    if($Name -cne ('Local\Basaltwater.Hold.'+(Split-Path -Leaf $Directory))){throw 'Invalid held-input cancellation identity'}
    $path=Join-Path $Directory 'cancel'; Assert-NativeDesktopPath $path
    if (Test-Path -LiteralPath $path) { Assert-NativeDesktopPrivatePath $path }
    [IO.File]::WriteAllText($path,'cancel')
    $signal=$null
    try {$signal=[Threading.EventWaitHandle]::OpenExisting($Name+'.cancel');$null=$signal.Set()}
    catch [Threading.WaitHandleCannotBeOpenedException] { } # Startup also checks the durable marker.
    finally {if($signal){$signal.Dispose()}}
}
function Remove-NativeHeldScheduledTask([string]$Name) {
    if ($Name -notmatch '\ABasaltwater-Hold-[0-9a-f]{32}\z') { throw 'Invalid held-input task name' }
    $tasks=@(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop | Where-Object TaskName -CEQ $Name)
    if ($tasks.Count) {
        try { Unregister-ScheduledTask -TaskName $Name -TaskPath '\' -Confirm:$false -ErrorAction Stop }
        catch {
            # A cooperating release may already have retired it. Query failures
            # remain failures; missing-name and unavailable Scheduler differ.
            if (@(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop | Where-Object TaskName -CEQ $Name).Count) { throw }
        }
    }
    if (@(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop | Where-Object TaskName -CEQ $Name).Count) { throw 'Held-input Scheduler retirement unconfirmed' }
}
function Test-NativeHeldClosedOwnership($Record) {
    $mutex=[Threading.Mutex]::new($false,$Record.name); $entered=$false
    try {
        try {$entered=$mutex.WaitOne(1000)} catch [Threading.AbandonedMutexException] {$entered=$true}
        if(-not $entered){throw 'Held-input ownership is busy; cleanup uncertain'}
        $path=Join-Path $Record.directory 'ownership.bin';Assert-NativeDesktopPrivatePath $path
        $stream=[IO.File]::Open($path,'Open','Read','ReadWrite')
        try {
            if($stream.Length -ne 64){throw 'Invalid held-input ownership record'}
            $bytes=New-Object byte[] 64;$offset=0
            while($offset -lt 64){$read=$stream.Read($bytes,$offset,64-$offset);if($read -eq 0){throw 'Incomplete held-input ownership record'};$offset+=$read}
        } finally {$stream.Dispose()}
        if([BitConverter]::ToInt32($bytes,0) -ne 0x484f4c44){throw 'Invalid held-input ownership record'}
        return [BitConverter]::ToInt32($bytes,4) -eq 2 -and [BitConverter]::ToInt32($bytes,8) -eq 0 -and [BitConverter]::ToInt32($bytes,16) -eq 0
    } finally {if($entered){$mutex.ReleaseMutex()};$mutex.Dispose()}
}
function Assert-NativeHeldIdle {
    $active = Join-Path (Get-NativeHeldRoot) 'active.json'
    if (-not (Test-Path -LiteralPath $active)) { return }
    $record = Read-NativeHeldJson $active
    $directory = Resolve-NativeHeldDirectory $record.id
    $final = Join-Path $directory 'receipt.json'
    if (-not (Test-Path -LiteralPath $final)) { throw "Held-input lease $($record.id) active or cleanup uncertain; inspect input lease" }
    $receipt = Read-NativeHeldJson $final
    if (-not $receipt.cleanup_confirmed) { throw "Held-input lease $($record.id) cleanup uncertain" }
    $request=Read-NativeHeldJson (Join-Path $directory 'request.json')
    Assert-NativeHeldRecord $request $record.id $directory
    if($receipt.version -ne 1 -or $receipt.lease -cne $record.id -or -not $receipt.scheduler_retired -or -not (Test-NativeHeldClosedOwnership $request)){throw 'Held-input receipt does not confirm closed ownership and Scheduler retirement'}
    Remove-Item -LiteralPath $active
}
function Get-NativeHeldControlReason($Record) {
    try {
        if (Test-Path -LiteralPath (Join-Path $Record.directory 'cancel')) { return 'cancelled' }
        $state = Read-NativeHeldJson (Get-NativeDesktopStatePath) 2097152
        if ($state.mode -ne 'enabled' -or $state.revision -cne $Record.revision -or $state.session -cne $Record.session -or $state.owner -cne $Record.sid) { return 'control-changed' }
        return $null
    } catch { return 'control-unavailable' }
}
function Invoke-NativeHeldDriver($Request) {
    $directory=Resolve-NativeHeldDirectory $Request.lease
    $record = Read-NativeHeldJson (Join-Path $directory 'request.json')
    Assert-NativeHeldRecord $record $Request.lease $directory
    $plan = [Basaltwater.Desktop.HeldPlan]$record.plan
    try {
        return [Basaltwater.Desktop.Native]::RunHold($plan,$record.directory,$record.name,[uint64]$record.marker,[int]$record.milliseconds,[int]$record.repeat_ms,[int]$record.caller_pid,[long]$record.caller_created,[Func[string]]{ Get-NativeHeldControlReason $record })
    } catch {
        Write-NativeHeldJson (Join-Path $record.directory 'driver-error.json') @{ error=$_.Exception.ToString(); stack=$_.ScriptStackTrace }
        throw
    }
}
function Start-NativeHeldWatchdog($Record) {
    $worker = (Join-Path $script:NativeDesktopSource 'Invoke-DesktopHoldWatchdog.ps1').Replace("'","''")
    $directory = $Record.directory.Replace("'","''")
    $entry = "`$env:PSModulePath = [Environment]::GetEnvironmentVariable('PSModulePath','Machine'); & '$worker' -Directory '$directory'"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($entry))
    $action = New-ScheduledTaskAction -Execute (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument ('-NoProfile -NonInteractive -Sta -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand ' + $encoded)
    $principal = New-ScheduledTaskPrincipal -UserId $Record.sid -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([timespan]::FromMinutes(2)) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $Record.task -Action $action -Principal $principal -Settings $settings | Out-Null
    Start-ScheduledTask -TaskName $Record.task
}
function Start-NativeHeldLease($Request,$Context,$State) {
    $admission = $null
    try {
    # Setup checks the active receipt under this same admission lock.
    $admission = [IO.File]::Open((Get-NativeHeldAdmissionPath),'OpenOrCreate','ReadWrite','None')
    # Preparation uses all existing native foreground/physical input guards.
    $prepare = @{}; foreach ($key in $Request.Keys) { $prepare[$key] = $Request[$key] }; $prepare.command = 'hold-plan'
    $plan = Invoke-NativeDesktopWorker $prepare
    $root = Get-NativeHeldRoot; New-NativeDesktopPrivateDirectory $root
    $id = [guid]::NewGuid().ToString('N'); $directory = Join-Path $root $id
    New-NativeDesktopPrivateDirectory $directory
    $caller = [Diagnostics.Process]::GetCurrentProcess()
    try { $callerCreated = $caller.StartTime.ToUniversalTime().Ticks } finally { $caller.Dispose() }
    $record = @{ version=1; id=$id; directory=$directory; sid=$Context.Sid; session=$Context.Session; revision=$State.revision;
        name=('Local\Basaltwater.Hold.' + $id); task=('Basaltwater-Hold-' + $id); marker=[uint32]('0x' + $id.Substring(0,7));
        caller_pid=$PID; caller_created=$callerCreated; milliseconds=$Request.milliseconds; repeat_ms=0; plan=$plan;
        created_utc=[DateTime]::UtcNow.ToString('o') }
    if ($Request.ContainsKey('repeat-ms')) { $record.repeat_ms=$Request['repeat-ms'] }
    Write-NativeHeldJson (Join-Path $directory 'request.json') $record
    # Reserve before dispatch. Failure stays inspectable and never starts a driver.
    Write-NativeHeldJson (Join-Path $root 'active.json') @{ id=$id }
    [IO.File]::WriteAllBytes((Join-Path $directory 'ownership.bin'),(New-Object byte[] 64))
    try { Start-NativeHeldWatchdog $record }
    catch {
        Request-NativeHeldCancellation $directory $record.name
        # No driver was dispatched. A late watchdog cannot inject key downs.
        $retired = $false
        try {
            Remove-NativeHeldScheduledTask $record.task; $retired=$true
        } catch { }
        $closed=$false
        try {Initialize-NativeDesktopTypes;$closed=[Basaltwater.Desktop.Native]::SettleUnstartedHold($directory,$record.name) -or [Basaltwater.Desktop.Native]::HeldReleaseConfirmed($directory,$record.name)} catch { }
        Write-NativeHeldJson (Join-Path $directory 'receipt.json') @{ version=1; lease=$id; cleanup_confirmed=($retired -and $closed); scheduler_retired=$retired; watchdog=$null; input_started=$false; error='Watchdog dispatch failed before driver launch' }
        throw "Held-input watchdog dispatch failed before input. Inspect input lease --lease $id"
    }
    return $record
    } finally { if ($admission) { $admission.Dispose() } }
}
function Get-NativeHeldLease([string]$Id,$Context,[bool]$Cancel) {
    $directory = Resolve-NativeHeldDirectory $Id
    $record = Read-NativeHeldJson (Join-Path $directory 'request.json')
    Assert-NativeHeldRecord $record $Id $directory
    if ($record.sid -cne $Context.Sid) { throw 'Held-input lease belongs to another account/session' }
    if ($record.session -cne $Context.Session) {
        if (-not $Cancel -or -not (Test-NativeHeldPreviousBoot $record $Context)) { throw 'Held-input lease belongs to another account/session' }
        $admission=[IO.File]::Open((Get-NativeHeldAdmissionPath),'OpenOrCreate','ReadWrite','None')
        try {
            # A verified later Windows boot ended the old interactive key state
            # and both helpers. This retires metadata without replaying input.
            Remove-NativeHeldScheduledTask $record.task
            Initialize-NativeDesktopTypes
            [Basaltwater.Desktop.Native]::RetireHeldAfterBoot($directory,$record.name)
            $receiptPath=Join-Path $directory 'receipt.json'
            if(Test-Path -LiteralPath $receiptPath){$previous=Read-NativeHeldJson $receiptPath;Write-NativeHeldJson (Join-Path $directory 'before-boot-recovery.json') $previous}
            $receipt=@{version=1;lease=$Id;cleanup_confirmed=$true;scheduler_retired=$true;watchdog=$null;reconciliation='Interrupted after verified Windows boot; no input replayed';completed_utc=[DateTime]::UtcNow.ToString('o')}
            Write-NativeHeldJson $receiptPath $receipt
            return @{ok=$true;lease=$Id;state='settled';receipt=$receipt;cancellation_requested=$true;evidence_directory=$directory}
        } finally {$admission.Dispose()}
    }
    if ($Cancel) { Request-NativeHeldCancellation $directory $record.name }
    $receiptPath = Join-Path $directory 'receipt.json'
    $receipt = $null; if (Test-Path -LiteralPath $receiptPath) { $receipt=Read-NativeHeldJson $receiptPath }
    if ($Cancel -and (-not $receipt -or -not $receipt.cleanup_confirmed)) {
        Initialize-NativeDesktopTypes
        if ([Basaltwater.Desktop.Native]::SettleUnstartedHold($directory,$record.name)) {
            Remove-NativeHeldScheduledTask $record.task
            $receipt=@{version=1;lease=$Id;cleanup_confirmed=$true;scheduler_retired=$true;watchdog=$null;reconciliation='Unstarted ownership closed under mutex; no down intent existed';completed_utc=[DateTime]::UtcNow.ToString('o')}
            Write-NativeHeldJson $receiptPath $receipt
        }
    }
    if ($Cancel -and (-not $receipt -or -not $receipt.cleanup_confirmed) -and (Test-Path -LiteralPath (Join-Path $directory 'worker.json'))) {
        $worker=Read-NativeHeldJson (Join-Path $directory 'worker.json')
        $process=Get-Process -Id ([int]$worker.pid) -ErrorAction SilentlyContinue
        $alive=$false
        if ($process) {
            try { $alive=$process.StartTime.ToUniversalTime().Ticks -eq [long]$worker.created } finally { $process.Dispose() }
        }
        if (-not $alive) {
            Initialize-NativeDesktopTypes
            $releaseConfirmed=[Basaltwater.Desktop.Native]::HeldReleaseConfirmed($directory,$record.name)
            $observedUp=$false
            if (-not $releaseConfirmed) { $observedUp=[Basaltwater.Desktop.Native]::ObserveHeldReleased([Basaltwater.Desktop.HeldPlan]$record.plan,$directory,$record.name) }
            if ($releaseConfirmed -or $observedUp) {
                Remove-NativeHeldScheduledTask $record.task
                $reconciliation=if ($observedUp) {'Watchdog gone; all leased keys observed up; no input injected'} else {'Watchdog gone; ownership record confirms release; registration retired'}
                if (-not $receipt) { $receipt=[pscustomobject]@{version=1;lease=$Id;watchdog=$null} }
                $receipt | Add-Member NoteProperty cleanup_confirmed $true -Force
                $receipt | Add-Member NoteProperty scheduler_retired $true -Force
                $receipt | Add-Member NoteProperty reconciliation $reconciliation -Force
                $receipt | Add-Member NoteProperty completed_utc ([DateTime]::UtcNow.ToString('o')) -Force
                Write-NativeHeldJson $receiptPath $receipt
            }
        }
    }
    if ($Cancel -and $receipt -and -not $receipt.cleanup_confirmed -and $receipt.scheduler_retired -and -not $receipt.watchdog) {
        Initialize-NativeDesktopTypes
        if ([Basaltwater.Desktop.Native]::SettleUnstartedHold($directory,$record.name)) {
            $receipt.cleanup_confirmed=$true
            $receipt | Add-Member NoteProperty reconciliation 'Unstarted ownership closed under mutex; no down intent existed' -Force
            Write-NativeHeldJson $receiptPath $receipt
        }
    }
    return @{ ok=$true; lease=$Id; evidence_directory=$directory; cancellation_requested=$Cancel; receipt=$receipt; state=$(if (-not $receipt) { 'active-or-uncertain' } elseif ($receipt.cleanup_confirmed) { 'settled' } else { 'cleanup-uncertain' }) }
}
function Get-NativeHeldActive($Context) {
    $path = Join-Path (Get-NativeHeldRoot) 'active.json'
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $record = Read-NativeHeldJson $path
    $directory=Resolve-NativeHeldDirectory $record.id
    $request=Read-NativeHeldJson (Join-Path $directory 'request.json')
    Assert-NativeHeldRecord $request $record.id $directory
    if($request.sid -cne $Context.Sid){throw 'Held-input lease belongs to another account'}
    if($request.session -cne $Context.Session){return @{lease=$record.id;state='other-session';evidence_directory=$directory;previous_boot=(Test-NativeHeldPreviousBoot $request $Context)}}
    return Get-NativeHeldLease $record.id $Context $false
}
function Wait-NativeHeldLease($Record) {
    $ready = $null; $driver = $null; $driverError = $null
    $clock = [Diagnostics.Stopwatch]::StartNew()
    try {
        while ($clock.Elapsed.TotalSeconds -lt 12) {
            try { $ready=[Threading.EventWaitHandle]::OpenExisting($Record.name + '.ready') } catch [Threading.WaitHandleCannotBeOpenedException] { }
            if ($ready -and $ready.WaitOne(0)) { break }
            if (Test-Path -LiteralPath (Join-Path $Record.directory 'watchdog.json')) { throw 'Watchdog ended before input; inspect retained evidence' }
            if ($ready) { $ready.Dispose(); $ready=$null }; Start-Sleep -Milliseconds 50
        }
        if (-not $ready -or -not $ready.WaitOne(0)) { throw 'Held-input watchdog startup timed out; no input submitted' }
        $driver = (Invoke-NativeDesktopWorker @{ command='hold'; lease=$Record.id } 10).held
        Write-NativeHeldJson (Join-Path $Record.directory 'driver.json') $driver
    } catch { $driverError=$_.Exception.Message; Request-NativeHeldCancellation $Record.directory $Record.name }
    finally { if ($ready) { $ready.Dispose() } }
    $path = Join-Path $Record.directory 'receipt.json'
    $wait = [Diagnostics.Stopwatch]::StartNew()
    while (-not (Test-Path -LiteralPath $path) -and $wait.Elapsed.TotalSeconds -lt 8) { Start-Sleep -Milliseconds 50 }
    $receipt = $null; if (Test-Path -LiteralPath $path) { $receipt=Read-NativeHeldJson $path }
    $started=$driver -and @($driver.sends | Where-Object {$_.action -eq 'down' -and $_.accepted -gt 0 -and $_.accepted -eq $_.requested}).Count -eq 1
    $durationCompleted = $started -and ($driver.reason -eq 'expired' -or ($receipt -and $receipt.watchdog -and $receipt.watchdog.reason -eq 'watchdog-expired'))
    $ok = $null -ne $receipt -and $receipt.cleanup_confirmed -and -not $driverError -and $driver -and -not $driver.error -and $driver.release_state -eq 'submitted' -and $durationCompleted
    return @{ ok=[bool]$ok; duration_completed=[bool]$durationCompleted; lease=$Record.id; result=$driver; receipt=$receipt; error=$driverError; evidence_directory=$Record.directory }
}
