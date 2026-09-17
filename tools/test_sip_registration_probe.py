import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("sip_registration_probe.py")
SPEC = importlib.util.spec_from_file_location("sip_registration_probe", MODULE_PATH)
probe = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)


class SipRegistrationProbeTests(unittest.TestCase):
    def test_loads_json_account_without_printing_or_transforming_it(self):
        password = "x" * 24
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.docker"
            path.write_text(
                "# ignored\nSIP_ACCOUNTS_JSON=" + json.dumps({"220": password}) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(probe.account_password(probe.load_env(path), "220"), password)

    def test_parses_digest_challenge_and_builds_bounded_header(self):
        challenge = probe.digest_parameters(
            'Digest realm="asterisk", nonce="abc", algorithm=MD5, qop="auth"'
        )
        header = probe.authorization("220", "x" * 24, "sip:127.0.0.1:5060", challenge)
        self.assertTrue(header.startswith("Digest "))
        self.assertIn("qop=auth", header)
        self.assertNotIn("x" * 24, header)

    def test_parses_contact_from_success_response(self):
        response = probe.parse_response(
            b"SIP/2.0 200 OK\r\nContact: <sip:220@127.0.0.1:12345>\r\nContent-Length: 0\r\n\r\n"
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(len(response.headers["contact"]), 1)

    def test_options_detection_is_bounded_to_options_requests(self):
        instance = object.__new__(probe.RegistrationProbe)
        sent = []

        class FakeSocket:
            def send(self, value):
                sent.append(value)

        instance.socket = FakeSocket()
        self.assertTrue(
            instance.reply_to_options(
                "OPTIONS sip:220@127.0.0.1 SIP/2.0\r\n"
                "Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bK-test\r\n"
                "From: <sip:asterisk@127.0.0.1>;tag=a\r\n"
                "To: <sip:220@127.0.0.1>\r\n"
                "Call-ID: options-test\r\nCSeq: 1 OPTIONS\r\n\r\n"
            )
        )
        self.assertTrue(sent[0].startswith(b"SIP/2.0 200 OK"))
        self.assertFalse(instance.reply_to_options("SIP/2.0 200 OK\r\n\r\n"))


if __name__ == "__main__":
    unittest.main()
