#!/usr/bin/env python3
"""Fail-closed static checks for the opt-in TLS Compose overlay."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "deploy" / "tls" / "docker-compose.tls.yml"


def fail(message: str) -> None:
    raise SystemExit(f"TLS overlay validation failed: {message}")


def main() -> int:
    command = [
        "docker", "compose", "-f", str(ROOT / "docker-compose.yml"),
        "-f", str(OVERLAY), "--profile", "tls", "config", "--format", "json",
    ]
    env_file = ROOT / ".env.docker"
    if env_file.is_file():
        command[2:2] = ["--env-file", str(env_file)]
    environment = os.environ.copy()
    environment.setdefault("TLS_ALLOWED_ORIGINS", "https://localhost:3000")
    environment.setdefault("SIP_BIND_ADDRESS", "127.0.0.1")
    result = subprocess.run(
        command, cwd=ROOT, env=environment, capture_output=True, text=True, check=False
    )
    if result.returncode:
        fail(result.stderr.strip() or "docker compose config failed")
    config = json.loads(result.stdout)
    services = config["services"]
    required = {"postgres", "backend", "frontend", "voice", "asterisk"}
    if set(services) != required:
        fail(f"unexpected service set: {sorted(services)}")
    serialized = json.dumps(config, sort_keys=True)
    forbidden = ("ca.key.pem", "sslmode=require", "verify=false", "ws://backend", "http://voice")
    for value in forbidden:
        if value in serialized.lower():
            fail(f"forbidden downgrade or secret reference: {value}")
    asterisk = services["asterisk"]
    expected_asterisk_command = [
        "/usr/sbin/asterisk", "-f", "-vvv", "-C", "/etc/asterisk/asterisk.conf"
    ]
    if asterisk.get("command") != expected_asterisk_command:
        fail("Asterisk TLS entrypoint does not receive the server command")
    published = {(str(port.get("published")), port.get("protocol")) for port in asterisk.get("ports", [])}
    if ("5060", "udp") in published or ("5061", "tcp") not in published:
        fail("SIP ports do not enforce TLS-only signaling")
    for name, service in services.items():
        if service.get("profiles") != ["tls"]:
            fail(f"{name} is not guarded by the tls profile")
        for mount in service.get("volumes", []):
            target = mount.get("target", "") if isinstance(mount, dict) else str(mount)
            source = mount.get("source", "") if isinstance(mount, dict) else str(mount)
            if "private/ca.key.pem" in str(mount) or target.endswith("ca.key.pem"):
                fail(f"{name} receives the CA private key")
            if "/private/" in source.replace("\\", "/") and source.endswith(".key.pem"):
                expected = f"/{name}.key.pem"
                if not source.replace("\\", "/").endswith(expected):
                    fail(f"{name} receives another service's private key")
    hba = (ROOT / "deploy" / "postgres" / "pg_hba.tls.conf").read_text(encoding="utf-8")
    if "hostnossl all" not in hba or "reject" not in hba or "hostssl all" not in hba:
        fail("PostgreSQL HBA does not explicitly require TLS")
    print("TLS overlay structure validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
