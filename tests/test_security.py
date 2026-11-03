"""Independent adversarial checks using synthetic input; no external network or repo code execution."""
from __future__ import annotations

import contextlib
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
import zipfile

from contradiction_map import engine, server
from contradiction_map import __main__ as cli


class InputBoundaryTests(unittest.TestCase):
    def test_relative_paths_reject_windows_and_posix_escapes(self):
        names = ["../outside.py", "x/../outside.py", "/etc/passwd.py", "C:/outside.py", "C:outside.py", "\\\\server\\share\\outside.py", "x.py:stream", "a//b.py", "./x.py", "folder./x.py", "folder /x.py", "a\x00.py"]
        for name in names:
            with self.subTest(path=name), self.assertRaises(engine.ScanError):
                engine.scan_files({name: "PORT=8000"})

    def test_secret_directories_never_become_evidence(self):
        files = {"app.py": "PORT=8000"}
        for folder in ("secrets", "credentials", ".aws", ".ssh", ".gnupg"):
            files[folder + "/package.json"] = '{"name":"synthetic","version":"1"}'
            files[folder + "/package-lock.json"] = '{"name":"synthetic","version":"2"}'
        result = engine.scan_files(files)
        self.assertEqual([row["path"] for row in result["files"]], ["app.py"])
        self.assertEqual(result["findings"], [])

    def test_local_and_uploaded_secret_exclusions_agree(self):
        files = {"app.py": "PORT=8000", ".env": "SYNTHETIC_VALUE=placeholder", "vendor/package.json": '{"name":"x","version":"1"}', "secrets/config.py": "PORT=9000"}
        with tempfile.TemporaryDirectory(prefix="contradiction-security-") as tmp:
            root = Path(tmp)
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            local = engine.scan(root)
        uploaded = engine.scan_files(files)
        self.assertEqual(local["files"], uploaded["files"])
        self.assertEqual([row["path"] for row in local["files"]], ["app.py"])

    def test_invalid_json_cannot_produce_deterministic_conflict(self):
        for bad in ('{"name":"x","version":"1",}', '{"name":"x","version":"1","dependencies":{"a":"1",}}', '{"name":"x","version":"1","extras":[1,]}'):
            with self.subTest(json=bad):
                report = engine.scan_files({"package.json": bad, "package-lock.json": '{"name":"x","version":"2"}'})
                self.assertEqual(report["findings"], [])
                self.assertTrue(report["coverage"]["warnings"])

    def test_valid_but_unsupported_toml_shapes_abstain(self):
        for document in ('project = "text"', 'project = []', '[project]\nname="x"\nversion="1"\ndynamic=7'):
            with self.subTest(toml=document):
                result = engine.scan_files({"pyproject.toml": document, "x.py": '__version__="2"'})
                self.assertEqual(result["findings"], [])
                self.assertTrue(result["coverage"]["warnings"])

    def test_non_utf8_surrogate_is_a_supported_input_error(self):
        with self.assertRaises(engine.ScanError):
            engine.scan_files({"app.py": "\ud800"})

    def test_non_utf8_surrogate_path_is_a_supported_input_error(self):
        with self.assertRaises(engine.ScanError):
            engine.scan_files({"\ud800.py": "PORT=8000"})

    def test_selected_code_is_parsed_without_execution(self):
        report = engine.scan_files({"app.py": 'raise RuntimeError("this target must never execute")\nPORT=8000\n', "README.md": "The default port is 9000."})
        self.assertEqual(report["summary"]["files_scanned"], 2)
        self.assertTrue(any(row["rule_id"] == "DOC001" for row in report["findings"]))

    def test_file_and_entry_limits_mark_partial_results(self):
        with mock.patch.object(engine, "MAX_FILES", 2):
            result = engine.scan_files({"a.md":"a", "b.md":"b", "c.md":"c"})
        self.assertEqual(result["summary"]["files_scanned"], 2)
        self.assertTrue(result["coverage"]["truncated"])
        with mock.patch.object(engine, "MAX_ENTRIES", 2):
            result = engine.scan_files({"a.bin":"a", "b.bin":"b", "c.md":"c"})
        self.assertEqual(result["summary"]["files_scanned"], 0)
        self.assertTrue(result["coverage"]["truncated"])

    def test_cancellation_interrupts_collection(self):
        with self.assertRaises(engine.ScanCancelled):
            engine.scan_files({"a.py": "PORT=8000"}, cancel=lambda: True)

    def test_nested_link_does_not_reveal_outside_contents(self):
        with tempfile.TemporaryDirectory(prefix="contradiction-links-") as tmp:
            parent = Path(tmp)
            target, source = parent / "target", parent / "source"
            target.mkdir()
            source.mkdir()
            (target / "package.json").write_text('{"name":"x","version":"1"}', encoding="utf-8")
            (target / "package-lock.json").write_text('{"name":"x","version":"2"}', encoding="utf-8")
            try:
                (source / "linked").symlink_to(target, target_is_directory=True)
            except OSError as exc:
                self.skipTest("Creating symbolic links is not permitted by this Windows account: " + type(exc).__name__)
            report = engine.scan(source)
            self.assertEqual(report["files"], [])
            self.assertEqual(report["findings"], [])
            self.assertGreater(report["summary"]["files_skipped"], 0)


class HumanCliBoundaryTests(unittest.TestCase):
    def test_source_control_codes_cannot_control_the_terminal(self):
        report = engine.scan_files({"app.py": "PORT=8000 # \x1b[31mUNTRUSTED\x1b[0m\n", "README.md": "The default port is 9000."})
        self.assertTrue(report["findings"])
        output = io.StringIO()
        with mock.patch.object(cli, "scan", return_value=report), contextlib.redirect_stdout(output):
            status = cli.main(["scan", "synthetic"])
        self.assertEqual(status, 0)
        self.assertNotIn("\x1b", output.getvalue())
        self.assertIn("UNTRUSTED", output.getvalue())
        self.assertRegex(output.getvalue(), r"\\(?:u001b|x1b)")


class RequestAdversarialTests(unittest.TestCase):
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

    def request(self, path, body=None, token=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=4)
        headers = {"X-Contradiction-Token": self.httpd.token if token is None else token}
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request("POST" if body is not None else "GET", path, body=body, headers=headers)
        response = connection.getresponse()
        status, data = response.status, response.read()
        connection.close()
        return status, json.loads(data)

    def test_non_ascii_token_is_rejected_without_handler_failure(self):
        status, result = self.request("/api/info", token="\u00e9")
        self.assertEqual(status, 403)
        self.assertIn("error", result)

    def test_deep_json_is_a_bounded_input_error(self):
        status, result = self.request("/api/scan", body='{"payload":' + '[' * 1500 + '0' + ']' * 1500 + '}')
        self.assertEqual(status, 400)
        self.assertIn("error", result)

    def test_native_picker_has_no_get_side_effect(self):
        status, result = self.request("/api/pick-folder")
        self.assertEqual(status, 404)
        self.assertIn("error", result)


class ArchiveAdversarialTests(unittest.TestCase):
    metadata = b'{"private":false,"visibility":"public","default_branch":"main"}'

    def import_archive(self, data):
        with mock.patch.object(server, "_fetch", side_effect=[self.metadata, data]):
            return server.public_github("https://github.com/synthetic/example", lambda: False)

    def test_archive_symlink_is_skipped(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            link = zipfile.ZipInfo("root/external.py")
            link.create_system = 3
            link.external_attr = 0o120777 << 16
            archive.writestr(link, "../../private.py")
            archive.writestr("root/app.py", "PORT=8000")
        result = self.import_archive(buffer.getvalue())
        self.assertEqual([row["path"] for row in result["files"]], ["app.py"])

    def test_archive_uncompressed_limit_is_enforced(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("root/a.py", "x" * 120)
        with mock.patch.object(server, "MAX_ARCHIVE_BYTES", 100), self.assertRaises(server.RequestError):
            self.import_archive(buffer.getvalue())

    def test_archive_member_limit_is_enforced(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("root/a.py", "PORT=8000")
            archive.writestr("root/b.py", "PORT=9000")
        with mock.patch.object(server, "MAX_ARCHIVE_MEMBERS", 1), self.assertRaises(server.RequestError):
            self.import_archive(buffer.getvalue())

    def test_case_ambiguous_archive_paths_are_rejected(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("root/app.py", "PORT=8000")
            archive.writestr("root/APP.py", "PORT=9000")
        with self.assertRaises((server.RequestError, engine.ScanError)):
            self.import_archive(buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
