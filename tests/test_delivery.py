import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class DeliveryTests(unittest.TestCase):
    def test_single_reviewed_customer_package_starts_with_its_private_review(self):
        spec = importlib.util.spec_from_file_location("customer_package_demo", ROOT / "scripts" / "package_demo.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from profile_store import review_template

        with tempfile.TemporaryDirectory(prefix="memorial-customer-delivery-") as directory:
            workspace = Path(directory)
            source = workspace / "source"
            for folder in ("server", "web"):
                shutil.copytree(ROOT / folder, source / folder, ignore=shutil.ignore_patterns(".env", ".env.*", "__pycache__"))
            profile = json.loads((ROOT / "profiles/demo.json").read_text(encoding="utf-8"))
            profile.update(id="customer-fixture", kind="real", name="虚构交付测试夹具")
            review = review_template(profile)
            review.update(status="approved", reviewer="测试审核员", reviewedAt="2026-11-01T22:00:00+08:00")
            review["authorization"] = {"status": "confirmed", "recordRef": "fixture-authorization"}
            for item in review["items"]:
                item.update(status="approved", sourceRef="fixture-material")
            for folder, content in (("profiles", profile), ("reviews", review)):
                (source / folder).mkdir()
                (source / folder / "customer-fixture.json").write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
            (source / "profiles/another-demo.json").write_bytes((ROOT / "profiles/another-demo.json").read_bytes())
            archive_path = workspace / "customer.zip"
            with mock.patch.object(module, "ROOT", source), mock.patch("builtins.print"):
                self.assertEqual(module.main(["--profile", "customer-fixture", "--out", str(archive_path)]), 0)
            extracted = workspace / "extracted"
            with zipfile.ZipFile(archive_path) as archive:
                self.assertIn("reviews/customer-fixture.json", archive.namelist())
                self.assertNotIn("profiles/another-demo.json", archive.namelist())
                archive.extractall(extracted)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            service = subprocess.Popen(
                [sys.executable, str(extracted / "server/server.py"), "--port", str(port)],
                cwd=extracted, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env={key: value for key, value in os.environ.items() if not key.startswith(("MODEL_", "TTS_"))},
            )
            try:
                deadline = time.monotonic() + 8
                while True:
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/profiles", timeout=1) as reply:
                            self.assertEqual([item["id"] for item in json.load(reply)["profiles"]], ["customer-fixture"])
                        break
                    except OSError:
                        if service.poll() is not None or time.monotonic() >= deadline:
                            self.fail("Single-customer delivery did not start")
                        time.sleep(0.05)
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/profile?id=customer-fixture", timeout=2) as reply:
                    body = reply.read().decode()
                    self.assertEqual(json.loads(body)["name"], profile["name"])
                    self.assertNotIn("fixture-authorization", body)
                    self.assertNotIn("fixture-material", body)
            finally:
                service.terminate()
                service.wait(timeout=5)

    def test_extracted_package_starts_without_project_history(self):
        with tempfile.TemporaryDirectory(prefix="memorial-delivery-") as directory:
            destination = Path(directory)
            archive_path = destination / "demo.zip"
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "package_demo.py"), "--out", str(archive_path)],
                check=True, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            with zipfile.ZipFile(archive_path) as archive:
                names = archive.namelist()
                self.assertIn("一键启动.cmd", names)
                self.assertIn("server/.env.example", names)
                self.assertFalse(any(".env" == Path(name).name or "__pycache__" in name for name in names))
                extracted = destination / "extracted"
                archive.extractall(extracted)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            server = subprocess.Popen(
                [sys.executable, str(extracted / "server" / "server.py"), "--host", "127.0.0.1", "--port", str(port)],
                cwd=extracted, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env={key: value for key, value in os.environ.items() if not key.startswith(("MODEL_", "TTS_"))},
            )
            base = f"http://127.0.0.1:{port}"
            try:
                deadline = time.monotonic() + 8
                while True:
                    try:
                        with urllib.request.urlopen(f"{base}/api/health", timeout=1) as reply:
                            self.assertEqual(json.load(reply)["service"], "digital-memorial")
                        break
                    except OSError:
                        if server.poll() is not None or time.monotonic() >= deadline:
                            self.fail("Extracted server did not start")
                        time.sleep(0.05)
                for path in ("/", "/web/app.js", "/web/api.js", "/web/people.js", "/api/profiles"):
                    with self.subTest(path=path), urllib.request.urlopen(base + path, timeout=2) as reply:
                        self.assertEqual(reply.status, 200)
                request = urllib.request.Request(
                    base + "/api/chat",
                    data=json.dumps({"profileId": "another-demo", "message": "你喜欢做什么？"}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(request, timeout=2) as reply:
                    body = json.load(reply)
                    self.assertTrue(body["ok"])
                    self.assertIn("茉莉", body["reply"])
                for path in ("/server/.env", "/profiles/demo.json", "/web/../server/server.py"):
                    with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(base + path, timeout=2)
                    self.assertEqual(error.exception.code, 404)
            finally:
                server.terminate()
                server.wait(timeout=5)

    def test_environment_variants_cannot_enter_delivery_package(self):
        spec = importlib.util.spec_from_file_location("package_demo", ROOT / "scripts" / "package_demo.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix="memorial-secrets-") as directory:
            for name in (".env", ".env.local", ".env.production"):
                secret = Path(directory) / name
                secret.write_text("TEST_ONLY=not-a-real-key\n", encoding="utf-8")
                with self.subTest(name=name):
                    self.assertFalse(module.should_include(secret))
            example = Path(directory) / ".env.example"
            example.write_text("TEST_ONLY=\n", encoding="utf-8")
            self.assertTrue(module.should_include(example))


if __name__ == "__main__":
    unittest.main()
