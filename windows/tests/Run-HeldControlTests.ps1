<# Offline control reads only: no desktop APIs, hooks, leases or input. #>
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '..\Desktop.Common.ps1')
Initialize-NativeDesktopTypes
function Assert-Control([bool]$Value,[string]$Message) {if(-not $Value){throw $Message}}
$temporary=Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-control-tests-'+[guid]::NewGuid().ToString('N'))
New-NativeDesktopPrivateDirectory $temporary
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$path=Join-Path $temporary 'state.json'
$lease=Join-Path $temporary 'lease';New-NativeDesktopPrivateDirectory $lease
$plan=[Basaltwater.Desktop.HeldPlan]::new()
$state=@{version=1;owner=$sid;session='fixture-session';revision=('a'*32);generation=('b'*32);mode='enabled';windows=@();elements=@()}
function Publish-Control {Write-NativeHeldJson $path $state 2097152}
function Assert-Reason([string]$Expected) {$actual=[string]$guard.Check();Assert-Control ($actual -ceq $Expected) "Expected control reason '$Expected', received '$actual'"}
try {
    Publish-Control
    $guard=[Basaltwater.Desktop.HeldControlGuard]::new($path,$lease,$sid,$state.session,$state.revision,$state.generation,$plan)
    Assert-Reason ''
    # Every replacement must be seen by the same object. No inode, permission,
    # JSON, generation or enabled-state observation may survive a Check call.
    foreach($field in @('generation','revision','session','owner','mode')) {
        $saved=$state[$field];$state[$field]='revoked';Publish-Control
        Assert-Reason 'control-changed'
        Assert-Control ($plan.control_expected -cne $plan.control_observed -and $plan.control_observed.Contains('revoked')) 'Control evidence lost'
        $state[$field]=$saved;Publish-Control;Assert-Reason ''
    }
    foreach($bad in @('{}','[]','{"version":1,','{"version":1,"version":1}',
        '{"version":1,"mode":"enabled","mode":"disabled"}',
        (($state|ConvertTo-Json -Compress)+' trailing'),
        (($state|ConvertTo-Json -Compress).Replace('"enabled"','true')),
        ('{"version":1,"data":'+('['*65)+'0'+(']'*65)+'}'))) {
        [IO.File]::WriteAllText($path,$bad);Assert-Reason 'control-unavailable'
    }
    [IO.File]::WriteAllBytes($path,[byte[]]@(0xff,0xfe,0xff));Assert-Reason 'control-unavailable'
    [IO.File]::WriteAllText($path,(' '*2097153));Assert-Reason 'control-unavailable'
    Publish-Control
    $locked=[IO.File]::Open($path,'Open','ReadWrite','None')
    try {Assert-Reason 'control-unavailable'} finally {$locked.Dispose()}
    Remove-Item -LiteralPath $path;Assert-Reason 'control-unavailable'
    Publish-Control
    [IO.File]::WriteAllText((Join-Path $lease 'cancel'),'cancel');Assert-Reason 'cancelled'
    Remove-Item -LiteralPath (Join-Path $lease 'cancel');Assert-Reason ''
    # Reject stream/UNC/device paths at construction, without opening them.
    foreach($unsafe in @('relative.json',($path+':stream'),'\\server\share\state.json','\\?\C:\state.json')) {
        $caught=$null;try{$null=[Basaltwater.Desktop.HeldControlGuard]::new($unsafe,$lease,$sid,'s','r','g',$plan)}catch{$caught=$_}
        Assert-Control ($null -ne $caught) 'Unsafe control path admitted'
    }
    $junction=Join-Path $temporary 'junction'
    [IO.File]::Copy($path,(Join-Path $lease 'state.json'))
    $null=New-Item -ItemType Junction -Path $junction -Target $lease
    try {
        $linked=[Basaltwater.Desktop.HeldControlGuard]::new((Join-Path $junction 'state.json'),$lease,$sid,$state.session,$state.revision,$state.generation,$plan)
        Assert-Control ($linked.Check() -eq 'control-unavailable') 'Reparse ancestor admitted'
    } finally {[IO.Directory]::Delete($junction)}
    # Exercise ACL rejection without modifying any filesystem permissions.
    $private=$guard.GetType().GetMethod('Private',[Reflection.BindingFlags]'Instance,NonPublic')
    $acl=[Security.AccessControl.FileSecurity]::new();$acl.SetOwner([Security.Principal.SecurityIdentifier]::new($sid))
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),'Read','Allow'))
    $caught=$null;try{$private.Invoke($guard,@($acl))}catch{$caught=$_}
    Assert-Control ($null -ne $caught) 'Unsafe ACL admitted'
    $acl=[Security.AccessControl.FileSecurity]::new();$acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-18'))
    $caught=$null;try{$private.Invoke($guard,@($acl))}catch{$caught=$_}
    Assert-Control ($null -ne $caught) 'Wrong ACL owner admitted'
    $peer=[Diagnostics.Process]::GetCurrentProcess()
    try {
        $future=[Diagnostics.Stopwatch]::GetTimestamp()+[Diagnostics.Stopwatch]::Frequency*10
        Assert-Control ([string]::IsNullOrEmpty($guard.CheckWorker($lease,$sid,$state.session,$state.revision,$state.generation,$future,$peer))) 'Current envelope refused'
        Assert-Control ($guard.CheckWorker($lease,$sid,$state.session,$state.revision,$state.generation,1,$peer) -eq 'Desktop request expired') 'Request deadline ignored'
        Assert-Control ($guard.CheckWorker($lease,$sid,$state.session,'changed',$state.generation,$future,$peer) -eq 'Desktop control/generation changed') 'Envelope mismatch ignored'
        [IO.File]::WriteAllText((Join-Path $lease 'stop'),'stop')
        Assert-Control ($guard.CheckWorker($lease,$sid,$state.session,$state.revision,$state.generation,$future,$peer) -eq 'Desktop worker stopped') 'Worker stop ignored'
    } finally {$peer.Dispose()}
    # Guard composition resolves the hold-local implementation from an existing
    # envelope delegate, as cold/warm dispatch does (no provider read remains).
    & {
        $record=@{directory=$lease;sid=$sid;session=$state.session}
        $envelope=@{revision=$state.revision;generation=$state.generation;deadline=1}
        $peer=[Diagnostics.Process]::GetCurrentProcess()
        $callback=[Func[string]]{Get-NativeWarmInputGuardReason $record $envelope $peer}
        try {
            & {
                $heldControl=$guard
                $shadow=(Get-Command Invoke-NativeHeldDriver).ScriptBlock.Ast.FindAll({param($node)
                    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-NativeWarmInputGuardReason'
                },$true)
                Assert-Control ($shadow.Count -eq 1) 'Hold envelope guard missing'
                . ([scriptblock]::Create($shadow[0].Extent.Text))
                Assert-Control ($callback.Invoke() -eq 'Desktop request expired') 'Envelope callback lost local fast guard'
            }
        } finally {$peer.Dispose()}
    }
    # Mock admission failure at the immediate pre-send boundary: no native calls.
    $flags=[Reflection.BindingFlags]'Static,NonPublic'
    $cancel=[Threading.EventWaitHandle]::new($false,'ManualReset')
    $activity=[Threading.EventWaitHandle]::new($false,'ManualReset')
    $peer=[Diagnostics.Process]::GetCurrentProcess()
    try {
        $check=[Basaltwater.Desktop.Native].GetMethod('HeldCheck',$flags)
        $never=[Func[string]]{throw 'Physical cancellation reached control/desktop APIs'}
        $null=$activity.Set()
        Assert-Control ($check.Invoke($null,@($plan,$never,$peer,$cancel,$activity)) -eq 'outside-input') 'Physical activity cancellation lost'
        $null=$cancel.Set()
        Assert-Control ($check.Invoke($null,@($plan,$never,$peer,$cancel,$activity)) -eq 'cancelled') 'Cancellation event lost'
    } finally {$cancel.Dispose();$activity.Dispose();$peer.Dispose()}
    $admission=$plan.GetType().GetField('admission',[Reflection.BindingFlags]'Instance,NonPublic')
    $admission.SetValue($plan,[Func[string]]{$plan.control_expected='expected';$plan.control_observed='revoked';return 'control-changed'})
    $caught=$null;try{[Basaltwater.Desktop.Native].GetMethod('HeldAdmission',$flags).Invoke($null,@($plan))}catch{$caught=$_.Exception.GetBaseException()}
    Assert-Control ($caught.Message -eq 'control-changed') 'Immediate guard did not stop'
    $receipt=[Basaltwater.Desktop.HeldReceipt]::new()
    $null=[Basaltwater.Desktop.Native].GetMethod('HeldFailureEvidence',$flags).Invoke($null,@($receipt,$caught))
    Assert-Control ($receipt.stop_field -eq 'control' -and $receipt.expected -eq 'expected' -and $receipt.observed -eq 'revoked') 'Pre-send control evidence lost'
    Write-Host 'Held-control offline fresh reads, replacement, fail-closed and envelope contracts passed (no live input).'
} finally {
    $resolved=[IO.Path]::GetFullPath($temporary)
    if(-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe control test cleanup'}
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
