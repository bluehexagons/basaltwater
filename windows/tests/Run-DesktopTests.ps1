<# Offline contract tests. No desktop APIs, personal content, or live GUI fixtures.
Native compilation and DTO conversion are checked without invoking native methods.
All desktop operations cross the mocked worker; persistent files use a temp root.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '..\Desktop.Common.ps1')
$realWorker = (Get-Command Invoke-NativeDesktopWorker).ScriptBlock
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
    # Parse/compile only: not a single user32, capture, UIA or input call.
    Initialize-NativeDesktopTypes
    $dto = [Basaltwater.Desktop.Element]([pscustomobject]@{ reference = 'r'; window = 123L; pid = 42; path = @(0,1); identity = 'i'; runtime = 'r'; name = 'n'; role = 'ControlType.Edit' })
    Assert-True ($dto.path.Length -eq 2 -and $dto.window -eq 123) 'Worker cannot deserialize UIA DTO'
    # Exercise actual worker transport/timeout using code with NO native APIs.
    $originalSource = $script:NativeDesktopSource
    $fixtureSource = Join-Path $temporary "worker's unicode $([char]0x03bb)"
    New-NativeDesktopPrivateDirectory $fixtureSource
    $fixture = Join-Path $fixtureSource 'Desktop.Common.ps1'
    [IO.File]::WriteAllText($fixture, 'function Invoke-NativeDesktopWorkerBody($Request) { return @{ echo = $Request.text } }')
    try {
        $script:NativeDesktopSource = $fixtureSource
        $text = "Quotes ' `" unicode $([char]0x03bb) `$literal `nnewline"
        $echo = & $realWorker @{ command = 'fixture'; text = $text }
        Assert-True ($echo.echo -ceq $text) 'Worker transport corrupted Unicode or executed text'
        [IO.File]::WriteAllText($fixture, 'function Invoke-NativeDesktopWorkerBody($Request) { throw "fixture failure" }')
        $caught = $null
        try { & $realWorker @{ command = 'fixture' } | Out-Null } catch { $caught = $_ }
        Assert-True ($null -ne $caught -and $caught.Exception.Message -match 'fixture failure') 'Worker swallowed failure'
        [IO.File]::WriteAllText($fixture, 'function Invoke-NativeDesktopWorkerBody($Request) { Start-Sleep -Seconds 20 }')
        $caught = $null
        try { & $realWorker @{ command = 'fixture' } 1 | Out-Null } catch { $caught = $_ }
        Assert-True ($null -ne $caught -and $caught.Exception.Message -match 'timed out') 'Owned hanging worker not bounded'
    } finally { $script:NativeDesktopSource = $originalSource }
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
    $before = $script:DesktopTestCalls.Count
    foreach ($arguments in @(
        @('window','resize','--window','123','--identity','42:birth:class:title','--generation',$generation,'--width','0','--height','480'),
        @('input','text','--window','123','--identity','42:birth:class:title','--generation',$generation,'--width','640','--height','480','--text',('x' * 1025)),
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
    Write-Host 'Native desktop offline contract tests passed (no live desktop calls).'
} finally {
    $resolved = [IO.Path]::GetFullPath($temporary)
    $root = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test cleanup path' }
    Assert-NativeDesktopPrivatePath $resolved
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
