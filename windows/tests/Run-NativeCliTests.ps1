<# Native argv transport through the installed launcher, using a disposable CLI. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '..\AgentWorkstation.Common.ps1')
. (Join-Path $PSScriptRoot '..\Cli.Common.ps1')
function Assert-True([bool]$Value, [string]$Message) { if (-not $Value) { throw $Message } }
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-native-cli-' + [guid]::NewGuid().ToString('N'))
$savedLocal = $env:LOCALAPPDATA
$savedPath = $env:PATH
$running = $null
try {
    $env:LOCALAPPDATA = Join-Path $temporary "account 'lambda λ'"
    function Add-NativeUserPath($Directory) { $env:PATH = $Directory + ';' + $env:PATH }
    Install-NativeCli $repo
    $bin = Join-Path $env:LOCALAPPDATA 'Basaltwater\bin'
    $launcher = Join-Path $bin 'basaltw.exe'
    $entry = Join-Path $bin 'Invoke-Basaltw.ps1'
    $originalHash = (Get-FileHash -LiteralPath $launcher).Hash
    & {
        function Build-NativeCli { throw 'fixture compilation failure' }
        $failed = $false
        try { Install-NativeCli $repo } catch { $failed = $_.Exception.Message -eq 'fixture compilation failure' }
        Assert-True ($failed -and (Get-FileHash -LiteralPath $launcher).Hash -eq $originalHash) 'Compilation failure replaced the working launcher'
    }
    $planProbe = Join-Path $temporary 'plan-probe.ps1'
    Set-Content -LiteralPath $planProbe -Encoding UTF8 -Value @'
param($Launcher, $Project)
$literal = @('quote"here', '', '%PATH%', '--dry-run')
$cli = @('desktop','develop','exec','--project',$Project,'--dry-run','--',(Join-Path $PSHOME 'pwsh.exe')) + $literal
$result = & $Launcher @cli | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $result.Plan -or $result.Argv.Count -ne $literal.Count) { throw 'Native task planning failed' }
for ($i=0; $i -lt $literal.Count; $i++) { if ($result.Argv[$i] -cne $literal[$i]) { throw "Planned argument $i changed" } }
'@
    $shell = Get-NativePowerShell7
    & $shell -NoProfile -ExecutionPolicy Bypass -File $planProbe -Launcher $launcher -Project $temporary
    Assert-True ($LASTEXITCODE -eq 0 -and -not (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'Basaltwater\development'))) 'Native dry-run created state or changed argv'
    Set-Content -LiteralPath $entry -Encoding UTF8 -Value @'
@{ Arguments = @($args) } | ConvertTo-Json -Depth 4 -Compress
exit 23
'@
    # PS7's native .exe argv mode preserves quotes/empty values before Main().
    # The PowerShell 5.1 bootstrap must then preserve them into the script.
    $probe = Join-Path $temporary 'probe.ps1'
    Set-Content -LiteralPath $probe -Encoding UTF8 -Value @'
param($Launcher)
$expected = @('two words', '', 'quote"here', 'trailing\', '%PATH%', '$literal & ;', "line`nbreak", 'lambda λ', '--', '--dry-run')
$result = & $Launcher @expected | ConvertFrom-Json
if ($LASTEXITCODE -ne 23 -or $result.Arguments.Count -ne $expected.Count) { throw 'Launcher lost exit status or argument count' }
for ($i=0; $i -lt $expected.Count; $i++) { if ($result.Arguments[$i] -cne $expected[$i]) { throw "Argument $i changed" } }
'@
    & $shell -NoProfile -ExecutionPolicy Bypass -File $probe -Launcher $launcher
    Assert-True ($LASTEXITCODE -eq 0) 'Native launcher argument roundtrip failed'
    Assert-True (@(Get-Command basaltw -CommandType Application)[0].Source -eq $launcher) 'Native launcher did not take precedence over batch fallback'
    # An active native launcher can be atomically replaced by setup/refresh.
    $ready = Join-Path $temporary 'ready'
    $release = Join-Path $temporary 'release'
    Set-Content -LiteralPath $entry -Encoding UTF8 -Value @'
[IO.File]::WriteAllText($args[0], 'ready')
$deadline = [DateTime]::UtcNow.AddSeconds(20)
while (-not [IO.File]::Exists($args[1]) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 50 }
'@
    $info = [Diagnostics.ProcessStartInfo]::new($launcher)
    $info.Arguments = '"' + $ready + '" "' + $release + '"'
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $running = [Diagnostics.Process]::Start($info)
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (-not (Test-Path -LiteralPath $ready) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 50 }
    Assert-True ((Test-Path -LiteralPath $ready) -and -not $running.HasExited) 'Replacement fixture did not acknowledge'
    Install-NativeCli $repo
    [IO.File]::WriteAllText($release, 'release')
    Assert-True ($running.WaitForExit(10000)) 'Replaced launcher did not settle'
    $versionInfo = [Diagnostics.ProcessStartInfo]::new($launcher, '--version')
    $versionInfo.UseShellExecute = $false; $versionInfo.CreateNoWindow = $true
    $versionInfo.RedirectStandardOutput = $true; $versionInfo.RedirectStandardError = $true
    $versionProcess = [Diagnostics.Process]::Start($versionInfo)
    try {
        $output = $versionProcess.StandardOutput.ReadToEndAsync()
        $errors = $versionProcess.StandardError.ReadToEndAsync()
        Assert-True ($versionProcess.WaitForExit(10000) -and $output.Wait(1000) -and $errors.Wait(1000)) 'Version output did not settle'
        Assert-True ($versionProcess.ExitCode -eq 0 -and $output.Result -match 'native Windows' -and -not $errors.Result) "Replacement launcher lost text output or emitted CLIXML (exit $($versionProcess.ExitCode), stdout $($output.Result.Length) chars, stderr $($errors.Result.Length) chars)"
    } finally { $versionProcess.Dispose() }
    Write-Host 'Native CLI argv and active replacement tests passed.'
} finally {
    if ($running) {
        if (-not $running.HasExited) { [IO.File]::WriteAllText((Join-Path $temporary 'release'), 'release'); $null = $running.WaitForExit(25000) }
        $running.Dispose()
    }
    $env:LOCALAPPDATA = $savedLocal; $env:PATH = $savedPath
    $resolved = [IO.Path]::GetFullPath($temporary)
    $root = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($root,[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe launcher test cleanup' }
    if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
