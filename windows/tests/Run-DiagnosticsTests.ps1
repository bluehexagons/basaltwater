<# Offline diagnostic contracts; all process queries are mocked, files temporary. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '..\Development.Common.ps1')
. (Join-Path $PSScriptRoot '..\Development.Diagnostics.ps1')
function Assert-True([bool]$Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Assert-Throws([scriptblock]$Action,[string]$Pattern) {
    $caught = $null
    try { & $Action | Out-Null } catch { $caught = $_ }
    Assert-True ($null -ne $caught -and $caught.Exception.Message -match $Pattern) "Expected $Pattern; got $caught"
}
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-diagnostics-' + [guid]::NewGuid().ToString('N'))
$savedLocal = $env:LOCALAPPDATA
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $env:LOCALAPPDATA = $temporary
    $id = 'a' * 32; $directory = Get-DevelopmentDirectory $id
    Initialize-DevelopmentDirectory $directory
    $record = [pscustomobject]@{ Id = $id; State = 'running'; CompletedUtc = $null; JobName = ('Local\BasaltwaterDevelopment-' + ('b' * 32) + '-job') }
    function Get-DevelopmentStatus($Id) { return $record }
    $script:DiagnosticCalls = 0
    function Invoke-DevelopmentDiagnosticsWorker($Record,$Target) {
        $script:DiagnosticCalls++
        return [pscustomobject]@{ ProcessId = $Target.ProcessId; WorkingSetBytes = 1024; ThreadCount = 1 }
    }
    $targetPath = Join-Path $directory 'target.json'
    $target = @{ ProcessId = 42; CreatedFileTimeUtc = [DateTime]::UtcNow.ToFileTimeUtc().ToString(); State = 'created'; ExitCode = $null; Private = 'omit-this' }
    Write-DevelopmentJson $targetPath $target
    $parsed = Read-DevelopmentTarget $directory
    Assert-True ($parsed.ProcessId -eq 42 -and -not $parsed.PSObject.Properties['Private'] -and $null -eq $parsed.ExitCodeHex) 'Identity parsing retained unrelated fields or invented exit evidence'
    $live = Get-DevelopmentDiagnostics $id
    Assert-True ($live.ok -and $live.State -eq 'live' -and $script:DiagnosticCalls -eq 1 -and
        (Test-Path -LiteralPath $live.SnapshotPath)) 'Live diagnostic did not persist a bounded snapshot'
    $target.State = 'exited'; $target.ExitCode = 3221225477
    Write-DevelopmentJson $targetPath $target
    $record.CompletedUtc = 'settled'; $record.State = 'failed'
    $settled = Get-DevelopmentDiagnostics $id
    Assert-True ($settled.ok -and $settled.State -eq 'settled' -and $null -eq $settled.Resources -and
        $settled.Target.ExitCodeHex -eq '0xC0000005' -and $script:DiagnosticCalls -eq 1) 'Terminal task queried a reused PID or lost unsigned native exit code'
    $record.CompletedUtc = $null; $record.State = 'cleanup-uncertain'
    Assert-True (-not (Get-DevelopmentDiagnostics $id).ok -and $script:DiagnosticCalls -eq 1) 'Uncertain task queried a process'
    $record.State = 'running'
    function Invoke-DevelopmentDiagnosticsWorker { throw 'fixture timed out' }
    $failed = Get-DevelopmentDiagnostics $id
    Assert-True (-not $failed.ok -and $failed.Error -match 'timed out' -and (Test-Path -LiteralPath $failed.SnapshotPath)) 'Failed probe lost evidence or claimed success'
    foreach ($invalid in @(
        @{ ProcessId = -1; CreatedFileTimeUtc = '1'; State = 'created'; ExitCode = $null },
        @{ ProcessId = 42; CreatedFileTimeUtc = 'bad'; State = 'created'; ExitCode = $null },
        @{ ProcessId = 42; CreatedFileTimeUtc = '1'; State = 'exited'; ExitCode = -1 },
        @{ ProcessId = 42; CreatedFileTimeUtc = '1'; State = 'created'; ExitCode = 23 }
    )) {
        Write-DevelopmentJson $targetPath $invalid
        Assert-Throws { Read-DevelopmentTarget $directory } 'Invalid native target'
    }
    [IO.File]::WriteAllText($targetPath, ('x' * 4097))
    Assert-Throws { Read-DevelopmentTarget $directory } 'size limit'
    Remove-Item -LiteralPath $targetPath
    $record.CompletedUtc = 'settled'
    Assert-True ($null -eq (Get-DevelopmentDiagnostics $id).Target) 'Legacy receipt invented a native identity'
    Assert-True (-not (Get-DevelopmentDiagnostics $id -Tree).ok) 'Legacy child selection was not explicitly unsupported'
    $record | Add-Member NoteProperty TrackChildren $true
    $member = [pscustomobject]@{ ProcessId = 42; CreatedFileTimeUtc = '1'; Token = '42:1'; ParentToken = $null;
        Role = 'child'; State = 'exited'; ExitCode = 7; ExitCodeHex = '0x00000007' }
    $catalog = @{ Version = 1; Coverage = 'partial: fixture'; Overflow = $false; Error = $null; Members = @($member) }
    $membersPath = Join-Path $directory 'members.json'
    Write-DevelopmentJson $membersPath $catalog
    function Invoke-DevelopmentMemberWorker { throw 'Exited identity must never invoke a worker' }
    $retained = Get-DevelopmentDiagnostics $id -Member '42:1'
    Assert-True ($retained.ok -and $retained.Members[0].ExitCode -eq 7 -and $retained.Samples.Count -eq 0) 'Retained exit selected a PID'
    Assert-True (-not (Get-DevelopmentDiagnostics $id -Member '42:2').ok) 'Stale token accepted'
    Assert-True (-not (Get-DevelopmentDiagnostics $id -Member '43:1').ok) 'Foreign token accepted'
    Assert-Throws { Get-DevelopmentDiagnostics $id -Member '42' } 'PID:FILETIME'
    $member.ExitCode = -1; Write-DevelopmentJson $membersPath $catalog
    Assert-Throws { Read-DevelopmentMembers $directory } 'Invalid retained member exit'
    $member.ExitCode = 7; $member.Token = '42:2'; Write-DevelopmentJson $membersPath $catalog
    Assert-Throws { Read-DevelopmentMembers $directory } 'Invalid retained member identity'
    $member.Token = '42:1'; $member.ParentToken = '99:1'; Write-DevelopmentJson $membersPath $catalog
    Assert-Throws { Read-DevelopmentMembers $directory } 'Invalid retained parent'
    $member.ParentToken = $null
    $catalog.Members = @(1..40 | ForEach-Object {
        [pscustomobject]@{ ProcessId = $_; CreatedFileTimeUtc = '1'; Token = "${_}:1"; ParentToken = $null;
            Role = 'child'; State = 'observed'; ExitCode = $null; ExitCodeHex = $null }
    })
    $catalog.Overflow = $true; $catalog.Error = 'fixture partial discovery'
    Write-DevelopmentJson $membersPath $catalog
    $bounded = Get-DevelopmentDiagnostics $id -Tree
    Assert-True ($bounded.ok -and $bounded.Members.Count -eq 32 -and $bounded.OmittedMembers -eq 8 -and $bounded.Overflow -and
        $bounded.UnobservedExits -eq 32 -and $bounded.CatalogError -eq 'fixture partial discovery') 'Tree failed to bound samples or invented terminal exits'
    $record.CompletedUtc = $null; $record.State = 'running'
    function Invoke-DevelopmentMemberWorker($Record,$Members) {
        return @{ samples = @($Members | ForEach-Object { @{ Token = $_.Token; Resources = $null; Error = 'fixture unavailable' } }) }
    }
    $partial = Get-DevelopmentDiagnostics $id -Tree
    Assert-True (-not $partial.ok -and $partial.Samples.Count -eq 32 -and $partial.CatalogError -eq 'fixture partial discovery') 'Partial counter errors or discovery error lost'
    . (Join-Path $PSScriptRoot '..\Development.Children.ps1')
    $tracker = [pscustomobject]@{ Members = @($member); Error = $null; Overflow = $false }
    $tracker | Add-Member ScriptMethod Poll { param($Job,$Root,$Created,$Enumerate); return $false }
    Remove-Item -LiteralPath $membersPath
    Update-DevelopmentChildren $tracker $record $directory
    Assert-True (-not (Test-Path -LiteralPath $membersPath)) 'Unchanged catalog was republished'
    $diagnostics = Join-Path $directory 'diagnostics'
    1..32 | ForEach-Object { [IO.File]::WriteAllText((Join-Path $diagnostics ("fixture-$_.json")), '{}') }
    Assert-Throws { Get-DevelopmentDiagnostics $id } '32 snapshots'
    Assert-Throws { Invoke-NativeDevelopmentCli @('diagnose',$id,'--pid','42') } 'Usage: develop diagnose'
    Add-Type -Path (Join-Path $PSScriptRoot '..\Diagnostics.Native.cs') -ErrorAction Stop
    Add-Type -Path (Join-Path $PSScriptRoot '..\Development.Children.cs') -ErrorAction Stop
    Write-Host 'Development diagnostics offline contracts passed.'
} finally {
    $env:LOCALAPPDATA = $savedLocal
    $resolved = [IO.Path]::GetFullPath($temporary)
    if (-not $resolved.StartsWith([IO.Path]::GetTempPath(),[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe diagnostic fixture cleanup' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
