"""Observable API, cancellation, archive safety and source privacy regression checks."""
import http.client
import io
import json
import threading
import time
import unittest
import zipfile
from unittest import mock

from contradiction_map import server


def archive(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, body in entries.items():
            bundle.writestr(name, body)
    return stream.getvalue()


class CompanionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.LocalServer(("127.0.0.1", 0))
        cls.worker = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.worker.join(timeout=2)

    def request(self, path, method="GET", body=None, token=True, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=4)
        extra = {"X-Contradiction-Token": self.httpd.token} if token else {}
        if body is not None:
            body = json.dumps(body)
            extra["Content-Type"] = "application/json"
        extra.update(headers or {})
        connection.request(method, path, body=body, headers=extra)
        response = connection.getresponse()
        status, head, data = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return status, head, data

    def test_loopback_only(self):
        with self.assertRaises(ValueError):
            server.LocalServer(("0.0.0.0", 0))

    def test_api_requires_token(self):
        self.assertEqual(self.request("/api/info", token=False)[0], 403)
        self.assertEqual(self.request("/api/pick-folder", "POST", {}, token=False)[0], 403)

    def test_native_picker_is_bounded_explicit_fallback(self):
        status, _, body = self.request("/api/pick-folder", "POST", {})
        self.assertEqual(status, 503)
        self.assertIn("browser", json.loads(body)["error"])

    def test_rebinding_and_cross_origin_rejected(self):
        self.assertEqual(self.request("/api/info", headers={"Host": "malicious.example"})[0], 403)
        self.assertEqual(self.request("/api/info", headers={"Origin": "https://malicious.example"})[0], 403)
        self.assertEqual(self.request("/api/info", headers={"Origin": "null"})[0], 403)

    def test_info_and_security_headers(self):
        status, headers, body = self.request("/api/info")
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["limits"]["max_files"], 500)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_ui_injects_session_token_and_demo_uses_bundled_fixture(self):
        status, _, body = self.request("/", token=False)
        self.assertEqual(status, 200)
        self.assertNotIn(b"__SESSION_TOKEN__", body)
        self.assertIn(self.httpd.token.encode("ascii"), body)
        status, _, body = self.request("/api/scan", "POST", {"mode": "demo"})
        self.assertEqual(status, 202)
        result = self.wait_job(json.loads(body)["job_id"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["result"]["target"]["kind"], "demo")
        self.assertEqual(result["result"]["summary"]["deterministic"], 2)
        self.assertEqual(result["result"]["summary"]["heuristic"], 3)

    def test_static_path_cannot_escape(self):
        for path in ("/../server.py", "/%2e%2e/server.py", "/C:/Windows/win.ini", "/app.js/../server.py"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)

    def test_bad_and_oversized_payload(self):
        self.assertEqual(self.request("/api/scan", "POST", [1, 2])[0], 400)
        self.assertEqual(self.request("/api/scan", "POST", {"mode": "unknown"})[0], 400)
        self.assertEqual(self.request("/api/scan", "POST", {}, headers={"Content-Length": str(server.MAX_REQUEST_BYTES + 1)})[0], 400)

    def test_selected_file_conflict_and_repeat(self):
        selected = {"mode": "files", "files": [
            {"path": "package.json", "content": '{"name":"sample","version":"1.0.0"}'},
            {"path": "package-lock.json", "content": '{"name":"sample","version":"2.0.0","lockfileVersion":1}'}]}
        for _ in range(2):
            status, _, body = self.request("/api/scan", "POST", selected)
            self.assertEqual(status, 202)
            result = self.wait_job(json.loads(body)["job_id"])
            self.assertEqual(result["status"], "complete")
            self.assertTrue(any(f["rule_id"] == "NPM001" for f in result["result"]["findings"]))
            self.assertNotIn("cancel", result)

    def wait_job(self, job_id):
        for _ in range(100):
            status, _, body = self.request("/api/jobs/" + job_id)
            self.assertEqual(status, 200)
            result = json.loads(body)
            if result["status"] != "running":
                return result
            time.sleep(0.01)
        self.fail("Job did not finish")

    def test_duplicate_paths_rejected_before_scan(self):
        status, _, body = self.request("/api/scan", "POST", {"mode": "files", "files": [{"path": "a.py", "content": ""}, {"path": "A.py", "content": ""}]})
        self.assertEqual(status, 400)
        self.assertIn("Duplicate", json.loads(body)["error"])

    def test_unknown_job_and_cancel(self):
        self.assertEqual(self.request("/api/jobs/missing")[0], 404)
        self.assertEqual(self.request("/api/jobs/missing/cancel", "POST", {})[0], 404)


class JobTests(unittest.TestCase):
    def test_cancel_releases_slot_only_after_worker_stops(self):
        entered, release = threading.Event(), threading.Event()
        def slow_scan(*args, cancel=None, **kwargs):
            entered.set()
            release.wait(timeout=2)
            return {"target": {}, "summary": {}}
        manager = server.JobManager()
        with mock.patch.object(server, "scan_files", side_effect=slow_scan):
            job_id = manager.start({"mode": "files", "files": [{"path": "x.py", "content": ""}]})
            self.assertTrue(entered.wait(1))
            manager.cancel(job_id)
            with self.assertRaises(BlockingIOError):
                manager.start({"mode": "demo"})
            release.set()
            for _ in range(100):
                result = manager.get(job_id)
                if result["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(result, {"status": "cancelled"})
            self.assertIsNone(manager.active)

    def test_internal_errors_hide_traceback_and_details(self):
        manager = server.JobManager()
        with mock.patch.object(server, "scan_files", side_effect=RuntimeError("SECRET_PRIVATE_CONTENT")):
            job_id = manager.start({"mode": "files", "files": [{"path": "x.py", "content": ""}]})
            for _ in range(100):
                result = manager.get(job_id)
                if result["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(result["status"], "error")
            self.assertNotIn("SECRET_PRIVATE_CONTENT", str(result))


class PublicImportTests(unittest.TestCase):
    metadata = json.dumps({"private": False, "visibility": "public", "default_branch": "main"}).encode()

    def run_import(self, entries):
        with mock.patch.object(server, "_fetch", side_effect=[self.metadata, archive(entries)]) as fetch:
            result = server.public_github("https://github.com/example/synthetic.git", lambda: False)
            self.assertEqual(fetch.call_args_list[0].args[0], "https://api.github.com/repos/example/synthetic")
            self.assertTrue(fetch.call_args_list[1].args[0].startswith("https://codeload.github.com/"))
            return result

    def test_import_uses_same_engine_without_extraction(self):
        result = self.run_import({"synthetic-main/package.json": '{"name":"sample","version":"1"}', "synthetic-main/package-lock.json": '{"name":"sample","version":"2","lockfileVersion":1}'})
        self.assertEqual(result["target"]["kind"], "url")
        self.assertEqual(len(result["findings"]), 1)

    def test_url_cannot_reach_other_hosts_or_credentials(self):
        urls = ["http://github.com/a/b", "https://github.com.evil/a/b", "https://user:secret@github.com/a/b", "https://127.0.0.1/a/b", "https://github.com/a/b?x=1", "https://github.com/a/b/tree/main", "https://github.com/a/..", "file:///C:/x"]
        with mock.patch.object(server, "_fetch") as fetch:
            for url in urls:
                with self.subTest(url=url), self.assertRaises(server.RequestError):
                    server.public_github(url, lambda: False)
            fetch.assert_not_called()

    def test_private_metadata_rejected_without_download(self):
        with mock.patch.object(server, "_fetch", return_value=b'{"private":true,"default_branch":"main"}') as fetch:
            with self.assertRaises(server.RequestError):
                server.public_github("https://github.com/a/b", lambda: False)
            self.assertEqual(fetch.call_count, 1)

    def test_archive_traversal_rejected(self):
        with self.assertRaises(server.RequestError):
            self.run_import({"root/../../escape.py": "PORT = 1"})

    def test_secrets_and_vendor_are_excluded(self):
        result = self.run_import({"root/.env": "PASSWORD=not-a-real-secret", "root/vendor/private.py": "PORT=3", "root/app.py": "PORT=4"})
        self.assertEqual([f["path"] for f in result["files"]], ["app.py"])

    def test_cancellation_stops_before_network(self):
        with mock.patch.object(server.urllib.request, "build_opener") as opener:
            with self.assertRaises(server.RequestError):
                server._fetch("https://api.github.com/repos/a/b", 100, lambda: True, time.monotonic() + 5)
            opener.assert_not_called()

    def test_redirects_are_not_followed(self):
        with self.assertRaises(server.RequestError):
            server.NoRedirect().redirect_request(None, None, 302, "Moved", {}, "https://evil.example/archive")


if __name__ == "__main__":
    unittest.main()
