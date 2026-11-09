@echo off
setlocal
rem Application-local setup; never invokes root setup. No interactive questions.
set "PSModulePath="
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
set "setupExitCode=%ERRORLEVEL%"
if not "%setupExitCode%"=="0" echo Experiment Runner setup failed with exit code %setupExitCode%.
exit /b %setupExitCode%
