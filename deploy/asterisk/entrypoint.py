#!/usr/bin/env python3
"""Validate runtime ENV, render private Asterisk configuration, then exec Asterisk."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
from pathlib import Path
from string import Template
from urllib.parse import urlsplit


CONFIG_SOURCE = Path("/opt/asterisk-config")
CONFIG_TARGET = Path("/etc/asterisk")
EXTENSION_MIN = 201
EXTENSION_MAX = 220
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,40}\Z")
SECRET = re.compile(r"[A-Za-z0-9._~!@#$%^&*+=:-]{16,128}\Z")


class ConfigurationError(ValueError):
    """A safe-to-display runtime configuration error."""


def required(env: dict[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value:
        raise ConfigurationError(f"{name} is required")
    return value


def validate_identifier(env: dict[str, str], name: str) -> str:
    value = required(env, name)
    if not IDENTIFIER.fullmatch(value):
        raise ConfigurationError(f"{name} must contain only letters, digits, underscore, or hyphen")
    return value


def validate_secret(value: str, label: str) -> str:
    if not SECRET.fullmatch(value):
        raise ConfigurationError(
            f"{label} must be 16-128 characters using the documented secret character set"
        )
    return value


def parse_accounts(raw: str) -> list[tuple[str, str]]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError("SIP_ACCOUNTS_JSON must be valid JSON") from exc
    if not isinstance(data, dict) or not data:
        raise ConfigurationError("SIP_ACCOUNTS_JSON must be a non-empty JSON object")
    accounts: list[tuple[str, str]] = []
    seen_secrets: set[str] = set()
    for extension, password in data.items():
        if not isinstance(extension, str) or not extension.isascii() or not extension.isdigit():
            raise ConfigurationError("every SIP account key must be an ASCII extension")
        number = int(extension)
        if not EXTENSION_MIN <= number <= EXTENSION_MAX or str(number) != extension:
            raise ConfigurationError("SIP extensions must be canonical numbers from 201 through 220")
        if not isinstance(password, str):
            raise ConfigurationError(f"SIP password for extension {extension} must be a string")
        validate_secret(password, f"SIP password for extension {extension}")
        if password in seen_secrets:
            raise ConfigurationError("every SIP extension must have a unique password")
        seen_secrets.add(password)
        accounts.append((extension, password))
    return sorted(accounts, key=lambda item: int(item[0]))


def validate_media_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ConfigurationError("VOICE_MEDIA_URL is malformed") from exc
    if parsed.scheme not in {"ws", "wss"} or not hostname:
        raise ConfigurationError("VOICE_MEDIA_URL must be an absolute ws:// or wss:// URL")
    if parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ConfigurationError("VOICE_MEDIA_URL must not contain credentials, query, or fragment")
    if parsed.path != "/media":
        raise ConfigurationError("VOICE_MEDIA_URL path must be /media")
    if any(char in value for char in "\r\n;[]"):
        raise ConfigurationError("VOICE_MEDIA_URL contains unsafe configuration characters")
    return value


def tls_enabled(env: dict[str, str]) -> bool:
    raw = env.get("ASTERISK_TLS_ENABLED", "false").strip().lower()
    if raw not in {"true", "false"}:
        raise ConfigurationError("ASTERISK_TLS_ENABLED must be true or false")
    return raw == "true"


def validate_tls_path(env: dict[str, str], name: str) -> str:
    value = required(env, name)
    path = Path(value)
    if len(value) > 240 or any(char in value for char in "\r\n;[]") or not path.is_absolute() or ".." in path.parts:
        raise ConfigurationError(f"{name} must be a safe absolute path")
    if not path.is_file():
        raise ConfigurationError(f"{name} does not exist or is not a file")
    return value


def network_settings(env: dict[str, str]) -> tuple[str, list[str]]:
    external = env.get("SIP_EXTERNAL_ADDRESS", "").strip()
    if external:
        if "%" in external:
            raise ConfigurationError("SIP_EXTERNAL_ADDRESS must not contain an IPv6 scope identifier")
        try:
            external = str(ipaddress.ip_address(external))
        except ValueError as exc:
            raise ConfigurationError("SIP_EXTERNAL_ADDRESS must be a literal IP address") from exc
    local_nets: list[str] = []
    for value in env.get("SIP_LOCAL_NET", "").split(","):
        value = value.strip()
        if not value:
            continue
        try:
            local_nets.append(str(ipaddress.ip_network(value, strict=False)))
        except ValueError as exc:
            raise ConfigurationError("SIP_LOCAL_NET must be a comma-separated list of CIDRs") from exc
    return external, local_nets


def pjsip_accounts(
    accounts: list[tuple[str, str]], external_address: str = "", *, use_tls: bool = False
) -> str:
    blocks = []
    media_address = f"media_address={external_address}\n" if external_address else ""
    for extension, password in accounts:
        blocks.append(
            f"""[{extension}]
type=endpoint
transport={'transport-tls' if use_tls else 'transport-udp'}
context=training-only
disallow=all
allow=g722,ulaw,alaw
{media_address}auth=auth-{extension}
aors={extension}
direct_media=no
; Трубка положена, а BYE потерян: без звука 15 с вызов завершается сам,
; иначе учебный номер остаётся «занятым».
rtp_timeout=15
rtp_timeout_hold=120
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
{'media_encryption=sdes' if use_tls else ''}
{'media_encryption_optimistic=no' if use_tls else ''}

[auth-{extension}]
type=auth
auth_type=userpass
username={extension}
password={password}

[{extension}]
type=aor
max_contacts=1
remove_existing=yes
qualify_frequency=30
"""
        )
    return "\n".join(blocks).rstrip()


def build_values(env: dict[str, str]) -> dict[str, str]:
    ari_username = validate_identifier(env, "ARI_USERNAME")
    media_username = validate_identifier(env, "MEDIA_USERNAME")
    if ari_username == media_username:
        raise ConfigurationError("ARI_USERNAME and MEDIA_USERNAME must be different")
    ari_password = validate_secret(required(env, "ARI_PASSWORD"), "ARI_PASSWORD")
    media_password = validate_secret(required(env, "MEDIA_PASSWORD"), "MEDIA_PASSWORD")
    accounts = parse_accounts(required(env, "SIP_ACCOUNTS_JSON"))
    secrets = [ari_password, media_password, *(password for _, password in accounts)]
    if len(secrets) != len(set(secrets)):
        raise ConfigurationError("ARI, media, and SIP credentials must all be unique")
    external, local_nets = network_settings(env)
    use_tls = tls_enabled(env)
    media_url = validate_media_url(env.get("VOICE_MEDIA_URL", "ws://voice:8001/media"))
    if use_tls and not media_url.startswith("wss://"):
        raise ConfigurationError("VOICE_MEDIA_URL must use wss:// when ASTERISK_TLS_ENABLED=true")
    transport_network = ""
    if external:
        transport_network += (
            f"external_media_address={external}\nexternal_signaling_address={external}\n"
        )
    transport_network += "".join(f"local_net={network}\n" for network in local_nets)
    if use_tls:
        cert_file = validate_tls_path(env, "ASTERISK_TLS_CERT_FILE")
        key_file = validate_tls_path(env, "ASTERISK_TLS_KEY_FILE")
        ca_file = validate_tls_path(env, "ASTERISK_TLS_CA_FILE")
        http_tls = (
            "tlsenable=yes\n"
            "tlsbindaddr=0.0.0.0:8089\n"
            f"tlscertfile={cert_file}\n"
            f"tlsprivatekey={key_file}\n"
            "tlsdisablev1=yes\ntlsdisablev11=yes\ntlsdisablev12=no"
        )
        pjsip_transport = (
            "[transport-tls]\n"
            "type=transport\nprotocol=tls\nbind=0.0.0.0:5061\n"
            "method=tlsv1_2\n"
            f"cert_file={cert_file}\npriv_key_file={key_file}\nca_list_file={ca_file}\n"
            "verify_client=no\nrequire_client_cert=no\n"
            f"{transport_network.rstrip()}"
        )
        websocket_tls = (
            "tls_enabled=yes\n"
            f"ca_list_file={ca_file}\n"
            "verify_server_cert=yes\nverify_server_hostname=yes"
        )
    else:
        http_tls = ""
        pjsip_transport = (
            "[transport-udp]\ntype=transport\nprotocol=udp\nbind=0.0.0.0:5060\n"
            f"{transport_network.rstrip()}"
        )
        websocket_tls = ""
    return {
        "ARI_USERNAME": ari_username,
        "ARI_PASSWORD": ari_password,
        "MEDIA_USERNAME": media_username,
        "MEDIA_PASSWORD": media_password,
        "VOICE_MEDIA_URL": media_url,
        "HTTP_BIND_ADDRESS": "127.0.0.1" if use_tls else "0.0.0.0",
        "HTTP_TLS": http_tls,
        "PJSIP_TRANSPORT": pjsip_transport,
        "WEBSOCKET_TLS": websocket_tls,
        "PJSIP_ACCOUNTS": pjsip_accounts(accounts, external, use_tls=use_tls),
    }


def render_configs(source: Path, target: Path, env: dict[str, str]) -> list[Path]:
    values = build_values(env)
    templates = sorted(source.glob("*.conf.template"))
    if not templates:
        raise ConfigurationError(f"no configuration templates found in {source}")
    target.mkdir(parents=True, exist_ok=True)
    rendered_paths: list[Path] = []
    for source_path in templates:
        output_name = source_path.name.removesuffix(".template")
        destination = target / output_name
        text = Template(source_path.read_text(encoding="utf-8")).substitute(values)
        temporary = target / f".{output_name}.tmp"
        temporary.write_text(text, encoding="utf-8", newline="\n")
        temporary.chmod(0o600)
        temporary.replace(destination)
        rendered_paths.append(destination)
    return rendered_paths


def main() -> int:
    try:
        render_configs(CONFIG_SOURCE, CONFIG_TARGET, dict(os.environ))
    except (ConfigurationError, KeyError, OSError) as exc:
        print(f"Asterisk configuration error: {exc}", file=sys.stderr)
        return 78
    if len(sys.argv) < 2:
        print("Asterisk configuration error: no command supplied", file=sys.stderr)
        return 64
    os.execvp(sys.argv[1], sys.argv[1:])
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
