<# Offline AV setup tests: package operations and persisted selections are mocked. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
. (Join-Path $repo 'windows\Media.Common.ps1')
foreach ($file in @('install.ps1', 'windows\Media.Common.ps1', 'windows\Cli.Common.ps1',
                    'windows\AgentWorkstation.Common.ps1', 'windows\WorkstationHealth.Common.ps1')) {
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $repo $file), [ref]$tokens, [ref]$errors)
    Assert-True ($errors.Count -eq 0) "Syntax errors: $file $errors"
    if ($file -eq 'install.ps1') {
        Assert-True ('AvTools' -in @($ast.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })) 'Installer lacks AV selection'
        $builder = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-NativeOptionalArguments' }, $true)
        Invoke-Expression $builder.Extent.Text
    }
}
function Invoke-NativeAgentSetup { param([switch]$AvTools) }
Assert-True ((Get-NativeOptionalArguments ([pscustomobject]@{ AvTools = $true })).AvTools) 'Saved AV selection was not forwarded'
Assert-True ((Get-NativeOptionalArguments ([pscustomobject]@{})).Count -eq 0) 'Legacy state selected AV implicitly'
$script:Packages = @(); $script:Saved = @{}; $script:Probes = @(); $script:Paths = @()
$script:Commands = @{ ffmpeg = 'C:\preinstalled 音\ffmpeg.exe'; ffprobe = 'C:\preinstalled 音\ffprobe.exe' }
function Get-NativeMediaCommand($Name) { return $script:Commands[$Name] }
function Assert-NativeSkillPath($Path) { $script:Paths += $Path }
function Update-NativePath { }
function Ensure-WingetPackage($PackageId, $Label) {
    $script:Packages += $PackageId
    $script:Commands.ffmpeg = 'C:\new\ffmpeg.exe'
    $script:Commands.ffprobe = 'C:\new\ffprobe.exe'
}
function Invoke-NativeHealthProbe($Command, $Arguments) {
    Assert-True ($Arguments -eq '-version') 'FFmpeg needs single-dash version option'
    $script:Probes += $Command
    return [pscustomobject]@{ State = 'ok'; Detail = 'fixture version' }
}
function Set-NativeMediaExecutable($Name, $Command) { $script:Saved[$Name] = $Command }
Install-NativeAvTools -Plan
Assert-True ($script:Packages.Count -eq 0 -and $script:Saved.Count -eq 0) 'Plan mutated setup'
Install-NativeAvTools
Assert-True ($script:Packages.Count -eq 0 -and $script:Saved.ffmpeg -like 'C:\preinstalled*') 'Preinstalled tools were replaced'
$script:Commands.ffprobe = $null
Install-NativeAvTools
Assert-True ($script:Packages.Count -eq 1 -and $script:Packages[0] -eq 'Gyan.FFmpeg') 'Missing tool did not select exact AV package'
Assert-True ($script:Saved.ffmpeg -like 'C:\preinstalled*' -and $script:Saved.ffprobe -eq 'C:\new\ffprobe.exe') 'Partial discovery replaced existing executable'
Write-Host 'Windows AV setup tests passed.'
