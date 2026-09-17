param([switch]$ExerciseReplicaFailure)
$ErrorActionPreference = 'Stop'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$files = @('-f', (Join-Path $root 'docker-compose.yml'), '-f', (Join-Path $PSScriptRoot 'compose.yaml'))
$replicas = docker compose @files ps --format json backend | ConvertFrom-Json
if (@($replicas).Count -lt 2) { throw 'Expected at least two Backend replicas.' }
$frontend = Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 http://127.0.0.1:3000/api/v1/health
if ($frontend.StatusCode -ne 200) { throw 'Frontend-to-cluster health check failed.' }
Write-Output "Backend replicas healthy: $(@($replicas).Count)"

if ($ExerciseReplicaFailure) {
    $target = @($replicas)[0].Name
    if (-not $target) { throw 'Could not resolve an exact Backend container.' }
    Write-Output "Stopping one test replica: $target"
    docker stop --time 10 $target | Out-Null
    try {
        $after = Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 http://127.0.0.1:3000/api/v1/health
        if ($after.StatusCode -ne 200) { throw 'Health check failed after replica stop.' }
        Write-Output 'New HTTP requests still succeed through the surviving replica.'
    } finally {
        docker start $target | Out-Null
        Write-Output "Restarted test replica: $target"
    }
}

