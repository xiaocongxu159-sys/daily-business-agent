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

function Get-MetricSum {
  param(
    [Parameter(Mandatory = $true)]$Rows,
    [Parameter(Mandatory = $true)][string]$Name
  )

  $total = 0.0
  foreach ($row in @($Rows)) {
    $value = $null
    if ($row -is [System.Collections.IDictionary]) {
      if ($row.Contains($Name)) {
        $value = $row[$Name]
      }
    } else {
      $property = $row.PSObject.Properties |
        Where-Object { $_.Name -eq $Name } |
        Select-Object -First 1
      if ($null -ne $property) {
        $value = $property.Value
      }
    }
    if ($null -ne $value -and [string]$value -ne "") {
      $total += [double]$value
    }
  }
  return $total
}

function Assert-LocalAnalysisThroughHttp {
  param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$ExpectedVersion,
    [Parameter(Mandatory = $true)][string]$DataRoot,
    [Parameter(Mandatory = $true)][string]$Label
  )

  $workspace = Join-Path $env:RUNNER_TEMP ("http-analysis-" + $Label)
  Remove-Item -LiteralPath $workspace -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force -Path $workspace | Out-Null

  $mapping = Join-Path $workspace "mapping.csv"
  $erp = Join-Path $workspace "product-performance.csv"

  @"
shop_id,shop_name,marketplace,seller-sku,MSKU,asin1,item-name,quantity
SYNTHETIC-STORE,Synthetic Store,US,SYNTH-SKU-1,SYNTH-SKU-1,B000TEST01,Synthetic Product,10
"@ | Set-Content -LiteralPath $mapping -Encoding utf8

  @"
日期,shop_id,marketplace,ASIN,MSKU,标题,销售额,订单量,Sessions-Total,PV-Total,展示,点击,广告花费,广告销售额,广告订单量
2026-07-27,SYNTHETIC-STORE,US,B000TEST01,SYNTH-SKU-1,Synthetic Product,39.98,2,10,12,100,10,5,19.99,1
"@ | Set-Content -LiteralPath $erp -Encoding utf8

  $agent = Start-Process $Executable -ArgumentList @("--no-browser", "--data-root", $DataRoot) -PassThru
  try {
    Wait-AgentHealth -ExpectedVersion $ExpectedVersion | Out-Null
    $base = "http://127.0.0.1:8766"
    $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    Invoke-WebRequest -Uri "$base/" -WebSession $session -UseBasicParsing -TimeoutSec 10 | Out-Null

    $job = Invoke-RestMethod `
      -Uri "$base/v1/jobs" `
      -Method Post `
      -WebSession $session `
      -ContentType "application/json" `
      -Body (@{
        label = "windows-http-$Label"
        write_excel = $true
        write_html = $true
      } | ConvertTo-Json) `
      -TimeoutSec 15

    Invoke-RestMethod `
      -Uri "$base/v1/jobs/$($job.job_id)/files/mapping" `
      -Method Post `
      -WebSession $session `
      -Form @{ file = Get-Item -LiteralPath $mapping } `
      -TimeoutSec 30 | Out-Null

    Invoke-RestMethod `
      -Uri "$base/v1/jobs/$($job.job_id)/files/erp" `
      -Method Post `
      -WebSession $session `
      -Form @{ file = Get-Item -LiteralPath $erp } `
      -TimeoutSec 30 | Out-Null

    $queued = Invoke-RestMethod `
      -Uri "$base/v1/jobs/$($job.job_id)/run" `
      -Method Post `
      -WebSession $session `
      -TimeoutSec 15
    if ($queued.status -ne "queued") {
      throw "installed Agent did not queue the HTTP analysis job"
    }

    $snapshot = $null
    foreach ($attempt in 1..180) {
      $snapshot = Invoke-RestMethod `
        -Uri "$base/v1/jobs/$($job.job_id)" `
        -WebSession $session `
        -TimeoutSec 10
      if ($snapshot.status -in @("success", "failed")) { break }
      Start-Sleep -Seconds 1
    }
    if (-not $snapshot -or $snapshot.status -ne "success") {
      $detail = if ($snapshot) { [string]$snapshot.error } else { "no job response" }
      throw "installed Agent HTTP analysis did not succeed: $detail"
    }

    $artifactPayload = Invoke-RestMethod `
      -Uri "$base/v1/jobs/$($job.job_id)/artifacts" `
      -WebSession $session `
      -TimeoutSec 15
    $paths = @($artifactPayload.artifacts | ForEach-Object { [string]$_.relative_path })
    if (-not ($paths | Where-Object { $_ -like "output/*.xlsx" })) {
      throw "installed Agent HTTP analysis did not create Excel"
    }
    foreach ($required in @("output/dashboard.html", "output/dashboard_data.json", "job_result.json")) {
      if ($required -notin $paths) {
        throw "installed Agent HTTP analysis artifact missing: $required"
      }
    }

    $dashboardResponse = Invoke-WebRequest `
      -Uri "$base/v1/jobs/$($job.job_id)/artifacts/output/dashboard_data.json" `
      -WebSession $session `
      -UseBasicParsing `
      -TimeoutSec 15
    $dashboard = $dashboardResponse.Content | ConvertFrom-Json -AsHashtable
    $daily = @($dashboard["daily"])
    if ($daily.Count -ne 1) {
      throw "installed Agent HTTP dashboard row count mismatch: $($daily.Count)"
    }

    $actual = @{
      sales = Get-MetricSum -Rows $daily -Name "销售额"
      orders = Get-MetricSum -Rows $daily -Name "总订单"
      sessions = Get-MetricSum -Rows $daily -Name "Sessions"
      page_views = Get-MetricSum -Rows $daily -Name "PV"
      ad_spend = Get-MetricSum -Rows $daily -Name "广告花费"
      ad_sales = Get-MetricSum -Rows $daily -Name "广告销售额"
    }
    $expected = @{
      sales = 39.98
      orders = 2.0
      sessions = 10.0
      page_views = 12.0
      ad_spend = 5.0
      ad_sales = 19.99
    }
    foreach ($metric in $expected.Keys) {
      if ([Math]::Abs([double]$actual[$metric] - [double]$expected[$metric]) -gt 0.001) {
        throw "installed Agent HTTP analysis lost metric ${metric}: actual=$($actual[$metric]) expected=$($expected[$metric])"
      }
    }
    Write-Host "PASS: installed Agent queued, ran and preserved nonzero dashboard metrics ($Label)"
  } finally {
    Stop-Process -Id $agent.Id -Force -ErrorAction SilentlyContinue
    Wait-Process -Id $agent.Id -ErrorAction SilentlyContinue
    Wait-PortReleased
  }
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
Assert-LocalAnalysisThroughHttp -Executable $exe -ExpectedVersion $version -DataRoot $dataDir -Label "first-install"

$upgradeAgent = Start-Process $exe -ArgumentList @("--no-browser", "--data-root", $dataDir) -PassThru
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
Assert-LocalAnalysisThroughHttp -Executable $exe -ExpectedVersion $version -DataRoot $dataDir -Label "after-upgrade"
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
$commandKey = Get-Item "HKCU:\Software\Classes\DailyBusinessAgent.ConnectionPackage\shell\open\command"
$openCommand = [string]$commandKey.GetValue("")
if (-not $openCommand.Contains($exe) -or -not $openCommand.Contains('%1')) {
  throw ".dba open command is incorrect"
}
Write-Host "PASS: .dba double-click association verified"

$agent = Start-Process $exe -ArgumentList @("--no-browser", "--data-root", $dataDir) -PassThru
try {
  Wait-AgentHealth -ExpectedVersion $version | Out-Null
  $page = Invoke-WebRequest "http://127.0.0.1:8766/lingxing" -UseBasicParsing -TimeoutSec 5
  if ($page.Content -notmatch 'id="package-ready"') { throw "single-file package state missing" }
  if ($page.Content -match 'type="file"') { throw "browser file picker must not ship" }
  if ($page.Content -match '\.sha256') { throw "sidecar checksum instructions must not ship" }
  if ($page.Content -match 'id="proxy-url"') { throw "manual proxy input must not ship" }
  $listeners = Get-NetTCPConnection -State Listen -LocalPort 8766
  if ($listeners.LocalAddress | Where-Object { $_ -notin @("127.0.0.1", "::1") }) {
    throw "non-loopback listener detected"
  }
  Write-Host "PASS: loopback UI and hidden technical fields verified"
} finally {
  Stop-Process -Id $agent.Id -Force -ErrorAction SilentlyContinue
  Wait-Process -Id $agent.Id -ErrorAction SilentlyContinue
  Wait-PortReleased
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
