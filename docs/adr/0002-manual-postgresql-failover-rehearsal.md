# ADR-0002: Fenced manual PostgreSQL failover rehearsal

- Status: accepted for an isolated test profile
- Date: 2026-09-15

## Context

The bounded Backend cluster still depends on one PostgreSQL writer. A second
container alone is not automatic HA: it cannot reliably distinguish a failed node
from a network partition, elect a writer, or prevent both sides accepting writes.
The available Compose host also provides no independent failure domains or external
fencing device. The project prohibits introducing a new service topology without
an architecture decision.

## Decision

Provide an opt-in PostgreSQL 16 physical streaming standby solely for a controlled
operator rehearsal. Its volumes and project namespace are distinct from the normal
deployment. Replication is synchronous with `remote_apply`; direct replication and
stable-endpoint application connections use CA-verified TLS with hostname checks.
The stable endpoint is a TCP HAProxy whose standby starts disabled and therefore
cannot auto-promote or accidentally receive writes while in recovery.

Promotion requires an explicit confirmation token. The procedure first stops all
application writers and removes the primary from the endpoint. It confirms a named
synchronous streaming receiver, takes an authenticated AES-256-GCM CMS-encrypted
logical backup, checkpoints, and waits for the standby replay LSN to reach the
primary flush LSN. It then stops the exact old-primary container and verifies its
state before calling `pg_ctl promote`. Only after PostgreSQL confirms recovery has
ended does the procedure clear the promoted node's inherited synchronous-standby
requirement and switch the stable endpoint. Clearing it is required in the degraded
one-node state; otherwise new commits would wait forever for the former standby.
HAProxy's runtime server state is persisted so a proxy restart does not silently
select the fenced primary.

The former primary is never restarted or rejoined by automation. A failed operation
before fencing may restore the old endpoint; a failed operation after fencing stays
stopped for operator inspection.

## Consequences

- Planned promotion has an application outage while writers are fenced.
- With one required synchronous standby, commits block when it is unavailable. This
  favors a zero-observed-loss rehearsal over write availability.
- The single Docker host, proxy and local storage remain common failure domains.
- `pg_dump` is a logical pre-promotion safety copy, not continuous WAL archiving or
  point-in-time recovery. Existing backup/restore drills remain necessary.
- Encrypted rehearsal dumps require retention of the matching leaf private key.
- After promotion the topology is degraded to one writer. Re-establishing a standby
  requires an explicitly reviewed rebuild or `pg_rewind` procedure.

## Rejected alternatives

- Automatic promotion on failed health check: unsafe under partitions and cannot
  fence a still-running old primary.
- Patroni/repmgr with only these two same-host containers: no independent quorum or
  credible host fencing, so it would only imitate automatic HA.
- Reusing the live PostgreSQL volume: risks the deployed data and makes the exercise
  destructive outside its stated scope.
- Plaintext network replication or `sslmode=require`: encryption without CA and
  hostname verification does not protect against a spoofed server.

Primary references: PostgreSQL 16 documents synchronous standby selection,
`sslmode=verify-full`, explicit `pg_ctl promote`, the post-failover degraded state,
and the need to recreate or rewind a new standby:

- https://www.postgresql.org/docs/16/runtime-config-replication.html
- https://www.postgresql.org/docs/16/libpq-connect.html
- https://www.postgresql.org/docs/16/warm-standby-failover.html
