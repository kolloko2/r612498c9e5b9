# Bounded Backend cluster profile

This opt-in profile runs two Backend containers behind HAProxy and coordinates
critical sections through the existing PostgreSQL database. It improves availability
when one Backend process/container fails. It is not full high availability: the
included PostgreSQL and HAProxy containers remain single points of failure.

## Live activation record — 2026-09-15

The local training stand was activated through `python deploy/manage.py --cluster
up` with the persisted directory + TLS profiles. The launcher completed its
two-phase sequence: one Backend reached healthy before the second replica was
created. Both Backend containers, `backend-lb`, PostgreSQL, Frontend, Voice,
Asterisk and the synthetic directory then reported healthy.

Eight CA-verified HTTPS health requests through `https://backend-lb:8000` returned
status `ok`, cluster coordination enabled, and two distinct Backend instance IDs.
The final Frontend image was recreated without dependencies and its running digest
matched the expected `a779eead...`; `https://127.0.0.1:3000/login` returned 200 with
CA and hostname verification. The saved deployment profile is
`directory=true,tls=true,cluster=true`. Voice still reports
`topology_verified=false`; Backend replica activation does not change or overstate
physical SIP/media verification. This is a live single-host functional check, not a
server-capacity benchmark or host-failure HA result.

The coordinator uses the application's existing PostgreSQL session for both the
advisory lease and protected writes. It must not be changed to a separate lock
connection unless every write also validates a persisted fencing token. A lost
database session is terminal for that Backend process; transparent reconnection
would violate the fencing guarantee and is therefore not supported.

## Application integration contract

The live Backend assembly implements these required hooks; keep them intact when
changing route construction or WebSocket ownership:

1. In `server.py`, create exactly one `Coordinator` after `store`:
   `coordinator = Coordinator.from_database(store.db)`, then wrap the Engine once
   with `engine = coordinate_engine(Engine(store), coordinator)`. Include
   `coordinator.status()` in the existing health response and execute `SELECT 1`
   there so HAProxy removes a replica whose fenced database session was lost.
2. Pass `coordinator` into the Workspace, generation, assessment and group-insights
   routers. Their process-local critical sections use these namespaces:
   `workspace-session/{sid}`, `lesson/{lid}`, `review/{sid}`, and
   `dialogue-session/{sid}`. Convert `LockUnavailable` and `ClusterUnavailable` to
   HTTP 503 without executing the protected operation.
3. In the Voice WebSocket endpoint, acquire `voice-connection/{sid}` with timeout 0
   before `accept()`, hold it for the complete socket lifetime, and close with 1013
   when busy/unavailable. The process-local `active` set remains a fast same-replica
   check; the distributed lease is authoritative across replicas.
4. Generation and other modules with read-modify-write process locks must use their
   own stable namespace before the profile can claim whole-Backend horizontal safety.
   Pure reads and single-statement inserts with database uniqueness constraints do
   not need a lease.
5. Install `install_global_mutation_guard(app, coordinator)` after creating the app.
   In cluster mode it serializes HTTP POST/PUT/PATCH/DELETE requests across replicas,
   except for an exact method/path allowlist whose handlers already hold audited
   `workspace-session` or `lesson` leases. Unknown and newly added routes
   stay guarded by default. Keep it enabled for this bounded profile.

Do not turn on `CLUSTER_ENABLED` before all mutation hooks above are integrated and
focused concurrency tests pass. A partial integration is worse than an explicit
single-replica deployment because it advertises unsafe capacity.

Example Workspace dependency:

```python
async def serialize_mutation(sid: UUID):
    try:
        async with coordinator.hold("workspace-session", str(sid)):
            yield
    except (LockUnavailable, ClusterUnavailable):
        raise HTTPException(503, "Учебная карточка занята или координация недоступна")
```

The lesson stop path currently acquires a lesson lock and then card locks. Preserve
that order everywhere to avoid distributed deadlocks. Never acquire two lesson
leases or reverse card/lesson order. The review provider call may be long; retain its
separate namespace so it does not block ordinary card saves.

The current fine-grained HTTP allowlist covers the exact card, message, service,
notification, processed, link, call/recover and student-finish session paths;
instructor feedback/session finish; student lesson next; and instructor lesson start/finish. Different
session or lesson IDs may therefore progress concurrently. Session creation,
AI review, rubric changes and all other mutations remain under the global guard
until their complete cross-endpoint aggregate scope is reviewed. AI review also
reacquires the session lease for its final reload/persist so concurrent feedback is
not overwritten. A
route added beneath an allowed prefix is not automatically allowed.

## Start and inspect

On an empty PostgreSQL database, bootstrap one Backend first. Router construction
contains multiple independent `CREATE TABLE IF NOT EXISTS` statements; two
concurrent first-time schema assemblers are not a migration system. After the first
Backend is healthy, start the profile and scale it to two:

```powershell
docker compose build
docker compose -f docker-compose.yml -f deploy/cluster/compose.yaml up -d postgres backend
docker compose -f docker-compose.yml -f deploy/cluster/compose.yaml up -d --scale backend=2
powershell -File deploy/cluster/verify.ps1
```

The overlay intentionally has no implicit replica count. Once the schema exists,
ordinary replica restarts may happen concurrently.

The script checks that two Backend containers exist and that Frontend reaches the
load-balanced health endpoint. It makes no mutation and does not prove failover.

For an intentional test-only replica failure, use:

```powershell
powershell -File deploy/cluster/verify.ps1 -ExerciseReplicaFailure
```

That option resolves one exact Backend container, stops it, verifies a new request
through the surviving replica, and restarts the stopped container in `finally`.
Run it only on the synthetic training stand. It does not test in-flight request or
WebSocket continuity; those connections are expected to break and reconnect.

### Cluster with the opt-in TLS profile

The TLS cluster requires a separate CA-issued `backend-lb` leaf. Issue it offline
with the TLS provisioner's bounded leaf command before startup. Runtime HAProxy
mounts only `backend-lb.cert.pem`, `backend-lb.key.pem`, and the public CA; it never
mounts `ca.key.pem`. Apply the overlays in this order so the cluster-specific URLs
win over both base overlays:

```powershell
python deploy/tls/issue_leaf.py --service backend-lb --dns-san backend-lb
docker compose -f docker-compose.yml -f deploy/cluster/compose.yaml -f deploy/tls/docker-compose.tls.yml -f deploy/cluster/compose.tls.yaml --profile tls up -d postgres backend
docker compose -f docker-compose.yml -f deploy/cluster/compose.yaml -f deploy/tls/docker-compose.tls.yml -f deploy/cluster/compose.tls.yaml --profile tls up -d --scale backend=2
```

HAProxy terminates client-side TLS with the `backend-lb` certificate, then creates
a separate verified TLS connection to each Backend. The upstream certificate must
chain to `ca.cert.pem` and match DNS name `backend`; neither certificate verification
is disabled. Frontend connects to `https://backend-lb:8000`, Voice uses
`wss://backend-lb:8000`, and both already trust the same public development CA.
Do not use the plain cluster overlay by itself when the other internal services run
under the TLS overlay.

## Acceptance checks

- Concurrent saves for one session serialize across two Backend containers; a stale
  revision receives 409 rather than overwriting the first save.
- Concurrent lesson-next calls issue at most one next card for a student/request.
- Two simultaneous Voice socket attempts for one session result in one accepted
  socket and one 1013 response.
- After killing the socket-owning Backend, Voice reconnects within its configured
  budget and a pending event is acknowledged exactly once.
- Lock/database outage produces 503/1013 and no unlocked write.
- Requests for different session IDs proceed concurrently.

## PostgreSQL standby rehearsal

`compose.ha-rehearsal.yaml` is a destructive, test-only failover drill with new
Compose volumes. It never attaches `postgres-data`. Use the fixed project name
`trainer112-ha-rehearsal`; the promotion script refuses any other project. The
profile provides:

- PostgreSQL 16 physical streaming replication with a physical slot;
- `synchronous_commit=remote_apply` and one named synchronous standby;
- TLS 1.2+ and `sslmode=verify-full` for Backend and replication traffic;
- a stable TCP writer endpoint named `postgres`, with standby disabled initially;
- explicit fencing, replay-position validation, promotion and durable proxy state;
- an AES-256-GCM CMS-encrypted pre-promotion `pg_dump` in a separate rehearsal
  backup volume. Recovery needs the matching old primary leaf private key.

Generate a distinct rehearsal replication password and keep the existing TLS leaf
files outside containers. The two database leaves have SANs for their direct service
name and the stable `postgres` endpoint. Issue them offline if they do not exist;
the bounded issuer refuses overwrite:

```powershell
python deploy/tls/issue_leaf.py --service pg-primary --dns-san postgres
python deploy/tls/issue_leaf.py --service pg-standby --dns-san postgres
```

Bootstrap the empty schema with one Backend,
then scale to two. Apply the overlays in exactly this order:

```powershell
$env:POSTGRES_REPLICATION_PASSWORD = '<separate strong rehearsal secret>'
docker compose --project-name trainer112-ha-rehearsal `
  -f docker-compose.yml -f deploy/cluster/compose.yaml `
  -f deploy/tls/docker-compose.tls.yml -f deploy/cluster/compose.tls.yaml `
  -f deploy/cluster/compose.ha-rehearsal.yaml --profile tls `
  up -d --build pg-primary pg-standby postgres backend
docker compose --project-name trainer112-ha-rehearsal `
  -f docker-compose.yml -f deploy/cluster/compose.yaml `
  -f deploy/tls/docker-compose.tls.yml -f deploy/cluster/compose.tls.yaml `
  -f deploy/cluster/compose.ha-rehearsal.yaml --profile tls `
  up -d --scale backend=2
```

Confirm `pg-primary`, `pg-standby`, `postgres`, both Backend replicas and
`backend-lb` are healthy. Promotion is intentionally explicit:

```powershell
powershell -NoProfile -File deploy/cluster/promote-standby.ps1 `
  -Confirm PROMOTE-REHEARSAL-STANDBY
```

The script checks `streaming:sync`, stops Backend/Frontend/Voice writers, removes
the primary from the stable endpoint, creates the encrypted dump, checkpoints and
waits until replay reaches the primary flush LSN. Only then does it stop and verify
the old primary container, promote the standby, persist the HAProxy server state,
clears the inherited synchronous-standby requirement for the degraded single-writer
state, and restarts application writers. If a failure occurs before fencing it
restores the old endpoint. After fencing it never restarts the former primary
automatically.

To inspect an encrypted dump without applying it, copy it out of the named backup
volume and decrypt with the corresponding archived primary leaf and key:

```powershell
openssl cms -decrypt -binary -inform DER -in pre-promotion.dump.cms `
  -recip deploy/tls/certs/pg-primary.cert.pem `
  -inkey deploy/tls/private/pg-primary.key.pem -out recovered.dump
pg_restore --list recovered.dump
```

Delete plaintext `recovered.dump` after the authorized restore check and safeguard
the old leaf key for the backup retention period. Certificate rotation without key
retention makes these rehearsal backups unrecoverable.

This drill does not rejoin the old primary. Its timeline diverges after promotion;
starting it again can create split brain. Destroy only the fixed rehearsal project
and its volumes when finished, after retaining any required encrypted dump:

```powershell
docker compose --project-name trainer112-ha-rehearsal `
  -f docker-compose.yml -f deploy/cluster/compose.yaml `
  -f deploy/tls/docker-compose.tls.yml -f deploy/cluster/compose.tls.yaml `
  -f deploy/cluster/compose.ha-rehearsal.yaml --profile tls down --volumes
```

The rehearsal was exercised on the local Docker host: the standby reported
`streaming:sync`, replay reached the captured LSN, the old primary was stopped,
`pg_ctl promote` completed, and the stable proxy selected the promoted node. This
proves the script path on one host, not independent failure-domain availability.

## What full automatic failover still requires

Automatic PostgreSQL failover cannot be established by adding a second container.
A production design needs, at minimum, multiple database nodes across independent
failure domains, a third quorum/voting member or managed consensus control plane,
fencing against split brain, synchronous/asynchronous replication and explicit data
loss objectives, backup restore drills, TLS, monitoring, and a stable writer endpoint.
Those choices depend on the target infrastructure and require a separate ADR.

Likewise, a single Compose host cannot prove host-level HA: losing Docker, storage,
the host network, HAProxy or the PostgreSQL volume stops this profile. The included
failure drill establishes only Backend-replica failover for new requests.

The included rehearsal standby shares the same host failure domain. It is not a
production HA controller, does not detect failures, does not decide whether a node
is safe to promote, and does not automatically rewind/rejoin the old primary.
Patroni/repmgr on two containers would not create a quorum or independent fencing,
so neither is presented as automatic HA here.

Implementation references: the Docker Compose CLI supports explicit service
scaling and replicated services, while HAProxy 3.0 documents `server-template`, DNS
resolvers, HTTP health checks and tunnel timeouts in its configuration manual:

- https://docs.docker.com/reference/cli/docker/compose/scale/
- https://docs.docker.com/reference/compose-file/services/#scale
- https://docs.haproxy.org/3.0/configuration.html
