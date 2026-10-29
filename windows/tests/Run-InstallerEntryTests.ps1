<# Offline downloaded-installer entry tests; child setup operations are fixtures. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-entry-' + [guid]::NewGuid().ToString('N'))
$savedLocal = $env:LOCALAPPDATA; $savedCapture = $env:BASALTWATER_TEST_CAPTURE
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $env:LOCALAPPDATA = $temporary
    $env:BASALTWATER_TEST_CAPTURE = Join-Path $temporary 'parameters.json'
    $text = Get-Content -LiteralPath (Join-Path $repo 'install.ps1') -Raw
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseInput($text, [ref]$tokens, [ref]$errors)
    $relay = $ast.Find({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-InstallerScriptBlock'
    }, $true)
    Invoke-Expression $relay.Extent.Text
    # Use a real child PowerShell with flags, false switches, arrays, Unicode,
    # punctuation and a trailing backslash, without native argv string quoting.
    $fixture = @'
[CmdletBinding()]
param([switch]$Sunshine, [switch]$T3CodeDesktop, [string[]]$AgentTool, [string]$StorageRoot)
@{ Options = $PSBoundParameters; Path = $PSCommandPath } | ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath $env:BASALTWATER_TEST_CAPTURE -Encoding UTF8
[Console]::Error.WriteLine('fixture warning')
'@
    $root = 'F:\cache & ' + [char]0x03bb + "'s\"
    Invoke-InstallerScriptBlock $fixture @{ Sunshine = [switch]$true; T3CodeDesktop = [switch]$false;
        AgentTool = @('claude', 'opencode'); StorageRoot = $root }
    $received = Get-Content -LiteralPath $env:BASALTWATER_TEST_CAPTURE -Raw | ConvertFrom-Json
    Assert-True ($received.Options.Sunshine.IsPresent -and -not $received.Options.T3CodeDesktop.IsPresent -and
        ($received.Options.AgentTool -join ',') -eq 'claude,opencode' -and $received.Options.StorageRoot -eq $root) 'Downloaded installer lost typed options'
    Assert-True (-not (Test-Path -LiteralPath $received.Path)) 'Installer relay retained its temporary script'
    $failed = $false
    try { Invoke-InstallerScriptBlock ($fixture + "`nexit 19") @{} } catch { $failed = $true }
    Assert-True $failed 'Downloaded installer hid a child failure'
    $received = Get-Content -LiteralPath $env:BASALTWATER_TEST_CAPTURE -Raw | ConvertFrom-Json
    Assert-True (-not (Test-Path -LiteralPath $received.Path)) 'Failed relay retained its temporary script'

    # Exercise the actual pasted scriptblock and root parameter binder. Only
    # host observations are stubbed; Plan must exit before downloads or setup.
    $safe = $text.Replace('$ErrorActionPreference = "Stop"', @'
$ErrorActionPreference = 'Stop'
function Get-CimInstance { return [pscustomobject]@{ ProductType = 1; BuildNumber = 26100 } }
function Get-PSDrive { return [pscustomobject]@{ Free = 100GB } }
function Invoke-RestMethod { throw 'Plan tried to download source' }
function Invoke-WebRequest { throw 'Plan tried to download source' }
'@)
    $plan = & ([scriptblock]::Create($safe)) -Profile agent_windows -Node -GameDev -ShaderTools -Godot -Sunshine -StorageDrive F: -Plan 6>&1 | Out-String
    Assert-True ($plan -match 'Sunshine: True' -and $plan -match 'Game dev: True' -and $plan -match 'F:\\Basaltwater') 'Pasted installer did not forward optional feature flags'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $temporary 'Basaltwater\windows-setup.json'))) 'Downloaded preview wrote saved setup'

    # Re-pasting on an installed account must enter its pinned installer even
    # when main is unchanged, or when source updates are explicitly disabled.
    $revision = 'a' * 40
    $source = Join-Path $temporary ('Basaltwater\source\' + $revision)
    New-Item -ItemType Directory -Path (Join-Path $source 'windows') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $source 'windows\AgentWorkstation.Common.ps1') -Value '# source marker'
    Set-Content -LiteralPath (Join-Path $source 'install.ps1') -Value @'
[CmdletBinding()]
param($Profile, [switch]$Resume, [switch]$NoUpdateSource, [switch]$NoRestartT3)
@{ Source = $PSScriptRoot; Resume = [bool]$Resume; NoUpdateSource = [bool]$NoUpdateSource;
   NoRestartT3 = [bool]$NoRestartT3 } | ConvertTo-Json |
    Set-Content -LiteralPath $env:BASALTWATER_TEST_CAPTURE -Encoding UTF8
'@
    @{ Version = 1; Profile = 'agent_windows'; Revision = $revision; Source = $source;
       OwnerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value;
       AgentTool = @('gh', 'codex'); Node = $true; Python = $true; Go = $false;
       T3CodeDesktop = $true; Phase = 'complete' } | ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $temporary 'Basaltwater\windows-setup.json') -Encoding UTF8
    & ([scriptblock]::Create($safe)) -Profile agent_windows -NoUpdateSource -NoRestartT3
    $received = Get-Content -LiteralPath $env:BASALTWATER_TEST_CAPTURE -Raw | ConvertFrom-Json
    Assert-True ($received.Source -eq $source -and $received.Resume -and $received.NoUpdateSource -and
        $received.NoRestartT3) 'Pasted source opt-out lost the pinned entry or restart opt-out'
    $apply = $safe.Replace("throw 'Plan tried to download source'", "return @{ sha = ('a' * 40) }")
    & ([scriptblock]::Create($apply)) -Profile agent_windows
    $received = Get-Content -LiteralPath $env:BASALTWATER_TEST_CAPTURE -Raw | ConvertFrom-Json
    Assert-True ($received.Source -eq $source -and $received.Resume -and $received.NoUpdateSource -and
        -not $received.NoRestartT3) 'Pasted default rerun failed to enter the unchanged source with normal restart defaults'
    Write-Host 'Windows installer entry tests passed.'
} finally {
    $env:LOCALAPPDATA = $savedLocal; $env:BASALTWATER_TEST_CAPTURE = $savedCapture
    $resolved = [IO.Path]::GetFullPath($temporary)
    $root = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe installer test cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
