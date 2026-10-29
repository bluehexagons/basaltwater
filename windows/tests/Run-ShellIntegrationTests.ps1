<# All shell launches, Terminal installation, shortcuts and notifications are mocked. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
$windows = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $windows 'AgentWorkstation.Common.ps1')
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-integration-' + [guid]::NewGuid().ToString('N'))
$savedLocal = $env:LOCALAPPDATA; $savedRoaming = $env:APPDATA; $savedHome = $env:USERPROFILE
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $env:LOCALAPPDATA = Join-Path $temporary 'local'; $env:APPDATA = Join-Path $temporary 'roaming'; $env:USERPROFILE = $temporary
    function Get-NativePowerShell7 { return 'C:\PowerShell 7\pwsh.exe' }
    function Get-NativeCommand($Name) { return 'C:\WindowsTerminal\wt.exe' }
    function Ensure-WingetPackage { throw 'Fixture attempted package installation' }
    function Install-NativeShellShortcut($Path, $Shell, $Entry, $Directory) {
        $script:Shortcut = @{ Path = $Path; Shell = $Shell; Entry = $Entry; Directory = $Directory }
    }
    function Start-Process($FilePath, $ArgumentList) { $script:Launch = @{ File = $FilePath; Arguments = $ArgumentList } }
    $script:Shortcut = $null; $script:Launch = $null
    Install-NativeShellIntegration -Source (Split-Path -Parent $windows) -Plan
    Assert-True (-not (Test-Path -LiteralPath $env:LOCALAPPDATA) -and $null -eq $script:Shortcut) 'Integration preview changed account files'
    Install-NativeShellIntegration -Source (Split-Path -Parent $windows)
    $fragment = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows Terminal\Fragments\Basaltwater\profiles.json'
    $record = Get-Content -LiteralPath $fragment -Raw | ConvertFrom-Json
    Assert-True ($record.profiles[0].commandline -match 'PowerShell 7' -and $record.profiles[0].name -eq 'Basaltwater' -and
        $script:Shortcut.Directory -eq $temporary) 'Terminal/Start Menu integration did not select the prepared shell'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'Microsoft\Windows Terminal\settings.json'))) 'Integration rewrote personal Terminal settings'
    $project = Join-Path $temporary "project & quote's"
    New-Item -ItemType Directory -Path $project | Out-Null
    $artifact = Join-Path $project ('artifact ' + [char]0x03bb + '.zip')
    Set-Content -LiteralPath $artifact -Value 'fixture'
    Open-NativeDesktopItem $artifact -Reveal | Out-Null
    Assert-True ($script:Launch.Arguments -eq ('/select,"' + $artifact + '"')) 'Explorer handoff changed the literal artifact path'
    Open-NativeDevelopmentTerminal (Split-Path -Parent $windows) $project -Msvc | Out-Null
    $encoded = ($script:Launch.Arguments -split ' ')[-1]
    $command = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($encoded))
    Assert-True ($command.Contains("quote''s") -and $command.Contains('-Msvc:$true')) 'Terminal handoff lost project quoting or MSVC selection'
    $failed = $false
    try { Get-NativeLocalItem 'https://example.com' | Out-Null } catch { $failed = $true }
    Assert-True $failed 'Artifact handoff accepted a non-file URL'
    function Enter-NativeDesktopLock { throw 'Invalid handoff reached desktop control' }
    $failed = $false
    try { Invoke-NativeDesktopHandoff (Split-Path -Parent $windows) @('open','--reveal') | Out-Null }
    catch { $failed = $_.Exception.Message -match 'Usage: basaltw desktop open' }
    Assert-True $failed 'Invalid handoff mutated desktop state before validation'
    Write-Host 'Native shell integration tests passed.'
} finally {
    $env:LOCALAPPDATA = $savedLocal; $env:APPDATA = $savedRoaming; $env:USERPROFILE = $savedHome
    $resolved = [IO.Path]::GetFullPath($temporary)
    if (-not $resolved.StartsWith([IO.Path]::GetTempPath(), [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test cleanup' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
