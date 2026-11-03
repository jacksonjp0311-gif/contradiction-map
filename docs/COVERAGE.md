# Verification and honest coverage

Initial release, verified locally on Windows / Python 3.12.10 / Node 24.19.0. Runtime requires Python 3.11+; the minimum version and other operating systems have not been executed here.

## Automated checks

`python -m unittest discover -s tests -v`: **75 tests, 74 passed, 1 skipped, 0 failed**. The skipped test requires symbolic-link creation privileges unavailable to this Windows account. Separate real Windows junction smoke checks passed: nested junction excluded; selecting a junction and selecting through a junction ancestor both rejected. These temporary synthetic paths were removed after the checks.

`python -m compileall -q contradiction_map tests` and `node --check contradiction_map/web/app.js`: passed. The PowerShell entry point was run against the clean fixture in the original test shell. A later default Windows PowerShell launch was blocked by the machine's existing script policy; no policy was changed. The primary Windows `.cmd` launcher was then verified from outside the checkout with a clean-fixture JSON scan. Thirteen launch/CLI regressions cover verified-instance reuse, legacy instance recognition, unrelated port occupants, Windows exclusive port ownership, bounded probes and exact CLI excerpt locations. No third-party linter is installed, so no linter pass is claimed. The bundled skill's naming, frontmatter, links and constraints were manually inspected; the generic skill validator could not run because its PyYAML dependency is absent. No dependency was installed for this check.

| Area | Exercised | Remaining boundary |
| --- | --- | --- |
| Five rules | Positive truth manifest, exact source lines, stable IDs, local/upload parity; demo 5 findings (2 deterministic, 3 candidates) | Synthetic regression evidence only; no measured real-world precision/recall |
| Clean/intentional differences | Clean fixture and false-positive fixture each produce 0 findings; dynamic values, distinct identities, optional/missing dependencies, import shadowing and doc examples abstain | General semantics and author intent not analyzed |
| Untrusted input | Malformed JSON/TOML/Python; duplicate and long/minified values; Unicode/control text; no source execution; secret/vendor filters; traversal, size and output limits | Stable-checkout assumption; no race-free sandbox against malicious concurrent filesystem mutation |
| Local companion | Token/Host/Origin rejection, static allowlist, request bounds, job cancellation/repeat, bounded picker fallback, safe error responses | Trusted local desktop process, not a multi-user server |
| Public import | Mocked archive/network fixtures cover fixed origins, private/redirect rejection, traversal, symlinks, duplicate paths and archive bounds | GitHub rate limits/network failures remain possible; live default-branch smoke passed; exact receipt below |

This is a behavior/rule coverage report. **No line or branch coverage percentage was measured.** Passing the fixtures does not establish complete contradiction detection, security proof, novelty or agent performance improvement.

## Browser QA

Real browser checks exercise the local service, not a static mock. The original rounded visual treatment was replaced after user feedback with an original boxy retro-futurist instrument. Screenshots in [screenshots/](screenshots/) show the final design on synthetic data.

The browser directory picker selected the synthetic demo through the built-in browser and produced all five findings, including the direct-import test candidate. Edge automation could not set selected files because its extension lacks file-URL permission; no permission or security setting was changed. The built-in browser completed the same selection successfully. Typed local paths remain a separate real companion-service route.

No private repository was scanned or published.

## Deliberate limitations

Five explicit comparison rules; bounded UTF-8 text; static literals rather than runtime behavior; no target code/test execution; no automatic repairs; no arbitrary Git hosting or private URL imports; no file watcher; graph is evidence relationships, not a full call graph. A clean or truncated report cannot establish consistency. Exclusions are not a secret-scanning guarantee. Wheel builds, minimum-version execution and non-Windows desktop UI have not been validated.

Final browser checks: desktop (1440-pixel target) and mobile (390-pixel target, document width 375 including scrollbar layout) screenshots inspected. Mobile evidence text measured 12px; no horizontal document overflow. Selecting PY002 highlighted settings.py and tests/test_settings.py and showed the exact 3-versus-5 assertion; Flat view rendered all five findings. The redesigned banner was rendered and visually inspected in a local HTML wrapper.

## Post-publication live public-URL smoke

**Passed** against this new public repository at main commit `c5b7d24c0289b26ae4cef91891d2daae4b5774b6`, through the running companion's actual `POST /api/scan` URL mode and job polling. This used real unauthenticated GitHub API and codeload requests, not mocked network responses. The job completed in 0.89 seconds; it returned schema 1.0 with target kind `url`, 50 eligible text files / 171,724 bytes, no engine truncation, four deterministic findings and two heuristic candidates.

All six findings are deliberate synthetic version/dependency mismatches in `fixtures/demo` and the bundled `contradiction_map/demo`: two each from NPM001, NPM002 and PY001. They are not newly discovered defects in Contradiction Map. A whole-project scan does not resolve nested demo test imports or pick a single port among several fixture copies; scanning the demo folder alone still yields its five expected findings. No downloaded code, tests, dependencies or hooks were executed. No credentials or model APIs were used for the import.

Public archive prefiltering omits unsupported files (including screenshots), secret/vendor/generated paths and linked entries before engine collection. Here `summary.files_skipped = 0` describes the engine-stage input, not a census of archive exclusions. The report retained the warning about default-branch-only import, skipped archive entries, missing history/submodules/links, and bounded scope. This is a successful smoke check of one small public repository, not exhaustive compatibility, network reliability or security validation. The subsequent release update changes documentation only.

[Machine-readable actual smoke receipt](public-import-smoke.json).

## Polish review and user test run

`node --test tests/ui-state.test.cjs`: **8 passed, 0 failed**, without third-party dependencies. The harness runs the shipped app JavaScript against a small DOM/transport fixture: exact columns and safe repository text, bounded excerpts, transient polling recovery, same-job reconnection, retained cancellation, expired/unauthorized sessions, terminal worker failures and request timeout. This is behavior coverage, not a substitute for real browser rendering.

Separate live browser checks passed against the running companion: clean folder -> zero findings; repeated Refresh -> zero findings; invalid path -> clear error while retaining the prior snapshot; synthetic minified JSON -> one deterministic finding with source `L1:1647` and explicit excerpt-start column `1055`; flat view retained exact path:line:column citations. Actual browser JSON export was read back and confirmed schema 1.0 plus its truncated-column metadata. The user's Edge test window and its running instance were kept available; background QA used a separate page and synthetic inputs only.

The README's three corrupted fixture paths were corrected and copied command text checked for control characters. Local document/image links resolve. Its first-run checklist states the expected five demo findings and zero-finding clean case. Windows users can now double-click the command launcher or use direct Python without weakening execution policy. Opening again probes only the fixed loopback origin and reopens an explicitly identified app or the strict initial-version fingerprint; it does not claim protection against other software impersonating the app locally.
