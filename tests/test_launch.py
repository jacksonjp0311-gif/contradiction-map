"""First-use launch behavior: existing apps, foreign ports and Windows entry point."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from contradiction_map import server
from contradiction_map.__main__ import main

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "a" * 43
HTML = ('<html><meta name="session-token" content="' + TOKEN + '"></html>').encode()
INFO = {"app_id": "contradiction-map", "version": "0.1.0", "limits": {"max_files": 500}}


@contextlib.contextmanager
def running_service(handler=None):
    httpd = server.LocalServer(("127.0.0.1", 0)) if handler is None else ThreadingHTTPServer(("127.0.0.1", 0), handler)
    worker = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    worker.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


def foreign_handler(root=HTML, info=None, root_status=200, info_status=200,
                    root_type="text/html", info_type="application/json", app_header=False):
    requests = []
    class ForeignHandler(BaseHTTPRequestHandler):
        server_version = "ContradictionMap" if app_header else "UnrelatedApplication"
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append((self.path, self.headers.get("X-Contradiction-Token")))
            is_root = self.path == "/"
            body = root if is_root else json.dumps(info).encode()
            self.send_response(root_status if is_root else info_status)
            self.send_header("Content-Type", root_type if is_root else info_type)
            self.send_header("Content-Length", str(len(body)))
            # Any redirect must remain unvisited; it is not an alternative probe URL.
            self.send_header("Location", "https://unrelated.invalid/do-not-follow")
            self.end_headers()
            self.wfile.write(body)
    return ForeignHandler, requests


class LaunchTests(unittest.TestCase):
    def test_repeated_open_reuses_real_token_protected_service(self):
        with running_service() as httpd, mock.patch.object(server.webbrowser, "open", return_value=True) as browser:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["serve", "--port", str(httpd.server_port), "--open"])
            self.assertEqual(code, 0)
            browser.assert_called_once_with(f"http://127.0.0.1:{httpd.server_port}")
            self.assertIn("running Contradiction Map", output.getvalue())

    def test_without_open_does_not_reuse_or_probe_a_running_service(self):
        with running_service() as httpd, mock.patch.object(server, "_is_existing_service") as probe, mock.patch.object(server.webbrowser, "open") as browser:
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                code = main(["serve", "--port", str(httpd.server_port)])
            self.assertEqual(code, 2)
            self.assertIn("already in use", errors.getvalue())
            self.assertNotIn("Traceback", errors.getvalue())
            probe.assert_not_called()
            browser.assert_not_called()

    def test_unrelated_application_is_not_opened(self):
        handler, requests = foreign_handler(info={"version": "1", "limits": {}, "app_id": "other-app"})
        with running_service(handler) as httpd, mock.patch.object(server.webbrowser, "open") as browser:
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                code = main(["serve", "--port", str(httpd.server_port), "--open"])
            self.assertEqual(code, 2)
            self.assertIn("--port 8768", errors.getvalue())
            self.assertEqual({path for path, _ in requests}, {"/", "/api/info"})
            self.assertTrue(all(token == TOKEN for path, token in requests if path == "/api/info"))
            browser.assert_not_called()

    def test_metadata_without_explicit_app_identity_is_not_enough(self):
        handler, _ = foreign_handler(info={"version": "0.1.0", "limits": {"max_files": 500}})
        with running_service(handler) as httpd:
            self.assertFalse(server._is_existing_service(httpd.server_port))

    def test_redirects_are_not_followed_at_either_probe_step(self):
        for root_status, info_status, expected in [(302, 200, ["/"]), (200, 302, ["/", "/api/info"])]:
            with self.subTest(root_status=root_status, info_status=info_status):
                handler, requests = foreign_handler(info=INFO, root_status=root_status, info_status=info_status)
                with running_service(handler) as httpd:
                    self.assertFalse(server._is_existing_service(httpd.server_port))
                self.assertEqual([path for path, _ in requests], expected)

    def test_probe_bounds_html_before_requesting_api(self):
        handler, requests = foreign_handler(root=HTML + b" " * server.MAX_PROBE_BYTES, info=INFO)
        with running_service(handler) as httpd:
            self.assertFalse(server._is_existing_service(httpd.server_port))
        self.assertEqual(requests, [("/", None)])

    def test_invalid_or_duplicate_session_tokens_are_rejected(self):
        for root in [b"<html>Another app</html>", b'<meta name="session-token" content="short">', HTML + HTML]:
            with self.subTest(root=root):
                handler, requests = foreign_handler(root=root, info=INFO)
                with running_service(handler) as httpd:
                    self.assertFalse(server._is_existing_service(httpd.server_port))
                self.assertEqual(requests, [("/", None)])

    def test_non_json_api_response_is_not_app_identity(self):
        handler, _ = foreign_handler(info=INFO, info_type="text/plain")
        with running_service(handler) as httpd:
            self.assertFalse(server._is_existing_service(httpd.server_port))

    def test_second_direct_server_cannot_bind_the_live_port(self):
        with running_service() as httpd:
            with self.assertRaises(OSError):
                server.LocalServer(("127.0.0.1", httpd.server_port))
            self.assertTrue(server._is_existing_service(httpd.server_port))

    def test_known_legacy_instance_is_recognized_without_restart(self):
        legacy = {"version": "0.1.0", "privacy": server.PRIVACY_MESSAGE,
                  "limits": {"max_files": server.MAX_FILES, "max_file_bytes": server.MAX_FILE_BYTES,
                             "max_bytes": server.MAX_TOTAL_BYTES, "max_total_bytes": server.MAX_TOTAL_BYTES,
                             "max_request_bytes": server.MAX_REQUEST_BYTES}}
        root = b'<title>Contradiction Map &#183; Evidence, connected</title>' + HTML
        handler, _ = foreign_handler(root=root, info=legacy, app_header=True)
        with running_service(handler) as httpd:
            self.assertTrue(server._is_existing_service(httpd.server_port))

    def test_partial_or_wrong_legacy_fingerprint_is_rejected(self):
        legacy = {"version": "0.1.0", "privacy": server.PRIVACY_MESSAGE,
                  "limits": {"max_files": server.MAX_FILES, "max_file_bytes": server.MAX_FILE_BYTES,
                             "max_bytes": server.MAX_TOTAL_BYTES, "max_total_bytes": server.MAX_TOTAL_BYTES,
                             "max_request_bytes": server.MAX_REQUEST_BYTES}}
        root = b'<title>Contradiction Map &#183; Evidence, connected</title>' + HTML
        cases = [(root, dict(legacy, version="0.2.0"), True),
                 (root, dict(legacy, privacy="Unrelated application"), True),
                 (HTML, legacy, True), (root, legacy, False),
                 (root, dict(legacy, app_id="other-app"), True)]
        for page, metadata, header in cases:
            with self.subTest(page=page[:20], metadata=metadata, app_header=header):
                handler, _ = foreign_handler(root=page, info=metadata, app_header=header)
                with running_service(handler) as httpd:
                    self.assertFalse(server._is_existing_service(httpd.server_port))

    def test_human_cli_marks_long_line_excerpt_and_exact_column(self):
        with tempfile.TemporaryDirectory(prefix="contradiction-cli-") as folder:
            target = Path(folder)
            (target / "package.json").write_text(json.dumps({"name": "sample", "padding": "x" * 1000, "version": "2"}), encoding="utf-8")
            (target / "package-lock.json").write_text('{"name":"sample","version":"1"}', encoding="utf-8")
            result = subprocess.run([sys.executable, "-B", "-m", "contradiction_map", "scan", folder],
                                    cwd=ROOT, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertRegex(result.stdout, r"package\.json:1:1[0-9]{3}")
            self.assertIn('"version": "2"', result.stdout)
            self.assertIn("bounded excerpt begins at column", result.stdout)
            self.assertIn("line truncated", result.stdout)

    @unittest.skipUnless(sys.platform == "win32", "Windows command launcher")
    def test_cmd_launcher_forwards_scan_arguments(self):
        result = subprocess.run(["cmd.exe", "/d", "/c", "call", str(ROOT / "Start-ContradictionMap.cmd"),
                                 "scan", str(ROOT / "fixtures/clean"), "--json"], cwd=ROOT.parent,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["summary"]["findings"], 0)


if __name__ == "__main__":
    unittest.main()
