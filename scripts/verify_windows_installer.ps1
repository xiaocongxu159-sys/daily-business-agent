$ErrorActionPreference = "Stop"

function Assert-SdkRuntime {
  param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$LogName
  )

  $sdkRuntimeLog = Join-Path $env:RUNNER_TEMP $LogName
  Remove-Item -LiteralPath $sdkRuntimeLog -Force -ErrorAction SilentlyContinue
  $previousCrashLog = $env:DAILY_BUSINESS_AGENT_CRASH_LOG
  $env:DAILY_BUSINESS_AGENT_CRASH_LOG = $sdkRuntimeLog
  try {
    $sdkCheck = Start-Process $Executable -ArgumentList @("--verify-sdk-runtime") -Wait -PassThru
  } finally {
    $env:DAILY_BUSINESS_AGENT_CRASH_LOG = $previousCrashLog
  }
  if ($sdkCheck.ExitCode -ne 0) {
    if (Test-Path $sdkRuntimeLog) {
      Write-Host "Frozen SDK runtime diagnostic:"
      Get-Content -LiteralPath $sdkRuntimeLog
    }
    throw "installed executable cannot import Lingxing SDK: $($sdkCheck.ExitCode)"
  }
}

function Stop-ProcessTree {
  param([Parameter(Mandatory = $true)][int]$ProcessId)

  & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
}

function Write-LocalAnalysisDiagnostic {
  param(
    [Parameter(Mandatory = $true)][string]$Workspace,
    [Parameter(Mandatory = $true)][string]$RuntimeLog
  )

  $progress = Join-Path $Workspace "verification-progress.txt"
  if (Test-Path $progress) {
    Write-Host "Frozen local-analysis last progress:"
    Get-Content -LiteralPath $progress
  }
  if (Test-Path $RuntimeLog) {
    Write-Host "Frozen local-analysis diagnostic:"
    Get-Content -LiteralPath $RuntimeLog
  }
}

function Assert-LocalAnalysisRuntime {
  param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$Label
  )

  $workspace = Join-Path $env:RUNNER_TEMP ("local-analysis-runtime-" + $Label)
  $runtimeLog = Join-Path $env:RUNNER_TEMP ("local-analysis-runtime-" + $Label + ".log")
  Remove-Item -LiteralPath $workspace -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $runtimeLog -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force -Path $workspace | Out-Null

  $previousCrashLog = $env:DAILY_BUSINESS_AGENT_CRASH_LOG
  $env:DAILY_BUSINESS_AGENT_CRASH_LOG = $runtimeLog
  $analysisCheck = $null
  try {
    $analysisCheck = Start-Process $Executable -ArgumentList @(
      "--verify-local-analysis-runtime",
      $workspace
    ) -PassThru

    if (-not $analysisCheck.WaitForExit(240000)) {
      Stop-ProcessTree -ProcessId $analysisCheck.Id
      Write-LocalAnalysisDiagnostic -Workspace $workspace -RuntimeLog $runtimeLog
      throw "installed executable local-analysis verification exceeded 240 seconds"
    }
    $analysisCheck.Refresh()
  } finally {
    $env:DAILY_BUSINESS_AGENT_CRASH_LOG = $previousCrashLog
  }

  if ($analysisCheck.ExitCode -ne 0) {
    Write-LocalAnalysisDiagnostic -Workspace $workspace -RuntimeLog $runtimeLog
    throw "installed executable cannot generate local analysis outputs: $($analysisCheck.ExitCode)"
  }

  $verificationPath = Join-Path $workspace "verification-result.json"
  if (-not (Test-Path $verificationPath)) {
    throw "frozen local-analysis verification result missing"
  }
  $verification = Get-Content -LiteralPath $verificationPath -Raw | ConvertFrom-Json
  if ($verification.status -ne "success") {
    throw "frozen local-analysis status was not success"
  }
  foreach ($property in @("excel", "html", "json")) {
    $artifact = [string]$verification.$property
    if ([string]::IsNullOrWhiteSpace($artifact) -or -not (Test-Path -LiteralPath $artifact)) {
      throw "frozen local-analysis artifact missing: $property"
    }
  }
  foreach ($metric in @("sales", "orders", "sessions", "page_views", "ad_spend", "ad_sales")) {
    if ([double]$verification.totals.$metric -le 0) {
      throw "frozen local-analysis metric was not preserved: $metric"
    }
  }
  Write-Host "PASS: installed executable generated nonzero Excel, HTML and JSON metrics ($Label)"
}

function Wait-AgentHealth {
  param([Parameter(Mandatory = $true)][string]$ExpectedVersion)

  $health = $null
  foreach ($attempt in 1..60) {
    try {
      $health = Invoke-RestMethod "http://127.0.0.1:8766/health" -TimeoutSec 2
      if ($health.status -eq "ok") { break }
    } catch {
      Start-Sleep -Seconds 1
    }
  }
  if (-not $health -or $health.bind -ne "127.0.0.1:8766" -or $health.version -ne $ExpectedVersion) {
    throw "loopback health failed"
  }
  return $health
}

function Wait-PortReleased {
  foreach ($attempt in 1..30) {
    $listener = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue
    if (-not $listener) { return }
    Start-Sleep -Milliseconds 500
  }
  throw "Agent port was not released after safe stop"
}

$version = (Get-Content -Raw "VERSION").Trim()
$setup = (Resolve-Path "release\DailyBusinessAgent-Setup-$version.exe").Path
$installDir = Join-Path $env:RUNNER_TEMP "DailyBusinessAgentApp"
$dataDir = Join-Path $env:RUNNER_TEMP "DailyBusinessAgentData"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
Set-Content (Join-Path $dataDir "preserve-me.txt") "keep"

$installArgs = @(
  "/VERYSILENT",
  "/SUPPRESSMSGBOXES",
  "/NORESTART",
  "/SP-",
  "/DIR=$installDir",
  "/TASKS=autostart"
)

$install = Start-Process $setup -ArgumentList $installArgs -Wait -PassThru
if ($install.ExitCode -ne 0) { throw "installer failed: $($install.ExitCode)" }

$exe = Join-Path $installDir "DailyBusinessAgent.exe"
if (-not (Test-Path $exe)) { throw "installed executable missing" }
Write-Host "PASS: candidate installed"

Assert-SdkRuntime -Executable $exe -LogName "daily-business-agent-sdk-runtime-first-install.log"
Write-Host "PASS: clean-installed executable imports Lingxing SDK runtime"
Assert-LocalAnalysisRuntime -Executable $exe -Label "first-install"

$upgradeAgent = Start-Process $exe -ArgumentList @("--no-browser","--data-root",$dataDir) -PassThru
Wait-AgentHealth -ExpectedVersion $version | Out-Null
$staleMarker = Join-Path $installDir "_internal\stale-upgrade-marker.txt"
Set-Content -LiteralPath $staleMarker -Value "must be removed during upgrade"
Stop-Process -Id $upgradeAgent.Id -Force
Wait-Process -Id $upgradeAgent.Id -ErrorAction SilentlyContinue
Wait-PortReleased

$upgrade = Start-Process $setup -ArgumentList $installArgs -Wait -PassThru
if ($upgrade.ExitCode -ne 0) { throw "in-place upgrade failed: $($upgrade.ExitCode)" }
if (Test-Path $staleMarker) {
  throw "upgrade installer did not clean the stale PyInstaller runtime directory"
}
if (-not (Test-Path (Join-Path $dataDir "preserve-me.txt"))) {
  throw "upgrade installer deleted user data"
}
Assert-SdkRuntime -Executable $exe -LogName "daily-business-agent-sdk-runtime-after-upgrade.log"
Assert-LocalAnalysisRuntime -Executable $exe -Label "after-upgrade"
Write-Host "PASS: stopped-Agent in-place upgrade cleaned stale runtime and preserved user data"

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
if (-not ($installerLines | Where-Object { $_ -eq 'Type: filesandordirs; Name: "{app}\_internal"' })) {
  throw "installer stale runtime cleanup contract missing"
}
if (-not ($installerLines | Where-Object { $_ -eq 'CloseApplicationsFilter={#MyAppExeName}' })) {
  throw "installer close-application filter contract missing"
}
Write-Host "PASS: dropped .dba path and conservative upgrade contracts verified"

$extensionKey = Get-Item "HKCU:\Software\Classes\.dba"
$association = [string]$extensionKey.GetValue("")
if ($association -ne "DailyBusinessAgent.ConnectionPackage") {
  throw ".dba file association missing"
}
Write-Host "PASS: .dba double-click association verified"

$agent = Start-Process $exe -ArgumentList @("--no-browser","--data-root",$dataDir) -PassThru
try {
  Wait-AgentHealth -ExpectedVersion $version | Out-Null
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
