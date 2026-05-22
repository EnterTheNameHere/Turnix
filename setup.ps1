# setup.ps1
param(
  # Version marker written to python-embedded/VERSION.txt
  [string] $PythonVersion = "3.12.10",

  # Local ZIP + checksum kept in this project root
  [string] $PythonZipRel  = "vendor/python/python-3.12.10-embed-amd64.zip",
  [string] $Sha256Rel     = "vendor/python/python-3.12.10-embed-amd64.zip.sha256",

  # Target install dir for the embeddable runtime (under this project root)
  [string] $EmbedDir      = "python-embedded",

  # Your Python deps (relative to this project root)
  [string] $Requirements  = "requirements.txt",

  # Optional local bootstrap for pip (recommended to commit this)
  [string] $GetPipRel     = "vendor/python/get-pip.py",

  # Write sitecustomize.py to python-embedded (includes optional stdlib http guard)
  [switch] $WithHttpGuard = $true
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Section($t){ Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Get-Sha256([string]$path){ (Get-FileHash -Algorithm SHA256 -Path $path).Hash.ToUpper() }
function Write-Utf8NoBom([string]$path, [string]$text) {
  $utf8NoBomEncoding = New-Object System.Text.UTF8Encoding($false)
  [IO.File]::WriteAllText($path, $text, $utf8NoBomEncoding)
}
function Invoke-NativeChecked([string] $label, [string] $exe, [string[]] $arguments) {
  Write-Host "Running: $label"

  $output = & $exe @arguments 2>&1
  $exitCode = $LASTEXITCODE

  if ($exitCode -ne 0) {
    $outputText = ($output | Out-String).TrimEnd()
    throw "$label failed with exit code $exitCode.`nCommand: $exe $($arguments -join ' ')`nOutput:`n$outputText"
  }

  $outputText = ($output | Out-String).TrimEnd()
  if ($outputText) {
    Write-Host $outputText
  }

  return $output
}

function Ensure-GitHooks() {
  Section "Git hooks setup"

  $gitCmd = Get-Command git -ErrorAction SilentlyContinue
  if (-not $gitCmd) { throw "git not found in PATH. Install Git for Windows or fix PATH." }

  $inRepo = $false
  try {
    & git rev-parse --show-toplevel | Out-Null
    $inRepo = $true
  } catch {
    $inRepo = $false
  }
  if (-not $inRepo) { throw "Not inside a git repository (or .git missing). Run setup from repo root." }

  $hooksDir = Join-Path $root ".githooks"
  $hookFile = Join-Path $hooksDir "pre-commit"

  if (-not (Test-Path $hooksDir)) { throw "Missing $hooksDir. Create it and commit it." }
  if (-not (Test-Path $hookFile)) { throw "Missing hook: $hookFile (expected pre-commit hook script)." }

  # Ensure Git uses the repo-tracked hooks directory.
  & git config core.hooksPath ".githooks"

  $configured = (& git config --get core.hooksPath).Trim()
  if ($configured -ne ".githooks") {
    throw "Failed to set core.hooksPath. Expected '.githooks', got '$configured'."
  }

  Write-Host "Configured core.hooksPath = .githooks"

  # Sanity check: confirm git sees the hook file via the configured path.
  $expected = (Resolve-Path $hookFile).Path
  $hookSeen = (Join-Path (Join-Path $root $configured) "pre-commit")
  $hookSeen = (Resolve-Path $hookSeen).Path

  if ($hookSeen -ne $expected) {
    throw "Hook path mismatch. Expected '$expected' but Git hooksPath resolves to '$hookSeen'."
  }

  Write-Host "Hook present: $hookSeen"

  # Line ending check: pre-commit should be LF, not CRLF.
  # Git Bash can handle CRLF sometimes, but it is a common source of subtle failures.
  # We fail loudly to avoid a future "why is hook not running" situation.
  $raw = [IO.File]::ReadAllBytes($hookFile)
  $hasCrLf = $false
  for ($i = 0; $i -lt ($raw.Length - 1); $i++) {
    if ($raw[$i] -eq 13 -and $raw[$i + 1] -eq 10) { $hasCrLf = $true; break }
  }
  if ($hasCrLf) {
    throw "Hook file has CRLF line endings: $hookFile`n" +
          "Convert it to LF (Unix) line endings. Example:`n" +
          "  - In VS Code: set 'End of Line Sequence' to LF, save`n" +
          "  - Or run in Git Bash: sed -i 's/\r$//' .githooks/pre-commit"
  }

  # Basic shebang check
  $firstLine = (Get-Content $hookFile -TotalCount 1 -ErrorAction Stop).TrimEnd()
  if ($firstLine -notmatch '^#!/usr/bin/env bash$') {
    throw "Hook file first line must be '#!/usr/bin/env bash' but got: '$firstLine'`n" +
          "Fix the shebang to ensure Git Bash runs it reliably."
  }

  Write-Host "Hook line endings OK (LF) and shebang OK."
}

function Normalize-EmbeddablePth([string]$embedDir) {
  $pth = Join-Path $embedDir "python312._pth"
  if (!(Test-Path $pth)) {
    $cand = Get-ChildItem -Path $embedDir -Filter "python*.pth" -ErrorAction SilentlyContinue |
            Select-Object -First 1
    if ($cand) {
      $pth = $cand.FullName
    } else {
      throw "No python ._pth file found in embedded Python directory: $embedDir"
    }
  }
  
  $lines = Get-Content -Path $pth -ErrorAction Stop
  $normalized = New-Object System.Collections.Generic.List[string]
  $sawParentPath = $false

  foreach ($line in $lines) {
    $trimmed = $line.Trim()
    
    if ($trimmed -eq "..") {
      $sawParentPath = $true
      $normalized.Add("..")
      continue
    }

    if ($trimmed -match '(?m)^\s*#\s*import\s+site\s*$' -or $trimmed -eq "import site") {
      continue
    }

    $normalized.Add($line)
  }

  if (-not $sawParentPath) {
    $insertAt = 0
    # Add it below "." if the file has it
    for ($ii = 0; $ii -lt $normalized.Count; $ii++) {
      if ($normalized[$ii].Trim() -eq ".") {
        $insertAt = $ii + 1
        break
      }
    }
    $normalized.Insert($insertAt, "..")
  }

  if ($normalized.Count -gt 0 -and $normalized[$normalized.Count - 1].Trim() -ne "") {
    $normalized.Add("")
  }
  $normalized.Add("# Run site.main() automatically")
  $normalized.Add("import site")

  $text = Get-Content $pth -Raw
  $text2 = ($normalized -join "`r`n") + "`r`n"

  if ($text2 -ne $text) {
    Copy-Item $pth "$pth.bak" -Force -ErrorAction SilentlyContinue
    Write-Utf8NoBom -path $pth -text $text2
    Write-Host "Normalized python ._pth file with repo parent path and import site: $pth"
  } else {
    Write-Host "python ._pth already normalized: $pth"
  }
}

function Ensure-SiteCustomize([string]$embedDir, [bool]$withHttpGuard = $true) {
  Section "sitecustomize.py (embeddable runtime setup)"
  $path = Join-Path $embedDir "sitecustomize.py"

  # --- Python body to mirror PYTHONPATH ---
  $body = @'
# Auto-generated by setup script — keeps embeddable Python friendly with VS Code pytest.
# Mirrors PYTHONPATH (provided by VS Code test adapter) into sys.path so '-p vscode_pytest' imports cleanly.

import os, sys

pp = os.environ.get("PYTHONPATH")
if pp:
    for p in pp.split(os.pathsep):
        if p and p not in sys.path:
            sys.path.insert(0, p)
'@

  if ($withHttpGuard) {
    # --- optional guard, appended safely ---
    $guard = @'
# sanity guard: ensure stdlib http.cookiejar is importable (optional)
if os.environ.get("ASSERT_STDLIB_HTTP", "1") == "1":
    import importlib.util as _u
    _s = _u.find_spec("http.cookiejar")
    if not _s or "python312.zip" not in (_s.origin or ""):
        raise RuntimeError(f"Unexpected http.cookiejar location: {_s and _s.origin}")
'@
    $body += "`n" + $guard
  }

  $existing = if (Test-Path $path) { Get-Content $path -Raw } else { "" }
  if ($existing -ne $body) {
    Write-Utf8NoBom -path $path -text $body
    Write-Host "Wrote sitecustomize.py ($([IO.Path]::GetFileName($path)))"
  } else {
    Write-Host "Already up to date → $([IO.Path]::GetFileName($path))"
  }
}

function Ensure-LocalPython(){
  Section "Install Python $PythonVersion (embeddable) from local ZIP"
  $zipPath = Join-Path $root $PythonZipRel
  if(!(Test-Path $zipPath)){ throw "Missing $PythonZipRel — add it to the repo." }

  # Optional integrity check
  $shaFile = Join-Path $root $Sha256Rel
  if(Test-Path $shaFile){
    $expected = ((Get-Content $shaFile -Raw).Trim() -split '\s+')[0].ToUpper()
    $actual   = Get-Sha256 $zipPath
    if($actual -ne $expected){ throw "SHA256 mismatch for $PythonZipRel. Expected $expected, got $actual" }
  }

  $dest = Join-Path $root $EmbedDir
  $verFile = Join-Path $dest "VERSION.txt"
  if(Test-Path $verFile){
    $installed = (Get-Content $verFile -Raw).Trim()
    if($installed -eq $PythonVersion){
      Write-Host "Python $installed already installed → skipping unzip."
      Normalize-EmbeddablePth -embedDir $dest
      Ensure-SiteCustomize -embedDir $dest -withHttpGuard:$WithHttpGuard
      return (Join-Path $dest "python.exe")
    }
    Write-Host "Different version detected ($installed) → reinstalling."
    Remove-Item $dest -Recurse -Force
  }

  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [System.IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $dest)

  Normalize-EmbeddablePth -embedDir $dest
  Ensure-SiteCustomize -embedDir $dest -withHttpGuard:$WithHttpGuard
  Write-Utf8NoBom -path $verFile -text "$PythonVersion`r`n"
  return (Join-Path $dest "python.exe")
}

function Ensure-Pip([string]$pyExe, [string]$getPipRelPath){
  Section "Bootstrapping pip (embeddable-safe)"
  
  if (-not (Test-Path $pyExe)) {
    throw "Python executable does not exist: $pyExe"
  }
  
  $embedDir = Split-Path $pyExe -Parent
  $scripts  = Join-Path $embedDir "Scripts"
  $pipExe   = Join-Path $scripts "pip.exe"

  # Make pip.exe discoverable for child processes.
  $env:PATH = "$scripts;$env:PATH"

  # First try ensurepip. Embeddable Python often does not have it, so failure here
  # is not immediately fatal. We only treat it as fatal if no later bootstrap works.
  if (-not (Test-Path $pipExe)) {
    Write-Host "pip.exe not found yet. Trying ensurepip..."

    try {
      Invoke-NativeChecked -label "python -m ensurepip" -exe $pyExe -arguments @("-m", "ensurepip", "--upgrade")
    } catch {
      Write-Host "ensurepip did not complete successfully; this is common for embeddable Python." `
        -ForegroundColor Yellow
      Write-Host $_.Exception.Message -ForegroundColor DarkYellow
    }
  }

  # If ensurepip did not produce pip.exe, use local get-pip.py.
  if (-not (Test-Path $pipExe)) {
    if (-not (Test-Path $getPipRelPath)) {
      throw "pip.exe was not found and local get-pip.py is missing.`n" +
            "Expected pip.exe: $pipExe`n" +
            "Expected get-pip.py: $getPipRelPath`n" +
            "Add get-pip.py to the repo at <root>/vendor/python/ or install a Python bundle that includes pip."
    }

    Write-Host "pip.exe still not found. Running local get-pip.py..."

    Invoke-NativeChecked -label "get-pip.py bootstrap" -exe $pyExe -arguments @($getPipRelPath)
  }

  # Hard verification: get-pip.py may have run but still failed to create pip.exe.
  if (-not (Test-Path $pipExe)) {
    throw "pip bootstrap completed, but pip.exe still does not exist.`nExpected: $pipExe"
  }

  Write-Host "pip.exe found: $pipExe"

  # Verify that this embedded Python can import pip.
  $pipImportOutput = Invoke-NativeChecked -label "python -c import pip" -exe $pyExe -arguments @("-c", "import pip, sys; print('pip-ok', pip.__version__)")
  
  $pipImportText = ($pipImportOutput | Out-String).Trim()
  if ($pipImportText) {
    Write-Host $pipImportText
  }

  # Verify pip.exe itself runs and belongs to this environment.
  $pipVersionOutput = Invoke-NativeChecked -label "pip.exe --version" -exe $pipExe -arguments @("--version")
  
  $pipVersionText = ($pipVersionOutput | Out-String).Trim()
  if ($pipVersionText) {
    Write-Host $pipVersionText
  }
  
  return $pipExe
}

function Pip-Step([string]$pipExe, [string]$req){
  Section "Python requirements ($req)"

  if (-not (Test-Path $pipExe)) {
    throw "pip.exe does not exist: $pipExe"
  }
  
  if(Test-Path $req){
    Invoke-NativeChecked -label "pip install --upgrade pip" -exe $pipExe -arguments @("install", "--upgrade", "pip")
    Invoke-NativeChecked -label "pip install requirements" -exe $pipExe -arguments @("install", "-r", $req)
  } else {
    Write-Host "No $req → skipping."
  }
}

# --- Run ---
Ensure-GitHooks
$py = Ensure-LocalPython
$pip = Ensure-Pip -pyExe $py -getPipRelPath (Join-Path $root $GetPipRel)
Pip-Step -pipExe $pip -req $Requirements

Write-Host "`nAll set ✅" -ForegroundColor Green
Write-Host "Run Turnix terminal with:" -ForegroundColor Green
Write-Host "  .\python-embedded\python.exe -m backend.cli.main" -ForegroundColor Green
Write-Host "Run with llama.cpp provider using (you need to run your own llama.cpp server):"  -ForegroundColor Green
Write-Host "  .\python-embedded\python.exe -m backend.cli.main --provider llamacpp" -ForegroundColor Green
