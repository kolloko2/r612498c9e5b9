# Opt-in encrypted deployment overlay

## Status

`deploy/tls/docker-compose.tls.yml` is a complete, explicitly selected TLS overlay.
It is activated on the current workstation with the cluster overlay. The root
Compose file alone remains the plain development deployment. Selecting the overlay is not enough: every service is also
guarded by the `tls` profile.

The overlay encrypts and verifies these paths:

- browser -> Frontend: HTTPS, secure cookies and exact HTTPS origins;
- Frontend -> Backend and Backend -> Voice: HTTPS with CA and hostname verification;
- Voice -> Backend control: WSS with CA and hostname verification;
- Voice -> Asterisk ARI/events: HTTPS/WSS with CA and hostname verification;
- Asterisk -> Voice media WebSocket: WSS with CA and hostname verification;
- Backend -> PostgreSQL: TLS with `sslmode=verify-full`, service hostname and CA;
- training phone -> Asterisk: SIP TLS on TCP 5061 and mandatory SDES-SRTP media.

The TLS Asterisk configuration contains no UDP SIP transport. The overlay replaces
the published SIP ports, so 5060/UDP is not published. RTP/SRTP still uses the
configured UDP media range; encryption is enforced on each endpoint with
`media_encryption=sdes` and `media_encryption_optimistic=no`.

TLS is transport security, not authorization and not permission for Internet
exposure. Keep the deployment on the private training LAN. No real 112 data is
permitted in testing.

## Certificate boundary

The already-issued set in `deploy/tls` contains separate certificates for Backend,
Frontend, Voice, PostgreSQL, Asterisk and the optional synthetic directory, plus a
later offline-issued `backend-lb` leaf for the optional cluster overlay. No original
certificate was reissued. The HA rehearsal also has distinct `pg-primary` and
`pg-standby` leaves; each includes its own service name and stable DNS SAN
`postgres`. Do not rerun issuance against these paths; both issuers deliberately
refuse overwrite.

Only the public `ca/ca.cert.pem` and each service's own key/certificate are mounted.
The CA private key is never mounted. A short root bootstrap copies the service key
from the read-only host mount to `/run/trainer-tls`, applies mode `0600`, changes
ownership, drops all root privileges to the image's normal user, and then starts the
service. PostgreSQL has its own equivalent entrypoint because its server key must be
owned by `postgres` and mode `0600`.

Do not commit, include in an image, log, or distribute `private/ca.key.pem`. On
Windows, restrict `deploy/tls/private` with ACLs; POSIX mode bits are not a Windows
access-control guarantee.

Before activation, confirm the Frontend and Asterisk certificates contain the exact
DNS names or IPs used by browsers and phones. The current generated set contains
only each Compose service name, `localhost` and `127.0.0.1`; therefore it supports
same-workstation browser/MicroSIP acceptance but **not LAN clients addressed by the
host's LAN IP**. The manifest is the source of truth. For LAN access, activation is
blocked until a reviewed rotation with the real LAN DNS/IP is staged in a separate
location. Do not bypass warnings and do not overwrite this certificate set.

## Offline validation (does not start containers)

Docker Compose 2.24.4 or newer is required because the overlay uses `!override` to
remove the UDP SIP publication and replace image commands.

```powershell
python -m unittest discover -s deploy/asterisk/tests -v
python -m unittest discover -s deploy/tls/tests -v
python deploy/tls/validate_overlay.py
```

The validator renders the merged Compose model and rejects downgrade URLs,
`sslmode=require`, UDP 5060, missing TCP 5061 and any CA-private-key mount. It uses
safe placeholder bind/origin values only for rendering. It does not start or alter
the deployment.

Building images is also non-activating:

```powershell
$env:TLS_ALLOWED_ORIGINS = 'https://localhost:3000'
$env:SIP_BIND_ADDRESS = '127.0.0.1'
docker compose --env-file .env.docker -f docker-compose.yml -f deploy/tls/docker-compose.tls.yml --profile tls build
```

## PostgreSQL enforcement

The derived PostgreSQL image copies the mounted key into a private runtime directory
with the ownership required by PostgreSQL. `ssl=on`, minimum TLS 1.2 and a dedicated
HBA file are passed directly to the server. TCP `hostssl` entries require SCRAM;
explicit `hostnossl ... reject` entries deny plain TCP. Backend uses host `postgres`,
`sslmode=verify-full` and the mounted public CA.

Its health check authenticates with `psql`, verifies the PostgreSQL hostname and CA,
runs a query, and confirms the current backend appears in `pg_stat_ssl`. It does not
use `pg_isready` as a substitute for a verified TLS session.

## Asterisk enforcement

With `ASTERISK_TLS_ENABLED=true`, the renderer fails closed unless all three TLS
files exist and the media URL is `wss://`. It produces:

- Asterisk HTTPS on 8089 with its own certificate and private key; its required
  plain HTTP listener is restricted to container loopback and is unreachable to
  Voice or other containers;
- PJSIP TLS only on 5061/TCP (minimum configured method TLS 1.2);
- endpoints fixed to that TLS transport with required SDES-SRTP;
- outbound media WSS with `tls_enabled=yes`, the private CA, server-certificate
  verification and hostname verification.

Voice connects to `https://asterisk:8089/ari`; its existing verified client TLS
support also covers the ARI events WebSocket. The Asterisk health check verifies the
service certificate as hostname `asterisk`, authenticates an HTTPS ARI request, and
checks the required modules including `res_srtp`.

These settings follow the official Asterisk 22 samples for
[HTTP TLS](https://github.com/asterisk/asterisk/blob/22/configs/samples/http.conf.sample),
[PJSIP](https://github.com/asterisk/asterisk/blob/22/configs/samples/pjsip.conf.sample),
and the [WebSocket client](https://github.com/asterisk/asterisk/blob/22/configs/samples/websocket_client.conf.sample).

## Managed-client prerequisites

Activation is blocked until an administrator completes both client-side items:

1. Install only `deploy/tls/ca/ca.cert.pem` in the trust store of each managed
   training workstation. Never install or copy the CA private key. Do not allow
   browser certificate-warning exceptions.
2. Configure each MicroSIP account for TLS to a certificate-matching host on port
   5061 (with the current set, `127.0.0.1:5061` on the same workstation) and enable
   mandatory SRTP (SDES). The precise UI labels vary by MicroSIP version. Confirm
   that its TLS implementation validates the server certificate against the managed
   trust store. If this cannot be established for the deployed version, activation
   remains blocked.

This repository does not change Windows trust, MicroSIP configuration, or firewall
state as part of normal startup: those are administrator/user-authorized actions.
After trust installation is separately authorized, the existing portable extension
201 can be backed up and switched without printing its password:

```powershell
tools/windows-training/MicroSIP/MicroSIP.exe /exit
python deploy/configure_microsip.py --extension 201 --tls-existing
tools/windows-training/MicroSIP/MicroSIP.exe
```

The backup is `MicroSIP.ini.pre-tls.bak`; an existing backup blocks a second
non-idempotent migration. The helper keeps auto-answer disabled. Current MicroSIP
source was found to create a TLS transport but not to set an explicit
`verify_server` option. Therefore OS CA installation and an encrypted TLS session do
not, by themselves, prove that this client validates Asterisk's certificate or
hostname. Treat verified SIP server identity as an open acceptance blocker unless a
packet/log/source-backed test for the deployed binary establishes it.

## Coordinated activation gate

Before the first live run:

- back up PostgreSQL and stop all exercises/calls;
- set `TLS_ALLOWED_ORIGINS` to comma-separated exact HTTPS origins represented in
  the Frontend certificate, and `SIP_BIND_ADDRESS` to a SAN-matching address
  (`127.0.0.1` for the current same-workstation certificate set);
- verify every certificate chain, validity interval and required SAN from
  `manifest.json`;
- verify every managed phone trusts the public CA and is configured for SIP TLS +
  mandatory SRTP;
- run the offline validator and build the images;
- arrange a maintenance window and a rollback operator.

Only then use the coordinated start command:

```powershell
$env:TLS_ALLOWED_ORIGINS = 'https://localhost:3000'
$env:SIP_BIND_ADDRESS = '127.0.0.1' # current certificate set; use a SAN-matching LAN address only after rotation
docker compose --env-file .env.docker -f docker-compose.yml -f deploy/tls/docker-compose.tls.yml --profile tls up -d --build
```

Do not run that command as part of ordinary development startup.

## Live acceptance and negative checks

Container health verifies the HTTPS services and PostgreSQL through the same private
CA with hostname checking. After all containers are healthy, additionally record:

- browser login over the intended Frontend DNS name with no certificate warning;
- Backend/Voice control and an authenticated ARI request;
- an Asterisk outbound media WSS connection without certificate errors;
- MicroSIP registration showing TLS on 5061 and no reachable 5060/UDP listener;
- a two-way headset call whose SIP/SDP and Asterisk state show SRTP, plus the existing
  media-spike/echo-isolation acceptance. A healthy container alone does not prove
  audible or encrypted phone media.

Negative tests must fail for an unknown CA, wrong hostname, expired certificate,
plain HTTP/WS/PostgreSQL/SIP attempts and a phone offering unencrypted RTP. Inspect
the merged mounts to prove no service receives `ca.key.pem` or another service key.

## Rollback

Rollback is whole-stack and coordinated: stop the TLS-profile stack, restore the
previous reviewed plain-development Compose deployment, and verify all dependencies
together. Never weaken only one link or leave mixed HTTPS/HTTP service URLs. Preserve
failure logs only after checking that they contain no credentials or key material.
