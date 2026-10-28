<# Small native Windows CLI, named separately so it cannot shadow basaltw.cmd. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Cli.Common.ps1')
try {
    $code = Invoke-NativeCli -Arguments $args
    exit $code
} catch {
    [Console]::Error.WriteLine('basaltw: ' + $_.Exception.Message)
    exit 1
}
