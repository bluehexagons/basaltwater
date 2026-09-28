<# Native Windows qualification job: write one small artifact. #>
$ErrorActionPreference = 'Stop'
$output = Join-Path (Get-Location).Path 'out\wsl-smoke.txt'
New-Item -ItemType Directory -Path (Split-Path -Parent $output) -Force | Out-Null
"Windows PowerShell job on $env:COMPUTERNAME at $([DateTime]::UtcNow.ToString('o'))" |
    Set-Content -LiteralPath $output -Encoding UTF8
