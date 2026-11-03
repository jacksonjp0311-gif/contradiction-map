"""Loopback-only companion UI. Never executes code from analyzed repositories."""
from __future__ import annotations

import hmac
import io
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath

from . import __version__
from .engine import MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES, ScanError, _excluded, _safe_name, scan, scan_files

MAX_REQUEST_BYTES = 12 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20000
NETWORK_TIMEOUT = 12
IMPORT_DEADLINE = 45
WEB_ROOT = Path(__file__).parent / "web"
DEMO_ROOT = Path(__file__).resolve().parent / "demo"
REPO_URL = re.compile(r"https://github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]{1,100})/?\Z", re.IGNORECASE)


class RequestError(ValueError):
    """A bounded user-readable input error."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RequestError("GitHub redirected this request; use the repository's current canonical URL.")


def _check_cancel(cancel):
    if cancel():
        raise RequestError("Scan cancelled.")


def _fetch(url, limit, cancel, deadline):
    """Only called with constructed GitHub API / codeload URLs; never user URLs."""
    _check_cancel(cancel)
    if time.monotonic() > deadline:
        raise RequestError("Public import exceeded its 45-second time budget.")
    opener = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "ContradictionMap/" + __version__, "Accept": "application/vnd.github+json"})
    try:
        with opener.open(req, timeout=NETWORK_TIMEOUT) as response:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > limit:
                raise RequestError("Public download exceeds the size limit; select a local folder.")
            chunks, total = [], 0
            while True:
                _check_cancel(cancel)
                if time.monotonic() > deadline:
                    raise RequestError("Public import exceeded its 45-second time budget.")
                chunk = response.read(min(65536, limit + 1 - total))
                if not chunk:
                    return b"".join(chunks)
                total += len(chunk)
                if total > limit:
                    raise RequestError("Public download exceeds the size limit; select a local folder.")
                chunks.append(chunk)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RequestError("Public repository not found. Private repositories are intentionally unsupported.") from None
        if exc.code in (403, 429):
            raise RequestError("GitHub's unauthenticated request limit was reached; select a local folder or retry later.") from None
        raise RequestError(f"GitHub returned HTTP {exc.code}.") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RequestError("Unable to download this public repository. Check connectivity or select a local folder.") from exc


def public_github(url, cancel):
    match = REPO_URL.fullmatch(url.strip())
    if not match:
        raise RequestError("Use a public https://github.com/owner/repository URL without credentials, query, branch, or fragment.")
    owner, repository = match.groups()
    if repository.endswith(".git"):
        repository = repository[:-4]
    if repository in ("", ".", ".."):
        raise RequestError("Invalid repository name.")
    deadline = time.monotonic() + IMPORT_DEADLINE
    metadata = json.loads(_fetch(f"https://api.github.com/repos/{owner}/{repository}", 512 * 1024, cancel, deadline))
    if metadata.get("private") is not False or metadata.get("visibility", "public") != "public":
        raise RequestError("Only public GitHub repositories can be imported.")
    branch = metadata.get("default_branch")
    if not isinstance(branch, str) or not branch or len(branch) > 256:
        raise RequestError("GitHub did not return a valid default branch.")
    # Response-supplied URLs are never followed; both destination origins are fixed.
    ref = urllib.parse.quote(branch, safe="")
    archive = _fetch(f"https://codeload.github.com/{owner}/{repository}/zip/refs/heads/{ref}", MAX_DOWNLOAD_BYTES, cancel, deadline)
    files, expanded = {}, 0
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = bundle.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise RequestError("Archive has too many entries; select a smaller local folder.")
            for info in members:
                _check_cancel(cancel)
                if time.monotonic() > deadline:
                    raise RequestError("Public import exceeded its 45-second time budget.")
                expanded += info.file_size
                if expanded > MAX_ARCHIVE_BYTES:
                    raise RequestError("Expanded archive exceeds 32 MiB; select a smaller local folder.")
                path = PurePosixPath(info.filename)
                if info.filename.startswith(("/", "\\")) or "\\" in info.filename or any(x in ("..", ".") for x in path.parts):
                    raise RequestError("Unsafe path in public archive.")
                if len(path.parts) < 2 or info.is_dir():
                    continue
                # No extraction; reject symlinks and read bounded in-memory UTF-8 text.
                if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    continue
                relative = _safe_name("/".join(path.parts[1:]))
                if _excluded(relative):
                    continue
                if info.file_size > MAX_FILE_BYTES:
                    continue
                if relative in files:
                    raise RequestError("Duplicate file in public archive.")
                payload = bundle.read(info)
                if b"\x00" in payload:
                    continue
                try:
                    files[relative] = payload.decode("utf-8-sig")
                except UnicodeDecodeError:
                    continue
    except (zipfile.BadZipFile, RuntimeError, OSError, ValueError) as exc:
        if isinstance(exc, RequestError):
            raise
        raise RequestError("GitHub did not return a readable bounded ZIP archive.") from exc
    result = scan_files(files, name=f"{owner}/{repository}", cancel=cancel)
    result["target"] = {"name": f"{owner}/{repository}", "kind": "url"}
    result["coverage"].setdefault("warnings", []).append("Imported public default-branch archive without credentials. History, submodules and linked files were not downloaded; archive entries outside engine limits were skipped.")
    return result


class JobManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = OrderedDict()
        self.active = None

    def start(self, payload):
        mode = payload.get("mode")
        if mode not in ("path", "url", "demo", "files"):
            raise RequestError("Choose a local folder, selected files, public GitHub URL, or demo.")
        if mode in ("path", "url") and (not isinstance(payload.get("target"), str) or not payload["target"].strip()):
            raise RequestError("Enter a target first.")
        if mode == "files":
            rows = payload.get("files")
            if not isinstance(rows, list) or not rows:
                raise RequestError("Choose a folder or at least one text file.")
            if len(rows) > MAX_ARCHIVE_MEMBERS:
                raise RequestError("Too many selected entries.")
            names = set()
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("path"), str) or not isinstance(row.get("content"), str):
                    raise RequestError("Each selected file needs a relative path and text content.")
                key = row["path"].casefold()
                if key in names:
                    raise RequestError("Duplicate selected file paths are not supported.")
                names.add(key)
        with self.lock:
            if self.active is not None:
                raise BlockingIOError("A scan is already running. Cancel it before starting another.")
            job_id = secrets.token_hex(12)
            event = threading.Event()
            self.jobs[job_id] = {"status": "running", "cancel": event}
            self.active = job_id
            while len(self.jobs) > 8:
                self.jobs.popitem(last=False)
        threading.Thread(target=self._run, args=(job_id, payload, event), daemon=True).start()
        return job_id

    def _run(self, job_id, payload, event):
        try:
            mode = payload["mode"]
            if mode == "demo":
                result = scan(DEMO_ROOT, cancel=event.is_set)
                result["target"] = {"name": "Synthetic demo repository", "kind": "demo"}
            elif mode == "path":
                result = scan(Path(payload["target"].strip()).expanduser(), cancel=event.is_set)
            elif mode == "url":
                result = public_github(payload["target"], event.is_set)
            else:
                result = scan_files({r["path"]: r["content"] for r in payload["files"]}, name="Selected local files", cancel=event.is_set)
            outcome = {"status": "cancelled"} if event.is_set() else {"status": "complete", "result": result}
        except (ScanError, RequestError, ValueError) as exc:
            outcome = {"status": "cancelled"} if event.is_set() else {"status": "error", "error": str(exc)}
        except Exception:
            outcome = {"status": "cancelled"} if event.is_set() else {"status": "error", "error": "Analysis failed unexpectedly. Check the input and retry; no target code was executed."}
        with self.lock:
            if event.is_set():
                outcome = {"status": "cancelled"}
            self.jobs[job_id].update(outcome)
            self.active = None

    def get(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            return {k: v for k, v in job.items() if k != "cancel"} if job else None

    def cancel(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if job["status"] == "running":
                job["cancel"].set()
            return {"status": job["status"], "cancellation_requested": job["cancel"].is_set()}


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler=None):
        if address[0] != "127.0.0.1":
            raise ValueError("The companion server only binds to 127.0.0.1.")
        self.token = secrets.token_urlsafe(32)
        self.jobs = JobManager()
        super().__init__(address, handler or Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "ContradictionMap"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        return  # Do not log repository paths or URL contents.

    def _headers(self, status, content_type, size):
        self.send_response(status)
        for name, value in {"Content-Type": content_type, "Content-Length": str(size), "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'", "Connection": "close"}.items():
            self.send_header(name, value)
        self.end_headers()

    def _json(self, status, value):
        body = json.dumps(value, ensure_ascii=True).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _authorized(self, api=False):
        hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.headers.get("Host") not in hosts:
            self._json(403, {"error": "Invalid local Host header."})
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{h}" for h in hosts}:
            self._json(403, {"error": "Cross-origin requests are disabled."})
            return False
        if api and not hmac.compare_digest(self.headers.get("X-Contradiction-Token", "").encode("utf-8"), self.server.token.encode("ascii")):
            self._json(403, {"error": "Missing or expired local session token. Reload this page."})
            return False
        return True

    def _payload(self):
        if self.headers.get("Transfer-Encoding"):
            raise RequestError("Chunked requests are unsupported.")
        length = self.headers.get("Content-Length", "0")
        if len(length) > 10 or not length.isdigit():
            raise RequestError("Invalid Content-Length.")
        count = int(length)
        if count > MAX_REQUEST_BYTES:
            raise RequestError("Selected files exceed the 12 MiB request limit.")
        if self.headers.get_content_type() != "application/json":
            raise RequestError("Use JSON requests.")
        self.connection.settimeout(10)
        try:
            body = self.rfile.read(count)
            if len(body) != count:
                raise RequestError("Incomplete request body.")
            payload = json.loads(body or b"{}")
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TimeoutError) as exc:
            raise RequestError("Invalid or incomplete JSON request.") from exc
        if not isinstance(payload, dict):
            raise RequestError("JSON request must be an object.")
        return payload

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self._authorized(api=path.startswith("/api/")):
            return
        if path == "/api/info":
            self._json(200, {"version": __version__, "limits": {"max_files": MAX_FILES, "max_file_bytes": MAX_FILE_BYTES, "max_bytes": MAX_TOTAL_BYTES, "max_total_bytes": MAX_TOTAL_BYTES, "max_request_bytes": MAX_REQUEST_BYTES}, "privacy": "Local analysis stays on this machine. Public URL import contacts GitHub without credentials; no model APIs are used."})
        elif path.startswith("/api/jobs/"):
            job = self.server.jobs.get(path[len("/api/jobs/"):])
            self._json(200 if job else 404, job or {"error": "Scan not found; run a new scan."})
        else:
            static = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8")}
            if path not in static:
                self._json(404, {"error": "Not found."})
                return
            name, content_type = static[path]
            try:
                body = (WEB_ROOT / name).read_bytes()
            except OSError:
                self._json(503, {"error": "UI files are unavailable in this installation."})
                return
            if name == "index.html":
                body = body.replace(b"__SESSION_TOKEN__", self.server.token.encode("ascii"))
            self._headers(200, content_type, len(body))
            self.wfile.write(body)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self._authorized(api=True):
            return
        try:
            payload = self._payload()
            if path == "/api/scan":
                self._json(202, {"job_id": self.server.jobs.start(payload)})
            elif path.startswith("/api/jobs/") and path.endswith("/cancel"):
                job_id = path[len("/api/jobs/"):-len("/cancel")]
                status = self.server.jobs.cancel(job_id)
                self._json(200 if status else 404, status or {"error": "Scan not found."})
            elif path == "/api/pick-folder":
                self._json(503, {"error": "Native folder dialogs are unavailable in this runtime. Use Choose folder in the browser or enter a local path."})
            else:
                self._json(404, {"error": "Not found."})
        except BlockingIOError as exc:
            self._json(409, {"error": str(exc)})
        except (RequestError, OSError) as exc:
            self._json(400, {"error": str(exc) if isinstance(exc, RequestError) else "Request could not be completed."})

    def do_OPTIONS(self):
        self._json(405, {"error": "Cross-origin access is disabled."})


def serve(host="127.0.0.1", port=8765, open_browser=False):
    with LocalServer((host, port)) as httpd:
        url = f"http://127.0.0.1:{httpd.server_port}"
        print(f"Contradiction Map {__version__} | {url}")
        print("Local companion only. Ctrl+C to stop. No model APIs or target code execution.")
        if open_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            if httpd.jobs.active:
                httpd.jobs.cancel(httpd.jobs.active)
