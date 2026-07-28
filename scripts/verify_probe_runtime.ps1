$ErrorActionPreference = "Stop"

$exe = (Resolve-Path "dist\DailyBusinessAgent\DailyBusinessAgent.exe").Path
$log = Join-Path $env:RUNNER_TEMP "daily-business-agent-probe-runtime.log"
Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
$previousCrashLog = $env:DAILY_BUSINESS_AGENT_CRASH_LOG
$env:DAILY_BUSINESS_AGENT_CRASH_LOG = $log
try {
  $check = Start-Process $exe -ArgumentList @("--verify-probe-runtime") -Wait -PassThru
} finally {
  $env:DAILY_BUSINESS_AGENT_CRASH_LOG = $previousCrashLog
}
if ($check.ExitCode -ne 0) {
  if (Test-Path $log) {
    Write-Host "Frozen probe runtime diagnostic:"
    Get-Content -LiteralPath $log
  }
  throw "frozen read-only probe runtime failed: $($check.ExitCode)"
}

$page = Get-Content -Raw "agent\static\lingxing.html"
foreach ($required in @(
  'id="probe-card"',
  'id="probe-now"',
  'id="probe-results"',
  '/v1/lingxing/probe',
  '不会显示或保存'
)) {
  if (-not $page.Contains($required)) {
    throw "probe UI contract missing: $required"
  }
}
if ($page -match '<script\s+src=' -or $page -match '<link[^>]+href=["'']https?://') {
  throw "probe UI must not load external resources"
}

Write-Host "PASS: frozen read-only probe runtime and local-only UI contract"
