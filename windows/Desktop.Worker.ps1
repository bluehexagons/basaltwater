<# Explicit finite warm workers. Mailboxes are private, single-use and never replayed. #>
if(-not(Get-Variable -Name NativeDesktopWorkerServing -Scope Script -ErrorAction SilentlyContinue)){$script:NativeDesktopWorkerServing=$false}
if(-not(Get-Variable -Name NativeDesktopInputGuard -Scope Script -ErrorAction SilentlyContinue)){$script:NativeDesktopInputGuard=$null}
function Get-NativeWarmInputGuardReason($Record,$Request,$Peer){
    if($Peer.HasExited){return 'Desktop caller exited'}
    if([Diagnostics.Stopwatch]::GetTimestamp() -ge [long]$Request.deadline){return 'Desktop request expired'}
    if(Test-Path -LiteralPath (Join-Path $Record.directory 'stop')){return 'Desktop worker stopped'}
    try {
        $state=Read-NativeHeldJson (Get-NativeDesktopStatePath) 2097152
        if($state.owner -cne $Record.sid -or $state.mode -ne 'enabled' -or $state.session -cne $Record.session -or $state.revision -cne $Request.revision -or $state.generation -cne $Request.generation){return 'Desktop control/generation changed'}
        return $null
    }catch{return 'Desktop control unavailable'}
}
function Get-NativeWarmRoot {return Join-Path (Split-Path -Parent (Get-NativeDesktopStatePath)) 'workers'}
function Get-NativeWarmSignalName($Record) {
    if($Record.id -notmatch '\A[0-9a-f]{32}\z'){throw 'Invalid desktop worker ID'}
    return 'Local\Basaltwater.Desktop.Worker.'+$Record.id
}
function Send-NativeWarmSignal($Record) {
    $signal=$null
    try{$signal=[Threading.EventWaitHandle]::OpenExisting((Get-NativeWarmSignalName $Record));$null=$signal.Set()}
    catch [Threading.WaitHandleCannotBeOpenedException] { } # Durable mailboxes/stop also survive startup or caller loss.
    finally{if($signal){$signal.Dispose()}}
}
function Get-NativeWarmSources {return @('Desktop.Common.ps1','Desktop.Hold.ps1','Desktop.Worker.ps1','Desktop.Native.cs','Desktop.Hold.cs','Desktop.Session.cs','Invoke-DesktopWorker.ps1')}
function Get-NativeWarmSourceKey([string]$Source) {
    $parts=@('desktop-worker-v1')
    foreach($name in Get-NativeWarmSources){$parts+=($name+':'+(Get-FileHash -LiteralPath (Join-Path $Source $name) -Algorithm SHA256).Hash)}
    $hash=[Security.Cryptography.SHA256]::Create()
    try {return [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes(($parts -join "`n")))).Replace('-','').ToLowerInvariant()}
    finally {$hash.Dispose()}
}
function Get-NativeWarmRecord {
    $active=Join-Path (Get-NativeWarmRoot) 'active.json'
    if(-not(Test-Path -LiteralPath $active)){return $null}
    $id=(Read-NativeHeldJson $active).id
    return Read-NativeWarmRecord $id
}
function Read-NativeWarmRecord([string]$id) {
    if($id -notmatch '\A[0-9a-f]{32}\z'){throw 'Invalid desktop worker ID'}
    $directory=Join-Path (Get-NativeWarmRoot) $id;Assert-NativeDesktopPrivatePath $directory
    for($attempt=0;$attempt -lt 3;$attempt++){
        try{$record=Read-NativeHeldJson (Join-Path $directory 'worker.json');break}
        catch{
            # Replacement publication can transiently deny an old pathname.
            # Retry this read only; never redispatch an input operation.
            if($attempt -eq 2 -or $_.Exception.GetBaseException() -isnot [IO.FileNotFoundException]){throw}
            Start-Sleep -Milliseconds 10
        }
    }
    if($record.version -ne 1 -or $record.id -cne $id -or $record.directory -cne $directory -or
        $record.task -cne ('Basaltwater-Desktop-Worker-'+$id)){throw 'Invalid desktop worker identity'}
    return $record
}
function Pin-NativeWarmProcess($Record,[int]$ProcessId,[long]$Created) {
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    try {
        $parts=$Record.session.Split(':')
        if($Record.sid -cne $identity.User.Value -or $parts.Count -ne 3){throw 'Desktop worker account/session mismatch'}
        if(-not('Basaltwater.Desktop.Session' -as [type])){$null=Get-NativeDesktopAuthenticationId $identity.Token}
        return [Basaltwater.Desktop.Session]::PinProcess($ProcessId,$Created,$Record.sid,[int]$parts[0],$parts[1])
    } finally {$identity.Dispose()}
}
function Remove-NativeWarmRegistration($Record) {
    $tasks=@(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop|Where-Object TaskName -CEQ $Record.task)
    if($tasks.Count){
        try{Unregister-ScheduledTask -TaskName $Record.task -TaskPath '\' -Confirm:$false -ErrorAction Stop}
        catch{if(@(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop|Where-Object TaskName -CEQ $Record.task).Count){throw}}
    }
    if(@(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop|Where-Object TaskName -CEQ $Record.task).Count){throw 'Desktop worker Scheduler retirement unconfirmed'}
}
function Complete-NativeWarmMailboxes($Record) {
    $inbox=Join-Path $Record.directory 'requests';Assert-NativeDesktopPrivatePath $inbox
    foreach($mailbox in @(Get-ChildItem -LiteralPath $inbox -Directory)){
        if($mailbox.Name -notmatch '\A[0-9a-f]{32}\z'){throw 'Invalid mailbox during worker closeout'}
        $path=$mailbox.FullName
        # The caller removes request.json BEFORE response.json. Check in this
        # order so consuming a response cannot make closeout recreate it or
        # write into a directory the caller has already removed.
        if(-not(Test-Path -LiteralPath (Join-Path $path 'response.json')) -and
            (Test-Path -LiteralPath (Join-Path $path 'request.json'))){
            $claimed=Test-Path -LiteralPath (Join-Path $path 'claimed')
            Write-NativeHeldJson (Join-Path $path 'response.json') @{version=1;id=$mailbox.Name;ok=$false;submitted=$claimed;result=$null;error='Desktop worker stopped; claimed action outcome may be unknown; inspect before retrying'}
        }
    }
}
function Remove-NativeWarmMailbox([string]$Directory) {
    Assert-NativeDesktopPrivatePath $Directory
    # This order pairs with Complete-NativeWarmMailboxes during shutdown.
    foreach($name in @('request.json','response.json','claimed')){
        $file=Join-Path $Directory $name
        if(Test-Path -LiteralPath $file){Assert-NativeDesktopPrivatePath $file;Remove-Item -LiteralPath $file -ErrorAction Stop}
    }
    [IO.Directory]::Delete($Directory)
}
function Get-NativeWarmStatus {
    $record=Get-NativeWarmRecord
    if(-not $record){return @{state='stopped';cleanup_confirmed=$true}}
    if($record.state -eq 'running' -and -not $record.cleanup_confirmed){
        $process=$null
        try{$process=Pin-NativeWarmProcess $record ([int]$record.pid) ([long]$record.created);if($process.HasExited){throw 'Worker exited'}}
        catch{$record.state='unavailable';$record.error='Worker identity is no longer live; use worker stop to reconcile cleanup; no replay'}
        finally{if($process){$process.Dispose()}}
    }
    return $record
}
function Assert-NativeWarmIdle {
    $record=Get-NativeWarmRecord
    if($record -and -not $record.cleanup_confirmed){throw "Desktop worker $($record.id) active or uncertain; use desktop worker stop before setup/reboot"}
}
function Start-NativeWarmWorker($Context,[int]$IdleSeconds=120,[int]$SessionSeconds=1800) {
    if($IdleSeconds -lt 30 -or $IdleSeconds -gt 600 -or $SessionSeconds -lt $IdleSeconds -or $SessionSeconds -gt 3600){throw 'Worker idle must be 30..600 seconds; session must be idle..3600 seconds'}
    Assert-NativeHeldIdle
    $old=Get-NativeWarmRecord
    if($old -and -not $old.cleanup_confirmed){throw 'A desktop worker is already active or uncertain; inspect/stop it first'}
    $admission=[IO.File]::Open((Get-NativeHeldAdmissionPath),'OpenOrCreate','ReadWrite','None')
    try {
        $root=Get-NativeWarmRoot;New-NativeDesktopPrivateDirectory $root
        $id=[guid]::NewGuid().ToString('N');$directory=Join-Path $root $id;New-NativeDesktopPrivateDirectory $directory
        $source=Join-Path $directory 'source';New-NativeDesktopPrivateDirectory $source
        New-NativeDesktopPrivateDirectory (Join-Path $directory 'requests')
        $key=Get-NativeWarmSourceKey $script:NativeDesktopSource
        foreach($name in Get-NativeWarmSources){Copy-Item -LiteralPath (Join-Path $script:NativeDesktopSource $name) -Destination (Join-Path $source $name)}
        if((Get-NativeWarmSourceKey $source) -cne $key){throw 'Desktop source changed during worker snapshot; no worker started'}
        $record=[pscustomobject]@{version=1;id=$id;directory=$directory;task=('Basaltwater-Desktop-Worker-'+$id);sid=$Context.Sid;session=$Context.Session;
            source_key=$key;origin=$script:NativeDesktopSource;state='starting';pid=0;created=0;idle_seconds=$IdleSeconds;session_seconds=$SessionSeconds;
            cleanup_confirmed=$false;error=$null;started_utc=[DateTime]::UtcNow.ToString('o');completed_utc=$null}
        Write-NativeHeldJson (Join-Path $directory 'worker.json') $record
        Write-NativeHeldJson (Join-Path $root 'active.json') @{id=$id}
        $entry=(Join-Path $source 'Invoke-DesktopWorker.ps1').Replace("'","''");$dir=$directory.Replace("'","''")
        $code="`$env:PSModulePath=[Environment]::GetEnvironmentVariable('PSModulePath','Machine');& '$entry' -Directory '$dir'"
        $action=New-ScheduledTaskAction -Execute (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument ('-NoProfile -NonInteractive -Sta -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code)))
        $principal=New-ScheduledTaskPrincipal -UserId $record.sid -LogonType Interactive -RunLevel Limited
        $settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([timespan]::FromSeconds($SessionSeconds+30)) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $record.task -Action $action -Principal $principal -Settings $settings|Out-Null
        Start-ScheduledTask -TaskName $record.task
    } finally {$admission.Dispose()}
    $clock=[Diagnostics.Stopwatch]::StartNew()
    do {
        $record=Get-NativeWarmRecord
        if($record.state -eq 'running'){return $record}
        if($record.state -ne 'starting'){throw 'Desktop worker startup failed; inspect desktop worker status'}
        Start-Sleep -Milliseconds 50
    } while($clock.Elapsed.TotalSeconds -lt 15)
    throw 'Desktop worker startup unconfirmed; inspect/stop it before another start'
}
function Stop-NativeWarmWorker {
    $record=Get-NativeWarmRecord
    if(-not $record -or $record.cleanup_confirmed){return Get-NativeWarmStatus}
    $stop=Join-Path $record.directory 'stop';Assert-NativeDesktopPath $stop;[IO.File]::WriteAllText($stop,'stop')
    Send-NativeWarmSignal $record
    # A running held driver retains its independent watchdog and exact lease.
    $busy=Join-Path $record.directory 'busy.json'
    if(Test-Path -LiteralPath $busy){
        $current=Read-NativeHeldJson $busy
        if($current.command -eq 'hold' -and $current.lease -match '\A[0-9a-f]{32}\z'){
            $lease=Resolve-NativeHeldDirectory $current.lease;$request=Read-NativeHeldJson (Join-Path $lease 'request.json')
            Assert-NativeHeldRecord $request $current.lease $lease;Request-NativeHeldCancellation $lease $request.name
        }
    }
    $clock=[Diagnostics.Stopwatch]::StartNew()
    while($clock.Elapsed.TotalSeconds -lt 12){$record=Get-NativeWarmRecord;if($record.cleanup_confirmed){return $record};Start-Sleep -Milliseconds 50}
    $process=$null;$workerLease=$null
    try {
        try {$workerLease=[IO.File]::Open((Join-Path $record.directory 'worker.lock'),'OpenOrCreate','ReadWrite','None')}
        catch [IO.IOException] { } # The acknowledged worker still owns the lease.
        if(-not $workerLease){
            $process=Pin-NativeWarmProcess $record ([int]$record.pid) ([long]$record.created)
            if(-not $process.HasExited){$process.Kill();if(-not $process.WaitForExit(2000)){throw 'Desktop worker termination unconfirmed'}}
            $workerLease=[IO.File]::Open((Join-Path $record.directory 'worker.lock'),'OpenOrCreate','ReadWrite','None')
        }
        Remove-NativeWarmRegistration $record
        Assert-NativeHeldIdle
        $record.state='stopped';$record.cleanup_confirmed=$true;$record.error='Worker stopped; last action outcome may be unknown';$record.completed_utc=[DateTime]::UtcNow.ToString('o')
        Write-NativeHeldJson (Join-Path $record.directory 'worker.json') $record
    } finally {if($process){$process.Dispose()};if($workerLease){$workerLease.Dispose()}}
    return $record
}
function Invoke-NativeWarmRequest($Record,$Request,[int]$TimeoutSeconds) {
    if($Record.state -ne 'running'){throw 'Desktop worker is not running; inspect/stop it; no request replayed'}
    if(Test-Path -LiteralPath (Join-Path $Record.directory 'busy.json')){throw 'Desktop worker has an in-flight operation; inspect its outcome before further requests'}
    if($Record.origin -cne $script:NativeDesktopSource -or $Record.source_key -cne (Get-NativeWarmSourceKey $script:NativeDesktopSource)){throw 'Desktop worker source changed; stop/start it before further actions'}
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    try {
        $authentication=Get-NativeDesktopAuthenticationId $identity.Token
        if($Record.sid -cne $identity.User.Value -or $Record.session -notlike ("$([Diagnostics.Process]::GetCurrentProcess().SessionId):${authentication}:*")){throw 'Desktop worker belongs to another logon/session'}
    } finally {$identity.Dispose()}
    $process=Pin-NativeWarmProcess $Record ([int]$Record.pid) ([long]$Record.created)
    $id=[guid]::NewGuid().ToString('N');$directory=Join-Path (Join-Path $Record.directory 'requests') $id
    New-NativeDesktopPrivateDirectory $directory
    $response=$null;$responseOwned=$false;$failure=$null;$cleanupError=$null
    try {
        $self=[Diagnostics.Process]::GetCurrentProcess()
        try {$created=$self.StartTime.ToUniversalTime().Ticks} finally {$self.Dispose()}
        $state=Read-NativeHeldJson (Get-NativeDesktopStatePath) 2097152
        $envelope=@{version=1;id=$id;pid=$PID;created=$created;session=$Record.session;revision=$state.revision;generation=$state.generation;
            deadline=([Diagnostics.Stopwatch]::GetTimestamp()+$TimeoutSeconds*[Diagnostics.Stopwatch]::Frequency);request=$Request}
        Write-NativeHeldJson (Join-Path $directory 'request.json') $envelope
        Send-NativeWarmSignal $Record
        $path=Join-Path $directory 'response.json';$clock=[Diagnostics.Stopwatch]::StartNew()
        while(-not(Test-Path -LiteralPath $path)){
            if($process.HasExited){throw 'Desktop worker exited; outcome unknown; observe before retrying'}
            if($clock.Elapsed.TotalSeconds -ge $TimeoutSeconds){
                [IO.File]::WriteAllText((Join-Path $Record.directory 'stop'),'stop')
                $process.Kill();$null=$process.WaitForExit(2000)
                throw 'Desktop worker timed out and was stopped; outcome unknown; no replay'
            }
            Start-Sleep -Milliseconds 10
        }
        $response=Read-NativeHeldJson $path 2097152
        if($null -eq $response -or $response -isnot [pscustomobject] -or
            -not $response.PSObject.Properties['version'] -or $response.version -ne 1 -or
            -not $response.PSObject.Properties['id'] -or $response.id -cne $id -or
            -not $response.PSObject.Properties['ok'] -or $response.ok -isnot [bool] -or
            -not $response.PSObject.Properties['result'] -or -not $response.PSObject.Properties['error']){
            throw 'Desktop worker response identity/schema mismatch; outcome unknown; do not replay'
        }
        $responseOwned=$true
        if(-not $response.ok){throw $response.error}
    } catch {$failure=$_}
    finally {
        $process.Dispose()
        # A timed-out mailbox is private retained evidence. Never race a
        # server still using it, consume an invalid reply, or replay its input.
        if($responseOwned){
            try{Remove-NativeWarmMailbox $directory}catch{$cleanupError=$_.Exception.Message}
        }
    }
    if($failure){
        if($cleanupError){throw ($failure.Exception.Message+'; mailbox cleanup unconfirmed; inspect retained evidence; do not replay')}
        throw $failure
    }
    if($cleanupError){
        if($Request.command -eq 'hold-plan'){throw 'Hold preparation completed but mailbox cleanup is unconfirmed; no held input started; inspect retained evidence'}
        $response.result|Add-Member NoteProperty worker_cleanup @{confirmed=$false;directory=$directory;error=$cleanupError;message='Action receipt retained; do not replay to repair mailbox cleanup'} -Force
    }
    return $response.result
}
