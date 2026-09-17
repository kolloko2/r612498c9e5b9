#!/usr/bin/env python3
"""Bounded SIP REGISTER probe for the local, synthetic Asterisk training stand."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import socket
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


AUTH_PARAM = re.compile(r'(\w+)=(?:"((?:[^"\\]|\\.)*)"|([^,\s]+))')


class ProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class SipResponse:
    status: int
    headers: dict[str, list[str]]


def load_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ProbeError(f"environment file not found: {path}")
    values: dict[str, str] = {}
    for line_number, source_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = source_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ProbeError(f"invalid environment assignment at line {line_number}")
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                try:
                    value = json.loads(f'"{value}"')
                except json.JSONDecodeError as exc:
                    raise ProbeError(f"invalid quoted value at line {line_number}") from exc
        values[name] = value
    return values


def account_password(env: dict[str, str], extension: str) -> str:
    raw = os.environ.get("SIP_ACCOUNTS_JSON", env.get("SIP_ACCOUNTS_JSON", ""))
    try:
        accounts = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProbeError("SIP_ACCOUNTS_JSON is not valid JSON") from exc
    if not isinstance(accounts, dict) or not isinstance(accounts.get(extension), str):
        raise ProbeError(f"extension {extension} is not present in SIP_ACCOUNTS_JSON")
    if not accounts[extension]:
        raise ProbeError(f"extension {extension} has an empty credential")
    return accounts[extension]


def parse_response(packet: bytes) -> SipResponse:
    try:
        text = packet.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProbeError("SIP server returned a non-text response") from exc
    # RFC 5626-style CRLF keepalives may precede a response on a UDP flow.
    head = text.lstrip("\r\n").split("\r\n\r\n", 1)[0]
    lines = head.split("\r\n")
    match = re.fullmatch(r"SIP/2\.0\s+(\d{3})(?:\s+.*)?", lines[0]) if lines else None
    if not match:
        raise ProbeError("SIP server returned an invalid status line")
    headers: dict[str, list[str]] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers.setdefault(name.strip().lower(), []).append(value.strip())
    return SipResponse(int(match.group(1)), headers)


def digest_parameters(challenge: str) -> dict[str, str]:
    if not challenge.lower().startswith("digest "):
        raise ProbeError("SIP server did not offer Digest authentication")
    params: dict[str, str] = {}
    for match in AUTH_PARAM.finditer(challenge[7:]):
        value = match.group(2) if match.group(2) is not None else match.group(3)
        params[match.group(1).lower()] = value.replace('\\"', '"')
    if not params.get("realm") or not params.get("nonce"):
        raise ProbeError("SIP Digest challenge is missing realm or nonce")
    algorithm = params.get("algorithm", "MD5").upper()
    if algorithm != "MD5":
        raise ProbeError(f"unsupported SIP Digest algorithm: {algorithm}")
    qops = [item.strip() for item in params.get("qop", "").split(",") if item.strip()]
    if qops and "auth" not in qops:
        raise ProbeError("SIP Digest challenge does not support qop=auth")
    return params


def md5(value: str) -> str:
    return hashlib.md5(value.encode("utf-8"), usedforsecurity=False).hexdigest()


def authorization(
    username: str, password: str, uri: str, challenge: dict[str, str]
) -> str:
    realm = challenge["realm"]
    nonce = challenge["nonce"]
    opaque = challenge.get("opaque")
    cnonce = secrets.token_hex(12)
    nonce_count = "00000001"
    ha1 = md5(f"{username}:{realm}:{password}")
    ha2 = md5(f"REGISTER:{uri}")
    qop = "auth" if challenge.get("qop") else ""
    if qop:
        response = md5(f"{ha1}:{nonce}:{nonce_count}:{cnonce}:{qop}:{ha2}")
    else:
        response = md5(f"{ha1}:{nonce}:{ha2}")
    fields = [
        f'username="{username}"',
        f'realm="{realm}"',
        f'nonce="{nonce}"',
        f'uri="{uri}"',
        f'response="{response}"',
        "algorithm=MD5",
    ]
    if opaque:
        fields.append(f'opaque="{opaque}"')
    if qop:
        fields.extend(("qop=auth", f"nc={nonce_count}", f'cnonce="{cnonce}"'))
    return "Digest " + ", ".join(fields)


class RegistrationProbe:
    def __init__(self, host: str, port: int, extension: str, password: str, timeout: float):
        self.host = host
        self.port = port
        self.extension = extension
        self.password = password
        self.timeout = timeout
        self.uri = f"sip:{host}:{port}"
        self.call_id = f"{uuid.uuid4()}@probe.invalid"
        self.from_tag = secrets.token_hex(8)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.settimeout(timeout)
        self.socket.bind(("0.0.0.0", 0))
        self.socket.connect((host, port))
        self.local_host, self.local_port = self.socket.getsockname()

    def close(self) -> None:
        self.socket.close()

    def reply_to_options(self, text: str) -> bool:
        lines = text.split("\r\n")
        if not lines or not lines[0].startswith("OPTIONS "):
            return False
        received: dict[str, list[str]] = {}
        for line in lines[1:]:
            if not line or ":" not in line:
                continue
            name, value = line.split(":", 1)
            received.setdefault(name.strip().lower(), []).append(value.strip())
        required = ("via", "from", "to", "call-id", "cseq")
        if any(not received.get(name) for name in required):
            return True
        response = ["SIP/2.0 200 OK"]
        response.extend(f"Via: {value}" for value in received["via"])
        response.extend(
            (
                f"From: {received['from'][0]}",
                f"To: {received['to'][0]}",
                f"Call-ID: {received['call-id'][0]}",
                f"CSeq: {received['cseq'][0]}",
                "Content-Length: 0",
                "",
                "",
            )
        )
        self.socket.send("\r\n".join(response).encode("utf-8"))
        return True

    def request(
        self,
        cseq: int,
        contact_expires: int | None,
        challenge: dict[str, str] | None,
    ) -> SipResponse:
        branch = "z9hG4bK-" + secrets.token_hex(10)
        headers = [
            f"REGISTER {self.uri} SIP/2.0",
            f"Via: SIP/2.0/UDP {self.local_host}:{self.local_port};branch={branch};rport",
            "Max-Forwards: 5",
            f"To: <sip:{self.extension}@{self.host}>",
            f"From: <sip:{self.extension}@{self.host}>;tag={self.from_tag}",
            f"Call-ID: {self.call_id}",
            f"CSeq: {cseq} REGISTER",
        ]
        if contact_expires is not None:
            headers.extend(
                (
                    f"Contact: <sip:{self.extension}@{self.local_host}:{self.local_port}>;expires={contact_expires}",
                    f"Expires: {contact_expires}",
                )
            )
        if challenge is not None:
            headers.append(
                challenge.get("_header", "Authorization")
                + ": "
                + authorization(self.extension, self.password, self.uri, challenge)
            )
        headers.extend(("User-Agent: trainer112-registration-probe", "Content-Length: 0", "", ""))
        payload = "\r\n".join(headers).encode("utf-8")
        self.socket.send(payload)
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeError(f"SIP response timed out after {self.timeout:g}s")
            self.socket.settimeout(remaining)
            try:
                packet = self.socket.recv(65535)
            except socket.timeout as exc:
                raise ProbeError(f"SIP response timed out after {self.timeout:g}s") from exc
            try:
                text = packet.decode("utf-8")
            except UnicodeDecodeError:
                continue
            text = text.lstrip("\r\n")
            if self.reply_to_options(text):
                continue
            if not text.startswith("SIP/2.0 "):
                continue
            response = parse_response(text.encode("utf-8"))
            call_ids = response.headers.get("call-id", [])
            cseqs = response.headers.get("cseq", [])
            vias = response.headers.get("via", [])
            if (
                self.call_id not in call_ids
                or f"{cseq} REGISTER" not in cseqs
                or not any(branch in via for via in vias)
            ):
                continue
            return response

    def authenticated_query(self) -> tuple[SipResponse, dict[str, str]]:
        response = self.request(1, None, None)
        if response.status not in {401, 407}:
            raise ProbeError(f"expected SIP authentication challenge, received {response.status}")
        header_name = "www-authenticate" if response.status == 401 else "proxy-authenticate"
        values = response.headers.get(header_name, [])
        if not values:
            raise ProbeError("SIP authentication challenge header is missing")
        challenge = digest_parameters(values[0])
        if response.status == 407:
            challenge["_header"] = "Proxy-Authorization"
        response = self.request(2, None, challenge)
        if response.status != 200:
            raise ProbeError(f"authenticated registration query returned SIP {response.status}")
        return response, challenge


def run_probe(host: str, port: int, extension: str, password: str, timeout: float) -> int:
    probe = RegistrationProbe(host, port, extension, password, timeout)
    registration_attempted = False
    challenge: dict[str, str] | None = None
    try:
        query, challenge = probe.authenticated_query()
        contacts = [value for value in query.headers.get("contact", []) if value != "*"]
        if contacts:
            print(f"SKIP: extension {extension} already has a registered contact")
            return 3
        registration_attempted = True
        registered_response = probe.request(3, 60, challenge)
        if registered_response.status != 200:
            raise ProbeError(f"registration returned SIP {registered_response.status}")
        print(f"PASS: extension {extension} registered with SIP Digest authentication")
        return 0
    finally:
        if registration_attempted and challenge is not None:
            try:
                cleanup = probe.request(4, 0, challenge)
                if cleanup.status in {401, 407}:
                    header_name = (
                        "www-authenticate" if cleanup.status == 401 else "proxy-authenticate"
                    )
                    headers = cleanup.headers.get(header_name, [])
                    if headers:
                        retry_challenge = digest_parameters(headers[0])
                        if cleanup.status == 407:
                            retry_challenge["_header"] = "Proxy-Authorization"
                        cleanup = probe.request(5, 0, retry_challenge)
                if cleanup.status != 200:
                    print(
                        f"WARNING: unregister returned SIP {cleanup.status}; inspect the test contact",
                        file=sys.stderr,
                    )
            except (OSError, ProbeError) as exc:
                print(f"WARNING: unregister failed ({exc}); inspect the test contact", file=sys.stderr)
        probe.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env.docker"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5060)
    parser.add_argument("--extension", default="220")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()
    if not args.extension.isascii() or not args.extension.isdigit():
        parser.error("--extension must contain only ASCII digits")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be from 1 through 65535")
    if not 0.2 <= args.timeout <= 15:
        parser.error("--timeout must be from 0.2 through 15 seconds")
    return args


def main() -> int:
    args = parse_args()
    try:
        env = load_env(args.env_file)
        password = account_password(env, args.extension)
        return run_probe(args.host, args.port, args.extension, password, args.timeout)
    except (OSError, ProbeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
