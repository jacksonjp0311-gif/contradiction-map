# Verification and honest coverage

Initial release, verified locally on Windows / Python 3.12.10 / Node 24.19.0. Runtime requires Python 3.11+; the minimum version and other operating systems have not been executed here.

## Automated checks

`python -m unittest discover -s tests -v`: **62 tests, 61 passed, 1 skipped, 0 failed**. The skipped test requires symbolic-link creation privileges unavailable to this Windows account. Separate real Windows junction smoke checks passed: nested junction excluded; selecting a junction and selecting through a junction ancestor both rejected. These temporary synthetic paths were removed after the checks.

`python -m compileall -q contradiction_map tests` and `node --check contradiction_map/web/app.js`: passed. The PowerShell entry point was actually run against the clean fixture and returned a valid zero-finding JSON report. No third-party linter is installed, so no linter pass is claimed. The bundled skill's naming, frontmatter, links and constraints were manually inspected; the generic skill validator could not run because its PyYAML dependency is absent. No dependency was installed for this check.

| Area | Exercised | Remaining boundary |
| --- | --- | --- |
| Five rules | Positive truth manifest, exact source lines, stable IDs, local/upload parity; demo 5 findings (2 deterministic, 3 candidates) | Synthetic regression evidence only; no measured real-world precision/recall |
| Clean/intentional differences | Clean fixture and false-positive fixture each produce 0 findings; dynamic values, distinct identities, optional/missing dependencies, import shadowing and doc examples abstain | General semantics and author intent not analyzed |
| Untrusted input | Malformed JSON/TOML/Python; duplicate and long/minified values; Unicode/control text; no source execution; secret/vendor filters; traversal, size and output limits | Stable-checkout assumption; no race-free sandbox against malicious concurrent filesystem mutation |
| Local companion | Token/Host/Origin rejection, static allowlist, request bounds, job cancellation/repeat, bounded picker fallback, safe error responses | Trusted local desktop process, not a multi-user server |
| Public import | Mocked archive/network fixtures cover fixed origins, private/redirect rejection, traversal, symlinks, duplicate paths and archive bounds | GitHub rate limits/network failures remain possible; live smoke result recorded below after publication |

This is a behavior/rule coverage report. **No line or branch coverage percentage was measured.** Passing the fixtures does not establish complete contradiction detection, security proof, novelty or agent performance improvement.

## Browser QA

Real browser checks exercise the local service, not a static mock. The original rounded visual treatment was replaced after user feedback with an original boxy retro-futurist instrument. Screenshots in [screenshots/](screenshots/) show the final design on synthetic data.

The browser directory picker selected the synthetic demo through the built-in browser and produced all five findings, including the direct-import test candidate. Edge automation could not set selected files because its extension lacks file-URL permission; no permission or security setting was changed. The built-in browser completed the same selection successfully. Typed local paths remain a separate real companion-service route.

Final browser QA and public-import smoke notes are appended with the release evidence. No private repository was scanned or published.

## Deliberate limitations

Five explicit comparison rules; bounded UTF-8 text; static literals rather than runtime behavior; no target code/test execution; no automatic repairs; no arbitrary Git hosting or private URL imports; no file watcher; graph is evidence relationships, not a full call graph. A clean or truncated report cannot establish consistency. Exclusions are not a secret-scanning guarantee. Wheel builds, minimum-version execution and non-Windows desktop UI have not been validated.

Final browser checks: desktop (1440-pixel target) and mobile (390-pixel target, document width 375 including scrollbar layout) screenshots inspected. Mobile evidence text measured 12px; no horizontal document overflow. Selecting PY002 highlighted settings.py and tests/test_settings.py and showed the exact 3-versus-5 assertion; Flat view rendered all five findings. The redesigned banner was rendered and visually inspected in a local HTML wrapper.
