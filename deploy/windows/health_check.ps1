param([string]$BaseUrl = "http://127.0.0.1:8000")

$ErrorActionPreference = "Stop"
$Liveness = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 10
$Readiness = Invoke-RestMethod -Uri "$BaseUrl/api/system/health" -TimeoutSec 10
if ($Liveness.status -ne "ok" -or $Readiness.data.status -ne "ready") {
    throw "smd-web-hmi is not ready"
}
Write-Host "smd-web-hmi is ready, version $($Liveness.version)"
