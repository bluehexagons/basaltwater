<# Concurrent temporary-file contracts; no Scheduler, desktop or account mutation. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '..\Development.Common.ps1')
$directory = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-state-' + [guid]::NewGuid().ToString('N'))
$worker = $null; $pending = $null
New-Item -ItemType Directory -Path $directory | Out-Null
try {
    $path = Join-Path $directory 'receipt.json'
    $ready = Join-Path $directory 'reader-ready'
    $stop = Join-Path $directory 'reader-stop'
    Write-DevelopmentJson $path @{ Version = 0 }
    $worker = [PowerShell]::Create()
    $code = @'
param($source,$path,$ready,$stop)
$ErrorActionPreference = 'Stop'
. $source
$reads = 0; $failures = @()
while (-not [IO.File]::Exists($stop)) {
    try {
        $value = Read-DevelopmentJson $path
        if ($null -eq $value.Version) { throw 'Incomplete receipt' }
        $reads++
    } catch { $failures += $_.Exception.Message }
    [IO.File]::WriteAllText($ready, 'ready')
}
[pscustomobject]@{ Reads=$reads; Failures=$failures }
'@
    $null = $worker.AddScript($code).AddArgument((Join-Path $PSScriptRoot '..\Development.Common.ps1')).AddArgument($path).AddArgument($ready).AddArgument($stop)
    $pending = $worker.BeginInvoke()
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while (-not [IO.File]::Exists($ready)) {
        if ($clock.Elapsed.TotalSeconds -gt 5) { throw 'Concurrent receipt reader did not start' }
        Start-Sleep -Milliseconds 10
    }
    foreach ($version in 1..100) { Write-DevelopmentJson $path @{ Version = $version } }
    [IO.File]::WriteAllText($stop, 'stop')
    if (-not $pending.AsyncWaitHandle.WaitOne(5000)) { throw 'Concurrent reader did not stop' }
    $result = @($worker.EndInvoke($pending))[0]
    $pending = $null
    if ($result.Reads -lt 1 -or $result.Failures.Count -ne 0) { throw "Concurrent receipt reads failed: $($result.Failures -join '; ')" }
    if ((Read-DevelopmentJson $path).Version -ne 100) { throw 'Final receipt update lost' }
    Write-Host 'Development state publication tests passed.'
} finally {
    if ($worker) { $worker.Stop(); $worker.Dispose() }
    $resolved = [IO.Path]::GetFullPath($directory)
    $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe state-test cleanup' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
