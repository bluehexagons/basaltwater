<# Local development API. Dot-source from the native CLI; no setup or upload dependencies.
   Get-NativePowerShell7 is supplied by the native host and returns a verified executable.
   Receipts are deliberately conservative: interrupted guardians require inspection.
#>
function Get-DevelopmentRoot { Join-Path $env:LOCALAPPDATA 'Basaltwater\development' }
function Assert-DevelopmentPath([string]$Path) {
    $cursor = [IO.Path]::GetFullPath($Path)
    if ($cursor -notmatch '\A[A-Za-z]:[\\/]' -or $cursor -match '[\x00-\x1f]' -or $cursor.Substring(2).Contains(':')) { throw 'Development state requires an absolute local path without streams' }
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Development state cannot use reparse paths' }
        }
        $cursor = Split-Path -Parent $cursor
    }
}
function Get-DevelopmentSid { [Security.Principal.WindowsIdentity]::GetCurrent().User.Value }
function Read-DevelopmentJson([string]$Path) {
    Assert-DevelopmentPath $Path
    if ((Get-Item -LiteralPath $Path).Length -gt 4MB) { throw 'Development state exceeds its size bound' }
    $options = @{}
    if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $options.DateKind = 'String' }
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json @options
}
function Stop-DevelopmentProcess($Job, $Process, [bool]$Started, [object[]]$Copies) {
    if ($Job) { $Job.TerminateAndWait(10000) }
    elseif ($Started) {
        if (-not $Process.HasExited) { $Process.Kill() }
        if (-not $Process.WaitForExit(10000)) { throw 'Gated child did not settle.' }
    }
    $streamIndex = 0
    foreach ($copy in $Copies) {
        if (-not $copy.Wait(10000)) { throw "Output stream $streamIndex did not settle (task status: $($copy.Status))." }
        $streamIndex++
    }
}
function Write-DevelopmentJson([string]$Path, $Value) {
    Assert-DevelopmentPath $Path
    $temp = $Path + '.' + [guid]::NewGuid().ToString('N')
    try {
        [IO.File]::WriteAllText($temp, ($Value | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temp -Destination $Path -Force
    } finally { Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue }
}
function Get-DevelopmentDirectory([string]$Id) {
    if ($Id -notmatch '\A[a-f0-9]{32}\z') { throw 'Expected a development task ID (32 lowercase hex characters); use the ID returned by develop exec/node/editor/run/import.' }
    $directory = Join-Path (Get-DevelopmentRoot) $Id
    Assert-DevelopmentPath $directory
    return $directory
}
function Get-DevelopmentRecord([string]$Id) {
    $path = Join-Path (Get-DevelopmentDirectory $Id) 'receipt.json'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Unknown development task $Id; check the ID and Windows account." }
    Assert-DevelopmentPath $path
    if ((Get-Item -LiteralPath $path).Length -gt 65536) { throw 'Development receipt exceeds its size limit' }
    $record = Read-DevelopmentJson $path
    if ($record.OwnerSid -ne (Get-DevelopmentSid) -or $record.Id -ne $Id -or $record.TaskName -ne ('Basaltwater Development ' + $Id)) { throw 'Development task belongs to another account or has an invalid identity.' }
    return $record
}
function Test-DevelopmentProcess([int]$ProcessId, [string]$StartedUtc) {
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) { return $false }
    try { return $process.StartTime.ToUniversalTime().ToString('o') -eq $StartedUtc }
    catch { return $false } finally { $process.Dispose() }
}
function Get-DevelopmentBoot {
    return (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime.ToUniversalTime().Ticks.ToString()
}
function Test-DevelopmentPreviousBoot($Record) {
    $boot = Get-DevelopmentBoot
    if ($Record.PSObject.Properties['Boot'] -and $Record.Boot) { return $Record.Boot -ne $boot }
    return ([DateTime]$Record.CreatedUtc).ToUniversalTime().Ticks -lt [long]$boot
}
function Remove-DevelopmentScheduledTask($Record) {
    $tasks = @(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop | Where-Object TaskName -eq $Record.TaskName)
    if ($tasks.Count) { Unregister-ScheduledTask -TaskName $Record.TaskName -TaskPath '\' -Confirm:$false -ErrorAction Stop }
    if (@(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop | Where-Object TaskName -eq $Record.TaskName).Count) {
        throw 'Development task registration could not be retired; inspect Task Scheduler before retrying.'
    }
}
function Remove-DevelopmentRequest([string]$Directory) {
    $request = Join-Path $Directory 'request.json'
    Assert-DevelopmentPath $request
    if (Test-Path -LiteralPath $request) { Remove-Item -LiteralPath $request -Force }
}
function Complete-AbandonedDevelopmentTask([string]$Id, [IO.FileStream]$GuardianLease = $null) {
    # Caller holds setup.lock. The guardian lock excludes startup/publication;
    # retired receipts prevent an already-dispatched worker from executing later.
    $directory = Get-DevelopmentDirectory $Id
    $lease = $GuardianLease
    if (-not $lease) { $lease = [IO.File]::Open((Join-Path $directory 'guardian.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
    try {
        $record = Get-DevelopmentRecord $Id
        if ($record.CompletedUtc) { return $record }
        $previousBoot = Test-DevelopmentPreviousBoot $record
        if (-not $previousBoot -and ($record.WorkerPid -or $record.State -notin @('queued','launch-uncertain'))) {
            throw 'Development cleanup cannot be confirmed during this boot. Inspect the task and reboot Windows before maintenance.'
        }
        Remove-DevelopmentScheduledTask $record
        $record.State = if ($previousBoot) { 'interrupted' } else { 'cancelled' }
        $record.CompletedUtc = [DateTime]::UtcNow.ToString('o')
        $record.Message = 'Registration retired without replaying project code.'
        Remove-DevelopmentRequest $directory
        Write-DevelopmentJson (Join-Path $directory 'receipt.json') $record
        return $record
    } finally { if (-not $GuardianLease) { $lease.Dispose() } }
}
function Get-DevelopmentStatus([string]$Id) {
    $record = Get-DevelopmentRecord $Id
    if (-not $record.CompletedUtc -and (Test-DevelopmentPreviousBoot $record)) {
        $record.State = 'interrupted'
        $record.Message = 'Work belongs to an earlier Windows boot. Cancel or run setup to retire it without replay.'
    } elseif (-not $record.CompletedUtc -and $record.WorkerPid -and
        -not (Test-DevelopmentProcess $record.WorkerPid $record.WorkerStartedUtc)) {
        $record.State = 'cleanup-uncertain'
        $record.Message = 'Guardian exited without confirming cleanup. Inspect owned process identities and logs before setup/reboot or retry.'
    } elseif ($record.State -eq 'queued' -and [DateTime]::UtcNow -gt ([DateTime]$record.CreatedUtc).ToUniversalTime().AddSeconds(60)) {
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
        if (-not $record.CompletedUtc -and $record.State -eq 'interrupted') {
            $record = Complete-AbandonedDevelopmentTask $record.Id
        }
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
        if (Test-Path -LiteralPath $path -PathType Leaf) { $pins += (Get-Content -LiteralPath $path -Raw -Encoding UTF8).Trim().TrimStart('v') }
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
function Get-DevelopmentTrustedGit {
    # Do not resolve Git from a project-controlled PATH/current directory.
    foreach ($root in @([Environment]::GetFolderPath('ProgramFiles'), [Environment]::GetFolderPath('ProgramFilesX86'))) {
        if (-not $root) { continue }
        $path = Join-Path $root 'Git\cmd\git.exe'
        try {
            Assert-DevelopmentPath $path
            if (Test-Path -LiteralPath $path -PathType Leaf) { return $path }
        } catch { }
    }
    return ''
}
function Get-DevelopmentPowerShellPresence {
    if ($PSVersionTable.PSVersion.Major -ge 7) { return Join-Path $PSHOME 'pwsh.exe' }
    $candidates = @()
    if ($env:ProgramFiles) { $candidates += Join-Path $env:ProgramFiles 'PowerShell\7\pwsh.exe' }
    $candidates += @(Get-Command pwsh.exe -CommandType Application -ErrorAction SilentlyContinue | ForEach-Object Source)
    foreach ($path in $candidates) { if (Test-Path -LiteralPath $path -PathType Leaf) { return $path } }
    return ''
}
function Invoke-DevelopmentGit([string]$Git, [string]$Project, [string]$Arguments, [switch]$AnyOutput) {
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $Git; $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.WorkingDirectory = $Project
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $info.Arguments = '-c core.fsmonitor=false -c core.hooksPath=NUL -c core.untrackedCache=false ' + $Arguments
    foreach ($key in @($info.EnvironmentVariables.Keys)) {
        if ($key -like 'GIT_*') { $info.EnvironmentVariables.Remove($key) }
    }
    $info.EnvironmentVariables['GIT_CONFIG_NOSYSTEM'] = '1'
    $info.EnvironmentVariables['GIT_CONFIG_GLOBAL'] = 'NUL'
    $info.EnvironmentVariables['GIT_OPTIONAL_LOCKS'] = '0'
    $info.EnvironmentVariables['GIT_TERMINAL_PROMPT'] = '0'
    $process = [Diagnostics.Process]::Start($info)
    try {
        # Hashes/config keys fit this bound. For untracked discovery, any output
        # proves dirtiness; stop the owned query without retaining file names.
        $buffer = New-Object char[] 8192
        $output = $process.StandardOutput.ReadBlockAsync($buffer, 0, $buffer.Length)
        $errors = $process.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
        if (-not $output.Wait(5000)) { throw 'Git metadata query timed out' }
        $count = $output.Result
        if ($AnyOutput -and $count -gt 0) {
            return [pscustomobject]@{ ExitCode = $null; Output = ''; HasOutput = $true }
        }
        if ($count -eq $buffer.Length) { throw 'Git metadata output exceeded its bound' }
        if (-not $process.WaitForExit(1000) -or -not $errors.Wait(1000)) { throw 'Git metadata output did not settle' }
        return [pscustomobject]@{ ExitCode = $process.ExitCode; Output = [string]::new($buffer, 0, $count).Trim(); HasOutput = ($count -gt 0) }
    } finally {
        if (-not $process.HasExited) {
            $process.Kill()
            if (-not $process.WaitForExit(10000)) { throw 'Git metadata process cleanup could not be confirmed' }
        }
        $process.Dispose()
    }
}
function Get-DevelopmentCheckout([string]$Project, [string]$Git) {
    $result = [pscustomobject]@{ State = 'unknown'; Revision = $null; Dirty = $null;
        TrackedDirty = $null; Untracked = $null; DirtyScope = 'tracked and untracked files; submodule contents excluded' }
    if (-not $Git) { return $result }
    try {
        $inside = Invoke-DevelopmentGit $Git $Project 'rev-parse --is-inside-work-tree'
        if ($inside.ExitCode -ne 0) {
            # A missing .git ancestor establishes nongit; corrupt metadata stays unknown.
            $cursor = $Project
            while ($cursor) {
                if (Test-Path -LiteralPath (Join-Path $cursor '.git')) { return $result }
                $cursor = Split-Path -Parent $cursor
            }
            $result.State = 'nongit'; return $result
        }
        if ($inside.Output -ne 'true') { return $result }
        # Diff can invoke repository clean/process filters even with --no-ext-diff.
        # Disable every declared filter before touching working files.
        $filters = Invoke-DevelopmentGit $Git $Project 'config --name-only --get-regexp ^filter\.'
        if ($filters.ExitCode -notin @(0,1)) { return $result }
        $disable = ''
        foreach ($key in @($filters.Output -split '\r?\n' | Where-Object { $_ })) {
            if ($key -notmatch '\Afilter\.([A-Za-z0-9_.-]+)\.(clean|smudge|process|required)\z') { return $result }
            $name = $Matches[1]
            $disable += " -c filter.$name.clean= -c filter.$name.process= -c filter.$name.required=false"
        }
        $head = Invoke-DevelopmentGit $Git $Project 'rev-parse --verify HEAD'
        $dirty = Invoke-DevelopmentGit $Git $Project ($disable + ' diff --no-ext-diff --no-textconv --ignore-submodules=all --quiet HEAD --')
        $untracked = Invoke-DevelopmentGit $Git $Project 'ls-files --others --exclude-standard --directory -z' -AnyOutput
        if ($head.ExitCode -eq 0 -and $head.Output -match '\A[0-9a-f]{40,64}\z') { $result.Revision = $head.Output }
        if ($dirty.ExitCode -in @(0,1)) { $result.TrackedDirty = $dirty.ExitCode -eq 1 }
        if ($untracked.HasOutput) { $result.Untracked = $true }
        elseif ($untracked.ExitCode -eq 0) { $result.Untracked = $false }
        if ($result.TrackedDirty -eq $true -or $result.Untracked -eq $true) { $result.Dirty = $true }
        elseif ($null -ne $result.TrackedDirty -and $null -ne $result.Untracked) { $result.Dirty = $false }
        if ($result.Revision -and $null -ne $result.Dirty) { $result.State = 'available' }
    } catch { } # Discovery is honest about unavailable or unreadable metadata.
    return $result
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
            $package = Get-Content -LiteralPath (Join-Path $project 'package.json') -Raw -Encoding UTF8 | ConvertFrom-Json
            if (-not $package.PSObject.Properties['scripts'] -or -not $package.scripts.PSObject.Properties[$scriptName]) { throw "package.json has no script '$scriptName'." }
            if ($package.PSObject.Properties['packageManager'] -and $package.packageManager -notmatch '^npm@\d+\.\d+\.\d+$') { throw 'Only installed runtime npm is supported. Use exec with an explicitly provisioned matching package manager for this project.' }
            $npm = Join-Path (Split-Path -Parent $node) 'node_modules\npm\bin\npm-cli.js'
            if (-not (Test-Path -LiteralPath $npm)) { throw 'Selected fnm runtime lacks npm; repair that runtime explicitly.' }
            if ($package.PSObject.Properties['packageManager']) {
                $npmPackage = Get-Content -LiteralPath (Join-Path (Split-Path -Parent $node) 'node_modules\npm\package.json') -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($package.packageManager -ne ('npm@' + $npmPackage.version)) { throw 'Project packageManager pin does not match selected runtime npm. Provision the pinned manager explicitly.' }
            }
            $exe = $node; $argv = @($npm, 'run', $scriptName, '--') + $literal
            $environment['PATH'] = (Split-Path -Parent $node) + ';' + $(if ($environment.ContainsKey('PATH')) { $environment['PATH'] } else { $env:PATH })
        }
        { $_ -in @('editor','run','import') } {
            if (-not (Test-Path -LiteralPath (Join-Path $project 'project.godot') -PathType Leaf)) { throw 'Godot project.godot is missing.' }
            $godot = if ($env:GODOT_BIN) { $env:GODOT_BIN } elseif ($env:BASALTWATER_GODOT_EXE) { $env:BASALTWATER_GODOT_EXE } else { 'godot.exe' }
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
    Assert-DevelopmentPath $Directory
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
    $action = New-ScheduledTaskAction -Execute $PowerShell -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand ' + $encoded)
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
            if ($record.CompletedUtc) { return [pscustomobject]@{ Id = $id; State = $record.State; CancelRequested = $false } }
            [IO.File]::WriteAllText((Join-Path $directory 'cancel'), 'cancel')
            $probe = $null
            $admission = [IO.File]::Open((Join-Path $env:LOCALAPPDATA 'Basaltwater\setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
            try {
                try { $probe = [IO.File]::Open((Join-Path $directory 'guardian.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
                catch [IO.IOException] { } # The active guardian will consume the marker.
                if ($probe) { $record = Complete-AbandonedDevelopmentTask $id $probe }
            } finally {
                if ($probe) { $probe.Dispose() }
                $admission.Dispose()
            }
            return [pscustomobject]@{ Id = $id; State = $record.State; CancelRequested = $true; Message = 'Use status to confirm cleanup. Uncertain tasks block setup/reboot.' }
        }
        $logs = @{}; $tails = @{}
        foreach ($name in @('stdout','stderr')) {
            $path = Join-Path $directory ($name + '.log'); $logs[$name] = ''
            $tails[$name] = [pscustomobject]@{ ReturnedBytes = 0; OmittedBytes = 0; Truncated = $false }
            if (Test-Path -LiteralPath $path) {
                $stream = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
                try {
                    $null = $stream.Seek([Math]::Max(0, $stream.Length - 65536), 'Begin')
                    $buffer = New-Object byte[] 65536
                    $count = $stream.Read($buffer, 0, $buffer.Length)
                    $logs[$name] = [Text.Encoding]::UTF8.GetString($buffer, 0, $count)
                    $omitted = [Math]::Max(0, $stream.Length - $count)
                    $tails[$name] = [pscustomobject]@{ ReturnedBytes = $count; OmittedBytes = $omitted; Truncated = ($omitted -gt 0) }
                } finally { $stream.Dispose() }
            }
        }
        $retention = if ($record.PSObject.Properties['Streams']) { $record.Streams } else { $null }
        return [pscustomobject]@{ Id = $id; State = $record.State; Stdout = $logs.stdout; Stderr = $logs.stderr; MaximumBytesPerStream = 65536;
            Tails = [pscustomobject]$tails; Streams = $retention }
    }
    $request = New-DevelopmentPlan $Arguments
    if ($request.Verb -eq 'doctor') {
        $node = ''; $nodeError = ''
        try { $node = Resolve-DevelopmentNode $request.Project '' } catch { $nodeError = $_.Exception.Message }
        $git = Get-DevelopmentTrustedGit
        $powershell = Get-DevelopmentPowerShellPresence
        $godot = ''; $missing = @()
        if (-not $powershell) { $missing += 'Stable PowerShell 7' }
        if ((Test-Path -LiteralPath (Join-Path $request.Project 'package.json')) -and -not $node) { $missing += $nodeError }
        if ($node -and (Test-Path -LiteralPath (Join-Path $request.Project 'package.json')) -and
            -not (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $node) 'node_modules\npm\bin\npm-cli.js') -PathType Leaf)) {
            $missing += 'Selected fnm runtime lacks npm-cli.js'
        }
        if (Test-Path -LiteralPath (Join-Path $request.Project 'project.godot')) {
            $selection = if ($env:GODOT_BIN) { $env:GODOT_BIN } elseif ($env:BASALTWATER_GODOT_EXE) { $env:BASALTWATER_GODOT_EXE } else { 'godot.exe' }
            try { $godot = Resolve-DevelopmentExecutable $selection } catch { $missing += $_.Exception.Message }
        }
        return [pscustomobject]@{ Project = $request.Project; PowerShell = $powershell; Node = $node; NodeDiagnostic = $nodeError;
            Git = $git; Checkout = (Get-DevelopmentCheckout $request.Project $git); Godot = $godot; MissingRequirements = @($missing);
            GodotOverride = @($env:BASALTWATER_GODOT_EXE, $env:GODOT_BIN); Message = 'Read-only discovery. Use exec -- literal argv; node SCRIPT selects installed fnm/npm. Tasks return IDs for status/logs/cancel. No upload credentials required.' }
    }
    $powershell = Get-NativePowerShell7
    if ($request.Plan) { return $request }
    if (-not $powershell) { throw 'Stable PowerShell 7 is required; reapply native setup' }
    $setupDirectory = Join-Path $env:LOCALAPPDATA 'Basaltwater'
    Assert-DevelopmentPath $setupDirectory
    New-Item -ItemType Directory -Path $setupDirectory -Force | Out-Null
    try { $admission = [IO.File]::Open((Join-Path $setupDirectory 'setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch [IO.IOException] { throw 'Windows setup/reboot is active; wait for it before starting a development task' }
    try {
    $id = [guid]::NewGuid().ToString('N'); $directory = Get-DevelopmentDirectory $id
    Initialize-DevelopmentDirectory $directory
    $provider = Get-Process -Id $PID
    $record = [pscustomobject]@{ Id = $id; OwnerSid = (Get-DevelopmentSid); SessionId = $provider.SessionId;
        ProviderPid = $PID; ProviderStartedUtc = $provider.StartTime.ToUniversalTime().ToString('o');
        WorkerPid = 0; WorkerStartedUtc = ''; ChildPid = 0; ChildStartedUtc = '';
        CreatedUtc = [DateTime]::UtcNow.ToString('o'); Boot = (Get-DevelopmentBoot); CompletedUtc = $null; State = 'queued'; ExitCode = $null;
        ExecutionStartedUtc = $null; ExecutionEndedUtc = $null; ElapsedSeconds = $null; Streams = $null; StreamLimitBytes = 4MB;
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
        # The guardian may already own or have completed the receipt. A stale
        # dispatcher snapshot must never overwrite its identity or completion.
        throw "Worker launch could not be confirmed. Inspect Task Scheduler; use status/logs/cancel $id before retrying."
    }
    return $record
    } finally { $admission.Dispose() }
}
