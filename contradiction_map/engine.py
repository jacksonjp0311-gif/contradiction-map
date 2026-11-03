"""Read-only, bounded repository analysis. Target code is never imported or executed."""
from __future__ import annotations

import ast
import bisect
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
import tomllib
from typing import Callable, Mapping

MAX_FILES = 500
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 20_000
MAX_FINDINGS = 500
SCHEMA_VERSION = "1.0"
EXCLUDED_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "vendor", "vendors", "dist", "build", "__pycache__", ".next", ".nuxt", "coverage", ".cache", ".tox", ".mypy_cache", ".pytest_cache", "site-packages", "target", ".idea", ".vscode", "secrets", "credentials", ".aws", ".ssh", ".gnupg"}
TEXT_EXTENSIONS = {".py", ".json", ".toml", ".md", ".markdown", ".txt", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".sh", ".ps1", ".rst", ".example", ".sample"}
SPECIAL_FILES = {"dockerfile", "makefile", "license", "readme", ".gitignore", ".env.example", ".env.sample", ".env.template"}
RULES = [
 {"id":"NPM001", "name":"Package / lock root version", "kind":"deterministic", "limitations":"Compares parsed literal metadata for the same package identity; does not prove installed dependency state. Supports package-lock v1–v3."},
 {"id":"NPM002", "name":"Package / lock root dependency declaration", "kind":"deterministic", "limitations":"Compares direct dependencies and devDependencies in package-lock v2–v3 root metadata. Optional and peer dependency semantics are outside this rule."},
 {"id":"PY001", "name":"Python project / module version", "kind":"heuristic", "limitations":"Only static [project].version and a matching root/src package __version__; projects may intentionally expose a different runtime version."},
 {"id":"PY002", "name":"Literal constant / test equality", "kind":"heuristic", "limitations":"Only unmodified direct from-module imports and literal equality assertions. Never runs tests or proves import-time/runtime behavior; mocking and side effects may explain a mismatch."},
 {"id":"DOC001", "name":"Documented default port / Python default", "kind":"heuristic", "limitations":"Only explicit default-port prose and one unambiguous Python PORT literal/default. Multiple services, intentional examples or runtime overrides may explain a mismatch."},
]
LIMITATIONS = ["A clean result means no supported pattern was found; it does not establish consistency, correctness or security.", "Only bounded UTF-8 text is inspected. Excluded and oversized files, unsupported languages, dynamic values and behavioral semantics are outside coverage.", "No target code, tests, install scripts, Git hooks or external models are executed. Findings require human review before changes.", "Source excerpts can contain sensitive information in otherwise eligible source files. Reports stay local unless you explicitly share them; exclusion is not a secret-scanning guarantee."]

class ScanError(ValueError):
    """Invalid or inaccessible input."""

class ScanCancelled(ScanError):
    """The caller requested cancellation."""

def _check_cancel(cancel):
    if cancel and cancel():
        raise ScanCancelled("Mapping cancelled.")

def _safe_name(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 1024 or any(ord(c)<32 for c in name):
        raise ScanError("File names must be nonempty relative paths of at most 1024 characters.")
    try:
        name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ScanError("File names must be valid UTF-8 text.") from exc
    name = name.replace("\\", "/")
    parts = name.split("/")
    if name.startswith("/") or any(p in {"", ".", ".."} for p in parts) or any(":" in p for p in parts):
        raise ScanError("File names must be relative paths without traversal, drives or alternate streams.")
    if any(p.endswith((" ", ".")) for p in parts):
        raise ScanError("File names with ambiguous trailing spaces or dots are not supported.")
    return str(PurePosixPath(name))

def _excluded(name: str) -> str | None:
    parts = name.lower().split("/")
    if any(p in EXCLUDED_DIRS for p in parts[:-1]):
        return "excluded_directory"
    base = parts[-1]
    if base.startswith(".env") and base not in {".env.example", ".env.sample", ".env.template"}:
        return "secret_name"
    if base.startswith(("credentials", "id_rsa", "id_ed25519", "secrets", "secret.")) or base.endswith((".pem", ".key", ".p12", ".pfx", ".keystore")):
        return "secret_name"
    if base.endswith((".min.js", ".min.css", ".map", ".lock")) or base in {"yarn.lock", "pnpm-lock.yaml"}:
        return "generated_file"
    if Path(base).suffix not in TEXT_EXTENSIONS and base not in SPECIAL_FILES:
        return "unsupported_type"
    return None

def _linked(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))

def _collect(root: Path, cancel):
    try:
        if any(_linked(ancestor) for ancestor in [root.absolute(), *root.absolute().parents]):
            raise ScanError("Select a real folder or file, not a symbolic link or reparse point.")
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise ScanError("The selected folder or file is not accessible.") from exc
    if not resolved.is_dir() and not resolved.is_file():
        raise ScanError("Select a regular folder or text file.")
    if any(part.casefold() in EXCLUDED_DIRS for part in resolved.parts[:-1]) or (resolved.is_dir() and resolved.name.casefold() in EXCLUDED_DIRS):
        raise ScanError("The selected path is inside an excluded vendor, generated or secret directory.")
    base = resolved if resolved.is_dir() else resolved.parent
    files, skipped = {}, Counter()
    total, entries = 0, 0
    truncated = False
    queue = [resolved]
    while queue:
        _check_cancel(cancel)
        current = queue.pop()
        try:
            if _linked(current):
                skipped["link_or_reparse"] += 1
                continue
            actual = current.resolve(strict=True)
            if not actual.is_relative_to(base):
                skipped["path_escape"] += 1
                continue
            if actual.is_dir():
                children = []
                with os.scandir(actual) as iterator:
                    for entry in iterator:
                        entries += 1
                        if entries > MAX_ENTRIES:
                            truncated = True
                            break
                        if entry.name.lower() in EXCLUDED_DIRS:
                            skipped["excluded_directory"] += 1
                            continue
                        children.append(Path(entry.path))
                if truncated:
                    break
                queue.extend(sorted(children, key=lambda p: p.name.casefold(), reverse=True))
                continue
            if not actual.is_file():
                skipped["not_regular_file"] += 1
                continue
            name = _safe_name(actual.relative_to(base).as_posix())
            reason = _excluded(name)
            if reason:
                skipped[reason] += 1
                continue
            size = actual.stat().st_size
            if size > MAX_FILE_BYTES:
                skipped["oversized_file"] += 1
                continue
            if len(files) >= MAX_FILES or total + size > MAX_TOTAL_BYTES:
                skipped["scan_limit"] += 1
                truncated = True
                break
            # Recheck before and after opening; no symlink traversal or target execution.
            if _linked(current):
                skipped["link_or_reparse"] += 1
                continue
            with actual.open("rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    skipped["not_regular_file"] += 1
                    continue
                raw = stream.read(MAX_FILE_BYTES + 1)
            if _linked(current) or current.resolve(strict=True) != actual:
                skipped["path_changed"] += 1
                continue
            if len(raw) > MAX_FILE_BYTES:
                skipped["oversized_file"] += 1
                continue
            if total + len(raw) > MAX_TOTAL_BYTES:
                skipped["scan_limit"] += 1
                truncated = True
                break
            if b"\x00" in raw:
                skipped["binary_file"] += 1
                continue
            try:
                value = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                skipped["non_utf8_file"] += 1
                continue
            files[name] = value
            total += len(raw)
        except (OSError, ScanError):
            skipped["unreadable_file"] += 1
    return files, skipped, truncated

class _Position(int):
    def __new__(cls, line, column):
        result = super().__new__(cls,line)
        result.column = column
        return result

def _ev(files, path, line, role):
    column = getattr(line,"column",1)
    lines = files[path].splitlines()
    line = max(1, min(int(line), len(lines) or 1))
    full = lines[line - 1] if lines else ""
    start = max(0,min(column-81,len(full)-600))
    return {"path":path, "line":line, "end_line":line, "column":column, "excerpt_start_column":start+1, "text":full[start:start+600], "role":role, "truncated":len(full) > 600}

def _json_document(text):
    """Parse JSON and retain exact line positions keyed by JSON object path."""
    def reject_constant(value): raise ValueError("Non-standard JSON constant")
    json.loads(text,parse_constant=reject_constant)
    decoder = json.JSONDecoder()
    newlines = [i for i,c in enumerate(text) if c == "\n"]
    positions = {}
    def parse(index, path):
        while index < len(text) and text[index].isspace(): index += 1
        line = bisect.bisect_left(newlines,index) + 1
        positions[path] = _Position(line,index-(newlines[line-2] if line>1 else -1))
        if text[index:index+1] == "{":
            result = {}; index += 1
            while True:
                while index < len(text) and text[index].isspace(): index += 1
                if text[index:index+1] == "}": return result, index + 1
                key, end = decoder.raw_decode(text, index)
                if not isinstance(key, str): raise ValueError("Invalid key")
                index = end
                while index < len(text) and text[index].isspace(): index += 1
                if text[index:index+1] != ":": raise ValueError("Missing colon")
                if key in result: raise ValueError("Duplicate JSON key")
                value, index = parse(index+1, path+(key,)); result[key] = value
                while index < len(text) and text[index].isspace(): index += 1
                if text[index:index+1] == "}": return result, index+1
                if text[index:index+1] != ",": raise ValueError("Missing comma")
                index += 1
        if text[index:index+1] == "[":
            result = []; index += 1
            while True:
                while index < len(text) and text[index].isspace(): index += 1
                if text[index:index+1] == "]": return result, index+1
                value, index = parse(index, path+(len(result),)); result.append(value)
                while index < len(text) and text[index].isspace(): index += 1
                if text[index:index+1] == "]": return result, index+1
                if text[index:index+1] != ",": raise ValueError("Missing comma")
                index += 1
        return decoder.raw_decode(text, index)
    result, end = parse(0, ())
    if text[end:].strip(): raise ValueError("Trailing JSON")
    return result, positions

def _literal(node):
    if isinstance(node, ast.Constant) and type(node.value) in {str, int, bool, float}:
        return True, node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant) and type(node.operand.value) in {int, float}:
        return True, -node.operand.value
    return False, None

def _brief(value):
    rendered = repr(value)
    return rendered if len(rendered) <= 180 else rendered[:177] + "..."

def _bindings(tree):
    stores = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            stores[node.id] += 1
        elif isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
            stores[node.name] += 1
        elif isinstance(node,ast.arg):
            stores[node.arg] += 1
        elif isinstance(node,(ast.Import,ast.ImportFrom)):
            for alias in node.names: stores[alias.asname or alias.name.split('.')[0]] += 1
        elif isinstance(node,ast.ExceptHandler) and node.name:
            stores[node.name] += 1
        elif isinstance(node,(ast.MatchAs,ast.MatchStar)) and node.name:
            stores[node.name] += 1
        elif isinstance(node,ast.MatchMapping) and node.rest:
            stores[node.rest] += 1
    return stores

def _constant_assignments(tree):
    literals, stores = {}, _bindings(tree)
    for node in tree.body:
        target, value = None, None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        ok, literal = _literal(value)
        if target and ok and stores[target] == 1:
            literals[target] = (literal, node.lineno)
    return literals

def _analyze(files, name, skipped, truncated, cancel):
    start = time.monotonic()
    findings, warnings = [], []
    def add(rule, title, severity, confidence, explanation, evidence):
        nonlocal truncated
        if len(findings) >= MAX_FINDINGS:
            truncated = True
            return
        metadata = next(r for r in RULES if r["id"] == rule)
        identity = rule + json.dumps(evidence, sort_keys=True, ensure_ascii=True)
        findings.append({"id":hashlib.sha256(identity.encode()).hexdigest()[:16], "rule_id":rule, "title":title, "kind":metadata["kind"], "severity":severity, "confidence":confidence, "explanation":explanation, "limitations":metadata["limitations"], "evidence":evidence, "related_paths":sorted({e["path"] for e in evidence})})
    json_docs, trees, constants = {}, {}, {}
    for path, text in files.items():
        _check_cancel(cancel)
        if path.endswith(".json"):
            try:
                json_docs[path] = _json_document(text)
            except (ValueError, IndexError, RecursionError):
                if Path(path).name in {"package.json", "package-lock.json"}: warnings.append(f"Could not parse {path}; package rules skipped for this file.")
        elif path.endswith(".py"):
            try:
                trees[path] = ast.parse(text, filename=path)
                constants[path] = _constant_assignments(trees[path])
            except (SyntaxError, ValueError, RecursionError):
                warnings.append(f"Could not parse {path}; Python rules skipped for this file.")
    for path, (package, ppos) in json_docs.items():
        if Path(path).name != "package.json" or not isinstance(package, dict): continue
        folder = path.rsplit("/", 1)[0] + "/" if "/" in path else ""
        lock_path = folder + "package-lock.json"
        if lock_path not in json_docs: continue
        lock, lpos = json_docs[lock_path]
        if not isinstance(lock, dict): continue
        roots = [(lock, ())]
        if isinstance(lock.get("packages"), dict) and isinstance(lock["packages"].get(""), dict): roots.append((lock["packages"][""], ("packages", "")))
        valid = [(root, prefix) for root, prefix in roots if root.get("name", lock.get("name")) == package.get("name") and isinstance(package.get("name"), str)]
        if not valid: continue
        seen_versions = set()
        for root, prefix in valid:
            a, b = package.get("version"), root.get("version")
            if isinstance(a, str) and isinstance(b, str) and a != b and b not in seen_versions:
                seen_versions.add(b)
                add("NPM001", "Package version differs from lock metadata", "high", 1.0, f"package.json declares {_brief(a)}; the same package's lock metadata declares {_brief(b)}. Refresh or review the lock file.", [_ev(files,path,ppos[("version",)],"manifest version"), _ev(files,lock_path,lpos[prefix+("version",)],"lock version")])
        root_entry = next(((r,p) for r,p in valid if p), None)
        if root_entry:
            root, prefix = root_entry
            for section in ("dependencies", "devDependencies"):
                declared, locked = package.get(section, {}), root.get(section, {})
                if not isinstance(declared, dict) or not isinstance(locked, dict): continue
                for dependency in sorted(set(declared) & set(locked)):
                    _check_cancel(cancel)
                    a, b = declared[dependency], locked[dependency]
                    if isinstance(a, str) and isinstance(b, str) and a != b:
                        add("NPM002", "Direct dependency declaration differs from lock metadata", "medium", 1.0, f"{_brief(dependency)} is declared as {_brief(a)} in {section}, but the lock's root declaration is {_brief(b)}. This compares requested ranges, not resolved versions.", [_ev(files,path,ppos[(section,dependency)],"manifest dependency"), _ev(files,lock_path,lpos[prefix+(section,dependency)],"lock root dependency")])
    for path, text in files.items():
        _check_cancel(cancel)
        if Path(path).name != "pyproject.toml": continue
        try:
            project = tomllib.loads(text).get("project", {})
        except tomllib.TOMLDecodeError:
            warnings.append(f"Could not parse {path}; Python project version rule skipped."); continue
        if not isinstance(project,dict):
            warnings.append(f"Invalid project table in {path}; Python project version rule skipped."); continue
        if not isinstance(project.get("name"), str) or not isinstance(project.get("version"), str): continue
        dynamic = project.get("dynamic", [])
        if not isinstance(dynamic,list):
            warnings.append(f"Invalid dynamic version metadata in {path}; Python project version rule skipped."); continue
        if "version" in dynamic: continue
        package_name = project["name"].replace("-", "_")
        if not re.fullmatch(r"[A-Za-z_]\w*", package_name): continue
        folder = path.rsplit("/",1)[0] + "/" if "/" in path else ""
        candidates = [folder + package_name + "/__init__.py", folder + "src/" + package_name + "/__init__.py", folder + package_name + ".py"]
        present = [p for p in candidates if p in constants and "__version__" in constants[p]]
        if len(present) != 1: continue
        module = present[0]; runtime, line = constants[module]["__version__"]
        if isinstance(runtime,str) and project["version"] != runtime:
            section, version_line = "", None
            for i, source_line in enumerate(text.splitlines(),1):
                if source_line.strip().startswith("["): section = source_line.strip().split("#",1)[0].strip()
                if section == "[project]" and re.match(r"\s*version\s*=",source_line): version_line = i
            if version_line:
                add("PY001", "Published and runtime version literals disagree", "medium", 0.92, f"The static project version is {_brief(project['version'])}; its matching package __version__ is {_brief(runtime)}. Confirm whether these identities are intended to align.", [_ev(files,path,version_line,"project version"),_ev(files,module,line,"module version")])
    # Direct imported constants only; parameters, assignment/rebinding and star imports abstain.
    for test_path, tree in trees.items():
        if not (Path(test_path).name.startswith("test_") or Path(test_path).name.endswith("_test.py")): continue
        if any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names) for n in ast.walk(tree)): continue
        bound = _bindings(tree)
        imports = {}
        for node in tree.body:
            if not isinstance(node,ast.ImportFrom) or node.level or not node.module: continue
            mod_path = node.module.replace(".","/")
            possibilities = [mod_path+".py",mod_path+"/__init__.py","src/"+mod_path+".py","src/"+mod_path+"/__init__.py"]
            resolved = [p for p in possibilities if p in constants]
            if len(resolved) != 1: continue
            for alias in node.names:
                local = alias.asname or alias.name
                if bound[local] == 1 and alias.name in constants[resolved[0]]:
                    imports[local] = (resolved[0],alias.name)
        for node in ast.walk(tree):
            if not isinstance(node,ast.Assert) or not isinstance(node.test,ast.Compare): continue
            comparison = node.test
            if len(comparison.ops) != 1 or not isinstance(comparison.ops[0],ast.Eq): continue
            left,right = comparison.left, comparison.comparators[0]
            if not isinstance(left,ast.Name): left,right = right,left
            if not isinstance(left,ast.Name) or left.id not in imports: continue
            ok, expected = _literal(right)
            if not ok: continue
            module, original = imports[left.id]; actual,line = constants[module][original]
            if actual != expected:
                add("PY002", "Test equality expects a different constant literal", "medium", 0.9, f"{_brief(original)} is assigned {_brief(actual)}; this direct-import test expects {_brief(expected)}. Review runtime mutation, mocking or intentional failing tests before changing either side.", [_ev(files,module,line,"constant assignment"),_ev(files,test_path,node.lineno,"test equality")])
    ports = []
    for path, tree in trees.items():
        if Path(path).name.startswith("test_") or Path(path).name.endswith("_test.py"): continue
        for node in tree.body:
            if not isinstance(node,(ast.Assign,ast.AnnAssign)): continue
            targets = node.targets if isinstance(node,ast.Assign) else [node.target]
            if len(targets) != 1 or not isinstance(targets[0],ast.Name) or targets[0].id not in {"PORT","DEFAULT_PORT"}: continue
            stores = _bindings(tree)[targets[0].id]
            if stores != 1: continue
            value = node.value; ok, port = _literal(value)
            if not ok and isinstance(value,ast.Call) and isinstance(value.func,ast.Name) and value.func.id == "int" and len(value.args) == 1:
                value = value.args[0]
            if not ok and isinstance(value,ast.Call) and len(value.args) == 2:
                func = value.func
                if isinstance(func,ast.Attribute) and isinstance(func.value,ast.Name) and func.value.id == "os" and func.attr == "getenv" and isinstance(value.args[0],ast.Constant) and value.args[0].value == "PORT":
                    ok,port = _literal(value.args[1])
            if ok and re.fullmatch(r"[0-9]{1,5}",str(port)) and 1 <= int(port) <= 65535: ports.append((path,node.lineno,int(port)))
    if len(ports) == 1:
        source,line,actual = ports[0]
        for path,text in files.items():
            if Path(path).name.lower() not in {"readme.md","readme.markdown","readme.rst","readme.txt","readme"}: continue
            in_fence = False
            for i,docline in enumerate(text.splitlines(),1):
                _check_cancel(cancel)
                if docline.lstrip().startswith(("```","~~~")): in_fence = not in_fence; continue
                if in_fence or docline.lstrip().startswith(("#",">")): continue
                matches = re.findall(r"\bdefault\s+port\s*(?:is|:|=)?\s*`?(\d{2,5})\b",docline,re.I)
                matches += re.findall(r"\bport\s+(?:defaults?\s+to)\s*`?(\d{2,5})\b",docline,re.I)
                for expected in set(matches):
                    if 1 <= int(expected) <= 65535 and int(expected) != actual:
                        add("DOC001", "Documented default port differs from code literal", "low", 0.72, f"The README describes default port {expected}; the single recognized Python PORT default is {actual}. This can describe different commands or services: verify scope.", [_ev(files,path,i,"documented default"),_ev(files,source,line,"Python default")])
    # Stable ordering gives humans and agents the same ranked evidence.
    rank = {"high":0,"medium":1,"low":2}
    findings.sort(key=lambda f:(rank[f["severity"]],f["kind"] != "deterministic",-f["confidence"],f["rule_id"],f["id"]))
    for index,finding in enumerate(findings,1): finding["rank"] = index
    counts = Counter(f["kind"] for f in findings)
    warnings.extend([f"Skipped {amount} input(s): {reason.replace('_',' ')}." for reason,amount in sorted(skipped.items())])
    if truncated: warnings.append("Input limits reached; this is a partial scan.")
    return {"schema_version":SCHEMA_VERSION,"target":{"name":name[:256],"kind":"files"},"summary":{"files_scanned":len(files),"bytes_scanned":sum(len(v.encode('utf-8')) for v in files.values()),"files_skipped":sum(skipped.values()),"deterministic":counts["deterministic"],"heuristic":counts["heuristic"],"findings":len(findings)},"findings":findings,"files":[{"path":p,"language":Path(p).suffix.lstrip('.') or "text"} for p in sorted(files)],"coverage":{"rules":RULES,"limitations":LIMITATIONS,"warnings":warnings,"skipped":dict(skipped),"truncated":truncated,"elapsed_ms":round((time.monotonic()-start)*1000,1)}}

def scan_files(files: Mapping[str,str], name: str = "Selected files", cancel: Callable[[],bool] | None = None) -> dict:
    """Analyze uploaded relative UTF-8 text using the same rules and limits as local scans."""
    if not isinstance(files, Mapping): raise ScanError("Expected a mapping of relative file names to text.")
    if not isinstance(name,str): raise ScanError("The selection name must be text.")
    try:
        name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ScanError("The selection name must be valid UTF-8 text.") from exc
    collected, skipped, names = {}, Counter(), set()
    total, truncated = 0, False
    for index,(raw_name,text) in enumerate(files.items()):
        _check_cancel(cancel)
        if index >= MAX_ENTRIES: truncated = True; break
        path = _safe_name(raw_name)
        if path.casefold() in names: raise ScanError("Duplicate or case-ambiguous file paths are not supported.")
        names.add(path.casefold())
        reason = _excluded(path)
        if reason: skipped[reason] += 1; continue
        if not isinstance(text,str): raise ScanError("Selected files must contain UTF-8 text.")
        try:
            raw = text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ScanError("Selected files must contain valid UTF-8 text without unpaired surrogates.") from exc
        if len(raw) > MAX_FILE_BYTES: skipped["oversized_file"] += 1; continue
        if "\x00" in text: skipped["binary_file"] += 1; continue
        if len(collected) >= MAX_FILES or total+len(raw) > MAX_TOTAL_BYTES:
            skipped["scan_limit"] += 1; truncated = True; break
        collected[path] = text.lstrip("\ufeff")
        total += len(raw)
    return _analyze(collected,str(name),skipped,truncated,cancel)

def scan(root: Path | str, cancel: Callable[[],bool] | None = None) -> dict:
    """Inspect a local regular folder or file without executing its contents."""
    root = Path(root).expanduser()
    if not root.exists(): raise ScanError("The selected folder or file does not exist.")
    files,skipped,truncated = _collect(root,cancel)
    report = _analyze(files,root.name,skipped,truncated,cancel)
    report["target"]["kind"] = "local"
    return report
