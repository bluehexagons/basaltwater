<# Optional build only. Uses existing MSVC and SDK; never installs components. #>
[CmdletBinding()]
param([Parameter(Mandatory)][string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$output = [IO.Path]::GetFullPath($OutputDirectory)
if ($output -match '["\r\n%&|<>^]') { throw 'Unsupported build output path' }
$source = Join-Path $PSScriptRoot 'Audio.Capture.cpp'
if ($source -match '["\r\n%&|<>^]') { throw 'Unsupported source path' }
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path -LiteralPath $vswhere)) { throw 'Optional audio build needs existing MSVC C++ tools and Windows SDK' }
$installation = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $installation) { throw 'MSVC C++ tools unavailable; no packages were installed' }
$developer = Join-Path $installation 'Common7\Tools\VsDevCmd.bat'
New-Item -ItemType Directory -Path $output -Force | Out-Null
$exe = Join-Path $output 'Audio.Capture.exe'; $obj = Join-Path $output 'Audio.Capture.obj'
$line = '""{0}" -no_logo -arch=x64 && cl.exe /nologo /std:c++17 /EHsc /W4 /WX /O2 /MT /DUNICODE /D_UNICODE "{1}" /Fo"{2}" /Fe"{3}" /link ole32.lib mmdevapi.lib advapi32.lib"' -f $developer,$source,$obj,$exe
$savedPath = $env:PATH
try {
    # Some existing VsDevCmd installations invoke vswhere by basename.
    $env:PATH = (Split-Path -Parent $vswhere) + ';' + $savedPath
    & $env:ComSpec /d /s /c $line
    if ($LASTEXITCODE -ne 0) { throw 'Native process audio compilation failed' }
} finally { $env:PATH = $savedPath }
Write-Output $exe
