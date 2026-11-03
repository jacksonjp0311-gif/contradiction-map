# JSON evidence contract · 1.0

Run `python -m contradiction_map scan <folder> --json`. Stdout contains a single JSON report on success. Diagnostics go to stderr on failure. `--output <file>` writes to the selected report file instead. The Python API returns the same dict that the UI displays.

```json
{
  "schema_version": "1.0",
  "target": {"name": "demo", "kind": "local"},
  "summary": {
    "files_scanned": 2, "bytes_scanned": 114, "files_skipped": 0,
    "deterministic": 1, "heuristic": 0, "findings": 1
  },
  "findings": [{
    "id": "stable-content-derived-id", "rank": 1,
    "rule_id": "NPM001", "title": "Example metadata mismatch",
    "kind": "deterministic", "severity": "high", "confidence": 1.0,
    "explanation": "The two explicit version values disagree.",
    "limitations": "Only the encoded metadata invariant was checked.",
    "related_paths": ["package.json", "package-lock.json"],
    "evidence": [
      {"path": "package.json", "line": 3, "end_line": 3,
       "text": "  \"version\": \"1.0.0\"", "role": "manifest version", "truncated": false}
    ]
  }],
  "files": [{"path": "package.json", "language": "json"}],
  "coverage": {
    "rules": [], "limitations": [], "warnings": [],
    "skipped": {}, "truncated": false, "elapsed_ms": 0.0
  }
}
```

This is a field illustration, not a captured scan; actual findings include all relevant evidence. Rule IDs identify the checks; IDs and ranks are stable for the same evidence and rules. Timestamps are deliberately omitted. `elapsed_ms` varies. Target kinds include `local`, `files`, `demo` and `url` in the UI. File paths are relative to the selected root and evidence lines are one-based. Line excerpts are bounded; use the authorized original source for wider context. Minified JSON evidence can include optional one-based `column` and `excerpt_start_column` fields to locate the token within a long line.

Always retain `kind`, `limitations`, `coverage.warnings` and `coverage.truncated`. If limits are reached, absence of findings cannot be generalized to omitted input. `files_skipped` counts encountered engine-stage skipped inputs; an excluded directory is one encountered input, not a census of its hidden descendants. Public URL imports also prefilter archive entries before engine collection: those exclusions are described by the import warning and are not included in this count. Confidence is a manually chosen rule weight. It is not a probability or measured precision.

The UI starts jobs with `POST /api/scan`, polls `GET /api/jobs/<id>`, and cancels with `POST /api/jobs/<id>/cancel`. All API calls require the ephemeral session token embedded in the local HTML; requests from other origins and invalid Host headers are rejected. This companion API is local infrastructure, not a hosted service or multi-user authentication boundary. The CLI is the simplest agent interface.
