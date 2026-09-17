[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("PROMOTE-REHEARSAL-STANDBY")]
    [string]$Confirm,
    [string]$ProjectName = "trainer112-ha-rehearsal",
    [switch]$LeaveWritersStopped
)

$ErrorActionPreference = "Stop"
if ($ProjectName -ne "trainer112-ha-rehearsal") {
    throw "Refusing project '$ProjectName'. This script only operates on trainer112-ha-rehearsal."
}

$composeFiles = @(
    "docker-compose.yml",
    "deploy/cluster/compose.yaml",
    "deploy/tls/docker-compose.tls.yml",
    "deploy/cluster/compose.tls.yaml",
    "deploy/cluster/compose.ha-rehearsal.yaml"
)
$composeArgs = @("compose", "--project-name", $ProjectName)
foreach ($file in $composeFiles) { $composeArgs += @("-f", $file) }
$composeArgs += @("--profile", "tls")

function Invoke-CheckedDocker {
    param([Parameter(Mandatory = $true)][string[]]$DockerArguments)
    $output = & docker @DockerArguments
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed (exit $LASTEXITCODE)." }
    return $output
}

function Get-OneContainer([string]$Service, [bool]$MustRun = $true) {
    $args = $composeArgs + @("ps", "-q")
    if ($MustRun) { $args += @("--status", "running") }
    [string[]]$ids = @((Invoke-CheckedDocker ($args + @($Service))) | Where-Object { $_ })
    if (@($ids).Count -ne 1) { throw "Expected exactly one $Service container; found $(@($ids).Count)." }
    $containerId = [string]$ids
    $metadata = ((Invoke-CheckedDocker @("inspect", $containerId)) | Out-String | ConvertFrom-Json)[0]
    $label = $metadata.Config.Labels.'com.docker.compose.project'
    if ($label -ne $ProjectName) { throw "Container $containerId belongs to '$label', not the rehearsal project." }
    return $containerId
}

function Invoke-Psql([string]$Container, [string]$Sql) {
    $result = Invoke-CheckedDocker @("exec", $Container, "psql", "-v", "ON_ERROR_STOP=1", "-U", "trainer", "-d", "trainer", "-Atqc", $Sql)
    return ($result | Out-String).Trim()
}

function Set-ProxyServer([string]$Proxy, [string]$Server, [string]$State) {
    $command = "set server postgres_nodes/$Server state $State"
    Invoke-CheckedDocker @("exec", $Proxy, "sh", "-ec", "printf '%s\n' '$command' | socat - UNIX-CONNECT:/var/lib/haproxy/admin.sock >/dev/null") | Out-Null
}

function Save-ProxyState([string]$Proxy) {
    Invoke-CheckedDocker @("exec", "--user", "0", $Proxy, "sh", "-ec", "umask 022; printf 'show servers state\n' | socat - UNIX-CONNECT:/var/lib/haproxy/admin.sock > /var/lib/haproxy/server-state.tmp; mv /var/lib/haproxy/server-state.tmp /var/lib/haproxy/server-state") | Out-Null
}

function Start-ApplicationWriters {
    if ($LeaveWritersStopped) {
        Write-Host "Application writers remain stopped as requested."
        return
    }
    Invoke-CheckedDocker ($composeArgs + @("up", "-d", "--no-deps", "--scale", "backend=2", "backend")) | Out-Null
    Invoke-CheckedDocker ($composeArgs + @("up", "-d", "--no-deps", "frontend", "voice")) | Out-Null
}

$primary = Get-OneContainer "pg-primary"
$standby = Get-OneContainer "pg-standby"
$proxy = Get-OneContainer "postgres"
$oldPrimaryFenced = $false

try {
    if ((Invoke-Psql $primary "select pg_is_in_recovery()") -ne "f") { throw "pg-primary is not a primary." }
    if ((Invoke-Psql $standby "select pg_is_in_recovery()") -ne "t") { throw "pg-standby is not in recovery." }
    $replication = Invoke-Psql $primary "select state || ':' || sync_state from pg_stat_replication where application_name='trainer_standby'"
    if ($replication -ne "streaming:sync") { throw "Standby is not synchronous and streaming (observed '$replication')." }

    Write-Host "Stopping application writers..."
    Invoke-CheckedDocker ($composeArgs + @("stop", "backend", "frontend", "voice")) | Out-Null
    foreach ($service in @("backend", "frontend", "voice")) {
        $running = @(Invoke-CheckedDocker ($composeArgs + @("ps", "-q", "--status", "running", $service))) | Where-Object { $_ }
        if ($running.Count -ne 0) { throw "Writer service $service is still running." }
    }

    Set-ProxyServer $proxy "primary" "maint"
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $backupPath = "/rehearsal-backups/pre-promotion-$stamp.dump.cms"
    Write-Host "Creating an encrypted pre-promotion pg_dump in the isolated rehearsal backup volume..."
    $backupCommand = "set -o pipefail; pg_dump -U trainer -d trainer --format=custom | openssl cms -encrypt -binary -aes-256-gcm -outform DER -recip /run/postgresql-tls/server.crt -out '$backupPath'"
    Invoke-CheckedDocker @("exec", $primary, "bash", "-ec", $backupCommand) | Out-Null
    Invoke-Psql $primary "checkpoint" | Out-Null
    $primaryLsn = Invoke-Psql $primary "select pg_current_wal_flush_lsn()"

    $caughtUp = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $behind = Invoke-Psql $standby "select pg_wal_lsn_diff('$primaryLsn', pg_last_wal_replay_lsn()) <= 0"
        if ($behind -eq "t") { $caughtUp = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $caughtUp) { throw "Standby did not replay through primary LSN $primaryLsn." }

    Write-Host "Fencing the old primary container..."
    Invoke-CheckedDocker @("stop", "--time", "30", $primary) | Out-Null
    $primaryState = ((Invoke-CheckedDocker @("inspect", $primary)) | Out-String | ConvertFrom-Json)[0].State
    if ($primaryState.Running) { throw "Old primary could not be fenced." }
    $oldPrimaryFenced = $true

    Write-Host "Promoting the caught-up standby..."
    Invoke-CheckedDocker @("exec", "--user", "postgres", $standby, "pg_ctl", "-D", "/var/lib/postgresql/data", "promote", "-w", "-t", "30") | Out-Null
    if ((Invoke-Psql $standby "select pg_is_in_recovery()") -ne "f") { throw "Standby promotion was not confirmed." }
    # The base backup inherited the old primary's required synchronous standby
    # name. In the documented degraded one-node state it must be cleared or every
    # new commit would wait forever for a standby that no longer exists.
    Invoke-Psql $standby "alter system set synchronous_standby_names=''" | Out-Null
    Invoke-Psql $standby "select pg_reload_conf()" | Out-Null
    if ((Invoke-Psql $standby "show synchronous_standby_names") -ne "") {
        throw "Promoted writer still requires the former synchronous standby."
    }

    Set-ProxyServer $proxy "standby" "ready"
    Save-ProxyState $proxy
    Write-Host "Starting application writers against the promoted endpoint..."
    Start-ApplicationWriters
    Write-Host "Promotion complete. Old primary remains stopped. Backup: $backupPath"
} catch {
    if (-not $oldPrimaryFenced) {
        try {
            Set-ProxyServer $proxy "primary" "ready"
            Save-ProxyState $proxy
            Start-ApplicationWriters
        } catch {
            Write-Warning "Rollback before fencing also failed; keep application writers stopped and inspect the rehearsal."
        }
    } else {
        Write-Warning "Old primary is fenced. It will not be restarted automatically; keep writers stopped until the promoted endpoint is verified."
    }
    throw
}
