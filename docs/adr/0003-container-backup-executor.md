# ADR-0003: Docker-native backup executor

- Status: accepted
- Date: 2026-09-15

## Context

Daily backups were scheduled by a Windows logon task. That made data protection
depend on an interactive user session and mixed Docker administration with backup
execution. The admin UI already persists schedule settings and manual job requests
in `deploy/operations`.

## Decision

Add one deployment-helper container, `backup-worker`, to the TLS Compose profile.
It has no API and no Docker socket. It uses `pg_dump` directly over PostgreSQL TLS
with CA and hostname verification, reads Voice volumes and selected configuration/
audit mounts, and emits only AES-256-GCM authenticated archives. Its encryption key
is supplied as an environment variable from an ignored host file and is never
mounted or archived.

The worker owns scheduled and manual backup actions through a durable ownership
marker and atomic request claims. It writes per-job results and scheduler heartbeat
state; Backend merges those files into the existing operations response. The host
worker remains responsible for host metrics, Docker service inventory and explicit
Voice/Asterisk controls, but disables backup execution while container ownership is
configured.

The worker refreshes its heartbeat while long backup work is active and offers an
explicit `mock` executor mode that exercises scheduling/claims without PostgreSQL,
the encryption key or archive output. On Linux it initializes ownership only for
its two state mounts and then drops to the application's fixed UID. The supported
launcher defaults a clean installation to TLS and fails on missing prerequisites;
plain development is an explicit opt-out.

## Consequences

- Backups run whenever the TLS Compose deployment is running, independent of a
  Windows login task.
- Manual backup remains available when host monitoring is absent; Docker service
  controls correctly remain unavailable/stale.
- PostgreSQL dump consistency does not make the separately read Voice/config/audit
  file snapshot cross-service atomic; the archive manifest says so.
- Backup storage and scheduler state are host bind mounts and need capacity/
  permission monitoring. There is no automatic retention deletion.
- Losing the separate encryption key makes archives unrecoverable.
- Historical archives are not rewritten or automatically deleted and may reflect
  the older host helper's broader configuration include set.

## Rejected alternatives

- Mounting `/var/run/docker.sock`: grants host-equivalent control and is unnecessary.
- Keeping Windows Task Scheduler as the backup scheduler: fails the unattended
  Docker deployment requirement.
- Running backup inside Backend: mixes privileged filesystem/data-protection work
  with the public application lifecycle and multiplies schedulers across replicas.
