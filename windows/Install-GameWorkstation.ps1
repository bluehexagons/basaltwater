<# Download entry point: native Antistatic/Godot coding account, including T3 Code. #>
[CmdletBinding()]
param([string]$StorageRoot = '', [switch]$Plan)

& {
    $ErrorActionPreference = 'Stop'
    $installer = Join-Path $env:TEMP ('basaltwater-' + [guid]::NewGuid().ToString('N') + '.ps1')
    try {
        Invoke-WebRequest 'https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.ps1' `
            -UseBasicParsing -OutFile $installer -TimeoutSec 120 -ErrorAction Stop
        $selection = @('-Profile', 'agent_windows', '-Node', '-GameDev', '-ShaderTools', '-Godot')
        if (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'Basaltwater\windows-setup.json')) {
            $selection += @('-Resume', '-UpdateSource')
        }
        if ($StorageRoot) { $selection += @('-StorageRoot', $StorageRoot) }
        if ($Plan) { $selection += '-Plan' }
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer @selection
        if ($LASTEXITCODE -ne 0) { throw "Windows game workstation setup failed (exit code $LASTEXITCODE)." }
    } finally {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
}
