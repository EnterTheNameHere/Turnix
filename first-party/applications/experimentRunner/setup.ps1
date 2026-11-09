# Application-local setup only. Run repository root setup separately first.
$ErrorActionPreference = "Stop"
$appDirectory = $PSScriptRoot
$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $appDirectory "../../.."))
$pythonExecutable = Join-Path $repositoryRoot "python-embedded/python.exe"
if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    Write-Host "Experiment Runner prerequisite unavailable. Run repository ROOT setup.bat separately first."
    exit 2
}
# Preserve native stderr warnings, and judge the readiness check by its exit code.
$ErrorActionPreference = "Continue"
& $pythonExecutable -X frozen_modules=off (Join-Path $appDirectory "setupLocal.py")
$setupExitCode = $LASTEXITCODE
if ($setupExitCode -ne 0) {
    Write-Host "Experiment Runner setup failed with exit code $setupExitCode."
    Write-Host "For runtime startup/import/tool failures, run repository ROOT setup.bat separately. Check local/ permissions for local setup errors."
}
exit $setupExitCode
