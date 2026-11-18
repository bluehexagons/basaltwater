<# Run one trusted native PowerShell job and upload one verified artifact. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SourceDirectory,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{40}$')][string]$Revision,
    [Parameter(Mandatory = $true)][string]$Script,
    [Parameter(Mandatory = $true)][string]$Artifact,
    [Parameter(Mandatory = $true)][uri]$UploadUri,
    [Parameter(Mandatory = $true)][string]$CredentialFile,
    [ValidateRange(1, 86400)][int]$TimeoutSeconds = 3600
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Resolve-JobPath([string]$root, [string]$relative) {
    if ([IO.Path]::IsPathRooted($relative) -or $relative -match '[:*?"<>|]' -or
        $relative -match '(^|[\\/])\.\.?(?:[\\/]|$)') {
        throw "Invalid job-relative path: $relative"
    }
    if (-not $relative -or $relative.EndsWith('\') -or $relative.EndsWith('/')) {
        throw "Expected a file path"
    }
    $full = [IO.Path]::GetFullPath((Join-Path $root $relative))
    $prefix = [IO.Path]::GetFullPath($root).TrimEnd('\') + '\'
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Job path escapes source directory"
    }
    $cursor = $full
    while ($cursor -and ($cursor.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or $cursor -eq $root)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Job path contains a reparse point: $cursor"
            }
        }
        $cursor = Split-Path -Parent $cursor
    }
    return $full
}

function New-JobSnapshot([string]$source, [string]$jobDirectory, [string]$revision, [string]$entry) {
    $git = (Get-Command git.exe -CommandType Application -ErrorAction Stop).Source
    $savedGitEnvironment = @(Get-ChildItem Env: | Where-Object Name -like 'GIT_*')
    $repository = Join-Path $jobDirectory 'git'
    $archive = Join-Path $jobDirectory 'source.zip'
    $snapshot = Join-Path $jobDirectory 'source'
    $emptyConfig = Join-Path $jobDirectory 'empty-git-config'
    [IO.File]::WriteAllText($emptyConfig, '')
    try {
        foreach ($variable in $savedGitEnvironment) { Remove-Item -LiteralPath "Env:$($variable.Name)" }
        $env:GIT_CONFIG_NOSYSTEM = '1'
        $env:GIT_CONFIG_GLOBAL = $emptyConfig
        $env:GIT_ATTR_NOSYSTEM = '1'
        $env:GIT_NO_REPLACE_OBJECTS = '1'
        $env:GIT_TERMINAL_PROMPT = '0'
        $head = & $git -C $source rev-parse --verify HEAD
        if ($LASTEXITCODE -ne 0 -or $head -ne $revision) { throw 'Source checkout does not match the requested revision' }

        # Fetch objects, with no checkout, hooks, shared object store or local
        # Git attributes. A later change to the source HEAD does not change
        # the object ID fetched and exported.
        & $git init --bare --quiet --template= $repository
        if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the private job repository' }
        & $git -C $repository -c protocol.allow=never -c protocol.file.allow=always -c fetch.fsckObjects=true `
            fetch --quiet --depth=1 --no-tags --no-write-fetch-head -- $source $revision
        if ($LASTEXITCODE -ne 0) { throw 'Could not fetch the requested revision into the private job repository' }
        $members = @(& $git -C $repository ls-tree -r $revision)
        if ($LASTEXITCODE -ne 0 -or @($members | Where-Object { $_ -notmatch '^100(644|755) blob ' }).Count) {
            throw 'The requested revision must contain only regular files; symlinks and submodules are unsupported'
        }
        $gitEntry = $entry.Replace('\', '/')
        $trackedEntry = @(& $git -C $repository --literal-pathspecs ls-tree -r --name-only $revision -- $gitEntry)
        if ($LASTEXITCODE -ne 0 -or $trackedEntry.Count -ne 1) {
            throw 'Job script is not tracked by the requested revision'
        }
        # Export the tree without commit-based export substitutions. The tree's
        # own export-ignore attributes can exclude files from the snapshot.
        & $git -C $repository -c "core.attributesFile=$emptyConfig" archive --format=zip "--output=$archive" "${revision}^{tree}"
        if ($LASTEXITCODE -ne 0) { throw 'Could not export the requested revision' }
        Expand-Archive -LiteralPath $archive -DestinationPath $snapshot -ErrorAction Stop
        return (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    } finally {
        foreach ($name in @('GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_GLOBAL', 'GIT_ATTR_NOSYSTEM',
                            'GIT_NO_REPLACE_OBJECTS', 'GIT_TERMINAL_PROMPT')) {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        }
        foreach ($variable in $savedGitEnvironment) { Set-Item -LiteralPath "Env:$($variable.Name)" -Value $variable.Value }
        if (Test-Path -LiteralPath $repository) { Remove-Item -LiteralPath $repository -Recurse -Force }
        if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
        Remove-Item -LiteralPath $emptyConfig -Force
    }
}

if ($UploadUri.Scheme -ne "https") { throw "Artifact upload requires HTTPS" }
if ($UploadUri.UserInfo) { throw "Artifact upload URL must not contain credentials" }
$source = (Resolve-Path -LiteralPath $SourceDirectory).Path
if ((Get-Item -LiteralPath $source).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "Source directory must not be a reparse point"
}
if (-not (Test-Path -LiteralPath $CredentialFile -PathType Leaf)) {
    throw "Create an upload credential with Save-BasaltwaterToken.ps1"
}

$null = Resolve-JobPath $source $Script
$null = Resolve-JobPath $source $Artifact
$jobDirectory = Join-Path $env:LOCALAPPDATA ("Basaltwater\jobs\" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $jobDirectory -Force | Out-Null
$snapshot = Join-Path $jobDirectory 'source'
try {
    $sourceHash = New-JobSnapshot $source $jobDirectory $Revision $Script
    $scriptPath = Resolve-JobPath $snapshot $Script
    $artifactPath = Resolve-JobPath $snapshot $Artifact
    if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) { throw 'Job script is missing from the exported revision' }
    if (Test-Path -LiteralPath $artifactPath) { throw 'Artifact path must not exist in the exported revision' }
} catch {
    Remove-Item -LiteralPath $jobDirectory -Recurse -Force
    throw
}
$stdout = Join-Path $jobDirectory "stdout.log"
$stderr = Join-Path $jobDirectory "stderr.log"
$receipt = Join-Path $jobDirectory "receipt.json"
$result = [ordered]@{ Revision = $Revision; Script = $Script; Artifact = $Artifact;
    SourceMode = 'git-tree-export'; SourceArchiveSha256 = $sourceHash;
    StartedUtc = [DateTime]::UtcNow.ToString('o'); CompletedUtc = $null;
    State = 'running'; Sha256 = $null }
$process = $null
$stdoutStream = $null
$stderrStream = $null
$processJob = $null
$startGate = $null
$cleanupConfirmed = $false
$cleanupFailure = $null
$processStarted = $false
try {
    . (Join-Path $PSScriptRoot 'GitHubRunner.Common.ps1')
    Import-RunnerJobType
    # The child must not execute user code before containment is established.
    $gateName = 'Local\BasaltwaterJob-' + [guid]::NewGuid().ToString('N')
    $startGate = New-Object Threading.EventWaitHandle($false, [Threading.EventResetMode]::ManualReset, $gateName)
    $literal = $scriptPath.Replace("'", "''")
    $code = "`$ErrorActionPreference = 'Stop'; " +
        "`$gate = [Threading.EventWaitHandle]::OpenExisting('$gateName'); " +
        "if (-not `$gate.WaitOne(30000)) { exit 1 }; `$gate.Dispose(); " +
        "`$global:LASTEXITCODE = 0; & '$literal'; exit `$LASTEXITCODE"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $startInfo.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' + $encoded
    $startInfo.WorkingDirectory = $snapshot
    $startInfo.UseShellExecute = $false
    $startInfo.EnvironmentVariables.Remove('PSModulePath')
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    $stdoutStream = [IO.File]::Open($stdout, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
    $stderrStream = [IO.File]::Open($stderr, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
    if (-not $process.Start()) { throw "Could not start native job" }
    $processStarted = $true
    $processJob = New-Object Basaltwater.ProcessJob($process.Handle)
    $startGate.Set() | Out-Null
    $stdoutCopy = $process.StandardOutput.BaseStream.CopyToAsync($stdoutStream)
    $stderrCopy = $process.StandardError.BaseStream.CopyToAsync($stderrStream)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while (-not $process.WaitForExit(1000)) {
        if ([DateTime]::UtcNow -ge $deadline) {
            $result.State = 'timed-out'
            throw "Native job exceeded $TimeoutSeconds seconds"
        }
        $logBytes = 0
        foreach ($log in @($stdout, $stderr)) {
            if (Test-Path -LiteralPath $log) { $logBytes += (Get-Item -LiteralPath $log).Length }
        }
        if ($logBytes -gt 50MB) {
            throw "Native job exceeded 50 MiB of logs"
        }
    }
    # Complete redirected output before checking its final size and process status.
    $process.WaitForExit()
    # A completed entry point cannot leave background workers modifying its snapshot.
    try { $processJob.TerminateAndWait(5000); $cleanupConfirmed = $true }
    catch { $cleanupFailure = $_; throw }
    if (-not $stdoutCopy.Wait(30000) -or -not $stderrCopy.Wait(30000)) {
        throw "Native job output streams did not close"
    }
    $stdoutStream.Flush()
    $stderrStream.Flush()
    $logBytes = 0
    foreach ($log in @($stdout, $stderr)) {
        if (Test-Path -LiteralPath $log) { $logBytes += (Get-Item -LiteralPath $log).Length }
    }
    if ($logBytes -gt 50MB) { throw "Native job exceeded 50 MiB of logs" }
    if ($process.ExitCode -ne 0) {
        $result.State = 'failed'
        throw "Native job exited with code $($process.ExitCode); logs: $jobDirectory"
    }
    # A job can create junctions after the initial preflight; recheck ancestors.
    $artifactPath = Resolve-JobPath $snapshot $Artifact
    if (-not (Test-Path -LiteralPath $artifactPath -PathType Leaf)) { throw "Job produced no artifact" }
    $artifactItem = Get-Item -LiteralPath $artifactPath -Force
    if ($artifactItem.Attributes -band [IO.FileAttributes]::ReparsePoint -or $artifactItem.Length -gt 1GB) {
        throw "Artifact is a reparse point or exceeds 1 GiB"
    }
    $hash = (Get-FileHash -LiteralPath $artifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $saved = (Get-Content -LiteralPath $CredentialFile -Raw).Trim()
    $secure = ConvertTo-SecureString $saved
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        $headers = @{ Authorization = "Bearer $token"; 'X-Artifact-SHA256' = $hash }
        $response = Invoke-WebRequest -UseBasicParsing -Method Put -Uri $UploadUri -InFile $artifactPath `
            -ContentType 'application/octet-stream' -Headers $headers -TimeoutSec 300 -MaximumRedirection 0
        if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
            throw "Upload destination returned a non-success status"
        }
        if ($response.Headers['X-Artifact-SHA256'] -ne $hash) {
            throw "Upload destination did not confirm the artifact SHA-256 digest"
        }
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        $token = $null
        $headers = $null
    }
    $result.State = 'uploaded'
    $result.Sha256 = $hash
    Write-Host "Uploaded $Artifact (SHA-256 $hash); logs: $jobDirectory"
} catch {
    if ($result.State -eq 'running') { $result.State = 'failed' }
    throw
} finally {
    try {
        if (-not $cleanupFailure -and -not $cleanupConfirmed) {
            if ($processJob) { $processJob.TerminateAndWait(5000) }
            elseif ($processStarted) {
                # Containment failed before the gate opened: only the blocked child exists.
                if (-not $process.HasExited) { $process.Kill() }
                if (-not $process.WaitForExit(5000)) { throw 'Blocked job process did not exit' }
            }
            $cleanupConfirmed = $true
        }
    } catch { $cleanupFailure = $_ }
    if ($processJob) { $processJob.Dispose() }
    if ($startGate) { $startGate.Dispose() }
    if ($stdoutStream) { $stdoutStream.Dispose() }
    if ($stderrStream) { $stderrStream.Dispose() }
    if ($process) { $process.Dispose() }
    if ($cleanupFailure) { $result.State = 'cleanup-uncertain' }
    else { $result.CompletedUtc = [DateTime]::UtcNow.ToString('o') }
    $result | ConvertTo-Json | Set-Content -LiteralPath $receipt -Encoding UTF8
    if ($cleanupFailure) { throw "Native job process cleanup could not be confirmed; inspect owned processes and logs: $jobDirectory" }
}
