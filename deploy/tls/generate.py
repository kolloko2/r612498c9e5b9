"""Provision a private development CA and per-service TLS certificates.

This module uses only the Python standard library and an administrator-supplied
OpenSSL executable. It never changes an OS/browser trust store and never
overwrites an existing certificate set.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


SERVICES = ("backend", "frontend", "voice", "postgres", "asterisk", "directory")
ARTIFACT_NAMES = ("private", "ca", "certs", "manifest.json")
DNS_RE = re.compile(r"^(?=.{1,253}\.?$)(?!-)(?:[A-Za-z0-9-]{1,63}\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$")


class ProvisionError(RuntimeError):
    """Safe, user-facing provisioning failure."""


def _dns_name(value: str) -> str:
    value = value.rstrip(".")
    if value.startswith("*.") or not DNS_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"invalid non-wildcard DNS SAN: {value!r}")
    return value.lower()


def _ip_address(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid IP SAN: {value!r}") from exc


def _artifact_conflicts(output: Path) -> list[Path]:
    return [output / name for name in ARTIFACT_NAMES if (output / name).exists()]


def _resolve_openssl(requested: str | None) -> str:
    candidate = requested or shutil.which("openssl")
    if not candidate:
        raise ProvisionError(
            "OpenSSL was not found on PATH. Install/provide it administratively, "
            "then pass --openssl with its executable path. No files were created."
        )
    resolved = shutil.which(candidate) if not Path(candidate).is_file() else str(Path(candidate).resolve())
    if not resolved:
        raise ProvisionError(f"OpenSSL executable was not found: {candidate}")
    return resolved


def _run(openssl: str, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            [openssl, *arguments],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip().splitlines()
        message = detail[-1] if detail else "unknown OpenSSL error"
        raise ProvisionError(f"OpenSSL failed while generating certificates: {message}") from exc
    except OSError as exc:
        raise ProvisionError(f"could not execute OpenSSL: {exc}") from exc


def _server_sans(service: str, dns_sans: list[str], ip_sans: list[str]) -> tuple[list[str], list[str]]:
    dns = list(dict.fromkeys((service, "localhost", *dns_sans)))
    ips = list(dict.fromkeys(("127.0.0.1", *ip_sans)))
    return dns, ips


def _extension_text(dns_sans: list[str], ip_sans: list[str]) -> str:
    lines = [
        "[v3_server]",
        "basicConstraints=critical,CA:FALSE",
        "keyUsage=critical,digitalSignature,keyEncipherment",
        "extendedKeyUsage=serverAuth",
        "subjectAltName=@alt_names",
        "[alt_names]",
    ]
    lines.extend(f"DNS.{index}={value}" for index, value in enumerate(dns_sans, 1))
    lines.extend(f"IP.{index}={value}" for index, value in enumerate(ip_sans, 1))
    return "\n".join(lines) + "\n"


def _make_private(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError as exc:
        raise ProvisionError(f"could not restrict private-key permissions for {path}: {exc}") from exc


def provision(
    output: Path,
    *,
    openssl_requested: str | None,
    dns_sans: list[str],
    ip_sans: list[str],
    ca_days: int,
    cert_days: int,
    dry_run: bool,
) -> dict[str, object]:
    output = output.resolve()
    conflicts = _artifact_conflicts(output)
    if conflicts:
        names = ", ".join(str(path) for path in conflicts)
        raise ProvisionError(f"refusing to overwrite existing TLS artifacts: {names}")

    plan: dict[str, object] = {
        "output": str(output),
        "services": {},
        "ca_days": ca_days,
        "cert_days": cert_days,
        "dry_run": dry_run,
    }
    for service in SERVICES:
        dns, ips = _server_sans(service, dns_sans, ip_sans)
        plan["services"][service] = {"dns": dns, "ip": ips}  # type: ignore[index]
    if dry_run:
        return plan

    openssl = _resolve_openssl(openssl_requested)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".tls-stage-", dir=output.parent) as temporary:
        stage = Path(temporary)
        private = stage / "private"
        ca_dir = stage / "ca"
        certs = stage / "certs"
        private.mkdir(mode=0o700)
        ca_dir.mkdir()
        certs.mkdir()

        ca_key = private / "ca.key.pem"
        ca_cert = ca_dir / "ca.cert.pem"
        _run(openssl, "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", "-out", str(ca_key))
        _make_private(ca_key)
        _run(
            openssl,
            "req", "-x509", "-new", "-sha256", "-days", str(ca_days),
            "-key", str(ca_key), "-out", str(ca_cert),
            "-subj", "/CN=Trainer112 Private Development CA",
            "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        )

        manifest_services: dict[str, object] = {}
        for service in SERVICES:
            key = private / f"{service}.key.pem"
            cert = certs / f"{service}.cert.pem"
            csr = stage / f"{service}.csr.pem"
            extensions = stage / f"{service}.ext.cnf"
            dns, ips = _server_sans(service, dns_sans, ip_sans)
            extensions.write_text(_extension_text(dns, ips), encoding="ascii")
            _run(openssl, "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", "-out", str(key))
            _make_private(key)
            _run(openssl, "req", "-new", "-sha256", "-key", str(key), "-out", str(csr), "-subj", f"/CN={service}")
            serial = "0x" + os.urandom(16).hex()
            _run(
                openssl,
                "x509", "-req", "-sha256", "-days", str(cert_days),
                "-in", str(csr), "-CA", str(ca_cert), "-CAkey", str(ca_key),
                "-set_serial", serial, "-extfile", str(extensions), "-extensions", "v3_server",
                "-out", str(cert),
            )
            _run(openssl, "verify", "-CAfile", str(ca_cert), str(cert))
            key_public = _run(openssl, "pkey", "-in", str(key), "-pubout").stdout
            cert_public = _run(openssl, "x509", "-in", str(cert), "-pubkey", "-noout").stdout
            if key_public != cert_public:
                raise ProvisionError(f"generated key/certificate mismatch for {service}")
            manifest_services[service] = {
                "certificate": f"certs/{service}.cert.pem",
                "private_key": f"private/{service}.key.pem",
                "dns_sans": dns,
                "ip_sans": ips,
                "certificate_sha256": hashlib.sha256(cert.read_bytes()).hexdigest(),
            }

        manifest = {
            "schema": 1,
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "ca_certificate": "ca/ca.cert.pem",
            "ca_private_key": "private/ca.key.pem",
            "ca_days": ca_days,
            "certificate_days": cert_days,
            "services": manifest_services,
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # Artifact directories are absent by contract. Keep the CA key host-side;
        # mounting policy is deliberately left to a later, reviewed deployment change.
        for name in ARTIFACT_NAMES:
            source = stage / name
            destination = output / name
            if destination.exists():
                raise ProvisionError(f"refusing race-time overwrite of {destination}")
            source.replace(destination)
    plan["dry_run"] = False
    return plan


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--openssl", help="OpenSSL executable path; otherwise resolved from PATH")
    parser.add_argument("--dns-san", action="append", default=[], type=_dns_name, help="additional deployment DNS SAN (repeatable)")
    parser.add_argument("--ip-san", action="append", default=[], type=_ip_address, help="additional deployment IP SAN (repeatable)")
    parser.add_argument("--ca-days", type=int, default=3650)
    parser.add_argument("--cert-days", type=int, default=397)
    parser.add_argument("--dry-run", action="store_true", help="validate and print the plan without invoking OpenSSL or writing files")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not 1 <= args.ca_days <= 3650:
        parser.error("--ca-days must be between 1 and 3650")
    if not 1 <= args.cert_days <= 397:
        parser.error("--cert-days must be between 1 and 397")
    if len(args.dns_san) + len(args.ip_san) > 16:
        parser.error("at most 16 additional SANs are allowed")
    try:
        result = provision(
            args.output,
            openssl_requested=args.openssl,
            dns_sans=args.dns_san,
            ip_sans=args.ip_san,
            ca_days=args.ca_days,
            cert_days=args.cert_days,
            dry_run=args.dry_run,
        )
    except ProvisionError as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
