<# Native desktop adapter contract (PowerShell 5.1 syntax).
Invoke-NativeDesktopCli returns objects, never JSON/exit codes. Errors are
{ok:false,error,code}; callers must exit nonzero. --json is accepted as a no-op.
Window/input require --window DECIMAL_HWND --identity TOKEN --generation GEN.
Input additionally requires --width/--height from windows (window-relative x/y).
Optional input --observation binds to the latest single-use screenshot receipt;
its geometry replaces the inventory requirement. Capture age uses Stopwatch/QPC.
Inspection references expire after 60s, any mutation, or control transition.
All OS desktop calls are behind Invoke-NativeDesktopWorker for offline mocks.
Desktop APIs run in an owned Windows PowerShell 5.1 8s worker: it provides the
installed .NET Framework UIAutomation assemblies without another runtime/package.
The public CLI wrapper also uses 5.1; development shells/task guardians use 7.
No elevation or clipboard access. Held keyboard input uses a finite lease and
an independent interactive Scheduled Task release watchdog.
#>
$script:NativeDesktopSource = $PSScriptRoot
. (Join-Path $PSScriptRoot 'Desktop.Hold.ps1')
. (Join-Path $PSScriptRoot 'Desktop.Worker.ps1')

function Get-NativeDesktopContext {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    try {
        $logon = Get-NativeDesktopAuthenticationId $identity.Token
        $boot = Get-NativeDesktopBoot
        return @{ Sid = $identity.User.Value; Session = "$([Diagnostics.Process]::GetCurrentProcess().SessionId):${logon}:$boot" }
    } finally { $identity.Dispose() }
}

function Get-NativeDesktopAuthenticationId([IntPtr]$Token) {
    if (-not ('Basaltwater.Desktop.Session' -as [type])) {
        Add-Type -Path (Join-Path $script:NativeDesktopSource 'Desktop.Session.cs') -ErrorAction Stop
    }
    return [Basaltwater.Desktop.Session]::AuthenticationId($Token)
}

function Get-NativeDesktopBoot {
    return (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime.ToUniversalTime().Ticks.ToString()
}

function Get-NativeDesktopStatePath { return Join-Path $env:LOCALAPPDATA 'Basaltwater\desktop\state.json' }

function Assert-NativeDesktopPath([string]$Path) {
    if ($Path -notmatch '\A[A-Za-z]:[\\/]' -or $Path -match '[\x00-\x1f]' -or $Path.Substring(2).Contains(':')) { throw 'Expected absolute local drive path without streams' }
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        try{$entry=Get-Item -LiteralPath $cursor -Force -ErrorAction Stop}
        catch [Management.Automation.ItemNotFoundException] {$entry=$null}
        if ($entry) {
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse paths unsupported: $cursor" }
        }
        $parent = Split-Path -Parent $cursor
        if ($parent -eq $cursor) { break }; $cursor = $parent
    }
}

function New-NativeDesktopPrivateDirectory([string]$Path) {
    Assert-NativeDesktopPath $Path
    if (-not (Test-Path -LiteralPath $Path)) {
        $acl = New-Object Security.AccessControl.DirectorySecurity
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $acl.SetOwner($sid); $acl.SetAccessRuleProtection($true, $false)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
        # Create with the final ACL, using the appropriate framework API.
        if ($PSVersionTable.PSVersion.Major -le 5) { [IO.Directory]::CreateDirectory($Path, $acl) | Out-Null }
        else { [IO.FileSystemAclExtensions]::Create((New-Object IO.DirectoryInfo($Path)), $acl) | Out-Null }
    }
    Assert-NativeDesktopPrivatePath $Path
}

function Assert-NativeDesktopPrivatePath([string]$Path) {
    Assert-NativeDesktopPath $Path
    $acl = Get-Acl -LiteralPath $Path
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid) { throw 'Desktop state owner mismatch' }
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin @($sid, 'S-1-5-18', 'S-1-5-32-544')) { throw 'Desktop state ACL allows another account' }
    }
}

function New-NativeDesktopPrivateFile([string]$Path, [byte[]]$Bytes) {
    Assert-NativeDesktopPrivatePath (Split-Path -Parent $Path)
    Assert-NativeDesktopPath $Path
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl = [Security.AccessControl.FileSecurity]::new()
    $acl.SetOwner($sid)
    $acl.SetAccessRuleProtection($true, $false)
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'Allow'))
    $rights = [Security.AccessControl.FileSystemRights]::Write
    if ($PSVersionTable.PSVersion.Major -le 5) {
        $stream = [IO.FileStream]::new($Path, 'CreateNew', $rights, 'None', 4096, 'None', $acl)
    } else {
        $stream = [IO.FileSystemAclExtensions]::Create([IO.FileInfo]::new($Path), 'CreateNew', $rights, 'None', 4096, 'None', $acl)
    }
    try { $stream.Write($Bytes, 0, $Bytes.Length) } finally { $stream.Dispose() }
}

function Set-NativeDesktopMarker([string]$Path, [string]$Text) {
    try { New-NativeDesktopPrivateFile $Path ([Text.Encoding]::ASCII.GetBytes($Text)) }
    catch [IO.IOException] {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw }
        Assert-NativeDesktopPrivatePath $Path
    }
}

function Read-NativeDesktopState($Context) {
    $path = Get-NativeDesktopStatePath
    $state = @{ version = 1; owner = $Context.Sid; mode = 'enabled'; until = 0L; revision = [guid]::NewGuid().ToString('N');
        session = $Context.Session; generation = [guid]::NewGuid().ToString('N'); windows = @(); elements = @(); window_observed = 0L; element_observed = 0L }
    if (Test-Path -LiteralPath $path) {
        Assert-NativeDesktopPrivatePath (Split-Path -Parent $path)
        Assert-NativeDesktopPrivatePath $path
        if ((Get-Item -LiteralPath $path).Length -gt 2097152) { throw 'Desktop state exceeds size bound' }
        $record = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($key in @($state.Keys)) {
            if (-not $record.PSObject.Properties[$key]) { throw "Invalid desktop state: missing $key" }
            $state[$key] = $record.$key
        }
        if ($state.version -ne 1 -or $state.owner -ne $Context.Sid -or $state.mode -notin @('enabled','human-override','disabled') -or
            $state.revision -notmatch '\A[0-9a-f]{32}\z' -or $state.generation -notmatch '\A[0-9a-f]{32}\z' -or
            [long]$state.until -lt 0 -or ($state.mode -ne 'human-override' -and [long]$state.until -ne 0)) { throw 'Invalid desktop control state; repair explicitly' }
        if ($state.session -ne $Context.Session) { $state.session = $Context.Session; $state.generation = [guid]::NewGuid().ToString('N'); $state.windows = @(); $state.elements = @(); $state.window_observed = 0L; $state.element_observed = 0L }
    }
    if ($state.mode -eq 'human-override' -and $state.until -gt 0 -and (Get-NativeDesktopTime) -ge $state.until) {
        $state.mode = 'enabled'; $state.until = 0L; $state.revision = [guid]::NewGuid().ToString('N'); $state.windows = @(); $state.elements = @()
    }
    # Optional bounded receipt: older version-1 state files remain readable.
    $state.observation = $null
    if (Test-Path -LiteralPath $path) {
        if ($record.PSObject.Properties['observation']) { $state.observation = $record.observation }
    }
    if ($state.observation -and ($state.observation.session -cne $state.session -or $state.observation.generation -cne $state.generation -or $state.observation.revision -cne $state.revision)) { $state.observation = $null }
    return $state
}

function Get-NativeDesktopTime { return [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }

function Get-NativeDesktopMonotonic { return [Diagnostics.Stopwatch]::GetTimestamp() }

function Get-NativeCaptureFreshness($Receipt) {
    if (-not $Receipt) { return @{ age_seconds = $null; remaining_seconds = 0; max_age_seconds = 60; fresh = $false } }
    $age = ((Get-NativeDesktopMonotonic) - [long]$Receipt.admitted) / [double][Diagnostics.Stopwatch]::Frequency
    $maximum = [int]$Receipt.max_age_seconds
    $fresh = $age -ge 0 -and $age -le $maximum
    return @{ age_seconds = [Math]::Max(0, $age); remaining_seconds = $(if ($fresh) { $maximum - $age } else { 0 }); max_age_seconds = $maximum; fresh = $fresh }
}

function Assert-NativeCaptureObservation($Request, $State) {
    $receipt = $State.observation
    if (-not $receipt -or $Request.observation_id -cne $receipt.observation_id) { throw 'Observation is not the latest successful capture' }
    if ($receipt.session -cne $State.session -or $receipt.generation -cne $State.generation -or $receipt.revision -cne $State.revision) { throw 'Observation control/session changed' }
    if ($receipt.coordinate_space -cne 'window' -or $receipt.window -ne $Request.window -or $receipt.identity -cne $Request.identity) { throw 'Observation target mismatch; window capture required' }
    if ($receipt.width -ne $Request.width -or $receipt.height -ne $Request.height) { throw 'Observation geometry mismatch' }
    if (-not (Get-NativeCaptureFreshness $receipt).fresh) { throw 'Observation expired; capture again' }
}

function Write-NativeDesktopState($State, [switch]$KeepObservation) {
    # Existing mutation callers (including shell handoff) invalidate by default.
    # Only explicit observation/control bookkeeping may preserve a receipt.
    if (-not $KeepObservation) { $State.observation = $null }
    $path = Get-NativeDesktopStatePath; $directory = Split-Path -Parent $path
    New-NativeDesktopPrivateDirectory $directory
    if (Test-Path -LiteralPath $path) { Assert-NativeDesktopPrivatePath $path }
    $temporary = Join-Path $directory ([guid]::NewGuid().ToString('N') + '.tmp')
    try {
        New-NativeDesktopPrivateFile $temporary ([Text.Encoding]::UTF8.GetBytes(($State | ConvertTo-Json -Depth 40 -Compress)))
        if ((Get-Item -LiteralPath $temporary).Length -gt 2097152) { throw 'Desktop inventory exceeds state budget' }
        if (Test-Path -LiteralPath $path) { [IO.File]::Replace($temporary, $path, [System.Management.Automation.Language.NullString]::Value) } else { [IO.File]::Move($temporary, $path) }
    } finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary } }
}

function Enter-NativeDesktopLock($Context) {
    # Global name coordinates this account across interactive sessions as well.
    $mutex = New-Object Threading.Mutex($false, ('Global\Basaltwater.Desktop.' + $Context.Sid))
    try { $acquired = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $acquired = $true }
    if (-not $acquired) { $mutex.Dispose(); throw 'Desktop controller busy; only one request may run at a time' }
    return $mutex
}
function Exit-NativeDesktopLock($Lock) { $Lock.ReleaseMutex(); $Lock.Dispose() }

function Initialize-NativeDesktopTypes {
    if (-not ('Basaltwater.Desktop.Native' -as [type])) {
        $framework = [Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory()
        Add-Type -ErrorAction Stop -Path @((Join-Path $script:NativeDesktopSource 'Desktop.Native.cs'), (Join-Path $script:NativeDesktopSource 'Desktop.Hold.cs')) -ReferencedAssemblies @(
            'System.dll','System.Core.dll','System.Drawing.dll','System.Windows.Forms.dll',
            (Join-Path $framework 'WPF\UIAutomationClient.dll'), (Join-Path $framework 'WPF\UIAutomationTypes.dll'), (Join-Path $framework 'WPF\WindowsBase.dll'))
    }
}

function Invoke-NativeDesktopInput($Request) {
    $inputGuard = $script:NativeDesktopInputGuard
    if (($Request -is [Collections.IDictionary] -and $Request.Contains('observation_deadline')) -or $Request.PSObject.Properties['observation_deadline']) {
        $inputGuard = [Basaltwater.Desktop.Native]::ObservationGuard($inputGuard, [long]$Request.observation_deadline)
    }
    [Basaltwater.Desktop.Native]::InputAction([long]$Request.window, $Request.identity, $Request.operation, $Request.text, [int]$Request.x, [int]$Request.y, [int]$Request.width, [int]$Request.height,$inputGuard)
    return @{ submitted = $true; outcome_verified = $false; verification = 'Input queue acceptance only; inspect application before further input' }
}

function Invoke-NativeDesktopWorkerBody($Request) {
    if ($Request.command -eq 'diagnostics') {
        if (-not ('Basaltwater.Diagnostics.Native' -as [type])) {
            Add-Type -Path (Join-Path $PSScriptRoot 'Diagnostics.Native.cs') -ErrorAction Stop
        }
        if ($Request.PSObject.Properties['members']) {
            if (@($Request.members).Count -gt 32) { throw 'Member sample limit is 32' }
            return @{ samples = @($Request.members | ForEach-Object {
                $target = $_
                try { @{ Token = $target.Token; Resources = [Basaltwater.Diagnostics.Native]::Capture([int]$target.ProcessId,[long]$target.CreatedFileTimeUtc,$Request.job,$Request.sid,[int]$Request.session); Error = $null } }
                catch { @{ Token = $target.Token; Resources = $null; Error = $_.Exception.Message } }
            }) }
        }
        return [Basaltwater.Diagnostics.Native]::Capture([int]$Request.pid,[long]$Request.created,$Request.job,$Request.sid,[int]$Request.session)
    }
    if ($Request.command -eq 'environment') {
        . (Join-Path $PSScriptRoot 'Environment.Common.ps1')
        return Invoke-NativeEnvironmentWorkerBody
    }
    Initialize-NativeDesktopTypes
    [Basaltwater.Desktop.Native]::Ready()
    if ($Request.command -eq 'ready') { return @{ healthy = $true; interactive = $true; capture_performed = $false; backend = 'win32-uia'; worker = 'WindowsPowerShell-5.1'; timeout_seconds = 8;
        capabilities = @{ keyboard_hold = $true; maximum_hold_ms = 5000; maximum_owned_keys=8; maximum_timeline_events=32; explicit_repeat = $true; mouse_hold = $true; drag=$true; wheel=$true; observation_hold=$true; post_input_observation=$true; warm_worker = $true } } }
    [Basaltwater.Desktop.Native]::Dpi()
    switch ($Request.command) {
        'hold-plan' {
            $guard=$script:NativeDesktopInputGuard
            $deadline=0L
            if($Request.PSObject.Properties['observation_deadline'] -or ($Request -is [Collections.IDictionary] -and $Request.Contains('observation_deadline'))){
                $deadline=[long]$Request.observation_deadline
                $guard=[Basaltwater.Desktop.Native]::ObservationGuard($guard,$deadline)
            }
            if($guard){$reason=$guard.Invoke();if($reason){throw $reason}}
            if($Request.PSObject.Properties['timeline'] -and $Request.timeline){$plan=[Basaltwater.Desktop.Native]::PrepareTimeline([long]$Request.window,$Request.identity,$Request.timeline,[int]$Request.width,[int]$Request.height,[int]$Request.milliseconds)}
            elseif($Request.PSObject.Properties['button'] -and $Request.button){$plan=[Basaltwater.Desktop.Native]::PreparePointerHold([long]$Request.window,$Request.identity,$Request.button,[int]$Request.width,[int]$Request.height,[int]$Request.x,[int]$Request.y,[int]$Request.to_x,[int]$Request.to_y,[bool]$Request.drag)}
            else{$plan=[Basaltwater.Desktop.Native]::PrepareHold([long]$Request.window,$Request.identity,$Request.key,[int]$Request.width,[int]$Request.height)}
            if($guard){$reason=$guard.Invoke();if($reason){throw $reason}}
            $plan.observation_deadline=$deadline
            return $plan
        }
        'hold' {
            # Keep the receipt's nullable error separate from transport errors.
            return @{ held = Invoke-NativeHeldDriver $Request }
        }
        'windows' { return @{ windows = @([Basaltwater.Desktop.Native]::Windows()); monitors = @([Basaltwater.Desktop.Native]::Monitors()); coverage = 'Visible accessible windows in current account/session; inaccessible windows omitted' } }
        'inspect' { return [Basaltwater.Desktop.Native]::Inspect([int]$Request.pid, $Request.record, $Request.name, $Request.role) }
        'window' { return @{ window = [Basaltwater.Desktop.Native]::Change([long]$Request.window, $Request.identity, $Request.operation, [int]$Request.x, [int]$Request.y, [int]$Request.width, [int]$Request.height,$script:NativeDesktopInputGuard); outcome_verified = $true } }
        'element' {
            $verified = [Basaltwater.Desktop.Native]::Act($Request.record, $Request.operation, $Request.text,$script:NativeDesktopInputGuard)
            if ($Request.operation -ne 'invoke' -and -not $verified) { throw 'Element outcome not observed; inspect before retrying' }
            return @{ accepted = $true; outcome_verified = $verified; verification = 'Focus/Value are read back; InvokePattern requires task-specific reinspection' }
        }
        'input' { return Invoke-NativeDesktopInput $Request }
        'screenshot' {
            $capture = [Basaltwater.Desktop.Native]::Capture([long]$Request.window, $Request.identity, $Request.monitor, $Request.output)
            return @{ output = $Request.output; bytes = (Get-Item -LiteralPath $Request.output).Length; capture_method = 'PrintWindow for window; CopyFromScreen for explicit monitor'; outcome_verified = $true; content_verified = $false;
                geometry = $capture.geometry; image_geometry = $capture.image_geometry; image_origin = $capture.image_origin; desktop_origin = $capture.desktop_origin; coordinate_space = $capture.coordinate_space; captured_at = $capture.captured_at } }
        default { throw 'Unsupported native worker request' }
    }
}

function Invoke-NativeDesktopWorker($Request, [int]$TimeoutSeconds = 8) {
    if(-not $script:NativeDesktopWorkerServing -and $Request.command -notin @('environment','diagnostics')){
        $warm=Get-NativeWarmRecord
        if($warm -and -not $warm.cleanup_confirmed){return Invoke-NativeWarmRequest $warm $Request $TimeoutSeconds}
    }
    # No external application is killed: this handle belongs exclusively to our worker.
    $directory = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-desktop-' + [guid]::NewGuid().ToString('N'))
    New-NativeDesktopPrivateDirectory $directory
    $inputPath = Join-Path $directory 'request.json'; $outputPath = Join-Path $directory 'response.json'
    $process = $null; $result=$null
    try {
        $payload=$Request
        $enveloped=$Request.command -in @('ready','windows','inspect','window','element','input','screenshot','hold-plan','hold')
        if($enveloped){
            $context=Get-NativeDesktopContext;$state=Read-NativeDesktopState $context
            $self=[Diagnostics.Process]::GetCurrentProcess()
            try{$created=$self.StartTime.ToUniversalTime().Ticks}finally{$self.Dispose()}
            $payload=@{version=1;id=[guid]::NewGuid().ToString('N');pid=$PID;created=$created;sid=$context.Sid;session=$context.Session;directory=$directory;
                revision=$state.revision;generation=$state.generation;deadline=([Diagnostics.Stopwatch]::GetTimestamp()+$TimeoutSeconds*[Diagnostics.Stopwatch]::Frequency);request=$Request}
        }
        [IO.File]::WriteAllText($inputPath, ($payload | ConvertTo-Json -Depth 40 -Compress))
        $source = (Join-Path $script:NativeDesktopSource 'Desktop.Common.ps1').Replace("'", "''")
        $escapedInput = $inputPath.Replace("'", "''"); $escapedOutput = $outputPath.Replace("'", "''")
        $entry = @"
`$ErrorActionPreference = 'Stop'
try { . '$source'; `$request = Get-Content -LiteralPath '$escapedInput' -Raw -Encoding UTF8 | ConvertFrom-Json; if(`$request.PSObject.Properties['request']){`$result=Invoke-NativeColdEnvelope `$request}else{`$result = Invoke-NativeDesktopWorkerBody `$request} }
catch { `$result = @{ error = `$_.Exception.Message; ok=`$false; submitted=`$false } }
[IO.File]::WriteAllText('$escapedOutput', (`$result | ConvertTo-Json -Depth 40 -Compress))
"@
        # Encode script, never interpolate user arguments into executable code.
        $start = New-Object Diagnostics.ProcessStartInfo
        $start.FileName = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $start.Arguments = '-NoLogo -NoProfile -NonInteractive -Sta -ExecutionPolicy Bypass -EncodedCommand ' + [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($entry))
        $start.UseShellExecute = $false; $start.CreateNoWindow = $true; $start.WindowStyle = 'Hidden'
        # .NET process launches do not apply pwsh's Windows PowerShell path filtering.
        $start.EnvironmentVariables.Remove('PSModulePath')
        $process = [Diagnostics.Process]::Start($start)
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill(); if (-not $process.WaitForExit(2000)) { throw 'Owned worker cleanup unconfirmed' }
            throw (New-NativeWorkerFailure 'Desktop worker timed out; outcome unknown, observe before retrying' $true 'transport')
        }
        if (-not (Test-Path -LiteralPath $outputPath) -or (Get-Item -LiteralPath $outputPath).Length -gt 2097152) { throw (New-NativeWorkerFailure 'Desktop worker response missing or oversized' $true 'transport') }
        $result = Get-Content -LiteralPath $outputPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if($enveloped){
            if(-not $result.PSObject.Properties['version'] -or $result.version -ne 1 -or -not $result.PSObject.Properties['id'] -or $result.id -cne $payload.id -or
                -not $result.PSObject.Properties['ok'] -or $result.ok -isnot [bool] -or -not $result.PSObject.Properties['submitted'] -or $result.submitted -isnot [bool] -or
                -not $result.PSObject.Properties['result'] -or -not $result.PSObject.Properties['error']){throw (New-NativeWorkerFailure 'Cold worker response schema mismatch; do not replay' $true 'transport')}
            if(-not $result.ok){throw (New-NativeWorkerFailure $result.error ([bool]$result.submitted))}
            return $result.result
        }
        if ($result.PSObject.Properties['error']) { throw $result.error }
        return $result
    } finally {
        if ($process) { $process.Dispose() }
        # directory was made here with a random name; never follow a replaced link.
        try {
            Assert-NativeDesktopPrivatePath $directory
            foreach ($file in @($inputPath, $outputPath)) { if (Test-Path -LiteralPath $file) { Assert-NativeDesktopPath $file; Remove-Item -LiteralPath $file } }
            [IO.Directory]::Delete($directory)
        } catch {
            $cleanupError=$_.Exception.Message
            if($result -and $enveloped -and $result.PSObject.Properties['ok']){
                if(-not $result.ok){throw (New-NativeWorkerFailure ($result.error+'; cold mailbox cleanup unconfirmed; do not replay') ([bool]$result.submitted))}
                if($Request.command -eq 'hold-plan'){throw (New-NativeWorkerFailure 'Hold preparation completed but cold mailbox cleanup is unconfirmed; no held input started' $false)}
                $result.result|Add-Member NoteProperty worker_cleanup @{confirmed=$false;directory=$directory;error=$cleanupError;message='Action receipt retained; do not replay to repair mailbox cleanup'} -Force
            } else {throw}
        }
    }
}

function Get-NativeDesktopReadiness {
    $lock = $null
    try {
        $context = Get-NativeDesktopContext
        $lock = Enter-NativeDesktopLock $context
        $null = Read-NativeDesktopState $context
        return Invoke-NativeDesktopWorker @{ command = 'ready' }
    }
    catch { return @{ healthy = $false; capture_performed = $false; error = $_.Exception.Message } }
    finally { if ($lock) { Exit-NativeDesktopLock $lock } }
}

function ConvertTo-NativeDesktopRequest([string[]]$Arguments) {
    if (-not $Arguments.Count) { throw 'Expected desktop command' }
    $request = @{ command = $Arguments[0] }; $index = 1
    $allowed = @{
        status = @(); doctor = @(); windows = @(); environment = @();
        control = @('human-approved','seconds','indefinite'); inspect = @('pid','root','generation','name','role');
        screenshot = @('window','identity','generation','output','monitor','max-age-seconds','compact');
        window = @('window','identity','generation','x','y','width','height');
        element = @('ref','generation','text','action-name');
        input = @('window','identity','generation','width','height','x','y','text','key','keys','milliseconds','repeat-ms','lease','observation','timeline','button','to-x','to-y','delta','observe');
        worker = @('idle-seconds','session-seconds')
        'map-point' = @('capture','view-width','view-height','x','y')
    }
    if (-not $allowed.ContainsKey($request.command)) { throw "Unsupported Windows desktop command: $($request.command)" }
    if ($request.command -in @('control','window','element','input','worker')) {
        if ($index -ge $Arguments.Count -or $Arguments[$index].StartsWith('--')) { throw 'Expected operation' }
        $request.operation = $Arguments[$index++];
    }
    while ($index -lt $Arguments.Count) {
        $option = $Arguments[$index++]
        if ($option -eq '--json') { continue }
        if (-not $option.StartsWith('--') -or $option.Substring(2) -notin $allowed[$request.command]) { throw "Unsupported option: $option" }
        $key = $option.Substring(2)
        if ($request.ContainsKey($key)) { throw "Duplicate option: $option" }
        if ($key -in @('human-approved','indefinite','compact')) { $request[$key] = $true; continue }
        if ($index -ge $Arguments.Count) { throw "Missing value: $option" }
        $request[$key] = $Arguments[$index++]
    }
    foreach ($key in @('pid','window','x','y','to-x','to-y','delta','view-width','view-height','width','height','seconds','milliseconds','repeat-ms','idle-seconds','session-seconds','max-age-seconds')) {
        if ($request.ContainsKey($key)) {
            $number = 0L
            if ($request[$key] -notmatch '\A-?[0-9]+\z' -or -not [long]::TryParse($request[$key], [ref]$number)) { throw "Invalid integer: $key" }
            if ($key -in @('pid','width','height','seconds') -and ($number -le 0 -or $number -gt [int]::MaxValue)) { throw "Invalid positive integer: $key" }
            if ($key -eq 'window' -and $number -le 0) { throw 'Invalid window handle' }
            if ($key -eq 'max-age-seconds' -and ($number -lt 1 -or $number -gt 600)) { throw 'Max age must be 1-600 seconds' }
            if ($key -in @('x','y','to-x','to-y') -and ($number -lt -100000 -or $number -gt 100000)) { throw 'Coordinate out of bounds' }
            if ($key -in @('width','height') -and $number -gt 16384) { throw 'Geometry out of bounds' }
            $request[$key] = $number
        }
    }
    if ($request.ContainsKey('observation')) {
        if ($request.observation -cnotmatch '\A[0-9a-f]{32}\z') { throw 'Invalid observation ID' }
        $request.observation_id = $request.observation; $request.Remove('observation')
    }
    if ($request.command -eq 'screenshot' -and -not $request.ContainsKey('max-age-seconds')) { $request['max-age-seconds'] = 60 }
    return $request
}

function Assert-NativeDesktopRequired($Request, [string[]]$Keys) {
    foreach ($key in $Keys) { if (-not $Request.ContainsKey($key) -or [string]$Request[$key] -eq '') { throw "Required option: --$key" } }
}
function Assert-NativeDesktopNewCapturePath([string]$Path) {
    Assert-NativeDesktopPath $Path
    if([IO.Path]::GetExtension($Path) -ine '.png' -or (Test-Path -LiteralPath $Path) -or -not(Test-Path -LiteralPath (Split-Path -Parent $Path) -PathType Container)){throw 'Output must be a NEW .png in an existing directory'}
    # Test directory write access before input without reserving the output name.
    $probe=Join-Path (Split-Path -Parent $Path) ([guid]::NewGuid().ToString('N')+'.probe')
    try{$stream=[IO.File]::Open($probe,'CreateNew','Write','None');$stream.Dispose()}finally{if(Test-Path -LiteralPath $probe){Remove-Item -LiteralPath $probe}}
}
function Invoke-NativePostInputCapture($Request,$InputResult,$State) {
    try {
        Assert-NativeHeldIdle
        $current=Read-NativeDesktopState (Get-NativeDesktopContext)
        if($current.mode -ne 'enabled' -or $current.revision -cne $State.revision -or $current.generation -cne $State.generation){throw 'Desktop control changed before post-input capture'}
        $post=@{command='screenshot';window=$Request.window;identity=$Request.identity;output=$Request.observe}
        $admitted=Get-NativeDesktopMonotonic
        $capture=Invoke-NativeDesktopWorker $post
        $receipt=@{observation_id=[guid]::NewGuid().ToString('N');admitted=$admitted;max_age_seconds=60;session=$State.session;generation=$State.generation;revision=$State.revision;
            coordinate_space=$capture.coordinate_space;window=$Request.window;identity=$Request.identity;width=$capture.geometry[0];height=$capture.geometry[1]}
        $State.observation=$receipt
        if($capture -is [Collections.IDictionary]){$capture.observation_id=$receipt.observation_id;$capture.capture_freshness=Get-NativeCaptureFreshness $receipt}
        else{$capture|Add-Member NoteProperty observation_id $receipt.observation_id -Force;$capture|Add-Member NoteProperty capture_freshness (Get-NativeCaptureFreshness $receipt) -Force}
        Write-NativeDesktopState $State -KeepObservation
        return @{ok=$true;generation=$State.generation;control_revision=$State.revision;result=@{input=$InputResult;observation=$capture;dispatch_stage='input-completed';input_delivery='completed'}}
    } catch {
        return @{ok=$false;error=$_.Exception.Message;dispatch_stage='post-input-observation';input_delivery='completed';submitted=$true;result=$InputResult}
    }
}

function Invoke-NativeDesktopCli([string[]]$Arguments) {
    $lock = $null; $request=$null; $dispatchAttempted=$false
    try {
        if ($Arguments.Count -eq 2 -and $Arguments[0] -eq 'input' -and $Arguments[1] -in @('--help','-h','help')) {
            return @{ ok = $true; help = 'input key|chord|sequence|text|click|hold|release|lease|move|wheel. Input and hold require --window --identity --generation --width --height and accept --observation ID from the latest window screenshot. Optional --observe NEW.png captures after completed input, preserving completion on capture failure. release/lease require --lease ID. Use input OPERATION --help for keys, limits and lease behavior. Queue acceptance does not prove application delivery.' }
        }
        if($Arguments.Count -ge 2 -and $Arguments[0] -eq 'worker' -and
            (($Arguments.Count -eq 2 -and $Arguments[1] -eq '--help') -or
             ($Arguments.Count -eq 3 -and $Arguments[1] -in @('start','status','stop') -and $Arguments[2] -eq '--help'))){
            return @{ok=$true;help='worker start [--idle-seconds 120] [--session-seconds 1800]; worker status; worker stop. Idle 30-600 seconds, total idle..3600. Explicit limited interactive helper, fresh control/identity/deadline checks, no replay. Active or uncertain helpers block setup; stop reconciles cleanup. No autostart.'}
        }
        if ($Arguments.Count -eq 3 -and $Arguments[0] -eq 'input' -and $Arguments[1] -in @('hold','release','lease','key','chord','sequence','text','click','move','wheel') -and $Arguments[2] -eq '--help') {
            if ($Arguments[1] -in @('hold','release','lease')) { return @{ ok = $true; help = 'input hold --milliseconds 500 with exactly one of --key W+J, --timeline 0:W+J,200:J,400:-, or --button left|right|middle --x X --y Y [--to-x X --to-y Y]. Pointer must already be at start; use input move first. Fresh --window --identity --generation --width --height required; --observation ID and --observe NEW.png supported. Duration 50-5000 ms, maximum 8 owned keys, 32 strictly ordered timeline events starting at 0 and ending before duration. --repeat-ms 50..duration applies only to final key of --key set. One lease/watchdog; never layer leases. input release --lease ID cancels that lease; input lease --lease ID reads evidence. Queue acceptance is not application verification.' } }
            if($Arguments[1] -in @('move','wheel')){return @{ok=$true;help='input move --x X --y Y; input wheel --x X --y Y --delta N (nonzero multiples of 120, -1200..1200). Require observed window identity, generation and geometry. --observation ID and --observe NEW.png supported. Pointer target must be foreground and unoccluded; physical modifiers/buttons refuse input.'}}
            return @{ ok = $true; help = 'input key --key KEY | input chord --key CTRL+A (one/two distinct CTRL/SHIFT modifiers). input sequence --keys HOME,SHIFT+END: at most 32 chords and 1024 characters. Keys: A-Z 0-9 F1-F12 ENTER ESC TAB SPACE LEFT UP RIGHT DOWN BACKSPACE DELETE HOME END PAGEUP PAGEDOWN INSERT. input text --text TEXT: at most 1024 UTF-16 code units. input click --x X --y Y. All require --window --identity --generation --width --height from fresh windows, or matching window screenshot geometry with --observation ID. Observation is single-use and consumed before input attempt; status/windows never renew it. Queue acceptance only; inspect application afterward.' }
        }
        $request = ConvertTo-NativeDesktopRequest $Arguments
        if($request.command -eq 'map-point'){
            Assert-NativeDesktopRequired $request @('capture','view-width','view-height','x','y')
            Assert-NativeDesktopPath $request.capture
            if((Get-Item -LiteralPath $request.capture).Length -gt 2097152){throw 'Capture receipt exceeds bound'}
            $capture=Get-Content -LiteralPath $request.capture -Raw -Encoding UTF8 | ConvertFrom-Json
            if($capture.PSObject.Properties['result']){$capture=$capture.result}
            if($capture.PSObject.Properties['observation']){$capture=$capture.observation}
            if($capture.coordinate_space -ne 'window' -or $capture.image_geometry.Count -ne 2 -or $capture.geometry.Count -ne 2){throw 'Expected window capture receipt'}
            if($capture.image_origin.Count -ne 2 -or $capture.image_origin[0] -ne 0 -or $capture.image_origin[1] -ne 0 -or
                $capture.image_geometry[0] -ne $capture.geometry[0] -or $capture.image_geometry[1] -ne $capture.geometry[1]){throw 'Windows mapping requires an unchanged full-window capture'}
            $vw=[long]$request['view-width'];$vh=[long]$request['view-height']
            if($vw -lt 1 -or $vh -lt 1 -or $vw -gt 16384 -or $vh -gt 16384 -or $request.x -lt 0 -or $request.y -lt 0 -or $request.x -ge $vw -or $request.y -ge $vh){throw 'Point outside displayed image'}
            $x=[int][Math]::Floor($request.x*[double]$capture.image_geometry[0]/$vw)+[int]$capture.image_origin[0]
            $y=[int][Math]::Floor($request.y*[double]$capture.image_geometry[1]/$vh)+[int]$capture.image_origin[1]
            if($x -lt 0 -or $y -lt 0 -or $x -ge $capture.geometry[0] -or $y -ge $capture.geometry[1]){throw 'Mapped point outside input geometry'}
            $mapState=Read-NativeDesktopState (Get-NativeDesktopContext)
            if(-not $mapState.observation -or $capture.observation_id -cne $mapState.observation.observation_id -or -not (Get-NativeCaptureFreshness $mapState.observation).fresh -or
                $capture.geometry[0] -ne $mapState.observation.width -or $capture.geometry[1] -ne $mapState.observation.height){throw 'Capture receipt is stale or changed; capture again'}
            return @{ok=$true;x=$x;y=$y;coordinate_space='window';geometry=$capture.geometry;observation_id=$capture.observation_id;authority_renewed=$false;
                input_context_args=@('--window',[string]$mapState.observation.window,'--identity',$mapState.observation.identity,'--generation',$mapState.generation,'--width',[string]$capture.geometry[0],'--height',[string]$capture.geometry[1],'--observation',$capture.observation_id);input_args=@('--x',[string]$x,'--y',[string]$y)}
        }
        if ($request.command -eq 'input') {
            $leaseOptions=@('milliseconds','repeat-ms','lease','timeline','button','to-x','to-y')
            if ($request.operation -in @('release','lease')) {
                foreach ($key in $request.Keys) { if ($key -notin @('command','operation','lease')) { throw "Unsupported lease option: --$key" } }
            } elseif ($request.operation -eq 'hold') {
                foreach ($key in $request.Keys) { if ($key -notin @('command','operation','window','identity','generation','width','height','key','milliseconds','repeat-ms','observation_id','timeline','button','x','y','to-x','to-y','observe')) { throw "Unsupported hold option: --$key" } }
            } else {
                foreach ($key in $leaseOptions) { if ($request.ContainsKey($key)) { throw "Unsupported input option: --$key" } }
            }
        }
        if($request.command -eq 'worker'){
            if($request.operation -notin @('start','status','stop')){throw 'Expected worker start/status/stop'}
            if($request.operation -ne 'start' -and ($request.ContainsKey('idle-seconds') -or $request.ContainsKey('session-seconds'))){throw 'Worker lifetime options apply only to start'}
        }
        if ($request.command -eq 'environment') {
            . (Join-Path $PSScriptRoot 'Environment.Common.ps1')
            $report = Get-NativeEnvironmentReport
            return @{ ok = ($report.State -ne 'unavailable'); result = $report }
        }
        if ($request.command -eq 'doctor') { return Get-NativeDesktopReadiness }
        $context = Get-NativeDesktopContext; $lock = Enter-NativeDesktopLock $context
        $state = Read-NativeDesktopState $context
        if($request.command -eq 'worker'){
            if($request.operation -eq 'status'){return @{ok=$true;result=Get-NativeWarmStatus}}
            if($request.operation -eq 'stop'){Exit-NativeDesktopLock $lock;$lock=$null;return @{ok=$true;result=Stop-NativeWarmWorker}}
            if($request.operation -ne 'start'){throw 'Expected worker start/status/stop'}
            if($state.mode -ne 'enabled'){throw 'Desktop control must be enabled to start a worker'}
            Write-NativeDesktopState $state -KeepObservation
            $idle=120;$duration=1800
            if($request.ContainsKey('idle-seconds')){$idle=[int]$request['idle-seconds']}
            if($request.ContainsKey('session-seconds')){$duration=[int]$request['session-seconds']}
            return @{ok=$true;result=Start-NativeWarmWorker $context $idle $duration}
        }
        if ($request.command -eq 'input' -and $request.operation -in @('release','lease')) {
            Assert-NativeDesktopRequired $request @('lease')
            if ($request.operation -eq 'release') { Write-NativeDesktopState $state }
            return Get-NativeHeldLease $request.lease $context ($request.operation -eq 'release')
        }
        if ($request.command -eq 'control') {
            if (-not $request['human-approved']) { throw 'Control transitions require explicit human authorization (--human-approved)' }
            if ($request.operation -notin @('enabled','human-override','disabled')) { throw 'Invalid control state' }
            if ($request.operation -ne 'human-override' -and ($request.ContainsKey('seconds') -or $request['indefinite'])) { throw 'Duration applies only to human-override' }
            if ($request.ContainsKey('seconds') -and $request['indefinite']) { throw 'Choose seconds or indefinite' }
            $seconds = 900; if ($request.ContainsKey('seconds')) { $seconds = $request.seconds }
            if ($seconds -lt 60 -or $seconds -gt 86400) { throw 'Override seconds must be 60-86400' }
            $state.mode = $request.operation; $state.until = 0L
            if ($state.mode -eq 'human-override' -and -not $request['indefinite']) { $state.until = (Get-NativeDesktopTime) + $seconds }
            $state.revision = [guid]::NewGuid().ToString('N'); $state.windows = @(); $state.elements = @(); $state.observation = $null
        } elseif ($request.command -eq 'status') {
            $readiness = Get-NativeDesktopReadiness
            return @{ ok = $true; generation = $state.generation; agent_control = $state.mode; control_revision = $state.revision; override_until = $state.until; readiness = $readiness; input_lease = Get-NativeHeldActive $context;
                observation_id = $(if ($state.observation) { $state.observation.observation_id } else { $null });
                capture_freshness = Get-NativeCaptureFreshness $state.observation; input_capabilities = @('observation-input') }
        } else {
            $mutation = $request.command -in @('window','element','input')
            if ($mutation -and $state.mode -ne 'enabled') { throw "Agent desktop control is $($state.mode)" }
            if ($mutation) { Assert-NativeHeldIdle }
            if ($mutation -or $request.ContainsKey('root') -or ($request.command -eq 'screenshot' -and $request.ContainsKey('window'))) {
                Assert-NativeDesktopRequired $request @('generation')
                if ($request.generation -ne $state.generation) { throw 'Stale session generation' }
            }
            if ($request.command -in @('window','input') -or ($request.command -eq 'screenshot' -and $request.ContainsKey('window'))) {
                Assert-NativeDesktopRequired $request @('window','identity')
                if (-not ($request.command -eq 'input' -and $request.ContainsKey('observation_id'))) {
                    $matches = @($state.windows | Where-Object { $_.id -eq $request.window -and $_.identity -ceq $request.identity })
                    $age = (Get-NativeDesktopTime) - [long]$state.window_observed
                    if ($matches.Count -ne 1 -or $age -lt 0 -or $age -gt 60) { throw 'Window identity was not recently observed; run windows' }
                }
            }
            if ($request.command -eq 'window') {
                if ($request.operation -notin @('focus','move','resize','maximize','minimize','restore','close')) { throw 'Unsupported window operation' }
                if ($request.operation -eq 'move') { Assert-NativeDesktopRequired $request @('x','y') }
                if ($request.operation -eq 'resize') { Assert-NativeDesktopRequired $request @('width','height') }
            }
            if ($request.command -eq 'input') {
                Assert-NativeDesktopRequired $request @('width','height')
                if ($request.ContainsKey('observation_id')) {
                    Assert-NativeCaptureObservation $request $state
                    $request.observation_deadline = [long]$state.observation.admitted + [long]$state.observation.max_age_seconds * [Diagnostics.Stopwatch]::Frequency
                }
                if ($request.operation -notin @('hold','key','chord','sequence','text','click','move','wheel')) { throw 'Unsupported input operation' }
                if ($request.operation -eq 'hold') {
                    Assert-NativeDesktopRequired $request @('milliseconds')
                    $selectors=@('key','timeline','button' | Where-Object {$request.ContainsKey($_)})
                    if($selectors.Count -ne 1){throw 'Hold requires exactly one of --key, --timeline or --button'}
                    if($request.ContainsKey('repeat-ms') -and $selectors[0] -ne 'key'){throw 'Repeat applies only to keyboard key sets'}
                    if($request.ContainsKey('timeline') -and $request.timeline.Length -gt 2048){throw 'Timeline exceeds bound'}
                    if($request.ContainsKey('button')){
                        Assert-NativeDesktopRequired $request @('x','y')
                        if($request.ContainsKey('to-x') -ne $request.ContainsKey('to-y')){throw 'Drag requires both endpoints'}
                        $request.drag=$request.ContainsKey('to-x');$request.to_x=$request.x;$request.to_y=$request.y
                        if($request.drag){$request.to_x=$request['to-x'];$request.to_y=$request['to-y']}
                    } elseif($request.ContainsKey('x') -or $request.ContainsKey('y') -or $request.ContainsKey('to-x') -or $request.ContainsKey('to-y')){throw 'Pointer coordinates require --button'}
                    foreach($name in @('key','timeline','button','drag','to_x','to_y')){if(-not $request.ContainsKey($name)){$request[$name]=$null}}
                    if ($request.milliseconds -lt 50 -or $request.milliseconds -gt 5000) { throw 'Hold duration must be 50-5000 milliseconds' }
                    if ($request.ContainsKey('repeat-ms') -and ($request['repeat-ms'] -lt 50 -or $request['repeat-ms'] -gt $request.milliseconds)) { throw 'Repeat must be 50..duration milliseconds' }
                    if ($request.key -and $request.key.Length -gt 64) { throw 'Hold key exceeds bound' }
                }
                if ($request.operation -in @('click','move','wheel')) { Assert-NativeDesktopRequired $request @('x','y') }
                if($request.operation -eq 'wheel'){
                    Assert-NativeDesktopRequired $request @('delta')
                    if($request.delta -eq 0 -or $request.delta -lt -1200 -or $request.delta -gt 1200 -or $request.delta%120 -ne 0){throw 'Wheel delta must be nonzero multiples of 120 within -1200..1200'}
                    $request.text=[string]$request.delta
                } elseif($request.ContainsKey('delta')){throw '--delta requires wheel'}
                if($request.ContainsKey('observe')){Assert-NativeDesktopNewCapturePath $request.observe}
                if ($request.operation -eq 'text') { Assert-NativeDesktopRequired $request @('text'); if ($request.text.Length -gt 1024) { throw 'Text limit is 1024' } }
                if ($request.operation -in @('key','chord')) { Assert-NativeDesktopRequired $request @('key'); $request.text = $request.key }
                if ($request.operation -eq 'sequence') {
                    Assert-NativeDesktopRequired $request @('keys'); $request.text = $request['keys']
                    if ($request.text.Length -gt 1024 -or $request.text.Split(',').Count -gt 32) { throw 'Sequence limit is 32 chords and 1024 characters' }
                }
            }
            $request.record = $null
            if ($request.command -eq 'element' -or $request.ContainsKey('root')) {
                $reference = $request['root']
                if ($request.command -eq 'element') { Assert-NativeDesktopRequired $request @('ref'); $reference = $request.ref }
                $matches = @($state.elements | Where-Object { $_.reference -ceq $reference })
                if ($matches.Count -ne 1 -or (Get-NativeDesktopTime) - [long]$state.element_observed -gt 60) { throw 'Stale element reference; inspect again' }
                $request.record = $matches[0]
            }
            if ($request.command -eq 'element') {
                if ($request.operation -notin @('invoke','set-text','focus')) { throw 'Unsupported element operation' }
                if ($request.ContainsKey('action-name') -and $request['action-name'] -notin @('invoke','click')) { throw 'Only InvokePattern action supported' }
                if ($request.operation -eq 'set-text') { if (-not $request.ContainsKey('text') -or $request.text.Length -gt 4096) { throw 'Expected --text of at most 4096 characters' } }
            }
            if ($request.command -eq 'inspect') { Assert-NativeDesktopRequired $request @('pid') }
            if ($request.command -eq 'screenshot') {
                Assert-NativeDesktopRequired $request @('output')
                if ($request.ContainsKey('window') -eq $request.ContainsKey('monitor')) { throw 'Choose exactly one explicit --window or --monitor' }
                Assert-NativeDesktopPath $request.output
                $request.output = [IO.Path]::GetFullPath($request.output)
                if ([IO.Path]::GetExtension($request.output) -ine '.png' -or (Test-Path -LiteralPath $request.output) -or -not (Test-Path -LiteralPath (Split-Path -Parent $request.output) -PathType Container)) { throw 'Output must be a NEW .png in an existing directory' }
            }
            # Invalidate references durably BEFORE a possibly timed-out mutation.
            if ($mutation) { $state.elements = @(); $state.windows = @(); $state.observation = $null; Write-NativeDesktopState $state }
            if ($request.command -eq 'input' -and $request.operation -eq 'hold') {
                $dispatchAttempted=$true
                $lease = Start-NativeHeldLease $request $context $state
                # Control handoff and lease cancellation must remain available
                # while the driver waits; the active lease blocks other mutations.
                Exit-NativeDesktopLock $lock; $lock = $null
                $held=Wait-NativeHeldLease $lease
                if($request.ContainsKey('observe') -and $held.ok){
                    try {
                        $lock=Enter-NativeDesktopLock $context
                        $post=Invoke-NativePostInputCapture $request $held $state
                        $post.lease=$held.lease;return $post
                    } catch {return @{ok=$false;lease=$held.lease;error=$_.Exception.Message;dispatch_stage='post-input-observation';input_delivery='completed';submitted=$true;result=$held}}
                }
                return $held
            }
            if ($request.command -eq 'screenshot') {
                $admitted = Get-NativeDesktopMonotonic
                # A running lease can send repeats/releases outside this lock.
                # Its screenshots remain useful evidence but cannot authorize
                # later input, even if cleanup settles during this capture.
                $observationUnavailable = $null
                try { Assert-NativeHeldIdle }
                catch { $observationUnavailable = 'keyboard-lease-active-or-uncertain' }
            }
            $dispatchAttempted=$true
            $result = Invoke-NativeDesktopWorker $request
            if ($request.command -eq 'screenshot') {
                $receipt = @{ observation_id = [guid]::NewGuid().ToString('N'); admitted = $admitted; max_age_seconds = $request['max-age-seconds'];
                    session = $state.session; generation = $state.generation; revision = $state.revision; coordinate_space = $result.coordinate_space;
                    window = $request['window']; identity = $request['identity']; width = $result.geometry[0]; height = $result.geometry[1] }
                if ($observationUnavailable) { $receipt.observation_id = $null; $state.observation = $null }
                else { $state.observation = $receipt }
                $freshness = Get-NativeCaptureFreshness $receipt
                if ($result -is [Collections.IDictionary]) {
                    $result.observation_id = $receipt.observation_id; $result.capture_freshness = $freshness
                    $result.observation_unavailable_reason = $observationUnavailable
                } else {
                    $result | Add-Member NoteProperty observation_id $receipt.observation_id
                    $result | Add-Member NoteProperty capture_freshness $freshness
                    $result | Add-Member NoteProperty observation_unavailable_reason $observationUnavailable
                }
            }
            if ($request.command -eq 'input' -and $request.ContainsKey('observation_id')) {
                if ($result -is [Collections.IDictionary]) { $result.observation_id = $request.observation_id }
                else { $result | Add-Member NoteProperty observation_id $request.observation_id }
            }
            if($request.command -eq 'input' -and $request.ContainsKey('observe')){
                return Invoke-NativePostInputCapture $request $result $state
            }
            if ($request.command -eq 'windows') { $state.windows = @($result.windows); $state.elements = @(); $state.window_observed = Get-NativeDesktopTime }
            if ($request.command -eq 'inspect') {
                $workerCleanup=$result.PSObject.Properties['worker_cleanup']
                $state.elements = @($result.elements); $state.element_observed = Get-NativeDesktopTime
                # Keep full identity only in private inventory; bound the public name.
                $public = @($result.elements | ForEach-Object {
                    $row = @{ ref = $_.reference; name = $_.name; role = $_.role; pid = $_.pid; window = $_.window; enabled = $_.enabled; focused = $_.focused; actions = $_.actions; name_truncated = ($_.name.Length -gt 256) }
                    if ($row.name_truncated) { $row.name = $row.name.Substring(0,256) }; $row
                })
                $result = @{ elements = $public; visited = $result.visited; truncated = $result.truncated }
                if($workerCleanup){$result.worker_cleanup=$workerCleanup.Value}
            }
            Write-NativeDesktopState $state -KeepObservation
            return @{ ok = $true; generation = $state.generation; control_revision = $state.revision; result = $result }
        }
        Write-NativeDesktopState $state
        return @{ ok = $true; agent_control = $state.mode; control_revision = $state.revision; override_until = $state.until }
    } catch {
        $reply=@{ ok = $false; code = 'native_desktop_error'; error = $_.Exception.Message }
        $reply.submitted=$dispatchAttempted
        $reply.dispatch_stage=$(if($dispatchAttempted){'transport'}else{'before-input'})
        $reply.input_delivery=$(if($dispatchAttempted){'unknown'}else{'not-sent'})
        if($request -and $request.ContainsKey('observation_id')){$reply.observation_id=$request.observation_id}
        $failure=$_.Exception
        while($failure){
            if($failure.Data.Contains('desktop_failure')){foreach($key in $failure.Data['desktop_failure'].Keys){$reply[$key]=$failure.Data['desktop_failure'][$key]};break}
            $failure=$failure.InnerException
        }
        return $reply
    }
    finally { if ($lock) { Exit-NativeDesktopLock $lock } }
}
