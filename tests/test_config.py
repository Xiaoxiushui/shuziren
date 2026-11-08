"""配置更新和带口令启动检查；只使用临时文件、本机 HTTP 服务和虚构凭据。"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


app = load("config_test_server", "server/server.py")
launcher = load("config_test_launcher", "scripts/launch.py")


class ConfigTests(unittest.TestCase):
    def test_updated_token_is_used_without_restarting(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {}, clear=True):
            root = Path(temp)
            (root / "server").mkdir()
            env = root / "server/.env"
            env.write_text("NLS_TOKEN=old-local-only\n", encoding="utf-8")
            with mock.patch.object(app, "ROOT", root), mock.patch.object(app, "_DOTENV_VALUES", {}):
                app.load_dotenv()
                self.assertEqual(os.environ["NLS_TOKEN"], "old-local-only")
                env.write_text("NLS_TOKEN=new-local-only\n", encoding="utf-8")
                app.refresh_dotenv()
                self.assertEqual(os.environ["NLS_TOKEN"], "new-local-only")
                env.write_text("NLS_TOKEN=\n", encoding="utf-8")
                app.refresh_dotenv()
                self.assertEqual(os.environ["NLS_TOKEN"], "")

    def test_explicit_environment_overrides_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {"NLS_TOKEN": "process-only"}, clear=True):
            root = Path(temp)
            (root / "server").mkdir()
            env = root / "server/.env"
            env.write_text("NLS_TOKEN=file-only\nNLS_APPKEY=file-app-only\n", encoding="utf-8")
            with mock.patch.object(app, "ROOT", root), mock.patch.object(app, "_DOTENV_VALUES", {}):
                app.load_dotenv()
                self.assertEqual(os.environ["NLS_TOKEN"], "process-only")
                os.environ["NLS_APPKEY"] = "explicit-app-only"
                app.refresh_dotenv()
                self.assertEqual(os.environ["NLS_TOKEN"], "process-only")
                self.assertEqual(os.environ["NLS_APPKEY"], "explicit-app-only")

    def test_notepad_bom_and_quoted_access_code_are_supported(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {}, clear=True):
            root = Path(temp)
            (root / "server").mkdir()
            (root / "server/.env").write_text('NLS_TOKEN="local-only"\nACCESS_CODE="local=code"\n', encoding="utf-8-sig")
            with mock.patch.object(app, "ROOT", root), mock.patch.object(app, "_DOTENV_VALUES", {}), mock.patch.object(launcher, "ROOT", root):
                self.assertEqual(launcher.access_code(), "local=code")
                app.load_dotenv()
                self.assertEqual(os.environ["NLS_TOKEN"], "local-only")
                self.assertEqual(os.environ["ACCESS_CODE"], "local=code")

    def test_launcher_health_check_authenticates_and_rejects_unrelated_service(self):
        class Provider(BaseHTTPRequestHandler):
            service = "digital-memorial"

            def log_message(self, *args):
                pass

            def do_GET(self):
                allowed = self.headers.get("X-Access-Code") == "local-only-code"
                payload = json.dumps({"ok": allowed, "service": self.service}).encode()
                self.send_response(200 if allowed else 403)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with mock.patch.dict(os.environ, {"ACCESS_CODE": "local-only-code"}):
                self.assertTrue(launcher.health_ok(server.server_port))
                Provider.service = "another-app"
                self.assertFalse(launcher.health_ok(server.server_port))
            with mock.patch.dict(os.environ, {"ACCESS_CODE": "wrong-local-code"}):
                self.assertFalse(launcher.health_ok(server.server_port))
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
