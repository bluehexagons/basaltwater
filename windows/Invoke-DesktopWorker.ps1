param([Parameter(Mandatory=$true)][string]$Directory)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Desktop.Common.ps1')
$script:NativeDesktopWorkerServing=$true
$record=$null;$lease=$null;$failure=$null;$owned=$false;$wake=$null
try {
    Assert-NativeDesktopPrivatePath $Directory
    $lease=[IO.File]::Open((Join-Path $Directory 'worker.lock'),'OpenOrCreate','ReadWrite','None')
    $record=Read-NativeWarmRecord (Split-Path -Leaf $Directory)
    if($record.directory -cne $Directory){throw 'Worker directory mismatch'}
    if($record.cleanup_confirmed){return}
    $owned=$true
    if($record.state -ne 'starting' -or
        (Test-Path -LiteralPath (Join-Path $Directory 'stop'))){throw 'Worker startup retired; no replay'}
    $context=Get-NativeDesktopContext
    if($context.Sid -cne $record.sid -or $context.Session -cne $record.session -or
        (Get-NativeWarmSourceKey $PSScriptRoot) -cne $record.source_key){throw 'Worker source/account/session mismatch'}
    Initialize-NativeDesktopTypes
    [Basaltwater.Desktop.Native]::Ready();[Basaltwater.Desktop.Native]::Dpi()
    $self=[Diagnostics.Process]::GetCurrentProcess()
    try {$record.pid=$self.Id;$record.created=$self.StartTime.ToUniversalTime().Ticks} finally {$self.Dispose()}
    $wake=[Threading.EventWaitHandle]::new($false,[Threading.EventResetMode]::AutoReset,(Get-NativeWarmSignalName $record))
    $record.state='running';Write-NativeHeldJson (Join-Path $Directory 'worker.json') $record
    $clock=[Diagnostics.Stopwatch]::StartNew();$last=0.0
    while(-not(Test-Path -LiteralPath (Join-Path $Directory 'stop')) -and $clock.Elapsed.TotalSeconds -lt $record.session_seconds -and
        $clock.Elapsed.TotalSeconds-$last -lt $record.idle_seconds){
        $inbox=Join-Path $Directory 'requests';Assert-NativeDesktopPrivatePath $inbox
        $pending=@(Get-ChildItem -LiteralPath $inbox -Directory|Select-Object -First 33)
        if($pending.Count -gt 32){throw 'Desktop worker mailbox bound exceeded'}
        foreach($mailbox in $pending){
            if($mailbox.Name -notmatch '\A[0-9a-f]{32}\z'){throw 'Invalid desktop worker mailbox'}
            $path=$mailbox.FullName
            try{Assert-NativeDesktopPrivatePath $path}catch{if(-not(Test-Path -LiteralPath $path)){continue};throw}
            $requestPath=Join-Path $path 'request.json';$output=Join-Path $path 'response.json';$claim=Join-Path $path 'claimed'
            if(-not(Test-Path -LiteralPath $requestPath) -or (Test-Path -LiteralPath $claim) -or (Test-Path -LiteralPath $output)){continue}
            $peer=$null;$submitted=$false;$result=$null;$dispatchError=$null;$request=$null
            try {
                $request=Read-NativeHeldJson $requestPath
                if($request.version -ne 1 -or $request.id -cne $mailbox.Name -or $request.session -cne $record.session){throw 'Invalid worker request identity'}
                $peer=Pin-NativeWarmProcess $record ([int]$request.pid) ([long]$request.created)
                # Claim durably before dispatch. A terminated server never
                # replays a claimed mailbox after a restart.
                [IO.File]::WriteAllText($claim,'claimed')
                $body=$request.request
                if($body.command -notin @('ready','windows','inspect','window','element','input','screenshot','hold-plan','hold')){throw 'Unsupported warm worker operation'}
                Write-NativeHeldJson (Join-Path $Directory 'busy.json') @{id=$request.id;command=$body.command;lease=$(if($body.PSObject.Properties['lease']){$body.lease}else{$null})}
                $state=Read-NativeHeldJson (Get-NativeDesktopStatePath) 2097152
                if($state.owner -cne $record.sid -or $state.session -cne $record.session -or
                    $state.generation -cne $request.generation -or $state.revision -cne $request.revision){throw 'Desktop control/generation changed before dispatch'}
                if($body.command -in @('window','element','input','hold-plan','hold') -and $state.mode -ne 'enabled'){throw 'Desktop control is not enabled'}
                $now=[Diagnostics.Stopwatch]::GetTimestamp();$deadline=[long]$request.deadline
                if($deadline -le $now -or $deadline-$now -gt 10*[Diagnostics.Stopwatch]::Frequency){throw 'Worker request expired or exceeds its budget'}
                if($peer.HasExited -or (Test-Path -LiteralPath (Join-Path $Directory 'stop'))){throw 'Worker caller exited or session stopped before dispatch'}
                # A worker-body parameter named Request must not shadow the
                # envelope when this delegate is called back from native code.
                $warmGuardRecord=$record;$warmGuardEnvelope=$request;$warmGuardPeer=$peer
                $script:NativeDesktopInputGuard=[Func[string]]{Get-NativeWarmInputGuardReason $warmGuardRecord $warmGuardEnvelope $warmGuardPeer}
                $submitted=$true;$result=Invoke-NativeDesktopWorkerBody $body
            } catch {$dispatchError=$_.Exception.Message}
            finally {
                $script:NativeDesktopInputGuard=$null
                Write-NativeHeldJson $output @{version=1;id=$mailbox.Name;ok=($null -eq $dispatchError);submitted=$submitted;result=$result;error=$dispatchError} 2097152
                $busy=Join-Path $Directory 'busy.json';if(Test-Path -LiteralPath $busy){Assert-NativeDesktopPrivatePath $busy;Remove-Item -LiteralPath $busy}
                if($peer){$peer.Dispose()}
            }
            $last=$clock.Elapsed.TotalSeconds
        }
        # Wake immediately for requests/stop without repeatedly scanning ACLs
        # while a game is using the machine. A bounded sweep covers a caller
        # disappearing between durable publication and signaling the event.
        $remaining=[Math]::Min($record.session_seconds-$clock.Elapsed.TotalSeconds,$record.idle_seconds-($clock.Elapsed.TotalSeconds-$last))
        $null=$wake.WaitOne([int][Math]::Max(1,[Math]::Min(5000,1000*$remaining)))
    }
} catch {$failure=$_.Exception.Message}
finally {
    if($record -and $lease -and $owned){
        $record.state='stopped';$record.error=$failure
        try {
            Complete-NativeWarmMailboxes $record
            Remove-NativeWarmRegistration $record
            $record.cleanup_confirmed=$true;$record.completed_utc=[DateTime]::UtcNow.ToString('o')
        } catch {$record.state='cleanup-uncertain';$record.error=$_.Exception.Message}
        Write-NativeHeldJson (Join-Path $Directory 'worker.json') $record
    }
    if($wake){$wake.Dispose()}
    if($lease){$lease.Dispose()}
}
