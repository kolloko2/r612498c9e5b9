# Asterisk 22 container

This image builds the pinned Asterisk 22.11.0 official source tarball and verifies
its published SHA-256 during the build. It is a private training PBX only: there are
no trunks, PSTN routes, endpoint-to-endpoint routes, or emergency-number routes.
Every dialled number from a registered phone is hung up. Voice still originates
`PJSIP/<extension>` through ARI.

## Runtime environment

Set all credentials at container runtime. They are rendered into mode `0600` files
under `/etc/asterisk`; no credential or generated configuration is copied into the
image. Values must use 16-128 characters from `A-Z a-z 0-9 . _ ~ ! @ # $ % ^ & * + = : -`.

- `ARI_USERNAME`, `ARI_PASSWORD`: required Voice-to-Asterisk ARI Basic Auth.
- `MEDIA_USERNAME`, `MEDIA_PASSWORD`: required Asterisk-to-Voice `/media` Basic Auth.
- `SIP_ACCOUNTS_JSON`: required non-empty JSON object. Keys are provisioned extensions
  in the inclusive range `201`-`220`; values are unique SIP passwords. Every SIP,
  ARI, and media password must be different.
- `VOICE_MEDIA_URL`: optional, defaults to `ws://voice:8001/media`; only an absolute
  `ws`/`wss` URL with exactly the `/media` path is accepted.
- `SIP_EXTERNAL_ADDRESS`: optional literal public/LAN IP advertised in SIP/SDP.
- `SIP_LOCAL_NET`: optional comma-separated CIDRs considered directly local to
  Asterisk for PJSIP NAT decisions. For bridge networking, list only the actual
  container network(s), for example `172.16.0.0/12`; do **not** list the phone LAN.
  On Docker Desktop, advertise the host's LAN IP, not `host.docker.internal`,
  because a SIP phone needs a routable address. When an external address is set,
  it is also applied as each endpoint's `media_address`, avoiding an unroutable
  container IP in SDP when Docker proxies the phone's source address.

The same ARI/media values must be set on Voice. Set Voice `ARI_URL` to
`http://asterisk:8088/ari`, `MEDIA_CONNECTION=voice-media`, and include exactly the
provisioned extensions in `ALLOWED_EXTENSIONS`.

The opt-in overlay in `deploy/tls/docker-compose.tls.yml` switches Asterisk to
HTTPS/WSS, SIP TLS on 5061/TCP, and mandatory SDES-SRTP. In that mode no PJSIP UDP
transport is rendered and 5060/UDP is not published. See `docs/TLS.md`; do not
activate it until managed MicroSIP clients trust the public CA and have passed the
required physical registration/audio acceptance.

Example (place real generated secrets in an ignored `.env`, never in Compose):

```dotenv
ARI_USERNAME=voice
ARI_PASSWORD=<unique-generated-secret>
MEDIA_USERNAME=asterisk
MEDIA_PASSWORD=<different-generated-secret>
SIP_ACCOUNTS_JSON={"201":"<unique-secret>","202":"<different-secret>"}
VOICE_MEDIA_URL=ws://voice:8001/media
SIP_EXTERNAL_ADDRESS=192.168.1.50
SIP_LOCAL_NET=172.16.0.0/12
```

Expose ARI `8088/tcp` only to the Compose network. Publish `5060/udp` and
`20000-20199/udp` only on the private training LAN/firewall. Do not expose these
ports to the Internet. UDP port publishing on Docker Desktop must preserve the
same RTP range. NAT behaviour still needs the physical media-spike acceptance test
in `voice/README.md`; a healthy container does not prove phone audio or echo isolation.

The image health check verifies Asterisk 22, ARI/HTTP on 8088, PJSIP, WebSocket
media, Stasis, and softmix modules. Inspect registrations with:

```text
docker exec <container> asterisk -rx "pjsip show contacts"
docker exec <container> asterisk -rx "http show status"
```

Run the renderer unit tests without Docker:

```text
python -m unittest discover -s deploy/asterisk/tests -v
```

After the container is healthy, probe the dedicated extension `220` from the
Windows host. The tool reads its credential from the ignored `.env.docker`, first
checks that the extension has no existing phone contact, registers for 60 seconds,
and unregisters in `finally`. It prints no password, digest header, or SIP packet:

```text
python tools/sip_registration_probe.py
```

Exit code `3` means the probe deliberately skipped an extension that already had
a contact; use a separate unused provisioned extension instead of displacing it.

The full source build is intentionally separate because it is large:

```text
docker build -t trainer112-asterisk:22.11.0 deploy/asterisk
```

Source and checksum: Asterisk's official downloads directory,
`https://downloads.asterisk.org/pub/telephony/asterisk/releases/`.
