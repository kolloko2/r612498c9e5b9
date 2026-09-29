# Operations monitoring and Docker backup worker

Daily and manual backups run in the `backup-worker` Compose service whenever the
TLS deployment profile is active. It connects directly to PostgreSQL with
`sslmode=verify-full`, mounts Voice recording/outbox volumes read-only, and writes
only authenticated AES-256-GCM `.t112` archives to `deploy/backups`. It has no
Docker socket or network listener. The key enters the container only as
`BACKUP_ENCRYPTION_KEY` from the ignored `deploy/private/backup.env`; that file and
root CA private key are not mounted or archived by this container worker.

The persistent `backup-worker-state.json` heartbeat uses the same ignored
`deploy/operations` directory as Backend. Therefore the operations UI can accept a
manual backup and show its result even when the Windows worker is absent. The UI's
existing `backup_enabled` and `backup_hour_utc` settings control the container
scheduler. A missed time runs once when the worker next starts that UTC day; failed
scheduled attempts wait 15 minutes before retrying. Backups are never auto-deleted.

Manual backup requests are atomically moved from `requests/` to `backup-claims/`.
The host worker sees the durable `backup-executor.json` ownership marker, does not
schedule/consume backups in container mode, and continues monitoring plus approved
Voice/Asterisk service operations. Final per-job files under `processed/` are merged
by Backend, avoiding a shared `completed.json` writer race.

Start through the normal TLS launcher. `backup-worker` is part of the TLS profile:

```powershell
python deploy/manage.py --tls up
```

On a clean machine `python deploy/manage.py up` selects this TLS profile by
default and reports missing key/certificate prerequisites instead of silently
starting without backups. `--plain` is an explicit development-only opt-out. At
container start, the image limits its root initialization to ownership and mode
`0770` of the two state bind mounts, then drops permanently to UID/GID 10001.

Plain development Compose deliberately does not start this worker because its
direct database client is required to verify PostgreSQL TLS and hostname.
`BACKUP_MODE=mock` is an explicit test-only executor mode: it exercises durable
scheduling and request results without contacting PostgreSQL, reading the recovery
key or creating an archive, and reports `simulated` rather than `completed`.
The live worker refreshes its durable heartbeat every 30 seconds during long dumps,
archive traversal and encryption, so the UI does not mistake active work for a
missing executor.

## Host monitoring and service-control worker

`deploy/ops_worker.py` is the host-only monitoring/service-control bridge.
It does not expose a port and must not run in a container with the Docker socket.
Backend and the worker share the ignored `deploy/operations` directory. Start the
worker separately from Compose, from the repository checkout that owns the root
`docker-compose.yml` and `.env.docker` files.

```powershell
python deploy/ops_worker.py
```

Use `--once` for a single health/configuration check. `--mock` publishes clearly
marked mock service state and simulates jobs without invoking Docker or `pg_dump`.
Mock state and its request queue live below `deploy/operations/mock`, isolated from
the real queue so a mock check cannot consume commands or suppress a real backup.
The regular poll interval is 15 seconds. In container backup mode it does not run
scheduled or manual backups. A PID lock prevents two host workers from
running in one checkout and safely recovers a lock whose process no longer exists.

## Files and safety boundary

- `status.json` is atomically replaced every poll. It contains the sample epoch,
  Compose service state/health, host CPU/memory/disk metrics, backup manifest,
  effective backup settings and a fixed non-secret deployment summary.
- `settings.json` accepts `{"backup_enabled":true,"backup_hour_utc":0}`. Defaults
  are enabled and 00:00 UTC. A missed scheduled time runs at the next poll that day.
- `requests/*.json` accepts `id` (canonical UUID), `created_at` (epoch seconds),
  `action` (`backup`, `start`, `stop`, `restart`) and, for service actions, `service`.
  Only `voice` and `asterisk` can be controlled. Requests older than 120 seconds
  are recorded as rejected so an old stop/restart cannot be replayed later.
- `completed.json` is an atomically written list of durable job results with
  `id`, `action`, `service`, `status`, `at` and a generic nullable `error`. It is
  bounded to the latest 200 results; durable per-job results and consumed request
  bodies are retained under `processed/`. An operation is first persisted as
  `in_progress`; after a worker crash it becomes `failed` and is never replayed.
- `events.jsonl` records only lifecycle transitions and generic errors. It never
  contains Docker log output, stderr, exceptions, environment dumps or credentials.
  Service sampling failures/recovery and CPU, memory, or disk usage crossing the
  90% warning boundary are recorded only on transition. Alert acknowledgement is
  is a UI-local view preference and does not rewrite this append-only file.

Consumed request files and completed results are archived for idempotency. Current
container backups are encrypted `.t112` bundles; historical host `.dump`, `.failed`
and `.partial` files remain visible and are not deleted. Protect this directory and
test restoration separately. Older encrypted host-helper bundles retain their
original include sets and must be treated as sensitive configuration backups. The
host worker executes fixed argument
arrays with `shell=False` and always uses the root `.env.docker`; it accepts no
caller-provided command or filesystem path.

## Service installation

On Linux, create a dedicated system service whose working directory is the checkout
and whose command uses the checkout's Python interpreter, for example:

```ini
[Unit]
Description=Trainer 112 host operations worker
After=docker.service
Requires=docker.service

[Service]
Type=simple
User=trainerops
Group=trainerops
SupplementaryGroups=docker
WorkingDirectory=/opt/trainer112
ExecStart=/usr/bin/python3 /opt/trainer112/deploy/ops_worker.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Save it as `/etc/systemd/system/trainer112-ops.service`, then run
`sudo systemctl daemon-reload` and `sudo systemctl enable --now trainer112-ops`.
Use a dedicated account in the Docker group only if its Docker-level privileges
are acceptable; Docker access is effectively host-administrator access.

For the example, provision `trainerops` with UID10001 (matching the Backend image)
before enabling the unit; check that this UID is not already assigned. Give this
account ownership of `deploy/operations` and `deploy/backups`, and read access to
the checkout and protected `.env.docker`. Atomic worker files are private to this
UID; running the worker as root while Backend is UID10001 prevents Backend from
reading them. Do not solve this by making the control directory world-writable.

On Windows, the repository provides `deploy/install_ops_task.ps1`. It installs the
current-user `Trainer112-Operations` task with an **At log on** trigger, `pythonw`,
three restart attempts, and starts it immediately. Run it only after Compose,
permissions and backup storage have been reviewed on the target host. The current
account must be able to use Docker Desktop and read the protected `.env.docker`.
The task keeps credentials out of command-line arguments.

## Verification

```powershell
python -m pytest deploy/test_ops_worker.py -q
python deploy/ops_worker.py --mock --once
```

The mock command writes runtime files below `deploy/operations/mock`; it performs no real
service operation or backup.

## Installed workstation (2026-09-15)

The current-user Windows task `Trainer112-Operations` remains installed for host
monitoring and service controls; it is no longer required for backups.
All five real Docker services appear healthy; CPU, memory and disk samples are
available through the Backend-mounted directory. The live Docker worker completed
both scheduled and UI-compatible manual encrypted backups; its heartbeat and
per-job results stayed visible while the host worker was not the backup executor.
Default schedule is00:00 UTC /03:00 Moscow. It requires Docker Desktop or a Docker
Engine host to be running; it does not require a logged-in Windows worker.
Failed scheduled backups retry after15 minutes. Existing backups are preserved.

The encrypted container bundle covers PostgreSQL, Voice recording/outbox volumes,
selected public deployment configuration and Backend/Voice/operations audit files.
Private deployment ENV files and all private keys are excluded and require a
separate protected recovery procedure. Monitoring notifications are
inside the admin page; no email/SMS destination is configured. Service controls
cover Voice/Asterisk only; SIP/DB deployment parameters are read-only in this
block. Full raw application log browsing, package updates, resource-limit editing
and Backend/PostgreSQL stop/restart remain host administration operations.

Focused checks:13 worker tests,2 Backend operations/RBAC tests,3 existing BFF tests,
Python/JavaScript syntax and secret-pattern/diff checks. No load run or SIP call.

## Administrator access reset

Passwords are stored as scrypt hashes and cannot be recovered. To hand full access to a
reviewer or restore a lost administrator, run on the server (Backend image built from this
revision or later):

```bash
docker compose --env-file .env.docker -f docker-compose.yml exec backend python reset_admin.py --username tech.admin
```

The command creates the administrator or sets a new generated password, re-activates the
account, restores the admin role, and clears lockout, MFA and old sessions. The password is
printed once; the audit log records `account.admin_created` or `account.admin_reset` without
it. To set a chosen password instead, pass `RESET_ADMIN_PASSWORD` through the environment
(`exec -e RESET_ADMIN_PASSWORD ...`), never on the command line. Hand the credentials over
through a protected channel and change the password after the review.
