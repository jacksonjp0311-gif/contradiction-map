# Local analysis and untrusted input

## What leaves the machine

- Folder paths, selected file contents and findings remain in the local Python process and browser, unless you explicitly export or share them.
- Public URL mode makes unauthenticated HTTPS requests to `api.github.com` and `codeload.github.com` to download the named public repository's default-branch snapshot. The URL/repository identity and normal network metadata reach GitHub. Content is downloaded, never uploaded to a model.
- No model provider, API key, telemetry, remote asset, credential configuration or paid service is used. No target dependency installation, code execution, test execution or Git hook execution occurs.

## Input controls

The local collector accepts explicit regular folders/files, ignores symbolic links and Windows reparse points, checks resolved containment, reads bounded UTF-8 text and skips common secrets, binary, generated and vendor inputs. A single scan is bounded to 500 included files, 512 KiB per file, 8 MiB combined, 20,000 encountered entries and 500 findings. It is a snapshot reader, not an atomic filesystem snapshot; do not mutate an untrusted target concurrently with a scan. Rechecks reduce path races but do not turn the OS filesystem into a sandbox.

Common exclusions include `.git`, `.venv`, `node_modules`, `vendor`, `dist`, `build`, caches, `target`, private `.env` variants, credentials/secrets names and private-key formats. Placeholder `.env.example`/`.env.sample` files are eligible. Root `package-lock.json` metadata is eligible because it is required by the NPM rules. Exclusions are intentionally conservative but cannot identify all sensitive content in ordinary source files. This is not a credential scanner.

Browser selection is real file input; the browser does not grant filesystem access by typing a path. Typed local paths are read by the local companion process, which runs with your user permissions. Choose folder opens the browser's real directory picker. The unused native-picker API responds immediately with a fallback message; it does not open a hidden desktop dialog. Only select folders you intend to inspect.

Public imports accept canonical root GitHub repository URLs only. No credentials, query strings, fragments, arbitrary hosts or branch URLs. Metadata must identify a public repository. Redirects are refused. Downloads are capped at 16 MiB compressed / 32 MiB declared expanded size, 20,000 archive members, a 45-second overall import budget with 12-second network operations. ZIP entries are read in memory without extraction; linked/traversing/oversized/excluded members are ignored or rejected. Engine limits still apply. Private repositories, history, submodules and Git LFS content are unsupported. GitHub may rate-limit unauthenticated requests.

## Local service boundary

The server binds only to `127.0.0.1`, checks Host and Origin headers, requires a random session token for every API call, rejects cross-origin requests, and serves only a fixed static-file allowlist. Requests and jobs are bounded. It has no persistent report database and retains at most eight in-memory jobs. Restarting the service discards jobs and rotates the token. The UI renders repository-controlled text as text, and the server sends a restrictive content-security policy. The service is for a trusted local desktop session, not shared hosting. Other software running as you is outside this boundary.

## Interpretation boundary

Deterministic conflicts are failures of narrow encoded metadata invariants. Heuristic candidates are potential inconsistencies, including intentional differences. Neither means the repository is broken or insecure. A clean result means no supported pattern matched collected text. Reports can include sensitive excerpts: inspect before sharing, committing or handing them to another service. The bundled demo, tests and published screenshots use synthetic content only.
