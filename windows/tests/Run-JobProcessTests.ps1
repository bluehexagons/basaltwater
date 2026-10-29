<# Real process-tree fixtures; only private temporary files and owned children. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-job-process-' + [guid]::NewGuid().ToString('N'))
$savedLocal = $env:LOCALAPPDATA
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $env:LOCALAPPDATA = Join-Path $temporary 'profile'
    $checkout = Join-Path $temporary 'checkout with spaces'
    New-Item -ItemType Directory -Path $checkout | Out-Null
    $fixture = Join-Path $checkout 'worker.ps1'
    Set-Content -LiteralPath $fixture -Encoding UTF8 -Value @'
$child = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList '-NoProfile -NonInteractive -Command "Start-Sleep -Seconds 60"' -WindowStyle Hidden -PassThru
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'child.pid'), [string]$child.Id)
if ($env:BASALTWATER_JOB_TEST_MODE -eq 'timeout') { Start-Sleep -Seconds 60 }
if ($env:BASALTWATER_JOB_TEST_MODE -eq 'fail') { exit 23 }
exit 0
'@
    & git.exe -C $checkout init --quiet
    if ($LASTEXITCODE) { throw 'Fixture git init failed' }
    & git.exe -C $checkout add -- worker.ps1
    if ($LASTEXITCODE) { throw 'Fixture git add failed' }
    & git.exe -C $checkout -c user.name=Fixture -c user.email=fixture@example.invalid commit --quiet -m fixture
    if ($LASTEXITCODE) { throw 'Fixture git commit failed' }
    $revision = (& git.exe -C $checkout rev-parse HEAD).Trim()
    $credential = Join-Path $temporary 'unused-token'
    Set-Content -LiteralPath $credential -Value 'never-read-on-failure'
    $savedMode = $env:BASALTWATER_JOB_TEST_MODE
    try {
        foreach ($mode in @('fail', 'complete', 'timeout')) {
            $env:BASALTWATER_JOB_TEST_MODE = $mode
            $caught = $null
            try {
                & (Join-Path $repo 'windows\Invoke-BasaltwaterJob.ps1') -SourceDirectory $checkout `
                    -Revision $revision -Script worker.ps1 -Artifact missing.txt `
                    -UploadUri https://upload.invalid/artifact -CredentialFile $credential -TimeoutSeconds 5
            } catch { $caught = $_ }
            $expected = switch ($mode) { fail { 'exited with code 23' }; complete { 'produced no artifact' }; timeout { 'exceeded 5 seconds' } }
            if (-not $caught -or $caught.Exception.Message -notmatch $expected) { throw "Expected $expected; received $caught" }
            $pidPath = Join-Path $checkout 'child.pid'
            $childId = [int][IO.File]::ReadAllText($pidPath)
            $worker = Get-Process -Id $childId -ErrorAction SilentlyContinue
            if ($worker) {
                try {
                    if (-not $worker.WaitForExit(5000)) {
                        $worker.Kill(); $worker.WaitForExit()
                        throw "Owned child survived $mode job cleanup"
                    }
                } finally { $worker.Dispose() }
            }
            Remove-Item -LiteralPath $pidPath
        }
    } finally { $env:BASALTWATER_JOB_TEST_MODE = $savedMode }
    Write-Host 'Native job process-tree tests passed.'
} finally {
    $env:LOCALAPPDATA = $savedLocal
    $resolved = [IO.Path]::GetFullPath($temporary)
    $root = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe job test cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
