#!/usr/bin/env python3
"""Issue one additional server leaf from the existing offline Trainer112 CA."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import tempfile

try:
    from deploy.tls import generate
except ModuleNotFoundError:  # Direct execution from outside the repository root.
    import generate  # type: ignore[no-redef]


SERVICE_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def service_name(value: str) -> str:
    if not SERVICE_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("service must be a lowercase DNS label")
    return value


def issue_leaf(
    output: Path,
    *,
    service: str,
    dns_sans: list[str],
    ip_sans: list[str],
    cert_days: int,
    openssl_requested: str | None,
    dry_run: bool,
) -> dict[str, object]:
    output = output.resolve()
    ca_cert = output / "ca" / "ca.cert.pem"
    ca_key = output / "private" / "ca.key.pem"
    manifest_path = output / "manifest.json"
    key_path = output / "private" / f"{service}.key.pem"
    cert_path = output / "certs" / f"{service}.cert.pem"
    required = (ca_cert, ca_key, manifest_path)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise generate.ProvisionError("existing CA set is incomplete: " + ", ".join(missing))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    services = manifest.get("services")
    if not isinstance(services, dict):
        raise generate.ProvisionError("manifest services object is invalid")
    dns = list(dict.fromkeys((service, *dns_sans)))
    ips = list(dict.fromkeys(ip_sans))
    existing = services.get(service)
    any_existing = key_path.exists() or cert_path.exists() or existing is not None
    if any_existing:
        complete = key_path.is_file() and cert_path.is_file() and isinstance(existing, dict)
        expected_paths = complete and existing.get("certificate") == f"certs/{service}.cert.pem"
        expected_paths = expected_paths and existing.get("private_key") == f"private/{service}.key.pem"
        if dry_run and expected_paths:
            return {
                "service": service,
                "certificate": str(cert_path),
                "private_key": str(key_path),
                "dns_sans": existing.get("dns_sans", []),
                "ip_sans": existing.get("ip_sans", []),
                "cert_days": cert_days,
                "dry_run": True,
                "exists": True,
            }
        if not complete:
            raise generate.ProvisionError(f"incomplete existing leaf state for {service}")
        raise generate.ProvisionError(f"refusing to overwrite existing leaf for {service}")
    plan: dict[str, object] = {
        "service": service,
        "certificate": str(cert_path),
        "private_key": str(key_path),
        "dns_sans": dns,
        "ip_sans": ips,
        "cert_days": cert_days,
        "dry_run": dry_run,
        "exists": False,
    }
    if dry_run:
        return plan

    openssl = generate._resolve_openssl(openssl_requested)
    ca_key_public = generate._run(openssl, "pkey", "-in", str(ca_key), "-pubout").stdout
    ca_cert_public = generate._run(openssl, "x509", "-in", str(ca_cert), "-pubkey", "-noout").stdout
    if ca_key_public != ca_cert_public:
        raise generate.ProvisionError("existing CA certificate/private-key mismatch")

    with tempfile.TemporaryDirectory(prefix=f".{service}-leaf-", dir=output) as temporary:
        stage = Path(temporary)
        key = stage / f"{service}.key.pem"
        cert = stage / f"{service}.cert.pem"
        csr = stage / f"{service}.csr.pem"
        extensions = stage / "server.ext.cnf"
        extensions.write_text(generate._extension_text(dns, ips), encoding="ascii")
        generate._run(
            openssl, "genpkey", "-algorithm", "RSA", "-pkeyopt",
            "rsa_keygen_bits:3072", "-out", str(key),
        )
        generate._make_private(key)
        generate._run(
            openssl, "req", "-new", "-sha256", "-key", str(key),
            "-out", str(csr), "-subj", f"/CN={service}",
        )
        generate._run(
            openssl, "x509", "-req", "-sha256", "-days", str(cert_days),
            "-in", str(csr), "-CA", str(ca_cert), "-CAkey", str(ca_key),
            "-set_serial", "0x" + os.urandom(16).hex(), "-extfile", str(extensions),
            "-extensions", "v3_server", "-out", str(cert),
        )
        generate._run(openssl, "verify", "-CAfile", str(ca_cert), str(cert))
        if generate._run(openssl, "pkey", "-in", str(key), "-pubout").stdout != generate._run(
            openssl, "x509", "-in", str(cert), "-pubkey", "-noout"
        ).stdout:
            raise generate.ProvisionError(f"generated key/certificate mismatch for {service}")

        services[service] = {
            "certificate": f"certs/{service}.cert.pem",
            "private_key": f"private/{service}.key.pem",
            "dns_sans": dns,
            "ip_sans": ips,
            "certificate_sha256": hashlib.sha256(cert.read_bytes()).hexdigest(),
        }
        manifest["updated_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        manifest_temp = output / f".{service}.manifest.tmp"
        created: list[Path] = []
        try:
            key.replace(key_path)
            created.append(key_path)
            cert.replace(cert_path)
            created.append(cert_path)
            manifest_temp.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            manifest_temp.replace(manifest_path)
        except Exception:
            manifest_temp.unlink(missing_ok=True)
            for path in created:
                path.unlink(missing_ok=True)
            raise
    plan["dry_run"] = False
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--service", required=True, type=service_name)
    parser.add_argument("--dns-san", action="append", default=[], type=generate._dns_name)
    parser.add_argument("--ip-san", action="append", default=[], type=generate._ip_address)
    parser.add_argument("--cert-days", type=int, default=397)
    parser.add_argument("--openssl")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.cert_days <= 397:
        parser.error("--cert-days must be between 1 and 397")
    if len(args.dns_san) + len(args.ip_san) > 16:
        parser.error("at most 16 additional SANs are allowed")
    try:
        result = issue_leaf(
            args.output, service=args.service, dns_sans=args.dns_san,
            ip_sans=args.ip_san, cert_days=args.cert_days,
            openssl_requested=args.openssl, dry_run=args.dry_run,
        )
    except (generate.ProvisionError, json.JSONDecodeError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
