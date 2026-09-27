# Docker deployment: PostgreSQL and training SIP

This block packages Frontend, Backend, Voice and Asterisk as separate images in
one Compose project. PostgreSQL 16 is the deployment database. The LLM is served
by a local Ollama on the host (`OLLAMA_URL`, current standard profile `qwen3:8b`) —
this is the isolated-contour acceptance configuration. OpenRouter is retained as a
development code path only; it requires Internet and is off in delivery.

## Prepare and start

Use Docker Engine + Compose on Linux or Docker Desktop in Linux-container mode
on Windows. The first build downloads packages and Asterisk source. Run from the
repository root:

```text
python -m pip install python-dotenv
python deploy/prepare.py
python deploy/manage.py build
python deploy/manage.py up
python deploy/manage.py status
```

`prepare.py` creates ignored `.env.docker`, generates separate random service
credentials and unique phone passwords for 201–220, and copies only the selected
OpenRouter settings from the existing `.env`. It refuses to overwrite an existing
deployment configuration. Never share `.env.docker`, `docker inspect` environment
output, or expanded `docker compose config`; use `config --quiet` to validate.
Protect the file with host user permissions. Secrets are runtime ENV, not build
arguments or image layers. The original `.env` and SQLite database stay unchanged.

The application opens at http://127.0.0.1:3000. Stop the old native launcher before
starting the container frontend, otherwise port 3000 is occupied. Backend :8000,
Voice :8001, PostgreSQL :5432 and Asterisk ARI :8088 are internal only. Only web
:3000, SIP UDP :5060 and RTP UDP :20000–20199 are published to loopback by default.
No real emergency calls, trunks, PSTN or endpoint-to-endpoint dialing are configured.

## Preserve existing data

Do not bootstrap an empty administrator if migrating existing accounts. Stop the
old Backend and make a consistent SQLite backup first. Initialize the destination
schema by starting the new Backend once, then stop it before copying. The migration
tool `tools/migrate_sqlite_to_postgres.py` has dry-run and explicit `--apply` modes;
it rejects a destination containing application data and verifies copied counts.
See Backend README for its exact invocation. Keep the original SQLite and backup
until the migrated accounts, cards, attachments and reports have been accepted.

Container migration example after stopping the native launcher (substitute an
absolute host path for `<snapshot>`; the Docker backend must not be running):

```text
python deploy/snapshot_sqlite.py --out deploy/backups/migration.sqlite3
docker compose --env-file .env.docker up -d postgres
docker compose --env-file .env.docker run --rm --no-deps backend python -c "import server"
docker compose --env-file .env.docker run --rm --no-deps -v "<snapshot>:/import/source.sqlite3:ro" backend python /srv/tools/migrate_sqlite_to_postgres.py /import/source.sqlite3
docker compose --env-file .env.docker run --rm --no-deps -v "<snapshot>:/import/source.sqlite3:ro" backend python /srv/tools/migrate_sqlite_to_postgres.py /import/source.sqlite3 --apply
```

The snapshot utility includes committed WAL contents and changes journal mode only
on its backup copy, so the copy works as a standalone read-only Docker mount. It
does not modify the original database. Avoid copying only a live SQLite main file.

PostgreSQL data, recordings and outbox use named volumes and survive rebuilds.
`python deploy/manage.py stop` stops services without deleting volumes. Never use
`docker compose down -v` for routine updates: it deletes those persistent volumes.

## Register a training phone

Use MicroSIP or another SIP client on the host; the softphone is not included in
the server image. Server/domain: `127.0.0.1`, port `5060`, transport UDP, username
and authentication ID: one assigned extension (201–220), password: that extension's
value in `.env.docker` `SIP_ACCOUNTS_JSON`. Use PCMU/PCMA and a headset. There are no
default/public passwords. Each phone must use a distinct extension.

On this workstation, official portable MicroSIP Lite3.22.16 was extracted to
`tools/windows-training/MicroSIP` and configured for201 with auto-answer OFF.
Launch its MicroSIP.exe (or restore it from the system tray). This directory is
excluded from Git and image build contexts. Unlike server ENV, the phone needs its
own private account INI: protect it as a credential file. The setup utility
`python deploy/configure_microsip.py` only creates a NEW INI and refuses overwrite.
It does not change existing installed softphones, Windows startup, firewall or
certificate trust. Download source: [official MicroSIP downloads](https://www.microsip.org/downloads).
Windows reported an untrusted certificate root for the distribution's signature;
no certificate trust or security protection was disabled or modified.

Check contacts without exposing credentials:

```text
docker compose --env-file .env.docker exec asterisk asterisk -rx "pjsip show contacts"
```

For classroom phones, set SIP_BIND_ADDRESS to the host's private LAN IP and
SIP_EXTERNAL_ADDRESS to the SAME routable host IP. Set WEB_BIND_ADDRESS and exact
ALLOWED_ORIGINS explicitly for web access. Do not expose these development ports
to the Internet. SIP_LOCAL_NET describes the container-side network, not the
phone LAN; see deploy/asterisk/README.md for NAT details. Windows firewall changes
are not automated. Test actual two-way audio on the target network.

## Voice readiness is not inferred

Fresh preparation defaults to `PIPELINE_MODE=spike`, `TOPOLOGY_VERIFIED=false`:
real SIP/ARI infrastructure with a test-tone media probe. The current workstation
has installed Vosk/GigaAM/Silero models and uses verified conversation mode.
A fresh host must receive the model files and pass the media check before enabling
conversation mode. Mock STT/TTS are never described as real speech recognition.

Follow the live media spike in voice/README.md, using the internal Voice URL from
within the Compose network. Only after capture/playback isolation and headset audio
are verified set `TOPOLOGY_VERIFIED=true` and `PIPELINE_MODE=conversation`.
To reuse Vosk/Piper adapters, install their optional dependencies by setting
INSTALL_LOCAL_PROVIDERS=true and rebuilding Voice; place licensed model files in
ignored deploy/models, then set STT_MODEL/TTS_VOICE to their `/models/...` paths and
select the actual providers. These speech adapters are separate from a local LLM.
No models download at startup, and no local LLM container is present.
The reproducible public-data map package is likewise outside images and Git. Copy
the ignored `deploy/maps` directory alongside `deploy/models` when moving a prepared
stand to another host; Backend mounts it read-only at `/data/maps`. Encrypted user
backups intentionally exclude this rebuildable map package.

After registering phone 201, start the manual headset probe from the container:

```text
docker compose --env-file .env.docker exec voice python tools/media_spike.py --out /data/recordings/spike-results
```

This intentionally rings the registered training phone. Answer with a headset;
it does not call any external number. A bounded registration-only smoke test from
the host is `python tools/sip_registration_probe.py`: it uses spare extension220,
skips an existing contact and unregisters its test contact afterwards.

## Operations and limits

```text
python deploy/manage.py up
```

The supported clean-machine launcher defaults to the TLS profile and starts the
Docker-native backup worker. It fails early until `deploy/prepare.py` has created
the ENV-only recovery key and the TLS artifacts have been prepared. Use `--plain`
only for an explicit development stand; that profile has no backup executor.
The worker connects directly to PostgreSQL with certificate and hostname
verification, creates an authenticated AES-256-GCM `.t112` archive every enabled
UTC day, and includes the database, recordings, outbox, selected public
configuration and audit files. It never mounts the Docker socket, root CA private
key, database ENV file, model weights or rebuildable map package. Keep a protected
off-machine copy of both archives and the separately stored recovery key, and test
restores into an empty database. `python deploy/manage.py backup-db` remains a
manual database-only diagnostic; it is not the daily recovery artifact.

Health checks and restart policies cover process startup/restart, not failover to
another physical node. Backend and Voice deliberately retain ONE worker each:
their session/call locks live in process memory. PostgreSQL does not alone make
the application horizontally safe. TLS and scheduled encrypted backups are provided
by the secure profile; six-month security-log retention and target-server load/audio
acceptance remain separate deployment work.
Container console logs rotate at 5 × 10 MiB; this is NOT six-month audit retention.
Original application audit records remain in the database.

To transport prebuilt images, run `python deploy/manage.py export-images` (archive
under output/docker), or use `docker image save -o` for trainer112-backend:dev,
trainer112-frontend:dev, trainer112-voice:dev, trainer112-asterisk:dev and
postgres:16-bookworm; load on the destination with `docker image load -i`. Supply
Compose, a private runtime ENV and any model assets separately. Image archives do
not contain database volumes or existing classroom data.

Asterisk media requirements follow the official [WebSocket driver documentation](https://docs.asterisk.org/Configuration/Channel-Drivers/WebSocket/).

## Verified on this Windows workstation, 2026-09-15

- Backend and Frontend images built and running with healthy PostgreSQL16.
- Migrated 20 application tables / 62 rows; preserved 5 users and 8 cards.
- Kept the original SQLite database, a verified standalone pre-migration snapshot,
  and a PostgreSQL custom-format dump in deploy/backups.
- Backend local suite: 146 passed, 3 opt-in PostgreSQL tests skipped. Focused real
  PostgreSQL suite: 5 passed (application workflow, attachments, transactions,
  migration). This is not a claim that all 146 tests ran against PostgreSQL.
- Deployment preparation/snapshot, portable phone and frontend origin/cookie checks: 7 passed.
- Existing accounts remain stored; bootstrap_required=false through the live BFF.
- OpenRouter configuration was carried over server-side; no paid LLM request was
  made for deployment verification. No local LLM installed.

Load, physical headset audio, voice recognition and target-server acceptance are
not established by these deployment checks. See the separate SIP verification below.

### SIP and image export

The later technical administration block adds `/operations` and a host operations
worker (see OPERATIONS.md). The archive named below is the earlier SIP/PostgreSQL
snapshot and does NOT contain these later application changes. Rebuild/export
again before transporting the updated application.

Asterisk22.11.0 and Voice are running healthy in the root Compose. MicroSIP201
registered with the actual Asterisk and reports an available contact. Voice health
reports `telephony_mode=asterisk`, `pipeline_mode=spike`, `active_calls=0`.
RTP20000–20199 was chosen because a host NVIDIA service occupied port10012 in the
initial range; no unrelated service was stopped. SIP remains127.0.0.1:5060.

The earlier portable archive is `output/docker/trainer112-20260915T152036060406Z.tar`
(382866944 bytes), containing all four application images and PostgreSQL16. It
does not contain `.env.docker`, the portable phone credentials, database volumes,
recordings, model files or the regional map package. Copy Compose/scripts,
`deploy/models`, `deploy/maps` and a protected runtime configuration separately;
load the archive using `docker image load -i <archive>`.

The Digest registration probe for extension220 passed and unregistered cleanly;
MicroSIP201 remained registered. Renderer and probe unit tests:11 passed.

The headset test has NOT been performed. Registration and healthy ARI do not prove
RTP audibility, echo isolation, STT/TTS quality, 20 concurrent calls or the latency
requirements. `TOPOLOGY_VERIFIED` remains false; no real emergency network is used.

### Current export after regional map, backup and voice fixes

The current archive is `output/docker/trainer112-20260915T175518518901Z.tar`
(776359936 bytes). It includes the updated application and backup-worker images.
The regional map package (`deploy/maps/regional.sqlite`), speech models
(`deploy/models`), Compose files/scripts and protected runtime configuration must
be transferred separately; they are not embedded in the image archive.

Daily backups now run in the Docker backup-worker, independently of the Windows
scheduled task. The latter remains responsible for host monitoring/control only.
The regional package covers Moscow and Moscow Oblast with 3160639 features and
778016 address records. Runtime map requests do not require Internet access.

The automatic extension 220 call passed the complete Vosk → Backend/OpenRouter →
Silero cycle over TLS/SRTP. `TOPOLOGY_VERIFIED=true` is now enabled for the existing
allowlist. This supersedes the earlier spike-mode state above; physical headset
acceptance remains separate. See `voice/docs/VERIFICATION.md` for evidence.
