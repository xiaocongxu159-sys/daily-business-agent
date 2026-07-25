$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "[1/6] Installing pinned build and CI dependencies..."
python -m pip install --disable-pip-version-check -r requirements-build.txt -r requirements-ci.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed with exit code $LASTEXITCODE" }

Write-Host "[2/6] Checking dependency consistency..."
python -m pip check
if ($LASTEXITCODE -ne 0) { throw "pip check failed with exit code $LASTEXITCODE" }

Write-Host "[3/6] Running public tests..."
python -B -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed with exit code $LASTEXITCODE" }

Write-Host "[4/6] Building standalone Windows application..."
Remove-Item -Recurse -Force build, dist, release -ErrorAction SilentlyContinue
python -m PyInstaller --noconfirm --clean packaging\DailyBusinessAgent.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$AppExe = Join-Path $Root "dist\DailyBusinessAgent\DailyBusinessAgent.exe"
if (-not (Test-Path $AppExe)) { throw "PyInstaller output is missing: $AppExe" }

Write-Host "[5/6] Compiling single-file installer..."
$Candidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 7\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
)
$Iscc = $Candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) { throw "Inno Setup compiler is not installed." }

& $Iscc "packaging\installer.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed with exit code $LASTEXITCODE" }

$Setup = Join-Path $Root "release\DailyBusinessAgent-Setup-0.1.0.exe"
if (-not (Test-Path $Setup)) { throw "Installer output is missing: $Setup" }

Write-Host "[6/6] Writing checksum and size metadata..."
$Hash = Get-FileHash -Algorithm SHA256 $Setup
$Size = (Get-Item $Setup).Length
@(
    "file=DailyBusinessAgent-Setup-0.1.0.exe"
    "size_bytes=$Size"
    "sha256=$($Hash.Hash.ToLowerInvariant())"
) | Set-Content -Encoding utf8 "release\DailyBusinessAgent-Setup-0.1.0.txt"

Write-Host "PASS: installer created at $Setup"
