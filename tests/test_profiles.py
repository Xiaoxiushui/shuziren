import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from profile_store import (
    ProfileRepository, ProfileUnavailable, load_json, profile_digest,
    review_template, validate_profile, validate_review,
)


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


workflow = load_module("profile_workflow_test", "scripts/manage_profile.py")
packager = load_module("profile_packager_test", "scripts/package_demo.py")
linker = load_module("profile_linker_test", "scripts/generate_links.py")
server_module = load_module("profile_server_test", "server/server.py")


def example(profile_id="fixture-demo", kind="fictional"):
    profile = load_json(ROOT / "profiles/demo.json")
    profile.update(id=profile_id, kind=kind, name="审核测试人物（虚构夹具）")
    return profile


def approved_review(profile):
    # 所有登记均只用于系统临时目录中的虚构测试夹具。
    review = review_template(profile)
    review.update(status="approved", reviewer="测试审核员", reviewedAt="2026-11-01T22:00:00+08:00")
    for item in review["items"]:
        item.update(status="approved", sourceRef="fixture-material-001")
    if profile["kind"] == "real":
        review["authorization"].update(status="confirmed", recordRef="fixture-authorization-001")
    return review


def write_draft(directory, profile, review=None):
    workflow.write_json(directory / "profile.json", profile)
    if review is not None:
        workflow.write_json(directory / "review.json", review)


class ProfileValidationTests(unittest.TestCase):
    def test_existing_demos_and_bad_shapes(self):
        for path in (ROOT / "profiles").glob("*.json"):
            with self.subTest(path=path.name):
                self.assertEqual(validate_profile(load_json(path)), [])
        mutations = [
            ("answers", [None]), ("answers", [{"keys": [""], "text": "回答"}]),
            ("persona", []), ("voice", {"mode": "browser-demo", "rate": True}),
            ("voice", {"mode": "browser-demo", "rate": 10 ** 1000}),
            ("voice", {"mode": "browser-demo", "rate": float("nan")}),
            ("id", "../private"), ("kind", []), ("welcome", ""), ("schemaVersion", True),
            ("review", {"private": "fixture-private"}), ("suggestedQuestions", ["完全没有关键词的测试问题"]),
        ]
        for field, value in mutations:
            profile = example()
            profile[field] = value
            with self.subTest(field=field, value_type=type(value).__name__):
                self.assertTrue(validate_profile(profile))
        self.assertTrue(validate_profile([]))
        self.assertTrue(validate_profile(None))

    def test_review_is_bound_to_content_and_every_field(self):
        profile = example()
        review = approved_review(profile)
        self.assertEqual(validate_review(profile, review), [])
        reformatted = json.loads(json.dumps(profile, indent=4, ensure_ascii=False))
        self.assertEqual(profile_digest(profile), profile_digest(reformatted))
        profile["answers"][0]["text"] = "修改后的测试回答"
        self.assertIn("review_mismatch", {error["code"] for error in validate_review(profile, review)})
        profile = example()
        review = approved_review(profile)
        review["items"].pop()
        self.assertIn("field_not_approved", {error["code"] for error in validate_review(profile, review)})

    def test_real_profile_requires_confirmed_authorization_reference(self):
        profile = example(kind="real")
        review = approved_review(profile)
        review["authorization"] = {"status": "pending", "recordRef": ""}
        codes = {error["code"] for error in validate_review(profile, review)}
        self.assertIn("authorization_pending", codes)
        self.assertIn("authorization_reference_required", codes)
        review["authorization"] = {"status": "confirmed", "recordRef": "fixture-authorization-001"}
        self.assertEqual(validate_review(profile, review), [])


class ProfileWorkflowTests(unittest.TestCase):
    def test_init_is_private_and_review_does_not_approve(self):
        with tempfile.TemporaryDirectory(prefix="memorial-workflow-") as temporary, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            root = Path(temporary)
            draft = root / "drafts/fixture-demo"
            with mock.patch.object(workflow, "ROOT", root):
                self.assertEqual(workflow.main(["init", "--id", "fixture-demo", "--kind", "fictional"]), 0)
                self.assertEqual(ProfileRepository(root).list_profiles(), [])
                self.assertEqual(workflow.main(["check", "--draft", str(draft)]), 1)
                self.assertEqual(workflow.main(["review", "--draft", str(draft)]), 1)
                self.assertFalse((draft / "review.json").exists())
                write_draft(draft, example())
                self.assertEqual(workflow.main(["review", "--draft", str(draft)]), 0)
                self.assertEqual(load_json(draft / "review.json")["status"], "pending")
                self.assertEqual(workflow.main(["publish", "--draft", str(draft)]), 1)
                self.assertFalse((root / "profiles/fixture-demo.json").exists())

    def test_publish_update_requires_new_review_and_archives_previous_version(self):
        with tempfile.TemporaryDirectory(prefix="memorial-publish-") as temporary:
            root = Path(temporary)
            draft = root / "drafts/fixture-demo"
            profile = example()
            write_draft(draft, profile, approved_review(profile))
            with mock.patch.object(workflow, "ROOT", root):
                result = workflow.publish_draft(draft)
                self.assertEqual(ProfileRepository(root).load(profile["id"]), profile)
                self.assertTrue((Path(result["archive"]) / "review.json").is_file())
                profile["intro"] = "更新后的虚构测试简介"
                write_draft(draft, profile)
                with self.assertRaises(ProfileUnavailable):
                    workflow.publish_draft(draft, replace=True)
                self.assertNotEqual(ProfileRepository(root).load(profile["id"])["intro"], profile["intro"])
                write_draft(draft, profile, approved_review(profile))
                with self.assertRaises(ValueError):
                    workflow.publish_draft(draft)
                result = workflow.publish_draft(draft, replace=True)
                self.assertEqual(ProfileRepository(root).load(profile["id"]), profile)
                self.assertNotEqual(load_json(Path(result["archive"]) / "previous-profile.json")["intro"], profile["intro"])

    def test_failed_profile_write_restores_previous_review(self):
        with tempfile.TemporaryDirectory(prefix="memorial-rollback-") as temporary:
            root = Path(temporary)
            draft = root / "drafts/fixture-demo"
            profile = example(kind="real")
            write_draft(draft, profile, approved_review(profile))
            with mock.patch.object(workflow, "ROOT", root):
                workflow.publish_draft(draft)
                profile_path = root / "profiles/fixture-demo.json"
                review_path = root / "reviews/fixture-demo.json"
                original_profile, original_review = profile_path.read_bytes(), review_path.read_bytes()
                profile["intro"] = "模拟写入失败的新版简介"
                write_draft(draft, profile, approved_review(profile))
                real_write = workflow.write_json

                def interrupted_write(path, value):
                    if path == profile_path:
                        raise OSError("fixture write failure")
                    return real_write(path, value)

                with mock.patch.object(workflow, "write_json", side_effect=interrupted_write), self.assertRaises(OSError):
                    workflow.publish_draft(draft, replace=True)
                self.assertEqual(profile_path.read_bytes(), original_profile)
                self.assertEqual(review_path.read_bytes(), original_review)
                self.assertEqual(ProfileRepository(root).load(profile["id"])["intro"], example()["intro"])

    def test_invalid_json_and_duplicate_default_never_break_listing(self):
        with tempfile.TemporaryDirectory(prefix="memorial-corrupt-") as temporary:
            root = Path(temporary)
            workflow.write_json(root / "profiles/demo.json", load_json(ROOT / "profiles/demo.json"))
            workflow.write_json(root / "profiles/lin-shu-demo.json", load_json(ROOT / "profiles/demo.json"))
            (root / "profiles/broken.json").write_text("[null", encoding="utf-8")
            repository = ProfileRepository(root)
            self.assertEqual(repository.list_profiles(), [])
            self.assertTrue(all(not record["ok"] for record in repository.inspect()))
            with self.assertRaises(ProfileUnavailable):
                repository.load("lin-shu-demo")

    def test_single_profile_package_excludes_other_customers_and_drafts(self):
        with tempfile.TemporaryDirectory(prefix="memorial-scope-") as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary)
            for name in ("demo.json", "another-demo.json"):
                workflow.write_json(root / "profiles" / name, load_json(ROOT / "profiles" / name))
            customer = example("customer-demo")
            workflow.write_json(root / "profiles/customer-demo.json", customer)
            workflow.write_json(root / "reviews/customer-demo.json", approved_review(customer))
            workflow.write_json(root / "drafts/private/profile.json", {"private": "fixture-private"})
            with mock.patch.object(packager, "ROOT", root):
                self.assertEqual(packager.main(["--out", "demo.zip"]), 0)
                with zipfile.ZipFile(root / "demo.zip") as archive:
                    self.assertIn("profiles/demo.json", archive.namelist())
                    self.assertNotIn("profiles/customer-demo.json", archive.namelist())
                self.assertEqual(packager.main(["--profile", "customer-demo", "--out", "customer.zip"]), 0)
                with zipfile.ZipFile(root / "customer.zip") as archive:
                    self.assertEqual([name for name in archive.namelist() if name.startswith("profiles/")], ["profiles/customer-demo.json"])
                    self.assertIn("reviews/customer-demo.json", archive.namelist())
                    self.assertFalse(any(name.startswith("drafts/") for name in archive.namelist()))

    def test_unapproved_new_profile_cannot_get_link_or_delivery_package(self):
        for kind in ("fictional", "real"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(prefix="memorial-gates-") as temporary, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                root = Path(temporary)
                profile = example(kind=kind)
                workflow.write_json(root / "profiles/fixture-demo.json", profile)
                with mock.patch.object(linker, "ROOT", root), mock.patch.object(packager, "ROOT", root):
                    self.assertEqual(linker.main(["--profile", profile["id"], "--base-url", "http://127.0.0.1:4174/web/", "--out", "links"]), 1)
                    self.assertFalse((root / "links").exists())
                    self.assertEqual(ProfileRepository(root).list_profiles(), [])
                    self.assertEqual(packager.main(["--profile", profile["id"], "--out", "rejected.zip"]), 1)
                    self.assertFalse((root / "rejected.zip").exists())


class ProfileApiTests(unittest.TestCase):
    def test_private_review_is_not_returned_and_withdrawal_blocks_all_person_apis(self):
        with tempfile.TemporaryDirectory(prefix="memorial-api-") as temporary:
            root = Path(temporary)
            profile = example(kind="real")
            review = approved_review(profile)
            workflow.write_json(root / "profiles/fixture-demo.json", profile)
            workflow.write_json(root / "reviews/fixture-demo.json", review)
            (root / "web").mkdir()

            class QuietHandler(server_module.Handler):
                def log_message(self, *args):
                    pass

            with mock.patch.multiple(server_module, ROOT=root, WEB_ROOT=root / "web"):
                service = server_module.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
                worker = threading.Thread(target=service.serve_forever, daemon=True)
                worker.start()
                base = f"http://127.0.0.1:{service.server_port}"

                def request(path, payload=None):
                    data = json.dumps(payload).encode() if payload is not None else None
                    query = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
                    try:
                        reply = urllib.request.urlopen(query, timeout=2)
                    except urllib.error.HTTPError as error:
                        reply = error
                    with reply:
                        raw = reply.read().decode()
                        return reply.status, json.loads(raw) if raw.startswith("{") else raw

                try:
                    status, public = request("/api/profile?id=fixture-demo")
                    self.assertEqual(status, 200)
                    for private_value in (review["reviewer"], "fixture-material-001", "fixture-authorization-001"):
                        self.assertNotIn(private_value, json.dumps(public, ensure_ascii=False))
                    self.assertEqual(request("/reviews/fixture-demo.json")[0], 404)
                    self.assertEqual(request("/web/../reviews/fixture-demo.json")[0], 404)
                    review["authorization"]["status"] = "withdrawn"
                    workflow.write_json(root / "reviews/fixture-demo.json", review)
                    for path, body in (
                        ("/api/profile?id=fixture-demo", None),
                        ("/api/chat", {"profileId": "fixture-demo", "message": "你好"}),
                        ("/api/tts", {"profileId": "fixture-demo", "text": "你好"}),
                    ):
                        with self.subTest(path=path):
                            status, body = request(path, body)
                            self.assertEqual(status, 503)
                            self.assertEqual(body["error"], "profile_unavailable")
                    self.assertEqual(request("/api/profiles")[1]["profiles"], [])
                finally:
                    service.shutdown()
                    worker.join(timeout=3)
                    service.server_close()


if __name__ == "__main__":
    unittest.main()
