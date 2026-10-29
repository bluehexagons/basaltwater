<# Local development API. Dot-source from the native CLI; no setup or upload dependencies.
   Get-NativePowerShell7 is supplied by the native host and returns a verified executable.
   Receipts are deliberately conservative: interrupted guardians require inspection.
#>
function Get-DevelopmentRoot { Join-Path $env:LOCALAPPDATA 'Basaltwater\development' }
function Get-DevelopmentSid { [Security.Principal.WindowsIdentity]::GetCurrent().User.Value }
function Stop-DevelopmentProcess($Job, $Process, [bool]$Started, [object[]]$Copies) {
    if ($Job) { $Job.TerminateAndWait(10000) }
    elseif ($Started) {
        if (-not $Process.HasExited) { $Process.Kill() }
        if (-not $Process.WaitForExit(10000)) { throw 'Gated child did not settle.' }
    }
    foreach ($copy in $Copies) { if (-not $copy.Wait(10000)) { throw 'Output stream did not settle.' } }
}
function Write-DevelopmentJson([string]$Path, $Value) {
    $temp = $Path + '.' + [guid]::NewGuid().ToString('N')
    try {
        [IO.File]::WriteAllText($temp, ($Value | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temp -Destination $Path -Force
    } finally { Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue }
}
function Get-DevelopmentDirectory([string]$Id) {
    if ($Id -notmatch '^[a-f0-9]{32}$') { throw 'Expected a development task ID (32 lowercase hex characters); use the ID returned by develop exec/node/editor/run/import.' }
    Join-Path (Get-DevelopmentRoot) $Id
}
function Get-DevelopmentRecord([string]$Id) {
    $path = Join-Path (Get-DevelopmentDirectory $Id) 'receipt.json'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Unknown development task $Id; check the ID and Windows account." }
    $record = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($record.OwnerSid -ne (Get-DevelopmentSid)) { throw 'Development task belongs to another account.' }
    return $record
}
function Test-DevelopmentProcess([int]$ProcessId, [string]$StartedUtc) {
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) { return $false }
    try { return $process.StartTime.ToUniversalTime().ToString('o') -eq $StartedUtc }
    catch { return $false } finally { $process.Dispose() }
}
function Get-DevelopmentStatus([string]$Id) {
    $record = Get-DevelopmentRecord $Id
    if (-not $record.CompletedUtc -and $record.WorkerPid -and
        -not (Test-DevelopmentProcess $record.WorkerPid $record.WorkerStartedUtc)) {
        $record.State = 'cleanup-uncertain'
        $record.Message = 'Guardian exited without confirming cleanup. Inspect owned process identities and logs before setup/reboot or retry.'
    } elseif ($record.State -eq 'queued' -and [DateTime]::UtcNow -gt ([DateTime]$record.CreatedUtc).AddSeconds(60)) {
        $record.State = 'launch-uncertain'
        $record.Message = 'Worker did not acknowledge launch. Inspect Task Scheduler and use develop cancel TASKID; do not blindly relaunch.'
    }
    return $record
}
function Assert-NativeDevelopmentIdle {
    $root = Get-DevelopmentRoot
    if (-not (Test-Path -LiteralPath $root)) { return }
    foreach ($directory in Get-ChildItem -LiteralPath $root -Directory) {
        if ($directory.Name -notmatch '^[a-f0-9]{32}$') { throw 'Unexpected development state directory; inspect before setup/reboot.' }
        $record = Get-DevelopmentStatus $directory.Name
        if (-not $record.CompletedUtc) { throw "Development task $($record.Id) is $($record.State). Use basaltw desktop develop status/logs/cancel $($record.Id); cleanup must settle before setup/reboot." }
    }
}
function Resolve-DevelopmentExecutable([string]$Name) {
    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $command) { throw "Executable '$Name' is unavailable in the provider PATH. Install it explicitly and restart the provider shell." }
    if ([IO.Path]::GetExtension($command.Source) -in @('.cmd', '.bat')) {
        throw 'Batch launchers are not literal executable argv. Use exec -- pwsh.exe -File SCRIPT.ps1, or node SCRIPT for package scripts.'
    }
    return $command.Source
}
function Resolve-DevelopmentNode([string]$Project, [string]$Runtime) {
    $pins = @()
    foreach ($name in @('.node-version', '.nvmrc')) {
        $path = Join-Path $Project $name
        if (Test-Path -LiteralPath $path -PathType Leaf) { $pins += (Get-Content -LiteralPath $path -Raw).Trim().TrimStart('v') }
    }
    if (@($pins | Select-Object -Unique).Count -gt 1) { throw 'Conflicting .node-version/.nvmrc pins; align the project pins.' }
    if (-not $Runtime -and $pins.Count) { $Runtime = $pins[0] }
    $Runtime = $Runtime.TrimStart('v')
    if ($Runtime -notmatch '^\d+(?:\.\d+){0,2}$') { throw 'Node requires a numeric project pin or --runtime VERSION and an explicitly installed fnm runtime; aliases/ranges are unsupported.' }
    if ($pins.Count -and $Runtime -ne $pins[0] -and -not $Runtime.StartsWith($pins[0] + '.')) { throw '--runtime conflicts with the project Node pin.' }
    $fnmRoot = if ($env:FNM_DIR) { $env:FNM_DIR } else { Join-Path $env:APPDATA 'fnm' }
    $versions = Join-Path $fnmRoot 'node-versions'
    $candidates = @(Get-ChildItem -LiteralPath $versions -Directory -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match '^v\d+\.\d+\.\d+$' -and ($_.Name.Substring(1) -eq $Runtime -or $_.Name.Substring(1).StartsWith($Runtime + '.'))
    } | Sort-Object { [version]$_.Name.Substring(1) } -Descending)
    foreach ($candidate in $candidates) {
        $node = Join-Path $candidate.FullName 'installation\node.exe'
        if (Test-Path -LiteralPath $node -PathType Leaf) { return $node }
    }
    throw "Node $Runtime is not installed under fnm ($versions). Install the reviewed runtime explicitly with fnm install $Runtime, then retry. System Node is never substituted."
}
function New-DevelopmentPlan([string[]]$Arguments) {
    if (-not $Arguments.Count) { throw 'Usage: develop doctor|exec|node|editor|run|import --project PATH [--msvc] [--runtime VERSION] [--env NAME=VALUE] [--timeout SECONDS] [--dry-run] -- ARGV; or status|logs|cancel TASKID' }
    $verb = $Arguments[0]
    if ($verb -notin @('doctor','exec','node','editor','run','import')) { throw "Unknown development command '$verb'. Use doctor, exec, node, editor, run, import, status, logs, cancel." }
    $project = ''; $runtime = ''; $msvc = $false; $plan = $false; $timeout = 3600
    $environment = @{}; $literal = @(); $scriptName = ''
    for ($i = 1; $i -lt $Arguments.Count; $i++) {
        $arg = $Arguments[$i]
        if ($arg -eq '--') { $literal = @($Arguments | Select-Object -Skip ($i + 1)); break }
        switch ($arg) {
            '--msvc' { $msvc = $true }
            { $_ -in @('--dry-run','--plan') } { $plan = $true }
            { $_ -in @('--project','--runtime','--timeout','--env') } {
                if (++$i -ge $Arguments.Count) { throw "Missing value for $arg" }
                $value = $Arguments[$i]
                switch ($arg) {
                    '--project' { $project = $value }
                    '--runtime' { $runtime = $value }
                    '--timeout' { if ($value -notmatch '^\d+$' -or [long]$value -lt 1 -or [long]$value -gt 86400) { throw 'Timeout must be 1-86400 seconds.' }; $timeout = [int]$value }
                    '--env' {
                        if ($value -notmatch '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { throw 'Use --env NAME=VALUE.' }
                        if ($Matches[1] -eq 'LOCALAPPDATA') { throw 'LOCALAPPDATA identifies the task store and cannot be overridden.' }
                        $environment[$Matches[1]] = $Matches[2]
                    }
                }
            }
            default { if ($verb -eq 'node' -and -not $scriptName -and $arg -notlike '--*') { $scriptName = $arg } else { throw "Unexpected option '$arg'; put literal arguments after --." } }
        }
    }
    if (-not $project -or -not (Test-Path -LiteralPath $project -PathType Container)) { throw 'Supply --project PATH naming an existing local project directory.' }
    $project = (Resolve-Path -LiteralPath $project).ProviderPath
    if ($project.StartsWith('\\')) { throw 'Use a local Windows project path, not a network share.' }
    $exe = ''; $argv = @(); $node = ''
    switch ($verb) {
        'exec' {
            if (-not $literal.Count) { throw 'exec requires -- EXECUTABLE [literal arguments].' }
            # cl/link are supplied by the supported VS environment only inside the child.
            $exe = if ($msvc -and $literal[0] -match '^[A-Za-z0-9_.+-]+\.exe$') { $literal[0] } else { Resolve-DevelopmentExecutable $literal[0] }
            $argv = @($literal | Select-Object -Skip 1)
        }
        'node' {
            if (-not $scriptName) { throw 'node requires a package script name before --project.' }
            $node = Resolve-DevelopmentNode $project $runtime
            $package = Get-Content -LiteralPath (Join-Path $project 'package.json') -Raw | ConvertFrom-Json
            if (-not $package.PSObject.Properties['scripts'] -or -not $package.scripts.PSObject.Properties[$scriptName]) { throw "package.json has no script '$scriptName'." }
            if ($package.PSObject.Properties['packageManager'] -and $package.packageManager -notmatch '^npm@\d+\.\d+\.\d+$') { throw 'Only installed runtime npm is supported. Use exec with an explicitly provisioned matching package manager for this project.' }
            $npm = Join-Path (Split-Path -Parent $node) 'node_modules\npm\bin\npm-cli.js'
            if (-not (Test-Path -LiteralPath $npm)) { throw 'Selected fnm runtime lacks npm; repair that runtime explicitly.' }
            if ($package.PSObject.Properties['packageManager']) {
                $npmPackage = Get-Content -LiteralPath (Join-Path (Split-Path -Parent $node) 'node_modules\npm\package.json') -Raw | ConvertFrom-Json
                if ($package.packageManager -ne ('npm@' + $npmPackage.version)) { throw 'Project packageManager pin does not match selected runtime npm. Provision the pinned manager explicitly.' }
            }
            $exe = $node; $argv = @($npm, 'run', $scriptName, '--') + $literal
            $environment['PATH'] = (Split-Path -Parent $node) + ';' + $(if ($environment.ContainsKey('PATH')) { $environment['PATH'] } else { $env:PATH })
        }
        { $_ -in @('editor','run','import') } {
            if (-not (Test-Path -LiteralPath (Join-Path $project 'project.godot') -PathType Leaf)) { throw 'Godot project.godot is missing.' }
            $godot = if ($env:BASALTWATER_GODOT_EXE) { $env:BASALTWATER_GODOT_EXE } elseif ($env:GODOT_BIN) { $env:GODOT_BIN } else { 'godot.exe' }
            $exe = Resolve-DevelopmentExecutable $godot
            $argv = @('--path', $project)
            if ($verb -eq 'editor') { $argv += '--editor' }
            if ($verb -eq 'import') { $argv += @('--headless','--editor','--import') }
            $argv += $literal
        }
    }
    [pscustomobject]@{ Verb = $verb; Project = $project; Executable = $exe; Argv = @($argv); Environment = $environment;
        Msvc = $msvc; TimeoutSeconds = $timeout; Plan = $plan; Node = $node }
}
function Initialize-DevelopmentDirectory([string]$Directory) {
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    # Provider environments can contain credentials. Do not inherit broad profile ACLs.
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $sid = [Security.Principal.SecurityIdentifier]::new((Get-DevelopmentSid))
    $acl.SetOwner($sid); $acl.SetAccessRuleProtection($true, $false)
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    Set-Acl -LiteralPath $Directory -AclObject $acl
}
function Start-DevelopmentScheduledTask($Record, [string]$PowerShell) {
    $worker = Join-Path $PSScriptRoot 'Invoke-DevelopmentTask.ps1'
    $code = "& '" + $worker.Replace("'", "''") + "' -TaskId '" + $Record.Id + "'"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
    $action = New-ScheduledTaskAction -Execute $PowerShell -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand ' + $encoded)
    $principal = New-ScheduledTaskPrincipal -UserId $Record.OwnerSid -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([timespan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $Record.TaskName -Action $action -Principal $principal -Settings $settings | Out-Null
    Start-ScheduledTask -TaskName $Record.TaskName
}
function Invoke-NativeDevelopmentCli([string[]]$Arguments) {
    if ($Arguments.Count -and $Arguments[0] -in @('status','logs','cancel')) {
        if ($Arguments.Count -ne 2) { throw 'Usage: develop status|logs|cancel TASKID. Logs return the last 64 KiB of each stream.' }
        $id = $Arguments[1]; $record = Get-DevelopmentStatus $id
        $directory = Get-DevelopmentDirectory $id
        if ($Arguments[0] -eq 'status') { return $record }
        if ($Arguments[0] -eq 'cancel') {
            if (-not $record.CompletedUtc) { [IO.File]::WriteAllText((Join-Path $directory 'cancel'), 'cancel') }
            return [pscustomobject]@{ Id = $id; State = $record.State; CancelRequested = (-not $record.CompletedUtc); Message = 'Cancellation is cooperative with the guardian; use status to confirm cleanup. Uncertain tasks block setup/reboot.' }
        }
        $logs = @{}
        foreach ($name in @('stdout','stderr')) {
            $path = Join-Path $directory ($name + '.log'); $logs[$name] = ''
            if (Test-Path -LiteralPath $path) {
                $stream = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
                try {
                    $null = $stream.Seek([Math]::Max(0, $stream.Length - 65536), 'Begin')
                    $buffer = New-Object byte[] 65536
                    $count = $stream.Read($buffer, 0, $buffer.Length)
                    $logs[$name] = [Text.Encoding]::UTF8.GetString($buffer, 0, $count)
                } finally { $stream.Dispose() }
            }
        }
        return [pscustomobject]@{ Id = $id; State = $record.State; Stdout = $logs.stdout; Stderr = $logs.stderr; MaximumBytesPerStream = 65536 }
    }
    $request = New-DevelopmentPlan $Arguments
    if ($request.Verb -eq 'doctor') {
        $node = ''; $nodeError = ''
        try { $node = Resolve-DevelopmentNode $request.Project '' } catch { $nodeError = $_.Exception.Message }
        return [pscustomobject]@{ Project = $request.Project; PowerShell = (Get-NativePowerShell7); Node = $node; NodeDiagnostic = $nodeError;
            GodotOverride = @($env:BASALTWATER_GODOT_EXE, $env:GODOT_BIN); Message = 'Read-only discovery. Use exec -- literal argv; node SCRIPT selects installed fnm/npm. Tasks return IDs for status/logs/cancel. No upload credentials required.' }
    }
    $powershell = Get-NativePowerShell7
    if ($request.Plan) { return $request }
    $id = [guid]::NewGuid().ToString('N'); $directory = Get-DevelopmentDirectory $id
    Initialize-DevelopmentDirectory $directory
    $provider = Get-Process -Id $PID
    $record = [pscustomobject]@{ Id = $id; OwnerSid = (Get-DevelopmentSid); SessionId = $provider.SessionId;
        ProviderPid = $PID; ProviderStartedUtc = $provider.StartTime.ToUniversalTime().ToString('o');
        WorkerPid = 0; WorkerStartedUtc = ''; ChildPid = 0; ChildStartedUtc = '';
        CreatedUtc = [DateTime]::UtcNow.ToString('o'); CompletedUtc = $null; State = 'queued'; ExitCode = $null;
        TaskName = ('Basaltwater Development ' + $id); Message = 'Use develop status/logs/cancel TASKID.' }
    $provider.Dispose()
    $snapshot = @{}; foreach ($entry in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) { $snapshot[$entry.Key] = $entry.Value }
    foreach ($key in $request.Environment.Keys) { $snapshot[$key] = $request.Environment[$key] }
    $request.Environment = $snapshot
    $request | Add-Member NoteProperty PowerShell $powershell
    Write-DevelopmentJson (Join-Path $directory 'request.json') $request
    Write-DevelopmentJson (Join-Path $directory 'receipt.json') $record
    try { Start-DevelopmentScheduledTask $record $powershell }
    catch {
        # Start may have reached Scheduler before transport failed. Never claim cleanup.
        $record.State = 'launch-uncertain'; $record.Message = "Worker launch failed: $($_.Exception.Message). Inspect Task Scheduler; use status/logs/cancel $id."
        Write-DevelopmentJson (Join-Path $directory 'receipt.json') $record
        throw $record.Message
    }
    return $record
}
