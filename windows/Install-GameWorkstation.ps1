<# Download entry point: native Antistatic/Godot coding account, including T3 Code. #>
[CmdletBinding()]
param([string]$StorageRoot = '', [string]$StorageDrive = '', [switch]$Plan)

if ($StorageDrive) {
    if ($StorageDrive -notmatch '\A[A-Za-z]:?\z') { throw 'StorageDrive must be one drive letter, such as F or F:' }
    if ($PSBoundParameters.ContainsKey('StorageRoot')) { throw 'Choose StorageDrive or StorageRoot, not both' }
    $StorageRoot = $StorageDrive.Substring(0, 1).ToUpperInvariant() + ':\Basaltwater'
}

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
        try {
            # Native stderr warnings are error records in Windows PowerShell
            # 5.1. Preserve their text and decide success from the exit code.
            $ErrorActionPreference = 'Continue'
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer @selection 2>&1 |
                ForEach-Object { Write-Host "$_" }
            $code = $LASTEXITCODE
        } finally { $ErrorActionPreference = 'Stop' }
        if ($code -ne 0) { throw "Windows game workstation setup failed (exit code $code)." }
    } finally {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
}
