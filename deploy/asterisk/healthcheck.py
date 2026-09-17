#!/usr/bin/env python3
"""Container health probe for the required Asterisk runtime modules and HTTP listener."""

import base64
import os
import ssl
import subprocess
import sys
import urllib.request


REQUIRED_MODULES = (
    "chan_pjsip.so",
    "chan_websocket.so",
    "res_ari.so",
    "res_ari_channels.so",
    "res_http_websocket.so",
    "res_stasis.so",
    "res_srtp.so",
    "res_websocket_client.so",
    "bridge_softmix.so",
)


def cli(command: str) -> str:
    result = subprocess.run(
        ["/usr/sbin/asterisk", "-rx", command],
        check=False,
        capture_output=True,
        text=True,
        timeout=4,
    )
    if result.returncode:
        raise RuntimeError(command)
    return result.stdout


def main() -> int:
    try:
        version = cli("core show version")
        if "Asterisk 22." not in version:
            return 1
        for module in REQUIRED_MODULES:
            output = cli(f"module show like {module}")
            if module not in output or "0 modules loaded" in output.lower():
                return 1
        http = cli("http show status")
        if "enabled" not in http.lower():
            return 1
        if os.environ.get("ASTERISK_TLS_ENABLED", "false").lower() == "true":
            if "8089" not in http:
                return 1
            context = ssl.create_default_context(cafile=os.environ["ASTERISK_TLS_CA_FILE"])
            request = urllib.request.Request("https://asterisk:8089/ari/api-docs/resources.json")
            credentials = f"{os.environ['ARI_USERNAME']}:{os.environ['ARI_PASSWORD']}".encode()
            request.add_header("Authorization", "Basic " + base64.b64encode(credentials).decode())
            with urllib.request.urlopen(request, timeout=3, context=context) as response:
                if response.status != 200:
                    return 1
        elif "8088" not in http:
            return 1
    except (KeyError, OSError, RuntimeError, ssl.SSLError, subprocess.TimeoutExpired):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
