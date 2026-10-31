<# Diagnose saved native selections without applying setup. #>
[CmdletBinding()]
param([switch]$Json, [switch]$Skills, [ValidateRange(1, 300)][int]$TimeoutSeconds = 30)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
try {
    foreach ($file in @('Cli.Common.ps1', 'AgentWorkstation.Common.ps1', 'GameDevelopment.Common.ps1',
                        'Godot.Common.ps1', 'Sunshine.Common.ps1', 'DevelopmentStorage.Common.ps1', 'T3Completion.Common.ps1',
                        'Desktop.Common.ps1', 'WorkstationHealth.Common.ps1')) {
        . (Join-Path $PSScriptRoot $file)
    }
    $saved = Read-NativeCliSetup
    $report = if ($Skills) { Get-NativeSkillHealth $saved } else { Get-NativeWorkstationHealth $saved -TimeoutSeconds $TimeoutSeconds }
    if ($Json) { $report | ConvertTo-Json -Depth 5 }
    else {
        if ($Skills) { Write-Host "Skill source: $($report.Source) (saved revision $($report.Revision)); session-loaded state unknown." }
        foreach ($check in $report.Checks) {
            Write-Host "[$($check.State)] $($check.Name): $($check.Detail)"
            if ($check.PSObject.Properties['Command'] -and $check.Command) { Write-Host "  $($check.Command)" }
            if ($check.PSObject.Properties['Path']) { Write-Host "  $($check.Path) (required: $($check.Required))" }
        }
        Write-Host 'Host checks do not establish provider login, project builds, export templates or GPU readiness.'
    }
    if (-not $report.Healthy) { exit 1 }
} catch {
    [Console]::Error.WriteLine('Windows doctor: ' + $_.Exception.Message)
    exit 1
}
