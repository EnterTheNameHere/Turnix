@echo off
setlocal
rem Internal platform setup only. Applications provide their own separate setup.
rem Let Windows PowerShell select its own modules when called from PowerShell 7.
set "PSModulePath="
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "setupExitCode=%ERRORLEVEL%"
if not "%setupExitCode%"=="0" (
    echo.
    echo Actant internal setup failed with exit code %setupExitCode%.
    rem Keep double-click failures visible; automation can suppress this wait.
    if not defined ACTANT_SETUP_NO_PAUSE pause
)
exit /b %setupExitCode%
