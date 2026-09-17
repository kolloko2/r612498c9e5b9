# TLS certificate provisioner

This directory contains the offline certificate provisioner and a complete opt-in
Compose TLS overlay. The overlay is staged but inactive unless both its file and the
`tls` profile are explicitly selected. It never modifies a trust store or MicroSIP.
See `docs/TLS.md` for validation, activation gates and acceptance requirements.

The original fixed certificate set covers Backend, Frontend, Voice, PostgreSQL,
Asterisk, and the optional test-only LDAP service named `directory`. A separately
reviewed `backend-lb` leaf has since been added from the same offline CA for the
optional cluster overlay. Separate `pg-primary` and `pg-standby` leaves carry their
own service SAN plus stable `postgres`; no original certificate was reissued.

Preview the bounded certificate set without writing files:

```text
python deploy/tls/generate.py --dry-run
```

When an administrator has installed OpenSSL, generate it once:

```text
python deploy/tls/generate.py
```

Add the deployment's actual DNS name and IP before issuance when clients will not
connect as `localhost`, `127.0.0.1`, or by a Compose service name:

```text
python deploy/tls/generate.py --dns-san trainer.example.test --ip-san 192.0.2.10
```

The command refuses to overwrite `private`, `ca`, `certs`, or `manifest.json`.
There is intentionally no `--force`: rotation must stage and validate a new set,
then replace it through a reviewed deployment procedure.

Validate the staged overlay without starting it:

```text
python deploy/tls/validate_overlay.py
```

To add a new reviewed internal endpoint without reissuing existing certificates,
use the bounded offline leaf issuer. It refuses overwrite and updates the manifest:

```text
python deploy/tls/issue_leaf.py --service backend-lb --dry-run
python deploy/tls/issue_leaf.py --service backend-lb --openssl /absolute/path/to/openssl
```

The CA private key is used only by this offline command and must never be mounted
into the load balancer or any runtime service.
