#!/usr/bin/env python3
"""Copy one service's TLS material safely, drop privileges, and exec a command."""

from __future__ import annotations

import argparse
import os
import pwd
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", required=True)
    parser.add_argument("--cert-name", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required after --")
    if not args.cert_name.isascii() or not args.cert_name.replace("-", "").isalnum():
        parser.error("invalid certificate name")

    account = pwd.getpwnam(args.user)
    source = Path("/run/tls-source")
    target = Path("/run/trainer-tls")
    inputs = {
        f"{args.cert_name}.cert.pem": source / f"{args.cert_name}.cert.pem",
        f"{args.cert_name}.key.pem": source / f"{args.cert_name}.key.pem",
        "ca.cert.pem": source / "ca.cert.pem",
    }
    for path in inputs.values():
        if not path.is_file():
            raise SystemExit(f"TLS bootstrap: required file is missing: {path}")

    target.mkdir(mode=0o700, exist_ok=True)
    os.chown(target, account.pw_uid, account.pw_gid)
    for output_name, input_path in inputs.items():
        output_path = target / output_name
        with input_path.open("rb") as reader, output_path.open("wb") as writer:
            shutil.copyfileobj(reader, writer)
        output_path.chmod(0o600 if output_name.endswith(".key.pem") else 0o644)
        os.chown(output_path, account.pw_uid, account.pw_gid)

    os.initgroups(account.pw_name, account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    os.environ['HOME']=account.pw_dir
    os.environ['USER']=account.pw_name
    os.environ['LOGNAME']=account.pw_name
    os.execvp(command[0], command)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
