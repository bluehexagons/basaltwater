<# Opt-in test sessions share development admission, containment and receipts.
   No application code, manifests or recipes are discovered/executed here. #>
function Test-DevelopmentSession($Record) {
    return $Record.PSObject.Properties['TestSession'] -and $null -ne $Record.TestSession
}
function Assert-DevelopmentProjectAdmission([string]$Project, [bool]$Session) {
    # Caller holds setup.lock through receipt publication. Receipts reserve the
    # project until cleanup is confirmed, including queued/uncertain workers.
    Assert-DevelopmentPath $Project
    $root = Get-DevelopmentRoot
    if (-not (Test-Path -LiteralPath $root)) { return }
    foreach ($directory in Get-ChildItem -LiteralPath $root -Directory) {
        $record = Get-DevelopmentRecord $directory.Name
        if ($record.CompletedUtc) { continue }
        $otherSession = Test-DevelopmentSession $record
        if (-not $Session -and -not $otherSession) { continue }
        if (-not $record.PSObject.Properties['Project'] -or -not $record.Project) {
            throw "Active legacy task $($record.Id) has unknown project scope; settle it before a test session."
        }
        if ([IO.Path]::GetFullPath($record.Project).TrimEnd('\') -ieq [IO.Path]::GetFullPath($Project).TrimEnd('\')) {
            throw "Project reserved by task $($record.Id); inspect status/logs/cancel and confirm cleanup before retrying."
        }
    }
}
function New-DevelopmentSession([string]$Id, [int]$ReadyTimeoutSeconds) {
    $directory = Get-DevelopmentDirectory $Id
    return [pscustomobject]@{
        ProfilePath = (Join-Path $directory 'profile'); EvidencePath = $directory;
        ReadyFile = (Join-Path $directory 'profile\ready.json'); ReadyToken = [guid]::NewGuid().ToString('N');
        ReadyTimeoutSeconds = $ReadyTimeoutSeconds; ReadyState = 'pending'; ReadyUtc = $null;
        ReadyElapsedSeconds = $null; ProfileCleanup = 'pending'
    }
}
function Get-DevelopmentFileVersion([string]$Path) {
    try {
        Assert-DevelopmentPath $Path
        return [Diagnostics.FileVersionInfo]::GetVersionInfo($Path).FileVersion
    }
    catch { return $null }
}
function Initialize-DevelopmentSession($Record, $Request) {
    $session = $Record.TestSession
    Initialize-DevelopmentDirectory $session.ProfilePath
    Initialize-DevelopmentDirectory (Join-Path $session.EvidencePath 'artifacts')
    $Request.Environment['BASALTWATER_TEST_PROFILE'] = $session.ProfilePath
    $Request.Environment['BASALTWATER_TEST_EVIDENCE'] = Join-Path $session.EvidencePath 'artifacts'
    $Request.Environment['BASALTWATER_TEST_READY_FILE'] = $session.ReadyFile
    $Request.Environment['BASALTWATER_TEST_READY_TOKEN'] = $session.ReadyToken
    $git = Get-DevelopmentTrustedGit
    $evidence = [pscustomobject]@{
        SchemaVersion = 1; Id = $Record.Id; CapturedUtc = [DateTime]::UtcNow.ToString('o');
        Project = $Request.Project; Checkout = (Get-DevelopmentCheckout $Request.Project $git);
        WorkerSource = $PSScriptRoot; WorkerCheckout = (Get-DevelopmentCheckout (Split-Path -Parent $PSScriptRoot) $git);
        Executable = $Request.Executable; PowerShell = $Request.PowerShell; Node = $Request.Node; Msvc = $Request.Msvc;
        ExecutableFileVersion = (Get-DevelopmentFileVersion $Request.Executable);
        PowerShellFileVersion = (Get-DevelopmentFileVersion $Request.PowerShell);
        WindowsVersion = [Environment]::OSVersion.VersionString;
        Graphics = $null; Display = $null; HardwareMetadata = 'Not collected by this session version';
        Receipt = 'receipt.json'; Stdout = 'stdout.log'; Stderr = 'stderr.log'; Artifacts = 'artifacts';
        Isolation = 'Project must honor BASALTWATER_TEST_PROFILE; this is not an OS sandbox';
        ArgumentCapture = 'Arguments and environment are omitted to avoid retaining credentials'
    }
    Write-DevelopmentJson (Join-Path $session.EvidencePath 'session-evidence.json') $evidence
}
function Update-DevelopmentSessionReadiness($Record, [double]$ElapsedSeconds) {
    if (-not (Test-DevelopmentSession $Record) -or $Record.TestSession.ReadyState -ne 'pending') { return }
    $session = $Record.TestSession
    # Deadline wins over a marker observed after the allowed interval.
    if ($ElapsedSeconds -ge $session.ReadyTimeoutSeconds) { $session.ReadyState = 'timed-out'; throw 'Test session readiness deadline exceeded' }
    Assert-DevelopmentPath $session.ReadyFile
    if (-not (Test-Path -LiteralPath $session.ReadyFile)) { return }
    if (-not (Test-Path -LiteralPath $session.ReadyFile -PathType Leaf) -or (Get-Item -LiteralPath $session.ReadyFile).Length -gt 1024) {
        throw 'Test session readiness marker must be a JSON file of at most 1024 bytes'
    }
    $marker = Read-DevelopmentJson $session.ReadyFile
    if (-not $marker.PSObject.Properties['Token'] -or $marker.Token -isnot [string] -or $marker.Token -cne $session.ReadyToken) { throw 'Test session readiness token mismatch' }
    $session.ReadyState = 'ready'; $session.ReadyUtc = [DateTime]::UtcNow.ToString('o'); $session.ReadyElapsedSeconds = $ElapsedSeconds
    Write-DevelopmentJson (Join-Path (Get-DevelopmentDirectory $Record.Id) 'receipt.json') $Record
}
function Remove-DevelopmentSessionProfile($Record) {
    if (-not (Test-DevelopmentSession $Record)) { return }
    $expected = Join-Path (Get-DevelopmentDirectory $Record.Id) 'profile'
    if ($Record.TestSession.ProfilePath -cne $expected) { throw 'Test session profile identity mismatch' }
    Assert-DevelopmentPath $expected
    if (Test-Path -LiteralPath $expected) {
        # Verify a bounded tree before removing anything. Do not traverse or
        # delete through project-created junctions/symlinks, even at cleanup.
        $pending = [Collections.Generic.Stack[string]]::new(); $pending.Push($expected)
        $entries = [Collections.Generic.List[string]]::new()
        while ($pending.Count) {
            $path = $pending.Pop(); Assert-DevelopmentPath $path
            $entries.Add($path)
            if ($entries.Count -gt 10000) { throw 'Test profile exceeds automatic cleanup limit; inspect retained profile' }
            $item = Get-Item -LiteralPath $path -Force
            if ($item.PSIsContainer) {
                foreach ($child in Get-ChildItem -LiteralPath $path -Force) {
                    if ($entries.Count + $pending.Count -ge 10000) { throw 'Test profile exceeds automatic cleanup limit; inspect retained profile' }
                    $pending.Push($child.FullName)
                }
            }
        }
        for ($i = $entries.Count - 1; $i -ge 0; $i--) {
            Assert-DevelopmentPath $entries[$i]
            $item = Get-Item -LiteralPath $entries[$i] -Force
            if ($item.PSIsContainer) { [IO.Directory]::Delete($entries[$i], $false) }
            else { [IO.File]::Delete($entries[$i]) }
        }
    }
    $Record.TestSession.ProfileCleanup = 'removed'
    if ($Record.TestSession.ReadyState -eq 'pending') { $Record.TestSession.ReadyState = 'not-ready' }
}
