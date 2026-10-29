@echo off
setlocal
rem Rebuild Windows PowerShell module paths instead of inheriting PowerShell 7 modules.
set "PSModulePath="
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Invoke-Basaltw.ps1" %*
exit /b %errorlevel%
