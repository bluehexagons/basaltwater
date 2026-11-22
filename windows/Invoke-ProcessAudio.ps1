param([Parameter(Mandatory=$true)][string]$Request)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
try {
    . (Join-Path $PSScriptRoot 'Desktop.Common.ps1')
    . (Join-Path $PSScriptRoot 'Development.Common.ps1')
    . (Join-Path $PSScriptRoot 'Development.Diagnostics.ps1')
    . (Join-Path $PSScriptRoot 'Audio.Capture.ps1')
    $options=Read-NativeHeldJson $Request
    $capture=Invoke-DevelopmentProcessAudio -Id $options.task -Project $options.project -Helper $options.helper -OptIn -Seconds $options.seconds -Member $options.member -CallerProcessId $options.caller_pid -CallerCreatedFileTimeUtc $options.caller_created
    @{schema_version=1;ok=[bool]$capture.Capture.ok;result=$capture} | ConvertTo-Json -Depth 12 -Compress
    if(-not $capture.Capture.ok){exit 1}
} catch {
    @{schema_version=1;ok=$false;error=$_.Exception.Message} | ConvertTo-Json -Compress
    exit 1
}
