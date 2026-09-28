<# Encrypt an upload bearer token for the current Windows user using DPAPI. #>
[CmdletBinding()]
param([string]$Path = (Join-Path $env:LOCALAPPDATA 'Basaltwater\artifact-token.dpapi'))
$ErrorActionPreference = "Stop"
$parent = Split-Path -Parent ([IO.Path]::GetFullPath($Path))
New-Item -ItemType Directory -Path $parent -Force | Out-Null
if (Test-Path -LiteralPath $Path) { throw "Credential file already exists; remove it explicitly to rotate" }
$token = Read-Host "Artifact upload bearer token" -AsSecureString
ConvertFrom-SecureString $token | Set-Content -LiteralPath $Path -Encoding ASCII
Write-Host "Saved a Windows user-bound encrypted token to $Path"
