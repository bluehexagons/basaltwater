param([Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z0-9._-]+$')][string]$Distribution)
$ErrorActionPreference = "Stop"
& wsl.exe --distribution $Distribution --exec /usr/bin/sleep infinity
exit $LASTEXITCODE
