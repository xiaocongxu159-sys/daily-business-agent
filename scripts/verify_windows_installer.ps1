$ErrorActionPreference = "Stop"

$version = (Get-Content -Raw "VERSION").Trim()
$setup = (Resolve-Path "release\DailyBusinessAgent-Setup-$version.exe").Path
$installDir = Join-Path $env:RUNNER_TEMP "DailyBusinessAgentApp"
$dataDir = Join-Path $env:RUNNER_TEMP "DailyBusinessAgentData"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
Set-Content (Join-Path $dataDir "preserve-me.txt") "keep"

$install = Start-Process $setup -ArgumentList @(
  "/VERYSILENT",
  "/SUPPRESSMSGBOXES",
  "/NORESTART",
  "/SP-",
  "/DIR=$installDir",
  "/TASKS=autostart"
) -Wait -PassThru
if ($install.ExitCode -ne 0) { throw "installer failed: $($install.ExitCode)" }

$exe = Join-Path $installDir "DailyBusinessAgent.exe"
if (-not (Test-Path $exe)) { throw "installed executable missing" }
Write-Host "PASS: candidate installed"

$startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\每日经营数据本地助手 后台同步.lnk"
if (-not (Test-Path $startup)) { throw "startup shortcut missing" }
Write-Host "PASS: startup shortcut exists"

$name = "导入每日经营连接包.lnk"
$desktopCandidates = @(
  [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory),
  (Join-Path $env:USERPROFILE "Desktop"),
  (Join-Path $env:PUBLIC "Desktop")
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique
$importShortcut = @($desktopCandidates | ForEach-Object { Join-Path $_ $name }) |
  Where-Object { Test-Path $_ } |
  Select-Object -First 1
if (-not $importShortcut) { throw "desktop import shortcut missing" }
Write-Host "PASS: drag-drop import shortcut exists"

$installerLines = Get-Content "packaging\installer.iss"
$importLines = @($installerLines | Where-Object { $_ -like '*Name: "{autodesktop}\导入每日经营连接包"*' })
if ($importLines.Count -ne 1) { throw "installer import shortcut contract missing or duplicated" }
$importLine = $importLines[0]
if ($importLine -notlike '*Filename: "{app}\{#MyAppExeName}"*') {
  throw "installer import shortcut does not target the Agent executable"
}
if ($importLine -match 'Parameters:') {
  throw "installer import shortcut must not contain fixed arguments"
}
Write-Host "PASS: dropped .dba path will be supplied as the only argument"

$extensionKey = Get-Item "HKCU:\Software\Classes\.dba"
$association = [string]$extensionKey.GetValue("")
if ($association -ne "DailyBusinessAgent.ConnectionPackage") {
  throw ".dba file association missing"
}
$commandKey = Get-Item "HKCU:\Software\Classes\DailyBusinessAgent.ConnectionPackage\shell\open\command"
$openCommand = [string]$commandKey.GetValue("")
if (-not $openCommand.Contains($exe) -or -not $openCommand.Contains('%1')) {
  throw ".dba open command is incorrect"
}
Write-Host "PASS: .dba double-click association verified"

$agent = Start-Process $exe -ArgumentList @("--no-browser","--data-root",$dataDir) -PassThru
try {
  $health = $null
  foreach ($attempt in 1..60) {
    try {
      $health = Invoke-RestMethod "http://127.0.0.1:8766/health" -TimeoutSec 2
      if ($health.status -eq "ok") { break }
    } catch {
      Start-Sleep -Seconds 1
    }
  }
  if (-not $health -or $health.bind -ne "127.0.0.1:8766" -or $health.version -ne $version) {
    throw "loopback health failed"
  }
  $page = Invoke-WebRequest "http://127.0.0.1:8766/lingxing" -UseBasicParsing -TimeoutSec 5
  if ($page.Content -notmatch 'id="package-ready"') { throw "single-file package state missing" }
  if ($page.Content -match 'type="file"') { throw "browser file picker must not ship" }
  if ($page.Content -match '\.sha256') { throw "sidecar checksum instructions must not ship" }
  if ($page.Content -match 'id="proxy-url"') { throw "manual proxy input must not ship" }
  $listeners = Get-NetTCPConnection -State Listen -LocalPort 8766
  if ($listeners.LocalAddress | Where-Object { $_ -notin @("127.0.0.1","::1") }) {
    throw "non-loopback listener detected"
  }
  Write-Host "PASS: loopback UI and hidden technical fields verified"
} finally {
  Stop-Process -Id $agent.Id -Force -ErrorAction SilentlyContinue
}

$uninstaller = Join-Path $installDir "unins000.exe"
$uninstall = Start-Process $uninstaller -ArgumentList @(
  "/VERYSILENT",
  "/SUPPRESSMSGBOXES",
  "/NORESTART"
) -Wait -PassThru
if ($uninstall.ExitCode -ne 0) { throw "uninstaller failed" }
if (-not (Test-Path (Join-Path $dataDir "preserve-me.txt"))) {
  throw "user data deleted"
}
if (Test-Path "HKCU:\Software\Classes\DailyBusinessAgent.ConnectionPackage") {
  throw "file association was not removed by uninstall"
}
Write-Host "PASS: uninstall preserved user data and removed association"
