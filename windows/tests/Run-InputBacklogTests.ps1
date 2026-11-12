<# Offline input backlog regressions. Never calls desktop/input APIs. #>
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '..\Desktop.Common.ps1')
Initialize-NativeDesktopTypes
function Assert-Backlog([bool]$Value,[string]$Message){if(-not $Value){throw $Message}}
$temporary=Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-input-backlog-'+[guid]::NewGuid().ToString('N'))
New-NativeDesktopPrivateDirectory $temporary
$flags=[Reflection.BindingFlags]'Static,NonPublic'
try {
    $checkWindow=[Basaltwater.Desktop.Native].GetMethod('CheckHeldWindow',$flags)
    $guardPlan=[Basaltwater.Desktop.HeldPlan]@{window=123;identity='fixture';width=640;height=480;layout=1}
    foreach($case in @('foreground','identity','geometry')){
        $window=[Basaltwater.Desktop.Window]@{identity='fixture';width=640;height=480;active=$true}
        if($case -eq 'foreground'){$window.active=$false}
        if($case -eq 'identity'){$window.identity='changed'*100}
        if($case -eq 'geometry'){$window.width=800}
        $caught=$null;try{$null=$checkWindow.Invoke($null,@($guardPlan,$window,456L))}catch{$caught=$_.Exception.InnerException}
        Assert-Backlog ($null -ne $caught -and $caught.Message -eq ($case+'-changed')) 'Held target stop code was combined or unstable'
        $guardReceipt=[Basaltwater.Desktop.HeldReceipt]::new()
        $null=[Basaltwater.Desktop.Native].GetMethod('HeldFailureEvidence',$flags).Invoke($null,@($guardReceipt,$caught))
        Assert-Backlog ($guardReceipt.expected.Length -le 256 -and $guardReceipt.observed.Length -le 256 -and $guardReceipt.stop_field -eq $case) 'Held target evidence lost bounds or field'
    }
    $caught=$null;try{$null=[Basaltwater.Desktop.Native].GetMethod('CheckHeldLayout',$flags).Invoke($null,@($guardPlan,2L))}catch{$caught=$_}
    Assert-Backlog ($null -ne $caught -and $caught.Exception.Message -match 'layout-changed') 'Layout stop code lost'
    $marker=[UIntPtr]::new([uint64]1234)
    $inputs=[Basaltwater.Desktop.Native].GetMethod('HeldInputs',$flags)
    foreach($button in @(@(1,2),@(2,8),@(4,32))){
        $keys=[Basaltwater.Desktop.HeldKey[]]@(@{vk=$button[0];flags=$button[1];mouse=$true})
        foreach($up in @($false,$true)){
            $batch=$inputs.Invoke($null,@($keys,1,$up,$marker))
            Assert-Backlog ($batch.Length -eq 1 -and $batch[0].data.mouse.flags -eq ($button[1]*[Math]::Pow(2,[int]$up)) -and $batch[0].data.mouse.extra -eq $marker) 'Mouse ownership flags/marker changed'
        }
    }
    $mouseKey=[Basaltwater.Desktop.Native].GetMethod('HeldMouseKey',$flags)
    foreach($pair in @(@(0x201,1),@(0x202,1),@(0x204,2),@(0x205,2),@(0x207,4),@(0x208,4),@(0x200,0),@(0x20a,0))){
        Assert-Backlog ($mouseKey.Invoke($null,@($pair[0])) -eq $pair[1]) 'Mouse takeover mapped unrelated input'
    }
    $move=[Basaltwater.Desktop.Native].GetMethod('PointerMoveForBounds',$flags)
    $event=$move.Invoke($null,@(-640,-20,$marker,-640,-20,1280,960))
    Assert-Backlog ($event.data.mouse.x -eq 0 -and $event.data.mouse.y -eq 0 -and $event.data.mouse.flags -eq 0xc001 -and $event.data.mouse.extra -eq $marker) 'Drag lost virtual desktop origin or marker'
    $event=$move.Invoke($null,@(639,939,$marker,-640,-20,1280,960))
    Assert-Backlog ($event.data.mouse.x -eq 65535 -and $event.data.mouse.y -eq 65535) 'Drag endpoint normalization changed'
    $builder=[Basaltwater.Desktop.Native].GetMethod('BuildHeldTimeline',$flags)
    $plan=$builder.Invoke($null,@('0:W+J,100:J,200:A+J,300:-',500,[Func[uint16,uint32]]{param($vk)return [uint32]($vk%100+1)}))
    $wire=[Basaltwater.Desktop.HeldPlan]($plan|ConvertTo-Json -Depth 12|ConvertFrom-Json)
    Assert-Backlog ($wire.keys.Length -eq 3 -and ($wire.timeline.mask -join ',') -eq '3,2,6,0') 'JSON hop lost the typed held timeline'
    $storeDir=Join-Path $temporary 'timeline';New-NativeDesktopPrivateDirectory $storeDir
    [Basaltwater.Desktop.HeldStore]::Create($storeDir)
    $store=[Basaltwater.Desktop.HeldStore]::new($storeDir,('Local\Basaltwater.HeldTest.'+[guid]::NewGuid().ToString('N')))
    try {
        $script:BacklogBatches=@();$script:BacklogReject=$false
        $press=[Basaltwater.Desktop.Native].GetMethod('HeldPress',$flags)
        $send=[Management.Automation.LanguagePrimitives]::ConvertTo({param($batch)
            $script:BacklogBatches+=,$batch
            if($script:BacklogReject){return [uint32]0}
            return [uint32]$batch.Length
        },$press.GetParameters()[6].ParameterType)
        $heartbeat=[Func[Basaltwater.Desktop.HeldStore+State,int]]{param($s)$s.heartbeat=[Diagnostics.Stopwatch]::GetTimestamp();return 0}
        $locked=$store.GetType().GetMethod('Locked').MakeGenericMethod([int])
        $null=$locked.Invoke($store,@($heartbeat))
        $receipt=[Basaltwater.Desktop.HeldReceipt]::new();$now=[Diagnostics.Stopwatch]::GetTimestamp();$end=$now+5*[Diagnostics.Stopwatch]::Frequency
        $null=$press.Invoke($null,@($store,$plan,$marker,$receipt,$now,$end,$send))
        $transition=[Basaltwater.Desktop.Native].GetMethod('HeldTransition',$flags)
        $null=$transition.Invoke($null,@($store,$plan,$marker,$receipt,2,[Diagnostics.Stopwatch]::GetTimestamp(),$end,$send))
        $null=$transition.Invoke($null,@($store,$plan,$marker,$receipt,6,[Diagnostics.Stopwatch]::GetTimestamp(),$end,$send))
        Assert-Backlog ($script:BacklogBatches.Count -eq 3 -and $script:BacklogBatches[1].Length -eq 1 -and ($script:BacklogBatches[1][0].data.key.flags -band 2) -ne 0 -and $script:BacklogBatches[2].Length -eq 1) 'Overlap transition replayed retained key'
        $script:BacklogReject=$true;$caught=$null
        try{$null=$transition.Invoke($null,@($store,$plan,$marker,$receipt,0,[Diagnostics.Stopwatch]::GetTimestamp(),$end,$send))}catch{$caught=$_}
        Assert-Backlog ($null -ne $caught) 'Partial timeline release hidden'
        $phase=[Func[Basaltwater.Desktop.HeldStore+State,int]]{param($s)return $s.phase}
        Assert-Backlog ($locked.Invoke($store,@($phase)) -eq 3) 'Failed timeline up did not retain uncertain ownership'
    } finally {$store.Dispose()}
    $expiredDir=Join-Path $temporary 'expired';New-NativeDesktopPrivateDirectory $expiredDir
    [Basaltwater.Desktop.HeldStore]::Create($expiredDir)
    $store=[Basaltwater.Desktop.HeldStore]::new($expiredDir,('Local\Basaltwater.HeldTest.'+[guid]::NewGuid().ToString('N')))
    try {
        $script:BacklogBatches=@();$script:BacklogReject=$false
        $plan.observation_deadline=1
        $null=$locked.Invoke($store,@($heartbeat))
        $now=[Diagnostics.Stopwatch]::GetTimestamp();$caught=$null
        try{$null=$press.Invoke($null,@($store,$plan,$marker,[Basaltwater.Desktop.HeldReceipt]::new(),$now,($now+[Diagnostics.Stopwatch]::Frequency),$send))}catch{$caught=$_}
        Assert-Backlog ($null -ne $caught -and $script:BacklogBatches.Count -eq 0) 'Expired capture injected held down after ownership wait'
        $idleTimeline=$builder.Invoke($null,@('0:-,100:W',500,[Func[uint16,uint32]]{return [uint32]30}))
        $idleTimeline.observation_deadline=1
        $active=[Func[Basaltwater.Desktop.HeldStore+State,int]]{param($s)$s.phase=1;$s.mask=0;$s.heartbeat=[Diagnostics.Stopwatch]::GetTimestamp();return 0}
        $null=$locked.Invoke($store,@($active));$caught=$null
        try{$null=$transition.Invoke($null,@($store,$idleTimeline,$marker,[Basaltwater.Desktop.HeldReceipt]::new(),1,[Diagnostics.Stopwatch]::GetTimestamp(),([Diagnostics.Stopwatch]::GetTimestamp()+[Diagnostics.Stopwatch]::Frequency),$send))}catch{$caught=$_}
        Assert-Backlog ($null -ne $caught -and $script:BacklogBatches.Count -eq 0) 'Initially empty timeline bypassed first-down capture expiry'
    } finally {$store.Dispose()}
    & {
        function Get-NativeDesktopStatePath {return Join-Path $temporary 'state.json'}
        function Get-NativeDesktopContext {return @{Sid='fixture';Session='fixture-session'}}
        function Pin-NativeWarmProcess($Record,[int]$ProcessId,[long]$Created){if($Created -ne 123){throw 'stale caller'};return [Diagnostics.Process]::GetCurrentProcess()}
        function Invoke-NativeDesktopWorkerBody($Request){$reason=$script:NativeDesktopInputGuard.Invoke();if($reason){throw $reason};$script:BacklogEffects++;return @{submitted=$true}}
        $script:BacklogEffects=0
        Write-NativeHeldJson (Get-NativeDesktopStatePath) @{owner='fixture';session='fixture-session';revision='r';generation='g';mode='enabled'}
        $envelope=[pscustomobject]@{version=1;id=('c'*32);sid='fixture';session='fixture-session';directory=$temporary;pid=$PID;created=123;revision='r';generation='g';deadline=([Diagnostics.Stopwatch]::GetTimestamp()+8*[Diagnostics.Stopwatch]::Frequency);request=@{command='input'}}
        $reply=Invoke-NativeColdEnvelope $envelope
        Assert-Backlog ($reply.ok -and $script:BacklogEffects -eq 1) 'Cold envelope refused valid pinned request'
        $envelope.created=124;$reply=Invoke-NativeColdEnvelope $envelope
        Assert-Backlog (-not $reply.ok -and -not $reply.submitted -and $script:BacklogEffects -eq 1) 'Cold worker admitted stale caller'
        $envelope.created=123;$envelope.deadline=0;$reply=Invoke-NativeColdEnvelope $envelope
        Assert-Backlog (-not $reply.ok -and -not $reply.submitted) 'Cold worker admitted expired request'
        $envelope.deadline=[Diagnostics.Stopwatch]::GetTimestamp()+8*[Diagnostics.Stopwatch]::Frequency;$envelope.generation='changed'
        $reply=Invoke-NativeColdEnvelope $envelope
        Assert-Backlog (-not $reply.ok -and -not $reply.submitted -and $script:BacklogEffects -eq 1) 'Cold worker ignored generation change'
    }
    Write-Host 'Input backlog event, ownership, timeline and cold envelope tests passed (all APIs mocked).'
} finally {
    $resolved=[IO.Path]::GetFullPath($temporary)
    if(-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe backlog cleanup'}
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
