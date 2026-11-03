# Supported rules · v0.1

Every rule compares explicit text in the collected snapshot. Findings do not identify the correct repair. Confidence is a fixed rule weight, not a calibrated probability. The engine sorts by severity, kind and confidence with stable tie breakers.

| Rule | Scope | Why it can matter | Limitations |
| --- | --- | --- | --- |
| NPM001 | A package.json beside a package-lock.json with matching package identity; compares version literals at lock root / packages[""] where present | Manifest and lock describe different root release metadata | Supports lockfile v1–v3. Does not inspect installed dependencies. Does not compare unrelated package names. |
| NPM002 | Same package, lock v2/v3 packages[""]; compares specs for keys explicitly present in both dependencies/devDependencies maps | Root dependency declarations drifted after an edit | Missing declarations are not assumed conflicts. Optional/peer dependency semantics and resolution equivalence are outside scope. Semantically equivalent but textually different specs may still drift. |
| PY001 | Static [project].version plus one root/src package mapped from normalized project name, with a unique literal __version__ | Published metadata and displayed runtime version may disagree | Dynamic versioning, alternative package mappings and intentional separate versions abstain or need review. Heuristic. |
| PY002 | Top-level direct absolute from-module import, unique matching local module, unmodified literal constant, a test file's assert NAME == literal | A test expectation may be stale relative to source | Conservatively abstains on rebinding/shadowing and ambiguous resolution. Imports are not executed; mocking, runtime effects and intentional failing tests can explain a candidate. Heuristic. |
| DOC001 | Explicit README prose such as “default port is 3000”, compared with exactly one recognized Python PORT/DEFAULT_PORT literal or os.getenv("PORT", literal) default | A documented launch port may be stale | Code fences and quoted examples are ignored. Different services, command defaults or runtime overrides may explain differences. Heuristic. |

The JSON report includes each rule's current limitations. Unsupported input and malformed documents produce warnings or abstention; they are not invented findings. Exact evidence is one-based and excerpts are capped, with truncation marked. Root metadata comparisons are deterministic **only with respect to their encoded literal invariant**; they are not proofs of deployment failure.

Five rules are a narrow initial lens. There is no general natural-language contradiction detection, inferred intent, complete semantic analysis, automatic patching or security proof.
