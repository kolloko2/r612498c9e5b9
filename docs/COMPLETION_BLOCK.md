# Completion block: exact delivered boundaries

## Administration and interchange

`/operations` edits allowlisted SIP addresses/extensions, MAX_CALLS, PostgreSQL
max_connections/shared_buffers and Backend/Voice CPU/memory limits. Secrets are
ENV-only and never returned. Applying changes is an explicit queued maintenance
action, with a restart warning, not hot reconfiguration. The host worker repeats
validation, edits only known keys and waits for container health. Failed startup
is reported as failed; a saved ENV is not silently described as rolled back.

XML configuration uses `trainer112-settings version="1"` with `<setting key="…">`.
Import is a preview first; confirmation submits the same validated settings.
DTD/entities, unknown keys, duplicates and oversized documents are rejected.
The earlier sanitized diagnostic XML export remains read-only and is a different
format. No claim of compatibility with an unspecified legacy XML schema is made.

Service logs are bounded tails of the enabled Compose services, scrubbed against known
ENV credentials and common credential patterns, stored in the protected operations
folder. They are admin-only. Application/security audit is the long-term journal;
rotated container logs are not promised six-month retention.

Offline updates require a host-approved `deploy/updates/approved.json` containing
`{"sha256":"<64 lowercase hex>"}` and `<sha256>.tar` in that same directory.
The host administrator must inspect/trust the images and expected tags before
approval. A checksum detects alteration, not malicious code or publisher identity.
The worker verifies SHA256, completes a backup, loads images and recreates with
`--no-build`. No browser file upload, arbitrary image name, shell or registry URL
is accepted. Failed startup requires host recovery; automatic rollback is not claimed.

## Backups and audit

Install host requirements, run `python deploy/full_backup.py prepare-key`, and keep
the recovery key separately protected. Existing credentials/keys are not replaced.
Each manual/daily backup keeps PostgreSQL `.dump` plus `full-*.t112` (AES-256-GCM).
The encrypted bundle contains DB, recordings/outbox, service certs/keys, ENV config
and security/operations logs. Root CA signing key, recovery key and model weights
are excluded. This is an online copy: a currently recorded file can end at copy
time; it is not an atomic cross-service snapshot. For a release snapshot, finish
lessons and stop accepting calls first. Backups remain on this host until explicitly
copied to separate storage. No automatic deletion is configured.

Recover an archive (never overwrites an existing destination):

```
python deploy/full_backup.py decrypt --source deploy/backups/full-<timestamp>.t112 --output deploy/backups/recovered.tar.gz
```

Authentication is verified before publishing plaintext. The command does not
extract files or overwrite a running DB. Restore only a trusted bundle in a
maintenance window; retain the original volumes until recovery is accepted.

Backend audit now includes HTTP and WS connection metadata, not bodies, credentials,
audio or every unsaved browser click. Voice audit has the same metadata boundary.
The retention floor remains 183 days and automatic deletion stays disabled.

## ARM, documents and map

VIS is an explicitly labelled training delivery, never an external emergency
dispatch. A saved registered assigned card is snapshotted into its teacher's DDS
journal; retries use message_id, and opening is audited. Profile filters enforce
teacher ownership. Informational recipients require explicit synthetic labels;
the source does not identify all official hidden recipients. See ARM_COVERAGE.md.

Passing completed attempts can export a Russian PDF training certificate from
`/assessment`; ownership and current effective grade are checked server-side.
It is not a qualification or an authorization to work in emergency services.
The exported identifier fingerprints the attempt/current verdict; a prior PDF
cannot be recalled if a teacher later revises the grade.

The regional package now contains 3,160,639 OSM features and 778,016 address records
from complete Moscow and Moscow Oblast extracts. The former centre-only JSON is
superseded. The read-only SQLite package has spatial and full-text indices; only
the viewport and search candidates reach the browser. Attribution remains visible.
Pan/zoom and address search work without Internet, including before card coordinates
are set. Selected coordinates require operator confirmation and a normal card save.
See `docs/MAPS.md` for reproducible preparation, transfer and source coverage.

## Cluster and transport

See TLS.md and CLUSTER.md. The bounded Backend cluster serializes HTTP mutations
through PostgreSQL advisory locks on the same connection used for writes; reads
and new connections can use surviving replicas. No transparent DB reconnect is
allowed after a lease connection is lost. This is Backend failover/read scaling,
not increased write throughput or full multi-host disaster recovery.
Monitoring includes enabled directory/LB services and aggregates every Backend
replica; fewer than the expected two healthy replicas is degraded, not healthy.

Backend sends `backend.ack` only after durable event handling and any reply. Voice
keeps the original event UUID pending until that ACK and replays in order on
reconnect. This does not resurrect a hung-up SIP dialog or audio lost before receipt.
While the student screen remains open, observed transport failures can trigger a
bounded automatic redial (three attempts/30 seconds), keeping the same card and
transcript. Ordinary hangup/teacher stop never redial; recordings are separate per
phone attempt. Unknown/missing Voice state requires intervention rather than guessing.
The physical headset/SIP endpoint is still part of end-to-end acceptance, not
something a source-code check can establish.
