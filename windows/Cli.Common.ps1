<# Native launcher installation and the basic Windows command surface. #>
function Build-NativeCli([string]$Source, [string]$Output) {
    $code = '$env:PSModulePath = [Environment]::GetEnvironmentVariable("PSModulePath", "Machine"); ' +
        '$ErrorActionPreference = "Stop"; $ProgressPreference = "SilentlyContinue"; Add-Type -Path ''' + $Source.Replace("'", "''") +
        ''' -OutputAssembly ''' + $Output.Replace("'", "''") + ''' -OutputType ConsoleApplication'
    $shell = Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
    & $shell -NoProfile -ExecutionPolicy Bypass -EncodedCommand ([Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code)))
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Output -PathType Leaf)) { throw 'Native CLI compilation failed' }
}

function Install-NativeCli([string]$Source, [switch]$Plan) {
    $bin = Join-Path $env:LOCALAPPDATA 'Basaltwater\bin'
    $files = @('basaltw.cmd', 'Invoke-Basaltw.ps1', 'Cli.Common.ps1')
    foreach ($file in $files) {
        Assert-NativeSkillPath (Join-Path $bin $file)
        if (-not (Test-Path -LiteralPath (Join-Path $Source "windows\$file") -PathType Leaf)) {
            throw "Native CLI source is incomplete: $file"
        }
    }
    $launcher = Join-Path $bin 'basaltw.exe'
    Assert-NativeSkillPath $launcher
    $launcherSource = Join-Path $Source 'windows\NativeCli.cs'
    if (-not (Test-Path -LiteralPath $launcherSource -PathType Leaf)) { throw 'Native CLI source is incomplete: NativeCli.cs' }
    Assert-NativeSkillPath (Join-Path $bin 'cli-source.json')
    $metadata = Get-Content -LiteralPath (Join-Path $Source 'pyproject.toml') -Raw
    if ($metadata -notmatch '(?m)^version\s*=\s*"([A-Za-z0-9.+-]+)"\s*$') { throw 'Missing Basaltwater package version' }
    $version = $Matches[1]
    Write-Host "Native basaltw command: $bin (help, version, info, doctor, storage, refresh)"
    if ($Plan) { return }
    New-Item -ItemType Directory -Path $bin -Force | Out-Null
    $temporary = Join-Path $bin ('basaltw-' + [guid]::NewGuid().ToString('N') + '.exe')
    try {
        Build-NativeCli $launcherSource $temporary
        # Compile before touching the working launcher. Atomic publication also
        # permits a refresh to replace its own still-running launcher image.
        if (Test-Path -LiteralPath $launcher) { [IO.File]::Replace($temporary, $launcher, [NullString]::Value) }
        else { [IO.File]::Move($temporary, $launcher) }
    } finally { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue }
    foreach ($file in $files) {
        Copy-Item -LiteralPath (Join-Path $Source "windows\$file") -Destination (Join-Path $bin $file) -Force
    }
    @{ Source = [IO.Path]::GetFullPath($Source); PackageVersion = $version } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $bin 'cli-source.json') -Encoding UTF8
    Add-NativeUserPath $bin
}

function Read-NativeCliSetup {
    $path = Join-Path $env:LOCALAPPDATA 'Basaltwater\windows-setup.json'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw 'No saved native Windows setup. Run install.ps1 -Profile agent_windows first.'
    }
    $saved = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if ($saved.Version -ne 1 -or $saved.OwnerSid -ne $sid -or $saved.Profile -ne 'agent_windows' -or
        $saved.Revision -notmatch '\A[0-9a-f]{40}\z' -or $saved.Source -notmatch '\A[A-Za-z]:[\\/]' -or
        -not (Test-Path -LiteralPath (Join-Path $saved.Source 'install.ps1') -PathType Leaf)) {
        throw 'Saved Windows setup does not match this account, profile or source; repair the saved installation before refreshing.'
    }
    return $saved
}

function Get-NativeCliSource {
    $receipt = Join-Path $PSScriptRoot 'cli-source.json'
    if (Test-Path -LiteralPath $receipt) { return (Get-Content -LiteralPath $receipt -Raw -Encoding UTF8 | ConvertFrom-Json).Source }
    return Split-Path -Parent $PSScriptRoot
}

function Show-NativeCliHelp {
    Write-Host @'
Usage: basaltw COMMAND [OPTIONS]

Native Windows commands:
  --help, help       Show this help
  --version, version Show the installed Basaltwater version
  info [--json]      Show this account's saved native setup
  doctor [--skills] [--json]  Check selected tools, or compare installed skill bundles
  storage [--apply] [--target-gib 5] [--json]  Preview/apply low-space system cleanup
  refresh           Update Basaltwater from main and reapply saved selections
  desktop           Native desktop status, observation, control and handoff
  desktop develop   Supervise project tasks, logs and cancellation
  media             Bounded file inspection (optional Python); media doctor probes AV tools

Refresh options:
  --detach          Return after submission; inspect status/logs separately
                    Add --json for a job receipt
  --wait            Run refresh in this terminal instead of a detached job
  --follow ID       Follow live output and wait for an existing job's result
  --status ID       Read a detached refresh receipt
  --logs ID         Read the last 100 lines of its log
  --cancel ID       Cancel a queued refresh before setup starts
  --dry-run         Preview saved/current selections without writes or downloads
  --no-update-source   Reapply the saved Basaltwater revision; still update tools
  --no-restart-t3      Skip the T3 Code start/restart after setup
  --no-reboot          Report required Windows reboots without rebooting
  --game-dev        Add native game build tools, Python, Go and fnm
  --shader-tools    Add Vulkan SDK shader validation
  --godot           Add the standard Godot editor/CLI
  --av-tools        Add optional FFmpeg/ffprobe, preserving preinstalled tools
  --sunshine        Add the native Sunshine streaming service (UAC may be required)
  --node-versions   Add fnm for project-selected Node versions
  --storage-root PATH  Select a mounted NTFS directory for development caches
  --storage-drive DRIVE  Use DRIVE:\Basaltwater (for example: --storage-drive F:)
  --channel main|dev   Select the source channel (both currently track main)
  --version REF        Select a specific source reference

Refresh installs available updates for selected host tools and preserves provider
logins. By default, live output and status are followed in this terminal.
Ctrl+C stops following without cancelling setup. The worker survives T3 closing;
use --follow ID from a new terminal to reconnect to output.
Successful setup queues a graceful T3 Code start/restart.
Explicit installer, Windows Update or servicing reboot requirements queue a
visible 60-second desktop countdown with a Later button and no forced app closure.
Pending file operations alone do not trigger reboot. Interrupted setup continues
once after boot and login. --no-reboot defers reboot for this run.
Other open terminals need to be reopened after setup changes PATH.
Desktop handoff: open PATH [--reveal], terminal --project PATH [--msvc],
notify --title TEXT --message TEXT. Other desktop commands list their own help.
Linux controller, server and agent commands require a Linux installation.
'@
}

function ConvertTo-NativeResultFields($Value) {
    $fields = [ordered]@{}
    if ($Value -is [Collections.IDictionary]) {
        foreach ($key in $Value.Keys) { $fields[$key] = $Value[$key] }
    } elseif ($null -ne $Value) {
        foreach ($property in $Value.PSObject.Properties) { $fields[$property.Name] = $property.Value }
    }
    return $fields
}

function Write-NativeToolResult($Result, [bool]$Ok = $true, [string]$Issue = 'operation_failed',
    [string]$Message = 'Inspect the command result for details.') {
    $fields = ConvertTo-NativeResultFields $Result
    $fields['schema_version'] = 1
    $fields['ok'] = $Ok
    if (-not $Ok) {
        if (-not $fields['issue']) { $fields['issue'] = $Issue }
        if (-not $fields['error']) { $fields['error'] = $Message }
    }
    Write-Host ($fields | ConvertTo-Json -Depth 20 -Compress)
    if ($Ok) { return 0 }
    switch ($fields['issue']) {
        'invalid_input' { return 2 }
        'wait_timeout' { return 124 }
        default { return 1 }
    }
}

function Get-NativeJobView([string]$Id, [string]$State) {
    $states = @{ queued = 'queued'; running = 'running'; complete = 'succeeded'; failed = 'failed';
        cancelled = 'cancelled'; 'timed-out' = 'failed'; 'dispatch-failed' = 'failed' }
    return @{ id = $Id; state = $(if ($states.ContainsKey($State)) { $states[$State] } else { 'unknown' }); native_state = $State }
}

function Write-NativeDevelopmentResult([string]$Verb, $Result) {
    $fields = ConvertTo-NativeResultFields $Result
    if ($Verb -eq 'list') {
        $fields['tasks'] = @(foreach ($task in $fields['tasks']) {
            $taskFields = ConvertTo-NativeResultFields $task
            $taskFields['job'] = Get-NativeJobView $task.task $task.state
            $taskFields
        })
    }
    $record = if ($fields['Receipt']) { ConvertTo-NativeResultFields $fields['Receipt'] } else { $fields }
    if ($fields['Id']) { $fields['job'] = Get-NativeJobView $fields['Id'] $record['State'] }
    if ($record.Contains('ExitCode')) { $fields['child_exit_code'] = $record['ExitCode'] }
    if ($Verb -eq 'cancel') {
        $fields['cancellation'] = if ($record['State'] -eq 'cancelled') { 'confirmed' }
            elseif ($fields['CancelRequested']) { 'requested' } else { 'not_requested' }
    }
    if ($Verb -eq 'logs') {
        $fields['output_truncated'] = [bool]($fields['Tails'].stdout.Truncated -or $fields['Tails'].stderr.Truncated)
        $streams = $fields['Streams']
        $fields['log_truncated'] = if ($streams) { [bool]($streams.Stdout.Truncated -or $streams.Stderr.Truncated) } else { $null }
        $artifacts = $fields['Artifacts']
        $fields.Remove('Artifacts')
        $fields['artifacts'] = @($artifacts)
    }
    if ($Verb -eq 'ready') {
        $fields['readiness'] = @{ condition = 'test_session_ready'; ready = ($fields['WaitState'] -eq 'ready') }
    }
    if ($fields['TimedOut']) {
        return Write-NativeToolResult $fields $false 'wait_timeout' 'The caller wait expired. Inspect or wait on the same job.'
    }
    if (($Verb -eq 'wait' -and ($fields['WaitState'] -ne 'completed' -or $record['State'] -ne 'complete')) -or
        ($Verb -eq 'ready' -and $fields['WaitState'] -ne 'ready')) {
        return Write-NativeToolResult $fields $false 'development_failed' 'The job did not reach the requested milestone. Inspect its receipt.'
    }
    return Write-NativeToolResult $fields
}

function Add-NativeCaptureArtifacts($Fields, [string]$Verb) {
    $nested = ConvertTo-NativeResultFields $Fields['result']
    $capture = if ($Verb -eq 'input') { ConvertTo-NativeResultFields $nested['observation'] } else { $nested }
    if (-not $capture['output']) { return }
    $files = @(@{ kind = 'screenshot'; path = [IO.Path]::GetFullPath($capture['output']); media_type = 'image/png' })
    if ($capture['observation_id']) {
        $capture['schema_version'] = 1
        $capture['ok'] = $true
        $temporary = $null
        $created = $false
        try {
            $receipt = [IO.Path]::ChangeExtension((New-NativeDesktopCapturePath), '.json')
            $temporary = $receipt + '.tmp'
            $bytes = [Text.Encoding]::UTF8.GetBytes(($capture | ConvertTo-Json -Depth 12 -Compress) + "`n")
            $stream = [IO.File]::Open($temporary, 'CreateNew', 'Write', 'None')
            $created = $true
            try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
            [IO.File]::Move($temporary, $receipt)
            $files += @{ kind = 'capture_receipt'; path = $receipt; media_type = 'application/json' }
        } catch {
            $Fields['ok'] = $false; $Fields['issue'] = 'artifact_save_failed'; $Fields['error'] = $_.Exception.Message
            $Fields['stage'] = 'retain_capture'; $Fields['effect'] = 'applied'
        } finally {
            if ($created -and [IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
        }
    }
    $Fields['artifacts'] = $files
}

function Add-NativeEvidenceArtifacts($Fields, [string]$Verb) {
    $files = @()
    if ($Verb -in @('capture','pacing') -and $Fields['output']) {
        $manifest = Join-Path $Fields['output'] 'manifest.json'
        if ([IO.File]::Exists($manifest)) {
            $files += @{ kind = 'evidence_manifest'; path = $manifest; media_type = 'application/json' }
        }
        if ($Verb -eq 'pacing') {
            $trace = Join-Path $Fields['output'] 'raw.csv'
            if ([IO.File]::Exists($trace)) { $files += @{ kind = 'pacing_trace'; path = $trace; media_type = 'text/csv' } }
        }
    } elseif ($Verb -eq 'audio') {
        $capture = ConvertTo-NativeResultFields $Fields['result']
        if ($capture['MetadataPath'] -and [IO.File]::Exists($capture['MetadataPath'])) {
            $files += @{ kind = 'audio_capture'; path = $capture['MetadataPath']; media_type = 'application/json' }
        }
        if ($capture['WavPath'] -and [IO.File]::Exists($capture['WavPath'])) {
            $files += @{ kind = 'process_audio'; path = $capture['WavPath']; media_type = 'audio/wav' }
        }
    }
    if ($files.Count) { $Fields['artifacts'] = $files }
}

function Invoke-NativeCli([string[]]$Arguments) {
    if (-not $Arguments -or ($Arguments.Count -eq 1 -and $Arguments[0] -in @('--help', '-h', 'help'))) {
        Show-NativeCliHelp
        return 0
    }
    $command = $Arguments[0]
    $options = @($Arguments | Select-Object -Skip 1)
    if ($command -eq 'media') {
        try {
            $source = Get-NativeCliSource
            . (Join-Path $source 'windows\Media.Common.ps1')
            return Invoke-NativeMediaCli -Source $source -Arguments $options
        } catch {
            return Write-NativeToolResult @{} $false 'media_unavailable' $_.Exception.Message
        }
    }
    if ($command -eq 'storage') {
        try {
            $source = Get-NativeCliSource
            . (Join-Path $source 'windows\Storage.Common.ps1')
            return (Invoke-NativeStorageCli -Arguments $options)
        } catch {
            if ($options -notcontains '--json') { throw }
            $invalid = $_.Exception -is [ArgumentException]
            $issue = if ($invalid) { 'invalid_input' } else { 'storage_cleanup_failed' }
            $fields = @{}
            if ($options -contains '--apply') {
                $fields.stage = if ($invalid) { 'validate' } else { 'cleanup' }
                $fields.effect = if ($invalid) { 'none' } else { 'unknown' }
            }
            return Write-NativeToolResult $fields $false $issue $_.Exception.Message
        }
    }
    if ($command -eq 'desktop') {
        if (-not $options.Count -or $options[0] -in @('--help','help','-h')) {
            Write-Host @'
Native desktop commands (JSON by default, --json selects the same result):
  status|doctor|environment [--json]
  windows [--task ID] [--json]
  worker start [--idle-seconds 120] [--session-seconds 1800] | worker status|stop
  inspect --pid PID [--name NAME] [--role ROLE] [--json]
  screenshot --window HWND --identity TOKEN --generation GEN [--output NEW.png] [--max-age-seconds 60]
  screenshot --monitor DEVICE [--output NEW.png] [--max-age-seconds 60]
  screenshot ... [--compact]
  capture --window HWND --identity TOKEN --generation GEN --width W --height H --output NEW_DIRECTORY
  pacing --task ID --project PATH --output NEW_DIRECTORY [--seconds 5] [--member PID:FILETIME]
  audio --task ID --project PATH [--member PID:FILETIME] [--seconds 1]
  window focus|move|resize|maximize|minimize|restore|close --window HWND --identity TOKEN --generation GEN
  element invoke|set-text|focus --ref REF --generation GEN [--text TEXT]
  input key|chord|sequence|text|click|move|wheel --window HWND --identity TOKEN --generation GEN --width W --height H
  input key --help  Supported editing keys, chords, sequences and text limits
    key --key CTRL+SHIFT+HOME | sequence --keys HOME,SHIFT+END,F11 (max 32 chords)
  input hold --key W+J --milliseconds 500 [--repeat-ms 100] (same window selectors)
  input hold --timeline 0:W+J,200:J,400:- --milliseconds 500 (max 8 keys, 32 events)
  input hold --button left|right|middle --x X --y Y [--to-x X --to-y Y] --milliseconds 500
  input move --x X --y Y | input wheel --x X --y Y --delta 120 (same window selectors)
  input ... [--observation ID] [--observe [--output NEW.png] [--settle-ms 120]]
  map-point --capture RECEIPT.json --x X --y Y [--to-x X --to-y Y]
    --image-coordinates or --view-width W --view-height H [--view-origin-x X] [--view-origin-y Y]
  input release|lease --lease ID          Cancel/read a finite keyboard lease
  control enabled|human-override|disabled --human-approved [--seconds N|--indefinite]
  open PATH [--reveal] | terminal --project PATH [--msvc]
  notify --title TEXT --message TEXT
  develop doctor|exec|node|editor|run|import|list|status|logs|cancel|wait|ready|diagnose ...
  develop --help  Task options, npm-script examples and bounded waiting
Observe windows/inspect before actions. Pixel capture does not prove rendered content or input outcome.
Screenshot and input --observe return screenshot and capture_receipt artifact entries.
Pass the capture_receipt path into map-point --capture.
Screenshot returns observation_id and capture_freshness, timed from capture admission
with a monotonic clock. Max age is an integer 1-600 (default 60); increase it explicitly
only for stable turn-based work. Tokens are single-use, consumed before input attempt.
New successful capture supersedes the token; failed capture retains it. Mutations and
control/session changes invalidate it. Status/windows never renew it. Token input needs
matching window/identity/generation/width/height; monitor capture cannot authorize it.
Tokens do not prove semantic or scene validity. Input without a token keeps its existing guards.
'@
            return 0
        }
        try {
        $source = Get-NativeCliSource
        . (Join-Path $source 'windows\AgentWorkstation.Common.ps1')
        . (Join-Path $source 'windows\Desktop.Common.ps1')
        if ($options.Count -and $options[0] -eq 'windows' -and $options -contains '--task') {
            . (Join-Path $source 'windows\Development.Common.ps1')
        }
        if ($options.Count -and $options[0] -eq 'develop') {
            . (Join-Path $source 'windows\Development.Common.ps1')
            $result = Invoke-NativeDevelopmentCli @($options | Select-Object -Skip 1)
            if ($null -eq $result) { return 0 }
            return Write-NativeDevelopmentResult $options[1] $result
        } elseif ($options.Count -and $options[0] -eq 'capture') {
            . (Join-Path $source 'windows\Capture.Common.ps1')
            $result = Invoke-NativeWindowCapture @($options | Select-Object -Skip 1)
        } elseif ($options.Count -and $options[0] -eq 'pacing') {
            . (Join-Path $source 'windows\Development.Common.ps1')
            . (Join-Path $source 'windows\Development.Diagnostics.ps1')
            . (Join-Path $source 'windows\Capture.Common.ps1')
            . (Join-Path $source 'windows\Capture.Pacing.ps1')
            $result = Invoke-NativePacing @($options | Select-Object -Skip 1)
        } elseif ($options.Count -and $options[0] -eq 'audio') {
            . (Join-Path $source 'windows\Capture.Common.ps1')
            . (Join-Path $source 'windows\Audio.Cli.ps1')
            $result = Invoke-NativeProcessAudioCli @($options | Select-Object -Skip 1)
        } elseif ($options.Count -and $options[0] -in @('open','terminal','notify')) {
            $result = Invoke-NativeDesktopHandoff -Source $source -Arguments $options
        } else { $result = Invoke-NativeDesktopCli $options }
        if ($null -eq $result) { return 0 }
        $fields = ConvertTo-NativeResultFields $result
        if ($options[0] -in @('screenshot','input')) { Add-NativeCaptureArtifacts $fields $options[0] }
        if ($options[0] -in @('capture','pacing','audio')) { Add-NativeEvidenceArtifacts $fields $options[0] }
        if ($options[0] -eq 'map-point' -and $fields['input_args']) {
            $fields['input_argv'] = $fields['input_args']
            $fields['input_context_argv'] = $fields['input_context_args']
        }
        $ok = (-not $fields.Contains('ok') -or $fields['ok']) -and ($options[0] -ne 'doctor' -or $fields['healthy'])
        if (-not $ok -and $fields['input_delivery']) {
            $effect = @{ 'not-sent' = 'none'; completed = 'applied'; partial = 'partial'; unknown = 'unknown' }
            if (-not $fields['effect']) { $fields['effect'] = $effect[$fields['input_delivery']] }
            if (-not $fields['stage']) { $fields['stage'] = $fields['dispatch_stage'].Replace('-', '_') }
        }
        return Write-NativeToolResult $fields $ok 'desktop_operation_failed'
        } catch {
            $errorInfo = $_.Exception
            $issue = if ($errorInfo -is [ArgumentException]) { 'invalid_input' } else { 'desktop_operation_failed' }
            $details = @{}
            if ($options[0] -eq 'develop' -and $options.Count -gt 1 -and $options[1] -in @('exec','node','editor','run','import','cancel')) {
                $details['stage'] = if ($errorInfo.Data['stage']) { $errorInfo.Data['stage'] } else { $options[1] }
                $details['effect'] = if ($errorInfo.Data['effect']) { $errorInfo.Data['effect'] }
                    elseif ($issue -eq 'invalid_input') { 'none' } else { 'unknown' }
                if ($errorInfo.Data['job_id']) { $details['job'] = Get-NativeJobView $errorInfo.Data['job_id'] $errorInfo.Data['job_state'] }
                if ($errorInfo.Data['cancellation']) { $details['cancellation'] = $errorInfo.Data['cancellation'] }
            }
            return Write-NativeToolResult $details $false $issue $errorInfo.Message
        }
    }
    if ($command -in @('--version', 'version')) {
        if ($options.Count) { throw 'Usage: basaltw --version' }
        $receipt = Join-Path $PSScriptRoot 'cli-source.json'
        if (Test-Path -LiteralPath $receipt) {
            $version = (Get-Content -LiteralPath $receipt -Raw -Encoding UTF8 | ConvertFrom-Json).PackageVersion
        } else {
            $metadata = Get-Content -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) 'pyproject.toml') -Raw
            if ($metadata -notmatch '(?m)^version\s*=\s*"([A-Za-z0-9.+-]+)"\s*$') { throw 'Missing Basaltwater package version' }
            $version = $Matches[1]
        }
        Write-Host "Basaltwater $version (native Windows)"
        return 0
    }
    if ($command -eq 'info') {
        if ($options.Count -gt 1 -or ($options.Count -eq 1 -and $options[0] -ne '--json')) { throw 'Usage: basaltw info [--json]' }
        try { $saved = Read-NativeCliSetup } catch {
            if ($options -notcontains '--json') { throw }
            return Write-NativeToolResult @{} $false 'setup_record_unavailable' $_.Exception.Message
        }
        $info = [ordered]@{ Platform = 'windows'; Profile = $saved.Profile; Revision = $saved.Revision;
            Source = $saved.Source; Phase = $saved.Phase }
        foreach ($name in @('AgentTool', 'T3CodeDesktop', 'Node', 'Python', 'Go', 'NodeVersions', 'GameDev', 'ShaderTools', 'Godot', 'Sunshine', 'AvTools', 'StorageRoot')) {
            if ($saved.PSObject.Properties[$name]) { $info[$name] = $saved.$name }
        }
        if ($options -contains '--json') { return Write-NativeToolResult $info }
        else { Write-Host (($info.GetEnumerator() | ForEach-Object { "  $($_.Key): $($_.Value -join ', ')" }) -join "`n") }
        return 0
    }
    if ($command -eq 'doctor') {
        if ($options.Count -eq 1 -and $options[0] -in @('--help', '-h')) { Show-NativeCliHelp; return 0 }
        if (@($options | Where-Object { $_ -notin @('--json','--skills') }).Count -or
            @($options | Select-Object -Unique).Count -ne $options.Count) { throw 'Usage: basaltw doctor [--skills] [--json]' }
        # Use this CLI's source, including when saved setup is interrupted at a
        # different revision. The diagnostic itself reads the saved selections.
        $source = Split-Path -Parent $PSScriptRoot
        $receipt = Join-Path $PSScriptRoot 'cli-source.json'
        if (Test-Path -LiteralPath $receipt) { $source = (Get-Content -LiteralPath $receipt -Raw -Encoding UTF8 | ConvertFrom-Json).Source }
        $entry = Join-Path $source 'windows\Test-AgentWorkstation.ps1'
        if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) {
            $message = 'Windows diagnostic source is missing; refresh Basaltwater from a current installer.'
            if ($options -contains '--json') { return Write-NativeToolResult @{} $false 'diagnostics_unavailable' $message }
            throw $message
        }
        $diagnosticOptions = @()
        if ($options -contains '--json') { $diagnosticOptions += '-Json' }
        if ($options -contains '--skills') { $diagnosticOptions += '-Skills' }
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $entry @diagnosticOptions |
            ForEach-Object { Write-Host $_ }
        return $LASTEXITCODE
    }
    if ($command -ne 'refresh') { throw "Unsupported Windows command '$command'. Run basaltw --help for available commands." }
    if ($options.Count -eq 1 -and $options[0] -in @('--help', '-h')) { Show-NativeCliHelp; return 0 }
    $json = $options.Count -eq 3 -and $options[2] -eq '--json'
    if (($options.Count -eq 2 -or $json) -and $options[0] -in @('--status','--logs','--cancel','--follow')) {
        if ($json -and $options[0] -eq '--follow') { throw 'Refresh --follow streams live text' }
        try {
        $source = Get-NativeCliSource
        . (Join-Path $source 'windows\AgentWorkstation.Common.ps1')
        . (Join-Path $source 'windows\Refresh.Common.ps1')
        if ($options[0] -eq '--follow') { return Watch-NativeRefresh $options[1] }
        if ($options[0] -eq '--cancel') {
            $receipt = Stop-NativeQueuedRefresh $options[1]
            return Write-NativeToolResult @{ job = (Get-NativeJobView $receipt.Id $receipt.Status); cancellation = 'confirmed' }
        }
        $receipt = Get-NativeRefreshStatus $options[1]
        $job = Get-NativeJobView $receipt.Id $receipt.Status
        if ($options[0] -eq '--logs') {
            $log = Join-Path (Get-NativeRefreshDirectory $receipt.Id) 'setup.log'
            Assert-NativeSkillPath $log
            if ($json) {
                $fields = @{ job = $job; log = ''; artifacts = @(); output_truncated = [bool]$receipt.LogTruncated }
                if (Test-Path -LiteralPath $log -PathType Leaf) {
                    $stream = [IO.File]::Open($log, 'Open', 'Read', 'ReadWrite')
                    try {
                        $offset = [Math]::Max(0, $stream.Length - 65536)
                        $null = $stream.Seek($offset, 'Begin')
                        $buffer = New-Object byte[] 65536
                        $count = $stream.Read($buffer, 0, $buffer.Length)
                        $fields.log = [Text.Encoding]::UTF8.GetString($buffer, 0, $count)
                        $fields.output_truncated = $fields.output_truncated -or ($offset -gt 0)
                        $fields.artifacts = @(@{ kind = 'job_log'; path = $log; media_type = 'text/plain' })
                    } finally { $stream.Dispose() }
                }
                return Write-NativeToolResult $fields
            }
            if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 100 -Encoding UTF8 | ForEach-Object { Write-Host $_ } }
        } else {
            $fields = ConvertTo-NativeResultFields ($receipt | Select-Object Id,Status,StartedUtc,CompletedUtc,Log)
            $fields['job'] = $job
            return Write-NativeToolResult $fields
        }
        return 0
        } catch {
            if (-not $json -and $options[0] -in @('--follow','--logs')) { throw }
            $fields = @{}
            if ($options[0] -eq '--cancel') { $fields.stage = 'cancel'; $fields.effect = 'unknown' }
            return Write-NativeToolResult $fields $false 'refresh_job_unavailable' $_.Exception.Message
        }
    }
    $wait = $options -contains '--wait'
    $detach = $options -contains '--detach'
    $json = $options -contains '--json'
    if ($json -and (-not $detach -or $wait -or $options -contains '--dry-run')) {
        return Write-NativeToolResult @{ stage = 'validation'; effect = 'none' } $false 'invalid_input' 'Refresh submission uses --detach --json.'
    }
    if ($wait -and $detach) { throw 'Choose --wait or --detach' }
    $options = @($options | Where-Object { $_ -notin @('--wait','--detach','--json') })
    $stage = 'validation'
    try {
    # Explicit switches also let an installed CLI update an older saved entry
    # point that predates automatic native reruns and source updates.
    $forward = @('-Profile', 'agent_windows', '-Resume', '-UpdateSource')
    $switches = @{ '--dry-run' = '-Plan'; '--game-dev' = '-GameDev'; '--shader-tools' = '-ShaderTools';
                   '--godot' = '-Godot'; '--av-tools' = '-AvTools'; '--sunshine' = '-Sunshine'; '--node-versions' = '-NodeVersions';
                   '--no-restart-t3' = '-NoRestartT3'; '--no-reboot' = '-NoReboot' }
    $values = @{ '--storage-root' = '-StorageRoot'; '--storage-drive' = '-StorageRoot'; '--channel' = '-Channel'; '--version' = '-Version' }
    $storageSelected = $false
    for ($index = 0; $index -lt $options.Count; $index++) {
        $option = $options[$index]
        if ($option -eq '--no-update-source') {
            $forward = @($forward | Where-Object { $_ -ne '-UpdateSource' })
            $forward += '-NoUpdateSource'
            continue
        }
        if ($switches.ContainsKey($option)) { $forward += $switches[$option]; continue }
        if ($values.ContainsKey($option)) {
            $index++
            if ($index -ge $options.Count -or -not $options[$index] -or $options[$index].StartsWith('-')) {
                throw "Missing value for $option"
            }
            $value = $options[$index]
            if ($option -in @('--storage-root', '--storage-drive')) {
                if ($storageSelected) { throw 'Choose one --storage-drive or --storage-root option' }
                $storageSelected = $true
            }
            if ($option -eq '--storage-drive') {
                if ($value -notmatch '\A[A-Za-z]:?\z') { throw 'Storage drive must be one drive letter, such as F or F:' }
                $value = $value.Substring(0, 1).ToUpperInvariant() + ':\Basaltwater'
            }
            if ($option -eq '--channel' -and $value -notin @('main', 'dev')) { throw 'Channel must be main or dev' }
            if ($option -eq '--version' -and $value -notmatch '\A[A-Za-z0-9._-]+\z') { throw 'Invalid release reference' }
            if ($option -eq '--storage-root' -and ($value -notmatch '\A[A-Za-z]:[\\/]' -or $value -match '["\x00-\x1f]')) {
                throw 'Storage root must be an absolute local Windows drive path'
            }
            # A trailing backslash can escape PS 5.1's native argument quotes.
            if ($option -eq '--storage-root' -and $value.Length -gt 3) { $value = $value.TrimEnd('\', '/') }
            $forward += @($values[$option], $value)
            continue
        }
        throw "Unsupported refresh option '$option'. Run basaltw refresh --help."
    }
    $stage = 'load_setup'
    $saved = Read-NativeCliSetup
    if (-not $wait -and '-Plan' -notin $forward) {
        $source = Get-NativeCliSource
        . (Join-Path $source 'windows\AgentWorkstation.Common.ps1')
        . (Join-Path $source 'windows\Refresh.Common.ps1')
        $stage = 'submit'
        $job = Start-NativeRefresh -Saved $saved -Source $source -Arguments $forward
        if ($json) { return Write-NativeToolResult @{ job = (Get-NativeJobView $job.Id $job.Status) } }
        Write-Host ("Refresh queued: $($job.Id). Setup runs independently of this terminal.")
        Write-Host ("Status: $($job.StatusCommand)`nLog: $($job.LogsCommand)")
        if ($detach) { return 0 }
        return Watch-NativeRefresh $job.Id
    }
    $previousPreference = $ErrorActionPreference
    try {
        # PS 5.1 converts redirected native stderr (including harmless tool
        # warnings) to error records. The installer exit code owns success.
        $ErrorActionPreference = 'Continue'
        & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File (Join-Path $saved.Source 'install.ps1') @forward 2>&1 |
            ForEach-Object { Write-Host "$_" }
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    return $code
    } catch {
        if (-not $json) { throw }
        $fields = @{ stage = $stage; effect = $(if ($stage -eq 'submit') { 'unknown' } else { 'none' }) }
        if ($_.Exception.Data['job_id']) { $fields.job = Get-NativeJobView $_.Exception.Data['job_id'] '' }
        $issue = if ($stage -eq 'validation') { 'invalid_input' } else { 'refresh_submit_failed' }
        return Write-NativeToolResult $fields $false $issue $_.Exception.Message
    }
}
