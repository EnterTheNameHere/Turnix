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
  [string] $GetPipRel     = "vendor/python/get-pip.py"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Section($t){ Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Get-Sha256([string]$path){ (Get-FileHash -Algorithm SHA256 -Path $path).Hash.ToUpper() }

function Normalize-EmbeddablePth([string]$embedDir) {
  $pth = Join-Path $embedDir "python312._pth"
  if (!(Test-Path $pth)) {
    $cand = Get-ChildItem -Path $embedDir -Filter "python*.pth" -ErrorAction SilentlyContinue |
            Select-Object -First 1
    if ($cand) { $pth = $cand.FullName } else { return }
  }

  $text = Get-Content $pth -Raw
  $text2 = [regex]::Replace($text, '(?m)^\s*#\s*import\s+site\s*$', 'import site', 1)

  if ($text2 -ne $text) {
    Copy-Item $pth "$pth.bak" -Force -ErrorAction SilentlyContinue
    Set-Content -Path $pth -Value $text2 -Encoding UTF8
    Write-Host "Uncommented 'import site' in python312._pth"
  } else {
    Write-Host "python312._pth already normalized"
  }
}

function Ensure-LocalPython(){
  Section "Install Python $PythonVersion (embeddable) from local ZIP"
  $zipPath = Join-Path $root $PythonZipRel
  if(!(Test-Path $zipPath)){ throw "Missing $PythonZipRel — add it to the repo." }

  # Optional integrity check
  $shaFile = Join-Path $root $Sha256Rel
  if(Test-Path $shaFile){
    $expected = (Get-Content $shaFile -Raw).Trim().ToUpper()
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
      return (Join-Path $dest "python.exe")
    }
    Write-Host "Different version detected ($installed) → reinstalling."
    Remove-Item $dest -Recurse -Force
  }

  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [System.IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $dest)

  Normalize-EmbeddablePth -embedDir $dest
  Set-Content $verFile $PythonVersion -Encoding UTF8
  return (Join-Path $dest "python.exe")
}

function Ensure-Pip([string]$pyExe, [string]$getPipRelPath){
  Section "Bootstrapping pip (embeddable-safe)"
  $embedDir = Split-Path $pyExe -Parent
  $scripts  = Join-Path $embedDir "Scripts"
  $pipExe   = Join-Path $scripts "pip.exe"

  # Make Scripts discoverable for this process (no global PATH change)
  $env:PATH = "$scripts;$env:PATH"

  # Try ensurepip first
  $ensureOk = $true
  try { & $pyExe -m ensurepip -q 2>$null | Out-Null } catch { $ensureOk = $false }

  if (!(Test-Path $pipExe)) {
    if (Test-Path $getPipRelPath) {
      Write-Host "ensurepip not available; running get-pip.py"
      & $pyExe $getPipRelPath
    } else {
      Write-Host "⚠️  pip not found and $getPipRelPath missing — skipping pip bootstrap"
    }
  } else {
    Write-Host "pip is available (ensurepip worked)."
  }

  # Sanity check (non-fatal)
  try { & $pyExe -c "import pip, sys; print('pip-ok', pip.__version__)" } catch {
    Write-Host "⚠️  'python -m pip' import not stable yet; will prefer pip.exe." -ForegroundColor Yellow
  }
}

function Pip-Step([string]$pyExe, [string]$req){
  Section "Python requirements ($req)"
  $embedDir = Split-Path $pyExe -Parent
  $scripts  = Join-Path $embedDir "Scripts"
  $pipExe   = Join-Path $scripts "pip.exe"

  if(Test-Path $req){
    if (Test-Path $pipExe) {
      & $pipExe install --upgrade pip
      & $pipExe install -r $req
    } else {
      Write-Host "pip.exe not found; attempting 'python -m pip'..."
      & $pyExe -m pip install --upgrade pip
      & $pyExe -m pip install -r $req
    }
  } else {
    Write-Host "No $req → skipping."
  }
}

function Npm-Electron(){
  Section "npm install (electron/)"
  if(Test-Path ".\electron\package.json"){
    if(Test-Path ".\electron\package-lock.json"){ npm --prefix .\electron ci } else { npm --prefix .\electron install }
  } else {
    Write-Host "electron/package.json not found → skipping."
  }
}

function Link-NodeModules(){
  Section "node_modules link (root → electron/node_modules)"
  $rootLink = Join-Path $root "node_modules"
  $elecNM   = Join-Path $root "electron\node_modules"

  if(!(Test-Path $elecNM)){
    Write-Host "electron/node_modules missing → run npm first." -ForegroundColor Yellow
    return
  }

  if(Test-Path $rootLink){
    $attr = Get-Item $rootLink -Force
    if ($attr.Attributes -band [IO.FileAttributes]::ReparsePoint) { Remove-Item $rootLink -Force }
    else { Remove-Item $rootLink -Recurse -Force }
  }

  $created = $false

  # --- Try symlink (Admin or Developer Mode) ---
  cmd /c "mklink /D `"$rootLink`" `"$elecNM`""
  $code = $LASTEXITCODE
  if ($code -eq 0 -and (Test-Path $rootLink)) {
    $created = $true
    Write-Host "Created symbolic link: $rootLink → $elecNM"
  } else {
    Write-Host "Symlink failed (exit $code). Will try junction..." -ForegroundColor Yellow
  }

  # --- Fallback: junction (no Admin needed) ---
  if (-not $created) {
    cmd /c "mklink /J `"$rootLink`" `"$elecNM`""
    $code = $LASTEXITCODE
    if ($code -eq 0 -and (Test-Path $rootLink)) {
      $created = $true
      Write-Host "Created junction:     $rootLink → $elecNM"
    } else {
      Write-Host "❌ Failed to create link or junction (exit $code)." -ForegroundColor Red
      Write-Host "   Try one of these manually:"
      Write-Host "     > Run PowerShell as Administrator (or enable Windows Developer Mode) and re-run"
      Write-Host "     > cmd /c mklink /D `"$rootLink`" `"$elecNM`""
      Write-Host "     > cmd /c mklink /J `"$rootLink`" `"$elecNM`""
      return
    }
  }

  # --- Verify result & show quick status ---
  $item = Get-Item $rootLink -Force
  $type = ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) ? "ReparsePoint" : "Directory"
  Write-Host "Created: $($item.FullName)  [$type]"
}

# --- Run ---
$py = Ensure-LocalPython
Ensure-Pip -pyExe $py -getPipRelPath (Join-Path $root $GetPipRel)
Pip-Step   -pyExe $py -req $Requirements
Npm-Electron
Link-NodeModules

Write-Host "`nAll set ✅" -ForegroundColor Green
