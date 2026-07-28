$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

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
    throw "loopback health failed for version $ExpectedVersion"
  }
  return $health
}

function Wait-PortReleased {
  foreach ($attempt in 1..40) {
    $listener = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue
    if (-not $listener) { return }
    Start-Sleep -Milliseconds 250
  }
  throw "Agent port 8766 was not released"
}

function Stop-AgentProcess {
  Get-Process -Name "DailyBusinessAgent" -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
  Wait-PortReleased
}

function Assert-UiAvailable {
  param([Parameter(Mandatory = $true)][string]$ExpectedVersion)

  $agent = Start-Process $script:Exe -ArgumentList @("--no-browser", "--data-root", $script:DataDir) -PassThru
  try {
    Wait-AgentHealth -ExpectedVersion $ExpectedVersion | Out-Null
    $page = Invoke-WebRequest "http://127.0.0.1:8766/" -UseBasicParsing -TimeoutSec 10
    if ($page.StatusCode -ne 200) { throw "local UI returned $($page.StatusCode)" }
    if ($page.Content -match 'local UI file is missing') { throw "local UI file is missing" }
    if ($page.Content -notmatch '每日经营') { throw "local UI content is incomplete" }
  } finally {
    Stop-Process -Id $agent.Id -Force -ErrorAction SilentlyContinue
    Wait-Process -Id $agent.Id -ErrorAction SilentlyContinue
    Stop-AgentProcess
  }
}

$version = (Get-Content -Raw "VERSION").Trim()
$setup = (Resolve-Path "release\DailyBusinessAgent-Setup-$version.exe").Path
$script:InstallDir = Join-Path $env:RUNNER_TEMP "DailyBusinessAgentRunningUpgradeApp"
$script:DataDir = Join-Path $env:RUNNER_TEMP "DailyBusinessAgentRunningUpgradeData"
$script:Exe = Join-Path $script:InstallDir "DailyBusinessAgent.exe"
$runtime = Join-Path $script:InstallDir "_internal\base_library.zip"
$uiFile = Join-Path $script:InstallDir "_internal\agent\static\index.html"
$backupDir = Join-Path $script:InstallDir ".upgrade-backup"

Stop-AgentProcess
Remove-Item -LiteralPath $script:InstallDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $script:DataDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $script:DataDir | Out-Null
Set-Content -LiteralPath (Join-Path $script:DataDir "preserve-running-upgrade.txt") -Value "keep"

$installArgs = @(
  "/VERYSILENT",
  "/SUPPRESSMSGBOXES",
  "/NORESTART",
  "/SP-",
  "/DIR=$script:InstallDir",
  "/TASKS=autostart"
)

$firstInstall = Start-Process $setup -ArgumentList $installArgs -Wait -PassThru
if ($firstInstall.ExitCode -ne 0) { throw "running-upgrade fixture install failed: $($firstInstall.ExitCode)" }
if (-not (Test-Path -LiteralPath $runtime)) { throw "base_library.zip missing after fixture install" }
if (-not (Test-Path -LiteralPath $uiFile)) { throw "local UI missing after fixture install" }

$runningAgent = Start-Process $script:Exe -ArgumentList @("--no-browser", "--data-root", $script:DataDir) -PassThru
Wait-AgentHealth -ExpectedVersion $version | Out-Null
$staleMarker = Join-Path $script:InstallDir "_internal\stale-running-upgrade-marker.txt"
Set-Content -LiteralPath $staleMarker -Value "must disappear"

$upgrade = Start-Process $setup -ArgumentList $installArgs -Wait -PassThru
if ($upgrade.ExitCode -ne 0) { throw "running Agent in-place upgrade failed: $($upgrade.ExitCode)" }
Start-Sleep -Milliseconds 500
if (Get-Process -Id $runningAgent.Id -ErrorAction SilentlyContinue) {
  throw "installer did not stop the running Agent process"
}
Wait-PortReleased
if (Test-Path -LiteralPath $staleMarker) {
  throw "running upgrade did not replace stale PyInstaller runtime"
}
if (-not (Test-Path -LiteralPath (Join-Path $script:DataDir "preserve-running-upgrade.txt"))) {
  throw "running upgrade deleted user data"
}
if (Test-Path -LiteralPath $backupDir) {
  throw "successful upgrade left rollback directory behind"
}
Assert-UiAvailable -ExpectedVersion $version
Write-Host "PASS: installer stopped a running Agent, upgraded it and preserved local UI and data"

$before = @{
  exe = (Get-FileHash -Algorithm SHA256 -LiteralPath $script:Exe).Hash
  runtime = (Get-FileHash -Algorithm SHA256 -LiteralPath $runtime).Hash
  ui = (Get-FileHash -Algorithm SHA256 -LiteralPath $uiFile).Hash
}
$lockMarker = Join-Path $env:RUNNER_TEMP "daily-business-agent-lock-acquired.txt"
$lockScript = Join-Path $env:RUNNER_TEMP "daily-business-agent-lock-runtime.ps1"
Remove-Item -LiteralPath $lockMarker -Force -ErrorAction SilentlyContinue
@'
param([string]$Target, [string]$Marker)
$stream = [IO.File]::Open($Target, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None)
try {
  Set-Content -LiteralPath $Marker -Value "locked"
  Start-Sleep -Seconds 30
} finally {
  $stream.Dispose()
}
'@ | Set-Content -LiteralPath $lockScript -Encoding utf8

$locker = Start-Process powershell.exe -ArgumentList @(
  "-NoLogo",
  "-NoProfile",
  "-NonInteractive",
  "-ExecutionPolicy", "Bypass",
  "-File", $lockScript,
  "-Target", $runtime,
  "-Marker", $lockMarker
) -PassThru
$lockerReleased = $false
try {
  foreach ($attempt in 1..40) {
    if (Test-Path -LiteralPath $lockMarker) { break }
    if ($locker.HasExited) { throw "runtime locker exited before acquiring the file" }
    Start-Sleep -Milliseconds 250
  }
  if (-not (Test-Path -LiteralPath $lockMarker)) { throw "runtime lock was not acquired" }

  $blocked = Start-Process $setup -ArgumentList $installArgs -Wait -PassThru
  if ($blocked.ExitCode -eq 0) {
    throw "installer unexpectedly succeeded while base_library.zip was locked"
  }
  if ($blocked.ExitCode -ne 7) {
    throw "locked-runtime upgrade returned unexpected exit code: $($blocked.ExitCode)"
  }

  Stop-Process -Id $locker.Id -Force -ErrorAction SilentlyContinue
  Wait-Process -Id $locker.Id -ErrorAction SilentlyContinue
  $lockerReleased = $true

  $after = @{
    exe = (Get-FileHash -Algorithm SHA256 -LiteralPath $script:Exe).Hash
    runtime = (Get-FileHash -Algorithm SHA256 -LiteralPath $runtime).Hash
    ui = (Get-FileHash -Algorithm SHA256 -LiteralPath $uiFile).Hash
  }
  foreach ($name in $before.Keys) {
    if ($before[$name] -ne $after[$name]) {
      throw "blocked upgrade modified existing $name before aborting"
    }
  }
  if (Test-Path -LiteralPath $backupDir) {
    throw "blocked upgrade left a rollback directory"
  }
} finally {
  if (-not $lockerReleased) {
    Stop-Process -Id $locker.Id -Force -ErrorAction SilentlyContinue
    Wait-Process -Id $locker.Id -ErrorAction SilentlyContinue
  }
  Remove-Item -LiteralPath $lockMarker, $lockScript -Force -ErrorAction SilentlyContinue
}

Assert-UiAvailable -ExpectedVersion $version
Write-Host "PASS: locked-runtime failure aborted before modifying the existing Agent"

$uninstaller = Join-Path $script:InstallDir "unins000.exe"
$uninstall = Start-Process $uninstaller -ArgumentList @(
  "/VERYSILENT",
  "/SUPPRESSMSGBOXES",
  "/NORESTART"
) -Wait -PassThru
if ($uninstall.ExitCode -ne 0) { throw "running-upgrade fixture uninstall failed" }
if (-not (Test-Path -LiteralPath (Join-Path $script:DataDir "preserve-running-upgrade.txt"))) {
  throw "uninstall deleted user data after running-upgrade test"
}
Write-Host "PASS: running-upgrade verification completed"
