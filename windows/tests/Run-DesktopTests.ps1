<# Offline contract tests. No desktop APIs, personal content, or live GUI fixtures.
Native compilation and DTO conversion are checked without invoking native methods.
All desktop operations cross the mocked worker; persistent files use a temp root.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '..\Desktop.Common.ps1')
$realWorker = (Get-Command Invoke-NativeDesktopWorker).ScriptBlock
$realContext = (Get-Command Get-NativeDesktopContext).ScriptBlock
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Assert-Failure($Result, [string]$Pattern) {
    Assert-True (-not $Result.ok -and $Result.error -match $Pattern) "Expected $Pattern; received $($Result | ConvertTo-Json -Depth 5 -Compress)"
}
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-desktop-tests-' + [guid]::NewGuid().ToString('N'))
$script:DesktopTestPath = Join-Path $temporary 'state\state.json'
$script:DesktopTestSession = 'fixture-session-1'
$script:DesktopTestNow = 10000L
$script:DesktopTestCalls = @()
$script:DesktopTestFail = $false
function Get-NativeDesktopStatePath { return $script:DesktopTestPath }
function Get-NativeDesktopContext { return @{ Sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value; Session = $script:DesktopTestSession } }
function Get-NativeDesktopTime { return $script:DesktopTestNow }
function Invoke-NativeDesktopWorker($Request, [int]$TimeoutSeconds = 8) {
    $script:DesktopTestCalls += $Request.command
    if ($script:DesktopTestFail) { throw 'Fixture timeout: outcome unknown' }
    switch ($Request.command) {
        ready { return @{ healthy = $true; capture_performed = $false } }
        windows { return @{ windows = @([pscustomobject]@{ id = 123L; pid = 42; identity = '42:birth:class:title'; width = 640; height = 480; x = -640; y = -20 }) } }
        inspect { return @{ elements = @([pscustomobject]@{ reference = 'element-ref'; name = ('x' * 300); role = 'ControlType.Edit'; pid = 42; window = 123; identity = '42:birth:class:title'; runtime = '1.2.3'; path = @(0,1); enabled = $true; focused = $false; actions = @('set-text','focus') }); visited = 2; truncated = $false } }
        default { return @{ outcome_verified = $true } }
    }
}
function Observe-Window {
    $result = Invoke-NativeDesktopCli @('windows')
    Assert-True $result.ok "Inventory failed: $($result | ConvertTo-Json -Depth 5 -Compress)"
    return $result.generation
}
function Window-Action([string]$Generation, [string]$Identity = '42:birth:class:title') {
    return Invoke-NativeDesktopCli @('window','focus','--window','123','--identity',$Identity,'--generation',$Generation)
}
New-NativeDesktopPrivateDirectory $temporary
try {
    & (Join-Path $PSScriptRoot 'Run-EnvironmentTests.ps1')
    & {
        # Identity comes from the token's AuthenticationId, never group-list
        # heuristics. A new boot must invalidate stale observation identities.
        function Get-NativeDesktopAuthenticationId($Token) { return '0000000012345678' }
        function Get-NativeDesktopBoot { return $script:ContextTestBoot }
        $script:ContextTestBoot = '100'
        $first = & $realContext
        $script:ContextTestBoot = '200'
        $second = & $realContext
        Assert-True ($first.Sid -eq $second.Sid -and $first.Session -like '*:0000000012345678:100' -and
            $first.Session -ne $second.Session) 'Token logon/boot identity is not stable or isolated'
    }
    & {
        function Get-NativeDesktopContext { throw 'fixture logon identity unavailable' }
        $ready = Get-NativeDesktopReadiness
        Assert-True (-not $ready.healthy -and $ready.error -match 'identity unavailable') 'Doctor ignored the same identity failure that blocks status/windows'
    }
    # Parse/compile only: not a single user32, capture, UIA or input call.
    Initialize-NativeDesktopTypes
    # Pure event construction with a synthetic keyboard map. No user32 calls.
    $builder = [Basaltwater.Desktop.Native].GetMethod('KeyInputs', [Reflection.BindingFlags]'Static,NonPublic')
    $map = [Func[uint16,uint32]]{ param($vk)
        switch ($vk) { 162 { return 0x1d }; 160 { return 0x2a }; 37 { return 0xe04b }; 36 { return 0xe047 }; 35 { return 0xe04f }; 122 { return 0x57 }; default { return 0x1e } }
    }
    foreach ($name in @('LEFT','HOME','END','PAGEUP','PAGEDOWN','F11')) {
        $single = $builder.Invoke($null, @($name,$false,$map))
        Assert-True ($single.Length -eq 2 -and $single[0].data.key.vk -eq 0 -and $single[0].data.key.scan -ne 0 -and
            ($single[0].data.key.flags -band 8) -ne 0 -and ($single[1].data.key.flags -band 2) -ne 0) 'Raw key omitted scan code or key up'
        if ($name -in @('LEFT','HOME')) { Assert-True ($single[0].data.key.flags -eq 9 -and $single[1].data.key.flags -eq 11) 'Extended key prefix lost' }
    }
    $single = $builder.Invoke($null, @('CTRL+A',$false,$map))
    Assert-True ($single.Length -eq 4 -and $single[1].data.key.flags -eq 8 -and $single[2].data.key.flags -eq 10) 'Nonextended chord gained extended-key flags'
    $maximum = $builder.Invoke($null, @(('A,' * 31 + 'A'),$true,$map))
    Assert-True ($maximum.Length -eq 64) 'Maximum bounded sequence rejected'
    $events = $builder.Invoke($null, @('ctrl+shift+HOME,END,F11', $true, $map))
    Assert-True ($events.Count -eq 10) 'Chord sequence event count changed'
    Assert-True ($events[0].data.key.scan -eq 0x1d -and $events[0].data.key.flags -eq 8 -and
        $events[1].data.key.scan -eq 0x2a -and $events[2].data.key.scan -eq 0x47 -and
        $events[2].data.key.vk -eq 0 -and $events[2].data.key.flags -eq 9 -and
        $events[3].data.key.flags -eq 11 -and $events[4].data.key.scan -eq 0x2a -and
        $events[5].data.key.scan -eq 0x1d -and $events[6].data.key.scan -eq 0x4f -and
        $events[8].data.key.scan -eq 0x57) 'Scan codes, extended flags or reverse releases incorrect'
    foreach ($invalid in @('CTRL+CTRL+A','ALT+A','CTRL+SHIFT+CTRL+A','CTRL+','+A','CTRL','A,,B', ('A,' * 32 + 'A'), 'F13', 'SHIFT+')) {
        $caught = $null
        try { $null = $builder.Invoke($null, @($invalid, $true, $map)) } catch { $caught = $_ }
        Assert-True ($null -ne $caught) "Invalid chord/sequence accepted: $invalid"
    }
    $caught = $null
    try { $null = $builder.Invoke($null, @('A', $false, [Func[uint16,uint32]]{ return 0 })) } catch { $caught = $_ }
    Assert-True ($null -ne $caught) 'Unmapped key accepted'
    $sender = [Basaltwater.Desktop.Native].GetMethod('SendKeys', [Reflection.BindingFlags]'Static,NonPublic')
    $script:SentBatches = @(); $script:PartialDelivery = $false
    $fakeSend = [Management.Automation.LanguagePrimitives]::ConvertTo({ param($batch)
        $script:SentBatches += ,$batch
        if ($script:PartialDelivery -and $script:SentBatches.Count -eq 1) { return [uint32]3 }
        return [uint32]$batch.Length
    }, $sender.GetParameters()[1].ParameterType)
    $null = $sender.Invoke($null, @($events, $fakeSend))
    Assert-True ($script:SentBatches.Count -eq 1 -and $script:SentBatches[0].Length -eq 10) 'Sequence was split across calls'
    $script:SentBatches = @(); $script:PartialDelivery = $true; $caught = $null
    try { $null = $sender.Invoke($null, @($events, $fakeSend)) } catch { $caught = $_ }
    Assert-True ($null -ne $caught -and $caught.Exception.Message -match 'observe before retrying' -and
        $script:SentBatches.Count -eq 2 -and $script:SentBatches[1].Length -eq 3 -and
        $script:SentBatches[1][0].data.key.flags -eq 11 -and $script:SentBatches[1][2].data.key.scan -eq 0x1d) 'Partial delivery did not release the submitted chord without replay'
    $script:SentBatches = @()
    $reject = [Management.Automation.LanguagePrimitives]::ConvertTo({ param($batch)
        $script:SentBatches += ,$batch
        return [uint32]0
    }, $sender.GetParameters()[1].ParameterType)
    $caught = $null
    try { $null = $sender.Invoke($null, @($events, $reject)) } catch { $caught = $_ }
    Assert-True ($null -ne $caught -and $script:SentBatches.Count -eq 1) 'Rejected input attempted replay or unrelated releases'
    $script:SentBatches = @()
    $releaseFails = [Management.Automation.LanguagePrimitives]::ConvertTo({ param($batch)
        $script:SentBatches += ,$batch
        if ($script:SentBatches.Count -eq 1) { return [uint32]7 }
        return [uint32]0
    }, $sender.GetParameters()[1].ParameterType)
    $caught = $null
    try { $null = $sender.Invoke($null, @($events, $releaseFails)) } catch { $caught = $_ }
    Assert-True ($null -ne $caught -and $caught.Exception.Message -match 'release unconfirmed' -and
        $script:SentBatches.Count -eq 2 -and $script:SentBatches[1].Length -eq 1 -and
        $script:SentBatches[1][0].data.key.scan -eq 0x4f) 'Cleanup failure hidden or already released chord cleaned again'
    $help = Invoke-NativeDesktopCli @('input','sequence','--help')
    Assert-True ($help.ok -and $help.help -match '32 chords' -and $help.help -match 'HOME') 'Leaf input help missing keys/limits'
    $dto = [Basaltwater.Desktop.Element]([pscustomobject]@{ reference = 'r'; window = 123L; pid = 42; path = @(0,1); identity = 'i'; runtime = 'r'; name = 'n'; role = 'ControlType.Edit' })
    Assert-True ($dto.path.Length -eq 2 -and $dto.window -eq 123) 'Worker cannot deserialize UIA DTO'
    # Exercise the native outcome rule without calling Win32: SW_RESTORE can
    # resume a minimized window maximized, but cannot leave it minimized.
    $method = [Basaltwater.Desktop.Native].GetMethod('WindowOutcomeMatches', [Reflection.BindingFlags]'Static,NonPublic')
    $outcome = $method.CreateDelegate([Func[string,Basaltwater.Desktop.Window,Basaltwater.Desktop.Window,int,int,int,int,bool]])
    $minimized = [Basaltwater.Desktop.Window]@{ minimized = $true }
    $maximized = [Basaltwater.Desktop.Window]@{ maximized = $true }
    $normal = New-Object Basaltwater.Desktop.Window
    Assert-True ($outcome.Invoke('restore',$minimized,$maximized,0,0,0,0)) 'Restore rejected the previous maximized presentation'
    Assert-True ($outcome.Invoke('restore',$minimized,$normal,0,0,0,0)) 'Restore rejected the previous normal presentation'
    Assert-True (-not $outcome.Invoke('restore',$minimized,$minimized,0,0,0,0)) 'Restore accepted a still-minimized window'
    Assert-True (-not $outcome.Invoke('restore',$maximized,$maximized,0,0,0,0)) 'Restore accepted an unchanged non-minimized maximized window'
    Assert-True ($outcome.Invoke('restore',$maximized,$normal,0,0,0,0)) 'Restore did not accept normal size after maximize'
    $method = [Basaltwater.Desktop.Native].GetMethod('WaitForClose', [Reflection.BindingFlags]'Static,NonPublic')
    $waitForClose = $method.CreateDelegate([Action[Func[bool],Func[bool],Action]])
    $script:ClosePolls = 0; $script:CloseIdentityChecks = 0
    $exists = [Func[bool]]{ $script:ClosePolls++; return $script:ClosePolls -lt 3 }
    $visible = [Func[bool]]{ return $script:ClosePolls -eq 2 }
    $verify = [Action]{ $script:CloseIdentityChecks++ }
    $waitForClose.Invoke($exists, $visible, $verify)
    Assert-True ($script:ClosePolls -eq 3 -and $script:CloseIdentityChecks -eq 1) 'Close did not wait through a hidden phase or verify a visible window'
    $caught = $null
    try { $waitForClose.Invoke([Func[bool]]{ return $true }, [Func[bool]]{ return $false }, $verify) } catch { $caught = $_ }
    Assert-True ($null -ne $caught -and $caught.Exception.Message -match 'outcome not observed') 'A hidden but surviving window was reported closed'
    # Fake provider callbacks exercise the actual observation boundary without UIA calls.
    Add-Type -ReferencedAssemblies (Join-Path ([Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory()) 'WPF\UIAutomationTypes.dll') -TypeDefinition @'
using System;
using System.Windows.Automation;
public static class DesktopInspectionFixture {
    public static void Available() { }
    public static void Disappeared() { throw new ElementNotAvailableException("fixture provider disappeared"); }
    public static void Broken() { throw new InvalidOperationException("fixture provider failure"); }
}
'@
    $method = [Basaltwater.Desktop.Native].GetMethod('InspectAvailable', [Reflection.BindingFlags]'Static,NonPublic')
    $inspect = $method.CreateDelegate([Action[Basaltwater.Desktop.Scan,Action]])
    $scan = New-Object Basaltwater.Desktop.Scan
    $available = [DesktopInspectionFixture].GetMethod('Available').CreateDelegate([Action])
    $inspect.Invoke($scan, $available)
    Assert-True (-not $scan.truncated) 'Healthy inspection was marked incomplete'
    $scan.elements.Add($dto)
    $disappeared = [DesktopInspectionFixture].GetMethod('Disappeared').CreateDelegate([Action])
    $inspect.Invoke($scan, $disappeared)
    Assert-True ($scan.truncated -and $scan.elements.Count -eq 1 -and $scan.elements[0].reference -eq 'r') 'Provider loss discarded the observed prefix or claimed completeness'
    $broken = [DesktopInspectionFixture].GetMethod('Broken').CreateDelegate([Action])
    $caught = $null
    try { $inspect.Invoke($scan, $broken) } catch { $caught = $_ }
    Assert-True ($null -ne $caught -and $caught.Exception.Message -match 'fixture provider failure') 'Inspection swallowed an unrelated provider error'
    # Exercise actual worker transport/timeout using code with NO native APIs.
    $originalSource = $script:NativeDesktopSource
    $fixtureSource = Join-Path $temporary "worker's unicode $([char]0x03bb)"
    New-NativeDesktopPrivateDirectory $fixtureSource
    $fixture = Join-Path $fixtureSource 'Desktop.Common.ps1'
    [IO.File]::WriteAllText($fixture, @'
function Invoke-NativeDesktopWorkerBody($Request) {
    # Autoload a framework module in the isolated worker, without desktop APIs.
    $null = Get-Command Get-Acl -ErrorAction Stop
    return @{ echo = $Request.text; edition = $PSVersionTable.PSEdition;
        major = $PSVersionTable.PSVersion.Major; modules = $env:PSModulePath }
}
'@)
    $savedModulePath = $env:PSModulePath
    $parentModules = Join-Path $temporary 'PowerShell7-only-modules'
    try {
        $script:NativeDesktopSource = $fixtureSource
        $text = "Quotes ' `" unicode $([char]0x03bb) `$literal `nnewline"
        $env:PSModulePath = $parentModules + ';' + $savedModulePath
        $inheritedModules = $env:PSModulePath
        $echo = & $realWorker @{ command = 'fixture'; text = $text }
        Assert-True ($echo.echo -ceq $text) 'Worker transport corrupted Unicode or executed text'
        Assert-True ($echo.edition -eq 'Desktop' -and $echo.major -eq 5 -and
            $echo.modules.Split(';') -notcontains $parentModules) 'Desktop worker inherited caller module paths or selected the project runtime'
        Assert-True ($env:PSModulePath -ceq $inheritedModules) 'Worker isolation changed the invoking shell module path'
        $env:PSModulePath = $savedModulePath
        [IO.File]::WriteAllText($fixture, 'function Invoke-NativeDesktopWorkerBody($Request) { throw "fixture failure" }')
        $caught = $null
        try { & $realWorker @{ command = 'fixture' } | Out-Null } catch { $caught = $_ }
        Assert-True ($null -ne $caught -and $caught.Exception.Message -match 'fixture failure') 'Worker swallowed failure'
        [IO.File]::WriteAllText($fixture, 'function Invoke-NativeDesktopWorkerBody($Request) { Start-Sleep -Seconds 20 }')
        $caught = $null
        try { & $realWorker @{ command = 'fixture' } 1 | Out-Null } catch { $caught = $_ }
        Assert-True ($null -ne $caught -and $caught.Exception.Message -match 'timed out') 'Owned hanging worker not bounded'
    } finally { $script:NativeDesktopSource = $originalSource; $env:PSModulePath = $savedModulePath }
    $readiness = Get-NativeDesktopReadiness
    Assert-True ($readiness.healthy -and -not $readiness.capture_performed -and -not (Test-Path -LiteralPath $script:DesktopTestPath)) 'Doctor wrote state or captured content'
    Assert-Failure (Invoke-NativeDesktopCli @('control','disabled')) 'human authorization'
    Assert-Failure (Invoke-NativeDesktopCli @('control','enabled','--human-approved','--seconds','60')) 'Duration applies'
    Assert-Failure (Invoke-NativeDesktopCli @('control','human-override','--human-approved','--seconds','59')) '60-86400'
    Assert-Failure (Invoke-NativeDesktopCli @('control','human-override','--human-approved','--seconds','60','--indefinite')) 'Choose seconds'
    $generation = Observe-Window
    $calls = $script:DesktopTestCalls.Count
    Assert-Failure (Window-Action 'wrong') 'Stale session generation'
    Assert-Failure (Window-Action $generation 'different') 'not recently observed'
    Assert-True ($script:DesktopTestCalls.Count -eq $calls) 'Invalid target reached native worker'
    $action = Window-Action $generation
    Assert-True $action.ok "Enabled action failed: $($action | ConvertTo-Json -Compress)"
    Assert-Failure (Window-Action $generation) 'not recently observed'
    $generation = Observe-Window
    $disabled = Invoke-NativeDesktopCli @('control','disabled','--human-approved')
    Assert-True ($disabled.ok -and $disabled.agent_control -eq 'disabled') 'Disable transition failed'
    Assert-Failure (Window-Action $generation) 'control is disabled'
    Assert-True (Invoke-NativeDesktopCli @('windows')).ok 'Disabled state blocked observation'
    $status = Invoke-NativeDesktopCli @('status')
    Assert-True ($status.agent_control -eq 'disabled') 'Disabled state did not persist'
    Assert-True (Get-NativeDesktopReadiness).healthy 'Doctor cannot inspect an intentionally disabled desktop'
    Assert-True ((Read-NativeDesktopState (Get-NativeDesktopContext)).mode -eq 'disabled') 'Doctor changed human control state'
    $override = Invoke-NativeDesktopCli @('control','human-override','--human-approved','--seconds','60')
    Assert-True ($override.ok -and $override.override_until -eq 10060) 'Override deadline not absolute'
    $script:DesktopTestNow = 10059L
    Assert-Failure (Window-Action $generation) 'human-override'
    $script:DesktopTestNow = 10060L
    $status = Invoke-NativeDesktopCli @('status')
    Assert-True ($status.agent_control -eq 'enabled' -and $status.control_revision -ne $override.control_revision) 'Approved timer did not expire'
    $generation = Observe-Window
    $inspection = Invoke-NativeDesktopCli @('inspect','--pid','42')
    Assert-True ($inspection.ok -and $inspection.result.elements[0].name.Length -eq 256 -and $inspection.result.elements[0].name_truncated) 'Inspection did not bound public names'
    $calls = $script:DesktopTestCalls.Count
    Assert-Failure (Invoke-NativeDesktopCli @('inspect','--pid','42','--root','element-ref')) 'generation'
    Assert-Failure (Invoke-NativeDesktopCli @('element','focus','--ref','unknown','--generation',$generation)) 'Stale element'
    Assert-True ($script:DesktopTestCalls.Count -eq $calls) 'Invalid element reached worker'
    $elementAction = @('element','set-text','--ref','element-ref','--generation',$generation,'--text',"Unicode $([char]0x03bb)`nline")
    Assert-True (Invoke-NativeDesktopCli $elementAction).ok 'Recent reference or Unicode text failed'
    Assert-Failure (Invoke-NativeDesktopCli $elementAction) 'Stale element'
    $generation = Observe-Window
    $script:DesktopTestNow += 61
    # Fresh inspect must not extend lifetime of an old HWND inventory.
    Assert-True (Invoke-NativeDesktopCli @('inspect','--pid','42')).ok 'Inspection failed'
    Assert-Failure (Window-Action $generation) 'not recently observed'
    $script:DesktopTestNow += 61
    Assert-Failure (Invoke-NativeDesktopCli $elementAction) 'Stale element'
    $generation = Observe-Window
    $script:DesktopTestFail = $true
    Assert-Failure (Window-Action $generation) 'outcome unknown'
    $script:DesktopTestFail = $false
    Assert-Failure (Window-Action $generation) 'not recently observed'
    $generation = Observe-Window
    $script:DesktopTestSession = 'fixture-session-2'
    Assert-Failure (Window-Action $generation) 'Stale session generation'
    $generation = Observe-Window
    $sequence = @('input','sequence','--window','123','--identity','42:birth:class:title','--generation',$generation,'--width','640','--height','480','--keys','HOME,SHIFT+END')
    Assert-True (Invoke-NativeDesktopCli $sequence).ok 'Sequence rejected by public CLI'
    Assert-Failure (Invoke-NativeDesktopCli $sequence) 'not recently observed'
    $generation = Observe-Window
    $before = $script:DesktopTestCalls.Count
    foreach ($arguments in @(
        @('window','resize','--window','123','--identity','42:birth:class:title','--generation',$generation,'--width','0','--height','480'),
        @('input','text','--window','123','--identity','42:birth:class:title','--generation',$generation,'--width','640','--height','480','--text',('x' * 1025)),
        @('input','sequence','--window','123','--identity','42:birth:class:title','--generation',$generation,'--width','640','--height','480','--keys',('A,' * 32 + 'A')),
        @('inspect','--pid','-2'), @('inspect','--pid','42','--pid','43'), @('windows','--surprise'),
        @('window','focus','--window','0','--identity','x','--generation',$generation),
        @('screenshot','--output',(Join-Path $temporary 'new.png'))
    )) { Assert-True (-not (Invoke-NativeDesktopCli $arguments).ok) "Invalid arguments accepted: $arguments" }
    Assert-True ($script:DesktopTestCalls.Count -eq $before) 'Invalid arguments reached worker'
    $existing = Join-Path $temporary 'existing.png'; [IO.File]::WriteAllText($existing, 'preserve')
    Assert-Failure (Invoke-NativeDesktopCli @('screenshot','--monitor','explicit-device','--output',$existing)) 'NEW .png'
    Assert-True ([IO.File]::ReadAllText($existing) -eq 'preserve') 'Screenshot overwrote existing file'
    # Account lock contention never enters a worker.
    $lockContext = Get-NativeDesktopContext
    $held = Enter-NativeDesktopLock $lockContext
    try {
        # Mutexes are recursive on one thread, so test contention from another runspace.
        $job = [PowerShell]::Create()
        try {
            $job.AddScript('param($name) $m = New-Object Threading.Mutex($false,$name); try { $m.WaitOne(0) } finally { $m.Dispose() }').AddArgument('Global\Basaltwater.Desktop.' + $lockContext.Sid) | Out-Null
            Assert-True (-not [bool]$job.Invoke()[0]) 'Controller lock allowed another thread'
        } finally { $job.Dispose() }
    } finally { Exit-NativeDesktopLock $held }
    $record = Read-NativeDesktopState (Get-NativeDesktopContext)
    $record.mode = 'garbage'; Write-NativeDesktopState $record
    Assert-Failure (Invoke-NativeDesktopCli @('windows')) 'Invalid desktop control state'
    [IO.File]::WriteAllText($script:DesktopTestPath, '{broken')
    Assert-True (-not (Invoke-NativeDesktopCli @('status')).ok) 'Corrupt state silently enabled control'
    Assert-True (-not (Get-NativeDesktopReadiness).healthy) 'Doctor hid unreadable control state'
    Write-Host 'Native desktop offline contract tests passed (no live desktop calls).'
} finally {
    $resolved = [IO.Path]::GetFullPath($temporary)
    $root = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test cleanup path' }
    Assert-NativeDesktopPrivatePath $resolved
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
