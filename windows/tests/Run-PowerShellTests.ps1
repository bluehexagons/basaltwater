<# Bootstrap and typed process handoff tests; no package installation. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
$windows = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $windows 'PowerShell.Common.ps1')
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-shell-' + [guid]::NewGuid().ToString('N'))
$savedCapture = $env:BASALTWATER_SHELL_CAPTURE
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    & {
        function Get-NativePowerShell7 { return $script:Available }
        function Ensure-Winget { $script:Winget++ }
        function Ensure-WingetPackage($package, $label) {
            Assert-True ($package -eq 'Microsoft.PowerShell') 'Bootstrap selected an unexpected package'
            if ($script:InstallFails) { throw 'fixture package failure' }
            $script:Available = 'C:\PowerShell\pwsh.exe'
        }
        function Update-NativePath { $script:Paths++ }
        $script:Available = $null; $script:Winget = 0; $script:Paths = 0; $script:InstallFails = $false
        Assert-True ((Ensure-NativePowerShell7) -eq 'C:\PowerShell\pwsh.exe' -and $script:Winget -eq 1) 'Missing PowerShell was not installed and rediscovered'
        Ensure-NativePowerShell7 | Out-Null
        Assert-True ($script:Winget -eq 1 -and $script:Paths -eq 1) 'An available stable shell was unnecessarily installed'
        $script:Available = $null; $script:InstallFails = $true
        $caught = $false
        try { Ensure-NativePowerShell7 | Out-Null } catch { $caught = $_.Exception.Message -match 'package failure' }
        Assert-True $caught 'PowerShell installation failure was hidden'
    }
    & {
        function Test-NativePowerShell7Host { return $false }
        function Get-NativePowerShellCandidates { return @('C:\preview\pwsh.exe', 'C:\stable\pwsh.exe') }
        function Test-NativePowerShellExecutable($Path) { return $Path -like '*\stable\*' }
        Assert-True ((Get-NativePowerShell7) -eq 'C:\stable\pwsh.exe') 'Discovery selected an unverified/preview executable'
    }
    & {
        function Get-NativePowerShell7 { return 'C:\Program Files\WindowsApps\Microsoft.PowerShell_7.fixture\pwsh.exe' }
        function Test-NativePowerShellExecutable($Path) { return $script:AliasAvailable }
        $script:AliasAvailable = $true
        Assert-True ((Get-NativeInteractivePowerShell7) -eq (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\pwsh.exe')) 'Interactive shell retained an expiring MSIX package path'
        $script:AliasAvailable = $false
        Assert-True ((Get-NativeInteractivePowerShell7) -eq (Get-NativePowerShell7)) 'Disabled alias hid a verified executable'
    }
    $fixture = Join-Path $temporary "installer & quote's.ps1"
    $env:BASALTWATER_SHELL_CAPTURE = Join-Path $temporary 'capture.json'
    Set-Content -LiteralPath $fixture -Encoding UTF8 -Value @'
param([switch]$NoReboot, [switch]$Node, [string[]]$AgentTool, [string]$StorageRoot, [int]$FixtureExit = 0)
@{ NoReboot = [bool]$NoReboot; Node = [bool]$Node; AgentTool = $AgentTool; StorageRoot = $StorageRoot } |
    ConvertTo-Json | Set-Content -LiteralPath $env:BASALTWATER_SHELL_CAPTURE -Encoding UTF8
[Console]::Error.WriteLine('fixture warning')
exit $FixtureExit
'@
    # Test the exact argv/serialization boundary with the available host; the
    # production discovery separately requires stable PS7.
    $hostExe = (Get-Process -Id $PID).Path
    $options = @{ NoReboot = [switch]$true; Node = [switch]$false; AgentTool = @('claude', 'opencode');
        StorageRoot = "F:\cache & quote's " + [char]0x03bb + '\' }
    Invoke-NativePowerShell7Installer $hostExe $fixture $options
    $record = Get-Content -LiteralPath $env:BASALTWATER_SHELL_CAPTURE -Raw | ConvertFrom-Json
    Assert-True ($record.NoReboot -and -not $record.Node -and ($record.AgentTool -join ',') -eq 'claude,opencode' -and
        $record.StorageRoot -eq $options.StorageRoot) 'PowerShell handoff changed explicit flags, arrays or literal paths'
    $options.FixtureExit = 19
    $failed = $false
    try { Invoke-NativePowerShell7Installer $hostExe $fixture $options } catch { $failed = $_.Exception.Message -match 'exit code 19' }
    Assert-True $failed 'PowerShell handoff hid child failure'
    $failed = $false
    try { Invoke-NativePowerShell7Installer (Join-Path $temporary 'missing.exe') $fixture $options 6>$null }
    catch { $failed = $_.Exception.Message -match 'missing\.exe|exit code -1' }
    Assert-True $failed 'PowerShell handoff reused a stale successful exit code after launch failed'
    Write-Host 'Native PowerShell bootstrap tests passed.'
} finally {
    $env:BASALTWATER_SHELL_CAPTURE = $savedCapture
    $resolved = [IO.Path]::GetFullPath($temporary)
    if (-not $resolved.StartsWith([IO.Path]::GetTempPath(), [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe temporary cleanup' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
