import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "issue_leaf.py"
SPEC = importlib.util.spec_from_file_location("issue_leaf", MODULE_PATH)
issue_leaf = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(issue_leaf)


class IssueLeafTests(unittest.TestCase):
    def fixture(self, root: Path) -> None:
        (root / "ca").mkdir()
        (root / "private").mkdir()
        (root / "certs").mkdir()
        (root / "ca" / "ca.cert.pem").write_text("ca-cert", encoding="ascii")
        (root / "private" / "ca.key.pem").write_text("ca-key", encoding="ascii")
        (root / "manifest.json").write_text(
            json.dumps({"schema": 1, "services": {"backend": {}}}), encoding="utf-8"
        )

    def test_dry_run_is_bounded_and_does_not_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            plan = issue_leaf.issue_leaf(
                root, service="backend-lb", dns_sans=["lb.example.test"], ip_sans=[],
                cert_days=397, openssl_requested=None, dry_run=True,
            )
            self.assertEqual(plan["dns_sans"], ["backend-lb", "lb.example.test"])
            self.assertFalse((root / "private" / "backend-lb.key.pem").exists())
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn("backend-lb", manifest["services"])

    def test_refuses_existing_leaf_before_openssl(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            marker = root / "certs" / "backend-lb.cert.pem"
            marker.write_text("keep", encoding="ascii")
            with self.assertRaisesRegex(issue_leaf.generate.ProvisionError, "existing leaf"):
                issue_leaf.issue_leaf(
                    root, service="backend-lb", dns_sans=[], ip_sans=[], cert_days=397,
                    openssl_requested=None, dry_run=False,
                )
            self.assertEqual(marker.read_text(encoding="ascii"), "keep")

    def test_dry_run_reports_complete_existing_leaf(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            (root / "private" / "backend-lb.key.pem").write_text("key", encoding="ascii")
            (root / "certs" / "backend-lb.cert.pem").write_text("cert", encoding="ascii")
            manifest_path = root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["services"]["backend-lb"] = {
                "certificate": "certs/backend-lb.cert.pem",
                "private_key": "private/backend-lb.key.pem",
                "dns_sans": ["backend-lb"],
                "ip_sans": [],
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            plan = issue_leaf.issue_leaf(
                root, service="backend-lb", dns_sans=[], ip_sans=[], cert_days=397,
                openssl_requested=None, dry_run=True,
            )
            self.assertTrue(plan["exists"])
            self.assertEqual(plan["dns_sans"], ["backend-lb"])

    def test_service_is_one_dns_label(self):
        self.assertEqual(issue_leaf.service_name("backend-lb"), "backend-lb")
        with self.assertRaises(Exception):
            issue_leaf.service_name("../backend")


if __name__ == "__main__":
    unittest.main()
