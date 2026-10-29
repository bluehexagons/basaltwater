<# Native desktop adapter contract (PowerShell 5.1 syntax).
Invoke-NativeDesktopCli returns objects, never JSON/exit codes. Errors are
{ok:false,error,code}; callers must exit nonzero. --json is accepted as a no-op.
Window/input require --window DECIMAL_HWND --identity TOKEN --generation GEN.
Input additionally requires --width/--height from windows (window-relative x/y).
Inspection references expire after 60s, any mutation, or control transition.
All OS desktop calls are behind Invoke-NativeDesktopWorker for offline mocks.
Windows PowerShell is used only in an owned 8s worker: it provides the installed
.NET Framework UIAutomation assemblies without installing a runtime/package.
No start/stop, launch, lease, elevation, clipboard, key chords, or held input.
#>
$script:NativeDesktopSource = $PSScriptRoot

function Get-NativeDesktopContext {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $logon = @($identity.Groups | Where-Object { $_.Value -like 'S-1-5-5-*' })
    if ($logon.Count -ne 1) { throw 'Cannot establish interactive logon identity' }
    return @{ Sid = $identity.User.Value; Session = "$([Diagnostics.Process]::GetCurrentProcess().SessionId):$($logon[0].Value)" }
}

function Get-NativeDesktopStatePath { return Join-Path $env:LOCALAPPDATA 'Basaltwater\desktop\state.json' }

function Assert-NativeDesktopPath([string]$Path) {
    if ($Path -notmatch '\A[A-Za-z]:[\\/]' -or $Path -match '[\x00-\x1f]' -or $Path.Substring(2).Contains(':')) { throw 'Expected absolute local drive path without streams' }
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse paths unsupported: $cursor" }
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
        else { [IO.FileSystemAclExtensions]::Create((New-Object IO.DirectoryInfo($Path)), $acl) }
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
    return $state
}

function Get-NativeDesktopTime { return [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }

function Write-NativeDesktopState($State) {
    $path = Get-NativeDesktopStatePath; $directory = Split-Path -Parent $path
    New-NativeDesktopPrivateDirectory $directory
    if (Test-Path -LiteralPath $path) { Assert-NativeDesktopPrivatePath $path }
    $temporary = Join-Path $directory ([guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [IO.File]::WriteAllText($temporary, ($State | ConvertTo-Json -Depth 40 -Compress), (New-Object Text.UTF8Encoding($false)))
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
        Add-Type -ErrorAction Stop -Path (Join-Path $script:NativeDesktopSource 'Desktop.Native.cs') -ReferencedAssemblies @(
            'System.dll','System.Core.dll','System.Drawing.dll','System.Windows.Forms.dll',
            (Join-Path $framework 'WPF\UIAutomationClient.dll'), (Join-Path $framework 'WPF\UIAutomationTypes.dll'), (Join-Path $framework 'WPF\WindowsBase.dll'))
    }
}

function Invoke-NativeDesktopWorkerBody($Request) {
    Initialize-NativeDesktopTypes
    [Basaltwater.Desktop.Native]::Ready()
    if ($Request.command -eq 'ready') { return @{ healthy = $true; interactive = $true; capture_performed = $false; backend = 'win32-uia'; worker = 'WindowsPowerShell-5.1'; timeout_seconds = 8 } }
    [Basaltwater.Desktop.Native]::Dpi()
    switch ($Request.command) {
        'windows' { return @{ windows = @([Basaltwater.Desktop.Native]::Windows()); monitors = @([Basaltwater.Desktop.Native]::Monitors()); coverage = 'Visible accessible windows in current account/session; inaccessible windows omitted' } }
        'inspect' { return [Basaltwater.Desktop.Native]::Inspect([int]$Request.pid, $Request.record, $Request.name, $Request.role) }
        'window' { return @{ window = [Basaltwater.Desktop.Native]::Change([long]$Request.window, $Request.identity, $Request.operation, [int]$Request.x, [int]$Request.y, [int]$Request.width, [int]$Request.height); outcome_verified = $true } }
        'element' {
            $verified = [Basaltwater.Desktop.Native]::Act($Request.record, $Request.operation, $Request.text)
            if ($Request.operation -ne 'invoke' -and -not $verified) { throw 'Element outcome not observed; inspect before retrying' }
            return @{ accepted = $true; outcome_verified = $verified; verification = 'Focus/Value are read back; InvokePattern requires task-specific reinspection' }
        }
        'input' { [Basaltwater.Desktop.Native]::InputAction([long]$Request.window, $Request.identity, $Request.operation, $Request.text, [int]$Request.x, [int]$Request.y, [int]$Request.width, [int]$Request.height)
            return @{ submitted = $true; outcome_verified = $false; verification = 'Input queue acceptance only; inspect application before further input' } }
        'screenshot' { [Basaltwater.Desktop.Native]::Capture([long]$Request.window, $Request.identity, $Request.monitor, $Request.output)
            return @{ output = $Request.output; bytes = (Get-Item -LiteralPath $Request.output).Length; capture_method = 'PrintWindow for window; CopyFromScreen for explicit monitor'; outcome_verified = $true; content_verified = $false } }
        default { throw 'Unsupported native worker request' }
    }
}

function Invoke-NativeDesktopWorker($Request, [int]$TimeoutSeconds = 8) {
    # No external application is killed: this handle belongs exclusively to our worker.
    $directory = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-desktop-' + [guid]::NewGuid().ToString('N'))
    New-NativeDesktopPrivateDirectory $directory
    $inputPath = Join-Path $directory 'request.json'; $outputPath = Join-Path $directory 'response.json'
    $process = $null
    try {
        [IO.File]::WriteAllText($inputPath, ($Request | ConvertTo-Json -Depth 40 -Compress))
        $source = (Join-Path $script:NativeDesktopSource 'Desktop.Common.ps1').Replace("'", "''")
        $escapedInput = $inputPath.Replace("'", "''"); $escapedOutput = $outputPath.Replace("'", "''")
        $entry = @"
`$ErrorActionPreference = 'Stop'
try { . '$source'; `$request = Get-Content -LiteralPath '$escapedInput' -Raw -Encoding UTF8 | ConvertFrom-Json; `$result = Invoke-NativeDesktopWorkerBody `$request }
catch { `$result = @{ error = `$_.Exception.Message } }
[IO.File]::WriteAllText('$escapedOutput', (`$result | ConvertTo-Json -Depth 40 -Compress))
"@
        # Encode script, never interpolate user arguments into executable code.
        $start = New-Object Diagnostics.ProcessStartInfo
        $start.FileName = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $start.Arguments = '-NoLogo -NoProfile -NonInteractive -Sta -EncodedCommand ' + [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($entry))
        $start.UseShellExecute = $false; $start.CreateNoWindow = $true; $start.WindowStyle = 'Hidden'
        $process = [Diagnostics.Process]::Start($start)
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill(); if (-not $process.WaitForExit(2000)) { throw 'Owned worker cleanup unconfirmed' }
            throw 'Desktop worker timed out; outcome unknown, observe before retrying'
        }
        if (-not (Test-Path -LiteralPath $outputPath) -or (Get-Item -LiteralPath $outputPath).Length -gt 2097152) { throw 'Desktop worker response missing or oversized' }
        $result = Get-Content -LiteralPath $outputPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($result.PSObject.Properties['error']) { throw $result.error }
        return $result
    } finally {
        if ($process) { $process.Dispose() }
        # directory was made here with a random name; never follow a replaced link.
        Assert-NativeDesktopPrivatePath $directory
        foreach ($file in @($inputPath, $outputPath)) { if (Test-Path -LiteralPath $file) { Assert-NativeDesktopPath $file; Remove-Item -LiteralPath $file } }
        [IO.Directory]::Delete($directory)
    }
}

function Get-NativeDesktopReadiness {
    try { return Invoke-NativeDesktopWorker @{ command = 'ready' } }
    catch { return @{ healthy = $false; capture_performed = $false; error = $_.Exception.Message } }
}

function ConvertTo-NativeDesktopRequest([string[]]$Arguments) {
    if (-not $Arguments.Count) { throw 'Expected desktop command' }
    $request = @{ command = $Arguments[0] }; $index = 1
    $allowed = @{
        status = @(); doctor = @(); windows = @();
        control = @('human-approved','seconds','indefinite'); inspect = @('pid','root','generation','name','role');
        screenshot = @('window','identity','generation','output','monitor');
        window = @('window','identity','generation','x','y','width','height');
        element = @('ref','generation','text','action-name');
        input = @('window','identity','generation','width','height','x','y','text','key')
    }
    if (-not $allowed.ContainsKey($request.command)) { throw "Unsupported Windows desktop command: $($request.command)" }
    if ($request.command -in @('control','window','element','input')) {
        if ($index -ge $Arguments.Count -or $Arguments[$index].StartsWith('--')) { throw 'Expected operation' }
        $request.operation = $Arguments[$index++];
    }
    while ($index -lt $Arguments.Count) {
        $option = $Arguments[$index++]
        if ($option -eq '--json') { continue }
        if (-not $option.StartsWith('--') -or $option.Substring(2) -notin $allowed[$request.command]) { throw "Unsupported option: $option" }
        $key = $option.Substring(2)
        if ($request.ContainsKey($key)) { throw "Duplicate option: $option" }
        if ($key -in @('human-approved','indefinite')) { $request[$key] = $true; continue }
        if ($index -ge $Arguments.Count) { throw "Missing value: $option" }
        $request[$key] = $Arguments[$index++]
    }
    foreach ($key in @('pid','window','x','y','width','height','seconds')) {
        if ($request.ContainsKey($key)) {
            $number = 0L
            if ($request[$key] -notmatch '\A-?[0-9]+\z' -or -not [long]::TryParse($request[$key], [ref]$number)) { throw "Invalid integer: $key" }
            if ($key -in @('pid','width','height','seconds') -and ($number -le 0 -or $number -gt [int]::MaxValue)) { throw "Invalid positive integer: $key" }
            if ($key -eq 'window' -and $number -le 0) { throw 'Invalid window handle' }
            if ($key -in @('x','y') -and ($number -lt -100000 -or $number -gt 100000)) { throw 'Coordinate out of bounds' }
            if ($key -in @('width','height') -and $number -gt 16384) { throw 'Geometry out of bounds' }
            $request[$key] = $number
        }
    }
    return $request
}

function Assert-NativeDesktopRequired($Request, [string[]]$Keys) {
    foreach ($key in $Keys) { if (-not $Request.ContainsKey($key) -or [string]$Request[$key] -eq '') { throw "Required option: --$key" } }
}

function Invoke-NativeDesktopCli([string[]]$Arguments) {
    $lock = $null
    try {
        $request = ConvertTo-NativeDesktopRequest $Arguments
        if ($request.command -eq 'doctor') { return Get-NativeDesktopReadiness }
        $context = Get-NativeDesktopContext; $lock = Enter-NativeDesktopLock $context
        $state = Read-NativeDesktopState $context
        if ($request.command -eq 'control') {
            if (-not $request['human-approved']) { throw 'Control transitions require explicit human authorization (--human-approved)' }
            if ($request.operation -notin @('enabled','human-override','disabled')) { throw 'Invalid control state' }
            if ($request.operation -ne 'human-override' -and ($request.ContainsKey('seconds') -or $request['indefinite'])) { throw 'Duration applies only to human-override' }
            if ($request.ContainsKey('seconds') -and $request['indefinite']) { throw 'Choose seconds or indefinite' }
            $seconds = 900; if ($request.ContainsKey('seconds')) { $seconds = $request.seconds }
            if ($seconds -lt 60 -or $seconds -gt 86400) { throw 'Override seconds must be 60-86400' }
            $state.mode = $request.operation; $state.until = 0L
            if ($state.mode -eq 'human-override' -and -not $request['indefinite']) { $state.until = (Get-NativeDesktopTime) + $seconds }
            $state.revision = [guid]::NewGuid().ToString('N'); $state.windows = @(); $state.elements = @()
        } elseif ($request.command -eq 'status') {
            $readiness = Get-NativeDesktopReadiness
            return @{ ok = $true; generation = $state.generation; agent_control = $state.mode; control_revision = $state.revision; override_until = $state.until; readiness = $readiness }
        } else {
            $mutation = $request.command -in @('window','element','input')
            if ($mutation -and $state.mode -ne 'enabled') { throw "Agent desktop control is $($state.mode)" }
            if ($mutation -or $request.ContainsKey('root') -or ($request.command -eq 'screenshot' -and $request.ContainsKey('window'))) {
                Assert-NativeDesktopRequired $request @('generation')
                if ($request.generation -ne $state.generation) { throw 'Stale session generation' }
            }
            if ($request.command -in @('window','input') -or ($request.command -eq 'screenshot' -and $request.ContainsKey('window'))) {
                Assert-NativeDesktopRequired $request @('window','identity')
                $matches = @($state.windows | Where-Object { $_.id -eq $request.window -and $_.identity -ceq $request.identity })
                if ($matches.Count -ne 1 -or (Get-NativeDesktopTime) - [long]$state.window_observed -gt 60) { throw 'Window identity was not recently observed; run windows' }
            }
            if ($request.command -eq 'window') {
                if ($request.operation -notin @('focus','move','resize','maximize','minimize','restore','close')) { throw 'Unsupported window operation' }
                if ($request.operation -eq 'move') { Assert-NativeDesktopRequired $request @('x','y') }
                if ($request.operation -eq 'resize') { Assert-NativeDesktopRequired $request @('width','height') }
            }
            if ($request.command -eq 'input') {
                Assert-NativeDesktopRequired $request @('width','height')
                if ($request.operation -notin @('key','text','click')) { throw 'Unsupported input operation' }
                if ($request.operation -eq 'click') { Assert-NativeDesktopRequired $request @('x','y') }
                if ($request.operation -eq 'text') { Assert-NativeDesktopRequired $request @('text'); if ($request.text.Length -gt 1024) { throw 'Text limit is 1024' } }
                if ($request.operation -eq 'key') { Assert-NativeDesktopRequired $request @('key'); $request.text = $request.key }
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
            if ($mutation) { $state.elements = @(); $state.windows = @(); Write-NativeDesktopState $state }
            $result = Invoke-NativeDesktopWorker $request
            if ($request.command -eq 'windows') { $state.windows = @($result.windows); $state.elements = @(); $state.window_observed = Get-NativeDesktopTime }
            if ($request.command -eq 'inspect') {
                $state.elements = @($result.elements); $state.element_observed = Get-NativeDesktopTime
                # Keep full identity only in private inventory; bound the public name.
                $public = @($result.elements | ForEach-Object {
                    $row = @{ ref = $_.reference; name = $_.name; role = $_.role; pid = $_.pid; window = $_.window; enabled = $_.enabled; focused = $_.focused; actions = $_.actions; name_truncated = ($_.name.Length -gt 256) }
                    if ($row.name_truncated) { $row.name = $row.name.Substring(0,256) }; $row
                })
                $result = @{ elements = $public; visited = $result.visited; truncated = $result.truncated }
            }
            Write-NativeDesktopState $state
            return @{ ok = $true; generation = $state.generation; control_revision = $state.revision; result = $result }
        }
        Write-NativeDesktopState $state
        return @{ ok = $true; agent_control = $state.mode; control_revision = $state.revision; override_until = $state.until }
    } catch { return @{ ok = $false; code = 'native_desktop_error'; error = $_.Exception.Message } }
    finally { if ($lock) { Exit-NativeDesktopLock $lock } }
}
