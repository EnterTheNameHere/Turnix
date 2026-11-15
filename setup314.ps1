# file: setup314.ps1
# Explicit fallback; share all bootstrap, dependency verification and hook behavior.
param(
  [string] $PythonVersion = "3.14.6",
  [string] $PythonZipRel = "vendor/python/python-3.14.6-embed-amd64.zip",
  [string] $Sha256Rel = "vendor/python/python-3.14.6-embed-amd64.zip.sha256",
  [string] $GetPipRel = "vendor/python/get-pip.py",
  [string] $EmbedDir = "python-embedded",
  [string] $Requirements = "requirements.txt"
)

$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "setup.ps1") `
  -PythonVersion $PythonVersion -PythonZipRel $PythonZipRel -Sha256Rel $Sha256Rel `
  -GetPipRel $GetPipRel -EmbedDir $EmbedDir -Requirements $Requirements
