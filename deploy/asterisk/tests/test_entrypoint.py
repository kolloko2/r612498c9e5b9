import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "entrypoint.py"
SPEC = importlib.util.spec_from_file_location("asterisk_entrypoint", MODULE_PATH)
entrypoint = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(entrypoint)


def valid_env():
    return {
        "ARI_USERNAME": "voice",
        "ARI_PASSWORD": "ari-secret-value-0001",
        "MEDIA_USERNAME": "asterisk",
        "MEDIA_PASSWORD": "media-secret-value-0002",
        "SIP_ACCOUNTS_JSON": (
            '{"201":"sip-secret-value-0201","220":"sip-secret-value-0220"}'
        ),
        "VOICE_MEDIA_URL": "ws://voice:8001/media",
        "SIP_EXTERNAL_ADDRESS": "192.0.2.10",
        "SIP_LOCAL_NET": "172.16.0.0/12,192.168.1.18/24",
    }


class EntrypointTests(unittest.TestCase):
    def test_silent_call_is_released(self):
        """Lost BYE must not keep the training extension busy."""
        config = entrypoint.pjsip_accounts([("201", "x" * 32)], use_tls=True)
        self.assertIn("rtp_timeout=15", config)

    def test_renders_all_templates_with_private_permissions(self):
        source = Path(__file__).resolve().parents[1] / "config"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            paths = entrypoint.render_configs(source, output, valid_env())
            self.assertGreaterEqual(len(paths), 9)
            pjsip = (output / "pjsip.conf").read_text(encoding="utf-8")
            self.assertIn("external_media_address=192.0.2.10", pjsip)
            self.assertIn("media_address=192.0.2.10", pjsip)
            self.assertIn("local_net=192.168.1.0/24", pjsip)
            self.assertIn("[201]", pjsip)
            self.assertIn("[220]", pjsip)
            self.assertNotIn("${", pjsip)
            if os.name != "nt":
                self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in paths))

    def test_rejects_config_injection_without_echoing_secret(self):
        env = valid_env()
        env["ARI_PASSWORD"] = "safe-value-123456\n[evil]"
        with self.assertRaises(entrypoint.ConfigurationError) as caught:
            entrypoint.build_values(env)
        self.assertNotIn(env["ARI_PASSWORD"], str(caught.exception))

    def test_rejects_out_of_range_extension(self):
        env = valid_env()
        env["SIP_ACCOUNTS_JSON"] = '{"221":"sip-secret-value-0221"}'
        with self.assertRaisesRegex(entrypoint.ConfigurationError, "201 through 220"):
            entrypoint.build_values(env)

    def test_rejects_reused_credentials(self):
        env = valid_env()
        env["SIP_ACCOUNTS_JSON"] = (
            '{"201":"sip-secret-value-0201","202":"sip-secret-value-0201"}'
        )
        with self.assertRaisesRegex(entrypoint.ConfigurationError, "unique password"):
            entrypoint.build_values(env)

    def test_rejects_non_media_websocket_url(self):
        env = valid_env()
        env["VOICE_MEDIA_URL"] = "http://voice:8001/media"
        with self.assertRaisesRegex(entrypoint.ConfigurationError, "ws://"):
            entrypoint.build_values(env)

    def test_rejects_malformed_websocket_url(self):
        env = valid_env()
        env["VOICE_MEDIA_URL"] = "ws://[broken/media"
        with self.assertRaisesRegex(entrypoint.ConfigurationError, "malformed"):
            entrypoint.build_values(env)

    def test_external_address_is_optional(self):
        env = valid_env()
        env.pop("SIP_EXTERNAL_ADDRESS")
        values = entrypoint.build_values(env)
        self.assertNotIn("media_address=", values["PJSIP_ACCOUNTS"])

    def test_tls_requires_wss(self):
        env = valid_env()
        env["ASTERISK_TLS_ENABLED"] = "true"
        with self.assertRaisesRegex(entrypoint.ConfigurationError, "must use wss"):
            entrypoint.build_values(env)

    def test_tls_renders_no_udp_and_requires_srtp(self):
        env = valid_env()
        env.update(
            {
                "ASTERISK_TLS_ENABLED": "true",
                "VOICE_MEDIA_URL": "wss://voice:8001/media",
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("server.crt", "server.key", "ca.crt"):
                (root / name).write_text("fixture", encoding="ascii")
            env.update(
                {
                    "ASTERISK_TLS_CERT_FILE": str(root / "server.crt"),
                    "ASTERISK_TLS_KEY_FILE": str(root / "server.key"),
                    "ASTERISK_TLS_CA_FILE": str(root / "ca.crt"),
                }
            )
            values = entrypoint.build_values(env)
        self.assertIn("protocol=tls", values["PJSIP_TRANSPORT"])
        self.assertNotIn("protocol=udp", values["PJSIP_TRANSPORT"])
        self.assertIn("media_encryption=sdes", values["PJSIP_ACCOUNTS"])
        self.assertIn("media_encryption_optimistic=no", values["PJSIP_ACCOUNTS"])
        self.assertIn("verify_server_cert=yes", values["WEBSOCKET_TLS"])
        self.assertIn("verify_server_hostname=yes", values["WEBSOCKET_TLS"])
        self.assertEqual(values["HTTP_BIND_ADDRESS"], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
