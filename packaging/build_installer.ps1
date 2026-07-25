$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$Version = (Get-Content -Raw "VERSION").Trim()
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "VERSION must be semantic x.y.z" }
$parts = $Version.Split('.')
$VersionTuple = "$($parts[0]), $($parts[1]), $($parts[2]), 0"

Write-Host "[1/6] Installing pinned build dependencies..."
python -m pip install --disable-pip-version-check -r requirements-build.lock
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }

Write-Host "[2/6] Running tests and public-boundary scan..."
python -B -m unittest discover -s tests -p "test_*.py"
if ($LASTEXITCODE -ne 0) { throw "Tests failed" }
python scripts/public_boundary_scan.py
if ($LASTEXITCODE -ne 0) { throw "Public boundary scan failed" }

Write-Host "[3/6] Generating Windows version metadata..."
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($VersionTuple), prodvers=($VersionTuple), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('080404B0', [
    StringStruct('CompanyName', 'CTJFyrdian'),
    StringStruct('FileDescription', '每日经营数据本地助手'),
    StringStruct('FileVersion', '$Version'),
    StringStruct('InternalName', 'DailyBusinessAgent'),
    StringStruct('OriginalFilename', 'DailyBusinessAgent.exe'),
    StringStruct('ProductName', '每日经营数据本地助手'),
    StringStruct('ProductVersion', '$Version')
  ])]), VarFileInfo([VarStruct('Translation', [2052, 1200])])]
)
"@ | Set-Content -Encoding utf8 "packaging\version_info.txt"

Write-Host "[4/6] Building standalone Windows application..."
Remove-Item -Recurse -Force build, dist, release -ErrorAction SilentlyContinue
python -m PyInstaller --noconfirm --clean packaging\DailyBusinessAgent.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "[5/6] Compiling installer..."
$Candidates = @(
  "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
  "${env:ProgramFiles}\Inno Setup 7\ISCC.exe",
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
)
$Iscc = $Candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) { throw "Inno Setup compiler is not installed" }
& $Iscc "/DMyAppVersion=$Version" "packaging\installer.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed" }

Write-Host "[6/6] Writing checksum metadata..."
$Setup = Join-Path $Root "release\DailyBusinessAgent-Setup-$Version.exe"
if (-not (Test-Path $Setup)) { throw "Installer output is missing: $Setup" }
$Hash = Get-FileHash -Algorithm SHA256 $Setup
"$($Hash.Hash.ToLowerInvariant())  DailyBusinessAgent-Setup-$Version.exe" | Set-Content -Encoding ascii "$Setup.sha256"
Write-Host "PASS: installer created at $Setup"
