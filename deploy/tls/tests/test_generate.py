from pathlib import Path
import tempfile
import unittest
from unittest import mock

from deploy.tls import generate


class GenerateTlsTests(unittest.TestCase):
    def test_dry_run_validates_sans_without_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "tls"
            plan = generate.provision(
                output,
                openssl_requested=None,
                dns_sans=["training.example"],
                ip_sans=["192.0.2.10"],
                ca_days=3650,
                cert_days=397,
                dry_run=True,
            )
            self.assertFalse(output.exists())
            self.assertEqual(
                plan["services"]["backend"]["dns"],
                ["backend", "localhost", "training.example"],
            )
            self.assertEqual(
                plan["services"]["backend"]["ip"],
                ["127.0.0.1", "192.0.2.10"],
            )

    def test_existing_artifact_refuses_overwrite_before_openssl_lookup(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "tls"
            (output / "private").mkdir(parents=True)
            marker = output / "private" / "backend.key.pem"
            marker.write_text("do not replace", encoding="utf-8")
            with mock.patch.object(generate.shutil, "which") as lookup:
                with self.assertRaisesRegex(generate.ProvisionError, "refusing to overwrite"):
                    generate.provision(
                        output,
                        openssl_requested=None,
                        dns_sans=[],
                        ip_sans=[],
                        ca_days=3650,
                        cert_days=397,
                        dry_run=False,
                    )
            lookup.assert_not_called()
            self.assertEqual(marker.read_text(encoding="utf-8"), "do not replace")

    def test_missing_openssl_creates_no_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "tls"
            with mock.patch.object(generate.shutil, "which", return_value=None):
                with self.assertRaisesRegex(generate.ProvisionError, "OpenSSL was not found"):
                    generate.provision(
                        output,
                        openssl_requested=None,
                        dns_sans=[],
                        ip_sans=[],
                        ca_days=3650,
                        cert_days=397,
                        dry_run=False,
                    )
            self.assertFalse((output / "private").exists())
            self.assertFalse((output / "ca").exists())
            self.assertFalse((output / "certs").exists())

    def test_server_certificate_sans_are_service_specific(self):
        for service in generate.SERVICES:
            dns, ips = generate._server_sans(service, [], [])
            self.assertEqual(dns, [service, "localhost"])
            self.assertEqual(ips, ["127.0.0.1"])

        self.assertIn("directory", generate.SERVICES)


if __name__ == "__main__":
    unittest.main()
