<# Offline observation contract: native calls are mocked; state uses a private temp directory. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '..\Desktop.Common.ps1')
function Assert-Observation([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Assert-ObservationFailure($Reply, [string]$Pattern) {
    Assert-Observation (-not $Reply.ok -and $Reply.error -match $Pattern) "Expected $Pattern; got $($Reply | ConvertTo-Json -Depth 8 -Compress)"
}
$script:ObservationRoot = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-observation-tests-' + [guid]::NewGuid().ToString('N'))
$script:ObservationTicks = 1000L * [Diagnostics.Stopwatch]::Frequency
$script:ObservationUtc = 10000L
$script:ObservationSession = 'fixture-session'
$script:ObservationFail = $false
$script:ObservationCalls = 0
$script:ObservationCaptureDelay = 0L
$script:ObservationCaptureHook = $null
$script:ObservationClosedOwnership = $true
$script:ObservationOwnershipChecks = 0
$script:ObservationLastInput = $null
$script:ObservationPostFail = $false
$script:ObservationInputs = 0
function Get-NativeDesktopStatePath { return Join-Path $script:ObservationRoot 'state.json' }
function Get-NativeDesktopContext { return @{Sid='fixture-owner';Session=$script:ObservationSession} }
function Get-NativeDesktopMonotonic { return $script:ObservationTicks }
function Get-NativeDesktopTime { return $script:ObservationUtc }
function Enter-NativeDesktopLock($Context) { return $null }
function Exit-NativeDesktopLock($Lock) { }
function Test-NativeHeldClosedOwnership($Record) {
    $script:ObservationOwnershipChecks++
    return $script:ObservationClosedOwnership
}
function Get-NativeHeldActive($Context) { return $null }
function Invoke-NativeDesktopWorker($Request, [int]$TimeoutSeconds=8) {
    $script:ObservationCalls++
    if ($script:ObservationFail) { throw 'fixture outcome unknown' }
    switch ($Request.command) {
        ready { return @{healthy=$true;capture_performed=$false} }
        windows { return @{windows=@([pscustomobject]@{id=123L;identity='fixture-identity';width=640;height=480})} }
        screenshot {
            if($script:ObservationPostFail){throw 'fixture post capture failed'}
            $script:ObservationTicks += $script:ObservationCaptureDelay
            if ($script:ObservationCaptureHook) { & $script:ObservationCaptureHook }
            # Match the real serialized worker response, including PSCustomObject properties.
            return [pscustomobject]@{geometry=@(640,480);image_geometry=@(640,480);image_origin=@(0,0);desktop_origin=@(-640,-20);
                coordinate_space=$(if($Request.ContainsKey('monitor')){'monitor'}else{'window'});captured_at='2026-01-01T00:00:00Z';
                artifact_sha256=('b'*64);output=$Request.output}
        }
        default {
            if ($Request.command -eq 'input') { $script:ObservationInputs++;$script:ObservationLastInput = $Request | ConvertTo-Json -Depth 10 | ConvertFrom-Json }
            return @{submitted=$true}
        }
    }
}
function New-ObservationCapture([string[]]$Extra=@(), [switch]$Monitor) {
    $inventory=Invoke-NativeDesktopCli @('windows')
    $selectors=@('--window','123','--identity','fixture-identity','--generation',$inventory.generation)
    if($Monitor){$selectors=@('--monitor','fixture-monitor')}
    $reply=Invoke-NativeDesktopCli (@('screenshot')+$selectors+@('--output',(Join-Path $script:ObservationRoot ([guid]::NewGuid().ToString('N')+'.png')))+$Extra)
    Assert-Observation $reply.ok "Capture failed: $($reply | ConvertTo-Json -Depth 8 -Compress)"
    return $reply
}
function Invoke-ObservationInput($Capture, [string[]]$Extra=@()) {
    return Invoke-NativeDesktopCli (@('input','key','--key','A','--window','123','--identity','fixture-identity','--generation',$Capture.generation,
        '--width','640','--height','480','--observation',$Capture.result.observation_id)+$Extra)
}
New-NativeDesktopPrivateDirectory $script:ObservationRoot
try {
    $privateFile = Join-Path $script:ObservationRoot 'owner-check.bin'
    New-NativeDesktopPrivateFile $privateFile ([byte[]]@(1))
    Assert-NativeDesktopPrivatePath $privateFile
    $existingFileRejected = $false
    try { New-NativeDesktopPrivateFile $privateFile ([byte[]]@(2)) }
    catch [IO.IOException] { $existingFileRejected = $true }
    Assert-Observation ($existingFileRejected -and [IO.File]::ReadAllBytes($privateFile)[0] -eq 1) 'Private creation overwrote an existing file'
    Initialize-NativeDesktopTypes
    $expiredGuard = [Basaltwater.Desktop.Native]::ObservationGuard($null, 0L)
    Assert-Observation ($expiredGuard.Invoke() -match 'observation expired') 'Cold/warm capture guard accepted expired input'
    $guard = [Basaltwater.Desktop.Native]::ObservationGuard([Func[string]]{return 'fixture control changed'}, [long]::MaxValue)
    Assert-Observation ($guard.Invoke() -eq 'fixture control changed') 'Capture guard replaced existing admission checks'
    # Exercise the actual worker input boundary after its JSON hop. Its guard
    # must reject before InputAction can call any desktop APIs, even for HWND 0.
    $wireInput = @{command='input';window=0L;identity='fixture';operation='key';text='A';x=0;y=0;width=640;height=480;observation_deadline=0L}
    foreach ($wireRequest in @($wireInput, ($wireInput | ConvertTo-Json | ConvertFrom-Json))) {
        $caught=$null
        try { $null=Invoke-NativeDesktopInput $wireRequest } catch { $caught=$_ }
        Assert-Observation ($null -ne $caught -and $caught.Exception.Message -match 'Capture observation expired') 'Serialized input lost the observation guard'
    }
    $savedGuard=$script:NativeDesktopInputGuard
    try {
        $script:NativeDesktopInputGuard=[Func[string]]{return 'fixture control changed'}
        $wireInput.observation_deadline=[long]::MaxValue
        $caught=$null
        try { $null=Invoke-NativeDesktopInput ($wireInput | ConvertTo-Json | ConvertFrom-Json) } catch { $caught=$_ }
        Assert-Observation ($null -ne $caught -and $caught.Exception.Message -match 'fixture control changed') 'Input boundary lost its warm-worker guard'
    } finally { $script:NativeDesktopInputGuard=$savedGuard }
    foreach($value in @('0','601','-1','1.5','NaN','9999999999999999999999')) {
        Assert-ObservationFailure (Invoke-NativeDesktopCli @('screenshot','--max-age-seconds',$value)) '(integer|1-600)'
    }
    foreach($value in @('1','600')) {
        $parsed=ConvertTo-NativeDesktopRequest @('screenshot','--max-age-seconds',$value)
        Assert-Observation ($parsed['max-age-seconds'] -eq [int]$value) 'Age bound rejected'
    }
    Assert-ObservationFailure (Invoke-NativeDesktopCli @('input','key','--observation','bad')) 'Invalid observation'
    Assert-ObservationFailure (Invoke-NativeDesktopCli @('screenshot','--max-age-seconds','1','--max-age-seconds','2')) 'Duplicate'
    Assert-ObservationFailure (Invoke-NativeDesktopCli @('screenshot','--max-age-seconds')) 'Missing value'
    $captureDirectory=Join-Path $script:ObservationRoot 'captures'
    $automatic=Invoke-NativeDesktopCli @('screenshot','--monitor','fixture-monitor','--compact')
    $secondAutomatic=Invoke-NativeDesktopCli @('screenshot','--monitor','fixture-monitor')
    Assert-Observation ($automatic.ok -and $secondAutomatic.ok -and $automatic.result.output -cne $secondAutomatic.result.output -and
        (Split-Path -Parent $automatic.result.output) -eq $captureDirectory -and $automatic.result.output -match '\.png$') 'Default screenshot did not select a unique private PNG path'
    Assert-NativeDesktopPrivatePath $captureDirectory
    $first=New-ObservationCapture
    $post=Invoke-ObservationInput $first @('--settle-ms','0','--observe')
    Assert-Observation ($post.ok -and $post.result.observation.last_input.input_id -ceq $post.result.input_id -and
        (Split-Path -Parent $post.result.observation.output) -eq $captureDirectory -and $post.result.observation.output -match '\.png$') 'Bare --observe did not return a private follow-up path'
    $first=New-ObservationCapture
    $postPath=Join-Path $script:ObservationRoot 'post.png'
    $before=$script:ObservationInputs
    Assert-ObservationFailure (Invoke-ObservationInput $first @('--output',$postPath)) 'requires --observe'
    Assert-ObservationFailure (Invoke-ObservationInput $first @('--observe',$postPath,'--output',$postPath)) 'Choose one'
    Assert-Observation ($script:ObservationInputs -eq $before) 'Conflicting output options reached input'
    $post=Invoke-ObservationInput $first @('--observe','--output',$postPath,'--settle-ms','0')
    Assert-Observation ($post.ok -and $post.result.observation.output -ceq $postPath) '--output did not select the follow-up path'
    $first=New-ObservationCapture
    $post=Invoke-ObservationInput $first @('--observe',$postPath,'--settle-ms','0')
    Assert-Observation ($post.ok -and $post.result.input_delivery -eq 'completed' -and $post.result.observation.observation_id -cmatch '\A[0-9a-f]{32}\z') 'Post capture lost completed input or fresh authority'
    Assert-Observation ($post.result.input_id -cmatch '\A[0-9a-f]{32}\z' -and $post.result.input.input_id -ceq $post.result.input_id -and
        $post.result.observation.last_input.input_id -ceq $post.result.input_id -and $post.result.observation_id -ceq $first.result.observation_id -and
        $post.result.observation.generation -ceq $first.result.generation -and $post.result.observation.artifact_sha256 -ceq ('b'*64) -and
        $post.result.observation.output -ceq $postPath -and $post.result.settle_ms -eq 0) 'Windows receipt cannot link the original capture, input and follow-up'
    $first=New-ObservationCapture
    Assert-Observation ($first.result.last_input.input_id -ceq $post.result.input_id) 'A separate capture lost the completed input summary'
    $before=$script:ObservationInputs
    $refused=Invoke-ObservationInput $first @('--settle-ms','1')
    Assert-ObservationFailure $refused 'requires --observe'
    Assert-Observation ($refused.input_id -cmatch '\A[0-9a-f]{32}\z' -and $refused.input_delivery -eq 'not-sent' -and
        $refused.observation_id -ceq $first.result.observation_id -and $script:ObservationInputs -eq $before) 'Preflight refusal lost input identity or attempted dispatch'
    Assert-ObservationFailure (Invoke-ObservationInput $first @('--observe',$postPath,'--settle-ms','5001')) '0-5000'
    $script:ObservationPostFail=$true;$before=$script:ObservationInputs
    $post=Invoke-ObservationInput $first @('--observe',$postPath)
    $script:ObservationPostFail=$false
    Assert-Observation (-not $post.ok -and $post.input_delivery -eq 'completed' -and $post.dispatch_stage -eq 'post-input-observation' -and
        $post.input_id -ceq $post.result.input_id -and $post.result.submitted -and $script:ObservationInputs -eq $before+1) 'Failed post capture lost completion or replayed input'
    Assert-ObservationFailure (Invoke-ObservationInput $first) 'latest successful'
    & {
        function Start-Sleep([int]$Milliseconds) {
            Assert-Observation ($Milliseconds -eq 120) 'Default settle time differs from Linux'
            $state=Read-NativeDesktopState (Get-NativeDesktopContext)
            $state.last_input.input_id='a'*32
            Write-NativeDesktopState $state -KeepObservation
        }
        $first=New-ObservationCapture
        $before=$script:ObservationCalls
        $post=Invoke-ObservationInput $first @('--observe',$postPath)
        Assert-ObservationFailure $post 'latest input changed'
        Assert-Observation ($post.input_delivery -eq 'completed' -and $script:ObservationCalls -eq $before+1) 'A superseded input triggered capture or lost completion'
    }
    $first=New-ObservationCapture
    [IO.File]::WriteAllText($postPath,'preserve')
    $before=$script:ObservationInputs
    Assert-ObservationFailure (Invoke-ObservationInput $first @('--observe',$postPath)) 'NEW .png'
    Assert-Observation ($script:ObservationInputs -eq $before -and [IO.File]::ReadAllText($postPath) -eq 'preserve') 'Post capture preflight injected or overwrote'
    Remove-Item -LiteralPath $postPath
    Assert-Observation (Invoke-ObservationInput $first).ok 'Preflight consumed observation'
    $first=New-ObservationCapture
    $mapPath=Join-Path $script:ObservationRoot 'capture.json'
    [IO.File]::WriteAllText($mapPath,($first|ConvertTo-Json -Depth 12))
    $mapped=Invoke-NativeDesktopCli @('map-point','--capture',$mapPath,'--view-width','320','--view-height','240','--x','160','--y','120')
    Assert-Observation ($mapped.ok -and $mapped.x -eq 320 -and $mapped.y -eq 240 -and -not $mapped.authority_renewed) 'Map aid lost scale or renewed authority'
    $mapped=Invoke-NativeDesktopCli @('map-point','--capture',$mapPath,'--view-width','320','--view-height','240','--view-origin-x','20','--view-origin-y','30',
        '--x','180','--y','150','--to-x','339','--to-y','269')
    Assert-Observation ($mapped.ok -and $mapped.point_space -eq 'presented-view' -and $mapped.x -eq 320 -and $mapped.y -eq 240 -and
        ($mapped.input_args -join ' ') -ceq '--x 320 --y 240 --to-x 638 --to-y 478') 'View margins or drag endpoint were mapped incorrectly'
    $sourceArgs=@('map-point','--capture',$mapPath,'--image-coordinates','--x','639','--y','479')
    $mapped=Invoke-NativeDesktopCli $sourceArgs
    Assert-Observation ($mapped.ok -and $mapped.point_space -eq 'source-image' -and $mapped.x -eq 639 -and $mapped.y -eq 479) 'Source pixels were scaled or offset'
    Assert-ObservationFailure (Invoke-NativeDesktopCli ($sourceArgs+@('--view-origin-x','1'))) 'dimensions or margins'
    Assert-ObservationFailure (Invoke-NativeDesktopCli ($sourceArgs+@('--to-x','100'))) 'both endpoints'
    Assert-ObservationFailure (Invoke-NativeDesktopCli ($sourceArgs+@('--to-x','640','--to-y','0'))) 'outside displayed image'
    Assert-Observation ($first.result.observation_id -cmatch '\A[0-9a-f]{32}\z' -and $first.result.capture_freshness.max_age_seconds -eq 60) 'Default receipt missing'
    Assert-Observation (($first.result.geometry -join ',') -eq '640,480') 'Capture geometry is not a shared dimension pair'
    $status = Invoke-NativeDesktopCli @('status')
    Assert-Observation ($status.observation_id -ceq $first.result.observation_id -and $status.capture_freshness.fresh) 'Status lost capture authority'
    $second=New-ObservationCapture
    Assert-ObservationFailure (Invoke-NativeDesktopCli $sourceArgs) 'stale or changed'
    Assert-ObservationFailure (Invoke-ObservationInput $first) 'latest successful'
    # Use real lease admission/receipt validation; mock only its ownership
    # mutex boundary. A monitoring image must not outlive later lease input.
    $leaseId=[guid]::NewGuid().ToString('N')
    $leaseRoot=Get-NativeHeldRoot; New-NativeDesktopPrivateDirectory $leaseRoot
    $leaseDirectory=Join-Path $leaseRoot $leaseId; New-NativeDesktopPrivateDirectory $leaseDirectory
    New-NativeHeldOwnership $leaseDirectory
    Write-NativeHeldJson (Join-Path $leaseDirectory 'request.json') @{version=1;id=$leaseId;directory=$leaseDirectory;task=('Basaltwater-Hold-'+$leaseId);name=('Local\Basaltwater.Hold.'+$leaseId)}
    Write-NativeHeldJson (Join-Path $leaseRoot 'active.json') @{id=$leaseId}
    $duringLease=New-ObservationCapture
    Assert-Observation ($null -eq $duringLease.result.observation_id -and $duringLease.result.observation_unavailable_reason -eq 'keyboard-lease-active-or-uncertain') 'Active lease minted input authority'
    Assert-Observation ($duringLease.result.capture_freshness.fresh) 'Monitoring capture lost image age'
    # Settle during capture: eligibility must be fixed before pixels are taken.
    $script:ObservationCaptureHook={
        Write-NativeHeldJson (Join-Path $leaseDirectory 'receipt.json') @{version=1;lease=$leaseId;cleanup_confirmed=$true;scheduler_retired=$true}
    }
    $settlingCapture=New-ObservationCapture
    $script:ObservationCaptureHook=$null
    Assert-Observation ($null -eq $settlingCapture.result.observation_id) 'Lease settling during capture minted authority'
    $status=Invoke-NativeDesktopCli @('status')
    Assert-Observation ($null -eq $status.observation_id) 'Monitoring screenshot retained old authority'
    $before=$script:ObservationCalls
    Assert-ObservationFailure (Invoke-ObservationInput $second) 'latest successful'
    Assert-Observation ($script:ObservationCalls -eq $before) 'Pre-lease observation reached input worker after cleanup'
    foreach ($uncertain in @('release','scheduler','ownership')) {
        Write-NativeHeldJson (Join-Path $leaseRoot 'active.json') @{id=$leaseId}
        Write-NativeHeldJson (Join-Path $leaseDirectory 'receipt.json') @{version=1;lease=$leaseId;cleanup_confirmed=($uncertain -ne 'release');scheduler_retired=($uncertain -ne 'scheduler')}
        $script:ObservationClosedOwnership=$uncertain -ne 'ownership'
        $uncertainCapture=New-ObservationCapture
        Assert-Observation ($null -eq $uncertainCapture.result.observation_id -and $uncertainCapture.result.observation_unavailable_reason -eq 'keyboard-lease-active-or-uncertain') "Uncertain $uncertain minted input authority"
    }
    $script:ObservationClosedOwnership=$true
    $afterLease=New-ObservationCapture
    Assert-Observation ($afterLease.result.observation_id -cmatch '\A[0-9a-f]{32}\z' -and $null -eq $afterLease.result.observation_unavailable_reason) 'Settled lease prevented new capture authority'
    Assert-Observation ($script:ObservationOwnershipChecks -ge 2 -and -not (Test-Path -LiteralPath (Join-Path $leaseRoot 'active.json'))) 'Capture did not confirm ownership and retire the lease marker'
    Assert-Observation (Invoke-ObservationInput $afterLease).ok 'Fresh capture after confirmed cleanup could not authorize input'
    $second=New-ObservationCapture
    # Independent inventory has expired; status/windows cannot renew the receipt.
    $script:ObservationUtc += 61
    $script:ObservationTicks += 59L * [Diagnostics.Stopwatch]::Frequency
    $null=Invoke-NativeDesktopCli @('status'); $null=Invoke-NativeDesktopCli @('windows')
    Assert-Observation (Invoke-ObservationInput $second).ok 'Screenshot did not authorize input independently'
    Assert-ObservationFailure (Invoke-ObservationInput $second) 'latest successful'
    $longCapture=New-ObservationCapture -Extra @('--max-age-seconds','180')
    $longReceipt=(Read-NativeDesktopState (Get-NativeDesktopContext)).observation
    $script:ObservationTicks += 90L * [Diagnostics.Stopwatch]::Frequency
    $script:ObservationUtc += 90
    Assert-Observation (Invoke-ObservationInput $longCapture).ok 'Long reasoning exhausted an explicitly selected age'
    Assert-Observation ($script:ObservationLastInput.observation_deadline -eq ([long]$longReceipt.admitted+180L*[Diagnostics.Stopwatch]::Frequency)) 'CLI dropped the capture deadline at the worker boundary'
    $boundaryCapture=New-ObservationCapture
    $script:ObservationTicks += 60L * [Diagnostics.Stopwatch]::Frequency
    Assert-Observation (Invoke-ObservationInput $boundaryCapture).ok 'Age boundary differs from Linux'
    $capture=New-ObservationCapture
    $state=Read-NativeDesktopState (Get-NativeDesktopContext)
    $state.windows=@(); Write-NativeDesktopState $state -KeepObservation
    $wrong=$capture | ConvertTo-Json -Depth 10 | ConvertFrom-Json
    $wrong.result.observation_id='a'*32
    Assert-ObservationFailure (Invoke-ObservationInput $wrong) 'latest successful'
    $targetArguments=@('input','key','--key','A','--window','124','--identity','fixture-identity','--generation',$capture.generation,'--width','640','--height','480','--observation',$capture.result.observation_id)
    $before=$script:ObservationCalls
    Assert-ObservationFailure (Invoke-NativeDesktopCli $targetArguments) 'target mismatch'
    $targetArguments[5]='123'; $targetArguments[7]='different-identity'
    Assert-ObservationFailure (Invoke-NativeDesktopCli $targetArguments) 'target mismatch'
    $targetArguments[7]='fixture-identity'; $targetArguments[11]='641'
    Assert-ObservationFailure (Invoke-NativeDesktopCli $targetArguments) 'geometry mismatch'
    Assert-Observation ($script:ObservationCalls -eq $before) 'Mismatched observation reached worker'
    # Failed capture retains the preceding receipt, even if inventory was cleared.
    $script:ObservationFail=$true
    Assert-ObservationFailure (Invoke-NativeDesktopCli @('screenshot','--monitor','fixture-monitor','--output',(Join-Path $script:ObservationRoot 'failed.png'))) 'outcome unknown'
    $script:ObservationFail=$false
    Assert-Observation (Invoke-ObservationInput $capture).ok 'Failed capture replaced old receipt'
    $capture=New-ObservationCapture
    $script:ObservationFail=$true
    $unknown=Invoke-ObservationInput $capture
    Assert-ObservationFailure $unknown 'outcome unknown'
    $script:ObservationFail=$false
    $next=New-ObservationCapture
    Assert-Observation ($unknown.input_delivery -eq 'unknown' -and $next.result.last_input.input_id -ceq $unknown.input_id -and
        $next.result.last_input.input_delivery -eq 'unknown' -and -not $next.result.last_input.PSObject.Properties['error']) 'Capture lost the uncertain input summary or exposed its error'
    Assert-ObservationFailure (Invoke-ObservationInput $capture) 'latest successful'
    $capture=New-ObservationCapture -Monitor
    Assert-ObservationFailure (Invoke-ObservationInput $capture) 'window capture required'
    $capture=New-ObservationCapture
    $script:ObservationUtc -= 5000 # UTC rollback must never extend token lifetime.
    $script:ObservationTicks += 60L * [Diagnostics.Stopwatch]::Frequency + 1L
    $null=Invoke-NativeDesktopCli @('windows'); $null=Invoke-NativeDesktopCli @('status')
    Assert-ObservationFailure (Invoke-ObservationInput $capture) 'expired'
    $capture=New-ObservationCapture
    $script:ObservationTicks--
    Assert-ObservationFailure (Invoke-ObservationInput $capture) 'expired'
    $script:ObservationCaptureDelay=2L * [Diagnostics.Stopwatch]::Frequency
    $capture=New-ObservationCapture -Extra @('--max-age-seconds','1')
    Assert-Observation (-not $capture.result.capture_freshness.fresh -and $capture.result.capture_freshness.age_seconds -eq 2) 'Capture duration was excluded from age'
    Assert-ObservationFailure (Invoke-ObservationInput $capture) 'expired'
    $script:ObservationCaptureDelay=0L
    $capture=New-ObservationCapture
    $null=Invoke-NativeDesktopCli @('control','disabled','--human-approved')
    $null=Invoke-NativeDesktopCli @('control','enabled','--human-approved')
    Assert-ObservationFailure (Invoke-ObservationInput $capture) 'latest successful'
    $capture=New-ObservationCapture
    $script:ObservationSession='fixture-session-2'
    Assert-ObservationFailure (Invoke-ObservationInput $capture) 'Stale session generation'
    $capture=New-ObservationCapture
    Assert-Observation ($null -eq $capture.result.last_input) 'A new session inherited input evidence'
    $before=$script:ObservationCalls
    $script:ObservationUtc--
    Assert-ObservationFailure (Invoke-NativeDesktopCli @('window','focus','--window','123','--identity','fixture-identity','--generation',$capture.generation)) 'not recently observed'
    Assert-Observation ($script:ObservationCalls -eq $before) 'Legacy inventory survived UTC rollback'
    $script:ObservationUtc++
    # Existing shell handoff callers clear references through the default writer.
    $state=Read-NativeDesktopState (Get-NativeDesktopContext)
    $state.windows=@(); $state.elements=@(); Write-NativeDesktopState $state
    Assert-ObservationFailure (Invoke-ObservationInput $capture) 'latest successful'
    $capture=New-ObservationCapture
    $null=Invoke-NativeDesktopCli @('window','focus','--window','123','--identity','fixture-identity','--generation',$capture.generation)
    Assert-ObservationFailure (Invoke-ObservationInput $capture) 'latest successful'
    & {
        function Start-NativeHeldLease($Request,$Context,$State) { throw 'fixture hold attempt failed' }
        $heldCapture=New-ObservationCapture
        $hold=@('input','hold','--key','W','--milliseconds','500','--window','123','--identity','fixture-identity','--generation',$heldCapture.generation,
            '--width','640','--height','480','--observation',$heldCapture.result.observation_id)
        Assert-ObservationFailure (Invoke-NativeDesktopCli $hold) 'fixture hold attempt failed'
        Assert-ObservationFailure (Invoke-ObservationInput $heldCapture) 'latest successful'
    }
    $heldCapture=New-ObservationCapture
    & {
        $script:PostHoldLocks=0
        function Enter-NativeDesktopLock($Context){$script:PostHoldLocks++;if($script:PostHoldLocks -gt 1){throw 'fixture controller busy after hold'};return $null}
        function Start-NativeHeldLease($Request,$Context,$State){return @{id=('a'*32)}}
        function Wait-NativeHeldLease($Record){return @{ok=$true;lease=$Record.id;duration_completed=$true}}
        $post=Invoke-NativeDesktopCli @('input','hold','--key','W','--milliseconds','500','--window','123','--identity','fixture-identity','--generation',$heldCapture.generation,
            '--width','640','--height','480','--observation',$heldCapture.result.observation_id,'--observe',(Join-Path $script:ObservationRoot 'held-post.png'))
        Assert-Observation (-not $post.ok -and $post.input_delivery -eq 'completed' -and $post.result.duration_completed) 'Post-hold lock failure hid completed lease'
    }
    # Version-1 files without the optional receipt remain supported.
    $state=Read-NativeDesktopState (Get-NativeDesktopContext)
    $state.Remove('observation')
    $state.Remove('last_input')
    [IO.File]::WriteAllText((Get-NativeDesktopStatePath), ($state | ConvertTo-Json -Depth 40))
    $legacy=Read-NativeDesktopState (Get-NativeDesktopContext)
    Assert-Observation ($null -eq $legacy.observation -and $null -eq $legacy.last_input) 'Legacy state rejected'
    Write-Host 'Observation offline contract tests passed (mocked desktop).'
} finally {
    Assert-NativeDesktopPrivatePath $script:ObservationRoot
    Remove-Item -LiteralPath $script:ObservationRoot -Recurse -Force
}
